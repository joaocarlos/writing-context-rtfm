from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np

from writing_context_rtfm.config import load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.local_models import (
    LocalCrossEncoderReranker,
    LocalSentenceEncoder,
)
from writing_context_rtfm.providers.local_semantic import LocalSemanticSearchProvider
from writing_context_rtfm.schemas import ProviderConfig, RTFMResult, SourceSpan
from writing_context_rtfm.storage import ExtensionStore


class FakeSentenceTransformer:
    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors
        self.calls: list[dict[str, Any]] = []

    def encode(self, texts: list[str], **kwargs: Any) -> np.ndarray:
        self.calls.append({"texts": list(texts), **kwargs})
        return np.asarray([self.vectors[text] for text in texts], dtype=np.float32)


def test_mixedbread_encoder_prompts_queries_but_not_documents() -> None:
    prefix = "Represent this sentence for searching relevant passages: "
    fake = FakeSentenceTransformer(
        {
            "source passage": [1.0, 0.0],
            f"{prefix}find evidence": [0.0, 1.0],
        }
    )
    encoder = LocalSentenceEncoder(
        "mixedbread-ai/mxbai-embed-large-v1",
        model=fake,
    )

    encoder.encode_documents(["source passage"])
    encoder.encode_queries(["find evidence"])

    assert fake.calls[0]["texts"] == ["source passage"]
    assert fake.calls[1]["texts"] == [f"{prefix}find evidence"]
    assert fake.calls[0]["normalize_embeddings"] is True
    assert fake.calls[1]["normalize_embeddings"] is True


def test_minilm_encoder_does_not_invent_a_query_prompt() -> None:
    fake = FakeSentenceTransformer({"find evidence": [1.0, 0.0]})
    encoder = LocalSentenceEncoder(
        "sentence-transformers/all-MiniLM-L6-v2",
        model=fake,
    )

    encoder.encode_queries(["find evidence"])

    assert fake.calls[0]["texts"] == ["find evidence"]


def _create_rtfm_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, filename TEXT NOT NULL)"
        )
        conn.execute(
            """
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                book_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                line_start INTEGER,
                line_end INTEGER
            )
            """
        )
        conn.execute("INSERT INTO books (id, filename) VALUES (1, 'main.tex')")
        conn.executemany(
            """
            INSERT INTO chunks (chunk_id, book_id, content, line_start, line_end)
            VALUES (?, 1, ?, ?, ?)
            """,
            [
                ("relevant", "calibration evidence", 10, 12),
                ("noise", "unrelated publisher boilerplate", 20, 22),
            ],
        )


def test_local_semantic_provider_caches_by_model_and_ranks_cosine_matches(
    tmp_path: Path, monkeypatch: Any
) -> None:
    rtfm_db = tmp_path / "rtfm.sqlite"
    _create_rtfm_db(rtfm_db)
    cache_path = tmp_path / "context.sqlite"
    base = load_config(str(tmp_path))
    config = replace(
        base,
        cache=replace(base.cache, path=str(cache_path)),
        providers={
            "local_embeddings": ProviderConfig(
                enabled=True,
                extra={
                    "model": "sentence-transformers/all-MiniLM-L6-v2",
                    "min_score": -1.0,
                },
            )
        },
    )
    fake = FakeSentenceTransformer(
        {
            "calibration evidence": [1.0, 0.0],
            "unrelated publisher boilerplate": [0.0, 1.0],
            "calibration": [1.0, 0.0],
        }
    )
    encoder = LocalSentenceEncoder(
        "sentence-transformers/all-MiniLM-L6-v2",
        model=fake,
    )
    provider = LocalSemanticSearchProvider(config, encoder=encoder)
    monkeypatch.setattr(
        "writing_context_rtfm.providers.local_semantic.resolve_rtfm_db_path",
        lambda _root: rtfm_db,
    )

    with ExtensionStore(str(cache_path)) as store:
        store.init_db()
        provider.sync_chunks(store, str(rtfm_db))
        cached = store.get_local_embeddings(provider.model_key)

    spans = provider.fetch_context(["calibration"], target=None, limit=2)

    assert len(cached) == 2
    assert all(row["model_key"] == provider.model_key for row in cached)
    assert [span.path for span in spans] == ["main.tex", "main.tex"]
    assert spans[0].line_start == 10
    assert spans[0].score > spans[1].score
    assert spans[0].source_role == "local_context"
    assert spans[0].metadata["provider_id"] == "local_embeddings"


