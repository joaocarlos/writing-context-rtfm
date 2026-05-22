import os
import json
import unittest
import yaml
from pathlib import Path
from unittest.mock import patch, MagicMock

from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.features import initialize_section_cards, audit_manuscript_terminology
from writing_context_rtfm.server import process_message
from writing_context_rtfm.config import load_config

class TestNewFeatures(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_features_cache.sqlite"
        self.store = ExtensionStore(self.db_path)
        self.store.init_db()
        self.project_dir = Path("test_project_temp")
        self.project_dir.mkdir(exist_ok=True)
        
    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        # Clean up temporary project directory
        import shutil
        if self.project_dir.exists():
            shutil.rmtree(self.project_dir)

    def test_database_feedback_and_pagination(self):
        # 1. Store a pack with selected (1) and unselected (0) sources
        run_id = "test-pagination-run"
        run_data = {
            "task_hash": "hash1",
            "task": "write summary",
            "corpus": "test_corp",
            "token_budget": 1000,
            "config_hash": "cfg",
            "section_cards_hash": "sc",
            "rtfm_index_fingerprint": "print"
        }
        payload = {"task": "write summary", "estimated_tokens": 100}
        sources = [
            {"path": "selected.md", "line_start": 1, "line_end": 5, "score": 0.9, "reason": "R1", "query": "q", "selected": 1},
            {"path": "unselected.md", "line_start": 6, "line_end": 10, "score": 0.8, "reason": "R2", "query": "q", "selected": 0},
            {"path": "unselected2.md", "line_start": 11, "line_end": 15, "score": 0.7, "reason": "R3", "query": "q", "selected": 0}
        ]
        self.store.store_pack(run_id, run_data, payload, sources)

        # 2. Test get_more_context pagination
        more_context = self.store.get_more_context(run_id, limit=1)
        self.assertEqual(len(more_context), 1)
        self.assertEqual(more_context[0]["path"], "unselected.md")

        more_context_2 = self.store.get_more_context(run_id, limit=1)
        self.assertEqual(len(more_context_2), 1)
        self.assertEqual(more_context_2[0]["path"], "unselected2.md")

        # 3. Test submit_feedback
        self.store.submit_feedback(run_id, "helpfulness", 1.0, "Very accurate context")
        with self.store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT metric_name, metric_value, metric_text FROM evaluation_records WHERE run_id=?", (run_id,))
            row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["metric_name"], "helpfulness")
        self.assertEqual(row["metric_value"], 1.0)
        self.assertEqual(row["metric_text"], "Very accurate context")

    def test_initialize_section_cards(self):
        # Create a mock file structure
        (self.project_dir / "intro.md").write_text("# Intro")
        (self.project_dir / "methods.tex").write_text("Methods content")
        
        # Call initialization
        res = initialize_section_cards(str(self.project_dir))
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["total_sections"], 2)
        
        # Verify the generated section cards YAML
        yaml_path = self.project_dir / ".writing-context" / "section_cards.yaml"
        self.assertTrue(yaml_path.exists())
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f)
            
        sections = data["sections"]
        self.assertIn("section_intro", sections)
        self.assertIn("section_methods", sections)
        self.assertEqual(sections["section_intro"]["path"], "intro.md")

        self.assertIn("document", data)
        self.assertIn("terminology", data["document"])
        self.assertIn("sample_term", data["document"]["terminology"])
        sample = data["document"]["terminology"]["sample_term"]
        self.assertEqual(sample["definition"], "A sample technical term description.")
        self.assertEqual(sample["variants"], ["alternate phrasing 1"])
        self.assertEqual(sample["avoid"], ["deprecated variant"])

    def test_audit_manuscript_terminology(self):
        sc_dir = self.project_dir / ".writing-context"
        sc_dir.mkdir(exist_ok=True)
        sc_file = sc_dir / "section_cards.yaml"
        sc_content = {
            "version": 1,
            "document": {
                "title": "Mock Manuscript",
                "thesis": "Thesis",
                "writing_style": {"tone": "academic", "avoid": []}
            },
            "sections": {
                "section_intro": {
                    "title": "Intro",
                    "role": "Intro",
                    "path": "intro.md",
                    "key_terms": ["quantization", "micro-controller"],
                    "depends_on": []
                }
            }
        }
        with open(sc_file, "w") as f:
            yaml.safe_dump(sc_content, f)

        mock_config = MagicMock()
        mock_config.section_cards.path = ".writing-context/section_cards.yaml"
        mock_config.rtfm.corpus = "manuscript"

        mock_result = MagicMock()
        mock_result.path = str(self.project_dir / "intro.md")
        mock_result.line_start = 1
        mock_result.line_end = 2
        mock_result.snippet = "We use quantization on the micro-controller."

        with patch("writing_context_rtfm.config.load_config", return_value=mock_config), \
             patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter") as MockAdapter:
            mock_adapter = MockAdapter.return_value
            mock_adapter.search.return_value = [mock_result]

            res = audit_manuscript_terminology(str(self.project_dir))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["audited_terms_count"], 2)
            self.assertIn("quantization", res["report"])

    def test_mcp_new_routes(self):
        # 1. Test prompts/list
        req_list = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "prompts/list"
        }
        res_list = json.loads(process_message(json.dumps(req_list)))
        self.assertIn("prompts", res_list["result"])
        prompt_names = [p["name"] for p in res_list["result"]["prompts"]]
        self.assertIn("write_section", prompt_names)
        self.assertIn("proofread_section", prompt_names)

        # 2. Test prompts/get write_section
        with patch("writing_context_rtfm.server.RTFMAdapter") as MockAdapter, \
             patch("writing_context_rtfm.server.ExtensionStore") as MockStore:
            mock_adapter = MockAdapter.return_value
            mock_adapter.search.return_value = []
            mock_store = MockStore.return_value
            mock_store.get_cached_pack.return_value = None

            req_get_prompt = {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "prompts/get",
                "params": {
                    "name": "write_section",
                    "arguments": {
                        "task": "Write intro",
                        "target": "section_intro"
                    }
                }
            }
            res_prompt = json.loads(process_message(json.dumps(req_get_prompt)))
            self.assertIn("messages", res_prompt["result"])
            self.assertEqual(res_prompt["result"]["messages"][0]["role"], "user")
            self.assertIn("Task: Write intro", res_prompt["result"]["messages"][0]["content"]["text"])

        # 3. Test new tools routing
        with patch("writing_context_rtfm.server.initialize_section_cards", return_value={"status": "success"}) as mock_init:
            req_call_init = {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "initialize_section_cards",
                    "arguments": {"project_root": "."}
                }
            }
            res_call = json.loads(process_message(json.dumps(req_call_init)))
            self.assertNotIn("isError", res_call.get("result", {}))
            mock_init.assert_called_once_with(".")

    def test_config_robustness_errors(self):
        # 1. Setup temp config file directory
        config_dir = self.project_dir / ".writing-context"
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "config.yaml"

        # Test malformed YAML syntax
        config_file.write_text("invalid_yaml: [unclosed_list")
        with self.assertRaises(yaml.YAMLError):
            load_config(str(self.project_dir))

        # Test invalid data types for nested attributes (e.g. context: "string-instead-of-dict")
        config_file.write_text("version: 1\ncontext: 'string-instead-of-dict'")
        with self.assertRaises(TypeError):
            load_config(str(self.project_dir))

    def test_initialize_scaffolding_edge_cases(self):
        # 1. Test empty directory
        empty_dir = self.project_dir / "empty_dir"
        empty_dir.mkdir(exist_ok=True)
        res = initialize_section_cards(str(empty_dir))
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["total_sections"], 0)

        # 2. Test corrupted YAML fallback
        cards_dir = self.project_dir / ".writing-context"
        cards_dir.mkdir(exist_ok=True)
        cards_file = cards_dir / "section_cards.yaml"
        cards_file.write_text("corrupted:: indentation-error")
        (self.project_dir / "new_section.md").write_text("# New Content")

        # It should handle parse failure gracefully by resetting or bypassing
        res_corrupted = initialize_section_cards(str(self.project_dir))
        self.assertEqual(res_corrupted["status"], "success")
        self.assertGreater(res_corrupted["total_sections"], 0)

    def test_audit_terminology_special_characters(self):
        sc_dir = self.project_dir / ".writing-context"
        sc_dir.mkdir(exist_ok=True)
        sc_file = sc_dir / "section_cards.yaml"
        sc_content = {
            "version": 1,
            "document": {
                "title": "Mock",
                "thesis": "Thesis",
                "writing_style": {"tone": "academic", "avoid": []}
            },
            "sections": {
                "section_intro": {
                    "title": "Intro",
                    "role": "Intro",
                    "path": "intro.md",
                    "key_terms": ["qu*ant[i]zation?", "controller\\d+", "üñïçødè"],
                    "depends_on": []
                }
            }
        }
        with open(sc_file, "w") as f:
            yaml.safe_dump(sc_content, f)

        mock_config = MagicMock()
        mock_config.section_cards.path = ".writing-context/section_cards.yaml"
        mock_config.rtfm.corpus = "manuscript"

        with patch("writing_context_rtfm.config.load_config", return_value=mock_config), \
             patch("writing_context_rtfm.rtfm_adapter.RTFMAdapter") as MockAdapter:
            mock_adapter = MockAdapter.return_value
            mock_adapter.search.return_value = []

            # Safe regex search check
            res = audit_manuscript_terminology(str(self.project_dir))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["audited_terms_count"], 3)

    def test_pagination_edge_cases(self):
        # 1. Invalid run ID
        more_context = self.store.get_more_context("non-existent-uuid", limit=5)
        self.assertEqual(more_context, [])

        # 2. Setup run data with multiple candidates
        run_id = "paginated-run-id"
        run_data = {
            "task_hash": "hash2",
            "task": "write section",
            "corpus": "test_corp",
            "token_budget": 1000,
            "config_hash": "cfg",
            "section_cards_hash": "sc",
            "rtfm_index_fingerprint": "print"
        }
        payload = {"task": "write section", "estimated_tokens": 10}
        sources = [
            {"path": "s1.md", "line_start": 1, "line_end": 5, "score": 0.9, "reason": "R", "query": "q", "selected": 0},
            {"path": "s2.md", "line_start": 6, "line_end": 10, "score": 0.8, "reason": "R", "query": "q", "selected": 0}
        ]
        self.store.store_pack(run_id, run_data, payload, sources)

        # 3. Request with limit=0
        more_context_zero = self.store.get_more_context(run_id, limit=0)
        self.assertEqual(more_context_zero, [])

        # 4. Request with large limit
        more_context_all = self.store.get_more_context(run_id, limit=9999)
        self.assertEqual(len(more_context_all), 2)

        # 5. Repeated requests when exhausted
        more_context_empty = self.store.get_more_context(run_id, limit=5)
        self.assertEqual(more_context_empty, [])

    def test_feedback_loop_bounds(self):
        run_id = "feedback-run-id"
        run_data = {
            "task_hash": "hash3",
            "task": "write summary",
            "corpus": "test_corp",
            "token_budget": 1000,
            "config_hash": "cfg",
            "section_cards_hash": "sc",
            "rtfm_index_fingerprint": "print"
        }
        self.store.store_pack(run_id, run_data, {}, [])

        # Test extreme metric values and long strings
        self.store.submit_feedback(run_id, "extremely-long-metric-name" * 10, -999.0, "A" * 5000)
        with self.store._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT metric_value, metric_text FROM evaluation_records WHERE run_id=?", (run_id,))
            row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["metric_value"], -999.0)
        self.assertEqual(len(row["metric_text"]), 5000)

    def test_prompt_hydration_failures(self):
        # 1. Non-existent target section ID in write_section
        with patch("writing_context_rtfm.server.RTFMAdapter") as MockAdapter, \
             patch("writing_context_rtfm.server.ExtensionStore") as MockStore:
            mock_adapter = MockAdapter.return_value
            mock_adapter.search.return_value = []
            mock_store = MockStore.return_value
            mock_store.get_cached_pack.return_value = None

            req_get_prompt = {
                "jsonrpc": "2.0",
                "id": 20,
                "method": "prompts/get",
                "params": {
                    "name": "write_section",
                    "arguments": {
                        "task": "Draft intro",
                        "target": "section_that_does_not_exist"
                    }
                }
            }
            res_prompt = json.loads(process_message(json.dumps(req_get_prompt)))
            self.assertIn("messages", res_prompt["result"])
            self.assertIn("Task: Draft intro", res_prompt["result"]["messages"][0]["content"]["text"])

        # 2. Inverted bounds in proofread_section
        with patch("writing_context_rtfm.server.RTFMAdapter") as MockAdapter, \
             patch("writing_context_rtfm.server.ExtensionStore") as MockStore:
            mock_adapter = MockAdapter.return_value
            mock_adapter.search.return_value = []
            mock_store = MockStore.return_value
            mock_store.get_cached_pack.return_value = None
            
            # Write a dummy target file for reading local context
            dummy_file = self.project_dir / "file.md"
            dummy_file.write_text("\n".join(f"Line {i}" for i in range(1, 100)))

            req_get_proof = {
                "jsonrpc": "2.0",
                "id": 21,
                "method": "prompts/get",
                "params": {
                    "name": "proofread_section",
                    "arguments": {
                        "target_file": str(dummy_file),
                        "line_start": 50, # Inverted bounds
                        "line_end": 10
                    }
                }
            }
            res_proof = json.loads(process_message(json.dumps(req_get_proof)))
            self.assertIn("messages", res_proof["result"])
            content_text = res_proof["result"]["messages"][0]["content"]["text"]
            self.assertIn("Line 10", content_text)
            self.assertIn("Line 50", content_text)
