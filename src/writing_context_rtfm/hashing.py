"""Hashing utilities for cache invalidation."""

import hashlib
from pathlib import Path
from typing import Any


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
    strict_budget: bool = False,
    role_budgets: dict[str, float] | None = None,
    output_mode: str = "prompt",
    retrieval_policy_version: int = 2,
    must_consider: list[str] | None = None,
) -> str:
    sorted_role_budgets = tuple(sorted(role_budgets.items())) if role_budgets else ()
    normalized_must_consider = tuple(
        item.strip() for item in (must_consider or []) if item.strip()
    )
    return stable_hash(
        task.strip(),
        target or "",
        str(token_budget),
        str(normalized_must_consider),
        task_type or "",
        str(line_start or ""),
        str(line_end or ""),
        pack_mode or "",
        str(strict_budget),
        str(sorted_role_budgets),
        output_mode,
        str(retrieval_policy_version),
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


def compute_retrieval_fingerprint(
    rtfm_db: Path,
    provider_fingerprints: list[str] | dict[str, Any] | None = None,
    schema_version: int = 2,
) -> str:
    """Compute combined retrieval fingerprint incorporating RTFM index, providers, and cache schema version."""
    rtfm_fp = compute_rtfm_fingerprint(rtfm_db)
    if isinstance(provider_fingerprints, dict):
        prov_parts = sorted(f"{k}:{v}" for k, v in provider_fingerprints.items() if v)
    elif provider_fingerprints:
        prov_parts = sorted(str(p) for p in provider_fingerprints if p)
    else:
        prov_parts = []
    return stable_hash(rtfm_fp, *prov_parts, f"schema_v{schema_version}")
