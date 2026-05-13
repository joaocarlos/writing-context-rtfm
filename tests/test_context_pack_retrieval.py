import unittest
import yaml
import os
from unittest.mock import patch, MagicMock

from writing_context_rtfm.config import AppConfig, ContextConfig, RTFMConfig, CacheConfig, SectionCardsConfig
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.rtfm_adapter import RTFMAdapter

class TestContextPackRetrieval(unittest.TestCase):
    def setUp(self):
        self.project_root = "tests/fixtures/mini_latex_project"
        self.expected_yaml = "tests/fixtures/expected_sources.yaml"
        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(project_root=self.project_root, corpus="test_corpus"),
            context=ContextConfig(default_token_budget=1000),
            cache=CacheConfig(enabled=False, path="test_cache.sqlite"),
            section_cards=SectionCardsConfig(path=os.path.join(self.project_root, ".writing-context", "section_cards.yaml"))
        )
        self.cards = load_section_cards(self.config.section_cards.path)
        
    @patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter.search")
    @patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter.sync")
    def test_retrieval_metrics(self, mock_sync, mock_search):
        # Mock RTFM sync and search
        mock_sync.return_value = True
        mock_search.return_value = [
            MagicMock(path="sections/03_approach.tex", line_start=1, line_end=10, score=0.9, reason="Mock reason", query="Mock query")
        ]
        
        adapter = RTFMAdapter()
        adapter.sync(self.project_root, "test_corpus")
        
        store = MagicMock(spec=ExtensionStore)
        store.get_cached_pack.return_value = None
        
        generator = ContextPackGenerator(self.config, self.cards, adapter, store)
        
        with open(self.expected_yaml, 'r') as f:
            tasks = yaml.safe_load(f)["tasks"]
            
        for task_def in tasks:
            pack = generator.generate(
                task=task_def["task"],
                target=task_def["target_section"],
                token_budget=1000
            )
            
            retrieved_paths = {span.path for span in pack.source_spans}
            expected_paths = set(task_def["expected_sources"])
            
            # True Positives
            tp = len(retrieved_paths.intersection(expected_paths))
            # False Positives
            fp = len(retrieved_paths - expected_paths)
            # False Negatives
            fn = len(expected_paths - retrieved_paths)
            
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0
            irrelevant_rate = fp / len(retrieved_paths) if len(retrieved_paths) > 0 else 0
            
            self.assertGreaterEqual(recall, 0.80, "Expected source recall is too low")
            self.assertLessEqual(irrelevant_rate, 0.30, "Irrelevant source rate is too high")
            self.assertLessEqual(pack.estimated_tokens, 1000, "Pack exceeds token budget")

if __name__ == '__main__':
    unittest.main()
