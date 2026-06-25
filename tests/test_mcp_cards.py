"""Tests for section card MCP tools."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

import writing_context_rtfm.server as server
from writing_context_rtfm.server import process_message


class TestMCPCards(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.wc_dir = self.test_dir / ".writing-context"
        self.wc_dir.mkdir()

        # Set WORKSPACE_ROOT dynamically to temp dir
        self.orig_root = server.WORKSPACE_ROOT
        self.orig_cache = server._RUNTIME_CACHE
        server.WORKSPACE_ROOT = self.test_dir
        server._RUNTIME_CACHE = None

        # Write initial generated cards
        self.gen_yaml_content = {
            "version": 2,
            "document": {
                "title": "Test Paper",
            },
            "sections": {
                "section_intro": {
                    "identity": {
                        "source": "sections/intro.tex",
                        "selector": "sec:intro",
                        "content_hash": "hash123",
                    },
                    "structure": {
                        "title": "Introduction",
                        "parent": "document_main",
                        "level": 2,
                        "children": [],
                    },
                    "purpose": {
                        "value": "Introduce the topic",
                        "confidence": 0.95,
                        "status": "generated",
                        "provenance": ["sections/intro.tex"],
                    },
                    "key_terms": [
                        {
                            "value": "FLC",
                            "confidence": 0.95,
                            "status": "generated",
                            "evidence": "Rule-based acronym extraction",
                        },
                        {
                            "value": "ML",
                            "confidence": 0.8,
                            "status": "generated",
                        },
                    ],
                    "facts": [
                        {
                            "id": "fact_0",
                            "value": "The system is robust",
                            "type": "semantic_claim",
                            "confidence": 0.85,
                            "status": "generated",
                            "provenance": ["sections/intro.tex"],
                        }
                    ],
                    "constraints": [
                        {
                            "id": "constraint_0",
                            "value": "Do not exceed page limit",
                            "type": "rhetorical_boundary",
                            "confidence": 0.9,
                            "status": "generated",
                        }
                    ],
                }
            },
        }

        self.overrides_content = {
            "version": 2,
            "document": {
                "title": "Test Paper",
                "thesis": "",
                "writing_style": {"tone": "academic, formal", "avoid": []},
                "terminology": {},
            },
            "sections": {},
        }

        self.lock_content = {
            "generation_version": 2,
            "extractor_version": 1,
            "sections": {
                "section_intro": {
                    "content_hash": "hash123",
                    "decisions": {},
                    "stale_fields": [],
                }
            },
        }

        self.save_files()

    def save_files(self):
        with open(self.wc_dir / "cards.generated.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(self.gen_yaml_content, f, sort_keys=False)
        with open(self.wc_dir / "cards.overrides.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(self.overrides_content, f, sort_keys=False)
        with open(self.wc_dir / "cards.lock.json", "w", encoding="utf-8") as f:
            json.dump(self.lock_content, f, indent=2)

    def tearDown(self):
        # Restore server workspace root and cache
        server.WORKSPACE_ROOT = self.orig_root
        server._RUNTIME_CACHE = self.orig_cache
        shutil.rmtree(self.test_dir)

    def test_tools_list_contains_card_tools(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        res = json.loads(process_message(json.dumps(req)))
        tools = res["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("review_card_candidates", tool_names)
        self.assertIn("accept_card_candidate", tool_names)
        self.assertIn("reject_card_candidate", tool_names)
        self.assertIn("edit_card_field", tool_names)
        self.assertIn("explain_card_candidate", tool_names)

    def test_review_card_candidates(self):
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "review_card_candidates",
                "arguments": {"project_root": str(self.test_dir)},
            },
        }
        res = json.loads(process_message(json.dumps(req)))
        self.assertNotIn("isError", res.get("result", {}))
        payload = json.loads(res["result"]["content"][0]["text"])
        candidates = payload["candidates"]
        self.assertEqual(len(candidates), 5)  # 1 purpose, 2 key_terms, 1 facts, 1 constraints

        fields = [c["field"] for c in candidates]
        self.assertIn("purpose", fields)
        self.assertIn("key_terms", fields)
        self.assertIn("facts", fields)
        self.assertIn("constraints", fields)

    def test_accept_card_candidate(self):
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "accept_card_candidate",
                "arguments": {
                    "section_id": "section_intro",
                    "field": "key_terms",
                    "value": "FLC",
                    "project_root": str(self.test_dir),
                },
            },
        }
        res = json.loads(process_message(json.dumps(req)))
        self.assertNotIn("isError", res.get("result", {}))

        # Check files updated
        with open(self.wc_dir / "cards.generated.yaml", encoding="utf-8") as f:
            gen = yaml.safe_load(f)
        self.assertEqual(gen["sections"]["section_intro"]["key_terms"][0]["status"], "accepted")

        with open(self.wc_dir / "cards.overrides.yaml", encoding="utf-8") as f:
            over = yaml.safe_load(f)
        self.assertIn("FLC", over["sections"]["section_intro"]["key_terms"])

        with open(self.wc_dir / "cards.lock.json", encoding="utf-8") as f:
            lock = json.load(f)
        self.assertEqual(lock["sections"]["section_intro"]["decisions"]["key_terms:FLC"], "accepted")

    def test_reject_card_candidate(self):
        req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "reject_card_candidate",
                "arguments": {
                    "section_id": "section_intro",
                    "field": "facts",
                    "value": "The system is robust",
                    "project_root": str(self.test_dir),
                },
            },
        }
        res = json.loads(process_message(json.dumps(req)))
        self.assertNotIn("isError", res.get("result", {}))

        # Check files updated
        with open(self.wc_dir / "cards.generated.yaml", encoding="utf-8") as f:
            gen = yaml.safe_load(f)
        self.assertEqual(gen["sections"]["section_intro"]["facts"][0]["status"], "rejected")

        with open(self.wc_dir / "cards.lock.json", encoding="utf-8") as f:
            lock = json.load(f)
        self.assertEqual(
            lock["sections"]["section_intro"]["decisions"]["facts:The system is robust"], "rejected"
        )

    def test_edit_card_field(self):
        req = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "edit_card_field",
                "arguments": {
                    "section_id": "section_intro",
                    "field": "title",
                    "value": "Custom Intro Title",
                    "project_root": str(self.test_dir),
                },
            },
        }
        res = json.loads(process_message(json.dumps(req)))
        self.assertNotIn("isError", res.get("result", {}))

        with open(self.wc_dir / "cards.overrides.yaml", encoding="utf-8") as f:
            over = yaml.safe_load(f)
        self.assertEqual(over["sections"]["section_intro"]["title"], "Custom Intro Title")

        # Test deletion
        req_delete = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "edit_card_field",
                "arguments": {
                    "section_id": "section_intro",
                    "field": "title",
                    "value": None,
                    "project_root": str(self.test_dir),
                },
            },
        }
        res_del = json.loads(process_message(json.dumps(req_delete)))
        self.assertNotIn("isError", res_del.get("result", {}))

        with open(self.wc_dir / "cards.overrides.yaml", encoding="utf-8") as f:
            over2 = yaml.safe_load(f)
        self.assertNotIn("title", over2["sections"]["section_intro"])

    def test_explain_card_candidate(self):
        req = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "explain_card_candidate",
                "arguments": {
                    "section_id": "section_intro",
                    "field": "key_terms",
                    "value": "FLC",
                    "project_root": str(self.test_dir),
                },
            },
        }
        res = json.loads(process_message(json.dumps(req)))
        self.assertNotIn("isError", res.get("result", {}))
        payload = json.loads(res["result"]["content"][0]["text"])

        self.assertEqual(payload["confidence"], 0.95)
        self.assertEqual(payload["evidence"], "Rule-based acronym extraction")
        self.assertIn("Rule-based acronym extraction", payload["explanation"])


if __name__ == "__main__":
    unittest.main()
