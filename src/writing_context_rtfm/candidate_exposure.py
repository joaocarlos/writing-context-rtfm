"""Benchmark-only candidate exposure policies and Pilot v1 experiment runner."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from writing_context_rtfm.benchmark import (
    BenchmarkError,
    ProductionRetrievalBackend,
    _atomic_json,
    canonical_json,
    cases_for_stage,
    current_code_revision,
    load_cases,
    load_prepared,
    retrieval_metrics,
    sha256_text,
)
from writing_context_rtfm.config import load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import QuerySpec, RTFMResult
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.token_budget import estimate_tokens

EXPOSURE_BENCHMARK_VERSION = "candidate-exposure-pilot-v1"
EXPOSURE_COST_REPETITIONS = 5
EXPOSURE_POLICIES = (
    "current",
    "deep_task",
    "global_cap",
    "score_tail_adaptive",
    "progressive_coverage",
    "oracle_trigger",
)


@dataclass(frozen=True)
class ExposurePolicySpec:
    name: str
    shallow_depth: int = 10
    intermediate_depth: int = 20
    maximum_depth: int = 40
    global_unique_cap: int = 80
    score_tail_ratio: float = 0.20
    progressive_unique_ratio: float = 0.50
    offline_only: bool = False


POLICY_SPECS = {
    name: ExposurePolicySpec(name=name, offline_only=name == "oracle_trigger")
    for name in EXPOSURE_POLICIES
}

PROMOTION_RULE = {
    "minimum_cases_with_exposure_gain": 2,
    "minimum_final_selection_recall_delta": 0.0,
    "maximum_hard_constraint_violations": 0,
    "maximum_candidate_processing_increase_ratio": 1.0,
    "maximum_total_latency_p95_increase_ratio": 1.0,
}


def _result_key(result: RTFMResult) -> tuple[str, int | None, int | None, str]:
    snippet_hash = sha256_text(result.snippet or "")[:16]
    return (result.path, result.line_start, result.line_end, snippet_hash)


def _result_tokens(result: RTFMResult) -> int:
    return estimate_tokens(result.snippet or "")


def _score_ratio(results: Sequence[RTFMResult]) -> float:
    if not results:
        return 0.0
    first = float(results[0].score or 0.0)
    last = float(results[-1].score or 0.0)
    return 0.0 if first <= 0 else max(0.0, last / first)


def _expected_hit(
    streams: dict[int, Sequence[RTFMResult]],
    expected: dict[str, Any],
    *,
    excluded_suffixes: Sequence[str] = (),
) -> bool:
    for results in streams.values():
        for result in results:
            if any(result.path.casefold().endswith(value.casefold()) for value in excluded_suffixes):
                continue
            if result.path != str(expected.get("path", "")):
                continue
            start = expected.get("line_start")
            end = expected.get("line_end")
            if start is None or end is None:
                return True
            if result.line_start is None or result.line_end is None:
                continue
            if result.line_start <= int(end) and result.line_end >= int(start):
                return True
    return False


def _label_covered(label: str, streams: dict[int, Sequence[RTFMResult]]) -> bool:
    terms = {
        value
        for value in re.findall(r"[\w-]+", label.casefold())
        if len(value) > 2
    }
    if not terms:
        return True
    text = " ".join(
        result.snippet or "" for results in streams.values() for result in results
    ).casefold()
    present = len(terms & set(re.findall(r"[\w-]+", text)))
    return present >= max(1, math.ceil(len(terms) * 0.8))


class CandidateExposurePolicy:
    """Callable query-stream retriever used only by the exposure benchmark."""

    def __init__(
        self,
        adapter: RTFMAdapter,
        spec: ExposurePolicySpec,
        *,
        expected_sources: Sequence[dict[str, Any]] = (),
        excluded_suffixes: Sequence[str] = (".bib",),
    ) -> None:
        self.adapter = adapter
        self.spec = spec
        self.name = spec.name
        self.expected_sources = tuple(dict(value) for value in expected_sources)
        self.excluded_suffixes = tuple(excluded_suffixes)
        self.telemetry: dict[str, Any] = {}
        self._last_streams: dict[int, Sequence[RTFMResult]] = {}
        self._calls: list[dict[str, Any]] = []
        self._retrieved_candidates = 0
        self._retrieved_tokens = 0
        self._retrieval_latency_ms = 0.0
        self._depths: dict[int, int] = {}

    def _search(
        self, index: int, query: QuerySpec, corpus: str, depth: int
    ) -> list[RTFMResult]:
        started = time.perf_counter()
        results = list(self.adapter.search(query.text, corpus=corpus, limit=depth))
        latency = (time.perf_counter() - started) * 1000
        self._retrieval_latency_ms += latency
        self._retrieved_candidates += len(results)
        self._retrieved_tokens += sum(_result_tokens(result) for result in results)
        self._depths[index] = max(depth, self._depths.get(index, 0))
        self._calls.append(
            {
                "query_index": index,
                "query_type": query.query_type,
                "depth": depth,
                "returned": len(results),
                "latency_ms": round(latency, 3),
            }
        )
        return results

    @staticmethod
    def _task_index(specs: Sequence[QuerySpec]) -> int:
        for index, spec in enumerate(specs):
            if spec.query_type == "task":
                return index
        raise BenchmarkError("Candidate exposure benchmark requires a task query stream")

    def _search_all(
        self, specs: Sequence[QuerySpec], corpus: str, depth: int
    ) -> dict[int, list[RTFMResult]]:
        return {
            index: self._search(index, spec, corpus, depth)
            for index, spec in enumerate(specs)
        }

    def _global_cap(
        self, streams: dict[int, list[RTFMResult]]
    ) -> dict[int, list[RTFMResult]]:
        selected: dict[int, list[RTFMResult]] = {index: [] for index in streams}
        seen: set[tuple[str, int | None, int | None, str]] = set()
        maximum = max((len(results) for results in streams.values()), default=0)
        for rank in range(maximum):
            for index in sorted(streams):
                if rank >= len(streams[index]):
                    continue
                result = streams[index][rank]
                key = _result_key(result)
                if key in seen:
                    continue
                seen.add(key)
                selected[index].append(result)
                if len(seen) >= self.spec.global_unique_cap:
                    return selected
        return selected

    def _progressive_reasons(
        self,
        specs: Sequence[QuerySpec],
        streams: dict[int, list[RTFMResult]],
        obligations: Sequence[str],
    ) -> list[str]:
        reasons: list[str] = []
        for index, spec in enumerate(specs):
            if spec.is_verified and spec.query_type != "task_keyword" and not streams[index]:
                reasons.append(f"empty_verified_stream:{index}")
        for index, label in enumerate(obligations):
            if not _label_covered(label, streams):
                reasons.append(f"uncovered_obligation:{index}")
        total = sum(len(results) for results in streams.values())
        unique = len(
            {_result_key(result) for results in streams.values() for result in results}
        )
        unique_ratio = 1.0 if total == 0 else unique / total
        if total and unique_ratio <= self.spec.progressive_unique_ratio:
            reasons.append("low_unique_candidate_ratio")
        return reasons

    def __call__(
        self,
        specs: Sequence[QuerySpec],
        corpus: str,
        default_limit: int,
        obligations: Sequence[str],
    ) -> dict[int, Sequence[RTFMResult]]:
        if default_limit != self.spec.shallow_depth:
            raise BenchmarkError(
                f"Frozen shallow depth is {self.spec.shallow_depth}, got {default_limit}"
            )
        task_index = self._task_index(specs)
        trigger_reasons: list[str] = []

        if self.name == "current":
            streams = self._search_all(specs, corpus, self.spec.shallow_depth)
        elif self.name == "deep_task":
            streams = {
                index: self._search(
                    index,
                    spec,
                    corpus,
                    self.spec.maximum_depth
                    if index == task_index
                    else self.spec.shallow_depth,
                )
                for index, spec in enumerate(specs)
            }
        elif self.name == "global_cap":
            streams = self._global_cap(
                self._search_all(specs, corpus, self.spec.maximum_depth)
            )
        elif self.name == "score_tail_adaptive":
            streams = {}
            for index, spec in enumerate(specs):
                results = self._search(index, spec, corpus, self.spec.shallow_depth)
                if (
                    len(results) >= self.spec.shallow_depth
                    and _score_ratio(results) >= self.spec.score_tail_ratio
                ):
                    trigger_reasons.append(f"strong_score_tail:{index}:10")
                    results = self._search(
                        index, spec, corpus, self.spec.intermediate_depth
                    )
                    if (
                        len(results) >= self.spec.intermediate_depth
                        and _score_ratio(results) >= self.spec.score_tail_ratio
                    ):
                        trigger_reasons.append(f"strong_score_tail:{index}:20")
                        results = self._search(
                            index, spec, corpus, self.spec.maximum_depth
                        )
                streams[index] = results
        elif self.name == "progressive_coverage":
            streams = self._search_all(specs, corpus, self.spec.shallow_depth)
            trigger_reasons = self._progressive_reasons(specs, streams, obligations)
            if trigger_reasons:
                streams[task_index] = self._search(
                    task_index,
                    specs[task_index],
                    corpus,
                    self.spec.intermediate_depth,
                )
                remaining = self._progressive_reasons(specs, streams, obligations)
                if remaining:
                    streams[task_index] = self._search(
                        task_index,
                        specs[task_index],
                        corpus,
                        self.spec.maximum_depth,
                    )
                    trigger_reasons.extend(
                        value for value in remaining if value not in trigger_reasons
                    )
        elif self.name == "oracle_trigger":
            streams = self._search_all(specs, corpus, self.spec.shallow_depth)
            missing = [
                expected
                for expected in self.expected_sources
                if not _expected_hit(
                    streams, expected, excluded_suffixes=self.excluded_suffixes
                )
            ]
            if missing:
                trigger_reasons.append("oracle_expected_source_missing")
                for depth in (self.spec.intermediate_depth, self.spec.maximum_depth):
                    streams[task_index] = self._search(
                        task_index, specs[task_index], corpus, depth
                    )
                    missing = [
                        expected
                        for expected in missing
                        if not _expected_hit(
                            streams,
                            expected,
                            excluded_suffixes=self.excluded_suffixes,
                        )
                    ]
                    if not missing:
                        break
        else:
            raise BenchmarkError(f"Unknown candidate exposure policy: {self.name}")

        final_results = [result for values in streams.values() for result in values]
        query_payload = [
            {
                "text": spec.text,
                "query_type": spec.query_type,
                "family": spec.family,
                "weight": spec.weight,
                "is_verified": spec.is_verified,
            }
            for spec in specs
        ]
        self.telemetry = {
            "policy": self.name,
            "offline_only": self.spec.offline_only,
            "query_hash": sha256_text(canonical_json(query_payload)),
            "query_count": len(specs),
            "retrieval_calls": len(self._calls),
            "retrieved_candidates": self._retrieved_candidates,
            "retrieved_candidate_tokens": self._retrieved_tokens,
            "candidate_spans": len(final_results),
            "candidate_tokens_processed": sum(
                _result_tokens(result) for result in final_results
            ),
            "unique_candidates": len({_result_key(result) for result in final_results}),
            "retrieval_latency_ms": round(self._retrieval_latency_ms, 3),
            "stream_depths": {
                str(index): depth for index, depth in sorted(self._depths.items())
            },
            "expansion_triggered": any(
                depth > self.spec.shallow_depth for depth in self._depths.values()
            ),
            "trigger_reasons": trigger_reasons,
            "calls": self._calls,
        }
        self._last_streams = streams
        return streams

    def expected_source_exposure(
        self,
        expected_sources: Sequence[dict[str, Any]],
        *,
        effective: bool,
    ) -> list[bool]:
        """Return benchmark-label coverage before or after known query-result exclusions."""
        excluded = self.excluded_suffixes if effective else ()
        return [
            _expected_hit(
                self._last_streams,
                dict(expected),
                excluded_suffixes=excluded,
            )
            for expected in expected_sources
        ]


def _query_payload(case: Any, prepared: dict[str, Any]) -> list[dict[str, Any]]:
    workspace = Path(prepared["workspace"])
    config = load_config(str(workspace))
    cards = load_section_cards(config.section_cards.path, required=True)
    adapter = RTFMAdapter(project_root=str(workspace), allow_cli_fallback=False)
    with ExtensionStore(":memory:") as store:
        generator = ContextPackGenerator(config, cards, adapter, store, providers=[])
        specs, _, _, _ = generator._build_queries(
            case.task,
            case.target_selector,
            [],
            task_type=case.task_type,
            pack_mode="standard",
            has_line_range=True,
        )
    return [
        {
            "text": spec.text,
            "query_type": spec.query_type,
            "family": spec.family,
            "weight": spec.weight,
            "is_verified": spec.is_verified,
        }
        for spec in specs
    ]


def build_pilot_v1_freeze(
    cases_path: Path, private_root: Path
) -> dict[str, Any]:
    cases = cases_for_stage(load_cases(cases_path.resolve()), "pilot")
    if not cases or any(not case.annotations_resolved for case in cases):
        raise BenchmarkError("Pilot v1 freeze requires every pilot annotation to be resolved")
    frozen_cases = []
    for case in cases:
        prepared = load_prepared(case, private_root.resolve())
        query_payload = _query_payload(case, prepared)
        frozen_cases.append(
            {
                "case_id": case.id,
                "case_hash": case.case_hash,
                "cards_hash": prepared["cards_hash"],
                "rtfm_fingerprint": prepared["rtfm_fingerprint"],
                "query_hash": sha256_text(canonical_json(query_payload)),
                "query_count": len(query_payload),
            }
        )
    payload = {
        "freeze_version": 1,
        "benchmark_version": EXPOSURE_BENCHMARK_VERSION,
        "stage": "pilot",
        "case_count": len(cases),
        "fixed_downstream": {
            "token_budget": 6000,
            "max_source_spans": 35,
            "rrf_enabled": False,
            "composer": "production-context-pack",
            "candidate_normalization": "existing-final-score-v1",
            "deduplication": "canonical-path-line-snippet-downstream",
            "query_result_excluded_suffixes": [".bib"],
            "structured_bibtex_excludes_rtfm_bib": True,
        },
        "policy_specs": {
            name: asdict(spec) for name, spec in POLICY_SPECS.items()
        },
        "promotion_rule": PROMOTION_RULE,
        "cost_measurement": {
            "repetitions": EXPOSURE_COST_REPETITIONS,
            "policy_order": "deterministic-rotation-by-case-and-repetition",
        },
        "cases": frozen_cases,
    }
    return {**payload, "freeze_sha256": sha256_text(canonical_json(payload))}


def write_pilot_v1_freeze(
    cases_path: Path, private_root: Path, output: Path
) -> dict[str, Any]:
    freeze = build_pilot_v1_freeze(cases_path, private_root)
    _atomic_json(output, freeze, mode=0o600)
    return freeze


def _load_freeze(path: Path) -> dict[str, Any]:
    import json

    freeze = json.loads(path.read_text(encoding="utf-8"))
    recorded = str(freeze.pop("freeze_sha256", ""))
    observed = sha256_text(canonical_json(freeze))
    freeze["freeze_sha256"] = recorded
    if not recorded or recorded != observed:
        raise BenchmarkError("Pilot v1 freeze hash mismatch")
    return freeze


def run_candidate_exposure(
    cases_path: Path,
    private_root: Path,
    freeze_path: Path,
    output: Path,
) -> dict[str, Any]:
    freeze = _load_freeze(freeze_path)
    current = build_pilot_v1_freeze(cases_path, private_root)
    if current["freeze_sha256"] != freeze["freeze_sha256"]:
        raise BenchmarkError("Pilot v1 inputs changed after freeze")
    cases = cases_for_stage(load_cases(cases_path.resolve()), "pilot")
    frozen_by_id = {value["case_id"]: value for value in freeze["cases"]}
    backend = ProductionRetrievalBackend()
    records = []
    for repetition in range(1, EXPOSURE_COST_REPETITIONS + 1):
        for case_index, case in enumerate(cases):
            prepared = load_prepared(case, private_root.resolve())
            offset = (case_index + repetition - 1) % len(EXPOSURE_POLICIES)
            policy_order = EXPOSURE_POLICIES[offset:] + EXPOSURE_POLICIES[:offset]
            for name in policy_order:
                workspace = Path(prepared["workspace"])
                adapter = RTFMAdapter(
                    project_root=str(workspace), allow_cli_fallback=False
                )
                policy = CandidateExposurePolicy(
                    adapter,
                    POLICY_SPECS[name],
                    expected_sources=(
                        case.expected_source_spans if name == "oracle_trigger" else ()
                    ),
                )
                evidence = backend.retrieve_exposure(case, prepared, policy)
                if policy.telemetry["query_hash"] != frozen_by_id[case.id]["query_hash"]:
                    raise BenchmarkError(f"Frozen query mismatch for {case.id}/{name}")
                metrics = retrieval_metrics(case, evidence)
                records.append(
                    {
                        "case_id": case.id,
                        "case_hash": case.case_hash,
                        "policy": name,
                        "repetition": repetition,
                        "raw_query_exposure": policy.expected_source_exposure(
                            case.expected_source_spans, effective=False
                        ),
                        "post_exclusion_query_exposure": policy.expected_source_exposure(
                            case.expected_source_spans, effective=True
                        ),
                        "metrics": metrics,
                        "costs": {
                            **policy.telemetry,
                            **evidence.get("phase_latency_ms", {}),
                            "total_latency_ms": evidence["retrieval_latency_ms"],
                            "context_tokens": evidence["context_tokens"],
                        },
                        "hard_constraint_violations": int(
                            evidence["context_tokens"] > case.context_budget
                        ),
                    }
                )
    result = {
        "result_version": 1,
        "benchmark_version": EXPOSURE_BENCHMARK_VERSION,
        "freeze_sha256": freeze["freeze_sha256"],
        "code_revision": current_code_revision(),
        "records": records,
    }
    _atomic_json(output, result, mode=0o600)
    return result


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(float(ordered[lower]), 4)
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * (
        position - lower
    )
    return round(float(interpolated), 4)


def build_candidate_exposure_report(results: dict[str, Any]) -> dict[str, Any]:
    records = list(results["records"])
    by_policy = {
        name: [record for record in records if record["policy"] == name]
        for name in EXPOSURE_POLICIES
    }
    current_by_case = {
        record["case_id"]: record
        for record in by_policy["current"]
        if int(record.get("repetition", 1)) == 1
    }
    summaries: dict[str, Any] = {}
    for name, policy_records in by_policy.items():
        coverage_records = {
            record["case_id"]: record
            for record in policy_records
            if int(record.get("repetition", 1)) == 1
        }
        expected = 0
        raw_exposed = 0
        post_exclusion_exposed = 0
        exposed = 0
        selected = 0
        gained_cases = 0
        hard_violations = sum(
            int(record["hard_constraint_violations"]) for record in policy_records
        )
        for record in coverage_records.values():
            outcomes = record["metrics"]["expected_source_outcomes"]
            expected += len(outcomes)
            raw_exposed += sum(bool(value) for value in record["raw_query_exposure"])
            post_exclusion_exposed += sum(
                bool(value) for value in record["post_exclusion_query_exposure"]
            )
            exposed += sum(
                1
                for outcome in outcomes
                if outcome["selected"] or outcome["lost_after"] is not None
            )
            selected += sum(1 for outcome in outcomes if outcome["selected"])
            baseline = current_by_case[record["case_id"]]["metrics"][
                "expected_source_outcomes"
            ]
            baseline_exposed = sum(
                1
                for outcome in baseline
                if outcome["selected"] or outcome["lost_after"] is not None
            )
            policy_exposed = sum(
                1
                for outcome in outcomes
                if outcome["selected"] or outcome["lost_after"] is not None
            )
            gained_cases += int(policy_exposed > baseline_exposed)
        costs = {
            key: [float(record["costs"].get(key, 0.0)) for record in policy_records]
            for key in (
                "retrieved_candidates",
                "unique_candidates",
                "candidate_spans",
                "candidate_tokens_processed",
                "retrieval_latency_ms",
                "fusion",
                "composer",
                "total_latency_ms",
            )
        }
        summaries[name] = {
            "offline_only": POLICY_SPECS[name].offline_only,
            "expected_sources": expected,
            "raw_query_exposed_sources": raw_exposed,
            "post_exclusion_query_exposed_sources": post_exclusion_exposed,
            "exposed_sources": exposed,
            "selected_sources": selected,
            "raw_query_recall": round(raw_exposed / expected, 4) if expected else 0.0,
            "post_exclusion_query_recall": round(
                post_exclusion_exposed / expected, 4
            )
            if expected
            else 0.0,
            "raw_to_post_exclusion_loss": raw_exposed - post_exclusion_exposed,
            "post_exclusion_to_effective_delta": exposed - post_exclusion_exposed,
            "exposed_recall": round(exposed / expected, 4) if expected else 0.0,
            "final_selection_recall": round(selected / expected, 4) if expected else 0.0,
            "selection_regret": exposed - selected,
            "cases_with_exposure_gain": gained_cases,
            "hard_constraint_violations": hard_violations,
            "expansion_rate": round(
                sum(bool(record["costs"]["expansion_triggered"]) for record in policy_records)
                / max(1, len(policy_records)),
                4,
            ),
            "costs": {
                key: {
                    "median": _percentile(values, 0.5),
                    "p95": _percentile(values, 0.95),
                    "total": round(sum(values), 4),
                }
                for key, values in costs.items()
            },
        }

    baseline = summaries["current"]
    for name, summary in summaries.items():
        candidate_base = baseline["costs"]["candidate_spans"]["total"]
        latency_base = baseline["costs"]["total_latency_ms"]["p95"]
        candidate_increase = (
            0.0
            if candidate_base == 0
            else summary["costs"]["candidate_spans"]["total"] / candidate_base - 1.0
        )
        latency_increase = (
            0.0
            if latency_base == 0
            else summary["costs"]["total_latency_ms"]["p95"] / latency_base - 1.0
        )
        reasons = []
        if summary["offline_only"]:
            reasons.append("offline_only")
        if summary["cases_with_exposure_gain"] < PROMOTION_RULE[
            "minimum_cases_with_exposure_gain"
        ]:
            reasons.append("insufficient_case_replication")
        if summary["final_selection_recall"] < baseline["final_selection_recall"]:
            reasons.append("final_selection_regression")
        if summary["hard_constraint_violations"]:
            reasons.append("hard_constraint_violation")
        if candidate_increase > PROMOTION_RULE[
            "maximum_candidate_processing_increase_ratio"
        ]:
            reasons.append("candidate_cost_threshold_exceeded")
        if latency_increase > PROMOTION_RULE[
            "maximum_total_latency_p95_increase_ratio"
        ]:
            reasons.append("latency_threshold_exceeded")
        summary["candidate_processing_increase_ratio"] = round(candidate_increase, 4)
        summary["total_latency_p95_increase_ratio"] = round(latency_increase, 4)
        summary["promotion"] = {
            "eligible": name != "current" and not reasons,
            "reasons": reasons,
        }

    return {
        "report_version": 1,
        "benchmark_version": EXPOSURE_BENCHMARK_VERSION,
        "freeze_sha256": results["freeze_sha256"],
        "case_count": len(current_by_case),
        "cost_repetitions": EXPOSURE_COST_REPETITIONS,
        "fixed_downstream_composition": True,
        "promotion_rule": PROMOTION_RULE,
        "policies": summaries,
        "limitations": [
            "Expected-source coverage is an offline benchmark label, not a production trigger.",
            "Pilot v1 alone cannot promote a policy because gains must replicate across cases.",
            "Latency is repeated local wall-clock telemetry, not a cross-machine estimate.",
        ],
    }


def write_candidate_exposure_report(results_path: Path, output: Path) -> dict[str, Any]:
    import json

    results = json.loads(results_path.read_text(encoding="utf-8"))
    report = build_candidate_exposure_report(results)
    _atomic_json(output, report)
    return report
