"""Stress and boundary test suite for Sprint 6 Eixo 2: Tokenomics, Preflight Checks, 5h Rolling Session Constraints, Counterfactual Math, and CLI Commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from writing_context_rtfm.cli import (
    calibrate_command,
    explain_pack_command,
    stats_command,
)
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.token_budget import (
    ASTRA_EXPENSIVE_TIER_THRESHOLD,
    _is_latex_or_code_dense,
    compute_counterfactual_savings,
    count_tokens,
    preflight_budget_check,
)

# ============================================================================
# 1. EXTREME EDGE CASES IN count_tokens
# ============================================================================


class TestCountTokensStress:
    """Stress tests for multi-model token counting under adversarial and extreme inputs."""

    @pytest.mark.parametrize(
        "model_family",
        [
            "openai",
            "gpt",
            "gpt5",
            "gpt6",
            "anthropic",
            "claude",
            "claude5",
            "opus",
            "opus5",
            "gemini",
            "google",
            "gemini3.8",
            "gemini38",
            "generic",
            "unknown_model_xyz",
            "",
            None,
        ],
    )
    def test_empty_and_pure_whitespace(self, model_family: str | None):
        """Empty and whitespace strings must strictly return 0 tokens without error."""
        assert count_tokens("", model_family=model_family or "generic") == 0
        assert count_tokens("   ", model_family=model_family or "generic") == 0
        assert count_tokens("\t\n\r  \n\t", model_family=model_family or "generic") == 0
        assert count_tokens(" " * 50_000, model_family=model_family or "generic") == 0

    def test_unicode_emojis_stress(self):
        """Stress test with complex Unicode emojis, modifier sequences, and flags."""
        simple_emojis = "🎉🚀🔥✨💡📊" * 50
        assert count_tokens(simple_emojis, "openai") > 0
        assert count_tokens(simple_emojis, "anthropic") > 0
        assert count_tokens(simple_emojis, "gemini") > 0
        assert count_tokens(simple_emojis, "generic") > 0

        # Zero-width joiner (ZWJ) family & profession sequences
        zwj_sequences = "👨‍👩‍👧‍👦 👩‍🔬 🧑‍💻 👨‍🚀" * 30
        assert count_tokens(zwj_sequences, "openai") > 0
        assert count_tokens(zwj_sequences, "anthropic") > 0
        assert count_tokens(zwj_sequences, "gemini") > 0

        # Flag sequences (regional indicator symbols)
        flags = "🇧🇷 🇺🇸 🇬🇧 🇪🇺 🇯🇵 🇨🇦 🇩🇪" * 30
        assert count_tokens(flags, "openai") > 0
        assert count_tokens(flags, "anthropic") > 0
        assert count_tokens(flags, "gemini") > 0

        # Mixed text and emojis
        mixed = "Theorem 1 🚀 states that for all $\\epsilon > 0$ 💡, $\\lim_{n\\to\\infty} P_n = 1$ 🎉."
        assert count_tokens(mixed, "openai") > 0
        assert count_tokens(mixed, "anthropic") > 0
        assert count_tokens(mixed, "gemini") > 0

    def test_mixed_latex_with_unbalanced_brackets_and_broken_math(self):
        """Unbalanced braces, unclosed math environments, and broken macros must not crash."""
        adversarial_latex_samples = [
            r"\frac{a}{b",
            r"\begin{equation} x = 1",
            r"$x + y",
            r"\[ \int f(x) dx",
            r"\cite{author2024, another",
            r"\eqref{eq:missing_brace",
            r"\section{Unclosed section",
            r"}}}{{{",
            r"$$$$$$$",
            r"\newcommand{\foo}[2]{#1 #2",
            r"\begin{align} a &= b \\ c &= d \end{something_else}",
            r"\def\macro#1#2{ incomplete",
            r"\item[incomplete",
            r"\\\\\\[[[[{{{}}}}]]]$$$$$$$$",
        ]
        for sample in adversarial_latex_samples:
            for fam in ("openai", "anthropic", "gemini", "generic"):
                tokens = count_tokens(sample, fam)
                assert isinstance(tokens, int)
                assert tokens >= 1

    def test_non_ascii_prose_multilingual(self):
        """Verify token counts for CJK, Arabic, Cyrillic, Devanagari, and accented Latin."""
        cjk_text = "在本文中，我们分析了具有稀疏注意力和张量并行的变换器模型的计算复杂性。" * 10
        assert count_tokens(cjk_text, "openai") > 0
        assert count_tokens(cjk_text, "anthropic") > 0
        assert count_tokens(cjk_text, "gemini") > 0

        arabic_text = (
            "في هذا البحث، نقوم بتحليل تقارب المقدر في العينات المحدودة ونقدم دليلاً رياضياً شاملاً."
            * 10
        )
        assert count_tokens(arabic_text, "openai") > 0
        assert count_tokens(arabic_text, "anthropic") > 0
        assert count_tokens(arabic_text, "gemini") > 0

        cyrillic_text = (
            "В данной работе рассматривается асимптотическая сходимость нейросетевых моделей в распределенной среде."
            * 10
        )
        assert count_tokens(cyrillic_text, "openai") > 0
        assert count_tokens(cyrillic_text, "anthropic") > 0

        portuguese_text = (
            "Apresentamos uma metodologia rigorosa para otimização de hiperparâmetros com base em inferência bayesiana."
            * 10
        )
        assert count_tokens(portuguese_text, "openai") > 0
        assert count_tokens(portuguese_text, "anthropic") > 0

    def test_massive_payloads(self):
        """Massive payload string (500k to 1M characters) must be counted in under 1 second without recursion or crash."""
        massive_latex = (
            r"\section{Performance Evaluation}\n"
            r"We assess the performance under varying sparsity levels $\alpha \in [0, 1]$.\n"
            r"See Figure \ref{fig:res} and Table \ref{tab:benchmarks} \cite{smith2024}.\n"
        ) * 4000  # ~760,000 characters

        assert len(massive_latex) > 700_000
        tok_openai = count_tokens(massive_latex, "openai")
        tok_anthropic = count_tokens(massive_latex, "anthropic")
        tok_gemini = count_tokens(massive_latex, "gemini")
        tok_generic = count_tokens(massive_latex, "generic")

        assert tok_openai > 100_000
        assert tok_anthropic > 100_000
        assert tok_gemini > 100_000
        assert tok_generic > 100_000

    def test_deterministic_offline_fallback_simulation(self):
        """When tiktoken is unavailable or raises an unexpected error, deterministic BPE fallback applies."""
        sample_prose = (
            "This is a clean prose paragraph describing an empirical study on distributed training."
        )
        sample_latex = r"\begin{equation}\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}\end{equation}\cite{gauss1809}"

        with patch("writing_context_rtfm.token_budget._get_encoding", return_value=None):
            # Prose
            c_openai_prose = count_tokens(sample_prose, "openai")
            c_anthropic_prose = count_tokens(sample_prose, "anthropic")
            c_gemini_prose = count_tokens(sample_prose, "gemini")
            c_generic_prose = count_tokens(sample_prose, "generic")

            assert c_openai_prose == max(1, int(round(len(sample_prose) / 3.7)))
            assert c_anthropic_prose == max(1, int(round(len(sample_prose) / 3.4)))
            assert c_gemini_prose == max(1, int(round(len(sample_prose) / 3.6)))
            assert c_generic_prose == max(1, len(sample_prose) // 4)

            # LaTeX dense
            assert _is_latex_or_code_dense(sample_latex) is True
            c_openai_latex = count_tokens(sample_latex, "openai")
            c_anthropic_latex = count_tokens(sample_latex, "anthropic")
            c_gemini_latex = count_tokens(sample_latex, "gemini")

            assert c_openai_latex == max(1, int(round(len(sample_latex) / 3.2)))
            assert c_anthropic_latex == max(1, int(round(len(sample_latex) / 3.0)))
            assert c_gemini_latex == max(1, int(round(len(sample_latex) / 3.2)))


# ============================================================================
# 2. MATHEMATICAL BOUNDARIES IN preflight_budget_check & compute_counterfactual_savings
# ============================================================================


class TestPreflightAndSavingsBoundaries:
    """Rigorous boundary and deficit testing for preflight budget check and counterfactual math."""

    def test_preflight_budget_zero(self):
        """Zero budget must report feasible=False, zero elastic tokens, and correct deficit."""
        res = preflight_budget_check(budget=0, target_text="Hello world", thesis="Some thesis")
        assert res["feasible"] is False
        assert res["available_elastic_tokens"] == 0
        assert res["fixed_tokens"] > 0
        assert res["deficit"] == res["fixed_tokens"]
        assert res["recommended_min_budget"] == res["fixed_tokens"] + 600

    def test_preflight_budget_negative(self):
        """Negative budget must report feasible=False and deficit = |budget - fixed_tokens|."""
        res = preflight_budget_check(budget=-500, target_text="Target text")
        assert res["feasible"] is False
        assert res["available_elastic_tokens"] == 0
        assert res["deficit"] == 500 + res["fixed_tokens"]
        assert res["session"]["calls_remaining_by_token_headroom"] >= 0

    def test_preflight_budget_exact_equality_with_fixed_tokens(self):
        """When budget == fixed_tokens, available_elastic == 0 so feasible is False (needs > 0)."""
        target = "Target section text"
        tok = count_tokens(target)
        res = preflight_budget_check(budget=tok, target_text=target)
        assert res["feasible"] is False
        assert res["available_elastic_tokens"] == 0
        assert res["deficit"] == 0

    def test_preflight_budget_one_token_surplus(self):
        """When budget == fixed_tokens + 1, feasible becomes True with 1 elastic token."""
        target = "Target section text"
        tok = count_tokens(target)
        res = preflight_budget_check(budget=tok + 1, target_text=target)
        assert res["feasible"] is True
        assert res["available_elastic_tokens"] == 1
        assert res["deficit"] == 0

    def test_preflight_massive_budget_exceeding_threshold(self):
        """Massive single budget > 272k triggers single_call_tier_warning."""
        res = preflight_budget_check(budget=300_000)
        assert res["session"]["single_call_tier_warning"] is True
        assert res["session"]["threshold_exceeded"] is True
        assert res["session"]["projected_session_tokens"] == 300_000

    def test_preflight_exact_272k_boundaries(self):
        """Check exact boundary values around 272,000 threshold."""
        t = ASTRA_EXPENSIVE_TIER_THRESHOLD  # 272,000

        # Single call budget at 271,999 vs 272,000
        res_below = preflight_budget_check(budget=t - 1)
        assert res_below["session"]["single_call_tier_warning"] is False

        res_exact = preflight_budget_check(budget=t)
        assert res_exact["session"]["single_call_tier_warning"] is True

        res_above = preflight_budget_check(budget=t + 1)
        assert res_above["session"]["single_call_tier_warning"] is True

        # Cumulative session tokens at threshold
        res_cum_below = preflight_budget_check(budget=1000, session_tokens_used=t - 1001)
        assert res_cum_below["session"]["threshold_exceeded"] is False

        res_cum_exact = preflight_budget_check(budget=1000, session_tokens_used=t - 1000)
        assert res_cum_exact["session"]["threshold_exceeded"] is True

        res_cum_above = preflight_budget_check(budget=1000, session_tokens_used=t - 999)
        assert res_cum_above["session"]["threshold_exceeded"] is True

    def test_preflight_session_message_limit_boundaries(self):
        """Test exact session message limits, overages, and zero limit."""
        # 1 call remaining
        res_1 = preflight_budget_check(budget=2000, session_calls_used=24, session_message_limit=25)
        assert res_1["session"]["calls_remaining_by_message_limit"] == 1

        # Exact limit reached
        res_exact = preflight_budget_check(
            budget=2000, session_calls_used=25, session_message_limit=25
        )
        assert res_exact["session"]["calls_remaining_by_message_limit"] == 0
        assert res_exact["session"]["bottleneck_calls_remaining"] == 0
        assert res_exact["session"]["bottleneck_cause"] == "message_limit"

        # Over limit (e.g. 35 used out of 25)
        res_over = preflight_budget_check(
            budget=2000, session_calls_used=35, session_message_limit=25
        )
        assert res_over["session"]["calls_remaining_by_message_limit"] == 0
        assert res_over["session"]["bottleneck_calls_remaining"] == 0

        # Zero message limit
        res_zero_limit = preflight_budget_check(
            budget=2000, session_calls_used=0, session_message_limit=0
        )
        assert res_zero_limit["session"]["calls_remaining_by_message_limit"] == 0

    def test_preflight_bottleneck_switching(self):
        """Bottleneck switches accurately between message_limit and token_headroom."""
        # Headroom plenty (headroom ~222k tokens => ~88 calls), message limit 5 => limited by message_limit
        res_msg = preflight_budget_check(
            budget=2500,
            session_tokens_used=50_000,
            session_calls_used=20,
            session_message_limit=25,
        )
        assert res_msg["session"]["calls_remaining_by_message_limit"] == 5
        assert res_msg["session"]["calls_remaining_by_token_headroom"] > 50
        assert res_msg["session"]["bottleneck_cause"] == "message_limit"
        assert res_msg["session"]["bottleneck_calls_remaining"] == 5

        # Message limit plenty (50 remaining), but session tokens used 270,000 (headroom 2,000) => limited by token_headroom
        res_tok = preflight_budget_check(
            budget=2500,
            session_tokens_used=270_000,
            session_calls_used=5,
            session_message_limit=55,
        )
        assert res_tok["session"]["calls_remaining_by_message_limit"] == 50
        assert res_tok["session"]["calls_remaining_by_token_headroom"] == 0
        assert res_tok["session"]["bottleneck_cause"] == "token_headroom"
        assert res_tok["session"]["bottleneck_calls_remaining"] == 0

    def test_preflight_handles_none_and_floats(self):
        """Robustness against None values, floats, and negative session counters."""
        res = preflight_budget_check(
            budget=3000,
            session_tokens_used=None,  # type: ignore[arg-type]
            session_calls_used=None,  # type: ignore[arg-type]
            session_message_limit=None,  # type: ignore[arg-type]
        )
        assert res["session"]["session_tokens_used"] == 0
        assert res["session"]["session_calls_used"] == 0
        assert res["session"]["session_message_limit"] == 25

        # Float inputs
        res_float = preflight_budget_check(
            budget=3000,
            session_tokens_used=12345.67,  # type: ignore[arg-type]
            session_calls_used=8.0,  # type: ignore[arg-type]
            session_message_limit=30.0,  # type: ignore[arg-type]
        )
        assert res_float["session"]["session_tokens_used"] == 12345
        assert res_float["session"]["session_calls_used"] == 8
        assert res_float["session"]["session_message_limit"] == 30

    def test_compute_counterfactual_zero_and_negative_tokens(self):
        """Zero and negative tokens in counterfactual savings must never divide by zero."""
        # All zeros
        c_zero = compute_counterfactual_savings(baseline_doc_tokens=0, pack_tokens=0)
        assert c_zero["realistic_tokens_saved"] == 0
        assert c_zero["tokens_saved"] == 0
        assert c_zero["realistic_savings_ratio"] == 0.0
        assert c_zero["savings_ratio"] == 0.0
        assert c_zero["effective_pack_cost"] == 420

        # Negative numbers
        c_neg = compute_counterfactual_savings(
            baseline_doc_tokens=-1000,
            pack_tokens=-500,
            baseline_realistic_tokens=-200,
            schema_overhead_tokens=100,
        )
        assert c_neg["effective_pack_cost"] == -400
        assert c_neg["baseline_realistic_tokens"] >= 1
        assert c_neg["baseline_document_tokens"] >= 1
        assert c_neg["realistic_savings_ratio"] >= 0.0
        assert c_neg["savings_ratio"] >= 0.0

    def test_compute_counterfactual_pack_exceeding_baseline(self):
        """When pack cost exceeds baseline, savings must be 0 (no negative savings or negative ratio)."""
        c_deficit = compute_counterfactual_savings(
            baseline_doc_tokens=2000,
            pack_tokens=5000,
            baseline_realistic_tokens=1500,
            schema_overhead_tokens=420,
        )
        assert c_deficit["effective_pack_cost"] == 5420
        assert c_deficit["realistic_tokens_saved"] == 0
        assert c_deficit["realistic_savings_ratio"] == 0.0
        assert c_deficit["realistic_savings_percentage"] == 0.0
        assert c_deficit["tokens_saved"] == 0
        assert c_deficit["savings_ratio"] == 0.0

    def test_compute_counterfactual_massive_values(self):
        """Counterfactual math with 10^9 tokens maintains precision without overflow."""
        c_huge = compute_counterfactual_savings(
            baseline_doc_tokens=1_000_000_000,
            pack_tokens=100_000_000,
            baseline_realistic_tokens=500_000_000,
            schema_overhead_tokens=420,
        )
        effective = 100_000_420
        assert c_huge["effective_pack_cost"] == effective
        assert c_huge["realistic_tokens_saved"] == 500_000_000 - effective
        assert c_huge["tokens_saved"] == 1_000_000_000 - effective
        assert 79.0 < c_huge["realistic_savings_percentage"] < 81.0
        assert 89.0 < c_huge["savings_percentage"] < 91.0

    @pytest.mark.parametrize(
        "b_mode", ["section_neighborhood", "chapter", "full_doc", "custom_unknown"]
    )
    def test_compute_counterfactual_modes(self, b_mode: str):
        """Counterfactual modes automatically generate appropriate realistic baseline when not supplied."""
        c = compute_counterfactual_savings(
            baseline_doc_tokens=80_000,
            pack_tokens=2000,
            baseline_mode=b_mode,
        )
        assert c["baseline_mode"] == b_mode
        assert c["baseline_realistic_tokens"] > 0
        assert c["realistic_tokens_saved"] > 0


# ============================================================================
# 3. SESSION ROLLING WINDOW LOGIC IN get_session_tokenomics
# ============================================================================


class TestSessionRollingWindowStress:
    """Stress tests for SQLite 5-hour rolling session window, message counters, and empirical calibration."""

    @pytest.fixture
    def empty_store(self, tmp_path: Path) -> ExtensionStore:
        db_path = str(tmp_path / "cache.sqlite")
        store = ExtensionStore(db_path)
        store.init_db()
        return store

    def test_empty_database_session_and_stats(self, empty_store: ExtensionStore):
        """Empty database returns zeroed-out structures without division-by-zero or crashes."""
        sess = empty_store.get_session_tokenomics(window_hours=5, message_limit=25)
        assert sess["runs_in_window"] == 0
        assert sess["session_tokens_used"] == 0
        assert sess["session_calls_used"] == 0
        assert sess["calls_remaining_by_message_limit"] == 25
        assert sess["remaining_headroom"] == 272_000
        assert sess["threshold_exceeded"] is False
        assert sess["message_limit_exceeded"] is False
        assert sess["bottleneck_calls_remaining"] == 25
        assert sess["bottleneck_cause"] == "message_limit"

        stats = empty_store.get_tokenomics_stats()
        assert stats["total_runs"] == 0
        assert stats["total_pack_tokens"] == 0
        assert stats["total_tokens_saved"] == 0
        assert stats["avg_savings_percentage"] == 0.0
        assert stats["avg_realistic_savings_percentage"] == 0.0
        assert stats["empirical_calibrated_runs"] == 0
        assert stats["avg_empirical_savings_percentage"] == 0.0
        assert stats["recent_runs"] == []
        assert stats["by_mode"] == {}

    def test_past_timestamps_outside_window_excluded(self, empty_store: ExtensionStore):
        """Runs created older than 5 hours (e.g. 6h, 24h, 7 days ago) are excluded from rolling window."""
        with empty_store._connect() as conn:
            # 6 hours ago (outside 5h window)
            conn.execute(
                """
                INSERT INTO context_pack_runs (run_id, task_hash, task, target, token_budget, pack_tokens, schema_overhead_tokens, created_at)
                VALUES ('run-6h', 'h1', 'Old task 6h', 'sec1', 4000, 3000, 420, datetime('now', '-6 hours'))
            """
            )
            # 24 hours ago (outside 5h window)
            conn.execute(
                """
                INSERT INTO context_pack_runs (run_id, task_hash, task, target, token_budget, pack_tokens, schema_overhead_tokens, created_at)
                VALUES ('run-24h', 'h2', 'Old task 24h', 'sec2', 4000, 3500, 420, datetime('now', '-24 hours'))
            """
            )
            # 2 hours ago (inside 5h window)
            conn.execute(
                """
                INSERT INTO context_pack_runs (run_id, task_hash, task, target, token_budget, pack_tokens, schema_overhead_tokens, created_at)
                VALUES ('run-2h', 'h3', 'Recent task 2h', 'sec3', 4000, 2000, 420, datetime('now', '-2 hours'))
            """
            )
            conn.commit()

        # In 5-hour window: only the 2h run should be present
        sess_5h = empty_store.get_session_tokenomics(window_hours=5)
        assert sess_5h["runs_in_window"] == 1
        assert sess_5h["session_pack_tokens"] == 2000

        # In 1-hour window: 0 runs present
        sess_1h = empty_store.get_session_tokenomics(window_hours=1)
        assert sess_1h["runs_in_window"] == 0

        # In 10-hour window: 2 runs present (2h and 6h)
        sess_10h = empty_store.get_session_tokenomics(window_hours=10)
        assert sess_10h["runs_in_window"] == 2

    def test_future_timestamps_handled_safely(self, empty_store: ExtensionStore):
        """Runs with clock skew / future timestamps (e.g. +1h, +5h) are safely counted without error."""
        with empty_store._connect() as conn:
            conn.execute(
                """
                INSERT INTO context_pack_runs (run_id, task_hash, task, target, token_budget, pack_tokens, schema_overhead_tokens, created_at)
                VALUES ('run-future', 'hf', 'Future task', 'sec:fut', 4000, 2500, 420, datetime('now', '+1 hour'))
            """
            )
            conn.commit()

        sess = empty_store.get_session_tokenomics(window_hours=5)
        assert sess["runs_in_window"] == 1
        assert sess["session_pack_tokens"] == 2500

    def test_100_runs_aggregation_stress(self, empty_store: ExtensionStore):
        """Verify performance and numerical aggregation across 100 runs in rolling window."""
        with empty_store._connect() as conn:
            for i in range(100):
                conn.execute(
                    """
                    INSERT INTO context_pack_runs (
                        run_id, task_hash, task, target, token_budget,
                        pack_tokens, baseline_doc_tokens, tokens_saved, savings_ratio,
                        schema_overhead_tokens, latency_ms, baseline_realistic_tokens,
                        realistic_tokens_saved, realistic_savings_ratio, mode, created_at
                    )
                    VALUES (
                        ?, ?, 'Task description', 'sec:test', 4000,
                        2000, 40000, 37580, 0.9395,
                        420, 12.5, 8000,
                        5580, 0.6975, 'write', datetime('now', '-5 minutes')
                    )
                """,
                    (f"run-stress-{i:03d}", f"thash-{i:03d}"),
                )
            conn.commit()

        sess = empty_store.get_session_tokenomics(window_hours=5, message_limit=25)
        assert sess["runs_in_window"] == 100
        assert sess["session_calls_used"] == 100
        assert sess["session_message_limit"] == 25
        assert sess["calls_remaining_by_message_limit"] == 0
        assert sess["message_limit_exceeded"] is True
        assert sess["session_pack_tokens"] == 100 * 2000
        assert sess["bottleneck_calls_remaining"] == 0
        assert sess["bottleneck_cause"] == "message_limit"

        # Check tokenomics stats aggregates 100 runs cleanly
        stats = empty_store.get_tokenomics_stats()
        assert stats["total_runs"] == 100
        assert stats["total_pack_tokens"] == 200_000
        assert stats["total_realistic_baseline_tokens"] == 800_000
        assert stats["total_realistic_tokens_saved"] == 558_000
        assert stats["avg_realistic_savings_percentage"] == 69.75
        assert stats["avg_savings_percentage"] == 93.95
        assert len(stats["recent_runs"]) == 10  # Capped at 10

    def test_mixed_calibrated_and_uncalibrated_runs(self, empty_store: ExtensionStore):
        """Mixed runs with and without empirical calibration correctly compute empirical averages."""
        with empty_store._connect() as conn:
            # 5 uncalibrated runs
            for i in range(5):
                conn.execute(
                    """
                    INSERT INTO context_pack_runs (
                        run_id, task_hash, task, token_budget, pack_tokens,
                        schema_overhead_tokens, baseline_doc_tokens, tokens_saved,
                        baseline_realistic_tokens, realistic_tokens_saved, mode
                    )
                    VALUES (?, 'h', 'task', 4000, 2000, 420, 30000, 27580, 8000, 5580, 'write')
                """,
                    (f"run-uncal-{i}",),
                )

            # 5 calibrated runs
            for i in range(5):
                conn.execute(
                    """
                    INSERT INTO context_pack_runs (
                        run_id, task_hash, task, token_budget, pack_tokens,
                        schema_overhead_tokens, baseline_doc_tokens, tokens_saved,
                        baseline_realistic_tokens, realistic_tokens_saved, mode,
                        empirical_choice, empirical_tokens
                    )
                    VALUES (?, 'h', 'task', 4000, 2000, 420, 30000, 27580, 8000, 5580, 'rewrite', 'chapter', 25000)
                """,
                    (f"run-cal-{i}",),
                )
            conn.commit()

        stats = empty_store.get_tokenomics_stats()
        assert stats["total_runs"] == 10
        assert stats["empirical_calibrated_runs"] == 5

        # pack_tokens (2000) + schema (420) = 2420. empirical_tokens = 25000.
        # Savings = 1.0 - (2420 / 25000) = 1.0 - 0.0968 = 0.9032 (90.32%)
        assert 89.0 < stats["avg_empirical_savings_percentage"] < 91.0

        # Mode breakdown
        assert "write" in stats["by_mode"]
        assert "rewrite" in stats["by_mode"]
        assert stats["by_mode"]["write"]["runs"] == 5
        assert stats["by_mode"]["rewrite"]["runs"] == 5

    def test_single_pack_exceeding_threshold_flag(self, empty_store: ExtensionStore):
        """When a single pack exceeds 272k, threshold_exceeded is set to True."""
        with empty_store._connect() as conn:
            conn.execute(
                """
                INSERT INTO context_pack_runs (run_id, task_hash, task, token_budget, pack_tokens, created_at)
                VALUES ('run-huge', 'h1', 'Huge task', 300000, 275000, datetime('now', '-1 minute'))
            """
            )
            conn.commit()

        sess = empty_store.get_session_tokenomics(window_hours=5)
        assert sess["max_single_pack_observed"] == 275_000
        assert sess["threshold_exceeded"] is True


# ============================================================================
# 4. CLI COMMANDS STRESS AND OUTPUT VALIDATION
# ============================================================================


class TestCLICommandsStress:
    """Stress tests for CLI commands: stats, stats --session, calibrate, and explain-pack."""

    @pytest.fixture
    def test_workspace(self, tmp_path: Path) -> Path:
        """Create a minimal mock workspace with config and sample LaTeX files."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        dot_wc = ws / ".writing-context"
        dot_wc.mkdir()
        db_path = str(dot_wc / "cache.sqlite")

        (dot_wc / "config.yaml").write_text(
            f"version: 1\ncache:\n  enabled: true\n  path: '{db_path}'\n",
            encoding="utf-8",
        )
        (ws / "main.tex").write_text(
            "\\section{Introduction}\nThis is a sample introduction section.\n"
            "We examine context compression and token budgets \\cite{ref1}.\n",
            encoding="utf-8",
        )
        (ws / "refs.bib").write_text(
            "@article{ref1, title={Tokenomics in LLMs}, author={Shannon, C.}, year={1948}}\n",
            encoding="utf-8",
        )

        store = ExtensionStore(db_path)
        store.init_db()
        return ws

    def test_cli_stats_empty_workspace(self, test_workspace: Path, capsys):
        """'stats' on an empty workspace displays clean 0-run audit table and exits code 0."""
        args = argparse.Namespace(
            project_root=str(test_workspace),
            json=False,
            session=False,
            calibrate=False,
        )
        stats_command(args)
        out = capsys.readouterr().out
        assert "Writing Context RTFM — Tokenomics & Financial Audit" in out
        assert "Total Context Pack Runs:      0" in out
        assert "Realistic Baseline (Truth):           0 tok" in out

    def test_cli_stats_json_output(self, test_workspace: Path, capsys):
        """'stats --json' outputs valid machine-readable JSON matching the schema."""
        args = argparse.Namespace(
            project_root=str(test_workspace),
            json=True,
            session=False,
            calibrate=False,
        )
        stats_command(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "total_runs" in data
        assert "total_pack_tokens" in data
        assert "avg_realistic_savings_percentage" in data

    def test_cli_stats_session_with_message_limit_and_json(self, test_workspace: Path, capsys):
        """'stats --session --message-limit 10' audits the 5h Astra rolling window."""
        args = argparse.Namespace(
            project_root=str(test_workspace),
            json=False,
            session=True,
            message_limit=10,
            calibrate=False,
        )
        stats_command(args)
        out = capsys.readouterr().out
        assert "5-Hour Rolling Session Audit (Astra)" in out
        assert "Message Limit (Session):      10 msgs" in out
        assert "Single-Call Threshold:        272,000 tok" in out
        assert "Bottleneck Headroom:          ~10 calls remaining" in out

        # Session JSON flag
        args_json = argparse.Namespace(
            project_root=str(test_workspace),
            json=True,
            session=True,
            message_limit=10,
            calibrate=False,
        )
        stats_command(args_json)
        out_json = capsys.readouterr().out
        sess_data = json.loads(out_json)
        assert sess_data["session_message_limit"] == 10
        assert sess_data["single_call_threshold"] == 272_000

    def test_cli_calibrate_choices_1_to_4(self, test_workspace: Path, capsys):
        """'calibrate' supports choices 1, 2, 3, 4 and canonical names cleanly."""
        db_path = str(test_workspace / ".writing-context" / "cache.sqlite")
        store = ExtensionStore(db_path)

        # Seed 4 context pack runs
        for i in range(1, 5):
            run_data = {
                "task_hash": f"th_{i}",
                "task": f"Task {i}",
                "target": f"sec_{i}.tex",
                "token_budget": 4000,
                "pack_tokens": 2000,
                "baseline_doc_tokens": 40000,
                "tokens_saved": 37580,
                "savings_ratio": 0.9395,
                "schema_overhead_tokens": 420,
                "latency_ms": 10.0,
                "baseline_realistic_tokens": 8000,
                "baseline_tokens_raw": 25000,
                "is_capped": 0,
                "instruction_tokens": 50,
                "generation_tokens": 500,
                "realistic_tokens_saved": 5580,
                "realistic_savings_ratio": 0.6975,
                "baseline_mode": "section_neighborhood",
                "mode": "write",
            }
            store.store_pack(f"run-00{i}", run_data, {"task": f"Task {i}"}, [])

        # Calibrate run 1 with choice '1' (section)
        args1 = argparse.Namespace(project_root=str(test_workspace), run_id="run-001", choice="1")
        calibrate_command(args1)
        out1 = capsys.readouterr().out
        assert "Calibrated run [run-001] as 'section'" in out1

        # Calibrate run 2 with choice '2' (neighborhood)
        args2 = argparse.Namespace(project_root=str(test_workspace), run_id="run-002", choice="2")
        calibrate_command(args2)
        out2 = capsys.readouterr().out
        assert "Calibrated run [run-002] as 'neighborhood'" in out2

        # Calibrate run 3 with choice '3' (chapter)
        args3 = argparse.Namespace(project_root=str(test_workspace), run_id="run-003", choice="3")
        calibrate_command(args3)
        out3 = capsys.readouterr().out
        assert "Calibrated run [run-003] as 'chapter'" in out3

        # Calibrate run 4 with choice '4' (full_doc)
        args4 = argparse.Namespace(project_root=str(test_workspace), run_id="run-004", choice="4")
        calibrate_command(args4)
        out4 = capsys.readouterr().out
        assert "Calibrated run [run-004] as 'full_doc'" in out4

        # Verify stats now reflects 4 calibrated runs
        args_stats = argparse.Namespace(
            project_root=str(test_workspace), json=False, session=False, calibrate=False
        )
        stats_command(args_stats)
        out_stats = capsys.readouterr().out
        assert "Empirical User Baseline:      (4 runs calibrated)" in out_stats

    def test_cli_calibrate_empty_db_and_missing_run(self, tmp_path: Path, capsys):
        """'calibrate' gracefully handles empty store or missing run ID without crashing."""
        empty_ws = tmp_path / "empty_ws"
        empty_ws.mkdir()
        dot_wc = empty_ws / ".writing-context"
        dot_wc.mkdir()
        (dot_wc / "config.yaml").write_text(
            f"version: 1\ncache:\n  enabled: true\n  path: '{empty_ws}/cache.sqlite'\n",
            encoding="utf-8",
        )

        # Empty DB
        args_empty = argparse.Namespace(project_root=str(empty_ws), run_id=None, choice="1")
        calibrate_command(args_empty)
        assert "No context pack runs recorded yet to calibrate." in capsys.readouterr().out

        # Store with run, but target ID does not match
        store = ExtensionStore(str(empty_ws / "cache.sqlite"))
        store.init_db()
        store.store_pack(
            "run-real-123",
            {"task_hash": "th", "task": "T", "token_budget": 1000},
            {"task": "T"},
            [],
        )

        args_missing = argparse.Namespace(
            project_root=str(empty_ws), run_id="nonexistent-id", choice="1"
        )
        calibrate_command(args_missing)
        assert "Target run not found." in capsys.readouterr().out

    def test_cli_explain_pack_full_execution(self, test_workspace: Path, capsys):
        """'explain-pack' generates pack with diagnostics and renders Tokenomics & Counterfactual Audit."""
        args = argparse.Namespace(
            project_root=str(test_workspace),
            corpus=None,
            task="Introduce our methodology and literature background",
            target="main.tex",
            budget=4000,
            must_consider=[],
            task_type=None,
            line_start=1,
            line_end=3,
            pack_mode=None,
            role_budgets=None,
            mode="write",
            profile="fast",
            git_diff=False,
            json=False,
            command="explain-pack",
            explain=True,
        )
        explain_pack_command(args)
        out = capsys.readouterr().out

        assert "Candidate Funnel" in out
        assert "Selected Spans" in out
        assert "Tokenomics & Counterfactual Audit" in out
        assert "Realistic Baseline" in out
        assert "Effective Pack Cost:" in out
        assert "Pre-flight Budget:          FEASIBLE" in out
        assert "5h Rolling Session:" in out
        assert "Summary" in out
