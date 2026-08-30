#!/usr/bin/env python3
"""Run independent, private card-author and annotation-auditor CLI reviews."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from writing_context_rtfm.benchmark import (
    BenchmarkError,
    CaseManifest,
    build_model_client,
    canonical_json,
    extract_json_object,
    load_cases,
    load_models,
    load_prepared,
)


def _atomic_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def _case_groups(cases: list[CaseManifest]) -> dict[str, list[CaseManifest]]:
    groups: dict[str, list[CaseManifest]] = defaultdict(list)
    for case in cases:
        groups[case.project_id].append(case)
    return dict(sorted(groups.items()))


def _card_author_packet(case: CaseManifest, private_root: Path) -> dict[str, Any]:
    prepared = load_prepared(case, private_root)
    workspace = Path(prepared["workspace"])
    cards_path = workspace / ".writing-context" / "section_cards.yaml"
    cards = yaml.safe_load(cards_path.read_text(encoding="utf-8"))
    target_card = cards["sections"][case.target_selector]
    return {
        "case_id": case.id,
        "task": case.task,
        "target": {
            "selector": case.target_selector,
            "heading": case.target_heading,
            "masked_line_start": prepared["target_line_start"],
            "masked_line_end": prepared["target_line_end"],
        },
        "card": {
            "document": cards["document"],
            "target": target_card,
            "section_outline": [
                {"selector": selector, "title": value.get("title"), "path": value.get("path")}
                for selector, value in cards["sections"].items()
            ],
        },
        "isolation": {
            "allowed_files": prepared["allowed_files"],
            "cards_provenance": prepared["cards_provenance"],
            "cards_frozen": prepared["cards_frozen"],
            "gold_in_cards": False,
            "gold_visible_files": [],
            "long_overlap_count": len(prepared["leakage_audit"]["matches"]),
        },
    }


def _source_span_packet(case: CaseManifest, private_root: Path, span: dict[str, Any]) -> dict[str, Any]:
    prepared = load_prepared(case, private_root)
    source = Path(prepared["workspace"]) / str(span["path"])
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    start = int(span["line_start"])
    end = int(span["line_end"])
    text = "\n".join(lines[start - 1 : end])
    return {
        **span,
        "text": text[:16000],
        "text_truncated": len(text) > 16000,
    }


def _auditor_packet(
    case: CaseManifest,
    private_root: Path,
    mechanical: dict[str, Any],
) -> dict[str, Any]:
    prepared = load_prepared(case, private_root)
    gold = Path(prepared["gold_path"]).read_text(encoding="utf-8")
    corrected = mechanical["cases"][case.id]["corrected_expected_source_spans"]
    return {
        "case_id": case.id,
        "task": case.task,
        "target_heading": case.target_heading,
        "rubric": case.rubric_for_judge(),
        "gold": gold,
        "graded_sources": [
            _source_span_packet(case, private_root, span) for span in corrected
        ],
        "mechanical_audit": {
            key: mechanical["cases"][case.id][key]
            for key in (
                "issues",
                "missing_manuscript_citation_keys",
                "missing_required_citation_keys",
                "generated_keys_absent_from_bibliography",
                "privacy",
                "rubric",
            )
        },
    }


def _render_prompt(role: str, project_id: str, packets: list[dict[str, Any]]) -> str:
    if role == "card_author":
        instruction = (
            "Act as the independent section-card author. You may use only the masked-project "
            "metadata and tasks below; no gold prose or gold-derived rubric is present. Check that "
            "each frozen target card is internally consistent with its task, selector, heading, "
            "path, and isolation metadata. Conservative empty semantic fields are allowed in this "
            "controlled benchmark if they avoid leakage."
        )
    else:
        instruction = (
            "Act as the independent annotation auditor. Check rubric ideas for atomicity, anchor "
            "specificity, terminology and protected literals against gold, graded-source relevance "
            "against the included excerpts, bibliography-key validity, and leakage findings. Return "
            "needs_revision only for defects the annotation curator can correct in the rubric, "
            "citations, or graded-source spans. Corpus warnings and invalid keys produced only by "
            "benchmark generations are separate diagnostics: do not use them to reject an otherwise "
            "valid annotation. Treat the mechanical terminology, bibliography, citation, and privacy "
            "fields as authoritative; do not claim a term or key is absent when those fields report "
            "it present. Legitimate supporting prose shared with another case is not leakage."
        )
    schema = {
        "role": role,
        "project_id": project_id,
        "cases": [
            {
                "case_id": "exact supplied ID",
                "decision": "approved or needs_revision",
                "issues": ["concise issue codes or an empty list"],
            }
        ],
    }
    return (
        f"{instruction}\n\nReturn JSON only with this shape:\n{canonical_json(schema)}"
        f"\n\nPRIVATE REVIEW PACKET:\n{canonical_json({'project_id': project_id, 'cases': packets})}"
    )


def _validate_response(
    value: dict[str, Any], role: str, project_id: str, expected_ids: set[str]
) -> dict[str, Any]:
    if value.get("role") != role or value.get("project_id") != project_id:
        raise BenchmarkError(f"{role} response identity mismatch for {project_id}")
    results = value.get("cases")
    if not isinstance(results, list):
        raise BenchmarkError(f"{role} response cases must be a list for {project_id}")
    observed: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            raise BenchmarkError(f"{role} case response must be an object for {project_id}")
        case_id = str(result.get("case_id", ""))
        observed.add(case_id)
        if result.get("decision") not in {"approved", "needs_revision"}:
            raise BenchmarkError(f"Invalid {role} decision for {case_id}")
        issues = result.get("issues")
        if not isinstance(issues, list) or not all(isinstance(item, str) for item in issues):
            raise BenchmarkError(f"Invalid {role} issues for {case_id}")
    if observed != expected_ids or len(results) != len(expected_ids):
        raise BenchmarkError(
            f"{role} response case IDs differ for {project_id}: "
            f"expected {sorted(expected_ids)}, got {sorted(observed)}"
        )
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="benchmark/cases.local.yaml")
    parser.add_argument("--models", default="benchmark/models.local.yaml")
    parser.add_argument("--private-root", default="benchmark/private.local")
    parser.add_argument(
        "--mechanical-audit", default="benchmark/private.local/annotation-audit.json"
    )
    parser.add_argument("--confirm-paid-run", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--project",
        action="append",
        dest="projects",
        help="Review only the selected project ID; repeat for multiple projects",
    )
    parser.add_argument(
        "--role",
        choices=("both", "card_author", "auditor"),
        default="both",
    )
    args = parser.parse_args()

    private_root = Path(args.private_root).resolve()
    cases = load_cases(Path(args.cases).resolve())
    models = load_models(Path(args.models).resolve())
    mechanical = json.loads(Path(args.mechanical_audit).read_text(encoding="utf-8"))
    groups = _case_groups(cases)
    if args.projects:
        requested = set(args.projects)
        unknown = sorted(requested - groups.keys())
        if unknown:
            raise BenchmarkError(f"Unknown annotation review projects: {unknown}")
        groups = {project_id: groups[project_id] for project_id in sorted(requested)}
    roles = ["card_author", "auditor"] if args.role == "both" else [args.role]
    request_count = len(groups) * len(roles)
    print(f"Independent annotation review will make exactly {request_count} CLI requests.")
    if args.dry_run:
        for project_id, project_cases in groups.items():
            card_packets = [_card_author_packet(case, private_root) for case in project_cases]
            auditor_packets = [
                _auditor_packet(case, private_root, mechanical) for case in project_cases
            ]
            card_prompt = _render_prompt("card_author", project_id, card_packets)
            auditor_prompt = _render_prompt("auditor", project_id, auditor_packets)
            gold_values = [
                Path(load_prepared(case, private_root)["gold_path"]).read_text(encoding="utf-8")
                for case in project_cases
            ]
            if any(gold and gold in card_prompt for gold in gold_values):
                raise BenchmarkError(f"Gold leaked into card-author packet for {project_id}")
            print(
                canonical_json(
                    {
                        "project_id": project_id,
                        "cases": len(project_cases),
                        "card_prompt_chars": len(card_prompt),
                        "auditor_prompt_chars": len(auditor_prompt),
                        "card_prompt_gold_free": True,
                    }
                )
            )
        return 0
    if not args.confirm_paid_run:
        raise BenchmarkError("Refusing subscription-backed CLI reviews without --confirm-paid-run")

    role_specs = {
        "card_author": next(spec for spec in models.judges if spec.family == "gemini"),
        "auditor": next(spec for spec in models.judges if spec.family == "openai"),
    }
    output_root = private_root / "annotation-reviews"
    for role in roles:
        spec = role_specs[role]
        client = build_model_client(spec)
        client.check_available()
        for project_id, project_cases in groups.items():
            packets = (
                [_card_author_packet(case, private_root) for case in project_cases]
                if role == "card_author"
                else [_auditor_packet(case, private_root, mechanical) for case in project_cases]
            )
            prompt = _render_prompt(role, project_id, packets)
            response = client.generate(prompt, temperature=models.temperature, max_tokens=4000)
            validated = _validate_response(
                extract_json_object(response),
                role,
                project_id,
                {case.id for case in project_cases},
            )
            _atomic_private_json(output_root / f"{role}-{project_id}.json", validated)
            print(f"{role} {project_id}: validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
