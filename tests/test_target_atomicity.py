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


class TestTargetAtomicity(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

        # Create section file
        self.sec_path = self.root / "sections" / "methodology.tex"
        self.sec_path.parent.mkdir(parents=True, exist_ok=True)
        self.sec_path.write_text(
            "\\section{Methodology}\n"
            "This is paragraph 1 with details on dataset.\n\n"
            "This is paragraph 2 with quantization specifics.\n\n"
            "This is paragraph 3 with equations.\n",
            encoding="utf-8",
        )

        cards = SectionCards(
            version=1,
            document=DocumentCard(title="Test Paper", thesis="Thesis statement"),
            sections={
                "section_methodology": SectionCard(
                    id="section_methodology",
                    title="Methodology",
                    path="sections/methodology.tex",
                    role="Explain dataset and quantization",
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

    def test_target_section_loaded_atomically_without_line_numbers(self):
        pack = self.generator.generate(
            task="Update methodology with learning rate details",
            target="section_methodology",
            token_budget=4000,
            project_root=str(self.root),
        )

        self.assertEqual(pack.status, "complete")
        target_spans = [s for s in pack.source_spans if s.source_role == "target_text"]
        self.assertEqual(len(target_spans), 1)
        span = target_spans[0]
        self.assertEqual(span.path, "sections/methodology.tex")
        self.assertEqual(span.line_start, 1)
        self.assertEqual(span.line_end, 6)
        self.assertEqual(span.priority, "essential")
        self.assertIn("This is paragraph 1", span.metadata["snippet"])
        self.assertIn("This is paragraph 3", span.metadata["snippet"])
