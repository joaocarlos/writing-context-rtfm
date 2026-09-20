"""Tests for Sprint 6 Eixo 2: Tokenomics, multi-model token counting, preflight checks, session message limits, and empirical calibration."""

import argparse
from pathlib import Path

from writing_context_rtfm.cli import calibrate_command, stats_command
from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.token_budget import (
    ASTRA_EXPENSIVE_TIER_THRESHOLD,
    compute_counterfactual_savings,
    count_tokens,
    estimate_tokens,
    preflight_budget_check,
)


def test_count_tokens_multi_model():
    text = "In this section, we analyze the finite-sample convergence of our estimator \\cite{smith2023}."

    # Generic
    tok_gen = count_tokens(text, model_family="generic")
    assert tok_gen > 0
    assert tok_gen == estimate_tokens(text)

    # OpenAI
    tok_openai = count_tokens(text, model_family="openai")
    assert tok_openai > 0

    # Anthropic (Claude 5 / Opus 5)
    tok_anthropic = count_tokens(text, model_family="anthropic")
    assert tok_anthropic > 0

    # Gemini 3.8
    tok_gemini = count_tokens(text, model_family="gemini")
    assert tok_gemini > 0

    # Empty text
    assert count_tokens("", model_family="openai") == 0
    assert count_tokens("   ", model_family="gemini") == 0


def test_preflight_budget_check():
    # Feasible scenario
    pf = preflight_budget_check(
        budget=4000,
        target_text="A brief target text.",
        thesis="Core document thesis explaining the unified framework.",
        constraints=["Preserve LaTeX macros", "Maintain section flow"],
        macros={"\\E": "\\mathbb{E}", "\\P": "\\mathbb{P}"},
        model_family="openai",
        session_tokens_used=50000,
        session_calls_used=10,
        session_message_limit=25,
    )
    assert pf["feasible"] is True
    assert pf["fixed_tokens"] > 0
    assert pf["available_elastic_tokens"] > 0
    assert pf["deficit"] == 0
    assert pf["recommended_min_budget"] > pf["fixed_tokens"]
    assert pf["session"]["threshold_exceeded"] is False
    assert pf["session"]["remaining_headroom"] == ASTRA_EXPENSIVE_TIER_THRESHOLD - 50000
    assert pf["session"]["session_calls_used"] == 10
    assert pf["session"]["calls_remaining_by_message_limit"] == 15
    assert pf["session"]["bottleneck_cause"] == "message_limit"
    assert pf["session"]["bottleneck_calls_remaining"] == 15

    # Infeasible scenario (deficit)
    large_target = "Word " * 2000
    pf_deficit = preflight_budget_check(
        budget=500,
        target_text=large_target,
        thesis="Short thesis",
        model_family="openai",
    )
    assert pf_deficit["feasible"] is False
    assert pf_deficit["deficit"] > 0
    assert pf_deficit["available_elastic_tokens"] == 0
    assert pf_deficit["recommended_min_budget"] > 500

    # Single-call threshold alert (> 272K)
    pf_single = preflight_budget_check(
        budget=280000,
    )
    assert pf_single["session"]["single_call_tier_warning"] is True


def test_compute_counterfactual_savings():
    # Realistic comparison vs Naive comparison
    savings = compute_counterfactual_savings(
        baseline_doc_tokens=50000,
        pack_tokens=3000,
        baseline_realistic_tokens=10000,
        baseline_tokens_raw=25000,
        is_capped=True,
        baseline_mode="chapter",
        schema_overhead_tokens=420,
    )
    assert savings["effective_pack_cost"] == 3420

    # Realistic: 10,000 baseline -> 3,420 pack -> 6,580 saved (65.8%)
    assert savings["baseline_realistic_tokens"] == 10000
    assert savings["baseline_tokens_raw"] == 25000
    assert savings["is_capped"] is True
    assert savings["realistic_tokens_saved"] == 6580
    assert savings["realistic_savings_percentage"] == 65.8
    assert savings["baseline_mode"] == "chapter"

    # Naive: 50,000 baseline -> 3,420 pack -> 46,580 saved (93.16%)
    assert savings["baseline_document_tokens"] == 50000
    assert savings["tokens_saved"] == 46580
    assert savings["savings_percentage"] == 93.16


