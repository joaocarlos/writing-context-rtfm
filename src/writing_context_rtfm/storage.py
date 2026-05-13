"""SQLite storage for extension data."""
import sqlite3
import json
import os
from typing import Optional, Dict, List, Any

SCHEMA_VERSION = 1

class ExtensionStore:
    def __init__(self, db_path: str = ".writing-context/context_cache.sqlite"):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _enable_foreign_keys(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys = ON;")

    def init_db(self) -> None:
        with self._connect() as conn:
            self._enable_foreign_keys(conn)
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
                extension_version TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """)

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
                FOREIGN KEY (run_id) REFERENCES context_pack_runs(run_id) ON DELETE CASCADE
            );
            """)

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
                payload_json TEXT NOT NULL,
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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id) REFERENCES context_pack_runs(run_id) ON DELETE CASCADE
            );
            """)
            conn.commit()

    def get_cached_pack(self, task_hash: str, config_hash: str, section_cards_hash: str, index_fingerprint: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            self._enable_foreign_keys(conn)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.payload_json
                FROM context_pack_payloads p
                JOIN context_pack_runs r ON p.run_id = r.run_id
                WHERE r.task_hash = ?
                  AND r.config_hash = ?
                  AND r.section_cards_hash = ?
                  AND r.rtfm_index_fingerprint = ?
                ORDER BY r.created_at DESC
                LIMIT 1
            """, (task_hash, config_hash, section_cards_hash, index_fingerprint))
            row = cursor.fetchone()
            if row:
                return json.loads(row["payload_json"])
        return None

    def store_pack(self, run_id: str, run_data: Dict[str, Any], payload: Dict[str, Any], sources: List[Dict[str, Any]]) -> None:
        with self._connect() as conn:
            self._enable_foreign_keys(conn)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO context_pack_runs
                (run_id, task_hash, task, target, corpus, token_budget, config_hash, section_cards_hash, rtfm_index_fingerprint, extension_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, run_data["task_hash"], run_data["task"], run_data.get("target"), run_data.get("corpus"),
                run_data["token_budget"], run_data["config_hash"], run_data["section_cards_hash"],
                run_data["rtfm_index_fingerprint"], run_data.get("extension_version", "0.1.0")
            ))

            for rank, src in enumerate(sources):
                cursor.execute("""
                    INSERT INTO context_pack_sources
                    (run_id, path, line_start, line_end, score, reason, rank, query, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    run_id, src.get("path"), src.get("line_start"), src.get("line_end"),
                    src.get("score"), src.get("reason"), rank, src.get("query"),
                    json.dumps(src.get("metadata", {}))
                ))

            cursor.execute("""
                INSERT INTO context_pack_payloads
                (run_id, payload_json, estimated_tokens, source_count)
                VALUES (?, ?, ?, ?)
            """, (
                run_id, json.dumps(payload), payload.get("estimated_tokens", 0), len(sources)
            ))
            conn.commit()

    def invalidate_for_fingerprint(self, fingerprint: str) -> None:
        with self._connect() as conn:
            self._enable_foreign_keys(conn)
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM context_pack_runs
                WHERE rtfm_index_fingerprint != ?
            """, (fingerprint,))
            conn.commit()

    def clear(self) -> None:
        with self._connect() as conn:
            self._enable_foreign_keys(conn)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM context_pack_runs")
            conn.commit()
