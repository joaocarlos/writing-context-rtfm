import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from writing_context_rtfm.features import (
    audit_manuscript_terminology,
    cards_build_command,
    cards_infer_command,
    cards_rebuild_command,
    cards_review_command,
    cards_scan_command,
    cards_update_command,
    cards_validate_command,
    find_entry_files,
    get_term_context,
    initialize_section_cards,
    sanitize_id,
)


class TestFeaturesCommands(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

        # Create basic directory structure
        self.wc_dir = self.root / ".writing-context"
        self.wc_dir.mkdir(parents=True, exist_ok=True)

        self.config_yaml = self.wc_dir / "config.yaml"
        self.config_yaml.write_text(
            """
version: 1
rtfm:
  corpus: test_corpus
  project_root: .
cache:
  path: .writing-context/cache.sqlite
section_cards:
  path: .writing-context/section_cards.yaml
""",
            encoding="utf-8",
        )

        # Create test tex and md files
        self.intro_tex = self.root / "intro.tex"
        self.intro_tex.write_text(
            "\\section{Introduction}\n"
            "This paper introduces Deep Learning and CNN models.\n"
            "We avoid outdated deprecated term in our workflow.\n",
            encoding="utf-8",
        )

        self.methods_md = self.root / "methods.md"
        self.methods_md.write_text(
            "# Methodology\n\n"
            "We use quantization and Neural Network architectures.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sanitize_id(self):
        self.assertEqual(sanitize_id("intro.tex"), "section_intro")
        self.assertEqual(sanitize_id("section_methodology.md"), "section_methodology")
        self.assertEqual(sanitize_id("ch-01!data.tex"), "section_ch_01_data")

    def test_initialize_section_cards_fresh(self):
        res = initialize_section_cards(str(self.root))
        self.assertEqual(res["status"], "success")
        self.assertTrue(len(res["added"]) >= 2)

        # Re-running preserves existing cards
        res2 = initialize_section_cards(str(self.root))
        self.assertEqual(res2["status"], "success")
        self.assertTrue(res2["preserved_count"] >= 2)

    @patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter.search")
    def test_audit_manuscript_terminology(self, mock_search):
        # Initialize cards first
        initialize_section_cards(str(self.root))

        # Add key_terms to sections
        sc_path = self.wc_dir / "section_cards.yaml"
        with open(sc_path) as f:
            data = yaml.safe_load(f)

        if "section_intro" in data.get("sections", {}):
            data["sections"]["section_intro"]["key_terms"] = ["Deep Learning"]

        with open(sc_path, "w") as f:
            yaml.dump(data, f)

        mock_search.return_value = []
        audit_res = audit_manuscript_terminology(str(self.root))
        self.assertEqual(audit_res["status"], "success")
        self.assertIn("report", audit_res)
        self.assertTrue(audit_res["audited_terms_count"] >= 1)

    def test_get_term_context(self):
        initialize_section_cards(str(self.root))
        sc_path = self.wc_dir / "section_cards.yaml"
        with open(sc_path) as f:
            data = yaml.safe_load(f)

        data["document"]["terminology"] = {
            "Quantization": {
                "definition": "Mapping continuous values to a finite set.",
                "variants": ["Model Quantization"],
                "avoid": ["Quantizing"],
            }
        }
        with open(sc_path, "w") as f:
            yaml.dump(data, f)

        res = get_term_context("Quantization", str(self.root))
        self.assertEqual(res["term"], "Quantization")
        self.assertEqual(res["match_type"], "canonical")
        self.assertIn("Mapping continuous values", res["definition"])

        # Test variant lookup
        res_var = get_term_context("Model Quantization", str(self.root))
        self.assertEqual(res_var["term"], "Quantization")
        self.assertEqual(res_var["match_type"], "variant")

        res_avoid = get_term_context("Quantizing", str(self.root))
        self.assertEqual(res_avoid["term"], "Quantization")
        self.assertEqual(res_avoid["match_type"], "avoid")
        self.assertIn("Quantization", res_avoid["consistency_guidance"])

        # Test unknown term
        res_unknown = get_term_context("NonExistentTerm", str(self.root))
        self.assertIsNone(res_unknown.get("definition"))

    def test_find_entry_files(self):
        entry_tex = self.root / "main.tex"
        entry_tex.write_text("\\documentclass{article}\n\\begin{document}\n\\end{document}\n")

        entries = find_entry_files(str(self.root))
        self.assertIn("main.tex", entries)

    def test_cards_scan_command(self):
        res = cards_scan_command(str(self.root))
        self.assertEqual(res["status"], "success")
        self.assertTrue((self.wc_dir / "cards.generated.yaml").exists())
        self.assertTrue((self.wc_dir / "cards.lock.json").exists())

    def test_cards_validate_command(self):
        cards_scan_command(str(self.root))
        res = cards_validate_command(str(self.root))
        self.assertEqual(res["status"], "success")
        self.assertIn("warnings", res)

    def test_cards_update_command(self):
        cards_scan_command(str(self.root))
        res = cards_update_command(str(self.root), changed_only=False)
        self.assertEqual(res["status"], "success")

    @patch("builtins.input", return_value="y")
    def test_cards_review_command(self, mock_input):
        cards_scan_command(str(self.root))
        res = cards_review_command(str(self.root))
        self.assertEqual(res["status"], "success")

    @patch("writing_context_rtfm.features.cards_infer_command")
    def test_cards_build_and_rebuild_command(self, mock_infer):
        mock_infer.return_value = {"status": "success", "inferred": ["section_intro"]}
        cards_scan_command(str(self.root))

        build_res = cards_build_command(str(self.root), review=False)
        self.assertEqual(build_res["status"], "success")

        rebuild_res = cards_rebuild_command(str(self.root), review=False)
        self.assertEqual(rebuild_res["status"], "success")

    @patch("writing_context_rtfm.semantic_extractor.extract_semantic_metadata")
    def test_cards_infer_command(self, mock_extract):

        mock_extract.return_value = {
            "rhetorical_role": "introductory",
            "purpose": "Introduce the study and methods.",
            "key_terms": [{"value": "Quantization", "confidence": 0.95}],
            "facts": [{"value": "Model has 7B parameters", "type": "numeric_constant", "confidence": 1.0}],
            "constraints": [{"value": "Do not extrapolate beyond testing domain", "type": "boundary", "confidence": 0.85}],
        }

        cards_scan_command(str(self.root))
        res = cards_infer_command(str(self.root), force=True)
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["inferred"] >= 1)
