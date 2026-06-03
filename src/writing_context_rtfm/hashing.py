"""Hashing utilities for cache invalidation."""

import hashlib
from pathlib import Path


def stable_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def compute_task_hash(
    task: str,
    target: str | None,
    token_budget: int,
    task_type: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    pack_mode: str | None = None,
) -> str:
    return stable_hash(
        task.strip(),
        target or "",
        str(token_budget),
        task_type or "",
        str(line_start or ""),
        str(line_end or ""),
        pack_mode or "",
    )


def compute_rtfm_fingerprint(db_path: Path) -> str:
    """Compute real RTFM database fingerprint based on mtime and size."""
    if db_path.exists():
        try:
            stat = db_path.stat()
            return stable_hash(str(stat.st_mtime), str(stat.st_size))
        except OSError:
            return "no-rtfm-db"
    return "no-rtfm-db"