def test_store_tokenomics_migrations_and_stats(tmp_path: Path):
    db_path = str(tmp_path / "cache.sqlite")
    store = ExtensionStore(db_path)
    store.init_db()

    # Initially empty stats
    stats = store.get_tokenomics_stats()
    assert stats["total_runs"] == 0
    assert stats["total_tokens_saved"] == 0
    assert stats["avg_savings_percentage"] == 0.0
    assert stats["avg_realistic_savings_percentage"] == 0.0

    # Insert a run with tokenomics data
    run_id = "run-12345"
    run_data = {
        "task_hash": "thash123",
        "task": "Write methodology",
        "target": "sec:method",
        "corpus": "test_corpus",
        "token_budget": 4000,
        "config_hash": "cfg1",
        "section_cards_hash": "sc1",
        "rtfm_index_fingerprint": "idx1",
        "pack_tokens": 2500,
        "baseline_doc_tokens": 40000,
        "tokens_saved": 37080,
        "savings_ratio": 0.927,
        "schema_overhead_tokens": 420,
        "latency_ms": 150.5,
        "baseline_realistic_tokens": 10000,
        "baseline_tokens_raw": 25000,
        "is_capped": 1,
        "instruction_tokens": 80,
        "generation_tokens": 1200,
        "realistic_tokens_saved": 7080,
        "realistic_savings_ratio": 0.708,
        "baseline_mode": "chapter",
        "mode": "adapt",
    }
    payload = {"task": "Write methodology", "estimated_tokens": 2500, "source_spans": []}
    sources = [
        {
            "path": "main.tex",
            "line_start": 1,
            "line_end": 50,
            "score": 1.0,
            "reason": "Target",
            "selected": 1,
        }
    ]

    store.store_pack(run_id, run_data, payload, sources)

    # Re-check stats
    stats2 = store.get_tokenomics_stats()
    assert stats2["total_runs"] == 1
    assert stats2["total_pack_tokens"] == 2500
    assert stats2["total_baseline_tokens"] == 40000
    assert stats2["total_realistic_baseline_tokens"] == 10000
    assert stats2["total_baseline_tokens_raw"] == 25000
    assert stats2["total_instruction_tokens"] == 80
    assert stats2["total_generation_tokens"] == 1200
    assert stats2["total_realistic_tokens_saved"] == 7080
    assert stats2["avg_realistic_savings_percentage"] == 70.8
    assert stats2["avg_savings_percentage"] == 92.7

    # Check mode breakdown
    assert "adapt" in stats2["by_mode"]
    assert stats2["by_mode"]["adapt"]["runs"] == 1
    assert stats2["by_mode"]["adapt"]["capped_runs"] == 1
    assert stats2["by_mode"]["adapt"]["avg_raw_baseline"] == 25000.0

    # Test empirical calibration
    success = store.store_calibration(run_id, "chapter", empirical_tokens=22000)
    assert success is True

    # Check empirical stats
    stats_cal = store.get_tokenomics_stats()
    assert stats_cal["empirical_calibrated_runs"] == 1
    assert stats_cal["avg_empirical_savings_percentage"] > 80.0

    # Check 5h session rolling window with message limit
    sess = store.get_session_tokenomics(window_hours=5, message_limit=20)
    assert sess["runs_in_window"] == 1
    assert sess["session_calls_used"] == 1
    assert sess["session_message_limit"] == 20
    assert sess["calls_remaining_by_message_limit"] == 19
    # Total roundtrip = pack (2500) + instruction (80) + generation (1200) + schema (420) = 4200
    assert sess["session_tokens_used"] == 4200
    assert sess["bottleneck_calls_remaining"] == 19
    assert sess["bottleneck_cause"] == "message_limit"


