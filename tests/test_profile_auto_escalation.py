from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from writing_context_rtfm.config import AppConfig, RTFMConfig, apply_profile, load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.section_cards import SectionCard, SectionCards
from writing_context_rtfm.storage import ExtensionStore


class DummyAdapter:
    def search(self, query: str, corpus: str = "default", limit: int = 10) -> list[Any]:
        return []

    def context(self, path: str, line_start: int, line_end: int, corpus: str = "default") -> list[Any]:
        return []


class MockReranker:
    def __init__(self) -> None:
        self.call_count = 0
        self.store = None

    def get_fingerprint(self) -> str:
        return "mock-reranker-v1"

    def rerank(self, query: str, spans: list[SourceSpan]) -> list[SourceSpan]:
        self.call_count += 1
        return [
            SourceSpan(
                path=s.path,
                line_start=s.line_start,
                line_end=s.line_end,
                reason=s.reason + " [reranked]",
                score=min(1.0, s.score + 0.1),
                priority=s.priority,
                source_role=s.source_role,
                metadata=s.metadata,
            )
            for s in spans
        ]


def test_apply_profile_auto():
    cfg = load_config("nonexistent.yaml")
    auto_cfg = apply_profile(cfg, "auto")

    assert auto_cfg.profile == "auto"
    assert auto_cfg.providers["local_reranker"].enabled is True
    assert (
        auto_cfg.providers.get("local_embeddings") is None
        or not auto_cfg.providers["local_embeddings"].enabled
    )


def test_should_auto_escalate_on_mathematical_cues():
    cfg = AppConfig(version=1, rtfm=RTFMConfig(), context=MagicMock(), cache=MagicMock(), section_cards=MagicMock(), profile="auto")
    store = ExtensionStore(":memory:")
    reranker = MockReranker()
    generator = ContextPackGenerator(cfg, None, DummyAdapter(), store, reranker=reranker)

    card = SectionCard(id="sec1", title="Convergence", path="math.tex")
    dummy_span = SourceSpan(
        path="math.tex", line_start=1, line_end=10, reason="BM25", score=0.8,
        priority="supporting", source_role="dependency", metadata={"snippet": "Proof of Theorem 1"}
    )

    # Mathematical / theoretical tasks should escalate
    tasks_that_escalate = [
        "Reescrever a prova do teorema da convergência assintótica",
        "Prove that the lower bound holds under asymptotic complexity",
        "Demonstrar o lema de otimização convexa",
        "Derive the gradient update formula using equation $O(n \\log n)$",
        "Revisar o texto e verificar a citação \\cite{smith2020}",
    ]
    for task in tasks_that_escalate:
        should_esc, reason = generator._should_auto_escalate(task, card, [dummy_span])
        assert should_esc is True, f"Failed for task: {task}"
        assert "Escalated" in reason


def test_should_auto_escalate_on_lexical_uncertainty():
    cfg = AppConfig(version=1, rtfm=RTFMConfig(), context=MagicMock(), cache=MagicMock(), section_cards=MagicMock(), profile="auto")
    store = ExtensionStore(":memory:")
    reranker = MockReranker()
    generator = ContextPackGenerator(cfg, None, DummyAdapter(), store, reranker=reranker)

    # 1. Low peak score (< 0.40)
    low_score_spans = [
        SourceSpan(
            path="intro.tex", line_start=1, line_end=5, reason="BM25", score=0.32,
            priority="supporting", source_role="reference", metadata={"snippet": "Discussion"}
        ),
        SourceSpan(
            path="intro.tex", line_start=10, line_end=15, reason="BM25", score=0.28,
            priority="supporting", source_role="reference", metadata={"snippet": "Background"}
        ),
    ]
    should_esc, reason = generator._should_auto_escalate("Ajustar a introdução geral", None, low_score_spans)
    assert should_esc is True
    assert "peak lexical retrieval score is low" in reason

    # 2. Flat score distribution (delta < 0.05 among top 3)
    flat_spans = [
        SourceSpan(path="f1.tex", line_start=1, line_end=5, reason="BM25", score=0.62, priority="supporting", metadata={"snippet": "A"}),
        SourceSpan(path="f2.tex", line_start=1, line_end=5, reason="BM25", score=0.61, priority="supporting", metadata={"snippet": "B"}),
        SourceSpan(path="f3.tex", line_start=1, line_end=5, reason="BM25", score=0.60, priority="supporting", metadata={"snippet": "C"}),
    ]
    should_esc, reason = generator._should_auto_escalate("Revisar visão geral", None, flat_spans)
    assert should_esc is True
    assert "score distribution is flat" in reason

    # 3. High-confidence distinctive score distribution does NOT escalate
    distinct_spans = [
        SourceSpan(path="f1.tex", line_start=1, line_end=5, reason="BM25", score=0.85, priority="supporting", metadata={"snippet": "A"}),
        SourceSpan(path="f2.tex", line_start=1, line_end=5, reason="BM25", score=0.55, priority="supporting", metadata={"snippet": "B"}),
        SourceSpan(path="f3.tex", line_start=1, line_end=5, reason="BM25", score=0.40, priority="supporting", metadata={"snippet": "C"}),
    ]
    should_esc, reason = generator._should_auto_escalate("Atualizar agradecimentos institucionais", None, distinct_spans)
    assert should_esc is False
    assert "confidence is high" in reason


def test_generator_auto_profile_execution(tmp_path):
    store = ExtensionStore(str(tmp_path / "cache.sqlite"))
    reranker = MockReranker()

    base_cfg = load_config("nonexistent.yaml")
    auto_cfg = apply_profile(base_cfg, "auto")

    from writing_context_rtfm.section_cards import DocumentCard

    generator = ContextPackGenerator(
        auto_cfg,
        section_cards=SectionCards(version=1, document=DocumentCard(title="Doc", thesis="Doc thesis"), sections={}),
        adapter=DummyAdapter(),
        store=store,
        reranker=reranker,
    )

    # 1. Non-escalating task: reranker must NOT be called
    distinct_spans = [
        SourceSpan(path="doc.md", line_start=1, line_end=5, reason="BM25", score=0.90, priority="supporting", metadata={"snippet": "A"}),
        SourceSpan(path="doc.md", line_start=10, line_end=15, reason="BM25", score=0.40, priority="supporting", metadata={"snippet": "B"}),
    ]
    generator._deduplicate_spans = MagicMock(return_value=distinct_spans)
    generator._filter_by_score = MagicMock(return_value=(distinct_spans, 0))
    generator._filter_avoid = MagicMock(return_value=(distinct_spans, 0))

    pack = generator.generate(task="Agradecimentos institucionais aos colaboradores", target=None, token_budget=2000)
    assert reranker.call_count == 0
    assert pack.quality.get("auto_escalation", {}).get("escalated") is False

    # 2. Mathematical task: reranker MUST be called
    pack_math = generator.generate(task="Verificar a prova do teorema 4 e limitante assintótico", target=None, token_budget=2000)
    assert reranker.call_count == 1
    assert pack_math.quality.get("auto_escalation", {}).get("escalated") is True
    assert "formal/theoretical" in pack_math.quality.get("auto_escalation", {}).get("reason", "")
