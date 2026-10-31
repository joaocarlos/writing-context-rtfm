"""Integration and practical fixture tests for TokenEconomizer methodologies in writing-context-rtfm.

Tests the 3 core mechanisms adapted from microsoft/TokenEconomizer (arXiv:2610.22732)
against the real manuscript fixtures (`mini_latex_project` and `conference_paper_project`):
1. Experience-Driven Span Priors & Micro-skills (k >= 3 confirmations + contradiction decay)
2. Retrieval Loop Guard (detecting repeated high-overlap queries & exhausted pagination redirects)
3. Empirical P90 Token Budget Calibration (1.25 * P90 of successful runs)
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.schemas import RTFMResult
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore


class _MiniLatexFixtureEngine:
    """Deterministic retrieval engine over the real mini_latex_project manuscript files."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.chunks: list[dict[str, Any]] = []
        for tex_file in sorted((project_root / "sections").glob("*.tex")):
            rel_path = str(tex_file.relative_to(project_root))
            text = tex_file.read_text(encoding="utf-8")
            lines = text.splitlines()
            self.chunks.append(
                {
                    "path": rel_path,
                    "line_start": 1,
                    "line_end": len(lines),
                    "snippet": text,
                }
            )

    def search(self, query: str, corpus: str | None = None, limit: int = 10) -> list[RTFMResult]:
        q_words = {w.lower() for w in query.split() if len(w) > 2}
        results: list[RTFMResult] = []
        for chunk in self.chunks:
            snippet_lower = chunk["snippet"].lower()
            hits = sum(1 for w in q_words if w in snippet_lower)
            base_score = min(0.95, 0.35 + 0.12 * hits)
            results.append(
                RTFMResult(
                    path=chunk["path"],
                    line_start=chunk["line_start"],
                    line_end=chunk["line_end"],
                    snippet=chunk["snippet"],
                    score=round(base_score, 3),
                    metadata={"chapter_title": Path(chunk["path"]).stem},
                )
            )
        results.sort(key=lambda r: -(r.score or 0.0))
        return results[:limit]

    def sync(self, corpus: str | None = None) -> dict[str, Any]:
        return {"status": "ok"}


