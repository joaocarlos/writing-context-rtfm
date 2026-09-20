"""SQLite storage for extension data."""

import contextlib
import hashlib
import json
import logging
import os
import sqlite3
import zlib
from typing import Any

logger = logging.getLogger("writing-context-rtfm.storage")

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
            self._conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON;")
            if self.db_path != ":memory:":
                with contextlib.suppress(sqlite3.OperationalError):
                    self._conn.execute("PRAGMA journal_mode = WAL;")
                with contextlib.suppress(sqlite3.OperationalError):
                    self._conn.execute("PRAGMA busy_timeout = 30000;")
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

        # Migration for older databases: add base and fingerprint columns if missing
        for col in (
            "target",
            "corpus",
            "config_hash",
            "section_cards_hash",
            "rtfm_index_fingerprint",
            "retrieval_fingerprint",
            "provider_fingerprint",
            "context_fingerprint",
            "extension_version",
        ):
            with contextlib.suppress(sqlite3.OperationalError):
                cursor.execute(f"ALTER TABLE context_pack_runs ADD COLUMN {col} TEXT;")

        # Migration for tokenomics and counterfactual audit columns
        for col, col_type in (
            ("pack_tokens", "INTEGER"),
            ("baseline_doc_tokens", "INTEGER"),
            ("tokens_saved", "INTEGER"),
            ("savings_ratio", "REAL"),
            ("schema_overhead_tokens", "INTEGER"),
            ("latency_ms", "REAL"),
            ("baseline_realistic_tokens", "INTEGER"),
            ("realistic_tokens_saved", "INTEGER"),
            ("realistic_savings_ratio", "REAL"),
            ("baseline_mode", "TEXT"),
            ("mode", "TEXT"),
            ("baseline_tokens_raw", "INTEGER"),
            ("is_capped", "INTEGER DEFAULT 0"),
            ("instruction_tokens", "INTEGER DEFAULT 0"),
            ("generation_tokens", "INTEGER DEFAULT 0"),
            ("empirical_choice", "TEXT"),
            ("empirical_tokens", "INTEGER"),
        ):
            with contextlib.suppress(sqlite3.OperationalError):
                cursor.execute(f"ALTER TABLE context_pack_runs ADD COLUMN {col} {col_type};")

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_context_pack_runs_task_hash
        ON context_pack_runs(task_hash);
        """)

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_context_pack_runs_created_at
        ON context_pack_runs(created_at);
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

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS reranker_scores (
            model_key TEXT NOT NULL,
            task_hash TEXT NOT NULL,
            snippet_hash TEXT NOT NULL,
            score REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (model_key, task_hash, snippet_hash)
        );
        """)

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_reranker_scores_lookup
        ON reranker_scores(model_key, task_hash);
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
                (
                    task_hash,
                    config_hash,
                    section_cards_hash,
                    index_fingerprint,
                    index_fingerprint,
                    index_fingerprint,
                ),
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
            pack_tokens = run_data.get("pack_tokens") or payload.get("estimated_tokens", 0)
            baseline_doc_tokens = run_data.get("baseline_doc_tokens") or 0
            tokens_saved = run_data.get("tokens_saved") or 0
            savings_ratio = run_data.get("savings_ratio") or 0.0
            schema_overhead = run_data.get("schema_overhead_tokens") or 420
            latency_ms = run_data.get("latency_ms") or 0.0
            baseline_realistic = run_data.get("baseline_realistic_tokens") or baseline_doc_tokens
            realistic_saved = run_data.get("realistic_tokens_saved") or tokens_saved
            realistic_ratio = run_data.get("realistic_savings_ratio") or savings_ratio
            baseline_mode = run_data.get("baseline_mode") or "section_neighborhood"
            task_mode = run_data.get("mode") or payload.get("mode") or "write"
            baseline_raw = run_data.get("baseline_tokens_raw") or baseline_realistic
            is_capped = 1 if run_data.get("is_capped") else 0
            instruction_tokens = run_data.get("instruction_tokens") or 0
            generation_tokens = run_data.get("generation_tokens") or 0
            empirical_choice = run_data.get("empirical_choice")
            empirical_tokens = run_data.get("empirical_tokens")

            cursor.execute(
                """
                INSERT INTO context_pack_runs
                (run_id, task_hash, task, target, corpus, token_budget, config_hash, section_cards_hash, rtfm_index_fingerprint, retrieval_fingerprint, extension_version, pack_tokens, baseline_doc_tokens, tokens_saved, savings_ratio, schema_overhead_tokens, latency_ms, baseline_realistic_tokens, realistic_tokens_saved, realistic_savings_ratio, baseline_mode, mode, baseline_tokens_raw, is_capped, instruction_tokens, generation_tokens, empirical_choice, empirical_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    run_id,
                    run_data["task_hash"],
                    run_data["task"],
                    run_data.get("target"),
                    run_data.get("corpus"),
                    run_data["token_budget"],
                    run_data.get("config_hash"),
                    run_data.get("section_cards_hash"),
                    run_data.get("rtfm_index_fingerprint"),
                    run_data.get("retrieval_fingerprint") or run_data.get("rtfm_index_fingerprint"),
                    run_data.get("extension_version", "0.1.0"),
                    pack_tokens,
                    baseline_doc_tokens,
                    tokens_saved,
                    savings_ratio,
                    schema_overhead,
                    latency_ms,
                    baseline_realistic,
                    realistic_saved,
                    realistic_ratio,
                    baseline_mode,
                    task_mode,
                    baseline_raw,
                    is_capped,
                    instruction_tokens,
                    generation_tokens,
                    empirical_choice,
                    empirical_tokens,
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
                (
                    run_id,
                    metric_name,
                    metric_value,
                    metric_text,
                    source_id,
                    source_path,
                    line_start,
                    line_end,
                ),
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

    def store_calibration(
        self,
        run_id: str,
        choice: str,
        empirical_tokens: int | None = None,
    ) -> bool:
        """Store empirical user calibration for what they would have pasted without MCP."""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE context_pack_runs
                    SET empirical_choice = ?,
                        empirical_tokens = ?
                    WHERE run_id = ?
                    """,
                    (choice, empirical_tokens, run_id),
                )
                if cursor.rowcount == 0 and len(run_id) >= 6:
                    cursor.execute(
                        """
                        UPDATE context_pack_runs
                        SET empirical_choice = ?,
                            empirical_tokens = ?
                        WHERE run_id LIKE ?
                        """,
                        (choice, empirical_tokens, f"{run_id}%"),
                    )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error("Failed to store calibration for run %s: %s", run_id, e)
            return False

    def record_generation_tokens(self, run_id: str, generation_tokens: int) -> bool:
        """Record actual generation tokens produced by the downstream LLM."""
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE context_pack_runs
                    SET generation_tokens = ?
                    WHERE run_id = ?
                    """,
                    (int(generation_tokens), run_id),
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error("Failed to record generation tokens for run %s: %s", run_id, e)
            return False

    def get_session_tokenomics(
        self, window_hours: int = 5, message_limit: int = 25
    ) -> dict[str, Any]:
        """Aggregate tokenomics and message limits within a rolling session window (e.g. 5 hours for Astra)."""
        try:
            w_hours = abs(int(window_hours))
        except (TypeError, ValueError):
            w_hours = 5

        try:
            msg_limit = max(1, int(message_limit))
        except (TypeError, ValueError):
            msg_limit = 25

        single_call_threshold = 272_000  # Astra threshold where single call price doubles

        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) as runs_in_window,
                        COALESCE(SUM(pack_tokens), 0) as total_pack_tokens,
                        COALESCE(SUM(instruction_tokens), 0) as total_instruction_tokens,
                        COALESCE(SUM(generation_tokens), 0) as total_generation_tokens,
                        COALESCE(SUM(schema_overhead_tokens), 0) as total_schema_overhead,
                        COALESCE(AVG(pack_tokens), 0.0) as avg_pack_tokens,
                        COALESCE(MAX(pack_tokens), 0) as max_pack_tokens
                    FROM context_pack_runs
                    WHERE created_at >= datetime('now', ? || ' hours')
                """,
                    (f"-{w_hours}",),
                )
                row = cursor.fetchone()
                total_pack = int(row["total_pack_tokens"] or 0) if row else 0
                total_instruction = int(row["total_instruction_tokens"] or 0) if row else 0
                total_generation = int(row["total_generation_tokens"] or 0) if row else 0
                total_schema = int(row["total_schema_overhead"] or 0) if row else 0
                total_roundtrip = total_pack + total_instruction + total_generation + total_schema
                runs = int(row["runs_in_window"] or 0) if row else 0
                avg_pack = float(row["avg_pack_tokens"] or 0.0) if row else 0.0
                max_pack = int(row["max_pack_tokens"] or 0) if row else 0

                # Message limit tracking (the primary Astra 5-hour constraint)
                session_calls_used = runs
                calls_remaining_by_message_limit = max(0, msg_limit - session_calls_used)

                # Token headroom across session
                token_headroom = max(0, single_call_threshold - total_roundtrip)
                calls_remaining_by_token_headroom = (
                    int(token_headroom // max(avg_pack, 2500.0)) if token_headroom > 0 else 0
                )

                # The real bottleneck is the minimum of message limit and token headroom
                bottleneck_calls_remaining = min(
                    calls_remaining_by_message_limit, calls_remaining_by_token_headroom
                )
                bottleneck_cause = (
                    "message_limit"
                    if calls_remaining_by_message_limit <= calls_remaining_by_token_headroom
                    else "token_headroom"
                )

                return {
                    "window_hours": w_hours,
                    "runs_in_window": runs,
                    "session_calls_used": session_calls_used,
                    "session_message_limit": msg_limit,
                    "calls_remaining_by_message_limit": calls_remaining_by_message_limit,
                    "session_tokens_used": total_roundtrip,
                    "session_pack_tokens": total_pack,
                    "session_instruction_tokens": total_instruction,
                    "session_generation_tokens": total_generation,
                    "remaining_headroom": token_headroom,
                    "threshold": single_call_threshold,
                    "single_call_threshold": single_call_threshold,
                    "max_single_pack_observed": max_pack,
                    "projected_calls_remaining": bottleneck_calls_remaining,
                    "calls_remaining_by_token_headroom": calls_remaining_by_token_headroom,
                    "bottleneck_calls_remaining": bottleneck_calls_remaining,
                    "bottleneck_cause": bottleneck_cause,
                    "threshold_exceeded": max_pack >= single_call_threshold,
                    "message_limit_exceeded": session_calls_used >= msg_limit,
                }
        except Exception as e:
            logger.warning("Failed to query session tokenomics: %s", e)
            return {
                "window_hours": w_hours,
                "runs_in_window": 0,
                "session_calls_used": 0,
                "session_message_limit": msg_limit,
                "calls_remaining_by_message_limit": msg_limit,
                "session_tokens_used": 0,
                "session_pack_tokens": 0,
                "session_instruction_tokens": 0,
                "session_generation_tokens": 0,
                "remaining_headroom": single_call_threshold,
                "threshold": single_call_threshold,
                "single_call_threshold": single_call_threshold,
                "max_single_pack_observed": 0,
                "projected_calls_remaining": msg_limit,
                "calls_remaining_by_token_headroom": int(single_call_threshold // 2500),
                "bottleneck_calls_remaining": msg_limit,
                "bottleneck_cause": "message_limit",
                "threshold_exceeded": False,
                "message_limit_exceeded": False,
            }

    def get_tokenomics_stats(self) -> dict[str, Any]:
        """Aggregate cumulative tokenomics, dual baselines, and breakdown by task mode."""
        default_stats: dict[str, Any] = {
            "total_runs": 0,
            "total_pack_tokens": 0,
            "total_baseline_tokens": 0,
            "total_tokens_saved": 0,
            "avg_savings_ratio": 0.0,
            "avg_savings_percentage": 0.0,
            "total_realistic_baseline_tokens": 0,
            "total_realistic_tokens_saved": 0,
            "avg_realistic_savings_ratio": 0.0,
            "avg_realistic_savings_percentage": 0.0,
            "total_baseline_tokens_raw": 0,
            "total_instruction_tokens": 0,
            "total_generation_tokens": 0,
            "avg_latency_ms": 0.0,
            "avg_schema_overhead": 420.0,
            "empirical_calibrated_runs": 0,
            "avg_empirical_savings_percentage": 0.0,
            "recent_runs": [],
            "by_mode": {},
        }
        try:
            with self._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT
                        COUNT(*) as total_runs,
                        COALESCE(SUM(pack_tokens), 0) as total_pack_tokens,
                        COALESCE(SUM(baseline_doc_tokens), 0) as total_baseline_tokens,
                        COALESCE(SUM(tokens_saved), 0) as total_tokens_saved,
                        COALESCE(AVG(savings_ratio), 0.0) as avg_savings_ratio,
                        COALESCE(SUM(COALESCE(baseline_realistic_tokens, baseline_doc_tokens, 0)), 0) as total_realistic_baseline_tokens,
                        COALESCE(SUM(COALESCE(realistic_tokens_saved, tokens_saved, 0)), 0) as total_realistic_tokens_saved,
                        COALESCE(AVG(COALESCE(realistic_savings_ratio, savings_ratio, 0.0)), 0.0) as avg_realistic_savings_ratio,
                        COALESCE(SUM(COALESCE(baseline_tokens_raw, baseline_realistic_tokens, baseline_doc_tokens, 0)), 0) as total_baseline_tokens_raw,
                        COALESCE(SUM(instruction_tokens), 0) as total_instruction_tokens,
                        COALESCE(SUM(generation_tokens), 0) as total_generation_tokens,
                        COALESCE(AVG(latency_ms), 0.0) as avg_latency_ms,
                        COALESCE(AVG(schema_overhead_tokens), 420.0) as avg_schema_overhead,
                        COUNT(CASE WHEN empirical_choice IS NOT NULL THEN 1 END) as empirical_calibrated_runs,
                        COALESCE(AVG(CASE WHEN empirical_tokens IS NOT NULL AND empirical_tokens > 0 THEN 1.0 - (CAST(COALESCE(pack_tokens, 0) + COALESCE(schema_overhead_tokens, 420) AS REAL) / CAST(empirical_tokens AS REAL)) END), 0.0) as avg_empirical_savings_ratio
                    FROM context_pack_runs
                """)
                row = cursor.fetchone()
                if not row or int(row["total_runs"] or 0) == 0:
                    return default_stats

                # Breakdown by task mode
                cursor.execute("""
                    SELECT
                        COALESCE(mode, 'write') as task_mode,
                        COUNT(*) as count,
                        COALESCE(AVG(pack_tokens), 0.0) as avg_pack_tokens,
                        COALESCE(AVG(COALESCE(baseline_realistic_tokens, baseline_doc_tokens, 0.0)), 0.0) as avg_realistic_baseline,
                        COALESCE(AVG(COALESCE(baseline_tokens_raw, baseline_realistic_tokens, baseline_doc_tokens, 0.0)), 0.0) as avg_raw_baseline,
                        COALESCE(AVG(COALESCE(realistic_savings_ratio, savings_ratio, 0.0)), 0.0) as avg_realistic_savings_ratio,
                        COALESCE(AVG(COALESCE(schema_overhead_tokens, 420.0)), 420.0) as avg_schema_overhead,
                        SUM(COALESCE(is_capped, 0)) as capped_runs
                    FROM context_pack_runs
                    GROUP BY COALESCE(mode, 'write')
                """)
                by_mode = {}
                for m_row in cursor.fetchall():
                    ratio = float(m_row["avg_realistic_savings_ratio"] or 0.0)
                    by_mode[str(m_row["task_mode"])] = {
                        "runs": int(m_row["count"] or 0),
                        "avg_pack_tokens": round(float(m_row["avg_pack_tokens"] or 0.0), 1),
                        "avg_realistic_baseline": round(
                            float(m_row["avg_realistic_baseline"] or 0.0), 1
                        ),
                        "avg_raw_baseline": round(float(m_row["avg_raw_baseline"] or 0.0), 1),
                        "avg_savings_percentage": round(ratio * 100.0, 2),
                        "avg_schema_overhead": round(
                            float(m_row["avg_schema_overhead"] or 420.0), 0
                        ),
                        "capped_runs": int(m_row["capped_runs"] or 0),
                    }

                cursor.execute("""
                    SELECT
                        run_id,
                        task,
                        target,
                        token_budget,
                        COALESCE(mode, 'write') as mode,
                        COALESCE(baseline_mode, 'section_neighborhood') as baseline_mode,
                        COALESCE(pack_tokens, 0) as pack_tokens,
                        COALESCE(baseline_doc_tokens, 0) as baseline_doc_tokens,
                        COALESCE(tokens_saved, 0) as tokens_saved,
                        COALESCE(savings_ratio, 0.0) as savings_ratio,
                        COALESCE(baseline_realistic_tokens, baseline_doc_tokens, 0) as baseline_realistic_tokens,
                        COALESCE(baseline_tokens_raw, baseline_realistic_tokens, baseline_doc_tokens, 0) as baseline_tokens_raw,
                        COALESCE(is_capped, 0) as is_capped,
                        COALESCE(instruction_tokens, 0) as instruction_tokens,
                        COALESCE(generation_tokens, 0) as generation_tokens,
                        empirical_choice,
                        empirical_tokens,
                        COALESCE(realistic_tokens_saved, tokens_saved, 0) as realistic_tokens_saved,
                        COALESCE(realistic_savings_ratio, savings_ratio, 0.0) as realistic_savings_ratio,
                        COALESCE(latency_ms, 0.0) as latency_ms,
                        created_at
                    FROM context_pack_runs
                    ORDER BY created_at DESC
                    LIMIT 10
                """)
                recent_runs = [dict(r) for r in cursor.fetchall()]

                realistic_ratio = round(float(row["avg_realistic_savings_ratio"] or 0.0), 4)
                naive_ratio = round(float(row["avg_savings_ratio"] or 0.0), 4)
                empirical_ratio = round(float(row["avg_empirical_savings_ratio"] or 0.0), 4)

                return {
                    "total_runs": int(row["total_runs"] or 0),
                    "total_pack_tokens": int(row["total_pack_tokens"] or 0),
                    "total_baseline_tokens": int(row["total_baseline_tokens"] or 0),
                    "total_tokens_saved": int(row["total_tokens_saved"] or 0),
                    "avg_savings_ratio": naive_ratio,
                    "avg_savings_percentage": round(naive_ratio * 100.0, 2),
                    "total_realistic_baseline_tokens": int(
                        row["total_realistic_baseline_tokens"] or 0
                    ),
                    "total_realistic_tokens_saved": int(row["total_realistic_tokens_saved"] or 0),
                    "avg_realistic_savings_ratio": realistic_ratio,
                    "avg_realistic_savings_percentage": round(realistic_ratio * 100.0, 2),
                    "total_baseline_tokens_raw": int(row["total_baseline_tokens_raw"] or 0),
                    "total_instruction_tokens": int(row["total_instruction_tokens"] or 0),
                    "total_generation_tokens": int(row["total_generation_tokens"] or 0),
                    "empirical_calibrated_runs": int(row["empirical_calibrated_runs"] or 0),
                    "avg_empirical_savings_percentage": round(empirical_ratio * 100.0, 2),
                    "avg_latency_ms": round(float(row["avg_latency_ms"] or 0.0), 2),
                    "avg_schema_overhead": round(float(row["avg_schema_overhead"] or 420.0), 0),
                    "recent_runs": recent_runs,
                    "by_mode": by_mode,
                }
        except Exception as e:
            logger.warning("Failed to query tokenomics stats: %s", e)
            return default_stats

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
        rtfm_conn: sqlite3.Connection | None = None
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
            logger.error("Failed to fetch missing OpenAI chunks: %s", e)
        finally:
            if rtfm_conn is not None:
                with contextlib.suppress(Exception):
                    rtfm_conn.execute("DETACH DATABASE cache_db")
                with contextlib.suppress(Exception):
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
            row["chunk_id"]: row["content_hash"] for row in self.get_local_embeddings(model_key)
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

    def get_reranker_scores(
        self,
        model_key: str,
        task_hash: str,
        snippet_hashes: list[str],
    ) -> dict[str, float]:
        """Fetch cached cross-encoder reranker scores for a task and a list of snippet hashes."""
        if not snippet_hashes:
            return {}
        with self._connect() as conn:
            cursor = conn.cursor()
            placeholders = ",".join("?" for _ in snippet_hashes)
            cursor.execute(
                f"""
                SELECT snippet_hash, score
                FROM reranker_scores
                WHERE model_key = ? AND task_hash = ? AND snippet_hash IN ({placeholders})
                """,
                [model_key, task_hash, *snippet_hashes],
            )
            return {str(row["snippet_hash"]): float(row["score"]) for row in cursor.fetchall()}

    def store_reranker_scores(
        self,
        model_key: str,
        task_hash: str,
        scores: dict[str, float],
    ) -> None:
        """Persist cross-encoder reranker scores for (model_key, task_hash, snippet_hash)."""
        if not scores:
            return
        with self._connect() as conn:
            data = [
                {
                    "model_key": model_key,
                    "task_hash": task_hash,
                    "snippet_hash": snippet_hash,
                    "score": score,
                }
                for snippet_hash, score in scores.items()
            ]
            conn.executemany(
                """
                INSERT INTO reranker_scores (
                    model_key, task_hash, snippet_hash, score, created_at
                )
                VALUES (:model_key, :task_hash, :snippet_hash, :score, CURRENT_TIMESTAMP)
                ON CONFLICT(model_key, task_hash, snippet_hash) DO UPDATE SET
                    score=excluded.score,
                    created_at=CURRENT_TIMESTAMP
                """,
                data,
            )
            conn.commit()
