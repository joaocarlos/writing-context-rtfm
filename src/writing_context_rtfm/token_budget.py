"""Token budgeting utilities."""

from typing import Any


def _load_encoding() -> Any | None:
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        # tiktoken may be installed while its encoding data is unavailable offline.
        # Token estimation already has a deterministic character-count fallback.
        return None


_ENCODING: Any | None = _load_encoding()


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
