import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from writing_context_rtfm.cli import doctor_command, inspect_target_command
from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator, SourceSpan
from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards
from writing_context_rtfm.storage import ExtensionStore


class TestMilestone1(unittest.TestCase):
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
            "sections:\n"
            "  sec1:\n"
            "    title: Section One\n"
            "    path: sec1.md\n"
            "    role: intro\n"
            "    depends_on: []\n"
            "    key_terms: [hello, world]\n"
        )

        # Create dummy library.db
        self.rtfm_db = self.project_root / ".rtfm" / "library.db"
        self.rtfm_db.write_text("dummy rtfm content")

        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(corpus="test_corpus", project_root=str(self.project_root)),
            context=ContextConfig(default_token_budget=1000),
            cache=CacheConfig(
                enabled=True, path=str(self.project_root / ".writing-context" / "cache.sqlite")
            ),
            section_cards=SectionCardsConfig(path=str(self.sc_file)),
        )

        self.section_cards = SectionCards(
            version=1,
            document=DocumentCard(title="Test Doc"),
            sections={
                "sec1": SectionCard(
                    id="sec1",
                    title="Section One",
                    path="sec1.md",
                    role="intro",
                    depends_on=[],
                    key_terms=["hello", "world"],
                )
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

    def test_deduplicate_spans_merging(self):
        # Setup overlapping and adjacent spans
        # Span A: lines 5-11
        # Span B: lines 12-15 (Adjacent)
        # Span C: lines 10-14 (Overlapping, but we sort first: 5-11, 10-14, 12-15)
        # Span D: lines 20-30 (Distinct)

        # In current sorting key: (start, end)
        # Spans on path "file.md":
        # 1. start=5, end=11
        # 2. start=10, end=14
        # 3. start=12, end=15
        # 4. start=20, end=30

        spans = [
            SourceSpan(
                path="file.md",
                line_start=5,
                line_end=11,
                reason="Reason A",
                score=0.8,
                priority="supporting",
                query="Query A",
                metadata={"snippet": "line5\nline6\nline7\nline8\nline9\nline10\nline11"},
            ),
            SourceSpan(
                path="file.md",
                line_start=12,
                line_end=15,
                reason="Reason B",
                score=0.9,
                priority="essential",
                query="Query B",
                metadata={"snippet": "line12\nline13\nline14\nline15"},
            ),
            SourceSpan(
                path="file.md",
                line_start=10,
                line_end=14,
                reason="Reason C",
                score=0.7,
                priority="background",
                query="Query C",
                metadata={"snippet": "line10\nline11\nline12\nline13\nline14"},
            ),
            SourceSpan(
                path="file.md",
                line_start=20,
                line_end=30,
                reason="Reason D",
                score=0.6,
                priority="background",
                query="Query D",
                metadata={"snippet": "line20\nto\nline30"},
            ),
        ]

        merged = self.generator._deduplicate_spans(spans)

        # We expect two final spans:
        # One merged span from 5 to 15 (covering all of 5-11, 10-14, 12-15)
        # One span from 20 to 30
        self.assertEqual(len(merged), 2)

        # Let's inspect the first merged span (it should have the highest score: 0.9, so it should be sorted first)
        span_5_15 = merged[0]
        self.assertEqual(span_5_15.line_start, 5)
        self.assertEqual(span_5_15.line_end, 15)
        self.assertEqual(span_5_15.score, 0.9)
        self.assertEqual(span_5_15.priority, "essential")

        # Combine reasons: "Reason A", "Reason C", "Reason B" in order of merge
        self.assertIn("Reason A", span_5_15.reason)
        self.assertIn("Reason B", span_5_15.reason)
        self.assertIn("Reason C", span_5_15.reason)

        # Combine queries: "Query A", "Query C", "Query B"
        self.assertIn("Query A", span_5_15.query)
        self.assertIn("Query B", span_5_15.query)
        self.assertIn("Query C", span_5_15.query)

        # Snippet verification
        snippet = span_5_15.metadata["snippet"]
        lines = snippet.splitlines()
        self.assertEqual(len(lines), 11)  # line5 to line15
        self.assertEqual(lines[0], "line5")
        self.assertEqual(lines[-1], "line15")

        # Distinct span
        span_20_30 = merged[1]
        self.assertEqual(span_20_30.line_start, 20)
        self.assertEqual(span_20_30.line_end, 30)
        self.assertEqual(span_20_30.score, 0.6)

    def test_deduplicate_spans_does_not_chain_boundary_only_overlaps(self):
        spans = [
            SourceSpan(
                path="file.md",
                line_start=start,
                line_end=end,
                reason=f"Chunk {start}",
                score=0.8,
                metadata={"snippet": "word\n" * (end - start + 1)},
            )
            for start, end in ((1, 100), (100, 200), (200, 300))
        ]

        merged = self.generator._deduplicate_spans(spans)

        self.assertEqual(
            [(span.line_start, span.line_end) for span in merged],
            [(1, 100), (100, 200), (200, 300)],
        )

    def test_cache_diagnostics_integration(self):
        # Mock RTFM adapter search response
        mock_result = MagicMock()
        mock_result.path = str(self.project_root / "sec1.md")
        mock_result.line_start = 1
        mock_result.line_end = 2
        mock_result.score = 0.85
        mock_result.snippet = "Hello world context"

        self.adapter.search.return_value = [mock_result]

        # 1. First run: cache miss
        pack1 = self.generator.generate(
            task="write introductory section", target="sec1", token_budget=1000
        )
        self.assertIsNotNone(pack1.cache)
        self.assertTrue(pack1.cache.enabled)
        self.assertFalse(pack1.cache.hit)
        self.assertIsNotNone(pack1.cache.task_hash)
        self.assertIsNotNone(pack1.cache.config_hash)
        self.assertIsNotNone(pack1.cache.section_cards_hash)
        self.assertIsNotNone(pack1.cache.rtfm_index_fingerprint)

        # Reset mock
        self.adapter.search.reset_mock()

        # 2. Second run: cache hit
        pack2 = self.generator.generate(
            task="write introductory section", target="sec1", token_budget=1000
        )
        self.adapter.search.assert_not_called()
        self.assertIsNotNone(pack2.cache)
        self.assertTrue(pack2.cache.enabled)
        self.assertTrue(pack2.cache.hit)
        self.assertEqual(pack1.cache.task_hash, pack2.cache.task_hash)
        self.assertEqual(pack1.cache.config_hash, pack2.cache.config_hash)
        self.assertEqual(pack1.cache.section_cards_hash, pack2.cache.section_cards_hash)
        self.assertEqual(pack1.cache.rtfm_index_fingerprint, pack2.cache.rtfm_index_fingerprint)

    def test_doctor_cli_command(self):
        # We can test doctor_command directly by redirecting stdout
        import io
        from contextlib import redirect_stdout

        args = MagicMock()
        args.project_root = str(self.project_root)

        f = io.StringIO()
        with redirect_stdout(f):
            # Patch import to simulate rtfm available / CLI available
            with patch("shutil.which", return_value="/usr/local/bin/rtfm"), patch("sys.exit"):
                doctor_command(args)

        output = f.getvalue()
        self.assertIn("Writing Context RTFM Extension Doctor", output)
        self.assertIn("[*] RTFM CLI:         [OK]", output)
        self.assertIn("[*] Config:           [OK]", output)
        self.assertIn("[*] Section Cards:    [OK] Parsed 1 sections", output)
        self.assertIn("[*] RTFM DB:          [OK] Found", output)
        self.assertIn("[*] Cache DB:         [OK] Found", output)

    def test_inspect_target_cli_command(self):
        import io
        from contextlib import redirect_stdout

        args = MagicMock()
        args.project_root = str(self.project_root)
        args.target = "sec1"

        f = io.StringIO()
        with redirect_stdout(f), patch("sys.exit") as mock_exit:
            inspect_target_command(args)
            mock_exit.assert_not_called()

        output = f.getvalue()
        self.assertIn("Target Section ID: sec1", output)
        self.assertIn("Title:             Section One", output)
        self.assertIn("Path:              sec1.md", output)
        self.assertIn("Role:              intro", output)
        self.assertIn("Depends On:        []", output)
        self.assertIn("Key Terms:         ['hello', 'world']", output)


if __name__ == "__main__":
    unittest.main()
