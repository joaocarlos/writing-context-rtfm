"""Token budgeting utilities."""

def estimate_tokens(text: str) -> int:
    """Estimate tokens for a given text based on a simple heuristic (char_count // 4)."""
    return max(1, len(text) // 4)

def estimate_span_tokens(line_start: int, line_end: int, avg_tokens_per_line: int = 15) -> int:
    """Estimate token count based on line span."""
    lines = max(1, line_end - line_start + 1)
    return lines * avg_tokens_per_line
