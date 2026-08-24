"""Comprehensive test suite for the 7 verified bottleneck architectural fixes."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import (
    ContextPackGenerator,
    apply_reciprocal_rank_fusion,
    compute_lexical_similarity_v2,
)
from writing_context_rtfm.hashing import (
    compute_retrieval_fingerprint,
    compute_task_hash,
)
from writing_context_rtfm.providers.bibtex import BibTeXProvider
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards
from writing_context_rtfm.server import (
    _sanitize_pack_for_output,
)
from writing_context_rtfm.storage import ExtensionStore


class TestVerifiedBottlenecksSuite(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.project_root = Path(self.tmp_dir)

        (self.project_root / ".writing-context").mkdir()
        (self.project_root / ".rtfm").mkdir()
        (self.project_root / "sections").mkdir()

        # Create target file with 100 lines
        self.target_file = self.project_root / "sections" / "03_approach.tex"
        self.target_file.write_text(
            "\n".join([f"Line {i}: Detailed methodology and equations with MIMII data." for i in range(1, 101)])
        )

        self.sc_file = self.project_root / ".writing-context" / "section_cards.yaml"
        self.sc_file.write_text("version: 1\ndocument:\n  title: Test\nsections:\n")

        self.rtfm_db = self.project_root / ".rtfm" / "library.db"
        self.rtfm_db.write_text("dummy rtfm content")

        self.config = AppConfig(
            version=1,
            rtfm=RTFMConfig(corpus="test_corpus", project_root=str(self.project_root)),
            context=ContextConfig(default_token_budget=4000, max_token_budget=16000),
            cache=CacheConfig(path=str(self.project_root / ".writing-context" / "cache.sqlite")),
            section_cards=SectionCardsConfig(path=str(self.sc_file)),
        )

        self.approach_card = SectionCard(
            id="section_approach",
            title="Approach",
            role="Methodology",
            path="sections/03_approach.tex",
            key_terms=["MIMII", "quantization"],
            unverified_key_terms=["speculative_term"],
            unverified_dependencies=["section_unverified"],
            must_preserve=["Split is fixed."],
            verified_facts=[
                {"value": "16-bit float baseline achieves 94.2% accuracy.", "status": "verified", "source": "sec_eval"}
            ],
        )


        self.section_cards = SectionCards(
            version=1,
            document=DocumentCard(title="Test Paper", thesis="TinyML fault detection."),
            sections={"section_approach": self.approach_card},
        )

        self.adapter = MagicMock()
        self.adapter.search.return_value = []

        self.store = ExtensionStore(self.config.cache.path)
        self.store.init_db()

        self.generator = ContextPackGenerator(
            self.config, self.section_cards, self.adapter, self.store
        )

    # -------------------------------------------------------------------------
    # Bottleneck 1 & 3: Strict Overflow Pre-Retrieval Stop Contract
    # -------------------------------------------------------------------------
    def test_strict_budget_overflow_stops_before_retrieval(self):
        """When strict_budget=True and baseline_tokens > token_budget, stop before retrieval.

        Must return empty source_spans, status='degraded', minimum_required_tokens,
        reason='budget_too_small_for_atomic_target', and must NOT call RTFM search.
        """
        pack = self.generator.generate(
            task="Revise methodology",
            target="section_approach",
            token_budget=50,  # Far below the target file's ~800 tokens
            strict_budget=True,
        )

        self.assertEqual(pack.status, "degraded")
        self.assertEqual(pack.source_spans, [])
        self.assertGreater(pack.quality.get("minimum_required_tokens", 0), 50)
        self.assertEqual(pack.quality.get("reason"), "budget_too_small_for_atomic_target")
        self.assertTrue(any("strict token_budget" in w.lower() for w in pack.warnings))
        # Verify RTFM search was never invoked
        self.adapter.search.assert_not_called()


    # -------------------------------------------------------------------------
    # Bottleneck 2 & 6: Score Decomposition and Lexical Similarity V2
    # -------------------------------------------------------------------------
    def test_source_span_score_decomposition(self):
        """SourceSpan preserves retrieval_score, fusion_score, structural_score, and score."""
        span = SourceSpan(
            path="sections/03_approach.tex",
            line_start=1,
            line_end=20,
            reason="Target text match",
            score=2.3,
            priority="essential",
            source_role="target_text",
            retrieval_score=0.8,
            fusion_score=0.5,
            structural_score=1.0,
        )
        self.assertEqual(span.score, 2.3)
        self.assertEqual(span.retrieval_score, 0.8)
        self.assertEqual(span.fusion_score, 0.5)
        self.assertEqual(span.structural_score, 1.0)

    def test_reciprocal_rank_fusion_with_query_families(self):
        """RRF preserves component scores and combines normalized stream ranks."""
        span1 = SourceSpan(
            path="file1.tex",
            line_start=1,
            line_end=10,
            reason="Hit 1",
            score=0.9,
            priority="supporting",
            retrieval_score=0.9,
            structural_score=0.4,
        )
        span2 = SourceSpan(
            path="file2.tex",
            line_start=1,
            line_end=10,
            reason="Hit 2",
            score=0.8,
            priority="supporting",
            retrieval_score=0.8,
            structural_score=0.0,
        )
        fused = apply_reciprocal_rank_fusion(
            {"task:query1": [span1], "terms:query2": [span1, span2]},
            weights={"task:query1": 1.0, "terms:query2": 0.8},
        )
        self.assertEqual(len(fused), 2)
        top = fused[0]
        self.assertEqual(top.path, "file1.tex")
        self.assertIsNotNone(top.fusion_score)
        self.assertEqual(top.retrieval_score, 0.9)
        self.assertEqual(top.structural_score, 0.4)
        self.assertGreater(top.score, 0.4)

    def test_lexical_similarity_v2_shingles(self):
        """compute_lexical_similarity_v2 detects near-duplicates using character/word shingles."""
        t1 = "Convolutional neural network architecture for acoustic vibration fault detection"
        t2 = "Convolutional neural network architecture for acoustic fault classification"
        t3 = "Thermodynamic properties of helium in cryogenic cooling pipes"

        sim_high = compute_lexical_similarity_v2(t1, t2)
        sim_low = compute_lexical_similarity_v2(t1, t3)

        self.assertGreater(sim_high, 0.5)
        self.assertLess(sim_low, 0.05)

    # -------------------------------------------------------------------------
    # Bottleneck 4: Feedback Storage with Source-Level Attribution & Summary
    # -------------------------------------------------------------------------
    def test_source_level_feedback_storage_and_summary(self):
        """Feedback can be recorded at whole-run or source-level and summarized by target."""
        run_id = "test-run-feedback-123"
        run_data = {
            "task_hash": "hash_123",
            "task": "write approach",
            "target": "section_approach",
            "corpus": "test_corpus",
            "token_budget": 2000,
            "config_hash": "cfg_hash",
            "section_cards_hash": "sc_hash",
            "rtfm_index_fingerprint": "fp_rtfm",
            "retrieval_fingerprint": "ret_fp_123",
        }
        self.store.store_pack(run_id, run_data, {"task": "write approach"}, [])

        # Record run-level feedback
        self.store.submit_feedback(run_id, "helpfulness", 0.9, "Very good overall context")

        # Record source-level feedback
        self.store.submit_feedback(
            run_id,
            "helpfulness",
            1.0,
            "Exact section needed",
            source_id="src_1",
            source_path="sections/03_approach.tex",
            line_start=1,
            line_end=50,
        )
        self.store.submit_feedback(
            run_id,
            "helpfulness",
            0.2,
            "Irrelevant distractor",
            source_id="src_2",
            source_path="sections/01_intro.tex",
            line_start=1,
            line_end=10,
        )

        records = self.store.get_feedback_for_target("section_approach")
        self.assertEqual(len(records), 3)

        summary = self.store.get_target_feedback_summary("section_approach")
        self.assertEqual(summary["target"], "section_approach")
        self.assertEqual(summary["metrics"]["helpfulness"]["count"], 3)
        self.assertAlmostEqual(summary["metrics"]["helpfulness"]["avg_value"], (0.9 + 1.0 + 0.2) / 3, places=2)

    # -------------------------------------------------------------------------
    # Bottleneck 5: QuerySpec and Card Uncertainties Telemetry
    # -------------------------------------------------------------------------
    def test_unverified_terms_route_to_card_uncertainties(self):
        """Unverified key terms and unverified dependencies populate quality.card_uncertainties."""
        pack = self.generator.generate(
            task="Revise methodology",
            target="section_approach",
            token_budget=4000,
        )

        self.assertIn("speculative_term", pack.quality.get("card_uncertainties", {}).get("unverified_key_terms", []))
        self.assertIn("section_unverified", pack.quality.get("card_uncertainties", {}).get("unverified_dependencies", []))
        # Ensure they did NOT create degraded status on their own
        self.assertEqual(pack.status, "complete")

    # -------------------------------------------------------------------------
    # Bottleneck 6: Cache Identity and Provider Fingerprints
    # -------------------------------------------------------------------------
    def test_cache_identity_includes_strict_budget_and_role_budgets(self):
        """Changing strict_budget or role_budgets alters task_hash, preventing cross-cache pollution."""
        hash_elastic = compute_task_hash(
            task="write methodology",
            target="section_approach",
            token_budget=2000,
            strict_budget=False,
            role_budgets={"target_text": 0.5, "reference": 0.5},
        )
        hash_strict = compute_task_hash(
            task="write methodology",
            target="section_approach",
            token_budget=2000,
            strict_budget=True,
            role_budgets={"target_text": 0.5, "reference": 0.5},
        )
        hash_diff_roles = compute_task_hash(
            task="write methodology",
            target="section_approach",
            token_budget=2000,
            strict_budget=False,
            role_budgets={"target_text": 0.8, "reference": 0.2},
        )
        self.assertNotEqual(hash_elastic, hash_strict)
        self.assertNotEqual(hash_elastic, hash_diff_roles)

    def test_provider_fingerprint_invalidation(self):
        """Touching provider source files invalidates the retrieval fingerprint."""
        bib_file = self.project_root / "references.bib"
        bib_file.write_text("@article{test, title={TinyML Acoustic}}\n")

        provider = BibTeXProvider(self.config)
        fp1 = provider.get_fingerprint(self.config)
        self.assertIsNotNone(fp1)

        ret_fp1 = compute_retrieval_fingerprint(self.rtfm_db, {"bibtex": fp1})

        # Modify bib file
        bib_file.write_text("@article{test2, title={Updated TinyML}}\n")
        fp2 = provider.get_fingerprint(self.config)
        self.assertNotEqual(fp1, fp2)


        ret_fp2 = compute_retrieval_fingerprint(self.rtfm_db, {"bibtex": fp2})
        self.assertNotEqual(ret_fp1, ret_fp2)

    def test_model_aware_openai_embeddings_composite_key(self):
        """openai_embeddings table uses composite key (chunk_id, model) preventing clobbering."""
        emb_3small = [0.1] * 1536
        emb_3large = [0.2] * 3072

        self.store.store_openai_embeddings([{"chunk_id": "chunk_1", "embedding": emb_3small, "model": "text-embedding-3-small"}])
        self.store.store_openai_embeddings([{"chunk_id": "chunk_1", "embedding": emb_3large, "model": "text-embedding-3-large"}])

        # Verify both exist independently
        stats_small = self.store.get_openai_embeddings_stats("text-embedding-3-small")
        stats_large = self.store.get_openai_embeddings_stats("text-embedding-3-large")

        self.assertEqual(stats_small["count"], 1)
        self.assertEqual(stats_large["count"], 1)

    # -------------------------------------------------------------------------
    # Bottleneck 7: Structured Output Modes and Excerpt Preservation
    # -------------------------------------------------------------------------
    def test_output_modes_prompt_structured_both(self):
        """Sanitization adheres to output_mode: prompt, structured, and both."""
        pack_data = {
            "task": "write intro",
            "target": "section_intro",
            "formatted_prompt": "Prompt text with detailed citations...",
            "source_spans": [
                {
                    "path": "sections/01_intro.tex",
                    "line_start": 1,
                    "line_end": 10,
                    "score": 0.9,
                    "metadata": {"snippet": "Actual intro excerpt text"},
                }
            ],
        }

        # 1. Prompt mode (default)
        prompt_out = _sanitize_pack_for_output(dict(pack_data), output_mode="prompt")
        self.assertIn("formatted_prompt", prompt_out)
        self.assertNotIn("excerpt", prompt_out["source_spans"][0])
        self.assertNotIn("metadata", prompt_out["source_spans"][0])

        # 2. Structured mode
        struct_out = _sanitize_pack_for_output(dict(pack_data), output_mode="structured")
        self.assertNotIn("formatted_prompt", struct_out)
        self.assertEqual(struct_out["source_spans"][0]["excerpt"], "Actual intro excerpt text")

        # 3. Both mode
        both_out = _sanitize_pack_for_output(dict(pack_data), output_mode="both")
        self.assertIn("formatted_prompt", both_out)
        self.assertEqual(both_out["source_spans"][0]["excerpt"], "Actual intro excerpt text")

    def test_prior_claims_provenance(self):
        """Prior claims extracted from dependency cards include section provenance."""
        dep_card = SectionCard(
            id="section_intro",
            title="Intro",
            role="Introduction",
            path="sections/01_intro.tex",
            verified_facts=[{"value": "Acoustic monitoring is non-invasive.", "status": "verified", "source": "intro"}],
        )
        approach_with_dep = SectionCard(
            id="section_approach",
            title="Approach",
            role="Methodology",
            path="sections/03_approach.tex",
            key_terms=["MIMII"],
            depends_on=["section_intro"],
        )
        self.section_cards.sections["section_intro"] = dep_card
        self.section_cards.sections["section_approach"] = approach_with_dep

        pack = self.generator.generate(
            task="Draft methodology",
            target="section_approach",
            token_budget=4000,
        )

        self.assertIn("[section_intro] Acoustic monitoring is non-invasive.", pack.prior_claims)



if __name__ == "__main__":
    unittest.main()
