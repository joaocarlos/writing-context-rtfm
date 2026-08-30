import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards
from writing_context_rtfm.storage import ExtensionStore


class TestElasticBudget(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

        # Create section file with substantial text (~1000 words / ~1300 tokens)
        self.sec_path = self.root / "sections" / "large_sec.tex"
        self.sec_path.parent.mkdir(parents=True, exist_ok=True)
        paragraphs = ["\\section{Large Section}\n"]
        for i in range(10):
            paragraphs.append(f"Paragraph {i}: " + ("word " * 100) + "\n\n")
        self.sec_path.write_text("".join(paragraphs), encoding="utf-8")

        cards = SectionCards(
            version=1,
            document=DocumentCard(title="Test Paper", thesis="Thesis statement"),
            sections={
                "section_large": SectionCard(
                    id="section_large",
                    title="Large Section",
                    path="sections/large_sec.tex",
                    role="Explain large section content",
                )
            },
        )

        config = AppConfig(
            version=1,
            rtfm=RTFMConfig(project_root=str(self.root)),
            context=ContextConfig(),
            cache=CacheConfig(path=str(self.root / "cache.sqlite")),
            section_cards=SectionCardsConfig(),
        )

        adapter = MagicMock()
        adapter.search.return_value = []
        store = ExtensionStore(str(self.root / "cache.sqlite"))
        store.init_db()

        self.generator = ContextPackGenerator(config, cards, adapter, store)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_undersized_budget_auto_expands_with_complete_status(self):
        # Request with a tiny token budget (e.g. 300 tokens)
        pack = self.generator.generate(
            task="Revise large section",
            target="section_large",
            token_budget=300,
            project_root=str(self.root),
        )

        # Status must remain complete (not degraded)
        self.assertEqual(pack.status, "complete")
        self.assertTrue(pack.estimated_tokens > 1000)
        target_spans = [s for s in pack.source_spans if s.source_role == "target_text"]
        self.assertEqual(len(target_spans), 1)
        self.assertEqual(target_spans[0].priority, "essential")

        # Warning should document the auto-expansion
        self.assertTrue(
            any("Auto-expanded token budget" in w for w in pack.warnings)
        )
