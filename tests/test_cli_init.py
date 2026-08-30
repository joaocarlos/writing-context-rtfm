import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

import yaml

from writing_context_rtfm.cli import init_command


@dataclass
class MockArgs:
    project_root: str


class TestCliInit(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_init_creates_missing_files(self):
        args = MockArgs(project_root=str(self.project_root))
        init_command(args)

        # Check standard config yaml/section cards
        self.assertTrue((self.project_root / ".writing-context" / "config.yaml").exists())
        self.assertTrue(
            (self.project_root / ".writing-context" / "cards.overrides.yaml.example").exists()
        )
        config_data = yaml.safe_load(
            (self.project_root / ".writing-context" / "config.yaml").read_text()
        )
        zotero_extra = config_data["providers"]["zotero"]["extra"]
        self.assertEqual(zotero_extra["library_name"], "My Library")
        self.assertEqual(zotero_extra["collections"], [])
        self.assertTrue(zotero_extra["include_subcollections"])

        # Check .gitignore
        gitignore = self.project_root / ".gitignore"
        self.assertTrue(gitignore.exists())
        self.assertIn(".writing-context/context_cache.sqlite", gitignore.read_text())

        # Check .mcp.json (should default to writing-context-rtfm command since uv.lock is not present)
        mcp_json = self.project_root / ".mcp.json"
        self.assertTrue(mcp_json.exists())
        mcp_data = json.loads(mcp_json.read_text())
        self.assertIn("writing-context-rtfm", mcp_data["mcpServers"])
        self.assertEqual(
            mcp_data["mcpServers"]["writing-context-rtfm"]["command"], "writing-context-rtfm"
        )

        # Check CLAUDE.md, AGENTS.md, GEMINI.md
        for name in ["CLAUDE.md", "AGENTS.md", "GEMINI.md"]:
            md_file = self.project_root / name
            self.assertTrue(md_file.exists())
            content = md_file.read_text()
            self.assertIn("<!-- writing-context-rtfm MCP tools -->", content)
            self.assertIn("## Agent Rules of Thumb for Writing Context", content)
            self.assertIn("<!-- end writing-context-rtfm MCP tools -->", content)

    def test_init_appends_gitignore_properly(self):
        # Create pre-existing gitignore with some rules
        gitignore = self.project_root / ".gitignore"
        gitignore.write_text("*.log\n/node_modules/\n")

        args = MockArgs(project_root=str(self.project_root))
        init_command(args)

        content = gitignore.read_text()
        self.assertTrue(content.startswith("*.log\n/node_modules/\n"))
        self.assertIn(".writing-context/context_cache.sqlite", content)

        # Running init_command again should not duplicate it
        init_command(args)
        new_content = gitignore.read_text()
        self.assertEqual(content, new_content)

    def test_init_with_existing_gitignore_ignored_patterns(self):
        # Case where a broader pattern is ignored in gitignore
        for pattern in [".writing-context/*.sqlite", ".writing-context/", ".writing-context/*"]:
            with self.subTest(pattern=pattern):
                # Clean root
                temp_dir = tempfile.TemporaryDirectory()
                root = Path(temp_dir.name).resolve()
                gitignore = root / ".gitignore"
                gitignore.write_text(f"*.log\n{pattern}\n")

                args = MockArgs(project_root=str(root))
                init_command(args)

                content = gitignore.read_text()
                # Should not append cache database because it's already ignored by the broader pattern
                self.assertNotIn(".writing-context/context_cache.sqlite", content.splitlines())
                temp_dir.cleanup()

    def test_init_mcp_json_with_uv_lock(self):
        # Create uv.lock in the root
        (self.project_root / "uv.lock").touch()

        args = MockArgs(project_root=str(self.project_root))
        init_command(args)

        mcp_json = self.project_root / ".mcp.json"
        mcp_data = json.loads(mcp_json.read_text())
        self.assertEqual(mcp_data["mcpServers"]["writing-context-rtfm"]["command"], "uv")
        self.assertEqual(
            mcp_data["mcpServers"]["writing-context-rtfm"]["args"],
            ["run", "writing-context-rtfm", "serve"],
        )

    def test_init_mcp_json_preserves_existing_servers(self):
        mcp_json = self.project_root / ".mcp.json"
        existing_data = {"mcpServers": {"other-server": {"command": "node", "args": ["index.js"]}}}
        mcp_json.write_text(json.dumps(existing_data))

        args = MockArgs(project_root=str(self.project_root))
        init_command(args)

        mcp_data = json.loads(mcp_json.read_text())
        self.assertIn("other-server", mcp_data["mcpServers"])
        self.assertIn("writing-context-rtfm", mcp_data["mcpServers"])
        self.assertEqual(mcp_data["mcpServers"]["other-server"]["command"], "node")

    def test_init_updates_markdown_files_with_anchors(self):
        # Pre-existing markdown files
        for name in ["CLAUDE.md", "AGENTS.md", "GEMINI.md"]:
            md_file = self.project_root / name
            md_file.write_text("# Existing Header\n\nSome introductory text.\n")

        args = MockArgs(project_root=str(self.project_root))
        init_command(args)

        for name in ["CLAUDE.md", "AGENTS.md", "GEMINI.md"]:
            md_file = self.project_root / name
            content = md_file.read_text()
            self.assertTrue(content.startswith("# Existing Header\n\nSome introductory text.\n"))
            self.assertIn("<!-- writing-context-rtfm MCP tools -->", content)
            self.assertIn("## Agent Rules of Thumb for Writing Context", content)
            self.assertIn("<!-- end writing-context-rtfm MCP tools -->", content)

            # Running again should update the anchored block in-place
            # Let's verify by replacing a part of rules inside anchor and running init again
            content_mod = content.replace(
                "1. **Retrieve Curated Context First**", "1. **MODIFIED RULE**"
            )
            md_file.write_text(content_mod)

            init_command(args)
            new_content = md_file.read_text()
            self.assertIn("1. **Retrieve Curated Context First**", new_content)
            self.assertNotIn("1. **MODIFIED RULE**", new_content)

    def test_init_creates_claude_settings_with_hooks(self):
        args = MockArgs(project_root=str(self.project_root))

        # 1. Run init on empty project
        init_command(args)

        settings_file = self.project_root / ".claude" / "settings.json"
        self.assertTrue(settings_file.exists())

        data = json.loads(settings_file.read_text(encoding="utf-8"))
        self.assertIn("hooks", data)
        self.assertIn("PostToolUse", data["hooks"])

        hooks_list = data["hooks"]["PostToolUse"]
        self.assertEqual(len(hooks_list), 1)
        self.assertIn("write_to_file", hooks_list[0]["matcher"])

        inner_hook = hooks_list[0]["hooks"][0]
        self.assertEqual(inner_hook["type"], "mcp_tool")
        self.assertEqual(inner_hook["server"], "writing-context-rtfm")
        self.assertEqual(inner_hook["tool"], "refresh_index")

        # 2. Run again to test idempotency
        init_command(args)
        data = json.loads(settings_file.read_text(encoding="utf-8"))
        self.assertEqual(len(data["hooks"]["PostToolUse"]), 1)

        # 3. Test preservation of pre-existing hooks/keys
        settings_file.unlink()  # reset
        pre_existing = {
            "permissions": {"allow": ["bash"]},
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "git_commit",
                        "hooks": [{"type": "command", "command": "echo hello"}],
                    }
                ]
            },
        }
        (self.project_root / ".claude").mkdir(exist_ok=True)
        settings_file.write_text(json.dumps(pre_existing), encoding="utf-8")

        init_command(args)
        data = json.loads(settings_file.read_text(encoding="utf-8"))

        # Verify enabledMcpjsonServers
        self.assertIn("enabledMcpjsonServers", data)
        self.assertIn("writing-context-rtfm", data["enabledMcpjsonServers"])

        # Verify PostToolUse inner hook uses 'input'
        inner_hook = (
            data["hooks"]["PostToolUse"][1]["hooks"][0]
            if len(data["hooks"]["PostToolUse"]) > 1
            else data["hooks"]["PostToolUse"][0]["hooks"][0]
        )
        self.assertIn("input", inner_hook)

        # Verify SessionEnd structure
        self.assertIn("SessionEnd", data["hooks"])
        session_end = data["hooks"]["SessionEnd"]
        self.assertEqual(len(session_end), 1)
        self.assertIn("hooks", session_end[0])
        self.assertEqual(session_end[0]["hooks"][0]["type"], "command")

        # 4. Test repair of legacy invalid flat SessionEnd hooks
        settings_file.unlink()
        invalid_legacy = {
            "hooks": {
                "SessionEnd": [{"type": "command", "command": "writing-context-rtfm cleanup"}]
            }
        }
        settings_file.write_text(json.dumps(invalid_legacy), encoding="utf-8")
        init_command(args)

        repaired_data = json.loads(settings_file.read_text(encoding="utf-8"))
        repaired_session_end = repaired_data["hooks"]["SessionEnd"]
        self.assertEqual(len(repaired_session_end), 1)
        self.assertIn("hooks", repaired_session_end[0])
        self.assertEqual(
            repaired_session_end[0]["hooks"][0]["command"], "writing-context-rtfm cleanup"
        )


if __name__ == "__main__":
    unittest.main()
