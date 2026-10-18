import logging
import os
from pathlib import Path
from typing import Any

import httpx

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.retrieval import RetrievalEngine
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.storage import ExtensionStore

logger = logging.getLogger("mcp-server")


def get_openai_embeddings(texts: list[str], model: str, api_key: str) -> list[list[float]]:
    """Fetch embeddings from OpenAI API."""
    if not api_key:
        raise ValueError("OpenAI API key not provided.")

    try:
        response = httpx.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"input": texts, "model": model},
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()

        # Sort embeddings by index to ensure they match the order of input texts
        sorted_data = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in sorted_data]
    except Exception as e:
        logger.error(f"OpenAI API request failed: {e}")
        raise


class OpenAISemanticSearchProvider(BaseContextProvider):
    def __init__(self, config: AppConfig, engine: RetrievalEngine | None = None):
        self.config = config
        self._engine = engine

    @property
    def provider_id(self) -> str:
        return "openai_semantic"

    def _get_provider_extra(self) -> dict[str, Any]:
        provider_cfg = self.config.providers.get("openai_semantic")
        if provider_cfg is None:
            return {}
        if isinstance(provider_cfg, dict):
            return dict(provider_cfg)
        extra = dict(provider_cfg.extra or {})
        extra["enabled"] = provider_cfg.enabled
        return extra

    def is_available(self, config: AppConfig) -> bool:
        provider_cfg = config.providers.get("openai_semantic")
        if not provider_cfg:
            return False

        if isinstance(provider_cfg, dict):
            enabled = bool(provider_cfg.get("enabled", False))
        else:
            enabled = bool(getattr(provider_cfg, "enabled", False))

        if not enabled:
            return False

        if os.environ.get("OPENAI_API_KEY"):
            return True

        with ExtensionStore(config.cache.path) as store:
            token = store.get_provider_token("openai_semantic")
            return bool(token)

    def get_fingerprint(self, config: AppConfig) -> str | None:
        """Return fingerprint incorporating model name and embedding store state."""
        if not self.is_available(config):
            return None
        extra = self._get_provider_extra()
        model = extra.get("model", "text-embedding-3-small")
        try:
            with ExtensionStore(config.cache.path) as store:
                stats = store.get_openai_embeddings_stats(model)
                from writing_context_rtfm.hashing import stable_hash

                return stable_hash(
                    "openai_semantic",
                    model,
                    str(stats.get("count", 0)),
                    str(stats.get("latest_updated") or ""),
                )
        except Exception:
            return f"openai_semantic-{model}"

    def _get_api_key(self, store: Any) -> str | None:
        return os.environ.get("OPENAI_API_KEY") or store.get_provider_token("openai_semantic")

    def sync_chunks(self, store: Any, rtfm_db_path: str) -> None:
        """Finds missing chunks and embeds them with OpenAI."""
        if not self.is_available(self.config):
            return

        extra = self._get_provider_extra()
        model = extra.get("model", "text-embedding-3-small")

        missing = store.get_missing_openai_chunks(rtfm_db_path, model=model)
        if not missing:
            return

        logger.info(
            f"OpenAI Semantic Sync: {len(missing)} missing chunks to embed for model {model}."
        )

        extra = self._get_provider_extra()
        model = extra.get("model", "text-embedding-3-small")
        batch_size = 100

        try:
            import numpy as np
        except ImportError:
            logger.error(
                "numpy is required for OpenAISemanticSearchProvider. Install with `pip install numpy`."
            )
            return

        api_key = self._get_api_key(store)
        if not api_key:
            logger.error(
                "OpenAI API key not found. Please set OPENAI_API_KEY or use 'writing-context-rtfm auth openai_semantic <key>'"
            )
            return

        for i in range(0, len(missing), batch_size):
            batch = missing[i : i + batch_size]
            texts = [c["content"] for c in batch]

            try:
                embeddings = get_openai_embeddings(texts, model, api_key)

                store_data = []
                for chunk, emb in zip(batch, embeddings, strict=False):
                    emb_bytes = np.array(emb, dtype=np.float32).tobytes()
                    store_data.append(
                        {"chunk_id": chunk["chunk_id"], "embedding": emb_bytes, "model": model}
                    )
                store.store_openai_embeddings(store_data)
                logger.debug(f"Embedded batch of {len(batch)} chunks.")
            except Exception as e:
                logger.error(f"Failed to embed batch during sync: {e}")
                break

    def fetch_context(
        self,
        queries: list[str],
        target: str | None,
        limit: int,
        query_type_map: dict[str, str] | None = None,
        task_type: str | None = None,
    ) -> list[SourceSpan]:
        import numpy as np

        from writing_context_rtfm.utils import resolve_rtfm_db_path

        extra = self._get_provider_extra()
        model = extra.get("model", "text-embedding-3-small")
        rtfm_db_path = str(resolve_rtfm_db_path(Path(self.config.rtfm.project_root)))

        with ExtensionStore(self.config.cache.path) as store:
            # Lazy loading check: if auto_sync is false, we sync right before querying
            if not extra.get("auto_sync", False):
                self.sync_chunks(store, rtfm_db_path)

            all_embs = store.get_all_openai_embeddings(model)
            if not all_embs:
                return []

            api_key = self._get_api_key(store)
            if not api_key:
                return []

        chunk_ids = [e["chunk_id"] for e in all_embs]
        matrix = np.vstack([np.frombuffer(e["embedding"], dtype=np.float32) for e in all_embs])

        try:
            query_embs = get_openai_embeddings(queries, model, api_key)
        except Exception:
            return []

        query_matrix = np.array(query_embs, dtype=np.float32)

        # Compute cosine similarity
        similarities = np.dot(query_matrix, matrix.T)  # Shape: (num_queries, num_chunks)

        # Best match for each chunk across all queries
        max_sims = np.max(similarities, axis=0)
        best_query_indices = np.argmax(similarities, axis=0)

        # Get top K indices
        top_k_indices = np.argsort(max_sims)[-limit:][::-1]

        candidate_indices = [
            int(idx) for idx in top_k_indices if float(max_sims[idx]) >= 0.2
        ]
        if not candidate_indices:
            return []

        selected_chunk_ids = [str(chunk_ids[idx]) for idx in candidate_indices]
        chunk_rows: dict[str, dict[str, Any]] = {}
        try:
            if self._engine is not None:
                engine_chunks = self._engine.get_chunks_by_ids(selected_chunk_ids)
                for cid, res in engine_chunks.items():
                    chunk_rows[cid] = {
                        "content": res.snippet,
                        "file_path": res.path,
                        "line_start": res.line_start,
                        "line_end": res.line_end,
                    }
            else:
                from writing_context_rtfm.rtfm_adapter import RTFMAdapter

                chunk_rows = {
                    row["chunk_id"]: row
                    for row in RTFMAdapter.list_indexed_chunks_from_db(
                        rtfm_db_path, chunk_ids=selected_chunk_ids
                    )
                }
        except Exception as e:
            logger.error(f"Failed to fetch chunk details from RTFM DB: {e}")
            return []

        spans = []
        for idx in candidate_indices:
            score = float(max_sims[idx])
            chunk_id = str(chunk_ids[idx])
            query_used = queries[best_query_indices[idx]]
            row = chunk_rows.get(chunk_id)
            if row:
                spans.append(
                    SourceSpan(
                        path=str(row["file_path"]),
                        line_start=row["line_start"],
                        line_end=row["line_end"],
                        reason="OpenAI semantic match for query",
                        score=score,
                        priority="supporting",
                        source_role="local_context",
                        query=query_used,
                        metadata={"snippet": str(row["content"])},
                    )
                )

        return spans
