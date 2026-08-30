"""SQLite storage for extension data."""

import contextlib
import hashlib
import json
import os
import sqlite3
import zlib
from typing import Any

SCHEMA_VERSION = 1


class ExtensionStore:
    def __init__(self, db_path: str = ".writing-context/context_cache.sqlite"):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "ExtensionStore":
        self._connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            if self.db_path != ":memory:":
                os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.row_factory = sqlite3.Row
            self._ensure_schema(self._conn)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None

    def init_db(self) -> None:
        with self._connect() as conn:
            self._ensure_schema(conn)

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS context_pack_runs (
            run_id TEXT PRIMARY KEY,
            task_hash TEXT NOT NULL,
            task TEXT NOT NULL,
            target TEXT,
            corpus TEXT,
            token_budget INTEGER NOT NULL,
            config_hash TEXT,
            section_cards_hash TEXT,
            rtfm_index_fingerprint TEXT,
            retrieval_fingerprint TEXT,
            provider_fingerprint TEXT,
            context_fingerprint TEXT,
            extension_version TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Migration for older databases: add fingerprint columns if missing
        for col in ("retrieval_fingerprint", "provider_fingerprint", "context_fingerprint"):
            with contextlib.suppress(sqlite3.OperationalError):
                cursor.execute(f"ALTER TABLE context_pack_runs ADD COLUMN {col} TEXT;")

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_context_pack_runs_task_hash
        ON context_pack_runs(task_hash);
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS context_pack_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            path TEXT NOT NULL,
            line_start INTEGER,
            line_end INTEGER,
            score REAL,
            reason TEXT,
            rank INTEGER,
            query TEXT,
            metadata_json TEXT,
            selected INTEGER DEFAULT 1,
            FOREIGN KEY (run_id) REFERENCES context_pack_runs(run_id) ON DELETE CASCADE
        );
        """)

        # Ensure selected column exists for migration from older versions
        with contextlib.suppress(sqlite3.OperationalError):
            cursor.execute(
                "ALTER TABLE context_pack_sources ADD COLUMN selected INTEGER DEFAULT 1;"
            )

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_context_pack_sources_run_id
        ON context_pack_sources(run_id);
        """)

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_context_pack_sources_path
        ON context_pack_sources(path);
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS context_pack_payloads (
            run_id TEXT PRIMARY KEY,
            payload_json BLOB NOT NULL,
            estimated_tokens INTEGER,
            source_count INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES context_pack_runs(run_id) ON DELETE CASCADE
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS retrieval_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            query TEXT NOT NULL,
            result_count INTEGER,
            elapsed_ms INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES context_pack_runs(run_id) ON DELETE CASCADE
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS evaluation_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL,
            metric_text TEXT,
            source_id TEXT,
            source_path TEXT,
            line_start INTEGER,
            line_end INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES context_pack_runs(run_id) ON DELETE CASCADE
        );
        """)

        # Migration for evaluation_records columns
        for col, col_type in (
            ("source_id", "TEXT"),
            ("source_path", "TEXT"),
            ("line_start", "INTEGER"),
            ("line_end", "INTEGER"),
        ):
            with contextlib.suppress(sqlite3.OperationalError):
                cursor.execute(f"ALTER TABLE evaluation_records ADD COLUMN {col} {col_type};")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS provider_tokens (
            provider_id TEXT PRIMARY KEY,
            token TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS provider_oauth (
            provider_id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL,
            access_token TEXT,
            refresh_token TEXT,
            expires_at REAL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS openai_embeddings (
            chunk_id TEXT NOT NULL,
            model TEXT NOT NULL,
            embedding BLOB NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chunk_id, model)
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS local_embeddings (
            chunk_id TEXT NOT NULL,
            model_key TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            embedding BLOB NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chunk_id, model_key)
        );
        """)
        conn.commit()

    def _compress(self, data: str) -> bytes:
        return zlib.compress(data.encode("utf-8"))

    def _decompress(self, data: bytes | str) -> str:
        if isinstance(data, (bytes, memoryview)):
            try:
                return zlib.decompress(data).decode("utf-8")
            except zlib.error:
                if isinstance(data, memoryview):
                    return bytes(data).decode("utf-8")
                return data.decode("utf-8")
        return str(data)

    def get_cached_pack(
        self, task_hash: str, config_hash: str, section_cards_hash: str, index_fingerprint: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT p.payload_json
                FROM context_pack_payloads p
                JOIN context_pack_runs r ON p.run_id = r.run_id
                WHERE r.task_hash = ?
                  AND r.config_hash = ?
                  AND r.section_cards_hash = ?
                  AND (r.retrieval_fingerprint = ? OR r.rtfm_index_fingerprint = ? OR r.context_fingerprint = ?)
                ORDER BY r.created_at DESC
                LIMIT 1
            """,
                (task_hash, config_hash, section_cards_hash, index_fingerprint, index_fingerprint, index_fingerprint),
            )
            row = cursor.fetchone()
            if row:
                data: dict[str, Any] = json.loads(self._decompress(row["payload_json"]))
                return data
        return None

    def store_pack(
        self,
        run_id: str,
        run_data: dict[str, Any],
        payload: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO context_pack_runs
                (run_id, task_hash, task, target, corpus, token_budget, config_hash, section_cards_hash, rtfm_index_fingerprint, retrieval_fingerprint, extension_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    run_id,
                    run_data["task_hash"],
                    run_data["task"],
                    run_data.get("target"),
                    run_data.get("corpus"),
                    run_data["token_budget"],
                    run_data["config_hash"],
                    run_data["section_cards_hash"],
                    run_data.get("rtfm_index_fingerprint"),
                    run_data.get("retrieval_fingerprint") or run_data.get("rtfm_index_fingerprint"),
                    run_data.get("extension_version", "0.1.0"),
                ),
            )

            if sources:
                sources_rows = [
                    (
                        run_id,
                        src.get("path"),
                        src.get("line_start"),
                        src.get("line_end"),
                        src.get("score"),
                        src.get("reason"),
                        rank,
                        src.get("query"),
                        json.dumps(src.get("metadata", {})),
                        src.get("selected", 1),
                    )
                    for rank, src in enumerate(sources)
                ]
                cursor.executemany(
                    """
                    INSERT INTO context_pack_sources
                    (run_id, path, line_start, line_end, score, reason, rank, query, metadata_json, selected)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    sources_rows,
                )

            cursor.execute(
                """
                INSERT INTO context_pack_payloads
                (run_id, payload_json, estimated_tokens, source_count)
                VALUES (?, ?, ?, ?)
            """,
                (
                    run_id,
                    self._compress(json.dumps(payload)),
                    payload.get("estimated_tokens", 0),
                    len(sources),
                ),
            )
            conn.commit()

    def invalidate_for_fingerprint(self, fingerprint: str) -> None:
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    DELETE FROM context_pack_runs
                    WHERE rtfm_index_fingerprint != ?
                """,
                    (fingerprint,),
                )
                conn.commit()
        except sqlite3.OperationalError:
            pass

    def clear(self) -> None:
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM context_pack_runs")
                conn.commit()
        except sqlite3.OperationalError:
            pass

    def get_more_context(self, run_id: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT path, line_start, line_end, score, reason, query, metadata_json
                FROM context_pack_sources
                WHERE run_id = ? AND selected = 0
                ORDER BY rank ASC
                LIMIT ?
            """,
                (run_id, limit),
            )
            rows = cursor.fetchall()

            results = []
            for row in rows:
                results.append(
                    {
                        "path": row["path"],
                        "line_start": row["line_start"],
                        "line_end": row["line_end"],
                        "score": row["score"],
                        "reason": row["reason"],
                        "query": row["query"],
                        "metadata": json.loads(row["metadata_json"])
                        if row["metadata_json"]
                        else {},
                    }
                )

            if results:
                # Mark retrieved as selected so we don't paginate them next time
                conditions = []
                params = [run_id]
                for r in results:
                    conditions.append("(path = ? AND line_start IS ? AND line_end IS ?)")
                    params.extend([r["path"], r["line_start"], r["line_end"]])
                sql = f"UPDATE context_pack_sources SET selected = 1 WHERE run_id = ? AND ({' OR '.join(conditions)})"
                cursor.execute(sql, params)
                conn.commit()

            return results

    def submit_feedback(
        self,
        run_id: str,
        metric_name: str,
        metric_value: float,
        metric_text: str | None = None,
        source_id: str | None = None,
        source_path: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO evaluation_records
                (run_id, metric_name, metric_value, metric_text, source_id, source_path, line_start, line_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (run_id, metric_name, metric_value, metric_text, source_id, source_path, line_start, line_end),
            )
            conn.commit()

    def get_feedback_for_target(self, target: str, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve run-level and source-level feedback for a target section for offline evaluation."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT e.id, e.run_id, e.metric_name, e.metric_value, e.metric_text,
                       e.source_id, e.source_path, e.line_start, e.line_end, e.created_at
                FROM evaluation_records e
                JOIN context_pack_runs r ON e.run_id = r.run_id
                WHERE r.target = ?
                ORDER BY e.created_at DESC
                LIMIT ?
            """,
                (target, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_target_feedback_summary(self, target: str) -> dict[str, Any]:
        """Compute aggregated feedback metrics for a target section for offline inspection."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT e.metric_name, AVG(e.metric_value) as avg_value, COUNT(*) as count
                FROM evaluation_records e
                JOIN context_pack_runs r ON e.run_id = r.run_id
                WHERE r.target = ?
                GROUP BY e.metric_name
            """,
                (target,),
            )
            metrics_summary = {
                row["metric_name"]: {
                    "avg_value": round(float(row["avg_value"]), 3),
                    "count": row["count"],
                }
                for row in cursor.fetchall()
            }
            return {"target": target, "metrics": metrics_summary}

    def get_provider_token(self, provider_id: str) -> str | None:
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT token FROM provider_tokens WHERE provider_id = ?", (provider_id,)
                )
                row = cursor.fetchone()
                if row:
                    return str(row["token"])
        except sqlite3.OperationalError:
            return None
        return None

    def set_provider_token(self, provider_id: str, token: str) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO provider_tokens (provider_id, token, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(provider_id) DO UPDATE SET token=excluded.token, updated_at=CURRENT_TIMESTAMP
            """,
                (provider_id, token),
            )
            conn.commit()

    def get_provider_oauth(self, provider_id: str) -> dict[str, Any] | None:
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT client_id, access_token, refresh_token, expires_at
                    FROM provider_oauth
                    WHERE provider_id = ?
                """,
                    (provider_id,),
                )
                row = cursor.fetchone()
                if row:
                    return {
                        "client_id": row["client_id"],
                        "access_token": row["access_token"],
                        "refresh_token": row["refresh_token"],
                        "expires_at": row["expires_at"],
                    }
        except sqlite3.OperationalError:
            return None
        return None

    def set_provider_oauth(
        self,
        provider_id: str,
        client_id: str,
        access_token: str | None = None,
        refresh_token: str | None = None,
        expires_at: float | None = None,
    ) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO provider_oauth (provider_id, client_id, access_token, refresh_token, expires_at, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(provider_id) DO UPDATE SET
                    client_id=excluded.client_id,
                    access_token=COALESCE(excluded.access_token, provider_oauth.access_token),
                    refresh_token=COALESCE(excluded.refresh_token, provider_oauth.refresh_token),
                    expires_at=COALESCE(excluded.expires_at, provider_oauth.expires_at),
                    updated_at=CURRENT_TIMESTAMP
            """,
                (provider_id, client_id, access_token, refresh_token, expires_at),
            )
            conn.commit()

    def get_openai_embeddings_stats(self, model: str) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as count, MAX(updated_at) as latest_updated FROM openai_embeddings WHERE model = ?",
                (model,),
            )
            row = cursor.fetchone()
            if row:
                return {"count": row["count"], "latest_updated": row["latest_updated"]}
            return {"count": 0, "latest_updated": None}

    def get_all_openai_embeddings(self, model: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.cursor()
            if model:
                cursor.execute(
                    "SELECT chunk_id, embedding, model FROM openai_embeddings WHERE model = ?",
                    (model,),
                )
            else:
                cursor.execute("SELECT chunk_id, embedding, model FROM openai_embeddings")
            return [
                {"chunk_id": row["chunk_id"], "embedding": row["embedding"], "model": row["model"]}
                for row in cursor.fetchall()
            ]

    def store_openai_embeddings(self, embeddings_data: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            cursor = conn.cursor()
            normalized = []
            for item in embeddings_data:
                emb = item["embedding"]
                if isinstance(emb, list):
                    emb = json.dumps(emb)
                normalized.append(
                    {
                        "chunk_id": item["chunk_id"],
                        "model": item.get("model", "text-embedding-3-small"),
                        "embedding": emb,
                    }
                )
            cursor.executemany(
                """
                INSERT INTO openai_embeddings (chunk_id, model, embedding, updated_at)
                VALUES (:chunk_id, :model, :embedding, CURRENT_TIMESTAMP)
                ON CONFLICT(chunk_id, model) DO UPDATE SET
                    embedding=excluded.embedding,
                    updated_at=CURRENT_TIMESTAMP
            """,
                normalized,
            )
            conn.commit()


    def get_missing_openai_chunks(
        self, rtfm_db_path: str, model: str = "text-embedding-3-small"
    ) -> list[dict[str, Any]]:
        """Returns chunks from RTFM DB that do not have an OpenAI embedding for the configured model in cache."""
        missing = []
        try:
            rtfm_conn = sqlite3.connect(rtfm_db_path, check_same_thread=False)
            rtfm_conn.row_factory = sqlite3.Row

            # Attach context_cache.sqlite to RTFM connection
            escaped_cache_path = self.db_path.replace("'", "''")
            rtfm_conn.execute(f"ATTACH DATABASE '{escaped_cache_path}' AS cache_db")

            cursor = rtfm_conn.cursor()
            # Select chunks from RTFM that are NOT in cache_db.openai_embeddings for the specific model
            cursor.execute(
                """
                SELECT c.chunk_id, c.content, b.filename as file_path, c.line_start, c.line_end
                FROM chunks c
                JOIN books b ON c.book_id = b.id
                LEFT JOIN cache_db.openai_embeddings e ON c.chunk_id = e.chunk_id AND e.model = ?
                WHERE e.chunk_id IS NULL
            """,
                (model,),
            )

            for row in cursor.fetchall():
                missing.append(
                    {
                        "chunk_id": row["chunk_id"],
                        "content": row["content"],
                        "file_path": row["file_path"],
                        "line_start": row["line_start"],
                        "line_end": row["line_end"],
                    }
                )
        except Exception as e:
            import logging

            logging.getLogger("mcp-server").error(f"Failed to fetch missing OpenAI chunks: {e}")
        finally:
            if "rtfm_conn" in locals():
                with contextlib.suppress(Exception):
                    rtfm_conn.execute("DETACH DATABASE cache_db")
                rtfm_conn.close()

        return missing

    def get_local_embeddings(self, model_key: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT chunk_id, model_key, content_hash, embedding, updated_at
                FROM local_embeddings
                WHERE model_key = ?
                """,
                (model_key,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_local_embeddings_stats(self, model_key: str) -> dict[str, Any]:
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) AS count, MAX(updated_at) AS latest_updated
                FROM local_embeddings
                WHERE model_key = ?
                """,
                (model_key,),
            )
            row = cursor.fetchone()
            if row:
                return {"count": row["count"], "latest_updated": row["latest_updated"]}
            return {"count": 0, "latest_updated": None}

    def store_local_embeddings(self, embeddings_data: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO local_embeddings (
                    chunk_id, model_key, content_hash, embedding, updated_at
                )
                VALUES (:chunk_id, :model_key, :content_hash, :embedding, CURRENT_TIMESTAMP)
                ON CONFLICT(chunk_id, model_key) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    embedding=excluded.embedding,
                    updated_at=CURRENT_TIMESTAMP
                """,
                embeddings_data,
            )
            conn.commit()

    def get_missing_local_chunks(
        self,
        rtfm_db_path: str,
        model_key: str,
    ) -> list[dict[str, Any]]:
        """Return new or content-changed RTFM chunks for one local model identity."""
        cached = {
            row["chunk_id"]: row["content_hash"]
            for row in self.get_local_embeddings(model_key)
        }
        missing: list[dict[str, Any]] = []
        with sqlite3.connect(rtfm_db_path, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT c.chunk_id, c.content, b.filename AS file_path,
                       c.line_start, c.line_end
                FROM chunks c
                JOIN books b ON c.book_id = b.id
                """
            ).fetchall()
        for row in rows:
            content = str(row["content"])
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if cached.get(row["chunk_id"]) == content_hash:
                continue
            missing.append(
                {
                    "chunk_id": row["chunk_id"],
                    "content": content,
                    "content_hash": content_hash,
                    "file_path": row["file_path"],
                    "line_start": row["line_start"],
                    "line_end": row["line_end"],
                }
            )
        return missing
