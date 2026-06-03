"""Token budgeting utilities."""

from typing import Any

_ENCODING: Any | None = None
try:
    import tiktoken

    _ENCODING = tiktoken.get_encoding("cl100k_base")
except ImportError:
    _ENCODING = None


def estimate_tokens(text: str) -> int:
    """Estimate tokens for a given text.

    Uses tiktoken's cl100k_base encoding if available, falling back to
    the char_count // 4 heuristic otherwise.
    """
    if _ENCODING is not None:
        try:
            return len(_ENCODING.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, len(text) // 4)


def estimate_span_tokens(line_start: int, line_end: int, avg_tokens_per_line: int = 15) -> int:
    """Estimate token count based on line span."""
    lines = max(1, line_end - line_start + 1)
    return lines * avg_tokens_per_line
