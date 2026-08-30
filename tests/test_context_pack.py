import unittest
from dataclasses import replace
from unittest.mock import MagicMock

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.storage import ExtensionStore


class TestContextPackGenerator(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(),
            context=ContextConfig(default_token_budget=1000),
            cache=CacheConfig(enabled=False),
            section_cards=SectionCardsConfig(),
        )
        self.adapter = MagicMock()
        self.adapter.search.return_value = []
        self.store = MagicMock(spec=ExtensionStore)
        self.generator = ContextPackGenerator(self.config, None, self.adapter, self.store)

    def test_generate_empty_results(self):
        pack = self.generator.generate(task="test task", target=None, token_budget=1000)
        self.assertEqual(pack.task, "test task")
        self.assertEqual(len(pack.source_spans), 0)
        self.adapter.search.assert_called()

    def test_generate_emits_private_candidate_diagnostic_stages(self):
        result = MagicMock()
        result.path = "evidence.tex"
        result.line_start = 1
        result.line_end = 3
        result.score = 0.9
        result.snippet = "Relevant evidence for the requested section."
        result.metadata = {"rank": 1}
        self.adapter.search.return_value = [result]
        snapshots = {}

        generator = ContextPackGenerator(
            self.config,
            None,
            self.adapter,
            self.store,
            diagnostic_recorder=lambda stage, spans: snapshots.__setitem__(stage, list(spans)),
        )
        pack = generator.generate(task="write section", target=None, token_budget=1000)

        self.assertEqual(
            [
                stage
                for stage in (
                    "retrieved",
                    "deduplicated",
                    "score_filtered",
                    "diversified",
                    "budget_candidates",
                    "selected",
                )
                if stage in snapshots
            ],
            [
                "retrieved",
                "deduplicated",
                "score_filtered",
                "diversified",
                "budget_candidates",
                "selected",
            ],
        )
        self.assertEqual(snapshots["selected"], pack.source_spans)
        self.assertGreaterEqual(len(snapshots["retrieved"]), len(pack.source_spans))

    def test_generate_accepts_benchmark_query_stream_retriever(self):
        result = MagicMock()
        result.path = "injected.tex"
        result.line_start = 1
        result.line_end = 2
        result.score = 0.9
        result.snippet = "Evidence injected by the benchmark exposure policy."
        result.metadata = {}
        observed = {}

        def retrieve(specs, corpus, default_limit, obligations):
            observed["specs"] = specs
            observed["corpus"] = corpus
            observed["default_limit"] = default_limit
            observed["obligations"] = obligations
            return {0: [result]}

        generator = ContextPackGenerator(
            self.config,
            None,
            self.adapter,
            self.store,
            query_stream_retriever=retrieve,
        )
        pack = generator.generate(task="write section", target=None, token_budget=1000)

        self.adapter.search.assert_not_called()
        self.assertEqual(observed["specs"][0].query_type, "task")
        self.assertEqual(observed["corpus"], "default")
        self.assertEqual(observed["default_limit"], 10)
        self.assertEqual(observed["obligations"], ())
        self.assertEqual(pack.source_spans[0].path, "injected.tex")

    def test_generate_accepts_bibliography_handoff_callback(self):
        result = MagicMock()
        result.path = "references.bib"
        result.line_start = 4
        result.line_end = 8
        result.score = 0.8
        result.snippet = "@article{missingKey, title={Missing Evidence}}"
        result.metadata = {}
        self.adapter.search.return_value = [result]

        provider = MagicMock()
        provider.provider_id = "bibtex"
        provider.is_available.return_value = True
        provider.fetch_context.return_value = [
            SourceSpan(
                path="bibtex:otherKey",
                line_start=None,
                line_end=None,
                reason="Existing provider evidence",
                score=0.7,
                metadata={"snippet": "Other", "citekey": "otherKey"},
            )
        ]
        observed = {}

        def handoff(excluded, provider_spans):
            observed["excluded"] = excluded
            observed["provider_spans"] = provider_spans
            return [
                replace(
                    excluded[0],
                    reason="Fallback bibliography evidence",
                    metadata={**excluded[0].metadata, "citekey": "missingKey"},
                )
            ]

        generator = ContextPackGenerator(
            self.config,
            None,
            self.adapter,
            self.store,
            providers=[provider],
            bibliography_handoff=handoff,
        )
        pack = generator.generate(task="write section", target=None, token_budget=1000)

        self.assertEqual(observed["excluded"][0].path, "references.bib")
        self.assertEqual(observed["provider_spans"][0].path, "bibtex:otherKey")
        self.assertIn("references.bib", [span.path for span in pack.source_spans])

    def test_resilient_target_resolution(self):
        from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards

        sections = {
            "section_abstract": SectionCard(
                id="section_abstract", title="Abstract Section", path="sections/abstract.tex"
            ),
            "introduction": SectionCard(
                id="introduction", title="Intro Section", path="sections/intro.tex"
            ),
        }
        cards = SectionCards(version=1, document=DocumentCard(title="Test Doc"), sections=sections)

        generator = ContextPackGenerator(self.config, cards, self.adapter, self.store)

        # 1. Exact match
        resolved_key, card, path = generator._resolve_target("section_abstract", ".")
        self.assertEqual(resolved_key, "section_abstract")
        self.assertEqual(path, "sections/abstract.tex")

        # 2. f"section_{target}" match
        resolved_key, card, path = generator._resolve_target("abstract", ".")
        self.assertEqual(resolved_key, "section_abstract")
        self.assertEqual(path, "sections/abstract.tex")

        # 3. target[8:] prefix stripping match
        resolved_key, card, path = generator._resolve_target("section_introduction", ".")
        self.assertEqual(resolved_key, "introduction")
        self.assertEqual(path, "sections/intro.tex")

        # 4. Case-insensitive title match
        resolved_key, card, path = generator._resolve_target("abstract section", ".")
        self.assertEqual(resolved_key, "section_abstract")
        self.assertEqual(path, "sections/abstract.tex")

        # 5. Path stem match
        resolved_key, card, path = generator._resolve_target("intro", ".")
        self.assertEqual(resolved_key, "introduction")
        self.assertEqual(path, "sections/intro.tex")

        # 6. Fallback path check
        resolved_key, card, path = generator._resolve_target("sections/abstract.tex", ".")
        self.assertEqual(resolved_key, "section_abstract")

        resolved_key, card, path = generator._resolve_target("nonexistent_file.tex", ".")
        self.assertEqual(resolved_key, None)
        self.assertEqual(path, "nonexistent_file.tex")

    def test_latex_safety_does_not_degrade(self):
        import os
        import tempfile

        from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "abstract.tex")
            # Write a LaTeX content that has math environment/commands
            with open(file_path, "w") as f:
                f.write(
                    "This is a line with \\ref{eq1} and some \\begin{equation} x=y \\end{equation} LaTeX content."
                )

            sections = {
                "section_abstract": SectionCard(
                    id="section_abstract", title="Abstract Section", path="abstract.tex"
                )
            }
            cards = SectionCards(
                version=1, document=DocumentCard(title="Test Doc"), sections=sections
            )

            generator = ContextPackGenerator(self.config, cards, self.adapter, self.store)

            # Request target pack using the resilient target name "abstract" and a line range
            pack = generator.generate(
                task="Write the abstract",
                target="abstract",
                token_budget=1000,
                project_root=tmpdir,
                line_start=1,
                line_end=1,
            )

            # Assert target span is extracted successfully (not omitted)
            self.assertEqual(pack.status, "complete")
            target_spans = [s for s in pack.source_spans if s.source_role == "target_text"]
            self.assertEqual(len(target_spans), 1)
            self.assertEqual(target_spans[0].path, "abstract.tex")
            self.assertIn("ref{eq1}", target_spans[0].metadata["snippet"])

            # Assert LaTeX Safety warning is present in warnings
            latex_warnings = [w for w in pack.warnings if "LaTeX Safety:" in w]
            self.assertTrue(len(latex_warnings) > 0)

    def test_token_budget_auto_scaling_warning(self):
        # Mock search results returning large snippets
        mock_result1 = MagicMock()
        mock_result1.path = "sections/abstract.tex"
        mock_result1.line_start = 1
        mock_result1.line_end = 20
        mock_result1.score = 0.9
        # 1000 'word ' tokens (approx 1000 tokens)
        mock_result1.snippet = "word " * 1000
        mock_result1.metadata = {}

        mock_result2 = MagicMock()
        mock_result2.path = "sections/intro.tex"
        mock_result2.line_start = 1
        mock_result2.line_end = 20
        mock_result2.score = 0.8
        # 1000 'word ' tokens (approx 1000 tokens)
        mock_result2.snippet = "word " * 1000
        mock_result2.metadata = {}

        self.adapter.search.return_value = [mock_result1, mock_result2]

        # Use a small token budget so both cannot fit
        pack = self.generator.generate(task="write intro", target=None, token_budget=1500)

        self.assertEqual(
            pack.status, "degraded"
        )  # Degraded due to missing section cards
        self.assertEqual(len(pack.source_spans), 1)
        self.assertTrue(pack.quality.get("dropped_for_budget", 0) >= 1)



if __name__ == "__main__":
    unittest.main()
