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
from writing_context_rtfm.section_cards import (
    DocumentCard,
    SectionCard,
    SectionCards,
    validate_section_cards,
)
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.utils import (
    extract_keywords,
    is_allowed_source,
)


class TestRelease0233Improvements(unittest.TestCase):
    def test_excluded_source_filtering(self):
        """Verify agent files and receipts are excluded from source spans."""
        self.assertFalse(is_allowed_source("CLAUDE.md"))
        self.assertFalse(is_allowed_source("AGENTS.md"))
        self.assertFalse(is_allowed_source("gemini.md"))
        self.assertFalse(is_allowed_source("path/to/claude.md"))
        self.assertFalse(is_allowed_source("references/CopyrightReceipt.pdf"))
        self.assertTrue(is_allowed_source("chapter5.tex"))
        self.assertTrue(is_allowed_source("section1.md"))

    def test_task_procedural_stopwords(self):
        """Verify task instruction verbs are filtered out of keyword extraction."""
        procedural_task = (
            "Expand only Chapter 5 Summary with practical implications of CRI and HP "
            "sensor priority computation. Merge concise scope into evidence paragraphs. "
            "Preserve the existing scheduling transition and all other sections. "
            "Avoid claiming network delivery gains or superiority to static configurations."
        )
        keywords = extract_keywords(procedural_task)
        self.assertNotIn("expand", keywords)
        self.assertNotIn("only", keywords)
        self.assertNotIn("preserve", keywords)
        self.assertNotIn("avoid", keywords)
        self.assertNotIn("claiming", keywords)
        self.assertNotIn("merge", keywords)
        self.assertNotIn("concise", keywords)
        self.assertNotIn("other", keywords)
        self.assertNotIn("superiority", keywords)
        self.assertNotIn("gains", keywords)
        self.assertIn("sensor", keywords)
        self.assertIn("priority", keywords)

    def test_validate_section_cards_ignores_asset_labels_and_uses_aliases(self):
        """Verify validate_section_cards ignores intra-document asset labels and resolves aliases."""
        cards = SectionCards(
            version=2,
            document=DocumentCard(title="Thesis", thesis="Urban EMS priority"),
            sections={
                "section_chap5": SectionCard(
                    id="section_chap5",
                    title="Chapter 5: Sensing",
                    path="chapter5.tex",
                    selector="sec:sensing",
                    depends_on=[
                        "eq:eval-time",
                        "fig:system-architecture",
                        "tab:sensor-specs",
                        "lst:code-sample",
                        "sec:sensing",  # alias to self / known selector
                        "unknown_nonexistent_section",
                    ],
                )
            },
        )
        warnings = validate_section_cards(cards)
        # Only the truly unknown section should produce a warning
        self.assertEqual(len(warnings), 1)
        self.assertIn("unknown_nonexistent_section", warnings[0])
        self.assertNotIn("eq:eval-time", warnings[0])
        self.assertNotIn("fig:system-architecture", warnings[0])

    def test_heuristic_target_inference_from_task(self):
        """Verify _resolve_target infers chapter/section target when target is omitted."""
        config = AppConfig(
            version=1,
            rtfm=RTFMConfig(),
            context=ContextConfig(default_token_budget=1000),
            cache=CacheConfig(enabled=False),
            section_cards=SectionCardsConfig(),
        )
        cards = SectionCards(
            version=2,
            document=DocumentCard(title="Thesis"),
            sections={
                "section_context_aware_sensor": SectionCard(
                    id="section_context_aware_sensor",
                    title="Chapter 5: Context-Aware Prioritization",
                    path="chapter5.tex",
                    selector="chap:cri-hp",
                )
            },
        )
        adapter = MagicMock()
        adapter.search.return_value = []
        store = MagicMock(spec=ExtensionStore)

        generator = ContextPackGenerator(config, cards, adapter, store)

        # English "Chapter 5"
        key, card, path = generator._resolve_target(
            target=None,
            pr="",
            task="Expand only Chapter 5 Summary with practical implications of CRI and HP.",
        )
        self.assertEqual(key, "section_context_aware_sensor")
        self.assertEqual(path, "chapter5.tex")

        # Portuguese "Capítulo 5"
        key, card, path = generator._resolve_target(
            target=None,
            pr="",
            task="Expandir conclusões do Capítulo 5 com novos dados.",
        )
        self.assertEqual(key, "section_context_aware_sensor")
        self.assertEqual(path, "chapter5.tex")

        # Direct file mention in task
        key, card, path = generator._resolve_target(
            target=None,
            pr="",
            task="Revisar texto em chapter5.tex para remover redundâncias.",
        )
        self.assertEqual(key, "section_context_aware_sensor")
        self.assertEqual(path, "chapter5.tex")

        # No target mention
        key, card, path = generator._resolve_target(
            target=None,
            pr="",
            task="Discuss overall methodology without specifying parts.",
        )
        self.assertIsNone(key)
        self.assertIsNone(card)
        self.assertIsNone(path)
