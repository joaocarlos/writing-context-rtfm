from __future__ import annotations

from unittest.mock import MagicMock

from writing_context_rtfm.config import load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.section_cards import SectionCard
from writing_context_rtfm.storage import ExtensionStore


def test_prioritize_and_bound_reranker_candidates():
    cfg = load_config("nonexistent.yaml")
    store = ExtensionStore(":memory:")
    generator = ContextPackGenerator(cfg, None, MagicMock(), store)

    target_card = SectionCard(
        id="sec_methods",
        title="Methods",
        path="sections/methods.tex",
        depends_on=["sec_theory"],
        key_terms=["convergence", "knuth1984"],
    )
    dep_card = SectionCard(
        id="sec_theory",
        title="Theory",
        path="sections/theory.tex",
    )

    # Protected span
    target_span = SourceSpan(
        path="sections/methods.tex",
        line_start=1,
        line_end=20,
        reason="Target text",
        score=1.0,
        priority="essential",
        source_role="target_text",
        metadata={"snippet": "Target content"},
    )

    # Dependency span (Theory section) - lower initial BM25 score
    dep_span = SourceSpan(
        path="sections/theory.tex",
        line_start=10,
        line_end=30,
        reason="Dependency section",
        score=0.45,
        priority="supporting",
        source_role="dependency",
        metadata={"snippet": "Theorem 1 asymptotic bound."},
    )

    # Citation span matching knuth1984
    citation_span = SourceSpan(
        path="sections/lit.tex",
        line_start=5,
        line_end=15,
        reason="BM25 match",
        score=0.40,
        priority="supporting",
        source_role="reference",
        metadata={"snippet": "As shown by Knuth1984 in prior studies."},
    )

    # Distant unrelated span with high BM25 keyword repetition
    unrelated_span = SourceSpan(
        path="sections/intro.tex",
        line_start=1,
        line_end=15,
        reason="BM25 match",
        score=0.90,
        priority="supporting",
        source_role="reference",
        metadata={"snippet": "Generic introductory discussion repeating keywords."},
    )

    # Candidate without text snippet
    no_text_span = SourceSpan(
        path="sections/appendix.tex",
        line_start=1,
        line_end=10,
        reason="File match",
        score=0.20,
        priority="background",
    )

    input_spans = [unrelated_span, dep_span, target_span, citation_span, no_text_span]

    prioritized = generator._prioritize_and_bound_reranker_candidates(
        input_spans,
        target_card=target_card,
        dep_cards=[dep_card],
        task="Revisar methods considerando \\cite{knuth1984}",
        limit=20,
    )

    # 1. Target span is preserved first (protected)
    assert prioritized[0].source_role == "target_text"

    # 2. Anchored spans (theory.tex and citation) are promoted ahead of generic unrelated spans
    candidate_spans_with_text = [
        s for s in prioritized
        if s.source_role != "target_text" and (s.metadata or {}).get("snippet")
    ]
    # The first candidates must be the anchored spans
    anchored_paths = {candidate_spans_with_text[0].path, candidate_spans_with_text[1].path}
    assert "sections/theory.tex" in anchored_paths
    assert "sections/lit.tex" in anchored_paths

    # 3. Non-anchored unrelated span comes after anchored candidates
    assert candidate_spans_with_text[2].path == "sections/intro.tex"

    # 4. Spans with no text snippet are retained at the end
    assert prioritized[-1].path == "sections/appendix.tex"
