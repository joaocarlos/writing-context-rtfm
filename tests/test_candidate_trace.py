"""Tests for candidate trace tracking, diagnostic observability, and behavioral neutrality."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from writing_context_rtfm.candidate_trace import (
    CandidateTraceTracker,
    compute_candidate_id,
    compute_evidence_id,
    compute_span_candidate_id,
)
from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import (
    FILTER_LOW_SCORE,
    REJECT_MAX_SOURCE_SPANS,
    REJECT_PROVIDER_REFERENCE_QUOTA,
    OwnershipAuditRecord,
    RTFMResult,
    SourceSpan,
)
from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards
from writing_context_rtfm.storage import ExtensionStore


def test_deterministic_candidate_and_evidence_ids() -> None:
    cid1 = compute_candidate_id(
        "src/intro.tex", 1, 10, snippet="Introductory text", provider="rtfm"
    )
    cid2 = compute_candidate_id(
        "src/intro.tex", 1, 10, snippet="Introductory text", provider="rtfm"
    )
    assert cid1 == cid2
    assert len(cid1) == 16

    span = SourceSpan(
        path="src/intro.tex",
        line_start=1,
        line_end=10,
        reason="ref",
        score=0.9,
        metadata={"snippet": "Introductory text", "citekey": "knuth1984"},
    )
    eid1 = compute_evidence_id(span)
    eid2 = compute_evidence_id(span)
    assert eid1 == eid2
    assert eid1 == "citekey:knuth1984"


def test_tracker_funnel_cardinality_and_immutable_traces() -> None:
    tracker = CandidateTraceTracker()
    span1 = SourceSpan(
        path="src/main.tex",
        line_start=1,
        line_end=15,
        reason="Target intro",
        score=1.0,
        source_role="target_text",
        metadata={"snippet": "Hello world"},
    )
    span2 = SourceSpan(
        path="src/deps.tex",
        line_start=10,
        line_end=30,
        reason="Dep background",
        score=0.85,
        source_role="dependency",
        metadata={"snippet": "Background details"},
    )
    span_bib = SourceSpan(
        path="refs.bib",
        line_start=1,
        line_end=20,
        reason="Bib entry",
        score=0.7,
        source_role="reference",
        metadata={"snippet": "@article{foo, ...}"},
    )

    # 1. Retrieved
    tracker.record_retrieved(span1, query="intro")
    tracker.record_retrieved(span2, query="deps")
    tracker.record_retrieved(span_bib, query="refs")

    # 2. Normalized
    cid1 = compute_span_candidate_id(span1)
    cid2 = compute_span_candidate_id(span2)
    tracker.record_normalized(cid1, span1, snapped=False)
    tracker.record_normalized(cid2, span2, snapped=False)

    # 3. Deduplicated
    tracker.record_deduplicated(span1, kept=True)
    tracker.record_deduplicated(span2, kept=True)

    # 4. Excluded by ownership
    rec = OwnershipAuditRecord(
        candidate_id=compute_span_candidate_id(span_bib),
        evidence_id="ev1",
        path="refs.bib",
        line_start=1,
        line_end=20,
        identities=["citekey:foo"],
        replacement_found=True,
        replacement_candidate_id="rep1",
        replacement_provider="bibtex",
    )
    tracker.record_excluded_ownership(span_bib, rec)

    # 5. Filtered
    tracker.record_filtered(span2, reason=FILTER_LOW_SCORE, action="filter_score")

    # 6. Exposed
    tracker.record_exposed(span1)

    # 7. Eligible
    tracker.record_eligible(span1)

    # 8. Selected
    tracker.record_selected(span1)

    diagnostics = tracker.build_diagnostics(ownership_audit=[rec])
    funnel = diagnostics.funnel

    # Invariants
    assert funnel.retrieved == 3
    assert funnel.normalized == 2
    assert funnel.deduplicated == 2
    assert funnel.excluded == 1
    assert funnel.exposed == funnel.deduplicated - funnel.excluded  # 2 - 1 = 1
    assert funnel.filtered == 1
    assert funnel.eligible == funnel.exposed - 0
    assert funnel.selected == 1
    assert funnel.selected <= funnel.eligible

    # Immutable trace checks
    trace_map = {t.candidate_id: t for t in diagnostics.candidates}
    trace1 = trace_map[cid1]
    assert isinstance(trace1.events, tuple)
    assert trace1.events[-1].stage == "selected"


def test_behavioral_neutrality_and_diagnostics_equivalence(tmp_path: Path) -> None:
    # Setup test workspace
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "paper.tex").write_text(
        "\\section{Introduction}\nThis is the intro.\n\\section{Methods}\nThis is methods.\n",
        encoding="utf-8",
    )

    cards = SectionCards(
        version=1,
        document=DocumentCard(
            title="Test Doc",
            thesis="Main thesis.",
        ),
        sections={
            "intro": SectionCard(
                id="intro",
                path="paper.tex",
                title="Introduction",
                role="intro",
                constraints=["Do not mention future work."],
            )
        },
    )

    mock_adapter = MagicMock(spec=RTFMAdapter)
    mock_adapter.search.return_value = [
        RTFMResult(
            path="paper.tex",
            line_start=1,
            line_end=2,
            snippet="\\section{Introduction}\nThis is the intro.",
            score=0.95,
            metadata={},
        ),
        RTFMResult(
            path="paper.tex",
            line_start=3,
            line_end=4,
            snippet="\\section{Methods}\nThis is methods.",
            score=0.40,
            metadata={},
        ),
    ]

    mock_store = MagicMock(spec=ExtensionStore)

    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(corpus="test", project_root=str(workspace)),
        section_cards=SectionCardsConfig(path=str(workspace / "section_cards.yaml")),
        context=ContextConfig(min_score=0.5, max_source_spans=10),
        cache=CacheConfig(enabled=False),
    )

    generator = ContextPackGenerator(
        config=config,
        section_cards=cards,
        adapter=mock_adapter,
        store=mock_store,
    )

    # Call generate WITHOUT diagnostics
    pack_plain = generator.generate(
        task="Draft the intro",
        target="intro",
        token_budget=4000,
        include_diagnostics=False,
    )

    # Call generate WITH diagnostics
    pack_diag = generator.generate(
        task="Draft the intro",
        target="intro",
        token_budget=4000,
        include_diagnostics=True,
    )

    # Behavioral neutrality assertions:
    assert pack_plain.diagnostics is None
    assert pack_diag.diagnostics is not None

    # Source spans, estimated tokens, status, quality must be identical
    assert len(pack_plain.source_spans) == len(pack_diag.source_spans)
    for s_plain, s_diag in zip(pack_plain.source_spans, pack_diag.source_spans, strict=True):
        assert s_plain.path == s_diag.path
        assert s_plain.line_start == s_diag.line_start
        assert s_plain.line_end == s_diag.line_end
        assert s_plain.score == s_diag.score
        assert s_plain.source_role == s_diag.source_role

    assert pack_plain.estimated_tokens == pack_diag.estimated_tokens
    assert pack_plain.status == pack_diag.status
    assert pack_plain.warnings == pack_diag.warnings

    # Quality metrics
    q_plain = dict(pack_plain.quality or {})
    q_diag = dict(pack_diag.quality or {})
    assert q_plain == q_diag

    # Funnel validations on diagnostic run
    funnel = pack_diag.diagnostics.funnel
    assert funnel.selected == len(pack_diag.source_spans)
    assert funnel.selected <= funnel.eligible
    assert funnel.eligible <= funnel.exposed
    assert funnel.exposed <= funnel.deduplicated
    assert funnel.deduplicated <= funnel.normalized
    assert funnel.normalized <= funnel.retrieved


def test_rejection_reasons_in_diagnostics(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.tex").write_text("Hello world", encoding="utf-8")

    mock_adapter = MagicMock(spec=RTFMAdapter)
    mock_adapter.search.return_value = [
        RTFMResult(
            path="main.tex",
            line_start=1,
            line_end=5,
            snippet="Hello world candidate 1",
            score=0.9,
            metadata={},
        ),
        RTFMResult(
            path="main.tex",
            line_start=10,
            line_end=15,
            snippet="Hello world candidate 2",
            score=0.8,
            metadata={},
        ),
        RTFMResult(
            path="main.tex",
            line_start=20,
            line_end=25,
            snippet="Hello world candidate 3",
            score=0.7,
            metadata={},
        ),
    ]

    mock_store = MagicMock(spec=ExtensionStore)

    # Test max_source_spans rejection reason
    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(corpus="test", project_root=str(workspace)),
        section_cards=SectionCardsConfig(path=str(workspace / "section_cards.yaml")),
        context=ContextConfig(min_score=0.1, max_source_spans=1),
        cache=CacheConfig(enabled=False),
    )
    generator = ContextPackGenerator(
        config=config,
        section_cards=None,
        adapter=mock_adapter,
        store=mock_store,
    )

    pack = generator.generate(
        task="Test task",
        target=None,
        token_budget=1000,
        include_diagnostics=True,
    )

    assert pack.diagnostics is not None
    rejected_traces = [
        t for t in pack.diagnostics.candidates if t.events and t.events[-1].stage == "rejected"
    ]
    assert len(rejected_traces) > 0
    assert any(t.events[-1].reason == REJECT_MAX_SOURCE_SPANS for t in rejected_traces)


def test_provider_reference_quota_rejection_in_diagnostics(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.tex").write_text("Hello world\n" * 50, encoding="utf-8")

    mock_adapter = MagicMock(spec=RTFMAdapter)
    mock_adapter.search.return_value = []

    mock_provider = MagicMock()
    mock_provider.provider_id = "bibtex"
    mock_provider.is_available.return_value = True
    # Two provider reference spans that will exceed the reference quota (20% of usable budget)
    mock_provider.fetch_context.return_value = [
        SourceSpan(
            path="refs.bib",
            line_start=1,
            line_end=50,
            reason="Reference entry 1",
            score=0.95,
            source_role="reference",
            metadata={
                "provider_id": "bibtex",
                "snippet": "@article{one,\n" + "title={A},\n" * 30 + "}",
            },
        ),
        SourceSpan(
            path="refs.bib",
            line_start=51,
            line_end=100,
            reason="Reference entry 2",
            score=0.90,
            source_role="reference",
            metadata={
                "provider_id": "bibtex",
                "snippet": "@article{two,\n" + "title={B},\n" * 30 + "}",
            },
        ),
    ]

    mock_store = MagicMock(spec=ExtensionStore)

    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(corpus="test", project_root=str(workspace)),
        section_cards=SectionCardsConfig(path=str(workspace / "section_cards.yaml")),
        context=ContextConfig(
            min_score=0.1,
            max_source_spans=10,
            role_budgets={
                "target_text": 0.5,
                "local_context": 0.2,
                "dependency": 0.2,
                "reference": 0.1,
            },
        ),
        cache=CacheConfig(enabled=False),
    )

    generator = ContextPackGenerator(
        config=config,
        section_cards=None,
        adapter=mock_adapter,
        store=mock_store,
        providers=[mock_provider],
    )

    pack = generator.generate(
        task="Test task",
        target=None,
        token_budget=1000,  # Budget > baseline (150) but reference quota is small
        strict_budget=True,
        include_diagnostics=True,
    )

    assert pack.diagnostics is not None
    rejected_traces = [
        t for t in pack.diagnostics.candidates if t.events and t.events[-1].stage == "rejected"
    ]
    assert len(rejected_traces) > 0
    assert any(t.events[-1].reason == REJECT_PROVIDER_REFERENCE_QUOTA for t in rejected_traces)
    assert pack.diagnostics.rejections_by_reason.get(REJECT_PROVIDER_REFERENCE_QUOTA, 0) > 0


def test_cross_process_id_determinism() -> None:
    """Ensure candidate_id and evidence_id hashing is independent of process seeds/Python hash seeds."""
    import os
    import subprocess
    import sys

    python_code = """
