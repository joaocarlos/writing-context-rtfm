import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from writing_context_rtfm.schemas import RTFMResult


class RTFMAdapterError(Exception):
    """Exception raised for errors in the RTFM Adapter."""

    pass


class RTFMAdapter:
    """Wrapper around the RTFM CLI with optional direct SQLite fast-path."""

    def __init__(
        self, project_root: str | None = None, *, allow_cli_fallback: bool = True
    ) -> None:
        self.project_root = project_root
        self.allow_cli_fallback = allow_cli_fallback
        self.resolved_rtfm = "rtfm"

        resolved = shutil.which("rtfm")
        if resolved:
            self.resolved_rtfm = resolved
        else:
            # Fallback to looking in the same directory as the current Python executable
            python_dir = os.path.dirname(sys.executable)
            candidate = os.path.join(python_dir, "rtfm")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                self.resolved_rtfm = candidate

    def _run_command(self, cmd: list[str], capture_output: bool = True) -> str:
        """Run a CLI command and return its standard output."""
        if cmd[0] == "rtfm":
            cmd[0] = self.resolved_rtfm

        try:
            if capture_output:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, check=True, cwd=self.project_root
                )
                return result.stdout
            else:
                subprocess.run(cmd, text=True, check=True, cwd=self.project_root)
                return ""
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr if e.stderr else "See console output above."
            raise RTFMAdapterError(f"Command '{' '.join(cmd)}' failed with error: {err_msg}") from e
        except FileNotFoundError as e:
            raise RTFMAdapterError(
                f"RTFM CLI not found (tried to run {cmd[0]}). Is it installed?"
            ) from e

    def _direct_sqlite_search(
        self, query: str, *, corpus: str, limit: int = 10
    ) -> list[RTFMResult] | None:
        """Attempt fast in-process SQLite FTS query if RTFM database is locally available."""
        if not self.project_root:
            return None
        import sqlite3

        from writing_context_rtfm.utils import resolve_rtfm_db_path

        db_path = resolve_rtfm_db_path(Path(self.project_root))
        if not db_path.is_file():
            return None

        clean_terms = [t for t in re.sub(r"[^\w\s]", " ", query).split() if len(t) > 1]
        if not clean_terms:
            return None
        fts_query = " OR ".join(f'"{t}"' for t in clean_terms)

        conn = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            book_columns = {
                str(row["name"]) for row in cursor.execute("PRAGMA table_info(books)")
            }
            if "path" in book_columns:
                book_file_column = "path"
            elif "filename" in book_columns:
                book_file_column = "filename"
            else:
                raise sqlite3.DatabaseError(
                    "RTFM books table has neither a path nor filename column"
                )
            corpus_clause = " AND b.corpus = ?" if "corpus" in book_columns else ""
            sql = f"""
                SELECT c.id, c.content, c.line_start, c.line_end, c.chapter_title,
                       b.{book_file_column} AS book_file, b.title AS book_title,
                       bm25(chunks_fts) AS rank_score
                FROM chunks_fts
                JOIN chunks c ON chunks_fts.rowid = c.id
                JOIN books b ON c.book_id = b.id
                WHERE chunks_fts MATCH ?{corpus_clause}
                ORDER BY rank_score ASC
                LIMIT ?
            """
            parameters: tuple[object, ...] = (
                (fts_query, corpus, limit) if corpus_clause else (fts_query, limit)
            )
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
            raw_scores = [
                float(item["rank_score"]) if item["rank_score"] is not None else 0.0
                for item in rows
            ]
            strongest = max((-raw_score for raw_score in raw_scores), default=0.0)
            results = []
            for retrieval_rank, (item, raw_score) in enumerate(
                zip(rows, raw_scores, strict=True), start=1
            ):
                score = round((-raw_score) / strongest, 6) if strongest > 0 else 1.0
                results.append(
                    RTFMResult(
                        path=item["book_file"] or "",
                        line_start=item["line_start"],
                        line_end=item["line_end"],
                        snippet=item["content"],
                        score=score,
                        metadata={
                            "chapter_title": item["chapter_title"],
                            "book_title": item["book_title"],
                            "rank": retrieval_rank,
                            "retrieval_rank": retrieval_rank,
                            "bm25_raw": raw_score,
                        },
                    )
                )
            return results
        except Exception as exc:
            if not self.allow_cli_fallback:
                raise RTFMAdapterError(
                    f"Direct SQLite search failed for {db_path}: {exc}"
                ) from exc
            return None
        finally:
            if conn:
                with contextlib.suppress(Exception):
                    conn.close()

    def search(self, query: str, *, corpus: str, limit: int = 10) -> list[RTFMResult]:
        """Search indexed content using RTFM."""
        direct_results = self._direct_sqlite_search(query, corpus=corpus, limit=limit)
        if direct_results is not None:
            return direct_results
        if not self.allow_cli_fallback:
            raise RTFMAdapterError(
                "Direct SQLite search is unavailable and CLI fallback is disabled"
            )

        cmd = [
            "rtfm",
            "search",
            query,
            "--corpus",
            corpus,
            "--limit",
            str(limit),
            "--format",
            "json",
        ]
        try:
            output = self._run_command(cmd)
            data = json.loads(output)
            results = []
            for item in data.get("results", []):
                chunk = item.get("chunk", {})
                results.append(
                    RTFMResult(
                        path=chunk.get("book_file", ""),
                        line_start=chunk.get("line_start"),
                        line_end=chunk.get("line_end"),
                        snippet=chunk.get("content"),
                        score=item.get("score"),
                        metadata={
                            "chapter_title": chunk.get("chapter_title"),
                            "book_title": chunk.get("book_title"),
                            "rank": item.get("rank"),
                        },
                    )
                )
            return results
        except json.JSONDecodeError as e:
            raise RTFMAdapterError(f"Failed to parse JSON output from RTFM search: {e}") from e

    def context(self, path: str, line_start: int, line_end: int) -> str:
        """Retrieve context around a file span."""
        cmd = [
            "rtfm",
            "context",
            path,
            "--line-start",
            str(line_start),
            "--line-end",
            str(line_end),
        ]
        return self._run_command(cmd)

    def expand(self, result_id: str) -> str:
        """Expand selected results."""
        cmd = ["rtfm", "expand", result_id]
        return self._run_command(cmd)

    def sync(
        self, path: str | None = None, *, corpus: str | None = None, capture_output: bool = True
    ) -> None:
        """Trigger RTFM sync.

        If path and corpus are not provided, runs `rtfm sync` without arguments
        to let RTFM use configurations defined in .rtfm/config.json.
        """
        cmd = ["rtfm", "sync"]
        if path:
            cmd.append(path)
        if corpus:
            cmd.extend(["--corpus", corpus])
        self._run_command(cmd, capture_output=capture_output)
