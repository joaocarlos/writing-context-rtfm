import unittest
from writing_context_rtfm.config import load_config

class TestConfig(unittest.TestCase):
    def test_load_defaults_when_missing(self):
        config = load_config("nonexistent.yaml")
        self.assertEqual(config.version, 1)
        self.assertEqual(config.rtfm.corpus, "manuscript")
        self.assertEqual(config.context.default_token_budget, 12000)

if __name__ == '__main__':
    unittest.main()
