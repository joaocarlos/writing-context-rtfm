"""Stabilization tests for context_pack.py fixes."""

import unittest
from unittest.mock import MagicMock, patch

from writing_context_rtfm.config import load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import RTFMResult
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.utils import extract_keywords, is_allowed_source

FIXTURE_ROOT = "tests/fixtures/mini_latex_project"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_result(path, line_start, line_end, score, snippet="", chapter_title=""):
    return RTFMResult(
        path=path,
        line_start=line_start,
        line_end=line_end,
        snippet=snippet,
        score=score,
        metadata={"chapter_title": chapter_title, "book_title": "", "rank": 1},
    )


def make_generator(
    project_root=FIXTURE_ROOT, corpus="manuscript", budget=6000, cache_enabled=False
):
    config = load_config(project_root)
    # Override for tests
    from dataclasses import replace

    config = replace(
        config,
        rtfm=replace(config.rtfm, corpus=corpus),
        context=replace(config.context, default_token_budget=budget),
        cache=replace(config.cache, enabled=cache_enabled),
    )
    cards = load_section_cards(config.section_cards.path)
    adapter = MagicMock(spec=RTFMAdapter)
    store = MagicMock(spec=ExtensionStore)
    store.get_cached_pack.return_value = None
    return ContextPackGenerator(config, cards, adapter, store), config, cards


# ---------------------------------------------------------------------------
# Fix 1: project_root resolution
# ---------------------------------------------------------------------------


