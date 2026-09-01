#!/usr/bin/env python3
"""Audit private benchmark citation keys, rubrics, spans, cards, and leakage."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from writing_context_rtfm.benchmark import ArtifactStore, audit_case_annotations, load_cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="benchmark/cases.local.yaml")
    parser.add_argument("--private-root", default="benchmark/private.local")
    parser.add_argument("--artifacts-root", default="benchmark/artifacts.local")
    parser.add_argument("--output", default="benchmark/private.local/annotation-audit.json")
    args = parser.parse_args()

    report = audit_case_annotations(
        load_cases(Path(args.cases).resolve()),
        private_root=Path(args.private_root).resolve(),
        artifacts=ArtifactStore(Path(args.artifacts_root).resolve()),
    )
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(output)
    summary = {
        "output": str(output),
        "cases": len(report["cases"]),
        "projects": len(report["projects"]),
        "all_mechanical_checks_pass": report["all_mechanical_checks_pass"],
        "annotations_resolved": sum(
            value["annotation_resolved"] for value in report["cases"].values()
        ),
        "unresolved_annotation_case_ids": report["unresolved_annotation_case_ids"],
        "unresolved_issue_code_counts": dict(
            sorted(
                Counter(
                    code
                    for value in report["cases"].values()
                    if not value["annotation_resolved"]
                    for code in value["auditor_issue_codes"]
                ).items()
            )
        ),
        "corpus_warning_case_ids": sorted(
            case_id for case_id, value in report["cases"].items() if value["corpus_warnings"]
        ),
        "issues": {
            case_id: value["issues"]
            for case_id, value in report["cases"].items()
            if value["issues"]
        },
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
