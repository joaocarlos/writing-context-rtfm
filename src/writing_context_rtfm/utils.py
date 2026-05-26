"""Common utilities for the extension."""
import os
from pathlib import Path
from typing import List

from writing_context_rtfm.latex import scan_latex_commands as scan_latex_commands

# ---------------------------------------------------------------------------
# Exclusion rules
# ---------------------------------------------------------------------------
EXCLUDED_SOURCE_PATTERNS = [
    ".writing-context/",
    ".rtfm/",
    ".git/",
    "__pycache__/",
    ".codex/",
    ".claude/",
    ".github/",
    ".vscode/",
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


def resolve_rtfm_db_path(project_root: Path) -> Path:
    """Resolve the RTFM library database path, prioritizing:
    1. RTFM_DB environment variable (absolute or relative to project_root)
    2. project_root / ".rtfm" / "library.db"
    3. project_root / "library.db"
    Fallback: project_root / ".rtfm" / "library.db"
    """
    env_db = os.environ.get("RTFM_DB")
    if env_db:
        db_path = Path(env_db)
        if db_path.is_absolute():
            return db_path
        return project_root / db_path

    primary_path = project_root / ".rtfm" / "library.db"
    if primary_path.exists():
        return primary_path

    fallback_path = project_root / "library.db"
    if fallback_path.exists():
        return fallback_path

    return primary_path