def test_generator_tokenomics_and_preflight(tmp_path: Path):
    # Setup mock workspace
    tex_file = tmp_path / "section1.tex"
    tex_file.write_text(
        "\\section{Introduction}\nThis is the introduction text to our paper.\n"
        "We discuss various prior works \\cite{ref1} and formalize our theorems.\n",
        encoding="utf-8",
    )
    bib_file = tmp_path / "refs.bib"
    bib_file.write_text(
        "@article{ref1, title={Foundations of Context}, author={Turing, A.}, year={1950}}\n",
        encoding="utf-8",
    )

    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(corpus="test", project_root=str(tmp_path)),
        context=ContextConfig(max_token_budget=8000),
        cache=CacheConfig(enabled=True, path=str(tmp_path / "cache.sqlite")),
        section_cards=SectionCardsConfig(path=str(tmp_path / "cards.yaml"), required=False),
    )

    store = ExtensionStore(config.cache.path)
    store.init_db()

    adapter = RTFMAdapter(project_root=str(tmp_path))
    generator = ContextPackGenerator(config, None, adapter, store)

    # 1. Normal run (write mode)
    pack = generator.generate(
        task="Write introduction details",
        target="section1.tex",
        line_start=1,
        line_end=3,
        token_budget=3000,
        project_root=str(tmp_path),
        model_family="openai",
        mode="write",
    )

    assert pack.quality is not None
    assert "tokenomics" in pack.quality
    tok = pack.quality["tokenomics"]
    assert tok["model_family"] == "openai"
    assert tok["baseline_mode"] == "section_neighborhood"
    assert tok["baseline_realistic_tokens"] > 0
    assert tok["instruction_tokens"] > 0
    assert tok["pack_tokens"] > 0
    assert "preflight" in tok
    assert tok["preflight"]["feasible"] is True

    # 2. Adapt run
    pack_adapt = generator.generate(
        task="Adapt paper to handbook chapter",
        target="section1.tex",
        token_budget=4000,
        project_root=str(tmp_path),
        mode="adapt",
    )
    tok_adapt = pack_adapt.quality["tokenomics"]
    assert tok_adapt["baseline_mode"] == "chapter"
    assert tok_adapt["is_capped"] is True

    # 3. Deficit preflight run
    pack_small = generator.generate(
        task="Write introduction details",
        target="section1.tex",
        line_start=1,
        line_end=3,
        token_budget=5,  # Unreasonably tiny budget
        project_root=str(tmp_path),
        model_family="anthropic",
    )
    assert pack_small.quality is not None
    tok_small = pack_small.quality["tokenomics"]
    assert tok_small["preflight"]["feasible"] is False
    assert any("Pre-flight budget evaluation" in w for w in pack_small.warnings)


def test_cli_stats_and_calibration(tmp_path: Path, capsys):
    # Setup store with a run
    db_path = str(tmp_path / "cache.sqlite")
    config_path = tmp_path / ".writing-context"
    config_path.mkdir(parents=True, exist_ok=True)
    (config_path / "config.yaml").write_text(
        f"version: 1\ncache:\n  enabled: true\n  path: '{db_path}'\n",
        encoding="utf-8",
    )
    store = ExtensionStore(db_path)
    store.init_db()

    run_data = {
        "task_hash": "th1",
        "task": "Rewrite intro",
        "target": "intro.tex",
        "token_budget": 3000,
        "config_hash": "ch1",
        "section_cards_hash": "sh1",
        "pack_tokens": 2000,
        "baseline_doc_tokens": 30000,
        "tokens_saved": 27580,
        "savings_ratio": 0.919,
        "schema_overhead_tokens": 420,
        "latency_ms": 10.0,
        "baseline_realistic_tokens": 8000,
        "baseline_tokens_raw": 8000,
        "is_capped": 0,
        "instruction_tokens": 40,
        "generation_tokens": 800,
        "realistic_tokens_saved": 5580,
        "realistic_savings_ratio": 0.6975,
        "baseline_mode": "section_neighborhood",
        "mode": "rewrite",
    }
    store.store_pack("run-test-01", run_data, {"task": "Rewrite intro"}, [])

    # Test stats
    args = argparse.Namespace(
        project_root=str(tmp_path), json=False, session=False, calibrate=False
    )
    stats_command(args)
    captured = capsys.readouterr()
    assert "Tokenomics & Financial Audit" in captured.out
    assert "Realistic Baseline (Truth)" in captured.out

    # Test stats --session
    args_sess = argparse.Namespace(
        project_root=str(tmp_path), json=False, session=True, message_limit=25, calibrate=False
    )
    stats_command(args_sess)
    captured_sess = capsys.readouterr()
    assert "5-Hour Rolling Session Audit (Astra)" in captured_sess.out
    assert "Message Limit (Session):      25 msgs" in captured_sess.out
    assert "Messages Consumed:            1 msgs" in captured_sess.out
    assert "Calls Remaining (by msgs):    24 msgs" in captured_sess.out
    assert "Single-Call Threshold:        272,000 tok" in captured_sess.out

    # Test calibrate command
    args_cal = argparse.Namespace(
        project_root=str(tmp_path), run_id="run-test-01", choice="neighborhood"
    )
    calibrate_command(args_cal)
    captured_cal = capsys.readouterr()
    assert "[OK] Calibrated run [run-test] as 'neighborhood'" in captured_cal.out

    # Re-check stats shows empirical user baseline
    stats_command(args)
    captured_re = capsys.readouterr()
    assert "Empirical User Baseline:" in captured_re.out
