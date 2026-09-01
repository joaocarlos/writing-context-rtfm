from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from writing_context_rtfm.benchmark import (
    STRATEGIES,
    ArtifactKey,
    ArtifactStore,
    BenchmarkError,
    CaseManifest,
    CLIModelClient,
    ModelsConfig,
    ModelSpec,
    ProductionRetrievalBackend,
    RTFMExplicitFileIndexer,
    _confirm_paid_run,
    _run_cli_process_group,
    _within_budget,
    audit_case_annotations,
    bibliography_key_inventory,
    build_cli_parser,
    build_report,
    build_retrieval_diagnostic_report,
    deterministic_output_metrics,
    file_sha256,
    generation_request_count,
    heading_and_label_prefix,
    judgment_request_count,
    load_cases,
    load_models,
    parse_citation_keys,
    prepare_case,
    prompt_invariant_sections,
    reconcile_judgments,
    remap_source_spans_after_mask,
    render_generation_prompt,
    retrieval_metrics,
    run_generation,
    run_judging,
    run_retrieval,
    sha256_text,
    validate_judgment,
    validate_zip_members,
)
from writing_context_rtfm.schemas import RTFMResult
from writing_context_rtfm.virtual_doc import VirtualDocumentParser

FIXTURE_ROOT = Path(__file__).parents[1] / "benchmark" / "fixtures" / "synthetic_project"


class RecordingIndexer:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, list[str]]] = []

    def index_files(self, workspace: Path, files: list[str]) -> None:
        self.calls.append((workspace, list(files)))


