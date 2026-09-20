from __future__ import annotations

from scripts.benchmark_semantic_audit import (
    SemanticBenchmarkRunner,
    dcg_at_k,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


def test_ranking_metrics_edge_cases() -> None:
    # 1. Empty relevance list
    assert dcg_at_k([], 3) == 0.0
    assert ndcg_at_k([], 3) == 0.0
    assert mrr_at_k([], 3) == 0.0
    assert precision_at_k([], 3) == 0.0
    assert recall_at_k([], 5, 3) == 0.0
    assert recall_at_k([], 0, 3) == 1.0

    # 2. Perfect ranking: [1.0, 1.0, 0.0]
    perfect = [1.0, 1.0, 0.0]
    assert ndcg_at_k(perfect, 3) == 1.0
    assert mrr_at_k(perfect, 3) == 1.0
    assert precision_at_k(perfect, 2) == 1.0
    assert precision_at_k(perfect, 3) == 2.0 / 3.0
    assert recall_at_k(perfect, 2, 2) == 1.0

    # 3. Inverted ranking: [0.0, 0.0, 1.0]
    inverted = [0.0, 0.0, 1.0]
    assert mrr_at_k(inverted, 3) == 1.0 / 3.0
    assert precision_at_k(inverted, 3) == 1.0 / 3.0
    assert ndcg_at_k(inverted, 3) < 1.0
    assert ndcg_at_k(inverted, 3) > 0.0


def test_semantic_audit_scenarios_e2e() -> None:
    runner = SemanticBenchmarkRunner()
    results = runner.run_all_scenarios()

    # Scenario 1: Math / Survey Trap
    s1 = results["scenario_1_math"]
    assert s1["fast"]["recall@3"] == 0.0
    assert s1["thorough_cold"]["recall@3"] == 1.0
    assert s1["thorough_cold"]["ndcg@3"] == 1.0
    assert s1["auto"]["recall@3"] == 1.0
    assert s1["auto"]["escalated"] is True

    # Scenario 2: Biomedical Paraphrase (SmartBreathe domain)
    s2 = results["scenario_2_biomedical"]
    assert s2["thorough_recall@3"] > s2["fast_recall@3"]
    assert s2["thorough_ndcg@3"] > s2["fast_ndcg@3"]
    assert s2["thorough_mrr"] == 1.0

    # Scenario 3: Negative Control / Editorial Task
    s3 = results["scenario_3_negative_control"]
    assert s3["escalated"] is False
    assert s3["fer_zero"] is True

    # Scenario 4: Adversarial Keyword Stuffing
    s4 = results["scenario_4_adversarial"]
    assert s4["bm25_fooled_by_spam"] is True
    assert s4["reranker_promoted_gold"] is True

    # Scenario 5: Truncation Stress
    s5 = results["scenario_5_truncation"]
    assert s5["handled_cleanly"] is True
    assert s5["spans_returned"] > 0
    assert s5["minilm_score"] < s5["modernbert_score"]
    assert s5["minilm_reranker_score"] == 0.15
    assert s5["minilm_promoted"] is False
    assert s5["minilm_truncated"] is True
    assert s5["modernbert_reranker_score"] == 0.95
    assert s5["modernbert_promoted"] is True
    assert s5["modernbert_truncated"] is False


def test_sqlite_invariance_cache_zero_delta() -> None:
    runner = SemanticBenchmarkRunner()
    results = runner.run_all_scenarios()
    s1 = results["scenario_1_math"]
    assert s1["thorough_warm"]["predict_calls_delta"] == 0
    assert s1["thorough_warm"]["latency_ms"] < s1["thorough_cold"]["latency_ms"]


def test_sqlite_cache_invariance_comprehensive() -> None:
    runner = SemanticBenchmarkRunner()
    inv = runner.run_cache_invariance_audit()

    # a) Identical call (100% warm): 0 new predict calls
    assert inv["identical_warm_passed"] is True
    assert inv["identical_warm_delta"] == 0

    # b) Incremental editing: 1/16 edited -> exactly 1 neural inference, 15 cache hits (93.75% savings)
    assert inv["incremental_passed"] is True
    assert inv["incremental_edit_delta"] == 1
    assert inv["incremental_edit_hits"] == 15
    assert inv["incremental_edit_misses"] == 1
    assert inv["incremental_savings_pct"] == 93.75

    # c) Order permutation: Shuffled candidate list yields identical blended scores and 0 new predict calls
    assert inv["order_permutation_passed"] is True
    assert inv["order_permutation_delta"] == 0
    assert inv["order_permutation_scores_identical"] is True

    # d) Query isolation: Different task query computes fresh scores without cache poisoning
    assert inv["query_isolation_passed"] is True
    assert inv["query_isolation_delta"] == 1
    assert inv["query_isolation_task1_recheck_delta"] == 0


def test_candidate_pool_scaling_sub_15ms() -> None:
    runner = SemanticBenchmarkRunner()
    scaling = runner.run_candidate_pool_scaling(pool_sizes=(5, 10, 20, 50))

    # a) Raw neural scaling: evaluates exactly N pairs without artificial capping
    raw = scaling["raw_neural_scaling"]
    assert set(raw.keys()) == {5, 10, 20, 50}
    for n, lat in raw.items():
        assert lat < 25.0, f"Raw neural latency for N={n} was {lat} ms (exceeded 25 ms ceiling)"

    # b) Production pipeline bounded: pre-filtering bounds scoring to 20 candidates, keeping CPU latency <= 15 ms
    bounded = scaling["production_pipeline_bounded"]
    assert set(bounded.keys()) == {5, 10, 20, 50}
    assert bounded[20] <= 15.0, f"N=20 latency was {bounded[20]} ms (exceeded 15 ms budget)"
    assert bounded[50] <= 15.0, (
        f"N=50 bounded latency was {bounded[50]} ms (exceeded 15 ms budget; pre-filtering failed to bound)"
    )


def test_evaluate_quality_targets_scorecard() -> None:
    from scripts.benchmark_semantic_audit import evaluate_quality_targets

    runner = SemanticBenchmarkRunner()
    results = runner.run_all_scenarios()
    scaling = runner.run_candidate_pool_scaling(pool_sizes=(5, 10, 20, 50))
    real = runner.run_real_corpus_evaluation()

    targets = evaluate_quality_targets(results, scaling=scaling, real=real)
    target_ids = {t["id"] for t in targets}
    assert "T5-LATENCY-BUDGET-N20" in target_ids
    assert "T5B-BOUNDED-PIPELINE-N50" in target_ids
    assert "T6-INCREMENTAL-CACHE-INVARIANCE" in target_ids

    for target in targets:
        assert target["passed"] is True, f"Quality target {target['id']} failed: {target}"


def test_real_corpus_evaluation_p1_p4() -> None:
    runner = SemanticBenchmarkRunner()
    real = runner.run_real_corpus_evaluation()
    if real is None:
        # Se os diretórios locais não existirem em algum ambiente isolado, pular
        return
    cases = real.get("evaluated_cases", [])
    assert len(cases) == 4, f"Expected 4 real cases (P1-P4), got {len(cases)}"
    for c in cases:
        assert c["thorough_spans"] > 0, f"Case {c['case_id']} retrieved 0 spans"
        assert c["status"] == "complete", (
            f"Case {c['case_id']} status was not complete: {c['status']}"
        )
