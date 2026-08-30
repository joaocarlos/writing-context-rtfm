from __future__ import annotations

from dataclasses import replace

from writing_context_rtfm.candidate_exposure import (
    POLICY_SPECS,
    CandidateExposurePolicy,
    build_candidate_exposure_report,
)
from writing_context_rtfm.schemas import QuerySpec, RTFMResult


def _results(
    prefix: str,
    count: int,
    *,
    shared: bool = False,
    strong_tail: bool = True,
) -> list[RTFMResult]:
    values = []
    for index in range(1, count + 1):
        score = 1.0 - (index - 1) * (0.01 if strong_tail else 0.11)
        values.append(
            RTFMResult(
                path="shared.tex" if shared else f"{prefix}.tex",
                line_start=index,
                line_end=index,
                snippet=f"{prefix} evidence {index}",
                score=max(0.001, score),
                metadata={},
            )
        )
    return values


class FakeAdapter:
    def __init__(self, values: dict[str, list[RTFMResult]]) -> None:
        self.values = values
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, corpus: str, limit: int) -> list[RTFMResult]:
        assert corpus == "manuscript"
        self.calls.append((query, limit))
        return self.values[query][:limit]


SPECS = (
    QuerySpec(text="full task", query_type="task", family="task"),
    QuerySpec(
        text="keyword",
        query_type="task_keyword",
        family="task",
        weight=0.7,
    ),
)


def test_current_and_deep_task_change_only_anchor_depth() -> None:
    values = {"full task": _results("task", 40), "keyword": _results("keyword", 40)}

    current_adapter = FakeAdapter(values)
    current = CandidateExposurePolicy(current_adapter, POLICY_SPECS["current"])
    current(SPECS, "manuscript", 10, ())

    deep_adapter = FakeAdapter(values)
    deep = CandidateExposurePolicy(deep_adapter, POLICY_SPECS["deep_task"])
    streams = deep(SPECS, "manuscript", 10, ())

    assert current_adapter.calls == [("full task", 10), ("keyword", 10)]
    assert deep_adapter.calls == [("full task", 40), ("keyword", 10)]
    assert len(streams[0]) == 40
    assert len(streams[1]) == 10
    assert deep.telemetry["expansion_triggered"] is True
    assert deep.telemetry["query_count"] == 2


def test_global_cap_round_robins_unique_candidates() -> None:
    shared = _results("same", 4, shared=True)
    adapter = FakeAdapter({"full task": shared, "keyword": shared})
    spec = replace(
        POLICY_SPECS["global_cap"], maximum_depth=4, global_unique_cap=3
    )
    policy = CandidateExposurePolicy(adapter, spec)

    streams = policy(SPECS, "manuscript", 10, ())

    assert sum(len(values) for values in streams.values()) == 3
    assert policy.telemetry["unique_candidates"] == 3
    assert adapter.calls == [("full task", 4), ("keyword", 4)]


def test_score_tail_adaptive_expands_only_strong_tail_stream() -> None:
    adapter = FakeAdapter(
        {
            "full task": _results("task", 40, strong_tail=True),
            "keyword": _results("keyword", 10, strong_tail=False),
        }
    )
    policy = CandidateExposurePolicy(adapter, POLICY_SPECS["score_tail_adaptive"])

    streams = policy(SPECS, "manuscript", 10, ())

    assert adapter.calls == [
        ("full task", 10),
        ("full task", 20),
        ("full task", 40),
        ("keyword", 10),
    ]
    assert len(streams[0]) == 40
    assert len(streams[1]) == 10
    assert policy.telemetry["trigger_reasons"] == [
        "strong_score_tail:0:10",
        "strong_score_tail:0:20",
    ]


