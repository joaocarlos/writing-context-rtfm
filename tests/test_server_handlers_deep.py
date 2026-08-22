import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from writing_context_rtfm.server import (
    handle_accept_card_candidate,
    handle_audit_manuscript_terminology,
    handle_edit_card_field,
    handle_explain_card_candidate,
    handle_get_manuscript_reference_graph,
    handle_get_proofreading_context_pack,
    handle_get_term_context,
    handle_get_writing_context_pack,
    handle_initialize_section_cards,
    handle_refresh_index,
    handle_reject_card_candidate,
    handle_request_more_context,
    handle_review_card_candidates,
    handle_submit_generation_feedback,
    process_message,
)


class TestServerHandlersDeep(unittest.TestCase):
    def setUp(self):
        from writing_context_rtfm import server

        self.orig_ws = server.WORKSPACE_ROOT
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        server.WORKSPACE_ROOT = self.root

        self.wc_dir = self.root / ".writing-context"
        self.wc_dir.mkdir(parents=True, exist_ok=True)

        config_path = self.wc_dir / "config.yaml"
        config_path.write_text(
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

        self.sc_path = self.wc_dir / "section_cards.yaml"
        self.sc_path.write_text(
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

        # Create generated cards and lock file
        self.gen_cards = self.wc_dir / "cards.generated.yaml"
        self.gen_cards.write_text(
            yaml.safe_dump(
                {
                    "version": 2,
                    "document": {
                        "title": "Test Doc",
                        "terminology": {
                            "AI": {"definition": "Artificial Intelligence"}
                        },
                    },
                    "sections": {
                        "section_intro": {
                            "structure": {"title": "Introduction"},
                            "identity": {"source": "intro.tex"},
                            "purpose": {"value": "Introduce paper"},
                            "key_terms": [{"value": "AI", "status": "generated"}],
                            "facts": [{"value": "Fact A", "status": "generated"}],
                            "constraints": [
                                {
                                    "value": "Avoid hype",
                                    "type": "terminology_avoidance",
                                    "status": "generated",
                                }
                            ],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        self.lock_json = self.wc_dir / "cards.lock.json"
        self.lock_json.write_text(
            json.dumps(
                {
                    "version": 1,
                    "sections": {
                        "section_intro": {
                            "fields": {
                                "purpose": {
                                    "source": "inference",
                                    "confidence": 0.9,
                                    "rationale": "Clear purpose statement in paragraph 1",
                                }
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        from writing_context_rtfm import server

        server.WORKSPACE_ROOT = self.orig_ws
        self.temp_dir.cleanup()

    @patch("writing_context_rtfm.server.RTFMAdapter.sync")
    def test_handle_refresh_index(self, mock_sync):
        mock_sync.return_value = None
        res = handle_refresh_index({"project_root": str(self.root)})
        data = json.loads(res["content"][0]["text"])
        self.assertEqual(data["status"], "ok")

    def test_handle_review_card_candidates(self):
        res = handle_review_card_candidates({"project_root": str(self.root)})
        data = json.loads(res["content"][0]["text"])
        self.assertIn("candidates", data)
        self.assertTrue(len(data["candidates"]) >= 1)

    def test_handle_accept_card_candidate(self):
        res = handle_accept_card_candidate(
            {
                "project_root": str(self.root),
                "section_id": "section_intro",
                "field": "key_terms",
                "value": "AI",
            }
        )
        data = json.loads(res["content"][0]["text"])
        self.assertEqual(data["status"], "accepted")

    def test_handle_reject_card_candidate(self):
        res = handle_reject_card_candidate(
            {
                "project_root": str(self.root),
                "section_id": "section_intro",
                "field": "facts",
                "value": "Fact A",
            }
        )
        data = json.loads(res["content"][0]["text"])
        self.assertEqual(data["status"], "rejected")

    def test_handle_edit_card_field(self):
        res = handle_edit_card_field(
            {
                "project_root": str(self.root),
                "section_id": "section_intro",
                "field": "purpose",
                "new_value": "Manually edited purpose",
            }
        )
        data = json.loads(res["content"][0]["text"])
        self.assertEqual(data["status"], "updated")

    def test_handle_explain_card_candidate(self):
        # Update generated purpose to include confidence and evidence
        with open(self.gen_cards) as f:
            data = yaml.safe_load(f)
        data["sections"]["section_intro"]["purpose"] = {
            "value": "Introduce paper",
            "confidence": 0.9,
            "evidence": "Clear intro in paragraph 1",
        }
        with open(self.gen_cards, "w") as f:
            yaml.safe_dump(data, f)

        res = handle_explain_card_candidate(
            {
                "project_root": str(self.root),
                "section_id": "section_intro",
                "field": "purpose",
                "value": "Introduce paper",
            }
        )
        data = json.loads(res["content"][0]["text"])
        self.assertEqual(data["confidence"], 0.9)
        self.assertIn("Clear intro in paragraph 1", data["evidence"])

    def test_handle_explain_card_candidate_missing_args(self):
        res = handle_explain_card_candidate({"project_root": str(self.root)})
        data = json.loads(res["content"][0]["text"])
        self.assertIn("error_code", data)

    @patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter.search")
    def test_handle_get_writing_context_pack(self, mock_search):
        mock_search.return_value = []
        res = handle_get_writing_context_pack({"task": "Write intro", "target": "section_intro"})
        data = json.loads(res["content"][0]["text"])
        self.assertIn("status", data)

    @patch("writing_context_rtfm.server.RTFMAdapter.sync")
    @patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter.search")
    def test_handle_get_proofreading_context_pack(self, mock_search, mock_sync):
        mock_sync.return_value = None
        mock_search.return_value = []
        res = handle_get_proofreading_context_pack(
            {
                "target_file": str(self.intro_tex),
                "line_start": 1,
                "line_end": 2,
            }
        )
        data = json.loads(res["content"][0]["text"])
        self.assertEqual(data["status"], "complete")

    @patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter.search")
    def test_handle_terminology_and_graph_tools(self, mock_search):
        mock_search.return_value = []
        # 1. audit_manuscript_terminology
        res_audit = handle_audit_manuscript_terminology({"project_root": str(self.root)})
        data_audit = json.loads(res_audit["content"][0]["text"])
        self.assertIn("audited_terms_count", data_audit)

        # 2. get_term_context
        res_term = handle_get_term_context({"term": "AI", "project_root": str(self.root)})
        data_term = json.loads(res_term["content"][0]["text"])
        self.assertEqual(data_term["term"], "AI")

        # 3. get_manuscript_reference_graph
        res_graph = handle_get_manuscript_reference_graph({"project_root": str(self.root)})
        data_graph = json.loads(res_graph["content"][0]["text"])
        self.assertIn("files", data_graph)

        # 4. initialize_section_cards
        res_init = handle_initialize_section_cards({"project_root": str(self.root)})
        data_init = json.loads(res_init["content"][0]["text"])
        self.assertIn("status", data_init)

        # 5. request_more_context
        res_more = handle_request_more_context({"run_id": "nonexistent_run_id"})
        data_more = json.loads(res_more["content"][0]["text"])
        self.assertEqual(data_more["source_spans"], [])

        # 6. submit_generation_feedback
        from writing_context_rtfm.storage import ExtensionStore
        with ExtensionStore(str(self.wc_dir / "cache.sqlite")) as store:
            store.init_db()
            with store._connect() as conn:
                conn.cursor().execute(
                    "INSERT INTO context_pack_runs (run_id, task_hash, task, target, token_budget) VALUES (?, ?, ?, ?, ?)",
                    ("test_run", "thash", "task", "sec", 1000),
                )
                conn.commit()

        res_fb = handle_submit_generation_feedback(
            {"run_id": "test_run", "metric_name": "rating", "metric_value": 5.0, "metric_text": "Great context"}
        )
        data_fb = json.loads(res_fb["content"][0]["text"])
        self.assertEqual(data_fb["status"], "feedback_saved")

    def test_mcp_protocol_messages(self):
        # 1. prompts/list
        msg_prompts = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})
        res_prompts = json.loads(process_message(msg_prompts))
        self.assertIn("prompts", res_prompts["result"])

        # 2. resources/list and templates
        msg_res = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
        res_res = json.loads(process_message(msg_res))
        self.assertEqual(res_res["result"], {"resources": []})

        msg_tmpl = json.dumps(
            {"jsonrpc": "2.0", "id": 3, "method": "resources/templates/list"}
        )
        res_tmpl = json.loads(process_message(msg_tmpl))
        self.assertEqual(res_tmpl["result"], {"resourceTemplates": []})

        # 3. notifications/
        msg_notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(process_message(msg_notif))

        # 4. tools/call for review_card_candidates
        msg_call = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "review_card_candidates",
                    "arguments": {"project_root": str(self.root)},
                },
            }
        )
        res_call = json.loads(process_message(msg_call))
        self.assertIn("content", res_call["result"])
