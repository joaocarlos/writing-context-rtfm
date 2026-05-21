import unittest
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock

from writing_context_rtfm.config import AppConfig, RTFMConfig, CacheConfig, ContextConfig, SectionCardsConfig
from writing_context_rtfm.section_cards import SectionCards, DocumentCard
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.storage import ExtensionStore

class TestCachingRobustness(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.project_root = Path(self.tmp_dir)
        
        # Create necessary directories
        (self.project_root / ".writing-context").mkdir()
        (self.project_root / ".rtfm").mkdir()
        
        # Write config and section cards
        self.config_file = self.project_root / ".writing-context" / "config.yaml"
        self.config_file.write_text("version: 1\n")
        
        self.sc_file = self.project_root / ".writing-context" / "section_cards.yaml"
        self.sc_file.write_text("version: 1\ndocument:\n  title: Test\nsections:\n")
        
        # Create mock library.db
        self.rtfm_db = self.project_root / ".rtfm" / "library.db"
        self.rtfm_db.write_text("dummy rtfm database content")
        
        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(corpus="test_corpus", project_root=str(self.project_root)),
            context=ContextConfig(),
            cache=CacheConfig(path=str(self.project_root / ".writing-context" / "cache.sqlite")),
            section_cards=SectionCardsConfig(path=str(self.sc_file))
        )
        self.section_cards = SectionCards(
            version=1,
            document=DocumentCard(title="Test"),
            sections={}
        )
        
        self.adapter = MagicMock()
        self.adapter.search.return_value = []
        
        self.store = ExtensionStore(self.config.cache.path)
        self.store.init_db()
        self.generator = ContextPackGenerator(self.config, self.section_cards, self.adapter, self.store)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def test_cache_hits_and_invalidations(self):
        task = "test task"
        
        # 1. First run (cache miss, gets cached)
        self.generator.generate(task=task, target=None, token_budget=1000)
        self.assertEqual(self.adapter.search.call_count, 3)
        self.adapter.search.reset_mock()
        
        # 2. Second run (cache hit, search NOT called)
        self.generator.generate(task=task, target=None, token_budget=1000)
        self.adapter.search.assert_not_called()
        
        # 3. Modify config.yaml (invalidates cache)
        self.config_file.write_text("version: 1\n# modified config comment")
        self.generator.generate(task=task, target=None, token_budget=1000)
        self.assertEqual(self.adapter.search.call_count, 3)
        self.adapter.search.reset_mock()
        
        # 4. Another run (cache hit on the new state)
        self.generator.generate(task=task, target=None, token_budget=1000)
        self.adapter.search.assert_not_called()
        
        # 5. Modify section_cards.yaml (invalidates cache)
        self.sc_file.write_text("version: 1\n# modified section cards comment")
        self.generator.generate(task=task, target=None, token_budget=1000)
        self.assertEqual(self.adapter.search.call_count, 3)
        self.adapter.search.reset_mock()
        
        # 6. Another run (cache hit)
        self.generator.generate(task=task, target=None, token_budget=1000)
        self.adapter.search.assert_not_called()
        
        # 7. Modify rtfm library.db (invalidates cache due to fingerprint change)
        # We rewrite with different content (changes size and modification time)
        self.rtfm_db.write_text("different and longer dummy rtfm database content")
        import time
        # Ensure system modification time changes if OS file resolution is coarse
        os.utime(self.rtfm_db, (time.time() + 10, time.time() + 10))
        
        self.generator.generate(task=task, target=None, token_budget=1000)
        self.assertEqual(self.adapter.search.call_count, 3)

if __name__ == '__main__':
    unittest.main()
