"""Tests verifying resilience of target resolution and non-degraded status behavior."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards
from writing_context_rtfm.server import get_tools_list
from writing_context_rtfm.storage import ExtensionStore


class TestTargetResolutionResilience(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.pr = Path(self.temp_dir.name)

        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(project_root=str(self.pr)),
            context=ContextConfig(default_token_budget=1000),
            cache=CacheConfig(enabled=False),
            section_cards=SectionCardsConfig(),
        )

        self.adapter = MagicMock()
        self.adapter.search.return_value = []
        self.store = MagicMock(spec=ExtensionStore)
        self.store.get_cached_pack.return_value = None

        self.generator = ContextPackGenerator(self.config, None, self.adapter, self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_heuristic_target_inference_with_explicit_filename_and_zero_padding(self) -> None:
        """When task explicitly mentions chapter6.tex and section cards has chapter06.tex, resolve card."""
        card_06 = SectionCard(
            id="chapter_06",
            title="Evaluation and Results",
            path="chapters/chapter06.tex",
            role="evaluation",
        )
        self.generator.section_cards = SectionCards(
            version=1,
            document=DocumentCard(title="Test Thesis"),
            sections={"chapter_06": card_06},
        )

        task = "Review chapter6.tex for minimal adaptation into the thesis narrative"
        pack = self.generator.generate(task=task, target=None, token_budget=4000)

        # Status must be complete, not degraded
        self.assertEqual(pack.status, "complete")
        self.assertTrue(
            any("Note: Target was inferred from task description" in w for w in pack.warnings),
            f"Warnings received: {pack.warnings}",
        )
        self.assertFalse(
            any("No --target provided" in w for w in pack.warnings),
            f"Unexpected warning: {pack.warnings}",
        )

    def test_heuristic_target_inference_zero_padding_chapter_text(self) -> None:
        """When task says 'chapter 6' and card is 'ch06', resolve correctly."""
        card = SectionCard(
            id="ch06",
            title="TRIAGE Scheduling",
            path="chapter06.tex",
            role="core_algorithm",
        )
        self.generator.section_cards = SectionCards(
            version=1,
            document=DocumentCard(title="Test Thesis"),
            sections={"ch06": card},
        )

        task = "Analyze chapter 6 performance bounds and guarantees"
        pack = self.generator.generate(task=task, target=None, token_budget=3000)

        self.assertEqual(pack.status, "complete")
        self.assertTrue(any("Note: Target was inferred" in w for w in pack.warnings))

    def test_heuristic_target_inference_workspace_file_without_card(self) -> None:
        """When task references a .tex file that exists in workspace, infer target_path and stay complete."""
        test_file = self.pr / "chapter6.tex"
        test_file.write_text("\\section{TRIAGE}\nContent goes here.", encoding="utf-8")

        self.generator.section_cards = SectionCards(
            version=1,
            document=DocumentCard(title="Test Thesis"),
            sections={},
        )

        task = "Review chapter6.tex for minimal adaptation into the thesis narrative"
        pack = self.generator.generate(task=task, target=None, token_budget=4000)

        self.assertEqual(pack.status, "complete")
        self.assertTrue(
            any("Note: Target was inferred from task description as 'chapter6.tex'" in w for w in pack.warnings),
            f"Warnings received: {pack.warnings}",
        )
        self.assertFalse(any("No --target provided" in w for w in pack.warnings))

    def test_auditing_and_preflight_warnings_do_not_degrade_pack(self) -> None:
        """Informative card audit/uncertainty warnings must not degrade pack status."""
        card = SectionCard(
            id="intro",
            title="Introduction",
            path="intro.tex",
            role="intro",
            unverified_key_terms=["quantum_edge"],
            unverified_dependencies=["background_sec"],
        )
        self.generator.section_cards = SectionCards(
            version=1,
            document=DocumentCard(title="Test Thesis"),
            sections={"intro": card},
        )

        with patch.object(self.generator, "_record_diagnostic"):
            pack = self.generator.generate(
                task="Write introduction covering context",
                target="intro",
                token_budget=5000,
            )

        self.assertEqual(pack.status, "complete")

    def test_mcp_tool_schema_descriptions_updated(self) -> None:
        """Verify get_writing_context_pack exposes updated target and token_budget guidance."""
        tools = get_tools_list()["tools"]
        writing_tool = next(t for t in tools if t["name"] == "get_writing_context_pack")
        props = writing_tool["inputSchema"]["properties"]

        self.assertIn("STRONGLY RECOMMENDED", props["target"]["description"])
        self.assertIn("chapter6.tex", props["target"]["description"])
        self.assertIn("6000–10000", props["token_budget"]["description"])


if __name__ == "__main__":
    unittest.main()
