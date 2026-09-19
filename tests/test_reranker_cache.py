from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from writing_context_rtfm.local_models import LocalCrossEncoderReranker
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.storage import ExtensionStore


def test_extension_store_reranker_cache(tmp_path):
    db_path = str(tmp_path / "test_cache.sqlite")
    store = ExtensionStore(db_path)
    store.init_db()

    model_key = "test-model-v1"
    task_hash = "task-hash-123"

    # Initially empty
    cached = store.get_reranker_scores(model_key, task_hash, ["snip_a", "snip_b"])
    assert cached == {}

    # Store scores
    scores_to_store = {"snip_a": 0.854, "snip_b": 0.123}
    store.store_reranker_scores(model_key, task_hash, scores_to_store)

    # Retrieve stored scores
    cached2 = store.get_reranker_scores(model_key, task_hash, ["snip_a", "snip_b", "snip_c"])
    assert len(cached2) == 2
    assert pytest.approx(cached2["snip_a"], 1e-4) == 0.854
    assert pytest.approx(cached2["snip_b"], 1e-4) == 0.123
    assert "snip_c" not in cached2

    # Update existing score
    store.store_reranker_scores(model_key, task_hash, {"snip_a": 0.999})
    cached3 = store.get_reranker_scores(model_key, task_hash, ["snip_a"])
    assert pytest.approx(cached3["snip_a"], 1e-4) == 0.999


def test_local_cross_encoder_reranker_with_store_cache(tmp_path):
    db_path = str(tmp_path / "cache.sqlite")
    store = ExtensionStore(db_path)
    store.init_db()

    mock_model = MagicMock()
    # First prediction returns [0.95, 0.40]
    mock_model.predict.return_value = np.array([0.95, 0.40], dtype=np.float32)

    reranker = LocalCrossEncoderReranker(
        model_id="mock-cross-encoder",
        model=mock_model,
        candidate_limit=10,
        blend_weight=0.5,
        store=store,
    )

    span1 = SourceSpan(
        path="doc1.tex", line_start=1, line_end=10, reason="BM25", score=0.5,
        priority="supporting", source_role="reference", metadata={"snippet": "First snippet content."}
    )
    span2 = SourceSpan(
        path="doc2.tex", line_start=1, line_end=10, reason="BM25", score=0.6,
        priority="supporting", source_role="reference", metadata={"snippet": "Second snippet content."}
    )

    query = "Reescrever teorema"

    # --- 1st run: Cache miss, mock_model.predict is invoked ---
    reranked_1 = reranker.rerank(query, [span1, span2])
    assert mock_model.predict.call_count == 1
    # Verify scores are updated with reranker_score metadata
    assert any(s.metadata.get("reranker_score") == 0.95 for s in reranked_1 if s.metadata)

    # --- 2nd run with same query and spans: Full cache hit! ---
    reranked_2 = reranker.rerank(query, [span1, span2])
    # mock_model.predict must NOT be called again!
    assert mock_model.predict.call_count == 1
    assert len(reranked_2) == 2
    assert reranked_1[0].score == reranked_2[0].score

    # --- 3rd run with 1 new span: Partial cache hit! ---
    mock_model.predict.return_value = np.array([0.77], dtype=np.float32)
    span3 = SourceSpan(
        path="doc3.tex", line_start=1, line_end=10, reason="BM25", score=0.4,
        priority="supporting", source_role="reference", metadata={"snippet": "Third new snippet."}
    )
    reranked_3 = reranker.rerank(query, [span1, span2, span3])
    # predict is called once more, but ONLY with the 1 missing pair
    assert mock_model.predict.call_count == 2
    assert len(reranked_3) == 3
    last_call_pairs = mock_model.predict.call_args[0][0]
    assert len(last_call_pairs) == 1
    assert last_call_pairs[0][1] == "Third new snippet."
