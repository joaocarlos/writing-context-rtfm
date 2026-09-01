import json
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from writing_context_rtfm.config import RTFMConfig
from writing_context_rtfm.rtfm_adapter import RTFMAdapter, RTFMAdapterError
from writing_context_rtfm.schemas import RTFMResult


class TestRTFMAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = RTFMAdapter()
        self.adapter.resolved_rtfm = "rtfm"

    def test_auto_sync_is_disabled_by_default(self):
        self.assertFalse(RTFMConfig().sync_before_pack)

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_search_success(self, mock_run):
        mock_output = {
            "results": [
                {
                    "rank": 1,
                    "score": 4.5,
                    "chunk": {
                        "book_file": "docs/architecture.md",
                        "line_start": 10,
                        "line_end": 20,
                        "content": "This is a test snippet.",
                        "chapter_title": "Architecture",
                        "book_title": "Test Book",
                    },
                }
            ]
        }

        mock_process = MagicMock()
        mock_process.stdout = json.dumps(mock_output)
        mock_run.return_value = mock_process

        results = self.adapter.search("test query", corpus="test_corpus", limit=1)

        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], RTFMResult)
        self.assertEqual(results[0].path, "docs/architecture.md")
        self.assertEqual(results[0].score, 4.5)
        self.assertEqual(results[0].snippet, "This is a test snippet.")

        mock_run.assert_called_once_with(
            [
                "rtfm",
                "search",
                "test query",
                "--corpus",
                "test_corpus",
                "--limit",
                "1",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            check=True,
            cwd=None,
        )

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_search_command_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()

        with self.assertRaises(RTFMAdapterError) as context:
            self.adapter.search("test", corpus="corpus", limit=5)

        self.assertIn("RTFM CLI not found", str(context.exception))

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_search_called_process_error(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["rtfm"], stderr="Some error"
        )

        with self.assertRaises(RTFMAdapterError) as context:
            self.adapter.search("test", corpus="corpus", limit=5)

        self.assertIn("Some error", str(context.exception))

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_search_invalid_json(self, mock_run):
        mock_process = MagicMock()
        mock_process.stdout = "Invalid JSON output"
        mock_run.return_value = mock_process

        with self.assertRaises(RTFMAdapterError) as context:
            self.adapter.search("test", corpus="corpus", limit=5)

        self.assertIn("Failed to parse JSON output", str(context.exception))

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_sync_success(self, mock_run):
        mock_run.return_value = MagicMock()

        result = self.adapter.sync("/project", corpus="test_corpus")
        self.assertIsNone(result)

        mock_run.assert_called_once_with(
            ["rtfm", "sync", "/project", "--corpus", "test_corpus"],
            capture_output=True,
            text=True,
            check=True,
            cwd=None,
        )

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_sync_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["rtfm"], stderr="Sync failed"
        )

        with self.assertRaises(RTFMAdapterError):
            self.adapter.sync("/project", corpus="test_corpus")

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_context_retrieval(self, mock_run):
        mock_process = MagicMock()
        mock_process.stdout = "Retrieved context lines."
        mock_run.return_value = mock_process

        result = self.adapter.context("path/to/file.md", 1, 10)
        self.assertEqual(result, "Retrieved context lines.")

        mock_run.assert_called_once_with(
            ["rtfm", "context", "path/to/file.md", "--line-start", "1", "--line-end", "10"],
            capture_output=True,
            text=True,
            check=True,
            cwd=None,
        )

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_expand(self, mock_run):
        mock_process = MagicMock()
        mock_process.stdout = "Expanded result content."
        mock_run.return_value = mock_process

        result = self.adapter.expand("res_123")
        self.assertEqual(result, "Expanded result content.")

        mock_run.assert_called_once_with(
            ["rtfm", "expand", "res_123"], capture_output=True, text=True, check=True, cwd=None
        )

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_custom_project_root(self, mock_run):
        mock_run.return_value = MagicMock()
        adapter = RTFMAdapter(project_root="/my/custom/root")
        adapter.resolved_rtfm = "rtfm"
        adapter.sync("/project", corpus="test_corpus")

        mock_run.assert_called_once_with(
            ["rtfm", "sync", "/project", "--corpus", "test_corpus"],
            capture_output=True,
            text=True,
            check=True,
            cwd="/my/custom/root",
        )

    @patch("writing_context_rtfm.rtfm_adapter.subprocess.run")
    def test_sync_no_arguments(self, mock_run):
        mock_run.return_value = MagicMock()
        self.adapter.sync()

        mock_run.assert_called_once_with(
            ["rtfm", "sync"], capture_output=True, text=True, check=True, cwd=None
        )