def test_progressive_coverage_expands_anchor_on_duplicate_concentration() -> None:
    shared = _results("same", 40, shared=True)
    adapter = FakeAdapter({"full task": shared, "keyword": shared})
    policy = CandidateExposurePolicy(adapter, POLICY_SPECS["progressive_coverage"])

    streams = policy(SPECS, "manuscript", 10, ())

    assert adapter.calls == [
        ("full task", 10),
        ("keyword", 10),
        ("full task", 20),
    ]
    assert len(streams[0]) == 20
    assert "low_unique_candidate_ratio" in policy.telemetry["trigger_reasons"]


def test_oracle_trigger_is_offline_and_stops_when_expected_source_appears() -> None:
    task = _results("task", 40)
    task[34] = RTFMResult(
        path="expected.tex",
        line_start=100,
        line_end=120,
        snippet="hidden benchmark target",
        score=0.4,
        metadata={},
    )
    adapter = FakeAdapter({"full task": task, "keyword": _results("keyword", 40)})
    policy = CandidateExposurePolicy(
        adapter,
        POLICY_SPECS["oracle_trigger"],
        expected_sources=(
            {"path": "expected.tex", "line_start": 105, "line_end": 110},
        ),
    )

    streams = policy(SPECS, "manuscript", 10, ())

    assert adapter.calls == [
        ("full task", 10),
        ("keyword", 10),
        ("full task", 20),
        ("full task", 40),
    ]
    assert streams[0][34].path == "expected.tex"
    assert policy.telemetry["offline_only"] is True
    assert policy.telemetry["trigger_reasons"] == ["oracle_expected_source_missing"]


def test_exposure_distinguishes_raw_bibtex_from_effective_query_results() -> None:
    task = _results("task", 10)
    task[3] = RTFMResult(
        path="references.bib",
        line_start=100,
        line_end=120,
        snippet="benchmark bibliography entry",
        score=0.7,
        metadata={},
    )
    policy = CandidateExposurePolicy(
        FakeAdapter({"full task": task, "keyword": _results("keyword", 10)}),
        POLICY_SPECS["current"],
    )
    expected = ({"path": "references.bib", "line_start": 105, "line_end": 110},)

    policy(SPECS, "manuscript", 10, ())

    assert policy.expected_source_exposure(expected, effective=False) == [True]
    assert policy.expected_source_exposure(expected, effective=True) == [False]


def test_report_never_promotes_oracle() -> None:
    def record(
        case_id: str, policy: str, exposed: bool, repetition: int
    ) -> dict[str, object]:
        outcome = {
            "selected": exposed,
            "lost_after": "retrieved" if exposed else None,
        }
        return {
            "case_id": case_id,
            "policy": policy,
            "repetition": repetition,
            "raw_query_exposure": [exposed],
            "post_exclusion_query_exposure": [exposed],
            "metrics": {"expected_source_outcomes": [outcome]},
            "hard_constraint_violations": 0,
            "costs": {
                "retrieved_candidates": 10,
                "unique_candidates": 10,
                "candidate_spans": 10,
                "candidate_tokens_processed": 100,
                "retrieval_latency_ms": 1,
                "fusion": 1,
                "composer": 1,
                "total_latency_ms": 3,
                "expansion_triggered": policy != "current",
            },
        }

    records = []
    for case_id in ("a", "b"):
        for policy in POLICY_SPECS:
            for repetition in range(1, 6):
                records.append(
                    record(
                        case_id,
                        policy,
                        exposed=policy != "current",
                        repetition=repetition,
                    )
                )
    report = build_candidate_exposure_report(
        {"freeze_sha256": "freeze", "records": records}
    )

    oracle = report["policies"]["oracle_trigger"]
    assert oracle["expected_sources"] == 2
    assert oracle["raw_query_exposed_sources"] == 2
    assert oracle["raw_to_post_exclusion_loss"] == 0
    assert oracle["post_exclusion_to_effective_delta"] == 0
    assert oracle["promotion"]["eligible"] is False
    assert "offline_only" in oracle["promotion"]["reasons"]