from writing_context_rtfm.candidate_trace import compute_candidate_id, compute_evidence_id
from writing_context_rtfm.schemas import SourceSpan

cid = compute_candidate_id("src/chapter1/intro.tex", 10, 25, snippet="Sample text content", provider="rtfm")
span = SourceSpan(path="src/chapter1/intro.tex", line_start=10, line_end=25, reason="ref", score=0.8, metadata={"citekey": "Knuth1984"})
eid = compute_evidence_id(span)
print(f"{cid}|{eid}")
"""
    # Run in subprocess 1 with PYTHONHASHSEED=0
    env1 = dict(os.environ, PYTHONHASHSEED="0")
    out1 = subprocess.check_output([sys.executable, "-c", python_code], env=env1, text=True).strip()

    # Run in subprocess 2 with PYTHONHASHSEED=987654
    env2 = dict(os.environ, PYTHONHASHSEED="987654")
    out2 = subprocess.check_output([sys.executable, "-c", python_code], env=env2, text=True).strip()

    assert out1 == out2
    cid, eid = out1.split("|")
    assert len(cid) == 16
    assert eid == "citekey:knuth1984"


def test_privacy_path_sanitization_and_bounded_trace_size() -> None:
    """Ensure absolute paths with home directories are sanitized and diagnostic payload size is bounded."""
    from writing_context_rtfm.candidate_trace import sanitize_path

    # Path sanitization checks
    assert (
        sanitize_path("/Users/joaocarlos/Developer/Projects/paper/intro.tex") == "paper/intro.tex"
    )
    assert sanitize_path("/home/ubuntu/project/sections/methods.tex") == "sections/methods.tex"
    assert sanitize_path("./sections/results.tex") == "sections/results.tex"

    tracker = CandidateTraceTracker()
    # Ingest 300 candidates
    for i in range(300):
        span = SourceSpan(
            path=f"/Users/joaocarlos/secret_project/file_{i}.tex",
            line_start=i,
            line_end=i + 5,
            reason=f"Candidate {i}",
            score=0.5 + (i / 1000.0),
            source_role="reference",
            metadata={"snippet": f"Snippet {i}"},
        )
        tracker.record_retrieved(span, query="test")
        cid = compute_span_candidate_id(span)
        tracker.record_normalized(cid, span)
        tracker.record_deduplicated(span, kept=True)
        tracker.record_exposed(span)
        tracker.record_eligible(span)
        if i < 10:
            tracker.record_selected(span)
        else:
            tracker.record_rejected(span, reason=REJECT_MAX_SOURCE_SPANS)

    # Build diagnostics with max_traces cap of 50
    diag = tracker.build_diagnostics(max_traces=50)

    # Funnel must accurately record all 300
    assert diag.funnel.retrieved == 300
    assert diag.funnel.selected == 10
    assert diag.funnel.exposed == 300

    # Traces list must be capped at 50 to prevent payload blowup
    assert len(diag.candidates) == 50

    # All paths in traces must be sanitized (no '/Users/joaocarlos')
    for t in diag.candidates:
        assert not t.path.startswith("/Users/")
        assert not t.path.startswith("/home/")


def test_passive_ownership_audit_zero_side_effects() -> None:
    """Verify audit_passive_bibtex_ownership never initiates IO or provider search/fetch."""
    from writing_context_rtfm.bibtex_handoff import audit_passive_bibtex_ownership

    excluded_spans = [
        SourceSpan(
            path="refs.bib",
            line_start=1,
            line_end=10,
            reason="Bib item",
            score=0.9,
            metadata={
                "citekey": "vaswani2017",
                "snippet": "@article{vaswani2017,\ntitle={Attention Is All You Need}\n}",
            },
        )
    ]
    provider_spans = [
        SourceSpan(
            path="refs.bib",
            line_start=1,
            line_end=10,
            reason="Provider bib item",
            score=0.95,
            source_role="reference",
            metadata={
                "provider_id": "bibtex",
                "citekey": "vaswani2017",
                "title": "Attention Is All You Need",
            },
        )
    ]

    mock_provider = MagicMock()
    records = audit_passive_bibtex_ownership(
        excluded_spans=excluded_spans,
        provider_spans=provider_spans,
        provider=mock_provider,
    )

    assert len(records) == 1
    assert records[0].replacement_found is True
    assert records[0].replacement_provider == "bibtex"
    assert "citekey:vaswani2017" in records[0].identities

    # Verify zero side effects: mock_provider methods were NOT called
    mock_provider.fetch_context.assert_not_called()
    mock_provider.search.assert_not_called()


def test_schema_backward_compatibility_without_diagnostics() -> None:
    """Ensure older JSON payloads without diagnostics deserialize gracefully with diagnostics=None."""
    from writing_context_rtfm.schemas import ContextPack

    legacy_json = json.dumps(
        {
            "task": "Legacy task",
            "target": "sec1",
            "document_thesis": "Thesis",
            "prior_claims": ["claim 1"],
            "terminology": {"SLM": "Small Language Model"},
            "constraints": ["Rule 1"],
            "source_spans": [],
            "estimated_tokens": 120,
            "status": "complete",
            "warnings": [],
        }
    )

    data = json.loads(legacy_json)
    # Reconstructing ContextPack from data dictionary
    pack = ContextPack(**data)
    assert pack.diagnostics is None
    assert pack.task == "Legacy task"
    assert pack.status == "complete"


def test_golden_explain_pack_output(capsys: Any) -> None:
    """Golden test verifying formatted terminal output structure and canonical labels for explain-pack."""
    from writing_context_rtfm.cli import _print_pack_explanation
    from writing_context_rtfm.schemas import (
        CandidateFunnel,
        ContextPack,
        ContextPackDiagnostics,
        OwnershipAuditRecord,
        SourceSpan,
    )

    mock_pack = ContextPack(
        task="Write intro section",
        target="intro",
        document_thesis="Thesis statement",
        prior_claims=[],
        terminology={},
        constraints=["Preserve LaTeX citations"],
        source_spans=[
            SourceSpan(
                path="sections/intro.tex",
                line_start=1,
                line_end=20,
                reason="Target section text",
                score=1.0,
                source_role="target_text",
            ),
        ],
        estimated_tokens=250,
        status="complete",
        warnings=["Note: Informational notice"],
        diagnostics=ContextPackDiagnostics(
            funnel=CandidateFunnel(
                retrieved=10,
                normalized=9,
                deduplicated=8,
                excluded=1,
                exposed=7,
                filtered=2,
                eligible=5,
                selected=1,
            ),
            candidates=[],
            ownership_audit=[
                OwnershipAuditRecord(
                    candidate_id="cand_123",
                    evidence_id="citekey:knuth1984",
                    path="references.bib",
                    line_start=1,
                    line_end=15,
                    identities=["citekey:knuth1984"],
                    replacement_found=True,
                    replacement_candidate_id="cand_456",
                    replacement_provider="bibtex",
                )
            ],
            rejections_by_reason={"REJECT_PROVIDER_REFERENCE_QUOTA": 2, "REJECT_TOKEN_BUDGET": 2},
        ),
    )

    _print_pack_explanation(mock_pack, as_json=False)
    captured = capsys.readouterr().out

    # Assert exact golden headers and formatted fields
    assert "=== Candidate Funnel ===" in captured
    assert "Retrieved:     10" in captured
    assert "Normalized:    9" in captured
    assert "Deduplicated:  8" in captured
    assert "Excluded:      1" in captured
    assert "Exposed:       7" in captured
    assert "Filtered:      2" in captured
    assert "Eligible:      5" in captured
    assert "Selected:      1" in captured

    assert "=== Selected Spans (1) ===" in captured
    assert (
        "[1] sections/intro.tex:1-20 (role=target_text, score=1.00) -> Target section text"
        in captured
    )

    assert "=== Rejections by Reason ===" in captured
    assert "REJECT_PROVIDER_REFERENCE_QUOTA: 2" in captured
    assert "REJECT_TOKEN_BUDGET: 2" in captured

    assert "=== Excluded by Provider Ownership (1) ===" in captured
    assert "references.bib:1-15 (citekey:knuth1984) -> replaced by bibtex" in captured

    assert "=== Summary ===" in captured
    assert "Status:           complete" in captured
    assert "Estimated Tokens: 250" in captured
