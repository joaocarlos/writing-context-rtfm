import tempfile
import unittest
from pathlib import Path

import yaml

from writing_context_rtfm.section_cards import (
    DocumentCard,
    SectionCard,
    SectionCards,
    load_section_cards,
    merge_cards,
    migrate_legacy_cards,
    validate_section_cards,
)


class TestSectionCardsDeep(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_merge_section_cards_complex(self):
        generated = {
            "document": {
                "title": "Generated Title",
                "thesis": "Generated Thesis",
                "terminology": {
                    "ML": "Machine Learning",
                    "NLP": {
                        "definition": "Natural Language Processing",
                        "variants": ["Language Processing"],
                        "avoid": ["Old NLP"],
                    },
                },
            },
            "sections": {
                "section_intro": {
                    "identity": {"source": "intro.tex"},
                    "structure": {"title": "Introduction"},
                    "purpose": {"value": "Introduce the topic"},
                    "key_terms": [
                        {"value": "AI", "status": "generated"},
                        {"value": "ObsoleteTerm", "status": "rejected"},
                    ],
                    "dependencies": [
                        {"target": "section_bg", "status": "generated"},
                        {"target": "section_deprecated", "status": "rejected"},
                    ],
                    "facts": [
                        {"value": "Preserve this fact", "status": "accepted"},
                        {"value": "Unverified fact", "status": "generated"},
                    ],
                    "constraints": [
                        {
                            "value": "Avoid saying buzzword",
                            "type": "terminology_avoidance",
                            "status": "accepted",
                        },
                        {
                            "value": "Limit to 500 words",
                            "type": "length",
                            "status": "accepted",
                        },
                        {
                            "value": "Unaccepted constraint",
                            "type": "misc",
                            "status": "generated",
                        },
                    ],
                }
            },
        }

        overrides = {
            "document": {
                "title": "Overridden Title",
                "terminology": {
                    "ML": {
                        "definition": "Overridden Machine Learning",
                        "variants": ["AI/ML"],
                        "avoid": ["Heuristics"],
                    }
                },
            },
            "sections": {
                "section_intro": {
                    "scope_exclusions": ["Exclude future work"],
                    "terminology_preferences": ["Prefer DL over DNN"],
                }
            },
        }

        cards = merge_cards(generated, overrides, {})
        self.assertEqual(cards.document.title, "Overridden Title")
        self.assertEqual(cards.document.thesis, "Generated Thesis")
        self.assertIn("ML", cards.document.terminology)
        self.assertIn("NLP", cards.document.terminology)
        self.assertEqual(
            cards.document.terminology["ML"]["definition"], "Overridden Machine Learning"
        )
        self.assertIn("AI/ML", cards.document.terminology["ML"]["variants"])

        sec = cards.sections["section_intro"]
        self.assertEqual(sec.title, "Introduction")
        self.assertIn("AI", sec.key_terms)
        self.assertNotIn("ObsoleteTerm", sec.key_terms)
        self.assertIn("section_bg", sec.depends_on)
        self.assertNotIn("section_deprecated", sec.depends_on)
        self.assertIn("Preserve this fact", sec.must_preserve)
        self.assertIn("Avoid saying buzzword", sec.avoid)
        self.assertIn("Limit to 500 words", sec.constraints)
        self.assertIn("Exclude future work", sec.constraints)
        self.assertIn("Prefer DL over DNN", sec.constraints)

    def test_merge_normalizes_scalar_terminology_entries(self):
        cards = merge_cards(
            {
                "version": 2,
                "document": {
                    "terminology": {
                        "Latency": "Time between request and response.",
                    }
                },
                "sections": {},
            },
            {},
            {},
        )

        self.assertEqual(
            cards.document.terminology["Latency"],
            {
                "definition": "Time between request and response.",
                "variants": [],
                "avoid": [],
            },
        )

    def test_validate_section_cards(self):
        # Create valid cards
        cards = SectionCards(
            version=1,
            document=DocumentCard(title="Valid Paper"),
            sections={
                "section_intro": SectionCard(
                    id="section_intro",
                    title="Intro",
                    path="intro.tex",
                    depends_on=[],
                ),
                "section_methods": SectionCard(
                    id="section_methods",
                    title="Methods",
                    path="methods.tex",
                    depends_on=["section_intro"],
                ),
            },
        )
        warnings = validate_section_cards(cards)
        self.assertEqual(len(warnings), 0)

        # Create cards with unknown dependency
        cards_broken = SectionCards(
            version=1,
            document=DocumentCard(title="Broken Paper"),
            sections={
                "section_methods": SectionCard(
                    id="section_methods",
                    title="Methods",
                    path="methods.tex",
                    depends_on=["section_nonexistent"],
                ),
            },
        )
        warnings_broken = validate_section_cards(cards_broken)
        self.assertTrue(any("unknown section" in w.lower() for w in warnings_broken))

    def test_load_section_cards_legacy_and_missing(self):
        sc_file = self.root / "section_cards.yaml"
        raw_data = {
            "version": 1,
            "document": {
                "title": "Paper Title",
                "thesis": "Paper Thesis",
                "terminology": {
                    "Term1": "Simple Definition String",
                    "Term2": {
                        "definition": "Dict Definition",
                        "variants": ["T2"],
                        "avoid": ["BadT2"],
                    },
                },
            },
            "sections": {
                "section_one": {
                    "title": "Section One",
                    "path": "one.tex",
                    "key_terms": ["Term1"],
                    "depends_on": ["section_two"],
                }
            },
        }

        with open(sc_file, "w") as f:
            yaml.safe_dump(raw_data, f)

        loaded = load_section_cards(str(sc_file), required=True)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.document.title, "Paper Title")
        self.assertIn("section_one", loaded.sections)
        self.assertEqual(
            loaded.document.terminology["Term1"]["definition"], "Simple Definition String"
        )
        self.assertEqual(loaded.document.terminology["Term2"]["definition"], "Dict Definition")

        # Test non-existent file
        self.assertIsNone(load_section_cards(str(self.root / "missing.yaml"), required=False))
        with self.assertRaises(FileNotFoundError):
            load_section_cards(str(self.root / "missing.yaml"), required=True)

    def test_migrate_legacy_cards(self):
        wc_dir = self.root / ".writing-context"
        wc_dir.mkdir(parents=True, exist_ok=True)
        sc_file = wc_dir / "section_cards.yaml"
        sc_file.write_text(
            """
version: 1
document:
  title: Legacy Paper
  thesis: Legacy Thesis
sections:
  section_intro:
    title: Introduction
    path: intro.tex
    role: Introductory
    key_terms: ["Term1"]
    depends_on: []
    must_preserve: ["Fact1"]
    avoid: ["Avoid1"]
    constraints: ["Con1"]
""",
            encoding="utf-8",
        )

        migrated = migrate_legacy_cards(str(self.root))
        self.assertTrue(migrated)
        self.assertTrue((wc_dir / "cards.overrides.yaml").exists())
        self.assertTrue((wc_dir / "cards.lock.json").exists())
        self.assertTrue((wc_dir / "section_cards.yaml.backup").exists())

        # Test missing legacy file returns False
        empty_dir = self.root / "empty"
        empty_dir.mkdir()
        self.assertFalse(migrate_legacy_cards(str(empty_dir)))
