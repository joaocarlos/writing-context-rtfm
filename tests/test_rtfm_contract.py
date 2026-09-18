"""Contract tests for RTFM retrieval engine dependency and RetrievalEngine interface.

These tests assert that:
1. RTFMAdapter rigorously adheres to the RetrievalEngine protocol (Anti-Corruption Layer).
2. The RTFM database schema satisfies the assumptions made by direct SQLite retrieval.
3. The RTFM CLI satisfies the flag and output format expectations.
4. Failure states (corrupted DB, missing CLI, malformed output) are strictly isolated
   within RetrievalEngineError / RTFMAdapterError without crashing callers unhandled.
"""

import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from writing_context_rtfm.retrieval import (
    RetrievalEngine,
    RetrievalEngineError,
    RetrievalEngineHealth,
)
from writing_context_rtfm.rtfm_adapter import RTFMAdapter, RTFMAdapterError
from writing_context_rtfm.schemas import RTFMResult


class TestRetrievalEngineProtocolContract:
    """Verify that RTFMAdapter satisfies the formal RetrievalEngine protocol."""

    def test_adapter_satisfies_protocol_runtime(self, tmp_path: Path):
        adapter = RTFMAdapter(project_root=str(tmp_path))
        assert isinstance(adapter, RetrievalEngine)

    def test_custom_engine_satisfies_protocol(self):
        class MockEngine:
            def search(
                self, query: str, *, corpus: str, limit: int = 10
            ) -> list[RTFMResult]:
                return []

            def context(self, path: str, line_start: int, line_end: int) -> str:
                return ""

            def expand(self, result_id: str) -> str:
                return ""

            def sync(
                self,
                path: str | None = None,
                *,
                corpus: str | None = None,
                capture_output: bool = True,
            ) -> None:
                pass

            def health_check(self) -> RetrievalEngineHealth:
                return RetrievalEngineHealth(
                    available=True,
                    engine_name="mock",
                    version="1.0.0",
                    db_path=None,
                    db_exists=False,
                )

            def get_fingerprint(self) -> str:
                return "mock-hash"

            def get_db_path(self) -> Path | None:
                return None

        engine = MockEngine()
        assert isinstance(engine, RetrievalEngine)

    def test_error_hierarchy(self):
        """RTFMAdapterError must inherit from RetrievalEngineError."""
        assert issubclass(RTFMAdapterError, RetrievalEngineError)
        err = RTFMAdapterError("Contract violation")
        assert isinstance(err, RetrievalEngineError)


class TestRTFMDatabaseSchemaContract:
    """Verify expectations regarding the SQLite database created by RTFM."""

    @pytest.fixture
    def mock_rtfm_db(self, tmp_path: Path) -> Path:
        rtfm_dir = tmp_path / ".rtfm"
        rtfm_dir.mkdir(parents=True, exist_ok=True)
        db_path = rtfm_dir / "library.db"

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Contractual table: books
        cursor.execute(
            """
            CREATE TABLE books (
                id INTEGER PRIMARY KEY,
                title TEXT,
                path TEXT,
                corpus TEXT
            )
            """
        )

        # Contractual table: chunks
        cursor.execute(
            """
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                book_id INTEGER,
                content TEXT,
                line_start INTEGER,
                line_end INTEGER,
                chapter_title TEXT,
                FOREIGN KEY(book_id) REFERENCES books(id)
            )
            """
        )

        # Contractual FTS table: chunks_fts
        cursor.execute(
            """
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                content,
                content='chunks',
                content_rowid='id'
            )
            """
        )

        # Populate sample data
        cursor.execute(
            "INSERT INTO books (id, title, path, corpus) VALUES (1, 'Paper Title', 'sections/intro.tex', 'default')"
        )
        cursor.execute(
            """
            INSERT INTO chunks (id, book_id, content, line_start, line_end, chapter_title)
            VALUES (1, 1, 'Quantum optimization of context tokens in distributed nodes', 1, 10, 'Introduction')
            """
        )
        cursor.execute(
            "INSERT INTO chunks_fts(rowid, content) VALUES (1, 'Quantum optimization of context tokens in distributed nodes')"
        )

        conn.commit()
        conn.close()
        return db_path

    def test_sqlite_search_schema_compatibility(self, tmp_path: Path, mock_rtfm_db: Path):
        adapter = RTFMAdapter(project_root=str(tmp_path), allow_cli_fallback=False)
        results = adapter.search("Quantum optimization", corpus="default", limit=5)

        assert len(results) == 1
        res = results[0]
        assert isinstance(res, RTFMResult)
        assert res.path == "sections/intro.tex"
        assert res.line_start == 1
        assert res.line_end == 10
        assert "Quantum optimization" in res.snippet
        assert 0.0 <= res.score <= 1.0
        assert res.metadata["chapter_title"] == "Introduction"
        assert res.metadata["book_title"] == "Paper Title"

    def test_sqlite_filename_fallback_schema(self, tmp_path: Path):
        """Older or variant RTFM schemas might use 'filename' instead of 'path'."""
        rtfm_dir = tmp_path / ".rtfm"
        rtfm_dir.mkdir(parents=True, exist_ok=True)
        db_path = rtfm_dir / "library.db"

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, filename TEXT)")
        cursor.execute(
            "CREATE TABLE chunks (id INTEGER PRIMARY KEY, book_id INTEGER, content TEXT, line_start INTEGER, line_end INTEGER, chapter_title TEXT)"
        )
        cursor.execute(
            "CREATE VIRTUAL TABLE chunks_fts USING fts5(content, content='chunks', content_rowid='id')"
        )
        cursor.execute("INSERT INTO books (id, title, filename) VALUES (1, 'Alt Book', 'paper.tex')")
        cursor.execute(
            "INSERT INTO chunks VALUES (1, 1, 'Algorithmic context selection', 5, 15, 'Methods')"
        )
        cursor.execute("INSERT INTO chunks_fts(rowid, content) VALUES (1, 'Algorithmic context selection')")
        conn.commit()
        conn.close()

        adapter = RTFMAdapter(project_root=str(tmp_path), allow_cli_fallback=False)
        results = adapter.search("Algorithmic selection", corpus="default", limit=5)
        assert len(results) == 1
        assert results[0].path == "paper.tex"

    def test_empty_or_whitespace_query_does_not_crash(self, tmp_path: Path, mock_rtfm_db: Path):
        adapter = RTFMAdapter(project_root=str(tmp_path), allow_cli_fallback=False)
        results = adapter.search("   ", corpus="default", limit=5)
        assert results == []


