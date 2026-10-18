#!/usr/bin/env python3
"""Compatibility shim for the retired two-condition generation experiment."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.eval.benchmark import main

if __name__ == "__main__":
    print(
        "The fixed A/B experiment was replaced by benchmark_context_quality.py; "
        "forwarding arguments to the case-driven runner.",
        file=sys.stderr,
    )
    raise SystemExit(main())
