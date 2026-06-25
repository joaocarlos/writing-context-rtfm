import unittest
from pathlib import Path
import json
import shutil
import tempfile
import yaml
from unittest.mock import patch, MagicMock
from writing_context_rtfm.cli import cards_command


class TestCardsCLI(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.wc_dir = self.test_dir / ".writing-context"
        self.wc_dir.mkdir()

        # Write config.yaml
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

        # Write a simple tex file to scan
        self.main_tex = self.test_dir / "main.tex"
        self.main_tex.write_text(
            r"""
            \documentclass{article}
            \begin{document}
            \section{Introduction}
            Intro text.
            \end{document}
            """,
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_cli_cards_scan(self):
        # Mock CLI args
        args = MagicMock()
        args.subcommand = "scan"
        args.project_root = str(self.test_dir)

        # We will capture stdout
        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            cards_command(args)
        out = f.getvalue()

        # Should output status JSON
        parsed = json.loads(out)
        self.assertEqual(parsed["status"], "success")
        self.assertIn("section_introduction", parsed["added"])

        # Check generated files
        self.assertTrue((self.wc_dir / "cards.generated.yaml").exists())
        self.assertTrue((self.wc_dir / "cards.lock.json").exists())
        self.assertTrue((self.wc_dir / "cards.overrides.yaml.example").exists())

    @patch("writing_context_rtfm.semantic_extractor.get_api_key")
    def test_cli_cards_infer_missing_key(self, mock_get_key):
        mock_get_key.return_value = None

        # Run scan first
        args_scan = MagicMock()
        args_scan.subcommand = "scan"
        args_scan.project_root = str(self.test_dir)
        cards_command(args_scan)

        # Now run infer without key
        args_infer = MagicMock()
        args_infer.subcommand = "infer"
        args_infer.project_root = str(self.test_dir)
        args_infer.force = False

        import sys
        with patch.object(sys, "exit") as mock_exit:
            import io
            from contextlib import redirect_stdout
            f = io.StringIO()
            with redirect_stdout(f):
                cards_command(args_infer)
            out = f.getvalue()
            mock_exit.assert_called_once_with(1)

        self.assertIn("OpenAI API key not found", out)

    def test_cli_cards_validate(self):
        # Run scan first
        args_scan = MagicMock()
        args_scan.subcommand = "scan"
        args_scan.project_root = str(self.test_dir)
        cards_command(args_scan)

        # Run validate
        args_val = MagicMock()
        args_val.subcommand = "validate"
        args_val.project_root = str(self.test_dir)

        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            cards_command(args_val)
        out = f.getvalue()
        parsed = json.loads(out)
        self.assertEqual(parsed["status"], "success")
        self.assertEqual(parsed["stale_count"], 0)
