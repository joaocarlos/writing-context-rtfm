"""Optional in-process transformer models for retrieval experiments."""

from __future__ import annotations

import contextlib
import importlib
from dataclasses import replace
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from writing_context_rtfm.hashing import stable_hash
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.token_budget import estimate_tokens

MXBAI_QUERY_PROMPT = "Represent this sentence for searching relevant passages: "
KNOWN_QUERY_PROMPTS = {
    "mixedbread-ai/mxbai-embed-large-v1": MXBAI_QUERY_PROMPT,
}


def _configure_torch_threads(limit: int) -> None:
    if limit <= 0:
        raise ValueError("torch_threads must be positive")
    torch = importlib.import_module("torch")
    torch.set_num_threads(limit)
    # PyTorch permits setting inter-op threads only before parallel work starts.
    with contextlib.suppress(RuntimeError):
        torch.set_num_interop_threads(1)


class SpanReranker(Protocol):
    def get_fingerprint(self) -> str:
        """Return the model and policy identity used for cache invalidation."""

    def rerank(self, query: str, spans: list[SourceSpan]) -> list[SourceSpan]:
        """Return spans with updated relevance scores and ordering."""


def local_encoder_key(
    model_id: str,
    *,
    revision: str | None = None,
    query_prompt: str | None = "auto",
) -> str:
    """Return a stable identity for cached document embeddings."""
    resolved_prompt = (
        KNOWN_QUERY_PROMPTS.get(model_id, "") if query_prompt == "auto" else query_prompt
    )
    return stable_hash("local-sentence-encoder-v1", model_id, revision or "", resolved_prompt or "")


