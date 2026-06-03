import unittest
from unittest.mock import MagicMock

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.storage import ExtensionStore


class TestContextPackGenerator(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(),
            context=ContextConfig(default_token_budget=1000),
            cache=CacheConfig(enabled=False),
            section_cards=SectionCardsConfig(),
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

    def test_resilient_target_resolution(self):
        from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards

        sections = {
            "section_abstract": SectionCard(
                id="section_abstract", title="Abstract Section", path="sections/abstract.tex"
            ),
            "introduction": SectionCard(
                id="introduction", title="Intro Section", path="sections/intro.tex"
            ),
        }
        cards = SectionCards(version=1, document=DocumentCard(title="Test Doc"), sections=sections)

        generator = ContextPackGenerator(self.config, cards, self.adapter, self.store)

        # 1. Exact match
        resolved_key, card, path = generator._resolve_target("section_abstract", ".")
        self.assertEqual(resolved_key, "section_abstract")
        self.assertEqual(path, "sections/abstract.tex")

        # 2. f"section_{target}" match
        resolved_key, card, path = generator._resolve_target("abstract", ".")
        self.assertEqual(resolved_key, "section_abstract")
        self.assertEqual(path, "sections/abstract.tex")

        # 3. target[8:] prefix stripping match
        resolved_key, card, path = generator._resolve_target("section_introduction", ".")
        self.assertEqual(resolved_key, "introduction")
        self.assertEqual(path, "sections/intro.tex")

        # 4. Case-insensitive title match
        resolved_key, card, path = generator._resolve_target("abstract section", ".")
        self.assertEqual(resolved_key, "section_abstract")
        self.assertEqual(path, "sections/abstract.tex")

        # 5. Path stem match
        resolved_key, card, path = generator._resolve_target("intro", ".")
        self.assertEqual(resolved_key, "introduction")
        self.assertEqual(path, "sections/intro.tex")

        # 6. Fallback path check
        resolved_key, card, path = generator._resolve_target("sections/abstract.tex", ".")
        self.assertEqual(resolved_key, "section_abstract")

        resolved_key, card, path = generator._resolve_target("nonexistent_file.tex", ".")
        self.assertEqual(resolved_key, None)
        self.assertEqual(path, "nonexistent_file.tex")

    def test_latex_safety_does_not_degrade(self):
        import os
        import tempfile

        from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "abstract.tex")
            # Write a LaTeX content that has math environment/commands
            with open(file_path, "w") as f:
                f.write(
                    "This is a line with \\ref{eq1} and some \\begin{equation} x=y \\end{equation} LaTeX content."
                )

            sections = {
                "section_abstract": SectionCard(
                    id="section_abstract", title="Abstract Section", path="abstract.tex"
                )
            }
            cards = SectionCards(
                version=1, document=DocumentCard(title="Test Doc"), sections=sections
            )

            generator = ContextPackGenerator(self.config, cards, self.adapter, self.store)

            # Request target pack using the resilient target name "abstract" and a line range
            pack = generator.generate(
                task="Write the abstract",
                target="abstract",
                token_budget=1000,
                project_root=tmpdir,
                line_start=1,
                line_end=1,
            )

            # Assert target span is extracted successfully (not omitted)
            self.assertEqual(pack.status, "complete")
            target_spans = [s for s in pack.source_spans if s.source_role == "target_text"]
            self.assertEqual(len(target_spans), 1)
            self.assertEqual(target_spans[0].path, "abstract.tex")
            self.assertIn("ref{eq1}", target_spans[0].metadata["snippet"])

            # Assert LaTeX Safety warning is present in warnings
            latex_warnings = [w for w in pack.warnings if "LaTeX Safety:" in w]
            self.assertTrue(len(latex_warnings) > 0)

    def test_token_budget_auto_scaling_warning(self):
        # Mock search results returning large snippets
        mock_result1 = MagicMock()
        mock_result1.path = "sections/abstract.tex"
        mock_result1.line_start = 1
        mock_result1.line_end = 20
        mock_result1.score = 0.9
        # 1000 'word ' tokens (approx 1000 tokens)
        mock_result1.snippet = "word " * 1000
        mock_result1.metadata = {}

        mock_result2 = MagicMock()
        mock_result2.path = "sections/intro.tex"
        mock_result2.line_start = 1
        mock_result2.line_end = 20
        mock_result2.score = 0.8
        # 1000 'word ' tokens (approx 1000 tokens)
        mock_result2.snippet = "word " * 1000
        mock_result2.metadata = {}

        self.adapter.search.return_value = [mock_result1, mock_result2]

        # Use a small token budget so both cannot fit
        pack = self.generator.generate(task="write intro", target=None, token_budget=1500)

        self.assertEqual(
            pack.status, "degraded"
        )  # Degraded due to missing section cards, NOT budget
        self.assertEqual(len(pack.source_spans), 2)


if __name__ == "__main__":
    unittest.main()
