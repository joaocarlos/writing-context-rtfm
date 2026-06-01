import unittest
import tempfile
import shutil
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from writing_context_rtfm.config import AppConfig, RTFMConfig, CacheConfig, ContextConfig, SectionCardsConfig
from writing_context_rtfm.context_pack import ContextPack, ContextPackGenerator
from writing_context_rtfm.proofread import ProofreadPackGenerator
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.server import process_message
from writing_context_rtfm.utils import scan_latex_commands
from writing_context_rtfm.features import get_term_context
from writing_context_rtfm.schemas import RTFMResult

class TestMilestone3(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.project_root = Path(self.tmp_dir)

        # Create necessary directories
        (self.project_root / ".writing-context").mkdir()
        (self.project_root / ".rtfm").mkdir()

        # Write dummy config
        self.config_file = self.project_root / ".writing-context" / "config.yaml"
        self.config_file.write_text(
            "version: 1\n"
            "rtfm:\n"
            "  corpus: test_corpus\n"
            "cache:\n"
            "  path: .writing-context/cache.sqlite\n"
        )

        # Write section cards yaml
        self.sc_file = self.project_root / ".writing-context" / "section_cards.yaml"
        self.sc_file.write_text(
            "version: 1\n"
            "document:\n"
            "  title: Test Doc\n"
            "  thesis: Main thesis statement\n"
            "  terminology:\n"
            "    Quantization:\n"
            "      definition: Reducing precision of weights\n"
            "      variants: [quantize, quantized]\n"
            "      avoid: [discretization, binning]\n"
            "    Latency:\n"
            "      definition: Time taken to process\n"
            "      variants: [delay]\n"
            "sections:\n"
            "  sec1:\n"
            "    title: Section One\n"
            "    path: sec1.md\n"
            "    role: intro\n"
            "    depends_on: [sec2]\n"
            "    key_terms: [Quantization, Latency]\n"
            "  sec2:\n"
            "    title: Section Two\n"
            "    path: sec2.md\n"
            "    role: approach\n"
            "    depends_on: []\n"
            "    key_terms: [delay]\n"
        )

        # Create dummy target files
        self.target_file = self.project_root / "sec1.md"
        self.target_file.write_text("This is target section content.\n")

        self.dep_file = self.project_root / "sec2.md"
        self.dep_file.write_text("This is dependency section content.\n")

        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(corpus="test_corpus", project_root=str(self.project_root)),
            context=ContextConfig(default_token_budget=1000, max_source_spans=10),
            cache=CacheConfig(enabled=False, path=str(self.project_root / ".writing-context" / "cache.sqlite")),
            section_cards=SectionCardsConfig(path=str(self.sc_file))
        )

        # Parse loaded section cards
        from writing_context_rtfm.section_cards import load_section_cards
        self.section_cards = load_section_cards(str(self.sc_file))

        self.adapter = MagicMock()
        self.adapter.search.return_value = []

        self.store = ExtensionStore(self.config.cache.path)
        self.store.init_db()
        self.generator = ContextPackGenerator(self.config, self.section_cards, self.adapter, self.store)
        self.proofread_generator = ProofreadPackGenerator(self.config, self.section_cards, self.adapter, self.store)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir)

    def test_latex_safety_scanner(self):
        # 1. Test utility scan_latex_commands
        text = "Check \\cite{paper1} and \\label{sec:intro}. Inline math $E=mc^2$ and block math $$E=mc^2$$. Also environment \\begin{equation}x=y\\end{equation}."
        cmds = scan_latex_commands(text)
        self.assertIn("\\cite{paper1}", cmds)
        self.assertIn("\\label{sec:intro}", cmds)
        self.assertIn("$E=mc^2$", cmds)
        self.assertIn("$$E=mc^2$$", cmds)
        self.assertIn("\\begin{equation}x=y\\end{equation}", cmds)

        # 2. Test safety warnings appended during ContextPack generation
        self.target_file.write_text("We propose a new model. See \\cite{test_ref} and equation $y = f(x)$.")
        pack = self.generator.generate(
            task="Revise intro line 1",
            target="sec1",
            token_budget=1000,
            line_start=1,
            line_end=1
        )
        self.assertTrue(any("LaTeX Safety" in w for w in pack.warnings))
        self.assertTrue(any("\\cite{test_ref}" in w for w in pack.warnings))
        self.assertTrue(any("$y = f(x)$" in w for w in pack.warnings))

        # 3. Test safety warnings appended during ProofreadingContextPack generation
        proof_pack = self.proofread_generator.generate(
            target_file=str(self.target_file),
            line_start=1,
            line_end=1,
            mode="latex_safe"
        )
        self.assertTrue(any("LaTeX Safety" in w for w in proof_pack.warnings))
        self.assertTrue(any("\\cite{test_ref}" in w for w in proof_pack.warnings))

    def test_terminology_lookup(self):
        # 1. Test get_term_context directly
        # Exact canonical lookup
        res = get_term_context("Quantization", str(self.project_root))
        self.assertEqual(res["status"], "found")
        self.assertEqual(res["term"], "Quantization")
        self.assertEqual(res["definition"], "Reducing precision of weights")
        self.assertEqual(res["variants"], ["quantize", "quantized"])
        self.assertEqual(res["avoid"], ["discretization", "binning"])

        # Case-insensitive canonical lookup
        res = get_term_context("quantization", str(self.project_root))
        self.assertEqual(res["status"], "found")
        self.assertEqual(res["term"], "Quantization")

        # Variant lookup
        res = get_term_context("quantized", str(self.project_root))
        self.assertEqual(res["status"], "found")
        self.assertEqual(res["term"], "Quantization")

        # Avoid term lookup
        res = get_term_context("binning", str(self.project_root))
        self.assertEqual(res["status"], "found")
        self.assertEqual(res["term"], "Quantization")

        # Not found lookup
        res = get_term_context("non-existent-term", str(self.project_root))
        self.assertEqual(res["status"], "not_found")

        # 2. Test terminology population inside generator
        pack = self.generator.generate(
            task="Draft quantization and latency",
            target="sec1",
            token_budget=1000
        )
        self.assertIn("Quantization", pack.terminology)
        self.assertEqual(pack.terminology["Quantization"], "Reducing precision of weights")
        self.assertIn("Latency", pack.terminology)
        self.assertEqual(pack.terminology["Latency"], "Time taken to process")

    def test_role_budgets_allocation(self):
        mock_results = [
            RTFMResult(path="sec1.md", line_start=1, line_end=10, snippet="B" * 1200, score=0.9, metadata={}),
            RTFMResult(path="sec1.md", line_start=15, line_end=25, snippet="B" * 1200, score=0.85, metadata={}),
            RTFMResult(path="sec2.md", line_start=1, line_end=20, snippet="B" * 1200, score=0.8, metadata={}),
            RTFMResult(path="ref.md", line_start=1, line_end=20, snippet="B" * 1200, score=0.75, metadata={}),
        ]
        self.adapter.search.return_value = mock_results

        pack = self.generator.generate(
            task="Draft quantization",
            target="sec1",
            token_budget=1000,
            line_start=1,
            line_end=10
        )
        
        selected_roles = [s.source_role for s in pack.source_spans]
        self.assertIn("target_text", selected_roles)
        self.assertIn("local_context", selected_roles)
        self.assertIn("dependency", selected_roles)
        self.assertIn("reference", selected_roles)
        self.assertEqual(len(pack.source_spans), 4)

        # 2. Test runtime override of budgets
        pack_ref_override = self.generator.generate(
            task="Draft quantization",
            target="sec1",
            token_budget=1000,
            line_start=1,
            line_end=10,
            role_budgets={"target_text": 0.0, "local_context": 0.0, "dependency": 0.0, "reference": 1.0}
        )
        ref_override_roles = [s.source_role for s in pack_ref_override.source_spans]
        self.assertIn("reference", ref_override_roles)
        self.assertIn("target_text", ref_override_roles)
        self.assertIn("local_context", ref_override_roles)
        self.assertIn("dependency", ref_override_roles)

    def test_mcp_and_cli_integration(self):
        # 1. MCP Server integration for get_term_context
        req = {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {
                "name": "get_term_context",
                "arguments": {
                    "term": "Quantization",
                    "project_root": str(self.project_root)
                }
            }
        }
        with patch("writing_context_rtfm.server._load_runtime") as mock_load:
            mock_load.return_value = (self.config, self.section_cards, [], self.adapter, self.store)
            res_str = process_message(json.dumps(req))
            res = json.loads(res_str)
            self.assertNotIn("error", res)
            payload = json.loads(res["result"]["content"][0]["text"])
            self.assertEqual(payload["status"], "found")
            self.assertEqual(payload["term"], "Quantization")
            self.assertEqual(payload["definition"], "Reducing precision of weights")

        # 2. CLI Main call for get-term
        with patch("sys.argv", ["writing-context-rtfm", "get-term", "Quantization", "--project-root", str(self.project_root)]):
            from writing_context_rtfm.cli import main
            import io
            from contextlib import redirect_stdout
            f = io.StringIO()
            with redirect_stdout(f):
                main()
            output = json.loads(f.getvalue())
            self.assertEqual(output["status"], "found")
            self.assertEqual(output["term"], "Quantization")

        # 3. CLI Main call for pack with --role-budgets override
        mock_generator_instance = MagicMock()
        fake_pack = ContextPack(
            task="my task",
            target=None,
            document_thesis=None,
            prior_claims=[],
            terminology={},
            constraints=[],
            source_spans=[],
            estimated_tokens=0
        )
        mock_generator_instance.generate.return_value = fake_pack
        
        with patch("writing_context_rtfm.cli.ContextPackGenerator", return_value=mock_generator_instance), \
             patch("sys.argv", ["writing-context-rtfm", "pack", "--task", "my task", "--role-budgets", '{"target_text": 0.5, "reference": 0.5}', "--project-root", str(self.project_root)]):
            from writing_context_rtfm.cli import main
            main()
            mock_generator_instance.generate.assert_called_once()
            _, kwargs = mock_generator_instance.generate.call_args
            self.assertEqual(kwargs["role_budgets"], {"target_text": 0.5, "reference": 0.5})

if __name__ == "__main__":
    unittest.main()
