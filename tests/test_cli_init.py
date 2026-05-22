import unittest
import json
import tempfile
from pathlib import Path
from dataclasses import dataclass

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
        self.assertTrue((self.project_root / ".writing-context" / "section_cards.yaml").exists())
        
        # Check .gitignore
        gitignore = self.project_root / ".gitignore"
        self.assertTrue(gitignore.exists())
        self.assertIn(".writing-context/context_cache.sqlite", gitignore.read_text())
        
        # Check .mcp.json (should default to writing-context-rtfm command since uv.lock is not present)
        mcp_json = self.project_root / ".mcp.json"
        self.assertTrue(mcp_json.exists())
        mcp_data = json.loads(mcp_json.read_text())
        self.assertIn("writing-context-rtfm", mcp_data["mcpServers"])
        self.assertEqual(mcp_data["mcpServers"]["writing-context-rtfm"]["command"], "writing-context-rtfm")

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
        self.assertEqual(mcp_data["mcpServers"]["writing-context-rtfm"]["args"], ["run", "writing-context-rtfm", "serve"])

    def test_init_mcp_json_preserves_existing_servers(self):
        mcp_json = self.project_root / ".mcp.json"
        existing_data = {
            "mcpServers": {
                "other-server": {
                    "command": "node",
                    "args": ["index.js"]
                }
            }
        }
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
            content_mod = content.replace("1. **Always retrieve context first**", "1. **MODIFIED RULE**")
            md_file.write_text(content_mod)
            
            init_command(args)
            new_content = md_file.read_text()
            self.assertIn("1. **Always retrieve context first**", new_content)
            self.assertNotIn("1. **MODIFIED RULE**", new_content)

if __name__ == '__main__':
    unittest.main()
