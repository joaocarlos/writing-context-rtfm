"""Hashing utilities for cache invalidation."""
import hashlib
from typing import Optional

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

