"""Opt-in local sentence-transformer retrieval provider."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

import numpy as np

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.hashing import stable_hash
from writing_context_rtfm.local_models import LocalSentenceEncoder, local_encoder_key
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.retrieval import RetrievalEngine
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.utils import resolve_rtfm_db_path

logger = logging.getLogger("mcp-server")

DEFAULT_LOCAL_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class LocalSemanticSearchProvider(BaseContextProvider):
    """Overlay local semantic retrieval on the existing RTFM chunk index."""

    def __init__(
        self,
        config: AppConfig,
        *,
        encoder: LocalSentenceEncoder | None = None,
        engine: RetrievalEngine | None = None,
    ):
        self.config = config
        self._encoder = encoder
        self._engine = engine

    @property
    def provider_id(self) -> str:
        return "local_embeddings"

    def _extra(self) -> dict[str, Any]:
        provider = self.config.providers.get(self.provider_id)
        if provider is None:
            return {}
        if isinstance(provider, dict):
            return dict(provider)
        return dict(provider.extra or {})

    @property
    def model_id(self) -> str:
        return str(self._extra().get("model", DEFAULT_LOCAL_EMBEDDING_MODEL))

    @property
    def model_key(self) -> str:
        if self._encoder is not None:
            return self._encoder.cache_key
        extra = self._extra()
        return local_encoder_key(
            self.model_id,
            revision=str(extra["revision"]) if extra.get("revision") else None,
            query_prompt=extra.get("query_prompt", "auto"),
        )

    def _get_encoder(self) -> LocalSentenceEncoder:
        if self._encoder is None:
            extra = self._extra()
            self._encoder = LocalSentenceEncoder(
                self.model_id,
                device=str(extra.get("device", "auto")),
                batch_size=int(extra.get("batch_size", 16)),
                revision=str(extra["revision"]) if extra.get("revision") else None,
                query_prompt=extra.get("query_prompt", "auto"),
                torch_threads=int(extra.get("torch_threads", 4)),
            )
        return self._encoder

    def is_available(self, config: AppConfig) -> bool:
        provider = config.providers.get(self.provider_id)
        enabled = bool(
            provider.get("enabled", False)
            if isinstance(provider, dict)
            else getattr(provider, "enabled", False)
        )
        return enabled and (
            self._encoder is not None
            or importlib.util.find_spec("sentence_transformers") is not None
        )

    def get_fingerprint(self, config: AppConfig) -> str | None:
        if not self.is_available(config):
            return None
        try:
            with ExtensionStore(config.cache.path) as store:
                store.init_db()
                stats = store.get_local_embeddings_stats(self.model_key)
        except Exception:
            stats = {"count": 0, "latest_updated": None}
        return stable_hash(
            self.provider_id,
            self.model_id,
            self.model_key,
            str(stats["count"]),
            str(stats["latest_updated"] or ""),
        )

    def sync_chunks(self, store: ExtensionStore, rtfm_db_path: str) -> None:
        if not self.is_available(self.config):
            return
        missing = store.get_missing_local_chunks(rtfm_db_path, self.model_key)
        if not missing:
            return
        extra = self._extra()
        batch_size = int(extra.get("batch_size", 16))
        encoder = self._get_encoder()
        logger.info(
            "Local semantic sync: embedding %d changed chunks with %s.",
            len(missing),
            self.model_id,
        )
        for offset in range(0, len(missing), batch_size):
            batch = missing[offset : offset + batch_size]
            matrix = encoder.encode_documents([str(chunk["content"]) for chunk in batch])
            rows = []
            for chunk, vector in zip(batch, matrix, strict=True):
                rows.append(
                    {
                        "chunk_id": chunk["chunk_id"],
                        "model_key": self.model_key,
                        "content_hash": chunk["content_hash"],
                        "embedding": np.asarray(vector, dtype=np.float32).tobytes(),
                    }
                )
            store.store_local_embeddings(rows)

    def fetch_context(
        self,
        queries: list[str],
        target: str | None,
        limit: int,
        query_type_map: dict[str, str] | None = None,
        task_type: str | None = None,
    ) -> list[SourceSpan]:
        del target, query_type_map, task_type
        if not queries or limit <= 0 or not self.is_available(self.config):
            return []
        rtfm_db_path = str(resolve_rtfm_db_path(Path(self.config.rtfm.project_root)))
        extra = self._extra()
        with ExtensionStore(self.config.cache.path) as store:
            store.init_db()
            if bool(extra.get("sync_on_query", True)):
                self.sync_chunks(store, rtfm_db_path)
            cached = store.get_local_embeddings(self.model_key)
        if not cached:
            return []

        chunk_ids = [str(row["chunk_id"]) for row in cached]
        matrix = np.vstack([np.frombuffer(row["embedding"], dtype=np.float32) for row in cached])
        query_matrix = self._get_encoder().encode_queries(queries)
        similarities = np.matmul(query_matrix, matrix.T)
        best_scores = np.max(similarities, axis=0)
        best_queries = np.argmax(similarities, axis=0)
        ranked_indices = np.argsort(best_scores)[::-1]
        minimum = float(extra.get("min_score", 0.5))

        candidate_indices = [
            int(idx) for idx in ranked_indices if float(best_scores[idx]) >= minimum
        ]
        if not candidate_indices:
            return []
        candidate_cids = [chunk_ids[idx] for idx in candidate_indices]
        chunk_map: dict[str, dict[str, Any]]
        if self._engine is not None:
            by_id_res = self._engine.get_chunks_by_ids(candidate_cids)
            chunk_map = {
                cid: {
                    "file_path": res.path,
                    "line_start": res.line_start,
                    "line_end": res.line_end,
                    "content": res.snippet,
                }
                for cid, res in by_id_res.items()
            }
        else:
            rows = RTFMAdapter.list_indexed_chunks_from_db(
                rtfm_db_path, chunk_ids=candidate_cids
            )
            chunk_map = {str(r["chunk_id"]): r for r in rows}

        spans: list[SourceSpan] = []
        for index in candidate_indices:
            if len(spans) >= limit:
                break
            score = float(best_scores[index])
            row = chunk_map.get(chunk_ids[index])
            if row is None:
                continue
            spans.append(
                SourceSpan(
                    path=str(row["file_path"]),
                    line_start=int(row["line_start"]) if row["line_start"] is not None else None,
                    line_end=int(row["line_end"]) if row["line_end"] is not None else None,
                    reason=f"Local semantic match via {self.model_id}",
                    score=round(score, 6),
                    retrieval_score=round(score, 6),
                    priority="supporting",
                    source_role="local_context",
                    query=queries[int(best_queries[index])],
                    metadata={
                        "snippet": str(row["content"]),
                        "provider_id": self.provider_id,
                        "embedding_model": self.model_id,
                        "embedding_model_key": self.model_key,
                    },
                )
            )
        return spans
