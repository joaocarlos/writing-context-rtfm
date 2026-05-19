"""Common utilities for the extension."""
import os
from pathlib import Path
from typing import List

# ---------------------------------------------------------------------------
# Exclusion rules
# ---------------------------------------------------------------------------
EXCLUDED_SOURCE_PATTERNS = [
    ".writing-context/",
    ".rtfm/",
    ".git/",
    "__pycache__/",
]
EXCLUDED_SOURCE_EXTENSIONS = {".sqlite", ".db", ".lock"}

def is_allowed_source(path: str) -> bool:
    """Return True if the path should appear as a manuscript source span."""
    normalized = path.replace("\\", "/")
    for pat in EXCLUDED_SOURCE_PATTERNS:
        if pat in normalized:
            return False
    if Path(normalized).suffix.lower() in EXCLUDED_SOURCE_EXTENSIONS:
        return False
    return True

# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------
KEYWORD_STOPWORDS = {
    "write", "the", "section", "detailing", "and", "of", "for", "a", "an",
    "in", "to", "that", "with", "this", "is", "are", "be", "from", "on",
    "how", "using", "about", "into", "each", "by", "our",
}

def extract_keywords(text: str) -> List[str]:
    """Extract keywords from a string by filtering out stopwords."""
    words = text.lower().split()
    return [w.strip(".,;:") for w in words
            if w.strip(".,;:") not in KEYWORD_STOPWORDS and len(w.strip(".,;:")) > 3]
