"""Stable retrieval engine protocol and interfaces.

This module acts as an Anti-Corruption Layer (ACL) between the MCP server / context
generators and the underlying retrieval engine (RTFM or any future alternative).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from writing_context_rtfm.schemas import RTFMResult


class RetrievalEngineError(Exception):
    """Base exception for all retrieval engine operations."""

    pass


@dataclass(frozen=True)
class RetrievalEngineHealth:
    """Health diagnostic report for a retrieval engine."""

    available: bool
    engine_name: str
    version: str | None
    db_path: Path | None
    db_exists: bool
    total_chunks: int = 0
    total_books: int = 0
    has_embeddings: bool = False
    details: str = ""


@runtime_checkable
class RetrievalEngine(Protocol):
    """Protocol defining the stable contract for manuscript retrieval engines."""

    def search(self, query: str, *, corpus: str, limit: int = 10) -> list[RTFMResult]:
        """Search indexed content and return normalized results ranked by relevance."""
        ...

    def context(self, path: str, line_start: int, line_end: int) -> str:
        """Retrieve surrounding context or exact content around a file span."""
        ...

    def expand(self, result_id: str) -> str:
        """Expand selected chunk results to full content."""
        ...

    def sync(
        self, path: str | None = None, *, corpus: str | None = None, capture_output: bool = True
    ) -> None:
        """Synchronize the manuscript files into the retrieval index."""
        ...

    def health_check(self) -> RetrievalEngineHealth:
        """Perform a quick health and readiness diagnostic on the engine."""
        ...

    def get_fingerprint(self) -> str:
        """Return a cache invalidation fingerprint for the retrieval database."""
        ...

    def get_db_path(self) -> Path | None:
        """Return the resolved filesystem path to the retrieval database, if applicable."""
        ...
