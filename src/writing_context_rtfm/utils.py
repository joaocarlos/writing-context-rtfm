"""Common utilities for the extension."""

import contextlib
import os
import re
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
    ".claude/worktrees/",
    ".codegraph/",
    "__pycache__/",
    "claude.md",
    "agents.md",
    "gemini.md",
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

_SKIP_IGNORE_WALK_DIRS = frozenset(
    {
        ".git",
        ".rtfm",
        ".writing-context",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".tox",
        "dist",
        "build",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".codegraph",
    }
)
_RTFMIGNORE_SECTION_RE = re.compile(r"^\[([a-zA-Z0-9_-]+)\]$")
_EXCLUDED_SUBPATHS: tuple[tuple[str, ...], ...] = ((".claude", "worktrees"),)


def _is_excluded_subpath(parts: list[str]) -> bool:
    """Return True if path segments contain any always-excluded subpath (e.g. .claude/worktrees)."""
    for sub in _EXCLUDED_SUBPATHS:
        sub_len = len(sub)
        if len(parts) >= sub_len and any(
            tuple(parts[i : i + sub_len]) == sub for i in range(len(parts) - sub_len + 1)
        ):
            return True
    return False


def _build_gitignore_spec(lines: list[str], pathspec_mod: Any) -> Any:
    """Compile gitignore pattern lines into a GitIgnoreSpec or PathSpec instance."""
    spec_cls = getattr(pathspec_mod, "GitIgnoreSpec", None)
    if spec_cls is not None:
        return spec_cls.from_lines(lines)
    return pathspec_mod.PathSpec.from_lines("gitignore", lines)


def _extract_rtfmignore_index_lines(text: str) -> list[str]:
    """Return index-exclusion lines from .rtfmignore, ignoring [versions] and other non-index sections."""
    section = "index"
    index_lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        match = _RTFMIGNORE_SECTION_RE.match(stripped)
        if match:
            section = match.group(1).lower()
            continue
        if section == "index" and stripped and not stripped.startswith("#"):
            index_lines.append(raw)
    return index_lines


def _spec_verdict(spec: Any, rel_path: str) -> bool | None:
    """Return True if ignored, False if negated (!), or None if unmatched."""
    check = getattr(spec, "check_file", None)
    if check is not None:
        res = check(rel_path)
        return bool(res.include) if res.include is not None else None
    return True if spec.match_file(rel_path) else None


class _ProjectIgnoreSpec:
    """Hierarchical ignore matcher combining root/nested .gitignore files and .rtfmignore."""

    def __init__(self, git_specs: dict[str, Any], rtfm_spec: Any | None) -> None:
        self._git_specs = git_specs
        self._rtfm_spec = rtfm_spec

    def _decide_git(self, clean_path: str, is_dir: bool) -> bool:
        parts = clean_path.split("/")
        verdict: bool | None = None
        for depth in range(len(parts)):
            rel_dir = "/".join(parts[:depth])
            spec = self._git_specs.get(rel_dir)
            if spec is None:
                continue
            sub = "/".join(parts[depth:])
            inc = _spec_verdict(spec, f"{sub}/" if is_dir else sub)
            if inc is None and is_dir:
                inc = _spec_verdict(spec, sub)
            if inc is not None:
                verdict = inc
        return bool(verdict)

    def match_file(self, path: str) -> bool:
        norm = path.replace("\\", "/").removeprefix("./").lstrip("/")
        is_dir = norm.endswith("/")
        clean = norm.strip("/")
        if not clean or clean == ".":
            return False

        parts = clean.split("/")
        if _is_excluded_subpath(parts):
            return True

        if self._rtfm_spec is not None:
            rtfm_inc = _spec_verdict(self._rtfm_spec, f"{clean}/" if is_dir else clean)
            if rtfm_inc is None and is_dir:
                rtfm_inc = _spec_verdict(self._rtfm_spec, clean)
            if rtfm_inc is not None:
                return rtfm_inc

        if not self._git_specs:
            return False

        return any(
            self._decide_git("/".join(parts[:depth]), is_dir=True) for depth in range(1, len(parts))
        ) or self._decide_git(clean, is_dir=is_dir)


def _collect_git_specs(root_path: Path, pathspec_mod: Any) -> dict[str, Any]:
    """Walk project directories and compile any non-empty .gitignore files."""
    git_specs: dict[str, Any] = {}
    for dirpath, dirnames, filenames in os.walk(root_path):
        rel_dir_path = Path(dirpath).relative_to(root_path)
        rel_dir = "" if str(rel_dir_path) == "." else rel_dir_path.as_posix()
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _SKIP_IGNORE_WALK_DIRS
            and not (rel_dir == ".claude" and d == "worktrees")
        ]
        if ".gitignore" in filenames:
            gi_path = Path(dirpath) / ".gitignore"
            with contextlib.suppress(Exception):
                raw_lines = gi_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if any(ln.strip() and not ln.strip().startswith("#") for ln in raw_lines):
                    git_specs[rel_dir] = _build_gitignore_spec(raw_lines, pathspec_mod)
    return git_specs


