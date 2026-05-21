import unittest
import os
from writing_context_rtfm.storage import ExtensionStore

class TestStorage(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_cache.sqlite"
        self.store = ExtensionStore(self.db_path)
        self.store.init_db()

    def tearDown(self):
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
        payload = {
            "task": "write intro",
            "estimated_tokens": 150,
            "source_spans": []
        }
        sources = [
            {"path": "file1.txt", "line_start": 1, "line_end": 10, "score": 0.9, "reason": "test reason", "query": "write"}
        ]
        
        # Store it
        self.store.store_pack(run_id, run_data, payload, sources)
        
        # Retrieve it
        cached = self.store.get_cached_pack(
            task_hash="task-hash-abc",
            config_hash="cfg-hash-111",
            section_cards_hash="sc-hash-222",
            index_fingerprint="fingerprint-333"
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
            index_fingerprint="fingerprint-333"
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

if __name__ == '__main__':
    unittest.main()