class LocalSentenceEncoder:
    """Thin sentence-transformers adapter with retrieval-correct query prompting."""

    def __init__(
        self,
        model_id: str,
        *,
        device: str = "auto",
        batch_size: int = 16,
        revision: str | None = None,
        query_prompt: str | None = "auto",
        torch_threads: int = 4,
        model: Any | None = None,
    ):
        self.model_id = model_id
        self.device = device
        self.batch_size = batch_size
        self.revision = revision
        self.query_prompt = (
            KNOWN_QUERY_PROMPTS.get(model_id, "") if query_prompt == "auto" else query_prompt or ""
        )
        self.torch_threads = torch_threads
        self.cache_key = local_encoder_key(
            model_id,
            revision=revision,
            query_prompt=query_prompt,
        )
        self._model = model

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            sentence_transformers = importlib.import_module("sentence_transformers")
        except ImportError as exc:  # pragma: no cover - exercised by availability checks
            raise RuntimeError(
                "Local embedding retrieval requires the 'local-models' optional dependency."
            ) from exc
        kwargs: dict[str, Any] = {}
        _configure_torch_threads(self.torch_threads)
        if self.device != "auto":
            kwargs["device"] = self.device
        if self.revision:
            kwargs["revision"] = self.revision
        self._model = sentence_transformers.SentenceTransformer(self.model_id, **kwargs)
        return self._model

    def _encode(self, texts: list[str]) -> NDArray[np.float32]:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        encoded = self._load().encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        matrix = np.asarray(encoded, dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        normalized = matrix / np.maximum(norms, np.finfo(np.float32).eps)
        return np.asarray(normalized, dtype=np.float32)

    def encode_documents(self, texts: list[str]) -> NDArray[np.float32]:
        return self._encode(texts)

    def encode_queries(self, texts: list[str]) -> NDArray[np.float32]:
        prompted = [f"{self.query_prompt}{text}" for text in texts]
        return self._encode(prompted)


class LocalCrossEncoderReranker:
    """Bounded cross-encoder scoring that never displaces protected target text."""

    def __init__(
        self,
        model_id: str,
        *,
        device: str = "auto",
        batch_size: int = 8,
        max_length: int | None = None,
        candidate_limit: int = 20,
        blend_weight: float = 0.25,
        revision: str | None = None,
        torch_threads: int = 4,
        model: Any | None = None,
        store: Any | None = None,
    ):
        if not 0.0 <= blend_weight <= 1.0:
            raise ValueError("blend_weight must be between 0 and 1")
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        self.model_id = model_id
        self.device = device
        self.batch_size = batch_size
        if max_length is not None:
            if max_length <= 0:
                raise ValueError("max_length must be positive")
            self.max_length = max_length
        elif "modernbert" in model_id.lower():
            self.max_length = 2048
        else:
            self.max_length = 512
        self.candidate_limit = candidate_limit
        self.blend_weight = blend_weight
        self.revision = revision
        self.torch_threads = torch_threads
        self._model = model
        self.store = store

    def get_fingerprint(self) -> str:
        return stable_hash(
            "local-cross-encoder-v1",
            self.model_id,
            self.revision or "",
            self.device,
            str(self.max_length),
            str(self.candidate_limit),
            str(self.blend_weight),
            str(self.torch_threads),
        )

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            sentence_transformers = importlib.import_module("sentence_transformers")
        except ImportError as exc:  # pragma: no cover - exercised by availability checks
            raise RuntimeError(
                "Local reranking requires the 'local-models' optional dependency."
            ) from exc
        kwargs: dict[str, Any] = {
            "max_length": self.max_length,
            "model_kwargs": {"torch_dtype": "auto"},
        }
        _configure_torch_threads(self.torch_threads)
        if self.device != "auto":
            kwargs["device"] = self.device
        if self.revision:
            kwargs["revision"] = self.revision
        self._model = sentence_transformers.CrossEncoder(self.model_id, **kwargs)
        return self._model

    @staticmethod
    def _is_protected(span: SourceSpan) -> bool:
        return span.priority == "essential" or span.source_role == "target_text"

    def rerank(self, query: str, spans: list[SourceSpan]) -> list[SourceSpan]:
        protected = [span for span in spans if self._is_protected(span)]
        candidates = [
            span
            for span in spans
            if not self._is_protected(span) and str((span.metadata or {}).get("snippet") or "")
        ]
        no_text = [
            span
            for span in spans
            if not self._is_protected(span) and not str((span.metadata or {}).get("snippet") or "")
        ]
        candidates.sort(key=lambda span: (-span.score, span.path, span.line_start or 0))
        scored = candidates[: self.candidate_limit]
        overflow = candidates[self.candidate_limit :]
        if not scored:
            return protected + overflow + no_text

        # Task and model hashing for semantic invariance cache
        model_key = stable_hash(
            "cross-encoder-model", self.model_id, self.revision or "", str(self.max_length)
        )
        task_hash = stable_hash("task-query", query)

        snippet_hashes: list[str] = [
            stable_hash("snippet", str((span.metadata or {}).get("snippet") or ""))
            for span in scored
        ]

        cached_scores: dict[str, float] = {}
        if self.store is not None:
            with contextlib.suppress(Exception):
                cached_scores = self.store.get_reranker_scores(model_key, task_hash, snippet_hashes)

        # Identify missing pairs that need neural inference
        missing_indices: list[int] = []
        missing_pairs: list[tuple[str, str]] = []
        for idx, (span, shash) in enumerate(zip(scored, snippet_hashes, strict=True)):
            if shash not in cached_scores:
                missing_indices.append(idx)
                missing_pairs.append((query, str((span.metadata or {}).get("snippet") or "")))

        if missing_pairs:
            raw_scores = self._load().predict(
                missing_pairs,
                batch_size=self.batch_size,
                show_progress_bar=False,
            )
            scores_arr = np.asarray(raw_scores, dtype=np.float32).reshape(-1)
            new_to_store: dict[str, float] = {}
            for m_idx, raw_val in zip(missing_indices, scores_arr, strict=True):
                shash = snippet_hashes[m_idx]
                val = float(raw_val)
                cached_scores[shash] = val
                new_to_store[shash] = val

            if self.store is not None and new_to_store:
                with contextlib.suppress(Exception):
                    self.store.store_reranker_scores(model_key, task_hash, new_to_store)

        final_scores = [cached_scores[shash] for shash in snippet_hashes]

        reranked: list[SourceSpan] = []
        for span, raw_score in zip(scored, final_scores, strict=True):
            reranker_score = float(raw_score)
            blended = (1.0 - self.blend_weight) * span.score + self.blend_weight * reranker_score
            snippet = str((span.metadata or {}).get("snippet") or "")
            truncated = (
                len(snippet.split()) > self.max_length or estimate_tokens(snippet) > self.max_length
            )
            metadata = {
                **(span.metadata or {}),
                "base_score": span.score,
                "reranker_score": round(reranker_score, 6),
                "reranker_model": self.model_id,
            }
            if truncated:
                metadata["reranker_truncated"] = True
            reranked.append(
                replace(
                    span,
                    score=round(blended, 6),
                    metadata=metadata,
                )
            )
        reranked.sort(key=lambda span: (-span.score, span.path, span.line_start or 0))
        overflow.sort(key=lambda span: (-span.score, span.path, span.line_start or 0))
        no_text.sort(key=lambda span: (-span.score, span.path, span.line_start or 0))
        return protected + reranked + overflow + no_text