class TestTokenEconomizerMethodologiesOnFixtures(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        repo_root = Path(__file__).resolve().parent.parent
        self.mini_fixture_src = repo_root / "tests" / "fixtures" / "mini_latex_project"
        self.conf_fixture_src = repo_root / "tests" / "fixtures" / "conference_paper_project"

        # Copy mini_latex_project into isolated temp workspace
        self.mini_root = self.tmp_dir / "mini_latex_project"
        shutil.copytree(self.mini_fixture_src, self.mini_root)
        (self.mini_root / ".rtfm").mkdir(exist_ok=True)
        (self.mini_root / ".rtfm" / "library.db").write_text("mini-rtfm-index-v1", encoding="utf-8")

        self.mini_sc_path = self.mini_root / ".writing-context" / "section_cards.yaml"
        self.mini_cache_path = self.mini_root / ".writing-context" / "context_cache.sqlite"
        self.mini_config = AppConfig(
            version=1,
            rtfm=RTFMConfig(corpus="mini_corpus", project_root=str(self.mini_root)),
            context=ContextConfig(
                default_token_budget=4000,
                max_token_budget=16000,
                min_score=0.20,
            ),
            cache=CacheConfig(enabled=True, path=str(self.mini_cache_path)),
            section_cards=SectionCardsConfig(path=str(self.mini_sc_path), required=True),
        )
        self.mini_cards = load_section_cards(str(self.mini_sc_path), required=True)
        self.mini_engine = _MiniLatexFixtureEngine(self.mini_root)
        self.mini_store = ExtensionStore(str(self.mini_cache_path))
        self.mini_store.init_db()
        self.mini_generator = ContextPackGenerator(
            self.mini_config,
            self.mini_cards,
            self.mini_engine,  # type: ignore[arg-type]
            self.mini_store,
        )

    def tearDown(self) -> None:
        self.mini_store.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_1_experience_driven_span_demotion_promotion_and_micro_skills(self) -> None:
        """Verify k=3 confirmation rule for span demotion/promotion, micro-skills, and contradiction decay on mini_latex_project."""
        task = "Write the methodology section detailing dataset partitioning, CNN architecture, and 8-bit quantization."
        target = "section_approach"

        # Run 1 (Baseline before any feedback)
        pack_1 = self.mini_generator.generate(
            task=task,
            target=target,
            token_budget=4000,
        )
        self.assertIsNotNone(pack_1.run_id)
        scores_before = {s.path: s.score for s in pack_1.source_spans}
        self.assertIn("sections/02_related.tex", scores_before)
        self.assertIn("sections/01_intro.tex", scores_before)

        # Simulate 2 sessions with negative feedback on 02_related.tex and a constraint violation
        # Because k < 3, the experience profile should NOT yet activate demotion or micro-skills
        for i in range(1, 3):
            rid = pack_1.run_id if i == 1 else f"sim-run-{i}"
            if i > 1:
                self.mini_store.store_pack(
                    rid,
                    {
                        "task_hash": f"th-{i}",
                        "task": task,
                        "target": target,
                        "corpus": "mini_corpus",
                        "token_budget": 4000,
                        "pack_tokens": pack_1.estimated_tokens,
                        "mode": "write",
                    },
                    {"task": task},
                    [],
                )
            self.mini_store.submit_feedback(
                rid,
                "helpfulness",
                0.1,
                "Related work distracts from methodology specifics",
                source_path="sections/02_related.tex",
            )
            self.mini_store.submit_feedback(
                rid,
                "helpfulness",
                0.95,
                "Intro provides essential water pump context",
                source_path="sections/01_intro.tex",
            )
            self.mini_store.submit_feedback(
                rid,
                "constraint_violated",
                1.0,
                "Always state the 16 kHz sampling rate and 8-microphone circular array when describing MIMII.",
            )

        profile_k2 = self.mini_store.get_target_experience_profile(target=target)
        self.assertEqual(profile_k2["demoted_paths"], [])
        self.assertEqual(profile_k2["promoted_paths"], [])
        self.assertEqual(profile_k2["learned_micro_skills"], [])

        # Session 3: 3rd confirmation reaches k = 3 threshold
        rid_3 = "sim-run-3"
        self.mini_store.store_pack(
            rid_3,
            {
                "task_hash": "th-3",
                "task": task,
                "target": target,
                "corpus": "mini_corpus",
                "token_budget": 4000,
                "pack_tokens": pack_1.estimated_tokens,
                "mode": "write",
            },
            {"task": task},
            [],
        )
        self.mini_store.submit_feedback(
            rid_3,
            "helpfulness",
            0.15,
            "Related work distracts from methodology specifics",
            source_path="sections/02_related.tex",
        )
        self.mini_store.submit_feedback(
            rid_3,
            "helpfulness",
            0.90,
            "Intro provides essential water pump context",
            source_path="sections/01_intro.tex",
        )
        self.mini_store.submit_feedback(
            rid_3,
            "constraint_violated",
            1.0,
            "Always state the 16 kHz sampling rate and 8-microphone circular array when describing MIMII.",
        )

        # Now generate Pack 4: cache must auto-invalidate because experience fingerprint changed!
        pack_4 = self.mini_generator.generate(
            task=task,
            target=target,
            token_budget=4000,
        )
        self.assertFalse(pack_4.cache.hit if pack_4.cache else True)
        scores_after = {s.path: s.score for s in pack_4.source_spans}
        reasons_after = {s.path: s.reason for s in pack_4.source_spans}

        # Verify 01_intro.tex was promoted (+0.35 boost) and 02_related.tex was demoted (-0.45 penalty)
        self.assertGreater(scores_after["sections/01_intro.tex"], scores_before["sections/01_intro.tex"])
        self.assertIn("[experience +boost]", reasons_after["sections/01_intro.tex"])
        if "sections/02_related.tex" in scores_after:
            self.assertLess(
                scores_after["sections/02_related.tex"], scores_before["sections/02_related.tex"]
            )
            self.assertIn("[experience -demote]", reasons_after["sections/02_related.tex"])

        # Verify the learned micro-skill was compiled into pack constraints
        self.assertTrue(
            any(
                "[Learned Micro-skill]: Always state the 16 kHz sampling rate" in c
                for c in pack_4.constraints
            ),
            f"Expected learned micro-skill in constraints, got: {pack_4.constraints}",
        )

        # Test Contradiction Decay: a subsequent session gives positive feedback (0.95) to 02_related.tex
        self.mini_store.submit_feedback(
            pack_4.run_id or "run-4",
            "helpfulness",
            0.95,
            "Actually needed for CNN comparison",
            source_path="sections/02_related.tex",
        )
        profile_after_contradiction = self.mini_store.get_target_experience_profile(target=target)
        self.assertNotIn("sections/02_related.tex", profile_after_contradiction["demoted_paths"])

    def test_2_retrieval_loop_guard_and_pagination_redirect(self) -> None:
        """Verify Loop Guard detects >= 3 high-overlap queries on the same target and issues a redirect."""
        target = "section_results"
        tasks = [
            "Report classification accuracy and F1-score for MFCC and MFE on Arduino Nano 33 BLE Sense.",
            "Report validation accuracy and F1-score comparisons for MFCC and MFE on microcontrollers.",
            "Write results discussion covering MFCC and MFE validation accuracy and F1-score.",
        ]

        packs = []
        for t in tasks:
            p = self.mini_generator.generate(task=t, target=target, token_budget=4000)
            packs.append(p)

        # After 3 runs on the same target with identical/overlapping spans, the 4th run triggers Loop Guard
        pack_loop = self.mini_generator.generate(
            task="Summarize MFCC and MFE accuracy and F1-score results.",
            target=target,
            token_budget=4000,
        )
        loop_warnings = [w for w in pack_loop.warnings if "Loop Guard intervention" in w]
        self.assertEqual(len(loop_warnings), 1)
        self.assertIn("Autonomous Direct-Read Fallback", loop_warnings[0])
        self.assertIn("sections/04_results.tex", loop_warnings[0])
        # Verify informational Note: prefix keeps status='complete'
        self.assertEqual(pack_loop.status, "complete")

        # Verify that even a cached hit on an identical query preserves the Loop Guard warning
        pack_cached_loop = self.mini_generator.generate(
            task=tasks[0],
            target=target,
            token_budget=4000,
        )
        self.assertTrue(pack_cached_loop.cache.hit if pack_cached_loop.cache else False)
        cached_loop_warnings = [
            w for w in pack_cached_loop.warnings if "Loop Guard intervention" in w
        ]
        self.assertEqual(len(cached_loop_warnings), 1)

    def test_3_empirical_p90_budget_calibration_on_conference_paper_fixture(self) -> None:
        """Verify 1.25 * P90 empirical budget calibration on the real conference_paper_project SQLite & RTFM DB."""
        conf_root = self.tmp_dir / "conference_paper_project"
        shutil.copytree(self.conf_fixture_src, conf_root)

        # Verify real conference_paper_project manuscript files and initialize section cards on it
        conf_tex = conf_root / "conference_101719.tex"
        self.assertTrue(conf_tex.exists())
        from writing_context_rtfm.features import initialize_section_cards

        init_res = initialize_section_cards(str(conf_root))
        self.assertEqual(init_res["status"], "success")
        self.assertGreaterEqual(init_res["total_sections"], 1)

        cache_db = conf_root / ".writing-context" / "context_cache.sqlite"
        store = ExtensionStore(str(cache_db))
        store.init_db()
        try:
            # Record 5 successful runs for section_proposed with realistic token counts
            observed_tokens = [1120, 1240, 1310, 1380, 1490]
            for idx, tok in enumerate(observed_tokens):
                store.store_pack(
                    f"conf-run-{idx}",
                    {
                        "task_hash": f"conf-hash-{idx}",
                        "task": f"Revise Fuzzy Logic Controller and CRI equations {idx}",
                        "target": "section_proposed",
                        "corpus": "default",
                        "token_budget": 4000,
                        "pack_tokens": tok,
                        "mode": "rewrite",
                    },
                    {"task": "Revise FLC"},
                    [],
                )

            #Also record 1 failed/unhelpful run with huge bloated tokens (5500) — must be excluded from P90!
            store.store_pack(
                "conf-run-bad",
                {
                    "task_hash": "conf-hash-bad",
                    "task": "Bloated run",
                    "target": "section_proposed",
                    "corpus": "default",
                    "token_budget": 8000,
                    "pack_tokens": 5500,
                    "mode": "rewrite",
                },
                {"task": "Bloated run"},
                [],
            )
            store.submit_feedback("conf-run-bad", "helpfulness", 0.1, "Too much noise")

            exp = store.get_target_experience_profile(
                target="section_proposed", mode="rewrite"
            )
            emp_budget = exp["empirical_budget"]
            self.assertEqual(emp_budget["sample_count"], 5)
            self.assertEqual(emp_budget["p50_tokens"], 1310)
            self.assertEqual(emp_budget["p90_tokens"], 1490)
            self.assertEqual(emp_budget["recommended_budget_p90"], int(round(1.25 * 1490)))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
