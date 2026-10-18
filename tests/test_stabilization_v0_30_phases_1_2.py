"""Regression tests for v0.30.0 Stabilization Plan — Phases 1 and 2."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from writing_context_rtfm import server
from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    ProviderConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import (
    ContextPackGenerator,
    compute_realistic_baseline_tokens,
    is_degrading_pack_warning,
)
from writing_context_rtfm.features import (
    audit_manuscript_terminology,
    cards_scan_command,
)
from writing_context_rtfm.intent_classifier import (
    IntentClassificationResult,
    LocalIntentClassifier,
)
from writing_context_rtfm.proofread import ProofreadPackGenerator
from writing_context_rtfm.providers import get_active_reranker
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.schemas import RTFMResult, SourceSpan
from writing_context_rtfm.section_cards import (
    DocumentCard,
    SectionCard,
    SectionCards,
    load_section_cards,
    migrate_legacy_cards,
)
from writing_context_rtfm.storage import ExtensionStore


def _make_config(
    project_root: str = ".",
    *,
    context: ContextConfig | None = None,
    cache: CacheConfig | None = None,
    section_cards: SectionCardsConfig | None = None,
    providers: dict[str, ProviderConfig] | None = None,
) -> AppConfig:
    return AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root=project_root),
        context=context or ContextConfig(),
        cache=cache or CacheConfig(enabled=False),
        section_cards=section_cards or SectionCardsConfig(),
        providers=providers or {},
    )


def test_get_active_reranker_preserves_modernbert_default_max_length():
    config_default = _make_config(
        providers={
            "local_reranker": ProviderConfig(
                enabled=True,
                extra={"model": "Alibaba-NLP/gte-reranker-modernbert-base"},
            )
        }
    )
    with patch("importlib.util.find_spec", return_value=MagicMock()):
        reranker = get_active_reranker(config_default)
        assert reranker is not None
        assert reranker.max_length == 2048

    config_explicit = _make_config(
        providers={
            "local_reranker": ProviderConfig(
                enabled=True,
                extra={
                    "model": "Alibaba-NLP/gte-reranker-modernbert-base",
                    "max_length": 768,
                },
            )
        }
    )
    with patch("importlib.util.find_spec", return_value=MagicMock()):
        reranker_exp = get_active_reranker(config_explicit)
        assert reranker_exp is not None
        assert reranker_exp.max_length == 768


def test_local_intent_classifier_enforces_local_files_only_and_memoizes_failure():
    clf = LocalIntentClassifier(model_id="org/missing-model", local_files_only=True)
    assert clf.local_files_only is True

    mock_st = MagicMock()
    mock_st.SentenceTransformer.side_effect = OSError("Model not in local cache")
    with patch("importlib.import_module", return_value=mock_st):
        assert clf._get_model() is None
        assert clf._load_failed is True
        # Must only have been called once with local_files_only=True (no online retry)
        assert mock_st.SentenceTransformer.call_count == 1
        mock_st.SentenceTransformer.assert_called_once_with(
            "org/missing-model", local_files_only=True
        )
        # Subsequent call returns None immediately without calling SentenceTransformer again
        assert clf._get_model() is None
        assert mock_st.SentenceTransformer.call_count == 1


def test_local_intent_classifier_downloads_on_demand_when_not_cached():
    clf = LocalIntentClassifier(model_id="org/remote-model")
    assert clf.local_files_only is False

    mock_st = MagicMock()
    mock_model = MagicMock()
    mock_st.SentenceTransformer.side_effect = [OSError("Not in cache"), mock_model]
    with patch("importlib.import_module", return_value=mock_st):
        assert clf._get_model() is mock_model
        assert mock_st.SentenceTransformer.call_count == 2
        mock_st.SentenceTransformer.assert_any_call("org/remote-model", local_files_only=True)
        mock_st.SentenceTransformer.assert_any_call("org/remote-model")


def test_infer_writing_mode_prioritizes_deterministic_regex_and_skips_neural_in_fast_profile(
    tmp_path: Path,
):
    target_file = tmp_path / "sec1.tex"
    target_file.write_text("Existing content", encoding="utf-8")

    # 1. Deterministic regex wins even if neural classifier returns conflicting intent
    conflicting_neural = IntentClassificationResult(
        intent="write",
        score=0.95,
        margin=0.40,
        scores={"write": 0.95},
        is_confident=True,
    )
    with patch(
        "writing_context_rtfm.context_pack.classify_task_intent",
        return_value=conflicting_neural,
    ) as mock_classify:
        mode = ContextPackGenerator._infer_writing_mode(
            mode=None,
            task="Shorten and condense Section 1 to 300 words",
            target="sec1",
            target_path="sec1.tex",
            pr=str(tmp_path),
            target_card=None,
            profile="balanced",
        )
        assert mode == "compress"
        mock_classify.assert_not_called()

    # 2. Fast profile skips neural classifier completely
    with patch(
        "writing_context_rtfm.context_pack.classify_task_intent"
    ) as mock_classify_fast:
        mode_fast = ContextPackGenerator._infer_writing_mode(
            mode=None,
            task="Improve clarity of the argument in Section 1",
            target="sec1",
            target_path="sec1.tex",
            pr=str(tmp_path),
            target_card=None,
            profile="fast",
        )
        assert mode_fast == "rewrite"
        mock_classify_fast.assert_not_called()


def test_resolve_target_guards_numeric_prefix_and_prioritizes_destination_preposition(
    tmp_path: Path,
):
    config = _make_config(project_root=str(tmp_path))
    cards = SectionCards(
        version=2,
        document=DocumentCard(title="Doc"),
        sections={
            "sec_ch5": SectionCard(id="sec_ch5", title="Chapter 5", path="05_results.tex"),
            "sec_ch6": SectionCard(id="sec_ch6", title="Chapter 6", path="06_methods.tex"),
        },
    )
    gen = ContextPackGenerator(config, cards, MagicMock(), MagicMock())

    # 1. Asset filename fig6.tex must NOT match 06_methods.tex
    key_fig, card_fig, path_fig = gen._resolve_target(
        target=None,
        pr=str(tmp_path),
        task="Update caption in fig6.tex",
    )
    assert key_fig is None
    assert card_fig is None
    assert path_fig == "fig6.tex"

    # 2.Explicit target="fig6" must NOT match 06_methods.tex
    key_exp, card_exp, _ = gen._resolve_target(
        target="fig6",
        pr=str(tmp_path),
    )
    assert key_exp is None
    assert card_exp is None

    # 3. Multi-chapter task with destination preposition ("into Chapter 6") resolves to Chapter 6
    key_dest, _, path_dest = gen._resolve_target(
        target=None,
        pr=str(tmp_path),
        task="Integrate the findings from Chapter 5 into Chapter 6",
    )
    assert key_dest == "sec_ch6"
    assert path_dest == "06_methods.tex"


def test_inferred_target_extracts_atomic_target_section_and_passes_resolved_key_to_providers(
    tmp_path: Path,
):
    chap6 = tmp_path / "chapter6.tex"
    chap6.write_text(
        "\\section{Discussion}\nUnbroken target text for Chapter 6.\n",
        encoding="utf-8",
    )
    config = _make_config(
        project_root=str(tmp_path),
        cache=CacheConfig(enabled=False),
        context=ContextConfig(role_budgets={"reference": 0.3}),
    )
    cards = SectionCards(
        version=2,
        document=DocumentCard(title="Doc", thesis="Main thesis"),
        sections={
            "section_chap6": SectionCard(
                id="section_chap6",
                title="Chapter 6: Discussion",
                path="chapter6.tex",
            )
        },
    )
    adapter = MagicMock()
    adapter.search.return_value = []
    store = MagicMock(spec=ExtensionStore)

    mock_provider = MagicMock(spec=BaseContextProvider)
    mock_provider.provider_id = "custom_ref"
    mock_provider.is_available.return_value = True
    mock_provider.fetch_context.return_value = []

    gen = ContextPackGenerator(
        config, cards, adapter, store, providers=[mock_provider]
    )
    pack = gen.generate(
        task="Rewrite Chapter 6 to emphasize limitations",
        target=None,
        token_budget=3000,
        project_root=str(tmp_path),
    )

    # Atomic target span must be extracted even though target=None was passed
    target_spans = [s for s in pack.source_spans if s.source_role == "target_text"]
    assert len(target_spans) == 1
    assert target_spans[0].path == "chapter6.tex"
    assert "Unbroken target text for Chapter 6" in (target_spans[0].metadata or {}).get(
        "snippet", ""
    )

    # Provider must receive resolved_key ("section_chap6") instead of None
    mock_provider.fetch_context.assert_called_once()
    assert mock_provider.fetch_context.call_args[0][1] == "section_chap6"


def test_mmr_diversity_order_preserved_within_priority_tier_and_scores_retained(
    tmp_path: Path,
):
    config = _make_config(
        project_root=str(tmp_path),
        cache=CacheConfig(enabled=False),
    )
    gen = ContextPackGenerator(config, None, MagicMock(), MagicMock())

    # Span 1 and Span 2 are near-identical supporting spans with high scores (0.95, 0.94);
    # Span 3 is a diverse supporting span with slightly lower score (0.85).
    s1 = SourceSpan(
        path="a.tex",
        line_start=1,
        line_end=10,
        reason="R1",
        score=0.95,
        retrieval_score=0.80,
        fusion_score=0.85,
        structural_score=0.90,
        metadata={"snippet": "alpha beta gamma delta epsilon zeta eta theta"},
    )
    s2 = SourceSpan(
        path="b.tex",
        line_start=1,
        line_end=10,
        reason="R2",
        score=0.94,
        retrieval_score=0.79,
        fusion_score=0.84,
        structural_score=0.88,
        metadata={"snippet": "alpha beta gamma delta epsilon zeta eta theta"},
    )
    s3 = SourceSpan(
        path="c.tex",
        line_start=1,
        line_end=10,
        reason="R3",
        score=0.85,
        retrieval_score=0.70,
        fusion_score=0.75,
        structural_score=0.80,
        metadata={"snippet": "completely distinct orthogonal theorem proof convergence"},
    )

    classified = gen._classify_priority([s1, s2, s3], None, [])
    # Score components must be preserved by _classify_priority
    for orig, cl in zip([s1, s2, s3], classified, strict=True):
        assert cl.retrieval_score == orig.retrieval_score
        assert cl.fusion_score == orig.fusion_score
        assert cl.structural_score == orig.structural_score

    adapter = MagicMock()
    adapter.search.return_value = [
        RTFMResult(
            path="a.tex",
            line_start=1,
            line_end=10,
            score=0.95,
            snippet="alpha beta gamma delta epsilon zeta eta theta",
            metadata={},
        ),
        RTFMResult(
            path="b.tex",
            line_start=1,
            line_end=10,
            score=0.94,
            snippet="alpha beta gamma delta epsilon zeta eta theta",
            metadata={},
        ),
        RTFMResult(
            path="c.tex",
            line_start=1,
            line_end=10,
            score=0.85,
            snippet="completely distinct orthogonal theorem proof convergence",
            metadata={},
        ),
    ]
    gen_pack = ContextPackGenerator(config, None, adapter, MagicMock())
    pack = gen_pack.generate(
        task="alpha beta orthogonal convergence",
        target=None,
        token_budget=4000,
        project_root=str(tmp_path),
    )
    paths = [s.path for s in pack.source_spans]
    # MMR promotes c.tex (diverse) ahead of b.tex (near-duplicate of a.tex)
    assert paths == ["a.tex", "c.tex", "b.tex"]


def test_degrading_warning_alignment_between_context_pack_and_server():
    non_degrading = [
        "LaTeX Safety: \\cite{a} detected",
        "Note: Target was inferred",
        "Budget overflow: 2 spans dropped",
        "Card uncertainty: 1 unverified term",
        "Card audit: Section 's1' depends_on unknown section",
        "Pre-flight budget evaluation: Fixed overhead...",
        "3 candidate span(s) were dropped due to budget",
    ]
    for w in non_degrading:
        assert is_degrading_pack_warning(w) is False
        assert server._is_degrading_pack_warning(w) is False

    assert is_degrading_pack_warning("Provider 'zotero' failed: timeout") is True
    assert server._is_degrading_pack_warning("Provider 'zotero' failed: timeout") is True


def test_section_cards_v2_split_merging_lock_decisions_and_cache_invalidation(
    tmp_path: Path,
):
    wc_dir = tmp_path / ".writing-context"
    wc_dir.mkdir()
    gen_path = wc_dir / "cards.generated.yaml"
    over_path = wc_dir / "cards.overrides.yaml"
    lock_path = wc_dir / "cards.lock.json"
    sc_path = wc_dir / "section_cards.yaml"

    gen_data = {
        "version": 2,
        "document": {"title": "Gen Doc", "thesis": "Gen Thesis"},
        "sections": {
            "sec1": {
                "identity": {"source": "old.tex", "selector": "sec:old"},
                "structure": {"title": "Section 1"},
                "purpose": {"value": "Gen Purpose", "status": "generated"},
                "key_terms": [
                    {"value": "TermA", "status": "generated"},
                    {"value": "TermB", "status": "generated"},
                    {"value": "TermC", "status": "generated"},
                ],
                "dependencies": [
                    {"target": "sec0", "status": "generated"},
                    {"target": "sec_bad", "status": "generated"},
                ],
            }
        },
    }
    over_data = {
        "version": 2,
        "document": None,
        "sections": {
            "sec1": {
                "path": "override_sec1.tex",
                "selector": "sec:override",
                "key_terms": ["TermA"],
            }
        },
    }
    lock_data = {
        "sections": {
            "sec1": {
                "decisions": {
                    "key_terms:TermC": "rejected",
                    "dependencies:sec_bad": "rejected",
                    "dependencies:sec0": "accepted",
                }
            }
        }
    }

    gen_path.write_text(yaml.safe_dump(gen_data), encoding="utf-8")
    over_path.write_text(yaml.safe_dump(over_data), encoding="utf-8")
    lock_path.write_text(json.dumps(lock_data), encoding="utf-8")

    merged = load_section_cards(str(sc_path))
    assert merged is not None
    sec1 = merged.sections["sec1"]
    # Overrides path and selector take precedence over generated identity
    assert sec1.path == "override_sec1.tex"
    assert sec1.selector == "sec:override"
    # TermA (accepted via overrides) and TermB (generated) are both kept; TermC (rejected in lock) is excluded
    assert sec1.key_terms == ["TermA", "TermB"]
    assert sec1.unverified_key_terms == ["TermB"]
    # sec0 (accepted in lock) is kept and verified; sec_bad (rejected in lock) is excluded
    assert sec1.depends_on == ["sec0"]
    assert sec1.unverified_dependencies == []

    # Verify ContextPackGenerator._get_section_cards_hash changes when cards.overrides.yaml changes
    cfg = _make_config(
        project_root=str(tmp_path),
        section_cards=SectionCardsConfig(path=str(sc_path)),
    )
    gen = ContextPackGenerator(cfg, merged, MagicMock(), MagicMock())
    h1 = gen._get_section_cards_hash(sc_path, "fallback")
    over_data["sections"]["sec1"]["title"] = "Updated Title"
    over_path.write_text(yaml.safe_dump(over_data), encoding="utf-8")
    h2 = gen._get_section_cards_hash(sc_path, "fallback")
    assert h1 != h2


def test_migrate_legacy_cards_preserves_path_and_selector_and_loads_without_generated(
    tmp_path: Path,
):
    wc_dir = tmp_path / ".writing-context"
    wc_dir.mkdir()
    legacy_path = wc_dir / "section_cards.yaml"
    legacy_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "document": {"title": "Legacy Title", "thesis": "Legacy Thesis"},
                "sections": {
                    "sec_intro": {
                        "title": "Introduction",
                        "role": "Setup context",
                        "path": "chapters/intro.tex",
                        "selector": "sec:intro",
                        "key_terms": ["KT1"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert migrate_legacy_cards(str(tmp_path)) is True
    assert not legacy_path.exists()
    assert (wc_dir / "cards.overrides.yaml").exists()
    assert not (wc_dir / "cards.generated.yaml").exists()

    # load_section_cards must succeed even when only cards.overrides.yaml exists
    loaded = load_section_cards(str(legacy_path))
    assert loaded is not None
    assert loaded.sections["sec_intro"].path == "chapters/intro.tex"
    assert loaded.sections["sec_intro"].selector == "sec:intro"
    assert loaded.sections["sec_intro"].role == "Setup context"


def test_cards_scan_preserves_rejected_deps_and_filters_acronym_noise(tmp_path: Path):
    (tmp_path / "main.tex").write_text(
        "\\section{Introduction}\\label{sec:intro}\n"
        "% TODO comment with FAKEACRONYM\n"
        "We study Section II and III with \\textbf{ML} and \\cite{REF} for NLP.\n"
        "\\section{Methods}\\label{sec:methods}\n"
        "See Section \\ref{sec:intro} for details.\n",
        encoding="utf-8",
    )
    cards_scan_command(str(tmp_path))

    gen_path = tmp_path / ".writing-context" / "cards.generated.yaml"
    lock_path = tmp_path / ".writing-context" / "cards.lock.json"
    gen_data = yaml.safe_load(gen_path.read_text(encoding="utf-8"))

    intro_sid = next(sid for sid in gen_data["sections"] if "intro" in sid)
    methods_sid = next(sid for sid in gen_data["sections"] if "methods" in sid)

    intro_kts = [kt["value"] for kt in gen_data["sections"][intro_sid]["key_terms"]]
    assert "ML" in intro_kts
    assert "NLP" in intro_kts
    assert "TODO" not in intro_kts
    assert "II" not in intro_kts
    assert "III" not in intro_kts
    assert "FAKEACRONYM" not in intro_kts

    # Reject the dependency in methods_sid and rescan -> must remain rejected
    for dep in gen_data["sections"][methods_sid]["dependencies"]:
        if dep["target"] == intro_sid:
            dep["status"] = "rejected"
    gen_path.write_text(yaml.safe_dump(gen_data), encoding="utf-8")
    lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    lock_data["sections"].setdefault(methods_sid, {}).setdefault("decisions", {})[
        f"dependencies:{intro_sid}"
    ] = "rejected"
    lock_path.write_text(json.dumps(lock_data), encoding="utf-8")

    cards_scan_command(str(tmp_path))
    gen_data_after = yaml.safe_load(gen_path.read_text(encoding="utf-8"))
    deps_after = {
        d["target"]: d["status"]
        for d in gen_data_after["sections"][methods_sid]["dependencies"]
    }
    assert deps_after.get(intro_sid) == "rejected"


def test_multi_section_single_file_audit_and_proofread_card_resolution(tmp_path: Path):
    ms_file = tmp_path / "manuscript.tex"
    ms_file.write_text(
        "\\section{Introduction}\\label{sec:intro}\n"
        "Line 2 of intro.\n"
        "Line 3 of intro.\n"
        "\\section{Methodology}\\label{sec:methods}\n"
        "Line 5 uses CRI in methodology.\n"
        "Line 6 of methodology.\n",
        encoding="utf-8",
    )
    wc_dir = tmp_path / ".writing-context"
    wc_dir.mkdir()
    sc_path = wc_dir / "section_cards.yaml"
    sc_data = {
        "version": 1,
        "document": {
            "title": "Single File Manuscript",
            "terminology": {"CRI": "Contextual Risk Index"},
        },
        "sections": {
            "sec_intro": {
                "title": "Introduction",
                "path": "manuscript.tex",
                "selector": "sec:intro",
                "key_terms": ["CRI"],
            },
            "sec_methods": {
                "title": "Methodology",
                "path": "manuscript.tex",
                "selector": "sec:methods",
                "role": "Explain methods",
                "key_terms": [],
                "depends_on": ["sec_intro"],
            },
        },
    }
    sc_path.write_text(yaml.safe_dump(sc_data), encoding="utf-8")
    (wc_dir / "config.yaml").write_text(
        f"version: 1\nrtfm:\n  project_root: '{tmp_path}'\nsection_cards:\n  path: '{sc_path}'\n",
        encoding="utf-8",
    )

    # 1. Audit must not emit false-positive undeclared warning when one of the sections in manuscript.tex declares CRI
    with patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter") as mock_adapter_cls:
        mock_adapter = MagicMock()
        mock_adapter.search.return_value = [
            RTFMResult(
                path="manuscript.tex",
                line_start=5,
                line_end=5,
                score=1.0,
                snippet="Line 5 uses CRI in methodology.",
                metadata={},
            )
        ]
        mock_adapter_cls.return_value = mock_adapter
        audit_res = audit_manuscript_terminology(str(tmp_path))
        assert audit_res["status"] == "success"
        assert audit_res["report"]["cri"]["warnings"] == []

    # 2. ProofreadPackGenerator._find_section_card resolves sec_methods for line 5
    cfg = _make_config(
        project_root=str(tmp_path),
        section_cards=SectionCardsConfig(path=str(sc_path)),
    )
    cards = load_section_cards(str(sc_path))
    pr_gen = ProofreadPackGenerator(cfg, cards, MagicMock(), MagicMock())
    card_line2 = pr_gen._find_section_card(str(ms_file), line_start=2)
    card_line5 = pr_gen._find_section_card(str(ms_file), line_start=5)
    assert card_line2 is not None and card_line2.id == "sec_intro"
    assert card_line5 is not None and card_line5.id == "sec_methods"


def test_cache_hit_resets_pagination_selection_state(tmp_path: Path):
    db_path = str(tmp_path / "cache.sqlite")
    store = ExtensionStore(db_path)
    store.init_db()

    run_id = "run-cache-page-1"
    run_data = {
        "task_hash": "th1",
        "task": "Write sec",
        "config_hash": "ch1",
        "section_cards_hash": "sh1",
        "retrieval_fingerprint": "fp1",
        "rtfm_index_fingerprint": "fp1",
        "token_budget": 1000,
    }
    payload = {
        "task": "Write sec",
        "run_id": run_id,
        "source_spans": [
            {
                "path": "s1.tex",
                "line_start": 1,
                "line_end": 5,
                "reason": "R1",
                "score": 0.9,
            }
        ],
    }
    sources = [
        {
            "path": "s1.tex",
            "line_start": 1,
            "line_end": 5,
            "reason": "R1",
            "score": 0.9,
            "selected": 1,
        },
        {
            "path": "s2.tex",
            "line_start": 1,
            "line_end": 5,
            "reason": "R2",
            "score": 0.8,
            "selected": 0,
        },
    ]
    store.store_pack(run_id, run_data, payload, sources)

    # First pagination consumes s2.tex
    unselected_1 = store.get_more_context(run_id, limit=5)
    assert len(unselected_1) == 1
    assert store.get_more_context(run_id, limit=5) == []

    # Cache hit resets selection state to initial payload source_spans (s1 selected, s2 unselected)
    cached = store.get_cached_pack("th1", "ch1", "sh1", "fp1")
    assert cached is not None
    unselected_after_hit = store.get_more_context(run_id, limit=5)
    assert len(unselected_after_hit) == 1
    assert unselected_after_hit[0]["path"] == "s2.tex"


def test_realistic_baseline_tokens_uses_actual_target_and_1hop_dependencies(
    tmp_path: Path,
):
    target_f = tmp_path / "target.tex"
    dep_f = tmp_path / "dep.tex"
    other_f = tmp_path / "other.tex"
    target_f.write_text("Target word " * 100, encoding="utf-8")
    dep_f.write_text("Dependency word " * 150, encoding="utf-8")
    other_f.write_text("Other word " * 500, encoding="utf-8")

    realistic, raw, is_capped = compute_realistic_baseline_tokens(
        project_root=str(tmp_path),
        target_path="target.tex",
        baseline_mode="section_neighborhood",
        model_family="generic",
        total_workspace_tokens=2000,
        pack_tokens=150,
        dependency_paths=["dep.tex"],
    )
    # Must equal actual target tokens + 1-hop dependency tokens (never inflated to 8,000)
    assert realistic == raw
    assert is_capped is False
    assert 400 <= realistic <= 900


def test_phase_4_retrieval_boundary_and_packaging_cleanup():
    src_pkg = Path(__file__).resolve().parents[1] / "src" / "writing_context_rtfm"
    # 1. Benchmark-only modules must not be shipped inside src/writing_context_rtfm
    for moved_module in ("benchmark.py", "candidate_exposure.py", "rtfm_sync_explicit.py"):
        assert not (src_pkg / moved_module).exists()

    # 2. Only rtfm_adapter.py and storage.py (for context_cache.sqlite) may import sqlite3 in src/
    sqlite_importers = []
    for py_file in src_pkg.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if "import sqlite3" in text or "from sqlite3" in text:
            sqlite_importers.append(py_file.relative_to(src_pkg).as_posix())
    assert sorted(sqlite_importers) == ["rtfm_adapter.py", "storage.py"]
