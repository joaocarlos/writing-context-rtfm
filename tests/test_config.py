import unittest
from pathlib import Path

from writing_context_rtfm.config import load_config


class TestConfig(unittest.TestCase):
    def test_load_defaults_when_missing(self):
        config = load_config("nonexistent.yaml")
        self.assertEqual(config.version, 1)
        self.assertEqual(config.rtfm.corpus, "default")
        self.assertEqual(config.context.default_token_budget, 12000)

    def test_load_providers_valid(self):
        import tempfile

        import yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            wc_dir = Path(tmpdir) / ".writing-context"
            wc_dir.mkdir()
            config_yaml = wc_dir / "config.yaml"

            yaml_content = {
                "version": 1,
                "providers": {
                    "zotero": {
                        "enabled": True,
                        "mcp_server": {"command": "npx", "args": ["-y", "zotero-mcp"]},
                    }
                },
            }
            config_yaml.write_text(yaml.dump(yaml_content))

            config = load_config(tmpdir)
            self.assertTrue(config.providers["zotero"].enabled)
            self.assertEqual(config.providers["zotero"].mcp_server.command, "npx")
            self.assertEqual(config.providers["zotero"].mcp_server.args, ["-y", "zotero-mcp"])
            self.assertIsNone(config.providers["zotero"].sse_url)

    def test_load_providers_invalid_type(self):
        import tempfile

        import yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            wc_dir = Path(tmpdir) / ".writing-context"
            wc_dir.mkdir()
            config_yaml = wc_dir / "config.yaml"

            yaml_content = {"version": 1, "providers": {"zotero": "not-a-dict"}}
            config_yaml.write_text(yaml.dump(yaml_content))

            with self.assertRaises(TypeError):
                load_config(tmpdir)

    def test_load_providers_enabled_without_server_or_sse(self):
        import tempfile

        import yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            wc_dir = Path(tmpdir) / ".writing-context"
            wc_dir.mkdir()
            config_yaml = wc_dir / "config.yaml"

            yaml_content = {"version": 1, "providers": {"zotero": {"enabled": True}}}
            config_yaml.write_text(yaml.dump(yaml_content))

            with self.assertRaises(ValueError):
                load_config(tmpdir)

    def test_load_providers_with_headers(self):
        import tempfile

        import yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            wc_dir = Path(tmpdir) / ".writing-context"
            wc_dir.mkdir()
            config_yaml = wc_dir / "config.yaml"

            yaml_content = {
                "version": 1,
                "providers": {
                    "zotero": {
                        "enabled": True,
                        "sse_url": "https://api.zotero.org/mcp",
                        "headers": {
                            "Authorization": "Bearer token123",
                            "X-Custom-Header": "custom_val",
                        },
                    }
                },
            }
            config_yaml.write_text(yaml.dump(yaml_content))

            config = load_config(tmpdir)
            zotero_cfg = config.providers["zotero"]
            self.assertTrue(zotero_cfg.enabled)
            self.assertEqual(zotero_cfg.sse_url, "https://api.zotero.org/mcp")
            self.assertEqual(
                zotero_cfg.headers,
                {"Authorization": "Bearer token123", "X-Custom-Header": "custom_val"},
            )

    def test_load_providers_invalid_headers_type(self):
        import tempfile

        import yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            wc_dir = Path(tmpdir) / ".writing-context"
            wc_dir.mkdir()
            config_yaml = wc_dir / "config.yaml"

            yaml_content = {
                "version": 1,
                "providers": {
                    "zotero": {
                        "enabled": True,
                        "sse_url": "https://api.zotero.org/mcp",
                        "headers": "not-a-dict",
                    }
                },
            }
            config_yaml.write_text(yaml.dump(yaml_content))

            with self.assertRaises(TypeError):
                load_config(tmpdir)

    def test_load_providers_invalid_header_values(self):
        import tempfile

        import yaml

        with tempfile.TemporaryDirectory() as tmpdir:
            wc_dir = Path(tmpdir) / ".writing-context"
            wc_dir.mkdir()
            config_yaml = wc_dir / "config.yaml"

            yaml_content = {
                "version": 1,
                "providers": {
                    "zotero": {
                        "enabled": True,
                        "sse_url": "https://api.zotero.org/mcp",
                        "headers": {"Authorization": 12345},
                    }
                },
            }
            config_yaml.write_text(yaml.dump(yaml_content))

            with self.assertRaises(TypeError):
                load_config(tmpdir)


if __name__ == "__main__":
    unittest.main()
