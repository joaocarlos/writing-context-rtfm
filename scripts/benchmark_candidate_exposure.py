#!/usr/bin/env python3
"""Freeze, run, and report the Pilot v1 candidate exposure experiment."""

from __future__ import annotations

import argparse
from pathlib import Path

from writing_context_rtfm.candidate_exposure import (
    run_candidate_exposure,
    write_candidate_exposure_report,
    write_pilot_v1_freeze,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("freeze", "run", "report")
    )
    parser.add_argument("--cases", default="benchmark/cases.local.yaml")
    parser.add_argument("--private-root", default="benchmark/private.local")
    parser.add_argument(
        "--freeze",
        default="benchmark/private.local/pilot-v1-exposure-freeze.json",
    )
    parser.add_argument(
        "--results",
        default="benchmark/private.local/pilot-v1-exposure-results.json",
    )
    parser.add_argument(
        "--output",
        default="benchmark/anonymized_aggregates/pilot-v1-exposure.json",
    )
    args = parser.parse_args()

    cases_path = Path(args.cases).resolve()
    private_root = Path(args.private_root).resolve()
    freeze_path = Path(args.freeze).resolve()
    results_path = Path(args.results).resolve()
    output_path = Path(args.output).resolve()

    if args.command == "freeze":
        result = write_pilot_v1_freeze(cases_path, private_root, freeze_path)
        print(f"Pilot v1 frozen: {result['freeze_sha256']}")
    elif args.command == "run":
        result = run_candidate_exposure(
            cases_path, private_root, freeze_path, results_path
        )
        print(f"Candidate exposure records ready: {len(result['records'])}")
    else:
        result = write_candidate_exposure_report(results_path, output_path)
        print(
            f"Candidate exposure report written: {output_path} "
            f"({result['case_count']} cases)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
