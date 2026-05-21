"""Hashing utilities for cache invalidation."""
import hashlib
from typing import Optional

from pathlib import Path

def stable_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()

def compute_task_hash(task: str, target: Optional[str], token_budget: int,
                      task_type: Optional[str] = None,
                      line_start: Optional[int] = None,
                      line_end: Optional[int] = None,
                      pack_mode: Optional[str] = None) -> str:
    return stable_hash(
        task.strip(),
        target or "",
        str(token_budget),
        task_type or "",
        str(line_start or ""),
        str(line_end or ""),
        pack_mode or ""
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