class FakeCrossEncoder:
    def predict(self, pairs: list[tuple[str, str]], **kwargs: Any) -> np.ndarray:
        assert pairs == [("task", "weak"), ("task", "strong")]
        return np.asarray([0.1, 0.9], dtype=np.float32)


def test_cross_encoder_reranks_candidates_without_demoting_target_text() -> None:
    target = SourceSpan(
        path="main.tex",
        line_start=1,
        line_end=4,
        reason="target",
        score=0.2,
        priority="essential",
        source_role="target_text",
        metadata={"snippet": "target section"},
    )
    weak = SourceSpan(
        path="weak.tex",
        line_start=1,
        line_end=2,
        reason="lexical",
        score=0.8,
        metadata={"snippet": "weak"},
    )
    strong = SourceSpan(
        path="strong.tex",
        line_start=1,
        line_end=2,
        reason="semantic",
        score=0.3,
        metadata={"snippet": "strong"},
    )
    reranker = LocalCrossEncoderReranker(
        "Alibaba-NLP/gte-reranker-modernbert-base",
        model=FakeCrossEncoder(),
        blend_weight=1.0,
    )

    reranked = reranker.rerank("task", [target, weak, strong])

    assert reranked[0] is target
    assert [span.path for span in reranked[1:]] == ["strong.tex", "weak.tex"]
    assert reranked[0].score == 0.2
    assert reranked[1].metadata["reranker_score"] == 0.9
    assert reranked[1].metadata["base_score"] == 0.3


def test_config_accepts_local_reranker_without_mcp_transport(tmp_path: Path) -> None:
    config_dir = tmp_path / ".writing-context"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        """
providers:
  local_reranker:
    enabled: true
    model: Alibaba-NLP/gte-reranker-modernbert-base
""".strip(),
        encoding="utf-8",
    )

    config = load_config(str(tmp_path))

    assert config.providers["local_reranker"].enabled is True
    assert config.providers["local_reranker"].extra["model"] == (
        "Alibaba-NLP/gte-reranker-modernbert-base"
    )


class FakeSpanReranker:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def get_fingerprint(self) -> str:
        return "fake-reranker-v1"

    def rerank(self, query: str, spans: list[SourceSpan]) -> list[SourceSpan]:
        self.queries.append(query)
        return [
            replace(
                span,
                score=0.95 if span.path == "semantic.tex" else 0.1,
                metadata={**(span.metadata or {}), "reranker_score": 0.95},
            )
            for span in spans
        ]


def test_context_pack_applies_injected_reranker_before_selection(tmp_path: Path) -> None:
    config = replace(
        load_config(str(tmp_path)),
        cache=replace(load_config(str(tmp_path)).cache, enabled=False),
    )
    adapter = MagicMock()
    adapter.search.return_value = [
        RTFMResult(
            path="lexical.tex",
            line_start=1,
            line_end=2,
            snippet="lexical evidence",
            score=0.9,
            metadata={},
        ),
        RTFMResult(
            path="semantic.tex",
            line_start=1,
            line_end=2,
            snippet="semantic evidence",
            score=0.2,
            metadata={},
        ),
    ]
    reranker = FakeSpanReranker()
    with ExtensionStore(":memory:") as store:
        store.init_db()
        generator = ContextPackGenerator(
            config,
            None,
            adapter,
            store,
            reranker=reranker,
        )
        pack = generator.generate(task="draft calibration", target=None, token_budget=1_000)

    assert reranker.queries == ["draft calibration"]
    assert pack.source_spans[0].path == "semantic.tex"
    assert pack.source_spans[0].metadata["reranker_score"] == 0.95
