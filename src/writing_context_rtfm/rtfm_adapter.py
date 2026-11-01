import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from writing_context_rtfm.hashing import compute_rtfm_fingerprint
from writing_context_rtfm.retrieval import (
    RetrievalEngine,
    RetrievalEngineError,
    RetrievalEngineHealth,
)
from writing_context_rtfm.schemas import RTFMResult
from writing_context_rtfm.utils import is_allowed_source, resolve_rtfm_db_path


class RTFMAdapterError(RetrievalEngineError):
    """Exception raised for errors in the RTFM Adapter."""


class RTFMAdapter(RetrievalEngine):
    """Wrapper around the RTFM CLI with optional direct SQLite fast-path."""

    def __init__(self, project_root: str | None = None, *, allow_cli_fallback: bool = True) -> None:
        self.project_root = project_root
        self.allow_cli_fallback = allow_cli_fallback
        candidate = os.path.join(os.path.dirname(sys.executable), "rtfm")
        self.resolved_rtfm = (
            shutil.which("rtfm")
            or (candidate if os.path.isfile(candidate) and os.access(candidate, os.X_OK) else "rtfm")
        )

    def _run_command(self, cmd: list[str], capture_output: bool = True) -> str:
        """Run a CLI command and return its standard output."""
        if cmd[0] == "rtfm":
            cmd[0] = self.resolved_rtfm
        try:
            result = subprocess.run(
                cmd, capture_output=capture_output, text=True, check=True, cwd=self.project_root
            )
            return result.stdout if capture_output else ""
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr if e.stderr else "See console output above."
            raise RTFMAdapterError(f"Command '{' '.join(cmd)}' failed with error: {err_msg}") from e
        except FileNotFoundError as e:
            raise RTFMAdapterError(
                f"RTFM CLI not found (tried to run {cmd[0]}). Is it installed?"
            ) from e

    @staticmethod
    def _connect_readonly_sqlite(db_path: Path) -> sqlite3.Connection:
        """Open the RTFM SQLite database in read-only mode with busy timeout and immutable fallback."""
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10.0, check_same_thread=False)
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            return conn
        except sqlite3.OperationalError:
            return sqlite3.connect(
                f"{uri}&immutable=1", uri=True, timeout=10.0, check_same_thread=False
            )

    @staticmethod
    def _rewrite_worktree_path(raw_path: str, db_path: Path, project_root: str | None) -> str:
        """Rewrite paths from a main working tree's index into the linked worktree copy if applicable."""
        if not raw_path or not project_root:
            return raw_path
        db_root = db_path.parent.parent if db_path.parent.name == ".rtfm" else db_path.parent
        try:
            main_str = str(db_root.resolve()).rstrip("/")
            copy_str = str(Path(project_root).resolve()).rstrip("/")
        except OSError:
            return raw_path
        if main_str == copy_str:
            return raw_path
        if raw_path == main_str:
            return copy_str
        prefix = main_str + "/"
        return copy_str + "/" + raw_path[len(prefix) :] if raw_path.startswith(prefix) else raw_path

    @staticmethod
    def _row_to_source_card(row: Any) -> dict[str, Any]:
        """Convert a source_cards SQLite row (RTFM >= 0.47.0) into a normalized dict."""
        extra: dict[str, Any] = {}
        if row["extra"]:
            with contextlib.suppress(ValueError, TypeError):
                parsed = json.loads(row["extra"])
                if isinstance(parsed, dict):
                    extra = parsed
        rng = (
            [row["range_start"], row["range_end"]]
            if row["range_start"] is not None and row["range_end"] is not None
            else None
        )
        return {
            "file": row["filepath"],
            "corpus": row["corpus"],
            "author": row["author"],
            "title": row["title"],
            "nature": row["nature"],
            "language": row["language"],
            "year": row["year"],
            "edition": row["edition"],
            "translator": row["translator"],
            "range": rng,
            "note": row["note"],
            "attributed": bool(row["author"] and row["title"]),
            "extra": extra,
            "updated_at": row["updated_at"],
        }

    @classmethod
    def _load_source_cards_map(
        cls, cursor: Any, corpus: str | None = None, filepath: str | None = None
    ) -> dict[str, dict[str, Any]]:
        """Load source_cards rows keyed by filepath when the table exists."""
        has_table = (
            cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_cards'"
            ).fetchone()
            is not None
        )
        if not has_table:
            return {}
        cards_by_file: dict[str, dict[str, Any]] = {}
        with contextlib.suppress(Exception):
            clauses = []
            params: list[Any] = []
            if filepath:
                clauses.append("filepath = ?")
                params.append(filepath)
            if corpus:
                clauses.append("corpus = ?")
                params.append(corpus)
            where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
            order = " ORDER BY filepath" if corpus else " ORDER BY corpus, filepath"
            for crow in cursor.execute(f"SELECT * FROM source_cards{where}{order}", params):
                card = cls._row_to_source_card(crow)
                cards_by_file[str(card["file"])] = card
        return cards_by_file

    def _build_fts_results(
        self,
        rows: list[Any],
        cards_by_file: dict[str, dict[str, Any]],
        db_path: Path,
        limit: int,
    ) -> list[RTFMResult]:
        """Filter allowed manuscript rows, normalize BM25 scores, and attach source cards."""
        allowed_rows = [
            row for row in rows if is_allowed_source(str(row["book_file"] or ""))
        ][:limit]
        raw_scores = [
            float(item["rank_score"]) if item["rank_score"] is not None else 0.0
            for item in allowed_rows
        ]
        strongest = max((-raw_score for raw_score in raw_scores), default=0.0)
        results: list[RTFMResult] = []
        for retrieval_rank, (item, raw_score) in enumerate(
            zip(allowed_rows, raw_scores, strict=True), start=1
        ):
            score = round((-raw_score) / strongest, 6) if strongest > 0 else 1.0
            raw_book_file = item["book_file"] or ""
            rewritten_path = self._rewrite_worktree_path(raw_book_file, db_path, self.project_root)
            meta: dict[str, Any] = {
                "chapter_title": item["chapter_title"],
                "book_title": item["book_title"],
                "rank": retrieval_rank,
                "retrieval_rank": retrieval_rank,
                "bm25_raw": raw_score,
            }
            matched_card = cards_by_file.get(raw_book_file) or cards_by_file.get(rewritten_path)
            if matched_card is not None:
                meta["source_card"] = matched_card
                if matched_card.get("author"):
                    meta["author"] = matched_card["author"]
                meta["attributed"] = matched_card.get("attributed", False)
            results.append(
                RTFMResult(
                    path=rewritten_path,
                    line_start=item["line_start"],
                    line_end=item["line_end"],
                    snippet=item["content"],
                    score=score,
                    metadata=meta,
                )
            )
        return results

    def _direct_sqlite_search(
        self, query: str, *, corpus: str, limit: int = 10
    ) -> list[RTFMResult] | None:
        """Attempt fast in-process SQLite FTS query if RTFM database is locally available."""
        if not self.project_root:
            return None
        db_path = resolve_rtfm_db_path(Path(self.project_root))
        if not db_path.is_file():
            return None

        clean_terms = [t for t in re.sub(r"[^\w\s]", " ", query).split() if len(t) > 1]
        if not clean_terms:
            return None
        fts_query = " OR ".join(f'"{t}"' for t in clean_terms)
        fetch_limit = max(limit * 3, limit + 10)

        conn = None
        try:
            conn = self._connect_readonly_sqlite(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            book_columns = {str(row["name"]) for row in cursor.execute("PRAGMA table_info(books)")}
            if "path" not in book_columns and "filename" not in book_columns:
                raise sqlite3.DatabaseError(
                    "RTFM books table has neither a path nor filename column"
                )
            book_file_col = "path" if "path" in book_columns else "filename"
            corpus_clause = " AND b.corpus = ?" if "corpus" in book_columns else ""
            sql = (
                "SELECT c.id, c.content, c.line_start, c.line_end, c.chapter_title, "
                f"b.{book_file_col} AS book_file, b.title AS book_title, "
                "bm25(chunks_fts) AS rank_score "
                "FROM chunks_fts JOIN chunks c ON chunks_fts.rowid = c.id "
                f"JOIN books b ON c.book_id = b.id WHERE chunks_fts MATCH ?{corpus_clause} "
                "ORDER BY rank_score ASC LIMIT ?"
            )
            params: tuple[object, ...] = (
                (fts_query, corpus, fetch_limit) if corpus_clause else (fts_query, fetch_limit)
            )
            rows = cursor.execute(sql, params).fetchall()
            cards_by_file = self._load_source_cards_map(cursor, corpus) if rows else {}
            return self._build_fts_results(rows, cards_by_file, db_path, limit)
        except Exception as exc:
            if not self.allow_cli_fallback:
                raise RTFMAdapterError(f"Direct SQLite search failed for {db_path}: {exc}") from exc
            return None
        finally:
            if conn:
                with contextlib.suppress(Exception):
                    conn.close()

    def search(self, query: str, *, corpus: str, limit: int = 10) -> list[RTFMResult]:
        """Search indexed content using RTFM."""
        if not query or not query.strip():
            return []

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
            data = json.loads(self._run_command(cmd))
            return [
                RTFMResult(
                    path=item.get("chunk", {}).get("book_file", ""),
                    line_start=item.get("chunk", {}).get("line_start"),
                    line_end=item.get("chunk", {}).get("line_end"),
                    snippet=item.get("chunk", {}).get("content"),
                    score=item.get("score"),
                    metadata={
                        "chapter_title": item.get("chunk", {}).get("chapter_title"),
                        "book_title": item.get("chunk", {}).get("book_title"),
                        "rank": item.get("rank"),
                    },
                )
                for item in data.get("results", [])
            ]
        except json.JSONDecodeError as e:
            raise RTFMAdapterError(f"Failed to parse JSON output from RTFM search: {e}") from e

    def context(self, path: str, line_start: int, line_end: int) -> str:
        """Retrieve context around a file span."""
        return self._run_command(
            ["rtfm", "context", path, "--line-start", str(line_start), "--line-end", str(line_end)]
        )

    def expand(self, result_id: str) -> str:
        """Expand selected results."""
        return self._run_command(["rtfm", "expand", result_id])

    def sync(
        self, path: str | None = None, *, corpus: str | None = None, capture_output: bool = True
    ) -> None:
        """Trigger RTFM sync using .rtfm/config.json defaults when path/corpus are omitted."""
        cmd = ["rtfm", "sync"]
        if path:
            cmd.append(path)
        if corpus:
            cmd.extend(["--corpus", corpus])
        self._run_command(cmd, capture_output=capture_output)

    def get_db_path(self) -> Path | None:
        """Return resolved path to the RTFM SQLite database."""
        return resolve_rtfm_db_path(Path(self.project_root)) if self.project_root else None

    def get_fingerprint(self) -> str:
        """Return database invalidation fingerprint."""
        db_path = self.get_db_path()
        return compute_rtfm_fingerprint(db_path) if db_path and db_path.is_file() else "no-rtfm-db"

    @staticmethod
    def list_indexed_chunks_from_db(
        db_path: Path | str, chunk_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Read indexed chunks from an RTFM SQLite database across schema variants."""
        resolved = Path(db_path)
        if not resolved.is_file() or (chunk_ids is not None and not chunk_ids):
            return []
        conn: sqlite3.Connection | None = None
        try:
            conn = RTFMAdapter._connect_readonly_sqlite(resolved)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            book_cols = {str(r["name"]) for r in cursor.execute("PRAGMA table_info(books)")}
            book_file_col = "path" if "path" in book_cols else "filename"
            chunk_cols = {str(r["name"]) for r in cursor.execute("PRAGMA table_info(chunks)")}
            chunk_id_col = "chunk_id" if "chunk_id" in chunk_cols else "id"

            sql = (
                f"SELECT c.{chunk_id_col} AS chunk_id, c.content, "
                f"b.{book_file_col} AS file_path, c.line_start, c.line_end "
                "FROM chunks c JOIN books b ON c.book_id = b.id"
            )
            params: list[Any] = []
            if chunk_ids is not None:
                placeholders = ",".join("?" for _ in chunk_ids)
                sql += f" WHERE CAST(c.{chunk_id_col} AS TEXT) IN ({placeholders})"
                params.extend(str(cid) for cid in chunk_ids)

            return [
                {
                    "chunk_id": str(row["chunk_id"]),
                    "content": str(row["content"] or ""),
                    "file_path": str(row["file_path"] or ""),
                    "line_start": row["line_start"],
                    "line_end": row["line_end"],
                }
                for row in cursor.execute(sql, params).fetchall()
            ]
        except Exception:
            return []
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()

    def get_chunks_by_ids(self, chunk_ids: list[str]) -> dict[str, RTFMResult]:
        """Retrieve indexed chunks keyed by chunk ID via the retrieval boundary."""
        db_path = self.get_db_path()
        if not db_path:
            return {}
        return {
            row["chunk_id"]: RTFMResult(
                path=self._rewrite_worktree_path(row["file_path"], db_path, self.project_root),
                line_start=row["line_start"],
                line_end=row["line_end"],
                snippet=row["content"],
                score=1.0,
                metadata={"chunk_id": row["chunk_id"]},
            )
            for row in self.list_indexed_chunks_from_db(db_path, chunk_ids=chunk_ids)
        }

    def list_source_cards(
        self, *, corpus: str | None = None, filepath: str | None = None
    ) -> list[dict[str, Any]]:
        """List bibliographic source cards (RTFM >= 0.47.0 `rtfm cite`) from the RTFM index."""
        db_path = self.get_db_path()
        if not db_path or not db_path.is_file():
            return []
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect_readonly_sqlite(db_path)
            conn.row_factory = sqlite3.Row
            cards_map = self._load_source_cards_map(conn.cursor(), corpus=corpus, filepath=filepath)
            return [
                {
                    **card,
                    "file": self._rewrite_worktree_path(
                        str(card["file"]), db_path, self.project_root
                    ),
                }
                for card in cards_map.values()
            ]
        except Exception:
            return []
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()

    def get_source_card(
        self, filepath: str, *, corpus: str | None = None
    ) -> dict[str, Any] | None:
        """Return the bibliographic card for a specific file path, if recorded in RTFM."""
        if not filepath:
            return None
        direct = self.list_source_cards(corpus=corpus, filepath=filepath)
        if direct:
            return direct[0]
        return next(
            (c for c in self.list_source_cards(corpus=corpus) if c.get("file") == filepath), None
        )

    def _inspect_db_health(self, db_path: Path) -> tuple[int, int, bool, bool, str]:
        """Inspect chunks, books, FTS, and vector embedding availability in the RTFM SQLite DB."""
        conn = None
        try:
            conn = self._connect_readonly_sqlite(db_path)
            cursor = conn.cursor()
            total_chunks = int(cursor.execute("SELECT count(*) FROM chunks").fetchone()[0])
            total_books = int(cursor.execute("SELECT count(*) FROM books").fetchone()[0])
            tables = {
                row[0]
                for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            has_fts = "chunks_fts" in tables
            has_embeddings = bool({"embeddings", "chunk_embeddings"} & tables) or bool(
                {"embedding", "vector"}
                & {row[1] for row in cursor.execute("PRAGMA table_info(chunks)")}
            )
            return (
                total_chunks,
                total_books,
                has_embeddings,
                has_fts,
                f"DB ready ({total_chunks} chunks, {total_books} books)",
            )
        except Exception as e:
            return 0, 0, False, False, f"DB read error: {e}"
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()

    def health_check(self) -> RetrievalEngineHealth:
        """Perform a quick health and readiness diagnostic on RTFM."""
        db_path = self.get_db_path()
        db_exists = db_path.is_file() if db_path else False
        cli_available = bool(
            shutil.which(self.resolved_rtfm)
            or (os.path.isabs(self.resolved_rtfm) and os.path.exists(self.resolved_rtfm))
        )

        version = None
        for mod_name in ("rtfm", "rtfm_ai"):
            with contextlib.suppress(ImportError):
                version = getattr(__import__(mod_name), "__version__", None)
                if version:
                    break

        if db_exists and db_path:
            total_chunks, total_books, has_embeddings, has_fts, detail = self._inspect_db_health(
                db_path
            )
        else:
            total_chunks, total_books, has_embeddings, has_fts, detail = (
                0,
                0,
                False,
                False,
                "Database not found",
            )

        return RetrievalEngineHealth(
            available=(db_exists and total_chunks > 0) or cli_available,
            engine_name="rtfm",
            version=version,
            db_path=db_path,
            db_exists=db_exists,
            total_chunks=total_chunks,
            total_books=total_books,
            has_embeddings=has_embeddings,
            has_fts=has_fts,
            details=detail,
        )
