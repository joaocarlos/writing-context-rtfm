"""
Pytest configuration for the tests package.

Adds the `tests/` directory to sys.path so that intra-test helper packages
(e.g. `eval.rubric`, `eval.metrics`) can be imported without an install step.
"""
import sys
from pathlib import Path

# Make `tests/` importable as a root so that `from eval.rubric import ...` works.
sys.path.insert(0, str(Path(__file__).parent))
