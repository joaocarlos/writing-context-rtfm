import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from writing_context_rtfm.cli import (
    _update_claude_settings,
    _update_codex_config,
    _update_gitignore,
    _update_markdown_rules,
    _update_mcp_json,
    cache_command,
    cleanup_command,
    get_term_command,
    init_cards_command,
    init_db_command,
    inspect_target_command,
    pack_command,
    proofread_pack_command,
    show_graph_command,
    sync_command,
)


class TestCLIDeepCoverage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()

        # Create basic directory structure
        self.wc_dir = self.root / ".writing-context"
        self.wc_dir.mkdir(parents=True, exist_ok=True)

        self.config_yaml = self.wc_dir / "config.yaml"
        self.config_yaml.write_text(
            f"""
version: 1
rtfm:
  corpus: test_corpus
  project_root: {self.root}
cache:
  path: {self.wc_dir / "cache.sqlite"}
section_cards:
  path: {self.wc_dir / "section_cards.yaml"}
""",
            encoding="utf-8",
        )

        self.sc_yaml = self.wc_dir / "section_cards.yaml"
        self.sc_yaml.write_text(
            """
version: 1
document:
  title: Test Doc
  thesis: Main Thesis
  terminology:
    AI:
      definition: Artificial Intelligence
sections:
  section_intro:
    title: Introduction
    path: intro.tex
    role: Introduce topic
    key_terms: ["AI"]
""",
            encoding="utf-8",
        )

        self.intro_tex = self.root / "intro.tex"
        self.intro_tex.write_text(
            "\\section{Introduction}\\label{sec:intro}\n"
            "This paper discusses AI and deep learning.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_update_gitignore(self):
        _update_gitignore(self.root)
        self.assertTrue((self.root / ".gitignore").exists())
        # Run again to test idempotency
        _update_gitignore(self.root)

    def test_update_mcp_json(self):
        _update_mcp_json(self.root)
        self.assertTrue((self.root / ".mcp.json").exists())
        # Run again with existing file
        _update_mcp_json(self.root)

    def test_update_markdown_rules(self):
        _update_markdown_rules(self.root, "AGENTS.md", "Agent Guidelines")
        self.assertTrue((self.root / "AGENTS.md").exists())
        # Update again
        _update_markdown_rules(self.root, "AGENTS.md", "Agent Guidelines")

    def test_update_claude_settings(self):
        _update_claude_settings(self.root)
        self.assertTrue((self.root / ".claude" / "settings.json").exists())

    @patch("writing_context_rtfm.cli.Path.home")
    def test_update_codex_config(self, mock_home):
        mock_home.return_value = self.root
        codex_dir = self.root / ".codex"
        codex_dir.mkdir()
        config_file = codex_dir / "config.toml"
        config_file.write_text(
            '[mcp_servers.writing-context-rtfm]\ncommand = "/old/.venv/bin/writing-context-rtfm"\n',
            encoding="utf-8",
        )

        _update_codex_config()
        self.assertTrue(config_file.exists())
        self.assertNotIn("/old/.venv", config_file.read_text(encoding="utf-8"))

    @patch("writing_context_rtfm.cli.RTFMAdapter.sync")
    def test_sync_command(self, mock_sync):
        mock_sync.return_value = None
        args = argparse.Namespace(project_root=str(self.root), path=".", corpus=None)
        f = io.StringIO()
        with redirect_stdout(f):
            sync_command(args)
        out = f.getvalue()
        self.assertIn("Sync completed successfully", out)

    def test_init_cards_command(self):
        args = argparse.Namespace(project_root=str(self.root))
        f = io.StringIO()
        with redirect_stdout(f):
            init_cards_command(args)
        out = f.getvalue()
        self.assertIn("status", out)

    def test_init_db_command(self):
        args = argparse.Namespace(project_root=str(self.root))
        f = io.StringIO()
        with redirect_stdout(f):
            init_db_command(args)
        out = f.getvalue()
        self.assertIn("Initialized database", out)

    def test_cache_command_stats_and_clear(self):
        # stats
        args_stats = argparse.Namespace(project_root=str(self.root), cache_action="stats")
        f = io.StringIO()
        with redirect_stdout(f):
            cache_command(args_stats)
        self.assertIn("Cache location", f.getvalue())

        # clear
        args_clear = argparse.Namespace(project_root=str(self.root), cache_action="clear")
        f = io.StringIO()
        with redirect_stdout(f):
            cache_command(args_clear)
        self.assertIn("Cache cleared successfully", f.getvalue())

    @patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter.search")
    def test_pack_command(self, mock_search):
        mock_search.return_value = []
        args = argparse.Namespace(
            task="Write intro",
            target="section_intro",
            budget=2000,
            must_consider=[],
            task_type=None,
            pack_mode=None,
            json=True,
            project_root=str(self.root),
            line_start=None,
            line_end=None,
            strict_budget=False,
            role_budgets=None,
        )
        f = io.StringIO()
        with redirect_stdout(f):
            pack_command(args)
        out = f.getvalue()
        data = json.loads(out)
        self.assertIn("status", data)

    def test_proofread_pack_command(self):
        args = argparse.Namespace(
            target_file=str(self.intro_tex),
            line_start=1,
            line_end=2,
            mode="surface",
            strictness="moderate",
            max_tokens=1000,
            json=True,
            project_root=str(self.root),
        )
        f = io.StringIO()
        with redirect_stdout(f):
            proofread_pack_command(args)
        out = f.getvalue()
        data = json.loads(out)
        self.assertEqual(data["status"], "complete")

    def test_get_term_command(self):
        args = argparse.Namespace(
            term="AI",
            json=True,
            project_root=str(self.root),
        )
        f = io.StringIO()
        with redirect_stdout(f):
            get_term_command(args)
        data = json.loads(f.getvalue())
        self.assertEqual(data["term"], "AI")

    def test_inspect_target_command(self):
        args = argparse.Namespace(
            target="section_intro",
            project_root=str(self.root),
        )
        f = io.StringIO()
        with redirect_stdout(f):
            inspect_target_command(args)
        out = f.getvalue()
        self.assertIn("Target Section ID: section_intro", out)
        self.assertIn("Title:             Introduction", out)

    def test_show_graph_command(self):
        args = argparse.Namespace(
            project_root=str(self.root),
        )
        f = io.StringIO()
        with redirect_stdout(f):
            show_graph_command(args)
        out = f.getvalue()
        self.assertIn("LaTeX Reference Graph", out)

    def test_cleanup_command(self):
        args = argparse.Namespace(
            project_root=str(self.root),
        )
        f = io.StringIO()
        with redirect_stdout(f):
            cleanup_command(args)
        out = f.getvalue()
        self.assertIn("active_pids.json not found", out)