def _make_case(tmp_path: Path) -> tuple[CaseManifest, Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    for fixture in FIXTURE_ROOT.iterdir():
        (source / fixture.name).write_bytes(fixture.read_bytes())
    parser = VirtualDocumentParser(str(source))
    parser.parse("main.tex")
    node = parser.find_section_node("section_method")
    assert node is not None
    manuscript = (source / node.source_path).read_text(encoding="utf-8")
    gold = manuscript[node.char_start : node.char_end]

    archive = tmp_path / "synthetic.local.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for file_path in sorted(source.iterdir()):
            bundle.write(file_path, file_path.name)
    raw = {
        "id": "P0-synthetic-method",
        "project_id": "P0",
        "stages": ["pilot", "confirmation"],
        "archive": str(archive),
        "archive_sha256": file_sha256(archive),
        "archive_root": "",
        "entry_point": "main.tex",
        "bibliography_files": ["references.bib"],
        "target": {
            "selector": "section_method",
            "heading": "Method",
            "content_hash": node.content_hash,
        },
        "task": "Write the masked method section from the supplied evidence.",
        "task_type": "write_new_section",
        "context_budget": 6000,
        "output_tokens": 1500,
        "expected_output_range": [20, 200],
        "gold_sha256": sha256_text(gold),
        "required_ideas": [
            {"id": "idea_sampling", "anchors": ["stratified sampling"]},
        ],
        "anchor_aliases": {"stratified sampling": ["balanced strata"]},
        "required_terminology": ["calibration set"],
        "prohibited_claims": ["state-of-the-art"],
        "protected_literals": ["64", "0.2"],
        "required_citation_keys": ["doe2024"],
        "valid_citation_keys": ["doe2024"],
        "expected_source_spans": [
            {
                "path": "evidence.tex",
                "line_start": 1,
                "line_end": 10,
                "grade": 3,
                "obligations": ["idea_sampling"],
            }
        ],
        "annotations": {
            "curator": "approved",
            "card_author": "approved",
            "auditor": "approved",
            "disagreements": [],
        },
    }
    manifest = tmp_path / "cases.local.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {"version": 1, "defaults": {"context_budget": 6000}, "cases": [raw]},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return load_cases(manifest)[0], manifest, gold


def test_case_manifest_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    manifest = tmp_path / "cases.local.yaml"
    manifest.write_text("version: 1\nversion: 1\ncases: []\n", encoding="utf-8")

    with pytest.raises(BenchmarkError, match="duplicate key 'version'"):
        load_cases(manifest)


def test_case_hash_excludes_annotation_status_but_includes_rubric(tmp_path: Path) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    review_changed = CaseManifest(
        **{
            **case.__dict__,
            "annotations": {
                **case.annotations,
                "auditor": "pending",
                "disagreements": ["pending review"],
            },
            "raw": {
                **case.raw,
                "annotations": {
                    **case.annotations,
                    "auditor": "pending",
                    "disagreements": ["pending review"],
                },
            },
        }
    )
    rubric_changed = CaseManifest(
        **{
            **case.__dict__,
            "required_terminology": (*case.required_terminology, "new term"),
            "raw": {
                **case.raw,
                "required_terminology": [*case.required_terminology, "new term"],
            },
        }
    )

    assert review_changed.case_hash == case.case_hash
    assert rubric_changed.case_hash != case.case_hash


@pytest.mark.parametrize("member", ["../escape.tex", "/absolute.tex", "C:\\drive.tex"])
def test_safe_zip_rejects_unsafe_paths(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(member, "private")
    with pytest.raises(BenchmarkError, match="Unsafe ZIP member"):
        validate_zip_members(archive)


def test_safe_zip_rejects_symlinks(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    info = zipfile.ZipInfo("link.tex")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(info, "target.tex")
    with pytest.raises(BenchmarkError, match="Unsafe ZIP member"):
        validate_zip_members(archive)


def test_heading_prefix_preserves_heading_and_leading_label() -> None:
    section = "\\section{Nested {Method}}\n\\label{sec:method}\nSecret body."
    assert heading_and_label_prefix(section) == ("\\section{Nested {Method}}\n\\label{sec:method}")


def test_prepare_masks_target_isolates_gold_and_indexes_explicit_files(tmp_path: Path) -> None:
    case, _manifest, gold = _make_case(tmp_path)
    indexer = RecordingIndexer()
    archive_before = file_sha256(case.archive)
    metadata = prepare_case(
        case,
        workspaces_root=tmp_path / "workspaces.local",
        private_root=tmp_path / "private.local",
        indexer=indexer,
    )
    workspace = Path(metadata["workspace"])
    masked = (workspace / "main.tex").read_text(encoding="utf-8")
    cards = (workspace / ".writing-context" / "section_cards.yaml").read_text(encoding="utf-8")
    assert "\\section{Method}\\label{sec:method}" in masked
    assert "% BENCHMARK_TARGET_MASKED:P0-synthetic-method" in masked
    assert gold not in masked
    assert gold == Path(metadata["gold_path"]).read_text(encoding="utf-8")
    assert Path(metadata["gold_path"]).parent != workspace
    assert gold not in cards
    assert metadata["cards_provenance"] == "masked_workspace_and_task_only"
    assert metadata["cards_frozen"] is True
    assert file_sha256(case.archive) == archive_before
    assert len(indexer.calls) == 1
    assert set(indexer.calls[0][1]) == {"main.tex", "evidence.tex", "references.bib"}
    assert not any("TRASH" in path for path in metadata["allowed_files"])


def test_annotation_audit_detects_review_manifest_disagreement(tmp_path: Path) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    private_root = tmp_path / "private.local"
    prepare_case(
        case,
        workspaces_root=tmp_path / "workspaces.local",
        private_root=private_root,
        indexer=RecordingIndexer(),
    )
    reviews = private_root / "annotation-reviews"
    reviews.mkdir()
    (reviews / "auditor-P0.json").write_text(
        json.dumps(
            {
                "role": "auditor",
                "project_id": "P0",
                "cases": [
                    {
                        "case_id": case.id,
                        "decision": "needs_revision",
                        "issues": ["anchor_under_specific:idea_sampling"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = audit_case_annotations([case], private_root=private_root)
    result = report["cases"][case.id]

    assert result["auditor_issue_codes"] == ["anchor_under_specific"]
    assert result["review_manifest_consistent"] is False
    assert result["annotation_resolved"] is False
    assert report["unresolved_annotation_case_ids"] == [case.id]


def test_annotation_audit_keeps_corpus_warning_separate_from_resolution(
    tmp_path: Path,
) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    annotations = {
        **case.annotations,
        "corpus_warnings": ["manuscript_citation_missing_from_bibliography"],
    }
    case = CaseManifest(
        **{
            **case.__dict__,
            "annotations": annotations,
            "raw": {**case.raw, "annotations": annotations},
        }
    )
    private_root = tmp_path / "private.local"
    prepare_case(
        case,
        workspaces_root=tmp_path / "workspaces.local",
        private_root=private_root,
        indexer=RecordingIndexer(),
    )
    reviews = private_root / "annotation-reviews"
    reviews.mkdir()
    (reviews / "auditor-P0.json").write_text(
        json.dumps(
            {
                "role": "auditor",
                "project_id": "P0",
                "cases": [{"case_id": case.id, "decision": "approved", "issues": []}],
            }
        ),
        encoding="utf-8",
    )

    report = audit_case_annotations([case], private_root=private_root)
    result = report["cases"][case.id]

    assert result["corpus_warnings"] == ["manuscript_citation_missing_from_bibliography"]
    assert result["review_manifest_consistent"] is True
    assert result["annotation_resolved"] is True


def test_rtfm_indexer_uses_synchronous_explicit_helper(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.tex").write_text("\\section{Test}\nEvidence.", encoding="utf-8")
    observed: list[list[str]] = []

    def fake_run(command, **kwargs):
        observed.append(command)
        return subprocess.CompletedProcess(command, 0, '{"books": 1, "chunks": 1}', "")

    monkeypatch.setattr(
        "writing_context_rtfm.benchmark.shutil.which",
        lambda _name: pytest.fail("explicit indexing must not inspect or invoke the RTFM CLI"),
    )
    monkeypatch.setattr("writing_context_rtfm.benchmark.subprocess.run", fake_run)

    RTFMExplicitFileIndexer().index_files(workspace, ["main.tex"])

    assert len(observed) == 1
    assert observed[0][0] == sys.executable
    assert "rtfm_sync_explicit.py" in observed[0][1]
    assert observed[0][-2:] == ["--files", "main.tex"]


def test_prepare_rejects_target_hash_mismatch_before_writing_gold(tmp_path: Path) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    bad = CaseManifest(**{**case.__dict__, "target_content_hash": "wrong"})
    with pytest.raises(BenchmarkError, match="Target content hash mismatch"):
        prepare_case(
            bad,
            workspaces_root=tmp_path / "workspaces.local",
            private_root=tmp_path / "private.local",
        )
    assert not (tmp_path / "private.local" / "gold").exists()


def test_budget_enforcement_skips_overflow_without_truncating_spans() -> None:
    spans = [
        {"id": "a", "tokens": 4},
        {"id": "b", "tokens": 8},
        {"id": "c", "tokens": 5},
    ]
    assert [span["id"] for span in _within_budget(spans, 10)] == ["a", "c"]


def test_rtfm_topk_preserves_raw_candidates_before_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    oversized = "token " * (case.context_budget * 2)
    results = [
        RTFMResult("main.tex", 1, 2, oversized, 1.0, {"rank": 1}),
        RTFMResult("evidence.tex", 1, 3, "stratified sampling", 0.9, {"rank": 2}),
    ]
    monkeypatch.setattr(
        "writing_context_rtfm.benchmark.RTFMAdapter.search",
        lambda *_args, **_kwargs: results,
    )

    evidence = ProductionRetrievalBackend()._topk(case, {"workspace": str(tmp_path)})

    assert len(evidence["candidate_spans"]) == 2
    assert [span["path"] for span in evidence["spans"]] == ["evidence.tex"]
    assert evidence["diagnostic_trace"]["retrieved"] == evidence["candidate_spans"]
    assert evidence["diagnostic_trace"]["selected"] == evidence["spans"]


def test_prompt_invariants_are_equal_across_conditions(tmp_path: Path) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    span = {
        "id": "S001-test",
        "path": "evidence.tex",
        "line_start": 1,
        "line_end": 2,
        "text": "Evidence",
    }
    base = render_generation_prompt(case, {"spans": [span], "pack_metadata": {}})
    pack = render_generation_prompt(
        case,
        {"spans": [span], "pack_metadata": {"constraints": ["preserve 64"]}},
    )
    assert prompt_invariant_sections(base) == prompt_invariant_sections(pack)
    assert "PACK GUIDANCE" not in base
    assert "PACK GUIDANCE" in pack


def test_artifact_resume_requires_exact_key(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.local")
    values = {
        "kind": "retrieval",
        "case_hash": "case",
        "strategy": "full_visible",
        "repetition": 0,
        "model": "retrieval-only",
        "code_revision": "revision",
        "prompt_version": "v1",
        "rtfm_fingerprint": "rtfm",
        "cards_hash": "cards",
        "retrieval_policy_version": "policy",
    }
    first = ArtifactKey(**values)
    changed = ArtifactKey(**{**values, "prompt_version": "v2"})
    saved = store.save(first, {"value": 1})
    assert store.load(first) == saved
    assert store.load(changed) is None


def test_citation_parser_and_deterministic_metrics(tmp_path: Path) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    output = (
        "Balanced strata are used for the calibration set with 64 observations and 0.2 "
        "learning rate~\\citep[see][]{doe2024}."
    )
    assert parse_citation_keys(output) == {"doe2024"}
    metrics = deterministic_output_metrics(case, output)
    assert metrics["idea_coverage"] == 1.0
    assert metrics["terminology_coverage"] == 1.0
    assert metrics["citation_validity"] == 1.0
    assert metrics["protected_literal_preservation"] == 1.0
    assert metrics["structural_validity"] == 1.0


def test_parse_tex_citation_keys_ignores_comments_and_at_tokens() -> None:
    source = r"""Text \cite{active2026}.
% Commented \cite{sampleKey}.
\makeatletter\@firstpage\makeatother
author@example.org
"""

    assert parse_citation_keys(source, source_format="tex") == {"active2026"}


def test_parse_markdown_citation_keys_supports_pandoc() -> None:
    assert parse_citation_keys("Evidence [@doe2024].", source_format="md") == {"doe2024"}


def test_retrieval_metrics_exclude_unresolved_annotations(tmp_path: Path) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    evidence = {
        "spans": [
            {
                "id": "S001",
                "path": "evidence.tex",
                "line_start": 1,
                "line_end": 3,
                "text": "stratified sampling",
                "tokens": 3,
            }
        ],
        "context_tokens": 3,
        "retrieval_latency_ms": 1.0,
    }
    assert retrieval_metrics(case, evidence)["graded_source_recall"] == 1.0
    unresolved = CaseManifest(
        **{
            **case.__dict__,
            "annotations": {**case.annotations, "disagreements": ["source relevance"]},
        }
    )
    assert retrieval_metrics(unresolved, evidence)["annotation_resolved"] is False


def test_retrieval_metrics_count_each_expected_source_once(tmp_path: Path) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    evidence = {
        "spans": [
            {
                "id": "S001",
                "path": "evidence.tex",
                "line_start": 1,
                "line_end": 10,
                "text": "stratified sampling for a calibration set",
                "tokens": 6,
            },
            {
                "id": "S002",
                "path": "evidence.tex",
                "line_start": 1,
                "line_end": 10,
                "text": "the same stratified sampling evidence",
                "tokens": 5,
            },
        ],
        "context_tokens": 11,
        "retrieval_latency_ms": 1.0,
    }

    metrics = retrieval_metrics(case, evidence)

    assert metrics["graded_source_recall"] == 1.0
    assert metrics["ndcg"] == 1.0
    assert 0.0 <= metrics["ndcg"] <= 1.0


def test_retrieval_metrics_report_raw_candidate_recall_and_first_rank(tmp_path: Path) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    distractor = {
        "path": "main.tex",
        "line_start": 1,
        "line_end": 2,
    }
    relevant = {
        "path": "evidence.tex",
        "line_start": 1,
        "line_end": 3,
    }
    evidence = {
        "spans": [],
        "candidate_spans": [distractor, distractor, relevant],
        "diagnostic_trace": {
            "retrieved": [distractor, distractor, relevant],
            "selected": [],
        },
        "selection_rejections": {"token_budget": [relevant]},
        "context_tokens": 0,
        "retrieval_latency_ms": 1.0,
    }

    metrics = retrieval_metrics(case, evidence)

    assert metrics["candidate_recall_at_1"] == 0.0
    assert metrics["candidate_recall_at_3"] == 1.0
    assert metrics["candidate_recall_at_5"] == 1.0
    assert metrics["expected_source_first_ranks"] == [3]
    assert metrics["candidate_to_selected_recall_delta"] == -1.0
    assert metrics["expected_source_outcomes"] == [
        {
            "source_index": 0,
            "first_candidate_rank": 3,
            "selected": False,
            "lost_after": "retrieved",
            "selection_loss_reason": "token_budget",
        }
    ]
    assert metrics["selection_loss_reason_counts"] == {"token_budget": 1}


def test_retrieval_metrics_attribute_first_pack_loss_stage(tmp_path: Path) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    relevant = {
        "path": "evidence.tex",
        "line_start": 1,
        "line_end": 3,
    }
    evidence = {
        "spans": [],
        "candidate_spans": [relevant],
        "diagnostic_trace": {
            "retrieved": [relevant],
            "deduplicated": [relevant],
            "score_filtered": [],
            "diversified": [],
            "budget_candidates": [],
            "selected": [],
        },
        "context_tokens": 0,
        "retrieval_latency_ms": 1.0,
    }

    metrics = retrieval_metrics(case, evidence)

    assert metrics["loss_stage_counts"] == {"score_filtered": 1}
    assert metrics["expected_source_outcomes"][0]["lost_after"] == "deduplicated"
    assert metrics["expected_source_outcomes"][0]["selection_loss_reason"] is None


def test_retrieval_metrics_ignore_rejected_duplicate_when_source_is_selected(
    tmp_path: Path,
) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    relevant = {"path": "evidence.tex", "line_start": 1, "line_end": 3}
    evidence = {
        "spans": [relevant],
        "candidate_spans": [relevant],
        "diagnostic_trace": {"retrieved": [relevant], "selected": [relevant]},
        "selection_rejections": {"token_budget": [relevant]},
        "context_tokens": 3,
        "retrieval_latency_ms": 1.0,
    }

    metrics = retrieval_metrics(case, evidence)

    assert metrics["expected_source_outcomes"][0]["selected"] is True
    assert metrics["expected_source_outcomes"][0]["selection_loss_reason"] is None
    assert metrics["selection_loss_reason_counts"] == {}


def test_judgment_schema_and_disagreement_rules() -> None:
    base = {
        "candidate_id": "C-1",
        "ratings": {
            "evidence_support": 4,
            "completeness": 4,
            "constraint_adherence": 4,
            "citation_correctness": 4,
            "writing_fitness": 4,
        },
        "pass": True,
        "evidence_span_ids": ["S001"],
    }
    assert validate_judgment(base, candidate_id="C-1", valid_span_ids={"S001"}) == base
    with pytest.raises(BenchmarkError, match="invalid evidence"):
        validate_judgment(base, candidate_id="C-1", valid_span_ids={"S999"})
    close = {**base, "ratings": {**base["ratings"], "completeness": 3}}
    assert reconcile_judgments(base, close)["resolved"] is True
    far = {**base, "ratings": {**base["ratings"], "completeness": 2}}
    far_result = reconcile_judgments(base, far)
    assert far_result["resolved"] is False
    assert "completeness" not in far_result["criterion_averages"]
    assert far_result["criterion_averages"] == {
        "evidence_support": 4.0,
        "constraint_adherence": 4.0,
        "citation_correctness": 4.0,
        "writing_fitness": 4.0,
    }
    assert far_result["pass"] is True
    conflict = {**base, "pass": False}
    conflict_result = reconcile_judgments(base, conflict)
    assert conflict_result["resolved"] is False
    assert conflict_result["criterion_averages"] == dict.fromkeys(base["ratings"], 4.0)
    assert conflict_result["pass"] is None


def test_paid_confirmation_guard(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(BenchmarkError, match="not confirmed"):
        _confirm_paid_run(False, 96, "generation")
    assert "Exact planned generation request count: 96" in capsys.readouterr().out


def test_paid_request_counts_respect_case_limit() -> None:
    case = SimpleNamespace(stages=("pilot", "confirmation"))
    cases = [case, case]

    assert (
        generation_request_count(cases, _models(), stage="pilot", repetitions=1, limit_cases=1) == 4
    )
    assert (
        judgment_request_count(cases, _models(), stage="pilot", repetitions=1, limit_cases=1) == 8
    )


@pytest.mark.parametrize("command", ("generate", "judge", "report", "retrieval-report"))
def test_paid_and_report_commands_accept_case_limit(command: str) -> None:
    args = [command, "--stage", "pilot", "--limit-cases", "1"]
    if command in {"report", "retrieval-report"}:
        args.append("--anonymized")

    parsed = build_cli_parser().parse_args(args)

    assert parsed.limit_cases == 1


@pytest.mark.parametrize("command", ("retrieve", "generate", "judge", "report", "retrieval-report"))
def test_case_limit_must_be_positive(command: str) -> None:
    args = [command, "--stage", "pilot", "--limit-cases", "-1"]
    if command in {"report", "retrieval-report"}:
        args.append("--anonymized")

    with pytest.raises(SystemExit):
        build_cli_parser().parse_args(args)


def test_bibliography_inventory_reports_complete_and_duplicate_keys(tmp_path: Path) -> None:
    bibliography = tmp_path / "references.bib"
    bibliography.write_text(
        """@article{alpha,
  title={Alpha}
}
@book{beta,
  title={Beta}
}
@misc{alpha,
  title={Duplicate}
}
""",
        encoding="utf-8",
    )
    inventory = bibliography_key_inventory([bibliography])
    assert inventory["keys"] == ["alpha", "beta"]
    assert inventory["duplicate_keys"] == ["alpha"]
    assert inventory["unparsed_keys"] == []


def test_source_spans_are_remapped_around_masked_target() -> None:
    spans = (
        {"path": "main.tex", "line_start": 2, "line_end": 9},
        {"path": "main.tex", "line_start": 15, "line_end": 20},
        {"path": "main.tex", "line_start": 8, "line_end": 16},
        {"path": "references.bib", "line_start": 1, "line_end": 5},
    )
    corrected, issues = remap_source_spans_after_mask(
        spans,
        target_path="main.tex",
        target_line_start=10,
        gold_text="heading\nbody\nbody\nbody\nbody\n",
        masked_target_line_end=12,
    )
    assert corrected[0]["line_start"] == 2
    assert corrected[1]["line_start"] == 12
    assert corrected[1]["line_end"] == 17
    assert corrected[3]["line_start"] == 1
    assert issues == ["source_span_2_overlaps_masked_target"]


def test_cli_model_config_uses_transport_independent_families(tmp_path: Path) -> None:
    config = tmp_path / "models.local.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "temperature": 0.2,
                "repetitions": 3,
                "generators": {
                    "pilot": [
                        {
                            "provider": "agy_cli",
                            "model": "gemini-test",
                            "command": "agy",
                            "cli_version": "1.2.3",
                        }
                    ],
                    "confirmation": [
                        {
                            "provider": "agy_cli",
                            "model": "gemini-test",
                            "command": "agy",
                            "cli_version": "1.2.3",
                        },
                        {
                            "provider": "codex_cli",
                            "model": "codex-test",
                            "command": "codex",
                            "cli_version": "4.5.6",
                        },
                    ],
                },
                "judges": [
                    {
                        "provider": "agy_cli",
                        "model": "gemini-judge",
                        "cli_version": "1.2.3",
                    },
                    {
                        "provider": "codex_cli",
                        "model": "codex-judge",
                        "cli_version": "4.5.6",
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    models = load_models(config)
    assert models.generators["pilot"][0].family == "gemini"
    assert models.generators["confirmation"][1].family == "openai"
    assert models.generators["confirmation"][1].id == "codex_cli:codex-test@4.5.6"


def test_cli_model_config_does_not_require_cli_versions(tmp_path: Path) -> None:
    config = tmp_path / "models.local.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "temperature": 0.2,
                "repetitions": 3,
                "generators": {
                    "pilot": [
                        {
                            "provider": "agy_cli",
                            "model": "gemini-test",
                            "command": "agy",
                        }
                    ],
                    "confirmation": [
                        {
                            "provider": "agy_cli",
                            "model": "gemini-test",
                            "command": "agy",
                        },
                        {
                            "provider": "codex_cli",
                            "model": "codex-test",
                            "command": "codex",
                        },
                    ],
                },
                "judges": [
                    {
                        "provider": "agy_cli",
                        "model": "gemini-judge",
                        "command": "agy",
                    },
                    {
                        "provider": "codex_cli",
                        "model": "codex-judge",
                        "command": "codex",
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    models = load_models(config)

    assert models.generators["pilot"][0].id == "agy_cli:gemini-test"
    assert models.generators["confirmation"][1].id == "codex_cli:codex-test"
    assert models.judges[1].id == "codex_cli:codex-judge"


def test_cli_process_group_is_isolated_and_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}
    signals: list[tuple[int, int]] = []

    class FakeProcess:
        pid = 424242
        returncode = 0

        def communicate(self, *, input: str | None, timeout: int) -> tuple[str, str]:
            observed["input"] = input
            observed["timeout"] = timeout
            return "model output", ""

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        observed["command"] = command
        observed["popen"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("writing_context_rtfm.benchmark.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "writing_context_rtfm.benchmark.os.killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )

    result = _run_cli_process_group(
        ["agy", "--model", "test"],
        prompt="private prompt",
        cwd=str(tmp_path),
        timeout=30,
        environment={"PATH": "/safe/bin"},
    )

    assert result.stdout == "model output"
    assert observed["input"] == "private prompt"
    assert observed["popen"]["start_new_session"] is True  # type: ignore[index]
    assert observed["popen"]["env"] == {"PATH": "/safe/bin"}  # type: ignore[index]
    assert signals == [(424242, signal.SIGTERM), (424242, signal.SIGKILL)]


@pytest.mark.parametrize(
    ("provider", "expected_arguments"),
    [
        (
            "agy_cli",
            {
                "--input-format",
                "stream-json",
                "--mode",
                "plan",
                "--sandbox",
                "--disable-slash-commands",
                "--output-format",
            },
        ),
        ("gemini_cli", {"--approval-mode", "plan", "--output-format", "text"}),
        ("claude_cli", {"--no-session-persistence", "--tools", ""}),
        (
            "codex_cli",
            {
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--ignore-user-config",
                "--ignore-rules",
            },
        ),
    ],
)
def test_cli_model_clients_are_isolated_and_pinned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    expected_arguments: set[str],
) -> None:
    executable = tmp_path / provider
    executable.touch(mode=0o700)
    unsafe_bin = tmp_path / "unsafe-bin"
    unsafe_bin.mkdir()
    (unsafe_bin / "rtfm").touch(mode=0o700)
    safe_bin = tmp_path / "safe-bin"
    safe_bin.mkdir()
    monkeypatch.setenv("PATH", f"{unsafe_bin}{os.pathsep}{safe_bin}")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "venv"))
    calls: list[tuple[list[str], Path, str | None, dict[str, str] | None]] = []

    def fake_run(
        command: list[str],
        *,
        prompt: str | None,
        cwd: str,
        timeout: int,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        cwd_path = Path(cwd)
        calls.append((command, cwd_path, prompt, environment))
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, stdout="tool version 9.8.7\n", stderr="")
        if command[-1] == "models":
            return subprocess.CompletedProcess(
                command, 0, stdout="exact-model-id\tExact Model\n", stderr=""
            )
        if provider == "agy_cli":
            stdout = "\n".join(
                (
                    json.dumps({"event": "init", "init": {"model": "exact-model-id"}}),
                    json.dumps(
                        {
                            "event": "result",
                            "result": {"status": "SUCCESS", "response": "agy_cli response"},
                        }
                    ),
                )
            )
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        if provider == "codex_cli":
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("codex response", encoding="utf-8")
            stdout = ""
        else:
            stdout = f"{provider} response"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(
        "writing_context_rtfm.benchmark.shutil.which", lambda _name: str(executable)
    )
    monkeypatch.setattr("writing_context_rtfm.benchmark._run_cli_process_group", fake_run)
    spec = ModelSpec(
        provider=provider,
        model="exact-model-id",
        command=provider,
        cli_version="9.8.7",
    )
    client = CLIModelClient(spec)
    client.check_available()
    output = client.generate("private prompt", temperature=0.2, max_tokens=1500)

    assert output == f"{provider} response".replace("codex_cli", "codex")
    generation_command, isolated_cwd, effective_prompt, environment = calls[-1]
    assert isolated_cwd != Path.cwd()
    assert "exact-model-id" in generation_command
    assert expected_arguments.issubset(set(generation_command))
    assert "private prompt" not in generation_command
    assert effective_prompt is not None and "private prompt" in effective_prompt
    assert "maximum output tokens=1500" in effective_prompt
    assert environment is not None
    assert str(unsafe_bin) not in environment["PATH"].split(os.pathsep)
    assert str(safe_bin) in environment["PATH"].split(os.pathsep)
    assert "VIRTUAL_ENV" not in environment
    if provider == "agy_cli":
        message = json.loads(effective_prompt)
        assert message["event"] == "user"
        assert "private prompt" in message["message"]["content"]


def test_cli_model_version_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "gemini"
    executable.touch(mode=0o700)
    monkeypatch.setattr(
        "writing_context_rtfm.benchmark.shutil.which", lambda _name: str(executable)
    )
    monkeypatch.setattr(
        "writing_context_rtfm.benchmark._run_cli_process_group",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="gemini 2.0.0\n", stderr=""
        ),
    )
    client = CLIModelClient(
        ModelSpec(
            provider="gemini_cli",
            model="gemini-test",
            command="gemini",
            cli_version="1.0.0",
        )
    )
    with pytest.raises(BenchmarkError, match="CLI version mismatch"):
        client.check_available()


def test_codex_cli_without_version_accepts_installed_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "codex"
    executable.touch(mode=0o700)
    monkeypatch.setattr(
        "writing_context_rtfm.benchmark.shutil.which", lambda _name: str(executable)
    )
    monkeypatch.setattr(
        "writing_context_rtfm.benchmark._run_cli_process_group",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="codex-cli 99.0.0\n", stderr=""
        ),
    )
    client = CLIModelClient(ModelSpec(provider="codex_cli", model="codex-test", command="codex"))

    client.check_available()


def test_antigravity_model_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "agy"
    executable.touch(mode=0o700)
    stream = "\n".join(
        (
            json.dumps({"event": "init", "init": {"model": "substituted-model"}}),
            json.dumps(
                {
                    "event": "result",
                    "result": {"status": "SUCCESS", "response": "model response"},
                }
            ),
        )
    )
    monkeypatch.setattr(
        "writing_context_rtfm.benchmark.shutil.which", lambda _name: str(executable)
    )
    monkeypatch.setattr(
        "writing_context_rtfm.benchmark._run_cli_process_group",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout=stream, stderr=""),
    )
    client = CLIModelClient(
        ModelSpec(
            provider="agy_cli",
            model="requested-model",
            command="agy",
            cli_version="1.0.0",
        )
    )

    with pytest.raises(BenchmarkError, match="model mismatch"):
        client.generate("private prompt", temperature=0.2, max_tokens=100)


class FakeRetrievalBackend:
    def retrieve(
        self, case: CaseManifest, prepared: dict[str, object], strategy: str
    ) -> dict[str, object]:
        assert strategy in STRATEGIES
        text = (
            "The evidence specifies stratified sampling for the calibration set with exactly "
            "64 observations, a learning rate of 0.2, and citation \\cite{doe2024}."
        )
        span = {
            "id": f"S001-{strategy[:4]}",
            "path": "evidence.tex",
            "line_start": 1,
            "line_end": 3,
            "text": text,
            "text_sha256": sha256_text(text),
            "score": 1.0,
            "tokens": 35,
        }
        metadata = (
            {"constraints": ["preserve exact literals"], "terminology": {}}
            if strategy.startswith("pack_")
            else {}
        )
        return {
            "strategy": strategy,
            "spans": [span],
            "candidate_spans": [span],
            "diagnostic_trace": {"retrieved": [span], "selected": [span]},
            "pack_metadata": metadata,
            "budget": None if strategy == "full_visible" else case.context_budget,
            "context_tokens": 35,
            "retrieval_latency_ms": 1.0,
        }


class FakeModelClient:
    def __init__(self, spec: ModelSpec):
        self.spec = spec

    def check_available(self) -> None:
        return None

    def generate(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        if prompt.startswith("Evaluate the blinded candidate"):
            candidate = prompt.split("CANDIDATE ID\n", 1)[1].split("\n", 1)[0]
            evidence_packet = prompt.split("EVIDENCE PACKET\n", 1)[1].split(
                "\n\nCANDIDATE OUTPUT", 1
            )[0]
            span_id = json.loads(evidence_packet)[0]["id"]
            score = 4 if self.spec.provider == "gemini" else 3
            return json.dumps(
                {
                    "candidate_id": candidate,
                    "ratings": {
                        "evidence_support": score,
                        "completeness": score,
                        "constraint_adherence": score,
                        "citation_correctness": score,
                        "writing_fitness": score,
                    },
                    "pass": True,
                    "evidence_span_ids": [span_id],
                }
            )
        return (
            "The method uses stratified sampling for the calibration set of 64 observations. "
            "It uses a learning rate of 0.2 following \\cite{doe2024}."
        )


class PartialDisagreementFakeModelClient(FakeModelClient):
    def generate(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        if not prompt.startswith("Evaluate the blinded candidate"):
            return super().generate(prompt, temperature=temperature, max_tokens=max_tokens)
        candidate = prompt.split("CANDIDATE ID\n", 1)[1].split("\n", 1)[0]
        evidence_packet = prompt.split("EVIDENCE PACKET\n", 1)[1].split("\n\nCANDIDATE OUTPUT", 1)[
            0
        ]
        span_id = json.loads(evidence_packet)[0]["id"]
        ratings = dict.fromkeys(
            (
                "completeness",
                "constraint_adherence",
                "citation_correctness",
                "writing_fitness",
            ),
            4 if self.spec.provider == "gemini" else 3,
        )
        ratings["evidence_support"] = 4 if self.spec.provider == "gemini" else 2
        return json.dumps(
            {
                "candidate_id": candidate,
                "ratings": ratings,
                "pass": True,
                "evidence_span_ids": [span_id],
            }
        )


def _models() -> ModelsConfig:
    gemini_generator = ModelSpec("gemini", "gemini-test-generator", "UNUSED")
    return ModelsConfig(
        temperature=0.2,
        repetitions=3,
        generators={
            "pilot": (gemini_generator,),
            "confirmation": (
                gemini_generator,
                ModelSpec("openai", "openai-test-generator", "UNUSED"),
            ),
        },
        judges=(
            ModelSpec("gemini", "gemini-test-judge", "UNUSED"),
            ModelSpec("openai", "openai-test-judge", "UNUSED"),
        ),
    )


def test_offline_end_to_end_all_strategies_and_anonymized_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, _manifest, gold = _make_case(tmp_path)
    private_root = tmp_path / "private.local"
    prepare_case(
        case,
        workspaces_root=tmp_path / "workspaces.local",
        private_root=private_root,
        indexer=RecordingIndexer(),
    )
    monkeypatch.setattr("writing_context_rtfm.benchmark.shutil.which", lambda _name: None)
    artifacts = ArtifactStore(tmp_path / "artifacts.local")
    retrievals = run_retrieval(
        [case],
        stage="pilot",
        private_root=private_root,
        artifacts=artifacts,
        backend=FakeRetrievalBackend(),
    )
    assert {item["key"]["strategy"] for item in retrievals} == set(STRATEGIES)
    retrieval_report = build_retrieval_diagnostic_report(
        [case],
        stage="pilot",
        private_root=private_root,
        artifacts=artifacts,
    )
    assert retrieval_report["report_type"] == "candidate_diagnostics"
    assert retrieval_report["strategies"]["pack_baseline"]["candidate_recall_at_5"]["median"] == 1.0
    assert retrieval_report["candidate_diagnostics"]["pack_baseline"]["selected"] == 1
    generations = run_generation(
        [case, case],
        _models(),
        stage="pilot",
        private_root=private_root,
        artifacts=artifacts,
        client_factory=FakeModelClient,
        repetitions=1,
        limit_cases=1,
    )
    assert len(generations) == 4
    judgments = run_judging(
        [case, case],
        _models(),
        stage="pilot",
        private_root=private_root,
        artifacts=artifacts,
        client_factory=FakeModelClient,
        repetitions=1,
        limit_cases=1,
    )
    assert len(judgments) == 8
    report = build_report(
        [case, case],
        _models(),
        stage="pilot",
        private_root=private_root,
        artifacts=artifacts,
        repetitions=1,
        limit_cases=1,
    )
    assert report["unresolved_judge_rate"] == 0.0
    assert report["report_version"] == 2
    assert set(report["strategies"]) == set(STRATEGIES)
    assert report["corpus_hashes"] == [case.archive_sha256]
    assert "materially_worse_cases" in report["rrf_promotion_decision"]
    assert "materially_worse_confirmation_cases" not in report["rrf_promotion_decision"]
    assert report["strategies"]["rtfm_topk"]["retrieval"]["candidate_recall_at_5"]["median"] == 1.0
    assert report["candidate_diagnostics"]["pack_baseline"] == {
        "cases_with_diagnostics": 1,
        "expected_sources_evaluated": 1,
        "never_retrieved": 0,
        "retrieved_not_selected": 0,
        "selected": 1,
        "first_candidate_rank": {"median": 1, "q1": 1, "q3": 1},
        "loss_stage_counts": {},
        "selection_loss_reason_counts": {},
    }

    private_serialized = "\n".join(
        json.dumps(value, sort_keys=True)
        for kind in ("retrieval", "generation", "judgment")
        for value in artifacts.iter_kind(kind)
    )
    workspace = Path(
        json.loads((private_root / "prepared" / f"{case.case_hash}.json").read_text())["workspace"]
    )
    cards = (workspace / ".writing-context" / "section_cards.yaml").read_text(encoding="utf-8")
    visible = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in workspace.rglob("*")
        if path.is_file() and ".writing-context" not in path.parts
    )
    assert gold not in private_serialized
    assert gold not in cards
    assert gold not in visible

    public_serialized = json.dumps(report, sort_keys=True)
    assert case.task not in public_serialized
    assert gold not in public_serialized
    assert "evidence.tex" not in public_serialized
    assert "stratified sampling" not in public_serialized

    source_revision = generations[0]["key"]["code_revision"]
    retrieval_path = artifacts.path_for(ArtifactKey(**retrievals[0]["key"]))
    stored_retrieval = json.loads(retrieval_path.read_text(encoding="utf-8"))
    stored_retrieval["payload"]["metrics"]["ndcg"] = 9.0
    retrieval_path.write_text(json.dumps(stored_retrieval), encoding="utf-8")
    monkeypatch.setattr(
        "writing_context_rtfm.benchmark.current_code_revision",
        lambda: "analysis-revision",
    )

    reanalyzed = build_report(
        [case],
        _models(),
        stage="pilot",
        private_root=private_root,
        artifacts=artifacts,
        repetitions=1,
        source_code_revision=source_revision,
    )

    assert reanalyzed["source_code_revision"] == source_revision
    assert reanalyzed["analysis_code_revision"] == "analysis-revision"
    assert reanalyzed["strategies"]["full_visible"]["retrieval"]["ndcg"]["median"] == 1.0


def test_report_keeps_resolved_criteria_from_unresolved_judge_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, _manifest, _gold = _make_case(tmp_path)
    private_root = tmp_path / "private.local"
    prepare_case(
        case,
        workspaces_root=tmp_path / "workspaces.local",
        private_root=private_root,
        indexer=RecordingIndexer(),
    )
    monkeypatch.setattr("writing_context_rtfm.benchmark.shutil.which", lambda _name: None)
    artifacts = ArtifactStore(tmp_path / "artifacts.local")
    run_retrieval(
        [case],
        stage="pilot",
        private_root=private_root,
        artifacts=artifacts,
        backend=FakeRetrievalBackend(),
    )
    run_generation(
        [case],
        _models(),
        stage="pilot",
        private_root=private_root,
        artifacts=artifacts,
        client_factory=PartialDisagreementFakeModelClient,
        repetitions=1,
    )
    run_judging(
        [case],
        _models(),
        stage="pilot",
        private_root=private_root,
        artifacts=artifacts,
        client_factory=PartialDisagreementFakeModelClient,
        repetitions=1,
    )

    report = build_report(
        [case],
        _models(),
        stage="pilot",
        private_root=private_root,
        artifacts=artifacts,
        repetitions=1,
    )

    assert report["unresolved_judge_rate"] == 1.0
    for strategy in STRATEGIES:
        assert "evidence_support" not in report["strategies"][strategy]["judges"]
        assert report["strategies"][strategy]["judges"]["completeness"]["median"] == 3.5
