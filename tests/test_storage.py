import os
import unittest

from writing_context_rtfm.storage import ExtensionStore


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_cache.sqlite"
        self.store = ExtensionStore(self.db_path)
        self.store.init_db()

    def tearDown(self):
        self.store.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_init_db_creates_tables(self):
        with self.store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row["name"] for row in cur.fetchall()]
        self.assertIn("context_pack_runs", tables)
        self.assertIn("schema_version", tables)

    def test_store_and_retrieve_pack(self):
        run_id = "test-run-123"
        run_data = {
            "task_hash": "task-hash-abc",
            "task": "write intro",
            "target": "intro",
            "corpus": "manuscript",
            "token_budget": 1000,
            "config_hash": "cfg-hash-111",
            "section_cards_hash": "sc-hash-222",
            "rtfm_index_fingerprint": "fingerprint-333",
        }
        payload = {"task": "write intro", "estimated_tokens": 150, "source_spans": []}
        sources = [
            {
                "path": "file1.txt",
                "line_start": 1,
                "line_end": 10,
                "score": 0.9,
                "reason": "test reason",
                "query": "write",
            }
        ]

        # Store it
        self.store.store_pack(run_id, run_data, payload, sources)

        # Retrieve it
        cached = self.store.get_cached_pack(
            task_hash="task-hash-abc",
            config_hash="cfg-hash-111",
            section_cards_hash="sc-hash-222",
            index_fingerprint="fingerprint-333",
        )
        self.assertIsNotNone(cached)
        self.assertEqual(cached["task"], "write intro")

        # Invalidate it with a different fingerprint
        self.store.invalidate_for_fingerprint("fingerprint-444")

        # Should now be missing
        cached_after = self.store.get_cached_pack(
            task_hash="task-hash-abc",
            config_hash="cfg-hash-111",
            section_cards_hash="sc-hash-222",
            index_fingerprint="fingerprint-333",
        )
        self.assertIsNone(cached_after)

    def test_clear_deletes_all(self):
        run_id = "test-run-123"
        run_data = {
            "task_hash": "task-hash-abc",
            "task": "write intro",
            "token_budget": 1000,
            "config_hash": "cfg-hash",
            "section_cards_hash": "sc-hash",
            "rtfm_index_fingerprint": "fingerprint",
        }
        self.store.store_pack(run_id, run_data, {}, [])

        self.store.clear()

        with self.store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM context_pack_runs")
            count = cur.fetchone()[0]
        self.assertEqual(count, 0)

    def test_compression_and_transparency(self):
        run_id = "test-run-comp"
        run_data = {
            "task_hash": "task-hash-comp",
            "task": "test compression",
            "token_budget": 1000,
            "config_hash": "cfg-hash-comp",
            "section_cards_hash": "sc-hash-comp",
            "rtfm_index_fingerprint": "fingerprint-comp",
        }
        payload = {
            "task": "test compression",
            "estimated_tokens": 200,
            "details": "This is a longer payload to verify compression details.",
        }

        # Store
        self.store.store_pack(run_id, run_data, payload, [])

        # Verify it's actually compressed bytes in the DB
        with self.store._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT payload_json FROM context_pack_payloads WHERE run_id = ?", (run_id,)
            )
            row = cur.fetchone()
            self.assertIsNotNone(row)
            db_payload = row["payload_json"]
            self.assertIsInstance(db_payload, bytes)

            # Decompress manually to check level/correctness
            import zlib

            decompressed = zlib.decompress(db_payload).decode("utf-8")
            import json

            self.assertEqual(json.loads(decompressed), payload)

        # Retrieve through store interface (transparency)
        cached = self.store.get_cached_pack(
            task_hash="task-hash-comp",
            config_hash="cfg-hash-comp",
            section_cards_hash="sc-hash-comp",
            index_fingerprint="fingerprint-comp",
        )
        self.assertIsNotNone(cached)
        self.assertEqual(cached, payload)

    def test_backward_compatibility_uncompressed(self):
        # Insert a legacy run & payload directly with plain text (TEXT) JSON
        run_id = "test-run-legacy"
        run_data = {
            "run_id": run_id,
            "task_hash": "task-hash-legacy",
            "task": "legacy task",
            "token_budget": 1000,
            "config_hash": "cfg-hash-legacy",
            "section_cards_hash": "sc-hash-legacy",
            "rtfm_index_fingerprint": "fingerprint-legacy",
        }
        payload = {"task": "legacy task", "estimated_tokens": 100, "legacy": True}

        with self.store._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO context_pack_runs
                (run_id, task_hash, task, token_budget, config_hash, section_cards_hash, rtfm_index_fingerprint)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    run_id,
                    run_data["task_hash"],
                    run_data["task"],
                    run_data["token_budget"],
                    run_data["config_hash"],
                    run_data["section_cards_hash"],
                    run_data["rtfm_index_fingerprint"],
                ),
            )

            # Insert uncompressed TEXT payload (SQLite will accept a string here)
            import json

            cursor.execute(
                """
                INSERT INTO context_pack_payloads
                (run_id, payload_json, estimated_tokens, source_count)
                VALUES (?, ?, ?, ?)
            """,
                (run_id, json.dumps(payload), payload.get("estimated_tokens", 0), 0),
            )
            conn.commit()

        # Retrieve through the store interface
        cached = self.store.get_cached_pack(
            task_hash="task-hash-legacy",
            config_hash="cfg-hash-legacy",
            section_cards_hash="sc-hash-legacy",
            index_fingerprint="fingerprint-legacy",
        )
        self.assertIsNotNone(cached)
        self.assertEqual(cached, payload)

    def test_provider_tokens(self):
        # Initial is None
        self.assertIsNone(self.store.get_provider_token("scite"))
        self.assertIsNone(self.store.get_provider_token("consensus"))

        # Save and retrieve
        self.store.set_provider_token("scite", "scite_token_abc")
        self.store.set_provider_token("consensus", "consensus_token_123")

        self.assertEqual(self.store.get_provider_token("scite"), "scite_token_abc")
        self.assertEqual(self.store.get_provider_token("consensus"), "consensus_token_123")

        # Update
        self.store.set_provider_token("scite", "new_scite_token")
        self.assertEqual(self.store.get_provider_token("scite"), "new_scite_token")

    def test_provider_oauth(self):
        # Initial is None
        self.assertIsNone(self.store.get_provider_oauth("scite"))

        # Save registration client_id only
        self.store.set_provider_oauth("scite", client_id="client123")
        oauth = self.store.get_provider_oauth("scite")
        self.assertIsNotNone(oauth)
        self.assertEqual(oauth["client_id"], "client123")
        self.assertIsNone(oauth["access_token"])
        self.assertIsNone(oauth["refresh_token"])
        self.assertIsNone(oauth["expires_at"])

        # Update access tokens (simulate code exchange)
        self.store.set_provider_oauth(
            "scite",
            client_id="client123",
            access_token="access_tok",
            refresh_token="refresh_tok",
            expires_at=1234567.8,
        )
        oauth = self.store.get_provider_oauth("scite")
        self.assertEqual(oauth["access_token"], "access_tok")
        self.assertEqual(oauth["refresh_token"], "refresh_tok")
        self.assertEqual(oauth["expires_at"], 1234567.8)

        # Update access token only (simulate refresh)
        self.store.set_provider_oauth(
            "scite", client_id="client123", access_token="new_access_tok", expires_at=999999.0
        )
        oauth = self.store.get_provider_oauth("scite")
        self.assertEqual(oauth["access_token"], "new_access_tok")
        self.assertEqual(oauth["refresh_token"], "refresh_tok")  # preserved
        self.assertEqual(oauth["expires_at"], 999999.0)

    def test_fresh_uninitialized_store_never_raises_operational_error(self):
        # Create a fresh store with temp file without calling init_db
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sqlite") as tmp:
            fresh_store = ExtensionStore(tmp.name)
            # Must return None safely without raising sqlite3.OperationalError
            self.assertIsNone(fresh_store.get_provider_token("openai_semantic"))
            self.assertIsNone(fresh_store.get_provider_oauth("openai_semantic"))
            stats = fresh_store.get_openai_embeddings_stats("text-embedding-3-small")
            self.assertEqual(stats.get("count", 0), 0)
            tokenomics = fresh_store.get_tokenomics_stats()
            self.assertEqual(tokenomics["total_runs"], 0)
            session = fresh_store.get_session_tokenomics()
            self.assertEqual(session["runs_in_window"], 0)
            fresh_store.close()

    def test_legacy_cache_database_migration_and_mixed_stats(self):
        import sqlite3
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sqlite") as tmp:
            legacy_path = tmp.name
            # 1. Create older database before Sprint 6 Eixo 2
            legacy_conn = sqlite3.connect(legacy_path)
            cur = legacy_conn.cursor()
            cur.execute("""
            CREATE TABLE context_pack_runs (
                run_id TEXT PRIMARY KEY,
                task_hash TEXT NOT NULL,
                task TEXT NOT NULL,
                token_budget INTEGER NOT NULL,
                pack_tokens INTEGER,
                baseline_doc_tokens INTEGER,
                tokens_saved INTEGER,
                savings_ratio REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cur.execute("""
            INSERT INTO context_pack_runs (run_id, task_hash, task, token_budget, pack_tokens, baseline_doc_tokens, tokens_saved, savings_ratio)
            VALUES ('run-legacy-001', 'thash001', 'legacy task', 4000, 3000, 50000, 47000, 0.94);
            """)
            legacy_conn.commit()
            legacy_conn.close()

            # 2. Open with ExtensionStore - triggers _ensure_schema migrations
            legacy_store = ExtensionStore(legacy_path)
            stats1 = legacy_store.get_tokenomics_stats()
            self.assertEqual(stats1["total_runs"], 1)
            self.assertEqual(stats1["total_baseline_tokens"], 50000)
            # Legacy row has baseline_realistic_tokens NULL -> falls back to baseline_doc_tokens
            self.assertEqual(stats1["total_realistic_baseline_tokens"], 50000)
            self.assertEqual(stats1["total_realistic_tokens_saved"], 47000)

            # 3. Store a modern pack with Sprint 6 Eixo 2 columns
            modern_run_data = {
                "task_hash": "thash002",
                "task": "modern task",
                "target": "intro.tex",
                "token_budget": 4000,
                "pack_tokens": 2000,
                "baseline_doc_tokens": 40000,
                "baseline_realistic_tokens": 10000,
                "baseline_tokens_raw": 20000,
                "tokens_saved": 38000,
                "realistic_tokens_saved": 8000,
                "savings_ratio": 0.95,
                "realistic_savings_ratio": 0.8,
                "is_capped": 1,
                "instruction_tokens": 50,
                "generation_tokens": 500,
                "mode": "write",
                "config_hash": "cfg2",
                "section_cards_hash": "sc2",
            }
            legacy_store.store_pack("run-modern-002", modern_run_data, {"task": "modern task"}, [])

            # 4. Check aggregate stats across legacy + modern
            stats2 = legacy_store.get_tokenomics_stats()
            self.assertEqual(stats2["total_runs"], 2)
            self.assertEqual(stats2["total_pack_tokens"], 5000)
            self.assertEqual(stats2["total_baseline_tokens"], 90000)  # 50k + 40k
            # 50k (legacy fallback) + 10k (modern) = 60k
            self.assertEqual(stats2["total_realistic_baseline_tokens"], 60000)
            # 47k (legacy fallback) + 8k (modern) = 55k
            self.assertEqual(stats2["total_realistic_tokens_saved"], 55000)
            # 50k (legacy fallback) + 20k (modern raw) = 70k
            self.assertEqual(stats2["total_baseline_tokens_raw"], 70000)

            # 5. Calibration test with exact and prefix match
            ok_exact = legacy_store.store_calibration("run-modern-002", "neighborhood", 12000)
            self.assertTrue(ok_exact)

            ok_prefix = legacy_store.store_calibration("run-legacy", "chapter", 35000)
            self.assertTrue(ok_prefix)

            # Check empirical stats
            stats3 = legacy_store.get_tokenomics_stats()
            self.assertEqual(stats3["empirical_calibrated_runs"], 2)
            self.assertGreater(stats3["avg_empirical_savings_percentage"], 0.0)

            # 6. Generation tokens recording
            ok_gen = legacy_store.record_generation_tokens("run-legacy-001", 650)
            self.assertTrue(ok_gen)
            sess = legacy_store.get_session_tokenomics(window_hours=24)
            self.assertEqual(sess["runs_in_window"], 2)
            self.assertEqual(sess["session_generation_tokens"], 1150)  # 500 + 650

            legacy_store.close()

    def test_defensive_error_suppression(self):
        # Test invalid arguments to get_session_tokenomics
        res = self.store.get_session_tokenomics(window_hours="invalid", message_limit=None)  # type: ignore[arg-type]
        self.assertIsInstance(res, dict)
        self.assertEqual(res["window_hours"], 5)
        self.assertEqual(res["session_message_limit"], 25)

        # Test closed connection or error condition does not crash
        self.store.close()
        # Even if connection is forcibly broken / closed
        import unittest.mock as mock

        with mock.patch.object(self.store, "_connect", side_effect=Exception("DB boom")):
            stats = self.store.get_tokenomics_stats()
            self.assertEqual(stats["total_runs"], 0)
            sess = self.store.get_session_tokenomics()
            self.assertEqual(sess["runs_in_window"], 0)
            cal = self.store.store_calibration("run-1", "chapter")
            self.assertFalse(cal)
            rec = self.store.record_generation_tokens("run-1", 100)
            self.assertFalse(rec)

    def test_wal_mode_and_concurrency(self):
        # Verify journal_mode is WAL for disk databases
        with self.store._connect() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode;")
            mode = cur.fetchone()[0]
            self.assertEqual(str(mode).lower(), "wal")


if __name__ == "__main__":
    unittest.main()
