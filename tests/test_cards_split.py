import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from writing_context_rtfm.section_cards import (
    load_section_cards,
    merge_cards,
    migrate_legacy_cards,
)


class TestCardsSplitAndMerge(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.wc_dir = self.test_dir / ".writing-context"
        self.wc_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_legacy_migration(self):
        # Create legacy section_cards.yaml
        legacy_yaml = self.wc_dir / "section_cards.yaml"
        legacy_yaml.write_text(
            """
version: 1
document:
  title: "Legacy Paper"
  thesis: "Legacy Thesis"
  writing_style:
    tone: "formal"
  terminology:
    legacy_term:
      definition: "A legacy definition"
sections:
  section_1:
    title: "Intro"
    role: "Introduce things"
    path: "sections/intro.tex"
    key_terms: ["term1"]
    depends_on: ["section_2"]
    must_preserve: ["preserve1"]
    avoid: ["avoid1"]
    constraints: ["constraint1"]
""",
            encoding="utf-8",
        )

        res = migrate_legacy_cards(str(self.test_dir))
        self.assertTrue(res)

        # Check legacy file is renamed to .backup
        self.assertFalse(legacy_yaml.exists())
        self.assertTrue((self.wc_dir / "section_cards.yaml.backup").exists())

        # Check overrides.yaml created
        overrides_path = self.wc_dir / "cards.overrides.yaml"
        self.assertTrue(overrides_path.exists())
        with open(overrides_path, encoding="utf-8") as f:
            overrides = yaml.safe_load(f)

        self.assertEqual(overrides["document"]["title"], "Legacy Paper")
        self.assertEqual(overrides["sections"]["section_1"]["purpose"], "Introduce things")
        self.assertEqual(overrides["sections"]["section_1"]["key_terms"], ["term1"])

        # Check lock.json created
        lock_path = self.wc_dir / "cards.lock.json"
        self.assertTrue(lock_path.exists())
        with open(lock_path, encoding="utf-8") as f:
            lock = json.load(f)
        self.assertIn("section_1", lock["sections"])

    def test_merge_generated_and_overrides(self):
        generated = {
            "version": 2,
            "document": {
                "title": "Generated Paper",
                "thesis": "Generated Thesis",
            },
            "sections": {
                "section_1": {
                    "identity": {
                        "source": "sections/intro.tex",
                        "selector": "sec:intro",
                    },
                    "structure": {
                        "title": "Generated Title",
                        "parent": "document_main",
                    },
                    "purpose": {
                        "value": "Generated purpose",
                        "confidence": 0.9,
                    },
                    "key_terms": [
                        {"value": "term_gen", "status": "generated"},
                        {"value": "term_rej", "status": "rejected"},
                    ],
                    "facts": [
                        {"value": "Gaussian sigma = 250 m", "status": "accepted"},
                        {"value": "Buffer = 500 m", "status": "generated"},
                    ],
                }
            },
        }

        overrides = {
            "version": 2,
            "document": {
                "thesis": "Override Thesis",
            },
            "sections": {
                "section_1": {
                    "purpose": "Override purpose",
                    "must_preserve": ["Custom rule"],
                }
            },
        }

        lock = {
            "generation_version": 2,
            "extractor_version": 1,
            "sections": {
                "section_1": {
                    "content_hash": "hash123",
                    "decisions": {},
                    "stale_fields": [],
                }
            },
        }

        cards = merge_cards(generated, overrides, lock)

        # Document checks
        self.assertEqual(cards.document.title, "Generated Paper")  # falls back to generated
        self.assertEqual(cards.document.thesis, "Override Thesis")  # overrides takes precedence

        # Section checks
        self.assertIn("section_1", cards.sections)
        sec = cards.sections["section_1"]
        self.assertEqual(sec.title, "Generated Title")
        self.assertEqual(sec.path, "sections/intro.tex")
        self.assertEqual(sec.role, "Override purpose")  # override purpose takes precedence
        self.assertEqual(sec.key_terms, ["term_gen"])  # rejects "term_rej"

        # must_preserve merges custom override rules + accepted facts
        self.assertIn("Custom rule", sec.must_preserve)
        self.assertIn("Gaussian sigma = 250 m", sec.must_preserve)
        self.assertNotIn("Buffer = 500 m", sec.must_preserve)  # status "generated" is not accepted

    def test_load_section_cards_with_split(self):
        # Write mock files directly to .writing-context
        generated_path = self.wc_dir / "cards.generated.yaml"
        generated_path.write_text(
            """
version: 2
document:
  title: "Split Paper"
sections:
  section_1:
    identity:
      source: "sections/intro.tex"
""",
            encoding="utf-8",
        )

        overrides_path = self.wc_dir / "cards.overrides.yaml"
        overrides_path.write_text(
            """
version: 2
sections:
  section_1:
    purpose: "Test purpose"
""",
            encoding="utf-8",
        )

        # Run loading
        cards = load_section_cards(str(self.wc_dir / "section_cards.yaml"))
        self.assertIsNotNone(cards)
        self.assertEqual(cards.document.title, "Split Paper")
        self.assertIn("section_1", cards.sections)
        self.assertEqual(cards.sections["section_1"].role, "Test purpose")
