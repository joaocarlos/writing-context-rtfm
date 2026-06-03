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


if __name__ == "__main__":
    unittest.main()
