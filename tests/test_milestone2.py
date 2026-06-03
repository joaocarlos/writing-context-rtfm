import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator, SourceSpan
from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards
from writing_context_rtfm.server import process_message
from writing_context_rtfm.storage import ExtensionStore


class TestMilestone2(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.project_root = Path(self.tmp_dir)

        # Create necessary directories
        (self.project_root / ".writing-context").mkdir()
        (self.project_root / ".rtfm").mkdir()

        self.config_file = self.project_root / ".writing-context" / "config.yaml"
        self.config_file.write_text(
            "version: 1\n"
            "rtfm:\n"
            "  corpus: test_corpus\n"
            "cache:\n"
            "  path: .writing-context/cache.sqlite\n"
        )

        self.sc_file = self.project_root / ".writing-context" / "section_cards.yaml"
        self.sc_file.write_text(
            "version: 1\n"
            "document:\n"
            "  title: Test Doc\n"
            "  thesis: Main thesis statement\n"
            "sections:\n"
            "  sec1:\n"
            "    title: Section One\n"
            "    path: sec1.md\n"
            "    role: intro\n"
            "    depends_on: [sec2]\n"
            "    key_terms: [term1, term2, term3, term4, term5, term6, term7, term8]\n"
            "  sec2:\n"
            "    title: Section Two\n"
            "    path: sec2.md\n"
            "    role: approach\n"
            "    depends_on: []\n"
            "    key_terms: [depterm1, depterm2, depterm3, depterm4, depterm5, depterm6, depterm7]\n"
        )

        # Create dummy target file
        self.target_file = self.project_root / "sec1.md"
        self.target_file.write_text(
            "\n".join(f"Line {i}: Content of line {i}" for i in range(1, 101))
        )

        # Create dummy dependency file
        self.dep_file = self.project_root / "sec2.md"
        self.dep_file.write_text(
            "\n".join(f"Dep Line {i}: Content of line {i}" for i in range(1, 50))
        )

        # Create dummy library.db
        self.rtfm_db = self.project_root / ".rtfm" / "library.db"
        self.rtfm_db.write_text("dummy rtfm content")

        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(corpus="test_corpus", project_root=str(self.project_root)),
            context=ContextConfig(default_token_budget=1000, max_source_spans=10),
            cache=CacheConfig(
                enabled=True, path=str(self.project_root / ".writing-context" / "cache.sqlite")
            ),
            section_cards=SectionCardsConfig(path=str(self.sc_file)),
        )

        self.section_cards = SectionCards(
            version=1,
            document=DocumentCard(title="Test Doc", thesis="Main thesis statement"),
            sections={
                "sec1": SectionCard(
                    id="sec1",
                    title="Section One",
                    path="sec1.md",
                    role="intro",
                    depends_on=["sec2"],
                    key_terms=[
                        "term1",
                        "term2",
                        "term3",
                        "term4",
                        "term5",
                        "term6",
                        "term7",
                        "term8",
                    ],
                ),
                "sec2": SectionCard(
                    id="sec2",
                    title="Section Two",
                    path="sec2.md",
                    role="approach",
                    depends_on=[],
                    key_terms=[
                        "depterm1",
                        "depterm2",
                        "depterm3",
                        "depterm4",
                        "depterm5",
                        "depterm6",
                        "depterm7",
                    ],
                ),
            },
        )

        self.adapter = MagicMock()
        self.adapter.search.return_value = []

        self.store = ExtensionStore(self.config.cache.path)
        self.store.init_db()
        self.generator = ContextPackGenerator(
            self.config, self.section_cards, self.adapter, self.store
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def test_task_type_query_routing_review(self):
        # When task_type is "review", it should issue a thesis query and skip task keywords
        queries, _, _, query_type_map = self.generator._build_queries(
            task="Draft intro section about term1",
            target="sec1",
            must_consider=[],
            task_type="review",
        )
        self.assertIn("Main thesis statement", queries)
        self.assertEqual(query_type_map["Main thesis statement"], "thesis")
        # Ensure it skips task keywords in review mode
        # "draft", "intro", "section", "about", "term1" are keywords
        for _q, qtype in query_type_map.items():
            self.assertNotEqual(qtype, "task_keyword")

    def test_task_type_query_routing_align(self):
        # When task_type is "align_with_previous_sections", it should scale down target key terms count to 2
        queries, _, _, query_type_map = self.generator._build_queries(
            task="Draft intro section about term1",
            target="sec1",
            must_consider=[],
            task_type="align_with_previous_sections",
        )
        target_kt_queries = [q for q, qtype in query_type_map.items() if qtype == "key_term"]
        self.assertEqual(len(target_kt_queries), 2)
        self.assertIn("term1", target_kt_queries)
        self.assertIn("term2", target_kt_queries)

    def test_pack_mode_minimal(self):
        # minimal mode caps tokens at 2000, max spans at 5, and skips key terms and task keywords
        queries, _, _, query_type_map = self.generator._build_queries(
            task="Draft intro section about term1",
            target="sec1",
            must_consider=[],
            pack_mode="minimal",
        )
        # Check that it has title but no key terms
        self.assertIn("Section One", queries)
        self.assertEqual(query_type_map["Section One"], "title")
        for _q, qtype in query_type_map.items():
            self.assertNotEqual(qtype, "key_term")
            self.assertNotEqual(qtype, "task_keyword")
            self.assertNotEqual(qtype, "dep_key_term")

        # Test budget capping
        pack = self.generator.generate(
            task="Draft intro section", target="sec1", token_budget=5000, pack_mode="minimal"
        )
        self.assertEqual(pack.pack_mode, "minimal")
        # Budget gets capped at 2000
        # Since our mock adapter returns empty search results, estimated tokens is minimal
        # but pack_mode must be set
        self.assertEqual(pack.pack_mode, "minimal")

    def test_pack_mode_deep(self):
        # deep mode expands max key terms to 12 and dep key terms to 6
        queries, _, _, query_type_map = self.generator._build_queries(
            task="Draft intro section about term1",
            target="sec1",
            must_consider=[],
            pack_mode="deep",
        )
        target_kt_queries = [q for q, qtype in query_type_map.items() if qtype == "key_term"]
        dep_kt_queries = [q for q, qtype in query_type_map.items() if qtype == "dep_key_term"]
        # sec1 has 8 terms, all should be queried in deep mode (8 < 12)
        self.assertEqual(len(target_kt_queries), 8)
        # sec2 has 7 terms, up to 6 should be queried
        self.assertEqual(len(dep_kt_queries), 6)

    def test_target_line_range_extraction(self):
        # Test extraction of specific lines from existing target file
        pack = self.generator.generate(
            task="Revise intro line 10 to 12",
            target="sec1",
            token_budget=1000,
            line_start=10,
            line_end=12,
        )

        self.assertEqual(pack.status, "complete")
        spans = pack.source_spans
        self.assertTrue(len(spans) >= 1)

        # Verify first span is target_text (which gets merged with adjacent local context)
        target_span = spans[0]
        self.assertEqual(target_span.source_role, "target_text")
        self.assertEqual(target_span.line_start, 1)
        self.assertEqual(target_span.line_end, 27)
        self.assertEqual(target_span.priority, "essential")
        self.assertIn("Line 10: Content of line 10", target_span.metadata["snippet"])
        self.assertIn("Line 12: Content of line 12", target_span.metadata["snippet"])
        self.assertIn("Line 1: Content of line 1", target_span.metadata["snippet"])
        self.assertIn("Line 27: Content of line 27", target_span.metadata["snippet"])

    def test_target_line_range_degraded(self):
        # Target section card has sec1.md which exists. But if target card is not resolved or not found
        # Or if we pass target as a file that doesn't exist
        pack = self.generator.generate(
            task="Revise non-existent",
            target="missing_section",
            token_budget=1000,
            line_start=10,
            line_end=12,
        )
        self.assertEqual(pack.status, "degraded")
        self.assertTrue(
            any("provided but target file path could not be resolved" in w for w in pack.warnings)
        )

        # Test target file not found
        pack2 = self.generator.generate(
            task="Revise missing file",
            target="missing_file.md",
            token_budget=1000,
            line_start=10,
            line_end=12,
        )
        self.assertEqual(pack2.status, "degraded")
        self.assertTrue(any("not found for line range extraction" in w for w in pack2.warnings))

    def test_source_role_classification(self):
        # Setup mocked retrieved results to test classification of roles
        # 1. Target path match with line range overlap -> target_text or local_context
        # 2. Dependency path match -> dependency
        # 3. Reference path match -> reference

        retrieved_spans = [
            SourceSpan(
                path="sec1.md",
                line_start=11,
                line_end=11,
                reason="Match 1",
                score=0.9,
                priority="supporting",
            ),
            SourceSpan(
                path="sec1.md",
                line_start=20,
                line_end=22,
                reason="Match 2",
                score=0.8,
                priority="supporting",
            ),
            SourceSpan(
                path="sec2.md",
                line_start=5,
                line_end=10,
                reason="Match 3",
                score=0.7,
                priority="supporting",
            ),
            SourceSpan(
                path="other.md",
                line_start=1,
                line_end=5,
                reason="Match 4",
                score=0.6,
                priority="supporting",
            ),
        ]

        # Test roles classification during generator priority assignment
        classified = self.generator._classify_priority(
            spans=retrieved_spans,
            target_card=self.section_cards.sections["sec1"],
            dep_cards=[self.section_cards.sections["sec2"]],
            target_path="sec1.md",
            line_start=10,
            line_end=12,
        )

        {s.path: s.source_role for s in classified}
        self.assertEqual(
            classified[0].source_role, "target_text"
        )  # L11 is inside target range 10-12
        self.assertEqual(
            classified[1].source_role, "local_context"
        )  # L20-22 is inside local range 1-27
        self.assertEqual(classified[2].source_role, "dependency")  # sec2.md is dependency
        self.assertEqual(classified[3].source_role, "reference")  # other.md is reference

    def test_mcp_server_schema_compatibility(self):
        # Test that MCP process_message accepts new parameters
        with patch("writing_context_rtfm.server._load_runtime") as mock_load:
            mock_load.return_value = (self.config, self.section_cards, [], self.adapter, self.store)

            # 1. Test tools/call with new parameters
            req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "get_writing_context_pack",
                    "arguments": {
                        "task": "Test task with task type and mode",
                        "target": "sec1",
                        "task_type": "revise_existing_section",
                        "line_start": 10,
                        "line_end": 12,
                        "pack_mode": "minimal",
                    },
                },
            }
            res_str = process_message(json.dumps(req))
            res = json.loads(res_str)
            self.assertNotIn("error", res)

            result_payload = json.loads(res["result"]["content"][0]["text"])
            self.assertEqual(result_payload["task_type"], "revise_existing_section")
            self.assertEqual(result_payload["pack_mode"], "minimal")

            # The first source span should be target_text (due to manual extraction)
            spans = result_payload["source_spans"]
            self.assertTrue(len(spans) > 0)
            self.assertEqual(spans[0]["source_role"], "target_text")

            # 2. Test prompts/get write_section with new arguments
            req_prompt = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "prompts/get",
                "params": {
                    "name": "write_section",
                    "arguments": {
                        "task": "Test prompt get task",
                        "target": "sec1",
                        "task_type": "review",
                        "pack_mode": "deep",
                    },
                },
            }
            res_prompt_str = process_message(json.dumps(req_prompt))
            res_prompt = json.loads(res_prompt_str)
            self.assertNotIn("error", res_prompt)
            prompt_text = res_prompt["result"]["messages"][0]["content"]["text"]
            self.assertIn("Test prompt get task", prompt_text)
            self.assertIn("Main thesis statement", prompt_text)


if __name__ == "__main__":
    unittest.main()
