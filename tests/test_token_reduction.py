import os
import unittest
from unittest.mock import MagicMock, patch

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore


class TestTokenReduction(unittest.TestCase):
    def setUp(self):
        self.project_root = "tests/fixtures/mini_latex_project"
        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(project_root=self.project_root, corpus="test_corpus"),
            context=ContextConfig(default_token_budget=1000),
            cache=CacheConfig(enabled=False, path="test_cache.sqlite"),
            section_cards=SectionCardsConfig(
                path=os.path.join(self.project_root, ".writing-context", "section_cards.yaml")
            ),
        )
        self.cards = load_section_cards(self.config.section_cards.path)

    def count_baseline_tokens(self) -> int:
        tokens = 0
        for root, _, files in os.walk(self.project_root):
            for file in files:
                if file.endswith(".tex"):
                    with open(os.path.join(root, file)) as f:
                        text = f.read()
                        # Rough estimate: 1 token ~= 4 chars
                        tokens += len(text) // 4
        return tokens

    @patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter.search")
    @patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter.sync")
    def test_token_reduction_ratio(self, mock_sync, mock_search):
        mock_sync.return_value = True
        mock_search.return_value = [
            MagicMock(
                path="sections/03_approach.tex",
                line_start=1,
                line_end=20,
                score=0.9,
                reason="Mock reason",
                query="Mock query",
            )
        ]

        adapter = RTFMAdapter()
        store = MagicMock(spec=ExtensionStore)
        store.get_cached_pack.return_value = None

        generator = ContextPackGenerator(self.config, self.cards, adapter, store)
        pack = generator.generate(
            task="Write methodology", target="section_approach", token_budget=1000
        )

        baseline_tokens = self.count_baseline_tokens()
        pack_tokens = pack.estimated_tokens

        reduction_ratio = baseline_tokens / pack_tokens if pack_tokens > 0 else float("inf")

        self.assertGreaterEqual(
            reduction_ratio, 3.0, f"Token reduction ratio too low: {reduction_ratio}"
        )
        self.assertLessEqual(pack_tokens, 1000, "Pack exceeds token budget")


if __name__ == "__main__":
    unittest.main()
