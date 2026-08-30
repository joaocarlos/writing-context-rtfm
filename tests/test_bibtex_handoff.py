from __future__ import annotations

from pathlib import Path

from writing_context_rtfm.bibtex_handoff import (
    HANDOFF_VARIANTS,
    BibTeXHandoffPolicy,
    build_bibtex_handoff_report,
)
from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.providers.bibtex import BibTeXProvider
from writing_context_rtfm.schemas import ProviderConfig, SourceSpan

BIB = """@article{missingKey,
  title={Missing Evidence},
  author={Doe, Jane},
  year={2026},
  doi={10.1000/missing}
}

@article{otherKey,
  title={Other Evidence},
  year={2025}
}
"""


def _provider(tmp_path: Path) -> BibTeXProvider:
    (tmp_path / "references.bib").write_text(BIB, encoding="utf-8")
    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root=str(tmp_path)),
        context=ContextConfig(),
        cache=CacheConfig(enabled=False),
        section_cards=SectionCardsConfig(path="dummy.yaml"),
        providers={"bibtex": ProviderConfig(enabled=True)},
    )
    return BibTeXProvider(config)


def _excluded() -> SourceSpan:
    return SourceSpan(
        path="references.bib",
        line_start=1,
        line_end=6,
        reason="RTFM query match",
        score=0.8,
        metadata={"snippet": BIB.split("\n\n", 1)[0]},
    )


def _other_provider_span() -> SourceSpan:
    return SourceSpan(
        path="bibtex:otherKey",
        line_start=None,
        line_end=None,
        reason="Existing provider result",
        score=0.7,
        metadata={
            "snippet": "Other Evidence",
            "citekey": "otherKey",
            "title": "Other Evidence",
        },
    )


def test_handoff_variants_repair_only_missing_structured_identity(tmp_path) -> None:
    expected = ({"path": "references.bib", "line_start": 1, "line_end": 6},)

    current = BibTeXHandoffPolicy(
        _provider(tmp_path), "current", expected_sources=expected
    )
    fallback = BibTeXHandoffPolicy(
        _provider(tmp_path), "fallback", expected_sources=expected
    )
    reconstruction = BibTeXHandoffPolicy(
        _provider(tmp_path), "reconstruction", expected_sources=expected
    )

    assert current([_excluded()], [_other_provider_span()]) == []
    fallback_spans = fallback([_excluded()], [_other_provider_span()])
    reconstructed = reconstruction([_excluded()], [_other_provider_span()])

    assert fallback_spans[0].path == "references.bib"
    assert fallback_spans[0].metadata["citekeys"] == ["missingKey"]
    assert reconstructed[0].path == "references.bib"
    assert reconstructed[0].metadata["citekey"] == "missingKey"
    assert reconstructed[0].metadata["doi"] == "10.1000/missing"
    assert current.telemetry["bibliographic_handoff_recall"] == 0.0
    assert fallback.telemetry["bibliographic_handoff_recall"] == 1.0
    assert reconstruction.telemetry["bibliographic_handoff_recall"] == 1.0
    assert reconstruction.telemetry["duplicate_identity_count"] == 0


def test_handoff_does_not_add_candidate_when_equivalent_provider_span_exists(
    tmp_path,
) -> None:
    provider = _provider(tmp_path)
    entry = provider.entries_for_source_span("references.bib", 1, 6)[0]
    existing = provider.reconstruct_entry(entry, score=0.9)

    for variant in ("fallback", "reconstruction"):
        policy = BibTeXHandoffPolicy(provider, variant)
        assert policy([_excluded()], [existing]) == []
        assert policy.telemetry["addition_count"] == 0


def test_handoff_report_marks_fix_candidate_but_never_promotes() -> None:
    def record(
        case_id: str, variant: str, repetition: int, affected: bool
    ) -> dict[str, object]:
        repaired = affected and variant != "current"
        selected = not affected or repaired
        duplicate = int(variant == "fallback" and affected)
        return {
            "case_id": case_id,
            "variant": variant,
            "repetition": repetition,
            "metrics": {
                "expected_source_outcomes": [
                    {
                        "selected": selected,
                        "lost_after": "retrieved" if selected else None,
                    }
                ]
            },
            "handoff": {
                "relevant_excluded_entries": int(affected),
                "replaced_relevant_entries": int(repaired),
                "bibliographic_handoff_recall": float(repaired),
                "duplicate_identity_count": duplicate,
                "addition_count": int(repaired),
                "additional_candidate_tokens": 10 if repaired else 0,
            },
            "hard_constraint_violations": 0,
            "costs": {
                "retrieved_candidates": 10,
                "unique_candidates": 10,
                "candidate_spans": 11 if repaired else 10,
                "candidate_tokens_processed": 110 if repaired else 100,
                "effective_candidate_spans": 11 if repaired else 10,
                "effective_candidate_tokens_processed": 110 if repaired else 100,
                "retrieval_latency_ms": 2,
                "fusion": 1,
                "composer": 6,
                "handoff_latency_ms": 1 if repaired else 0,
                "total_latency_ms": 11 if repaired else 10,
                "context_tokens": 100,
            },
        }

    records = [
        record(case_id, variant, repetition, affected=case_id == "affected")
        for repetition in range(1, 6)
        for case_id in ("affected", "control")
        for variant in HANDOFF_VARIANTS
    ]
    report = build_bibtex_handoff_report(
        {"freeze_sha256": "freeze", "records": records}
    )

    reconstruction = report["variants"]["reconstruction"]
    fallback = report["variants"]["fallback"]
    assert reconstruction["bibliographic_handoff_recall"] == 1.0
    assert reconstruction["correctness_fix_candidate"] is True
    assert reconstruction["production_promotion"]["eligible"] is False
    assert "pilot_only" in reconstruction["production_promotion"]["reasons"]
    assert fallback["correctness_fix_candidate"] is False
    assert "bibliographic_duplication" in fallback["production_promotion"]["reasons"]
