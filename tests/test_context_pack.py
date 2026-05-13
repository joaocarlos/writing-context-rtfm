import unittest
from unittest.mock import MagicMock
from writing_context_rtfm.config import AppConfig, ContextConfig, RTFMConfig, CacheConfig, SectionCardsConfig
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.storage import ExtensionStore

class TestContextPackGenerator(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(),
            context=ContextConfig(default_token_budget=1000),
            cache=CacheConfig(enabled=False),
            section_cards=SectionCardsConfig()
        )
        self.adapter = MagicMock()
        self.adapter.search.return_value = []
        self.store = MagicMock(spec=ExtensionStore)
        self.generator = ContextPackGenerator(self.config, None, self.adapter, self.store)

    def test_generate_empty_results(self):
        pack = self.generator.generate(task="test task", target=None, token_budget=1000)
        self.assertEqual(pack.task, "test task")
        self.assertEqual(len(pack.source_spans), 0)
        self.adapter.search.assert_called()

if __name__ == '__main__':
    unittest.main()