class TestFailureIsolationContract:
    """Verify that retrieval failures are strictly isolated and typed."""

    def test_corrupt_database_raises_isolated_adapter_error(self, tmp_path: Path):
        rtfm_dir = tmp_path / ".rtfm"
        rtfm_dir.mkdir(parents=True, exist_ok=True)
        corrupt_db = rtfm_dir / "library.db"
        corrupt_db.write_text("NOT A SQLITE FILE")

        adapter = RTFMAdapter(project_root=str(tmp_path), allow_cli_fallback=False)
        with pytest.raises(RTFMAdapterError) as exc_info:
            adapter.search("test query", corpus="default")

        assert "Direct SQLite search failed" in str(exc_info.value)
        assert isinstance(exc_info.value, RetrievalEngineError)

    def test_missing_cli_raises_isolated_adapter_error(self, tmp_path: Path):
        adapter = RTFMAdapter(project_root=str(tmp_path), allow_cli_fallback=True)
        adapter.resolved_rtfm = "/nonexistent/path/to/rtfm"

        with pytest.raises(RTFMAdapterError) as exc_info:
            adapter.search("test query", corpus="default")

        assert "RTFM CLI not found" in str(exc_info.value)

    def test_cli_error_output_wrapped_in_adapter_error(self, tmp_path: Path):
        adapter = RTFMAdapter(project_root=str(tmp_path))

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=["rtfm", "search"], stderr="Corrupt index fatal error"
            )
            with pytest.raises(RTFMAdapterError) as exc_info:
                adapter._run_command(["rtfm", "search"])

            assert "Corrupt index fatal error" in str(exc_info.value)

    def test_malformed_cli_json_raises_adapter_error(self, tmp_path: Path):
        adapter = RTFMAdapter(project_root=str(tmp_path))

        with patch.object(adapter, "_direct_sqlite_search", return_value=None):
            with patch.object(adapter, "_run_command", return_value="Invalid Non-JSON Output"):
                with pytest.raises(RTFMAdapterError) as exc_info:
                    adapter.search("test", corpus="default")
                assert "Failed to parse JSON output" in str(exc_info.value)


class TestHealthCheckContract:
    """Verify health_check diagnostic behavior."""

    def test_health_check_uninitialized_project(self, tmp_path: Path):
        adapter = RTFMAdapter(project_root=str(tmp_path))
        health = adapter.health_check()

        assert isinstance(health, RetrievalEngineHealth)
        assert health.engine_name == "rtfm"
        assert not health.db_exists
        assert health.total_chunks == 0
        assert "Database not found" in health.details

    def test_health_check_initialized_project(self, tmp_path: Path):
        rtfm_dir = tmp_path / ".rtfm"
        rtfm_dir.mkdir(parents=True, exist_ok=True)
        db_path = rtfm_dir / "library.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE chunks (id INTEGER PRIMARY KEY, book_id INTEGER, content TEXT, embedding BLOB)"
        )
        conn.execute("INSERT INTO books VALUES (1)")
        conn.execute("INSERT INTO chunks VALUES (1, 1, 'Chunk text', X'0001')")
        conn.commit()
        conn.close()

        adapter = RTFMAdapter(project_root=str(tmp_path))
        health = adapter.health_check()

        assert health.db_exists
        assert health.total_chunks == 1
        assert health.total_books == 1
        assert health.has_embeddings
        assert health.available
        assert "DB ready (1 chunks, 1 books)" in health.details
