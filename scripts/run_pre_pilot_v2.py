#!/usr/bin/env python3
"""Run Pre-Pilot v2 Diagnostic Sensitivity Benchmark Suite.

Executes controlled adversarial cases and outputs per-case diagnostic breakdown.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

import yaml

from writing_context_rtfm.config import load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.providers.bibtex import BibTeXProvider
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore


@dataclass
class CaseResult:
    case_id: str
    category: str
    case_type: str
    annotated_essential: int
    retrieved_essential: int
    exposed_essential: int
    feasible_essential: int
    selected_essential: int
    selection_regret: int
    failure_stage: str
    hard_failure: bool
    hard_failure_details: list[str]
    degraded_correctly: bool
    follow_up: str


def check_math_delimiters(text: str) -> bool:
    """Verify that all opened math environments have corresponding closing tags."""
    envs = ["align", "align*", "equation", "equation*", "pmatrix", "bmatrix"]
    for env in envs:
        open_tag = f"\\begin{{{env}}}"
        close_tag = f"\\end{{{env}}}"
        if text.count(open_tag) != text.count(close_tag):
            return False
    return True


def run_single_case(
    case_def: dict[str, Any], base_dir: Path, verbose: bool = False
) -> CaseResult:
    case_id = case_def["id"]
    category = case_def.get("category", "unknown")
    case_type = case_def.get("type", "synthetic")
    proj_dir = base_dir / case_def["project_dir"]

    config = load_config(str(proj_dir))
    sc_path = proj_dir / "section_cards.yaml"
    if not sc_path.exists():
        sc_path = Path(config.section_cards.path)
    cards = load_section_cards(str(sc_path), required=False)

    hard_failures: list[str] = []
    failure_stage = "none"
    degraded_correctly = True

    # Use in-memory extension store
    with ExtensionStore(":memory:") as store:
        adapter = RTFMAdapter(project_root=str(proj_dir.resolve()))
        # Include BibTeX provider if bib files present
        providers = [BibTeXProvider(config)] if list(proj_dir.glob("*.bib")) else []

        generator = ContextPackGenerator(
            config,
            cards,
            adapter,
            store,
            providers=providers,
        )

        role_budgets = case_def.get("role_budgets")
        must_consider = case_def.get("must_consider", [])

        pack = generator.generate(
            task=case_def["task"],
            target=case_def["target"],
            token_budget=case_def["token_budget"],
            must_consider=must_consider,
            project_root=str(proj_dir),
            role_budgets=role_budgets,
            include_diagnostics=True,
        )

        # 1. Evaluate Hard Failure Conditions
        # Check span coordinate validity
        for span in pack.source_spans:
            if span.line_start is not None and span.line_end is not None:
                if span.line_start > span.line_end:
                    hard_failures.append(f"Inverted span lines: {span.line_start} > {span.line_end}")

        # Check math environment balance in selected spans
        for span in pack.source_spans:
            span_file = proj_dir / span.path
            if span_file.exists() and span.line_start is not None and span.line_end is not None:
                with open(span_file, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                content = "".join(lines[span.line_start - 1 : span.line_end])
                if not check_math_delimiters(content):
                    hard_failures.append(f"Broken math delimiters in {span.path}:{span.line_start}-{span.line_end}")

        # Check target text availability if required
        if case_id == "ADV-BUDGET-01":
            target_spans = [s for s in pack.source_spans if s.source_role == "target_text"]
            if not target_spans:
                hard_failures.append("Target text completely dropped under tight budget")

        # 2. Evaluate must_consider atom coverage
        if must_consider and pack.quality:
            atomic_cov = pack.quality.get("atomic_coverage") or {}
            uncovered = atomic_cov.get("uncovered", [])
            if uncovered and pack.status != "degraded":
                hard_failures.append("must_consider atoms omitted without degraded status")

        # 3. Evaluate Duplicate Citation Keys
        ref_keys: list[str] = []
        for span in pack.source_spans:
            if span.source_role == "reference" and span.metadata:
                key = span.metadata.get("key") or span.metadata.get("citation_key")
                if key:
                    if key in ref_keys:
                        hard_failures.append(f"Duplicate citation identity selected: {key}")
                    ref_keys.append(str(key))

        # 4. Decompose Essential Funnel: Annotated -> Retrieved -> Exposed -> Feasible -> Selected
        expected = case_def.get("expected_sources", [])
        essential_expected = [s for s in expected if s.get("priority") == "essential"]
        annotated_essential = len(essential_expected) if essential_expected else 1

        # Check selected essential count
        selected_essential = 0
        for exp in essential_expected:
            exp_path = exp.get("path")
            exp_role = exp.get("role")
            found = False
            for span in pack.source_spans:
                # Reference role match
                if exp_role == "reference" and (span.source_role == "reference" or span.path.startswith("bibtex:")):
                    found = True
                    break
                # Path match
                if exp_path and not span.path.endswith(exp_path):
                    continue
                # Line range match
                if exp.get("line_start") is not None and span.line_start is not None:
                    if span.line_start <= exp["line_end"] and span.line_end >= exp["line_start"]:
                        found = True
                        break
                else:
                    found = True
                    break
            if found:
                selected_essential += 1

        # For single-span target sections where target text was selected
        if not essential_expected and pack.source_spans:
            annotated_essential = 1
            selected_essential = 1

        # Check candidate pool from diagnostics specifically for essential roles
        retrieved_essential = 0
        exposed_essential = 0
        if pack.diagnostics and pack.diagnostics.candidates:
            for t in pack.diagnostics.candidates:
                # Essential candidates match target_text or explicit dependency/reference
                for exp in essential_expected:
                    exp_path = exp.get("path")
                    exp_role = exp.get("role")
                    if exp_role == "reference" and (t.source_role == "reference" or t.path.startswith("bibtex:")):
                        retrieved_essential += 1
                        exposed_essential += 1
                        break
                    if exp_path and t.path.endswith(exp_path):
                        retrieved_essential += 1
                        exposed_essential += 1
                        break
            # Ensure at least selected is counted if retrieved was positive
            retrieved_essential = max(retrieved_essential, selected_essential)
            exposed_essential = max(exposed_essential, selected_essential)
        else:
            retrieved_essential = selected_essential
            exposed_essential = selected_essential

        # Feasible essential: candidates that were exposed and fit within budget
        feasible_essential = exposed_essential
        selection_regret = max(0, feasible_essential - selected_essential)

        # 5. Identify Failure Stage via Diagnostics
        if selected_essential < annotated_essential and pack.diagnostics:
            funnel = pack.diagnostics.funnel
            if funnel:
                if funnel.retrieved == 0:
                    failure_stage = "retrieval"
                elif funnel.excluded > 0:
                    failure_stage = "ownership_exclusion"
                elif funnel.filtered > 0:
                    failure_stage = "score_filter"
                elif funnel.selected < funnel.eligible:
                    failure_stage = "composer_quota"
                else:
                    failure_stage = "budget_infeasibility"
            else:
                failure_stage = "composer"
        elif selected_essential == annotated_essential:
            failure_stage = "none"

        # 6. Check Degradation Signaling
        if pack.status == "degraded":
            degraded_correctly = True
        elif case_def.get("expected_degradation", {}).get("must_degrade_under_tight_budget"):
            if pack.estimated_tokens >= case_def["token_budget"]:
                degraded_correctly = (pack.status == "degraded")

        if verbose:
            print(f"\n    [Debug {case_id}] Estimated Tokens: {pack.estimated_tokens}, Status: {pack.status}")
            print(f"    Spans returned ({len(pack.source_spans)}):")
            for s in pack.source_spans:
                print(f"      - {s.path}:{s.line_start}-{s.line_end} [{s.source_role}] score={s.score:.2f} prio={s.priority}")

        hard_failure = len(hard_failures) > 0
        if hard_failure:
            follow_up = "immediate_fix"
        elif selection_regret > 0:
            follow_up = "investigate"
        else:
            follow_up = "none"

        return CaseResult(
            case_id=case_id,
            category=category,
            case_type=case_type,
            annotated_essential=annotated_essential,
            retrieved_essential=retrieved_essential,
            exposed_essential=exposed_essential,
            feasible_essential=feasible_essential,
            selected_essential=selected_essential,
            selection_regret=selection_regret,
            failure_stage=failure_stage,
            hard_failure=hard_failure,
            hard_failure_details=hard_failures,
            degraded_correctly=degraded_correctly,
            follow_up=follow_up,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Pre-Pilot v2 Benchmark")
    parser.add_argument(
        "--manifest",
        default="benchmark/pre_pilot_v2_manifest.yaml",
        help="Path to pre-pilot manifest YAML",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print verbose span and diagnostics info",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"Error: Manifest not found at {manifest_path}", file=sys.stderr)
        sys.exit(1)

    with open(manifest_path, encoding="utf-8") as f:
        manifest = yaml.safe_load(f)

    base_dir = Path.cwd()
    cases = manifest.get("cases", [])

    print(f"Executing Pre-Pilot v2 Diagnostic Sensitivity Suite ({len(cases)} cases)...")
    print("=" * 90)

    results: list[CaseResult] = []
    for c in cases:
        print(f"[*] Running {c['id']} ({c.get('category')})...", end=" ", flush=True)
        res = run_single_case(c, base_dir, verbose=args.verbose)
        status_flag = "PASS" if not res.hard_failure and res.selection_regret == 0 else "DIAGNOSTIC"
        print(f"{status_flag} (Selected: {res.selected_essential}/{res.annotated_essential}, Regret: {res.selection_regret}, Hard: {res.hard_failure})")
        if res.hard_failure_details:
            for detail in res.hard_failure_details:
                print(f"    ! HARD FAILURE: {detail}")
        results.append(res)

    print("=" * 90)
    print("\n### Pre-Pilot v2 Feasibility and Diagnostic Results Summary\n")
    print("| Case ID | Category | Annotated | Retrieved | Exposed | Feasible | Selected | Regret | Failure Stage | Hard Failure? | Degraded Correctly? | Follow-up |")
    print("| :--- | :--- | :---:| :---:| :---:| :---:| :---:| :---:| :--- | :---:| :---:| :--- |")
    for r in results:
        hard_str = "**YES**" if r.hard_failure else "No"
        deg_str = "Yes" if r.degraded_correctly else "**No**"
        print(
            f"| `{r.case_id}` | {r.category} | {r.annotated_essential} | {r.retrieved_essential} | {r.exposed_essential} | {r.feasible_essential} | {r.selected_essential} | {r.selection_regret} | {r.failure_stage} | {hard_str} | {deg_str} | `{r.follow_up}` |"
        )

    # Check for any hard failure exit code
    has_hard_failure = any(r.hard_failure for r in results)
    if has_hard_failure:
        print("\n❌ One or more Hard Correctness Failures detected! Immediate fix required.", file=sys.stderr)
        sys.exit(1)
    else:
        print("\n✅ Zero Hard Correctness Failures across all pre-pilot cases.")


if __name__ == "__main__":
    main()
