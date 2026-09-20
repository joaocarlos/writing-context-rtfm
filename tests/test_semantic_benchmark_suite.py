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


def test_sqlite_invariance_cache_zero_delta() -> None:
    runner = SemanticBenchmarkRunner()
    results = runner.run_all_scenarios()
    s1 = results["scenario_1_math"]
    assert s1["thorough_warm"]["predict_calls_delta"] == 0
    assert s1["thorough_warm"]["latency_ms"] < s1["thorough_cold"]["latency_ms"]


def test_candidate_pool_scaling_sub_25ms() -> None:
    runner = SemanticBenchmarkRunner()
    latencies = runner.run_candidate_pool_scaling(pool_sizes=(5, 10, 20))
    for n, lat in latencies.items():
        assert lat < 25.0, f"Latency for pool N={n} was {lat} ms (exceeded 25 ms ceiling)"


def test_evaluate_quality_targets_scorecard() -> None:
    from scripts.benchmark_semantic_audit import evaluate_quality_targets

    runner = SemanticBenchmarkRunner()
    results = runner.run_all_scenarios()
    scaling = runner.run_candidate_pool_scaling(pool_sizes=(5, 10, 20))
    real = runner.run_real_corpus_evaluation()

    targets = evaluate_quality_targets(results, scaling=scaling, real=real)
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
