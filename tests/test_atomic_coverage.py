"""Quality-first atomic coverage behavior for writing context packs."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.hashing import compute_task_hash
from writing_context_rtfm.schemas import RTFMResult, SourceSpan
from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards
from writing_context_rtfm.server import (
    _is_degrading_pack_warning,
    handle_refresh_index,
)
from writing_context_rtfm.storage import ExtensionStore


def _result(path: str, score: float, snippet: str, rank: int) -> RTFMResult:
    return RTFMResult(
        path=path,
        line_start=1,
        line_end=20,
        snippet=snippet,
        score=score,
        metadata={"rank": rank},
    )


def _generator(tmp_path: Path, *, max_budget: int = 2_000) -> tuple[ContextPackGenerator, MagicMock]:
    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root=str(tmp_path)),
        context=ContextConfig(
            max_token_budget=max_budget,
            max_search_results_per_query=10,
            max_source_spans=4,
            reserved_generation_margin=0.0,
        ),
        cache=CacheConfig(enabled=False, path=str(tmp_path / "cache.sqlite")),
        section_cards=SectionCardsConfig(path=str(tmp_path / "cards.yaml")),
    )
    adapter = MagicMock()
    store = ExtensionStore(config.cache.path)
    store.init_db()
    return ContextPackGenerator(config, None, adapter, store), adapter


def test_elastic_selection_prioritizes_required_atom_over_higher_ranked_noise(tmp_path: Path) -> None:
    generator, adapter = _generator(tmp_path)
    noise = _result("noise.tex", 0.99, "general background " * 90, 1)
    evidence = _result(
        "evidence.tex",
        0.20,
        "The atomic calibration protocol uses a sealed reference chamber. " * 65,
        2,
    )
    adapter.search.return_value = [noise, evidence]

    pack = generator.generate(
        task="Draft the method.",
        target=None,
        token_budget=220,
        must_consider=["atomic calibration protocol"],
        strict_budget=False,
    )

    assert "evidence.tex" in {span.path for span in pack.source_spans}
    coverage = pack.quality["atomic_coverage"]
    assert coverage["ratio"] == 1.0
    assert coverage["uncovered"] == []
    assert coverage["effective_token_budget"] > coverage["requested_token_budget"]
    assert coverage["expanded_for_coverage"] is True
    assert adapter.search.call_count == pack.quality["queries_issued"]


def test_strict_budget_does_not_expand_for_atomic_coverage(tmp_path: Path) -> None:
    generator, adapter = _generator(tmp_path)
    adapter.search.return_value = [
        _result(
            "evidence.tex",
            0.9,
            "The atomic calibration protocol uses a sealed reference chamber. " * 100,
            1,
        )
    ]

    pack = generator.generate(
        task="Draft the method.",
        target=None,
        token_budget=120,
        must_consider=["atomic calibration protocol"],
        strict_budget=True,
    )

    coverage = pack.quality["atomic_coverage"]
    assert pack.estimated_tokens <= 120
    assert coverage["effective_token_budget"] == 120
    assert coverage["expanded_for_coverage"] is False
    assert coverage["uncovered"] == ["must_consider:1"]
    assert pack.status == "degraded"


def test_missing_atom_is_reported_without_retrieval_retry(tmp_path: Path) -> None:
    generator, adapter = _generator(tmp_path)
    adapter.search.return_value = [
        _result("background.tex", 0.9, "Unrelated historical background.", 1)
    ]

    pack = generator.generate(
        task="Draft the method.",
        target=None,
        token_budget=1_000,
        must_consider=["sealed reference chamber"],
    )

    coverage = pack.quality["atomic_coverage"]
    assert coverage["ratio"] == 0.0
    assert coverage["uncovered"] == ["must_consider:1"]
    assert pack.status == "degraded"
    assert any("direct-read" in warning and "must_consider:1" in warning for warning in pack.warnings)
    assert adapter.search.call_count == pack.quality["queries_issued"]


def test_explicit_task_citation_key_is_an_atomic_obligation(tmp_path: Path) -> None:
    generator, adapter = _generator(tmp_path)
    citation = _result("bibtex:Smith2025", 0.25, "Calibration evidence", 2)
    citation = replace(citation, metadata={"citekey": "Smith2025", "rank": 2})
    adapter.search.return_value = [
        _result("noise.tex", 0.99, "General background " * 80, 1),
        citation,
    ]

    pack = generator.generate(
        task=r"Explain calibration and cite \cite{Smith2025}.",
        target=None,
        token_budget=300,
    )

    assert "bibtex:Smith2025" in {span.path for span in pack.source_spans}
    citation_atoms = [
        item for item in pack.quality["atomic_coverage"]["obligations"]
        if item["kind"] == "citation"
    ]
    assert citation_atoms == [
        {
            "id": "citation:Smith2025",
            "kind": "citation",
            "label": "Smith2025",
            "covered": True,
            "source_paths": ["bibtex:Smith2025"],
        }
    ]


def test_must_consider_changes_cache_identity() -> None:
    base = compute_task_hash("Draft", None, 1_000, must_consider=[])
    required = compute_task_hash(
        "Draft", None, 1_000, must_consider=["sealed reference chamber"]
    )

    assert base != required


def test_elastic_budget_stops_at_configured_ceiling(tmp_path: Path) -> None:
    generator, adapter = _generator(tmp_path, max_budget=300)
    adapter.search.return_value = [
        _result(
            "evidence.tex",
            0.9,
            "The atomic calibration protocol uses a sealed reference chamber. " * 100,
            1,
        )
    ]

    pack = generator.generate(
        task="Draft the method.",
        target=None,
        token_budget=120,
        must_consider=["atomic calibration protocol"],
        strict_budget=False,
    )

    coverage = pack.quality["atomic_coverage"]
    assert coverage["effective_token_budget"] == 300
    assert coverage["uncovered"] == ["must_consider:1"]
    assert pack.estimated_tokens <= 300
    assert pack.status == "degraded"


def test_fixed_card_evidence_satisfies_atom_without_extra_span(tmp_path: Path) -> None:
    generator, adapter = _generator(tmp_path)
    generator.section_cards = SectionCards(
        version=1,
        document=DocumentCard(title="Personal project"),
        sections={
            "method": SectionCard(
                id="method",
                title="Method",
                role="Explain calibration",
                must_preserve=["The sealed reference chamber is the calibration baseline."],
            )
        },
    )
    adapter.search.return_value = []

    pack = generator.generate(
        task="Draft the method.",
        target="method",
        token_budget=500,
        must_consider=["sealed reference chamber"],
    )

    coverage = pack.quality["atomic_coverage"]
    assert coverage["ratio"] == 1.0
    assert coverage["uncovered"] == []
    assert coverage["obligations"][0]["source_paths"] == []
    assert coverage["expanded_for_coverage"] is False


def test_elastic_notes_do_not_degrade_server_status() -> None:
    assert not _is_degrading_pack_warning(
        "Note: Atomic evidence coverage expanded the token budget once from 500 to 900 tokens."
    )
    assert not _is_degrading_pack_warning(
        "2 candidate span(s) were dropped to strictly respect the token budget."
    )
    assert _is_degrading_pack_warning(
        "Atomic evidence coverage is incomplete for must_consider:1."
    )


def test_omitted_provider_span_does_not_count_as_fixed_evidence(tmp_path: Path) -> None:
    generator, adapter = _generator(tmp_path, max_budget=300)
    adapter.search.return_value = []
    provider = MagicMock()
    provider.provider_id = "local_reference"
    provider.is_available.return_value = True
    provider.get_fingerprint.return_value = None
    provider.fetch_context.return_value = [
        SourceSpan(
            path="reference:oversized",
            line_start=None,
            line_end=None,
            reason="Provider marked this span essential",
            score=0.95,
            priority="essential",
            source_role="reference",
            metadata={
                "snippet": "The sealed reference chamber is required. " * 100
            },
        )
    ]
    generator.providers = [provider]

    pack = generator.generate(
        task="Draft the method.",
        target=None,
        token_budget=120,
        must_consider=["sealed reference chamber"],
    )

    assert pack.source_spans == []
    assert pack.quality["atomic_coverage"]["uncovered"] == ["must_consider:1"]
    assert pack.status == "degraded"


@patch("writing_context_rtfm.server.resolve_rtfm_db_path")
@patch("writing_context_rtfm.server.ExtensionStore")
@patch("writing_context_rtfm.server.compute_rtfm_fingerprint", return_value="fingerprint")
@patch("writing_context_rtfm.server.RTFMAdapter")
@patch("writing_context_rtfm.server.load_config")
def test_refresh_index_reports_completed_sync_without_serializing_none(
    load_config: MagicMock,
    adapter_class: MagicMock,
    _fingerprint: MagicMock,
    store_class: MagicMock,
    _resolve_db: MagicMock,
    tmp_path: Path,
) -> None:
    config = MagicMock()
    config.rtfm.corpus = "manuscript"
    config.cache.path = str(tmp_path / "cache.sqlite")
    load_config.return_value = config
    adapter_class.return_value.sync.return_value = None

    response = handle_refresh_index({"project_root": str(tmp_path)})
    payload = json.loads(response["content"][0]["text"])

    assert payload["status"] == "ok"
    assert payload["rtfm_sync"] == "completed"
    adapter_class.return_value.sync.assert_called_once_with(corpus="manuscript")
    store_class.return_value.invalidate_for_fingerprint.assert_called_once_with(
        "fingerprint"
    )
