#!/usr/bin/env python3
"""Compatibility shim for the retired two-condition generation experiment."""

import sys

from writing_context_rtfm.benchmark import main

if __name__ == "__main__":
    print(
        "The fixed A/B experiment was replaced by benchmark_context_quality.py; "
        "forwarding arguments to the case-driven runner.",
        file=sys.stderr,
    )
    raise SystemExit(main())
