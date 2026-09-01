#!/usr/bin/env python3
"""Unified benchmark regression tool.

Verifies retrieval, candidate exposure, and bibliographic handoff invariants
against frozen baseline manifests and anonymized aggregates without hardcoded metric ratios.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Required benchmark aggregate not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid JSON format in {path}: expected dict, got {type(data).__name__}")
    return data


def verify_exposure_aggregates(exposure_data: dict[str, Any], verbose: bool = False) -> list[str]:
    errors: list[str] = []
    version = exposure_data.get("benchmark_version", "")
    freeze_hash = exposure_data.get("freeze_sha256", "")
    case_count = exposure_data.get("case_count", 0)

    if not version or not freeze_hash:
        errors.append("Exposure aggregate missing benchmark_version or freeze_sha256")
    if case_count <= 0:
        errors.append(f"Exposure aggregate case_count must be positive, got {case_count}")

    policies = exposure_data.get("policies", {})
    if "current" not in policies:
        errors.append("Exposure aggregate missing 'current' policy metrics")
        return errors

    curr = policies["current"]
    expected_sources = curr.get("expected_sources", 0)
    raw_exposed = curr.get("raw_query_exposed_sources", 0)
    post_exposed = curr.get("post_exclusion_query_exposed_sources", 0)
    selected_sources = curr.get("selected_sources", 0)
    selection_regret = curr.get("selection_regret", 0)
    hard_violations = curr.get("hard_constraint_violations", 0)

    # Invariants
    if expected_sources <= 0:
        errors.append(f"Invalid expected_sources: {expected_sources}")
    if raw_exposed > expected_sources:
        errors.append(
            f"Raw exposed sources ({raw_exposed}) cannot exceed expected ({expected_sources})"
        )
    if post_exposed > raw_exposed:
        errors.append(
            f"Post-exclusion exposed ({post_exposed}) cannot exceed raw exposed ({raw_exposed})"
        )
    if selected_sources > post_exposed:
        errors.append(
            f"Selected sources ({selected_sources}) cannot exceed post-exclusion exposed ({post_exposed})"
        )
    if selection_regret != 0:
        errors.append(
            f"Selection regret must be 0 for current production policy, got {selection_regret}"
        )
    if hard_violations != 0:
        errors.append(f"Hard constraint violations must be 0, got {hard_violations}")

    # Verify score-tail policy negative-decision invariants (must not have been promoted)
    if "score_tail_adaptive" in policies:
        st = policies["score_tail_adaptive"]
        if st.get("promotion", {}).get("eligible", False):
            errors.append("score_tail_adaptive must not be eligible for production promotion")

    if verbose:
        print(
            f"✓ Exposure Aggregate ({version}): {selected_sources}/{expected_sources} selected, 0 regret, freeze={freeze_hash[:8]}"
        )

    return errors


def verify_handoff_aggregates(handoff_data: dict[str, Any], verbose: bool = False) -> list[str]:
    errors: list[str] = []
    version = handoff_data.get("benchmark_version", "")
    freeze_hash = handoff_data.get("freeze_sha256", "")
    case_count = handoff_data.get("case_count", 0)

    if not version or not freeze_hash:
        errors.append("Handoff aggregate missing benchmark_version or freeze_sha256")
    if case_count <= 0:
        errors.append(f"Handoff aggregate case_count must be positive, got {case_count}")

    variants = handoff_data.get("variants", {})
    if "current" not in variants:
        errors.append("Handoff aggregate missing 'current' variant metrics")
        return errors

    curr = variants["current"]
    gain = curr.get("cases_with_final_selection_gain", 0)
    if gain != 0:
        errors.append(f"Current variant final selection gain must be 0, got {gain}")

    # Fallback and Reconstruction must remain benchmark-only variants (not promoted)
    for vname in ("fallback", "reconstruction"):
        if vname in variants:
            vinfo = variants[vname]
            # Verify cost increase ratio is positive (demonstrating why they were not promoted)
            inc = vinfo.get("candidate_processing_increase_ratio", 0.0)
            if inc <= 0.0:
                errors.append(
                    f"{vname} variant expected candidate processing increase ratio > 0, got {inc}"
                )

    if verbose:
        print(
            f"✓ BibTeX Handoff Aggregate ({version}): {case_count} cases, freeze={freeze_hash[:8]}"
        )

    return errors


def verify_retrieval_aggregates(retrieval_data: dict[str, Any], verbose: bool = False) -> list[str]:
    errors: list[str] = []
    diag = retrieval_data.get("candidate_diagnostics", {})
    if "pack_baseline" not in diag:
        errors.append("Retrieval aggregate missing 'pack_baseline' diagnostics")
        return errors

    pb = diag["pack_baseline"]
    evaluated = pb.get("expected_sources_evaluated", 0)
    selected = pb.get("selected", 0)
    never_retrieved = pb.get("never_retrieved", 0)

    if evaluated <= 0:
        errors.append(f"Evaluated expected sources must be positive, got {evaluated}")
    if selected + never_retrieved != evaluated:
        errors.append(
            f"Diagnostic partitioning mismatch: selected ({selected}) + never_retrieved ({never_retrieved}) != evaluated ({evaluated})"
        )

    if verbose:
        print(f"✓ Retrieval Aggregate: {selected}/{evaluated} evaluated sources selected")

    return errors


def run_benchmark_regression(
    aggregates_dir: Path,
    verbose: bool = False,
) -> int:
    print(f"Running Benchmark Regression Suite against: {aggregates_dir.as_posix()}")
    exposure_path = aggregates_dir / "pilot-v1-exposure.json"
    handoff_path = aggregates_dir / "pilot-v1-bibtex-handoff.json"
    retrieval_path = aggregates_dir / "pilot-retrieval.json"

    all_errors: list[str] = []

    # 1. Candidate Exposure Aggregate Invariants
    try:
        exp_data = _load_json(exposure_path)
        all_errors.extend(verify_exposure_aggregates(exp_data, verbose=verbose))
    except Exception as e:
        all_errors.append(f"Failed to verify candidate exposure aggregate: {e}")

    # 2. BibTeX Handoff Aggregate Invariants
    try:
        handoff_data = _load_json(handoff_path)
        all_errors.extend(verify_handoff_aggregates(handoff_data, verbose=verbose))
    except Exception as e:
        all_errors.append(f"Failed to verify bibtex handoff aggregate: {e}")

    # 3. Retrieval Aggregate Invariants
    try:
        retrieval_data = _load_json(retrieval_path)
        all_errors.extend(verify_retrieval_aggregates(retrieval_data, verbose=verbose))
    except Exception as e:
        all_errors.append(f"Failed to verify retrieval aggregate: {e}")

    if all_errors:
        print("\n❌ Benchmark Regression FAILED with errors:")
        for err in all_errors:
            print(f"  - {err}")
        return 1

    print("\n✅ All benchmark regression assertions passed successfully.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified benchmark regression verification")
    parser.add_argument(
        "--aggregates-dir",
        default="benchmark/anonymized_aggregates",
        help="Path to anonymized aggregates directory (default: benchmark/anonymized_aggregates)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose assertion output",
    )
    args = parser.parse_args()

    aggregates_dir = Path(args.aggregates_dir).resolve()
    return run_benchmark_regression(aggregates_dir, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