def load_ignore_spec(project_root: str | Path) -> Any | None:
    """Load combined ignore spec from root/nested .gitignore and .rtfmignore in project root.

    .rtfmignore patterns take precedence over .gitignore. Only the [index] section
    (patterns before any [versions] header) of .rtfmignore is applied for index exclusion.
    Returns a matcher instance with .match_file() or None if no ignore files exist.
    """
    try:
        import pathspec
    except Exception:
        return None

    root_path = Path(project_root).resolve()
    if not root_path.is_dir():
        return None

    git_specs = _collect_git_specs(root_path, pathspec)
    rtfm_spec: Any | None = None
    ri_path = root_path / ".rtfmignore"
    if ri_path.is_file():
        with contextlib.suppress(Exception):
            ri_lines = _extract_rtfmignore_index_lines(
                ri_path.read_text(encoding="utf-8", errors="replace")
            )
            if ri_lines:
                rtfm_spec = _build_gitignore_spec(ri_lines, pathspec)

    if not git_specs and rtfm_spec is None:
        return None

    return _ProjectIgnoreSpec(git_specs=git_specs, rtfm_spec=rtfm_spec)


def is_path_ignored(rel_path: str | Path, spec: Any | None, is_dir: bool = False) -> bool:
    """Check if a relative path matches the ignore spec.

    If is_dir is True, checks both 'rel_path' and 'rel_path/' to match directory patterns.
    """
    norm_path = Path(rel_path).as_posix().removeprefix("./").lstrip("/")
    if not norm_path or norm_path == ".":
        return False
    if _is_excluded_subpath(norm_path.strip("/").split("/")):
        return True
    if spec is None:
        return False
    if is_dir:
        return bool(spec.match_file(norm_path) or spec.match_file(f"{norm_path}/"))
    return bool(spec.match_file(norm_path))


def is_allowed_source(path: str) -> bool:
    """Return True if the path should appear as a manuscript source span."""
    normalized = path.replace("\\", "/").lower()
    for pat in EXCLUDED_SOURCE_PATTERNS:
        if pat in normalized:
            return False
        if pat.endswith("/"):
            trimmed = pat[:-1]
            if normalized == trimmed or normalized.endswith("/" + trimmed):
                return False
    if "receipt" in normalized:
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
    # Procedural instructions & generic task verbs
    "expand",
    "explain",
    "avoid",
    "only",
    "other",
    "preserve",
    "claiming",
    "claim",
    "merge",
    "concise",
    "scope",
    "existing",
    "sections",
    "subsequent",
    "delivery",
    "detail",
    "describe",
    "summarize",
    "summary",
    "compare",
    "discuss",
    "conclude",
    "conclusion",
    "revise",
    "rewrite",
    "draft",
    "superiority",
    "gains",
    "transition",
    "chapter",
    "chapters",
}


def extract_keywords(text: str) -> list[str]:
    """Extract keywords from a string by filtering out stopwords."""
    words = text.lower().split()
    return [
        w.strip(".,;:")
        for w in words
        if w.strip(".,;:") not in KEYWORD_STOPWORDS and len(w.strip(".,;:")) > 3
    ]


def _parse_worktree_git_file(marker: Path) -> Path | None:
    """Resolve main repository root from a linked worktree `.git` pointer file."""
    try:
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
        if not text.startswith("gitdir:"):
            return None
        gitdir = Path(text.removeprefix("gitdir:").strip())
        if not gitdir.is_absolute():
            gitdir = marker.parent / gitdir
        common = (gitdir / "commondir").read_text(encoding="utf-8", errors="replace").strip()
        common_path = Path(common)
        resolved_common = (
            common_path if common_path.is_absolute() else gitdir / common_path
        ).resolve()
    except OSError:
        return None
    return resolved_common.parent if resolved_common.name == ".git" else None


def resolve_git_worktree_main_root(project_root: Path) -> Path | None:
    """Return the main working tree root when project_root lies in a linked git worktree.

    Follows the .git file ('gitdir: <main>/.git/worktrees/<name>') and its
    'commondir' pointer back to the main tree without invoking git.
    """
    try:
        here = project_root.resolve()
    except OSError:
        return None
    for d in (here, *here.parents):
        marker = d / ".git"
        with contextlib.suppress(OSError):
            if marker.is_dir():
                return None
            if marker.is_file():
                return _parse_worktree_git_file(marker)
    return None


def resolve_rtfm_db_path(project_root: Path) -> Path:
    """Resolve the RTFM library database path, prioritizing:
    1. RTFM_DB environment variable (absolute or relative to project_root)
    2. project_root / ".rtfm" / "library.db"
    3. project_root / "library.db"
    4. Main working tree's ".rtfm/library.db" or "library.db" when in a linked git worktree (RTFM >= 0.52.0)
    Fallback: project_root / ".rtfm" / "library.db"
    """
    env_db = os.environ.get("RTFM_DB")
    if env_db:
        db_path = Path(env_db)
        return db_path if db_path.is_absolute() else project_root / db_path

    primary_path = project_root / ".rtfm" / "library.db"
    candidates: list[Path] = [primary_path, project_root / "library.db"]
    main_root = resolve_git_worktree_main_root(project_root)
    if main_root is not None:
        candidates.extend([main_root / ".rtfm" / "library.db", main_root / "library.db"])

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return primary_path
