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

if __name__ == '__main__':
    unittest.main()
