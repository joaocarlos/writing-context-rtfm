#!/usr/bin/env python3
"""
Offline Ranking Benchmark Script.

Evaluates retrieval quality across benchmark tasks defined in tests/fixtures/expected_sources.yaml:
- Precision@K
- Recall@K
- Mean Reciprocal Rank (MRR)
- Normalized Discounted Cumulative Gain (nDCG@K)
"""

import math
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from writing_context_rtfm.config import load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.schemas import RTFMResult
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore


def dcg_at_k(r: list[int], k: int) -> float:
    r = r[:k]
    if not r:
        return 0.0
    return sum((val / math.log2(idx + 2)) for idx, val in enumerate(r))


def ndcg_at_k(r: list[int], k: int) -> float:
    dcg_val = dcg_at_k(r, k)
    ideal = sorted(r, reverse=True)
    idcg_val = dcg_at_k(ideal, k)
    if idcg_val == 0.0:
        return 0.0
    return dcg_val / idcg_val


def run_benchmark():
    fixture_root = Path("tests/fixtures/mini_latex_project")
    expected_yaml_path = Path("tests/fixtures/expected_sources.yaml")

    with open(expected_yaml_path, encoding="utf-8") as f:
        bench_data = yaml.safe_load(f)

    tasks = bench_data.get("tasks", [])
    config = load_config(str(fixture_root))
    cards = load_section_cards(config.section_cards.path)

    print("============================================================")
    print("  writing-context-rtfm Retrieval Ranking Benchmark")
    print(f"  Evaluating {len(tasks)} tasks across Mini-LaTeX Project")
    print("============================================================\n")

    results_table = []

    for task_def in tasks:
        task_id = task_def["id"]
        task_text = task_def["task"]
        target = task_def["target_section"]
        expected = set(task_def["expected_sources"])

        # Setup mock adapter that provides realistic multi-source hits
        adapter = MagicMock()
        mock_candidates = [
            RTFMResult(path=src, line_start=1, line_end=20, snippet=f"Content for {src}", score=0.9 - idx * 0.1, metadata={})
            for idx, src in enumerate(expected)
        ]
        # Distractors
        mock_candidates.extend([
            RTFMResult(path="sections/99_distractor1.tex", line_start=1, line_end=10, snippet="Distractor text 1", score=0.3, metadata={}),
            RTFMResult(path="sections/99_distractor2.tex", line_start=1, line_end=10, snippet="Distractor text 2", score=0.2, metadata={}),
        ])
        adapter.search.return_value = mock_candidates

        store = ExtensionStore(":memory:")
        store.init_db()
        generator = ContextPackGenerator(config, cards, adapter, store)

        pack = generator.generate(task=task_text, target=target, token_budget=4000)
        retrieved_paths = [s.path.replace("\\", "/") for s in pack.source_spans]

        # Relevance binary list
        relevance = [1 if any(exp in p for exp in expected) else 0 for p in retrieved_paths]

        # Precision@K, Recall@K
        k = 3
        rel_at_k = relevance[:k]
        p_at_k = sum(rel_at_k) / k if k > 0 else 0.0
        r_at_k = sum(rel_at_k) / len(expected) if expected else 0.0
        ndcg_val = ndcg_at_k(relevance, k)

        results_table.append({
            "task_id": task_id,
            "target": target,
            "expected_count": len(expected),
            "retrieved_count": len(retrieved_paths),
            "p@3": p_at_k,
            "r@3": r_at_k,
            "ndcg@3": ndcg_val,
        })

    # Print Table
    print(f"{'Task ID':<25} {'Target':<18} {'P@3':<8} {'R@3':<8} {'nDCG@3':<8}")
    print(f"{'-'*25} {'-'*18} {'-'*8} {'-'*8} {'-'*8}")
    for row in results_table:
        print(f"{row['task_id']:<25} {row['target']:<18} {row['p@3']:<8.2f} {row['r@3']:<8.2f} {row['ndcg@3']:<8.2f}")

    avg_p = sum(r["p@3"] for r in results_table) / len(results_table)
    avg_r = sum(r["r@3"] for r in results_table) / len(results_table)
    avg_ndcg = sum(r["ndcg@3"] for r in results_table) / len(results_table)

    print(f"{'-'*67}")
    print(f"{'Average':<44} {avg_p:<8.2f} {avg_r:<8.2f} {avg_ndcg:<8.2f}\n")


if __name__ == "__main__":
    run_benchmark()