import os
import shutil
import tempfile
from pathlib import Path

from writing_context_rtfm.utils import resolve_rtfm_db_path


class TestResolveRtfmDbPath(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.project_root = Path(self.temp_dir)
        self.old_env = os.environ.get("RTFM_DB")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)
        if self.old_env is not None:
            os.environ["RTFM_DB"] = self.old_env
        elif "RTFM_DB" in os.environ:
            del os.environ["RTFM_DB"]

    def test_priority_env_var_absolute(self):
        os.environ["RTFM_DB"] = "/custom/path/library.db"
        res = resolve_rtfm_db_path(self.project_root)
        self.assertEqual(res, Path("/custom/path/library.db"))

    def test_priority_env_var_relative(self):
        os.environ["RTFM_DB"] = "custom/path/library.db"
        res = resolve_rtfm_db_path(self.project_root)
        self.assertEqual(res, self.project_root / "custom/path/library.db")

    def test_priority_primary_exists(self):
        if "RTFM_DB" in os.environ:
            del os.environ["RTFM_DB"]
        # Create primary .rtfm/library.db
        rtfm_dir = self.project_root / ".rtfm"
        rtfm_dir.mkdir()
        db_file = rtfm_dir / "library.db"
        db_file.touch()

        res = resolve_rtfm_db_path(self.project_root)
        self.assertEqual(res, db_file)

    def test_priority_fallback_exists(self):
        if "RTFM_DB" in os.environ:
            del os.environ["RTFM_DB"]
        # Create fallback library.db in root
        db_file = self.project_root / "library.db"
        db_file.touch()

        res = resolve_rtfm_db_path(self.project_root)
        self.assertEqual(res, db_file)

    def test_priority_default_when_none_exists(self):
        if "RTFM_DB" in os.environ:
            del os.environ["RTFM_DB"]
        res = resolve_rtfm_db_path(self.project_root)
        self.assertEqual(res, self.project_root / ".rtfm" / "library.db")

    def test_direct_sqlite_search(self):
        import sqlite3

        db_path = self.project_root / "library.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT, title TEXT);")
        conn.execute(
            "CREATE TABLE chunks (id INTEGER PRIMARY KEY, book_id INTEGER, content TEXT, line_start INTEGER, line_end INTEGER, chapter_title TEXT);"
        )
        conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(content);")
        conn.execute("INSERT INTO books VALUES (1, 'intro.tex', 'Intro Book');")
        conn.execute(
            "INSERT INTO chunks VALUES (1, 1, 'Deep learning quantization techniques', 1, 10, 'Quantization');"
        )
        conn.execute(
            "INSERT INTO chunks_fts (rowid, content) VALUES (1, 'Deep learning quantization techniques');"
        )
        conn.commit()
        conn.close()

        adapter = RTFMAdapter(project_root=str(self.project_root))
        results = adapter.search("quantization", corpus="default", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].path, "intro.tex")
        self.assertIn("quantization", results[0].snippet.lower())

    def test_direct_sqlite_search_preserves_relative_bm25_scores(self):
        import sqlite3

        db_path = self.project_root / "library.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, filename TEXT, title TEXT, corpus TEXT);"
        )
        conn.execute(
            "CREATE TABLE chunks (id INTEGER PRIMARY KEY, book_id INTEGER, content TEXT, "
            "line_start INTEGER, line_end INTEGER, chapter_title TEXT);"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE chunks_fts USING fts5(content, tokenize='porter unicode61');"
        )
        rows = [
            (1, "strong.tex", "Strong", "routing mechanism routing mechanism routing mechanism"),
            (2, "medium.tex", "Medium", "routing mechanism"),
            (3, "weak.tex", "Weak", "routing appears once in otherwise unrelated material"),
        ]
        for row_id, filename, title, content in rows:
            conn.execute(
                "INSERT INTO books VALUES (?, ?, ?, 'manuscript')",
                (row_id, filename, title),
            )
            conn.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, 1, 2, 'Routing')",
                (row_id, row_id, content),
            )
            conn.execute(
                "INSERT INTO chunks_fts(rowid, content) VALUES (?, ?)",
                (row_id, content),
            )
        conn.commit()
        conn.close()

        adapter = RTFMAdapter(project_root=str(self.project_root), allow_cli_fallback=False)
        results = adapter.search("routing mechanism", corpus="manuscript", limit=3)

        self.assertEqual([item.path for item in results], ["strong.tex", "medium.tex", "weak.tex"])
        self.assertEqual(results[0].score, 1.0)
        self.assertGreater(results[0].score, results[1].score)
        self.assertGreater(results[1].score, results[2].score)
        self.assertEqual(
            [item.metadata["retrieval_rank"] for item in results],
            [1, 2, 3],
        )
        self.assertTrue(all(item.metadata["bm25_raw"] < 0 for item in results))

    def test_direct_sqlite_search_uses_exact_tokens_not_prefixes(self):
        import sqlite3

        db_path = self.project_root / "library.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, filename TEXT, title TEXT, corpus TEXT);"
        )
        conn.execute(
            "CREATE TABLE chunks (id INTEGER PRIMARY KEY, book_id INTEGER, content TEXT, "
            "line_start INTEGER, line_end INTEGER, chapter_title TEXT);"
        )
        conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(content);")
        for row_id, filename, content in (
            (1, "exact.tex", "cat"),
            (2, "prefix.tex", "catalog"),
        ):
            conn.execute(
                "INSERT INTO books VALUES (?, ?, ?, 'manuscript')",
                (row_id, filename, filename),
            )
            conn.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, 1, 1, 'Terms')",
                (row_id, row_id, content),
            )
            conn.execute(
                "INSERT INTO chunks_fts(rowid, content) VALUES (?, ?)",
                (row_id, content),
            )
        conn.commit()
        conn.close()

        adapter = RTFMAdapter(project_root=str(self.project_root), allow_cli_fallback=False)
        results = adapter.search("cat", corpus="manuscript", limit=5)

        self.assertEqual([item.path for item in results], ["exact.tex"])

    def test_direct_sqlite_search_rtfm_028_schema_and_corpus(self):
        import sqlite3

        db_path = self.project_root / "library.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, filename TEXT, title TEXT, corpus TEXT);"
        )
        conn.execute(
            "CREATE TABLE chunks (id INTEGER PRIMARY KEY, book_id INTEGER, content TEXT, line_start INTEGER, line_end INTEGER, chapter_title TEXT);"
        )
        conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(content);")
        conn.execute("INSERT INTO books VALUES (1, 'intro.tex', 'Intro', 'manuscript');")
        conn.execute("INSERT INTO books VALUES (2, 'other.tex', 'Other', 'other');")
        conn.execute("INSERT INTO chunks VALUES (1, 1, 'routing mechanism', 1, 2, 'Routing');")
        conn.execute("INSERT INTO chunks VALUES (2, 2, 'routing elsewhere', 3, 4, 'Other');")
        conn.execute("INSERT INTO chunks_fts(rowid, content) VALUES (1, 'routing mechanism');")
        conn.execute("INSERT INTO chunks_fts(rowid, content) VALUES (2, 'routing elsewhere');")
        conn.commit()
        conn.close()

        adapter = RTFMAdapter(project_root=str(self.project_root), allow_cli_fallback=False)
        results = adapter.search("routing", corpus="manuscript", limit=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].path, "intro.tex")

    @patch.object(RTFMAdapter, "_run_command")
    def test_strict_local_search_never_uses_cli(self, run_command):
        adapter = RTFMAdapter(project_root=str(self.project_root), allow_cli_fallback=False)
        with self.assertRaises(RTFMAdapterError):
            adapter.search("routing", corpus="manuscript", limit=1)
        run_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
