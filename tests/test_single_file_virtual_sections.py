import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from writing_context_rtfm.config import AppConfig, RTFMConfig, ContextConfig, CacheConfig, SectionCardsConfig
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.storage import ExtensionStore


class TestSingleFileVirtualSections(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

        # Create a single monolithic paper.tex with multiple sections
        self.tex_path = self.root / "paper.tex"
        self.tex_path.write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n\n"
            "\\section{Introduction}\n"
            "Introduction text line 1.\n"
            "Introduction text line 2.\n\n"
            "\\section{Methodology}\n"
            "Methodology text line 1.\n"
            "Methodology text line 2.\n"
            "Methodology text line 3.\n\n"
            "\\section{Results}\n"
            "Results text line 1.\n\n"
            "\\end{document}\n",
            encoding="utf-8",
        )

        config = AppConfig(
            version=1,
            rtfm=RTFMConfig(project_root=str(self.root)),
            context=ContextConfig(),
            cache=CacheConfig(path=str(self.root / "cache.sqlite")),
            section_cards=SectionCardsConfig(required=False),
        )

        adapter = MagicMock()
        adapter.search.return_value = []
        store = ExtensionStore(str(self.root / "cache.sqlite"))
        store.init_db()

        self.generator = ContextPackGenerator(config, None, adapter, store)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_target_virtual_section_in_single_file_paper(self):
        # Target "Methodology" section without explicit line numbers or section cards
        pack = self.generator.generate(
            task="Refine methodology details",
            target="Methodology",
            token_budget=2000,
            project_root=str(self.root),
        )

        target_spans = [s for s in pack.source_spans if s.source_role == "target_text"]
        self.assertEqual(len(target_spans), 1)
        span = target_spans[0]
        self.assertEqual(span.path, "paper.tex")
        self.assertEqual(span.line_start, 8)
        self.assertIn("Methodology text line 1.", span.metadata["snippet"])
        self.assertIn("Methodology text line 3.", span.metadata["snippet"])
        self.assertNotIn("Introduction text", span.metadata["snippet"])
        self.assertNotIn("Results text", span.metadata["snippet"])