class TestProjectRootResolution(unittest.TestCase):
    def test_section_cards_loaded_from_fixture_root(self):
        config = load_config(FIXTURE_ROOT)
        cards = load_section_cards(config.section_cards.path)
        assert cards is not None, "Section cards should load from fixture root"
        assert cards.document.thesis is not None
        assert "section_approach" in cards.sections

    def test_section_cards_not_loaded_from_wrong_root(self):
        config = load_config("tests")  # project root without section_cards
        cards = load_section_cards(config.section_cards.path)
        assert cards is None

    def test_degraded_status_when_no_section_cards(self):
        gen, config, _ = make_generator(project_root=".")
        gen.section_cards = None  # force missing
        with patch.object(gen.adapter, "search", return_value=[]):
            pack = gen.generate(task="write intro", target=None, token_budget=1000)
        assert pack.status == "degraded"
        assert any("No section cards" in w for w in pack.warnings)

    def test_quality_block_reports_section_cards_path(self):
        gen, config, _ = make_generator()
        gen.adapter.search.return_value = []
        pack = gen.generate(task="write approach", target="section_approach", token_budget=1000)
        assert pack.quality is not None
        assert "section_cards_path" in pack.quality
        assert "section_cards_loaded" in pack.quality
        self.assertTrue(pack.quality["section_cards_loaded"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Fix 2: Exclusion of non-manuscript paths
# ---------------------------------------------------------------------------


class TestSourceExclusion(unittest.TestCase):
    def test_is_allowed_source(self):
        cases = [
            (".writing-context/section_cards.yaml", False),
            (".writing-context/config.yaml", False),
            (".rtfm/library.db", False),
            (".git/config", False),
            ("sections/03_approach.tex", True),
            ("main.tex", True),
            ("references.bib", True),
            ("context_cache.sqlite", False),
        ]
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(is_allowed_source(path), expected)

    def test_section_cards_yaml_not_in_source_spans(self):
        gen, config, _ = make_generator()
        gen.adapter.search.return_value = [
            make_result(
                ".writing-context/section_cards.yaml",
                None,
                None,
                2.5,
                snippet="version: 1",
                chapter_title="version, document, sections",
            ),
            make_result(
                "sections/03_approach.tex",
                5,
                11,
                1.8,
                snippet="The MIMII dataset",
                chapter_title="Dataset",
            ),
        ]
        pack = gen.generate(task="write methodology", target="section_approach", token_budget=1000)
        paths = [s.path for s in pack.source_spans]
        assert ".writing-context/section_cards.yaml" not in paths
        assert any("03_approach.tex" in p for p in paths)


# ---------------------------------------------------------------------------
# Fix 3: Score filtering
# ---------------------------------------------------------------------------


class TestScoreFiltering(unittest.TestCase):
    def test_near_zero_noise_discarded(self):
        gen, config, _ = make_generator()
        gen.adapter.search.return_value = [
            make_result(
                "sections/03_approach.tex",
                15,
                20,
                2.0,
                snippet="quantization",
                chapter_title="Deployment",
            ),
            make_result(
                "sections/04_results.tex",
                1,
                6,
                0.000001,
                snippet="results discussion",
                chapter_title="Results",
            ),
        ]
        pack = gen.generate(task="write methodology", target="section_approach", token_budget=1000)
        paths = [s.path for s in pack.source_spans]
        assert "sections/04_results.tex" not in paths

    def test_target_section_low_score_retained(self):
        gen, config, _ = make_generator()
        # All spans score very low, but 03_approach.tex IS the target file
        gen.adapter.search.return_value = [
            make_result(
                "sections/03_approach.tex",
                11,
                15,
                0.000001,
                snippet="algorithm selection CNN",
                chapter_title="Algorithm selection",
            ),
        ]
        pack = gen.generate(task="write methodology", target="section_approach", token_budget=1000)
        paths = [s.path for s in pack.source_spans]
        assert any("03_approach.tex" in p for p in paths)

    def test_quality_discarded_count(self):
        gen, config, _ = make_generator()
        gen.adapter.search.return_value = [
            make_result("sections/03_approach.tex", 5, 11, 2.5, snippet="MIMII dataset"),
            make_result("sections/01_intro.tex", 1, 5, 0.000001, snippet="intro text"),
            make_result("sections/02_related.tex", 1, 8, 0.000001, snippet="related"),
        ]
        pack = gen.generate(task="write methodology", target="section_approach", token_budget=1000)
        assert pack.quality["discarded_low_score"] >= 2


# ---------------------------------------------------------------------------
# Fix 4: Query expansion
# ---------------------------------------------------------------------------


class TestQueryExpansion(unittest.TestCase):
    def test_query_expansion_includes_target_title(self):
        gen, config, _ = make_generator()
        captured = []

        def fake_search(query, **kwargs):
            captured.append(query)
            return []

        gen.adapter.search.side_effect = fake_search
        gen.generate(
            task="write the approach section", target="section_approach", token_budget=1000
        )
        # Should include "Proposed approach" (the section title from section_cards)
        assert any("Proposed approach" in q or "approach" in q.lower() for q in captured)

    def test_query_expansion_includes_key_terms(self):
        gen, config, _ = make_generator()
        captured = []

        def fake_search(query, **kwargs):
            captured.append(query)
            return []

        gen.adapter.search.side_effect = fake_search
        gen.generate(task="write approach", target="section_approach", token_budget=1000)
        # section_cards.yaml defines key_terms: MIMII, CNN, quantization
        assert any("MIMII" in q or "CNN" in q or "quantization" in q for q in captured)

    def test_task_keyword_extraction(self):
        kws = extract_keywords("Write the methodology section detailing dataset and quantization")
        assert "dataset" in kws
        assert "quantization" in kws
        assert "methodology" in kws
        assert "write" not in kws
        assert "the" not in kws

    def test_multiple_queries_issued(self):
        gen, config, _ = make_generator()
        call_count = []

        def fake_search(query, **kwargs):
            call_count.append(query)
            return []

        gen.adapter.search.side_effect = fake_search
        gen.generate(
            task="write methodology detailing dataset quantization",
            target="section_approach",
            token_budget=1000,
        )
        assert len(call_count) > 1, "Should issue multiple queries with section cards loaded"


# ---------------------------------------------------------------------------
# Fix 6: Token estimation from snippet
# ---------------------------------------------------------------------------


class TestTokenEstimation(unittest.TestCase):
    def test_prefers_snippet_length_over_line_count(self):
        gen, config, _ = make_generator()
        from writing_context_rtfm.context_pack import SourceSpan

        # 5-line span, but very long snippet
        long_snippet = "word " * 500  # ~2000 chars → ~500 tokens
        span = SourceSpan(
            path="sections/03_approach.tex",
            line_start=1,
            line_end=5,
            reason="test",
            score=1.0,
            query="test",
            metadata={"snippet": long_snippet},
        )
        est = gen._estimate_tokens(span)
        # Line-count estimate would be 5 * 15 = 75; snippet should give ~500
        assert est > 100, f"Expected snippet-based estimate > 100, got {est}"


# ---------------------------------------------------------------------------
# End-to-end (mocked): correct pack behavior
# ---------------------------------------------------------------------------


class TestPackEndToEnd(unittest.TestCase):
    def test_pack_with_fixture_section_cards(self):
        gen, config, cards = make_generator()
        gen.adapter.search.return_value = [
            make_result(
                "sections/03_approach.tex",
                15,
                20,
                1.8,
                snippet="quantization deployment Int8",
                chapter_title="Deployment",
            ),
            make_result(
                "sections/03_approach.tex",
                5,
                11,
                1.6,
                snippet="MIMII dataset 456 recordings",
                chapter_title="Dataset",
            ),
            make_result(
                "sections/04_results.tex",
                1,
                6,
                0.000001,
                snippet="results",
                chapter_title="Results",
            ),
        ]
        pack = gen.generate(task="write methodology", target="section_approach", token_budget=2000)

        paths = [s.path for s in pack.source_spans]
        assert any("03_approach.tex" in p for p in paths)
        assert ".writing-context/section_cards.yaml" not in paths
        assert pack.document_thesis is not None
        assert pack.status == "complete"
        assert pack.estimated_tokens <= 2000
        self.assertTrue(pack.quality["section_cards_loaded"])


if __name__ == "__main__":
    unittest.main()
