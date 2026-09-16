"""Common utilities for the extension."""

import contextlib
import os
from pathlib import Path
from typing import Any

from writing_context_rtfm.latex import scan_latex_commands as scan_latex_commands

# ---------------------------------------------------------------------------
# Exclusion rules
# ---------------------------------------------------------------------------
EXCLUDED_SOURCE_PATTERNS = [
    ".writing-context/",
    ".rtfm/",
    ".git/",
    "__pycache__/",
]
EXCLUDED_SOURCE_EXTENSIONS = {
    ".sqlite",
    ".db",
    ".lock",
    ".cls",
    ".sty",
    ".bst",
    ".dtx",
    ".ins",
    ".aux",
    ".log",
    ".out",
    ".toc",
    ".synctex.gz",
}


def load_ignore_spec(project_root: str | Path) -> Any | None:
    """Load combined ignore spec from .gitignore and .rtfmignore in project root.

    .rtfmignore patterns take precedence (appended after .gitignore).
    Returns a pathspec.PathSpec instance or None if no ignore files exist or pathspec is unavailable.
    """
    root_path = Path(project_root).resolve()
    lines: list[str] = []

    # 1. .gitignore (if present)
    gi_path = root_path / ".gitignore"
    if gi_path.is_file():
        with contextlib.suppress(Exception):
            lines.extend(gi_path.read_text(encoding="utf-8", errors="replace").splitlines())

    # 2. .rtfmignore (if present, takes precedence if appended)
    ri_path = root_path / ".rtfmignore"
    if ri_path.is_file():
        with contextlib.suppress(Exception):
            lines.extend(ri_path.read_text(encoding="utf-8", errors="replace").splitlines())

    if not lines:
        return None

    try:
        import pathspec

        return pathspec.PathSpec.from_lines("gitignore", lines)
    except Exception:
        return None


def is_path_ignored(rel_path: str | Path, spec: Any | None, is_dir: bool = False) -> bool:
    """Check if a relative path matches the ignore spec.

    If is_dir is True, checks both 'rel_path' and 'rel_path/' to match directory patterns.
    """
    if spec is None:
        return False
    norm_path = Path(rel_path).as_posix().lstrip("./")
    if not norm_path:
        return False
    if is_dir:
        return bool(spec.match_file(norm_path) or spec.match_file(f"{norm_path}/"))
    return bool(spec.match_file(norm_path))


def is_allowed_source(path: str) -> bool:
    """Return True if the path should appear as a manuscript source span."""
    normalized = path.replace("\\", "/")
    for pat in EXCLUDED_SOURCE_PATTERNS:
        if pat in normalized:
            return False
    return Path(normalized).suffix.lower() not in EXCLUDED_SOURCE_EXTENSIONS


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------
KEYWORD_STOPWORDS = {
    "write",
    "the",
    "section",
    "detailing",
    "and",
    "of",
    "for",
    "a",
    "an",
    "in",
    "to",
    "that",
    "with",
    "this",
    "is",
    "are",
    "be",
    "from",
    "on",
    "how",
    "using",
    "about",
    "into",
    "each",
    "by",
    "our",
}


def extract_keywords(text: str) -> list[str]:
    """Extract keywords from a string by filtering out stopwords."""
    words = text.lower().split()
    return [
        w.strip(".,;:")
        for w in words
        if w.strip(".,;:") not in KEYWORD_STOPWORDS and len(w.strip(".,;:")) > 3
    ]


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
