import hashlib
import json
import math
import re
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from writing_context_rtfm.candidate_trace import (
    compute_evidence_id,
    compute_span_candidate_id,
)
from writing_context_rtfm.config import load_config
from writing_context_rtfm.providers.bibtex import BibEntry, BibTeXProvider
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import OwnershipAuditRecord, SourceSpan
from writing_context_rtfm.token_budget import estimate_tokens


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


class BenchmarkError(RuntimeError):
    pass

BIBTEX_HANDOFF_BENCHMARK_VERSION = "bibtex-handoff-pilot-v1"
HANDOFF_COST_REPETITIONS = 5
HANDOFF_VARIANTS = ("current", "fallback", "reconstruction")
HANDOFF_CORRECTION_RULE = {
    "minimum_cases_with_handoff_gain": 1,
    "minimum_final_selection_recall_delta": 0.0,
    "maximum_duplicate_identity_count": 0,
    "maximum_hard_constraint_violations": 0,
    "maximum_candidate_processing_increase_ratio": 0.10,
    "maximum_total_latency_p95_increase_ratio": 0.20,
}


def _normalize_doi(value: str) -> str:
    normalized = value.strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix).strip()
    return normalized


def _normalize_title(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _entry_identities(entry: BibEntry) -> set[str]:
    identities = {f"citekey:{entry.citekey.casefold()}"}
    doi = _normalize_doi(entry.fields.get("doi", ""))
    title = _normalize_title(entry.title)
    if doi:
        identities.add(f"doi:{doi}")
    if title:
        identities.add(f"title:{title}")
    return identities


def _span_identities(span: SourceSpan) -> set[str]:
    metadata = span.metadata or {}
    identities = {str(value) for value in metadata.get("bibliographic_ids", [])}
    citekeys = [metadata.get("citekey"), *metadata.get("citekeys", [])]
    identities.update(
        f"citekey:{str(value).casefold()}" for value in citekeys if value
    )
    doi = _normalize_doi(str(metadata.get("doi") or ""))
    title = _normalize_title(str(metadata.get("title") or ""))
    if doi:
        identities.add(f"doi:{doi}")
    if title:
        identities.add(f"title:{title}")
    return identities


def _primary_identity(span: SourceSpan) -> str | None:
    identities = _span_identities(span)
    for prefix in ("citekey:", "doi:", "title:"):
        match = next((value for value in sorted(identities) if value.startswith(prefix)), None)
        if match:
            return match
    return None


def _candidate_id(span: SourceSpan) -> str:
    payload = {
        "path": span.path,
        "line_start": span.line_start,
        "line_end": span.line_end,
        "identities": sorted(_span_identities(span)),
        "snippet_hash": sha256_text(str((span.metadata or {}).get("snippet") or "")),
    }
    return sha256_text(canonical_json(payload))[:20]


def _paths_equal(left: str, right: str) -> bool:
    return Path(left).as_posix().casefold() == Path(right).as_posix().casefold()


def _entry_matches_expected(
    entry: BibEntry,
    expected_sources: Sequence[dict[str, Any]],
    root: Path,
) -> bool:
    try:
        entry_path = Path(entry.source_path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    for expected in expected_sources:
        if not _paths_equal(entry_path, str(expected.get("path", ""))):
            continue
        start = expected.get("line_start")
        end = expected.get("line_end")
        if start is None or end is None:
            return True
        if entry.line_start is None or entry.line_end is None:
            continue
        if entry.line_start <= int(end) and entry.line_end >= int(start):
            return True
    return False


def audit_passive_bibtex_ownership(
    excluded_spans: Sequence[SourceSpan],
    provider_spans: Sequence[SourceSpan],
    provider: BibTeXProvider | None = None,
) -> list[OwnershipAuditRecord]:
    """Perform passive ownership audit comparing excluded .bib spans with existing provider spans.

    In-memory comparison only. Never queries external services or generates additional candidates.
    """
    records: list[OwnershipAuditRecord] = []
    if not excluded_spans:
        return records

    provider_map: list[tuple[set[str], SourceSpan]] = [
        (_span_identities(ps), ps) for ps in provider_spans
    ]

    for span in excluded_spans:
        cid = compute_span_candidate_id(span)
        eid = compute_evidence_id(span) or f"source:{span.path}#{span.line_start}-{span.line_end}"
        identities: set[str] = _span_identities(span)
        if provider is not None and span.line_start is not None and span.line_end is not None:
            try:
                entries = provider.entries_for_source_span(span.path, span.line_start, span.line_end)
                for entry in entries:
                    identities.update(_entry_identities(entry))
            except Exception:
                pass

        matching_span: SourceSpan | None = None
        for p_ids, ps in provider_map:
            if identities & p_ids:
                matching_span = ps
                break

        if matching_span is not None:
            rep_cid = compute_span_candidate_id(matching_span)
            rep_prov = str((matching_span.metadata or {}).get("provider_id") or "bibtex")
            records.append(
                OwnershipAuditRecord(
                    candidate_id=cid,
                    evidence_id=eid,
                    path=span.path,
                    line_start=span.line_start,
                    line_end=span.line_end,
                    identities=sorted(identities),
                    replacement_found=True,
                    replacement_candidate_id=rep_cid,
                    replacement_provider=rep_prov,
                )
            )
        else:
            records.append(
                OwnershipAuditRecord(
                    candidate_id=cid,
                    evidence_id=eid,
                    path=span.path,
                    line_start=span.line_start,
                    line_end=span.line_end,
                    identities=sorted(identities),
                    replacement_found=False,
                    replacement_candidate_id=None,
                    replacement_provider=None,
                )
            )

    return records



class BibTeXHandoffPolicy:
    """Audit and optionally repair provider ownership for excluded BibTeX spans."""

    def __init__(
        self,
        provider: BibTeXProvider,
        variant: str,
        *,
        expected_sources: Sequence[dict[str, Any]] = (),
    ) -> None:
        if variant not in HANDOFF_VARIANTS:
            raise BenchmarkError(f"Unknown BibTeX handoff variant: {variant}")
        self.provider = provider
        self.variant = variant
        self.expected_sources = tuple(dict(value) for value in expected_sources)
        self.telemetry: dict[str, Any] = {}

    def __call__(
        self,
        excluded: Sequence[SourceSpan],
        provider_spans: Sequence[SourceSpan],
    ) -> Sequence[SourceSpan]:
        started = time.perf_counter()
        resolved: list[tuple[SourceSpan, list[BibEntry]]] = [
            (
                span,
                self.provider.entries_for_source_span(
                    span.path, span.line_start, span.line_end
                ),
            )
            for span in excluded
        ]
        existing_ids = set().union(
            *(_span_identities(span) for span in provider_spans), set()
        )
        additions: list[SourceSpan] = []
        reconstructed_keys: set[str] = set()

        for excluded_span, entries in resolved:
            missing = [
                entry for entry in entries if not (_entry_identities(entry) & existing_ids)
            ]
            if not missing or self.variant == "current":
                continue
            if self.variant == "fallback":
                identities = sorted(
                    set().union(*(_entry_identities(entry) for entry in entries), set())
                )
                additions.append(
                    replace(
                        excluded_span,
                        reason="RTFM BibTeX fallback: provider replacement absent",
                        priority="supporting",
                        source_role="reference",
                        metadata={
                            **(excluded_span.metadata or {}),
                            "provider_id": "rtfm_bibtex_fallback",
                            "handoff_mode": "fallback",
                            "citekeys": [entry.citekey for entry in entries],
                            "bibliographic_ids": identities,
                        },
                    )
                )
                continue
            for entry in missing:
                key = entry.citekey.casefold()
                if key in reconstructed_keys:
                    continue
                reconstructed_keys.add(key)
                reconstructed = self.provider.reconstruct_entry(
                    entry, score=excluded_span.score
                )
                additions.append(
                    replace(
                        reconstructed,
                        metadata={
                            **(reconstructed.metadata or {}),
                            "provider_id": "bibtex",
                            "handoff_mode": "reconstruction",
                            "bibliographic_ids": sorted(_entry_identities(entry)),
                        },
                    )
                )

        resulting_spans = [*provider_spans, *additions]
        resulting_ids = set().union(
            *(_span_identities(span) for span in resulting_spans), set()
        )
        root = Path(self.provider.config.rtfm.project_root)
        relevant_entries = {
            entry.citekey.casefold(): entry
            for _, entries in resolved
            for entry in entries
            if _entry_matches_expected(entry, self.expected_sources, root)
        }
        replaced_relevant = sum(
            bool(_entry_identities(entry) & resulting_ids)
            for entry in relevant_entries.values()
        )
        primary_counts = Counter(
            identity
            for span in resulting_spans
            if (identity := _primary_identity(span)) is not None
        )
        replacement_records = []
        for excluded_span, entries in resolved:
            for entry in entries:
                entry_idents = _entry_identities(entry)
                replacement = next(
                    (span for span in resulting_spans if entry_idents & _span_identities(span)),
                    None,
                )
                replacement_records.append(
                    {
                        "excluded_candidate_id": _candidate_id(excluded_span),
                        "excluded_entry_id": sha256_text(entry.citekey.casefold())[:20],
                        "replacement_candidate_id": (
                            _candidate_id(replacement) if replacement is not None else None
                        ),
                        "reason": "structured_bibtex_provider_ownership",
                        "replacement_provider": (
                            str((replacement.metadata or {}).get("provider_id"))
                            if replacement is not None
                            else None
                        ),
                        "equivalence": (
                            sorted(entry_idents & _span_identities(replacement))
                            if replacement is not None
                            else []
                        ),
                    }
                )
        self.telemetry = {
            "variant": self.variant,
            "excluded_candidate_count": len(excluded),
            "excluded_entry_count": sum(len(entries) for _, entries in resolved),
            "provider_candidate_count": len(provider_spans),
            "addition_count": len(additions),
            "additional_candidate_tokens": sum(
                estimate_tokens(str((span.metadata or {}).get("snippet") or ""))
                for span in additions
            ),
            "relevant_excluded_entries": len(relevant_entries),
            "replaced_relevant_entries": replaced_relevant,
            "bibliographic_handoff_recall": (
                round(replaced_relevant / len(relevant_entries), 4)
                if relevant_entries
                else 1.0
            ),
            "duplicate_identity_count": sum(
                count - 1 for count in primary_counts.values() if count > 1
            ),
            "replacement_records": replacement_records,
            "handoff_latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        return additions


def build_bibtex_handoff_freeze(
    cases_path: Path, private_root: Path
) -> dict[str, Any]:
    from writing_context_rtfm.candidate_exposure import build_pilot_v1_freeze

    base = build_pilot_v1_freeze(cases_path, private_root)
    payload = {
        "freeze_version": 1,
        "benchmark_version": BIBTEX_HANDOFF_BENCHMARK_VERSION,
        "stage": "pilot",
        "case_count": base["case_count"],
        "cases": base["cases"],
        "fixed_pipeline": {
            **base["fixed_downstream"],
            "candidate_exposure_policy": "current-top-10",
            "bibliographic_equivalence": ["citation_key", "doi", "normalized_title"],
            "candidate_processing_metric": "effective-deduplicated-candidate-spans",
        },
        "variants": list(HANDOFF_VARIANTS),
        "correction_rule": HANDOFF_CORRECTION_RULE,
        "cost_measurement": {
            "repetitions": HANDOFF_COST_REPETITIONS,
            "variant_order": "deterministic-rotation-by-case-and-repetition",
        },
    }
    return {**payload, "freeze_sha256": sha256_text(canonical_json(payload))}


def write_bibtex_handoff_freeze(
    cases_path: Path, private_root: Path, output: Path
) -> dict[str, Any]:
    from writing_context_rtfm.benchmark import _atomic_json

    freeze = build_bibtex_handoff_freeze(cases_path, private_root)
    _atomic_json(output, freeze, mode=0o600)
    return freeze


def _load_freeze(path: Path) -> dict[str, Any]:
    import json

    freeze = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(freeze, dict):
        raise BenchmarkError("Invalid BibTeX handoff freeze file")
    recorded = str(freeze.pop("freeze_sha256", ""))
    observed = sha256_text(canonical_json(freeze))
    freeze["freeze_sha256"] = recorded
    if not recorded or recorded != observed:
        raise BenchmarkError("BibTeX handoff freeze hash mismatch")
    return freeze


def run_bibtex_handoff(
    cases_path: Path,
    private_root: Path,
    freeze_path: Path,
    output: Path,
) -> dict[str, Any]:
    from writing_context_rtfm.benchmark import (
        ProductionRetrievalBackend,
        _atomic_json,
        cases_for_stage,
        current_code_revision,
        load_cases,
        load_prepared,
        retrieval_metrics,
    )
    from writing_context_rtfm.candidate_exposure import (
        POLICY_SPECS,
        CandidateExposurePolicy,
    )

    freeze = _load_freeze(freeze_path)
    current = build_bibtex_handoff_freeze(cases_path, private_root)
    if current["freeze_sha256"] != freeze["freeze_sha256"]:
        raise BenchmarkError("Pilot v1 inputs changed after BibTeX handoff freeze")
    cases = cases_for_stage(load_cases(cases_path.resolve()), "pilot")
    frozen_by_id = {value["case_id"]: value for value in freeze["cases"]}
    backend = ProductionRetrievalBackend()
    records = []
    for repetition in range(1, HANDOFF_COST_REPETITIONS + 1):
        for case_index, case in enumerate(cases):
            prepared = load_prepared(case, private_root.resolve())
            offset = (case_index + repetition - 1) % len(HANDOFF_VARIANTS)
            variant_order = HANDOFF_VARIANTS[offset:] + HANDOFF_VARIANTS[:offset]
            for variant in variant_order:
                workspace = Path(prepared["workspace"])
                config = load_config(str(workspace))
                adapter = RTFMAdapter(
                    project_root=str(workspace), allow_cli_fallback=False
                )
                exposure = CandidateExposurePolicy(adapter, POLICY_SPECS["current"])
                handoff = BibTeXHandoffPolicy(
                    BibTeXProvider(config),
                    variant,
                    expected_sources=case.expected_source_spans,
                )
                evidence = backend.retrieve_exposure(
                    case,
                    prepared,
                    exposure,
                    bibliography_handoff=handoff,
                )
                if exposure.telemetry["query_hash"] != frozen_by_id[case.id]["query_hash"]:
                    raise BenchmarkError(
                        f"Frozen query mismatch for {case.id}/{variant}"
                    )
                records.append(
                    {
                        "case_id": case.id,
                        "case_hash": case.case_hash,
                        "variant": variant,
                        "repetition": repetition,
                        "metrics": retrieval_metrics(case, evidence),
                        "handoff": handoff.telemetry,
                        "costs": {
                            **exposure.telemetry,
                            **evidence.get("phase_latency_ms", {}),
                            "effective_candidate_spans": len(
                                evidence.get("candidate_spans", [])
                            ),
                            "effective_candidate_tokens_processed": sum(
                                int(span.get("tokens", 0))
                                for span in evidence.get("candidate_spans", [])
                            ),
                            "handoff_latency_ms": handoff.telemetry[
                                "handoff_latency_ms"
                            ],
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
        "benchmark_version": BIBTEX_HANDOFF_BENCHMARK_VERSION,
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
    return round(
        float(
            ordered[lower]
            + (ordered[upper] - ordered[lower]) * (position - lower)
        ),
        4,
    )


def build_bibtex_handoff_report(results: dict[str, Any]) -> dict[str, Any]:
    records = list(results["records"])
    by_variant = {
        name: [record for record in records if record["variant"] == name]
        for name in HANDOFF_VARIANTS
    }
    summaries: dict[str, Any] = {}
    current_by_case = {
        record["case_id"]: record
        for record in by_variant["current"]
        if int(record.get("repetition", 1)) == 1
    }
    cost_keys = (
        "retrieved_candidates",
        "unique_candidates",
        "candidate_spans",
        "candidate_tokens_processed",
        "effective_candidate_spans",
        "effective_candidate_tokens_processed",
        "retrieval_latency_ms",
        "fusion",
        "composer",
        "handoff_latency_ms",
        "total_latency_ms",
        "context_tokens",
    )
    for variant, variant_records in by_variant.items():
        coverage_records = [
            record
            for record in variant_records
            if int(record.get("repetition", 1)) == 1
        ]
        expected = sum(
            len(record["metrics"]["expected_source_outcomes"])
            for record in coverage_records
        )
        exposed = sum(
            outcome["selected"] or outcome["lost_after"] is not None
            for record in coverage_records
            for outcome in record["metrics"]["expected_source_outcomes"]
        )
        selected = sum(
            outcome["selected"]
            for record in coverage_records
            for outcome in record["metrics"]["expected_source_outcomes"]
        )
        relevant_excluded = sum(
            int(record["handoff"]["relevant_excluded_entries"])
            for record in coverage_records
        )
        replaced_relevant = sum(
            int(record["handoff"]["replaced_relevant_entries"])
            for record in coverage_records
        )
        duplicate_identities = sum(
            int(record["handoff"]["duplicate_identity_count"])
            for record in coverage_records
        )
        gained_cases = sum(
            int(
                sum(
                    outcome["selected"]
                    for outcome in record["metrics"]["expected_source_outcomes"]
                )
                > sum(
                    outcome["selected"]
                    for outcome in current_by_case[record["case_id"]]["metrics"][
                        "expected_source_outcomes"
                    ]
                )
            )
            for record in coverage_records
        )
        cost_values = {
            key: [float(record["costs"].get(key, 0.0)) for record in variant_records]
            for key in cost_keys
        }
        summaries[variant] = {
            "expected_sources": expected,
            "exposed_sources": exposed,
            "selected_sources": selected,
            "exposed_recall": round(exposed / expected, 4) if expected else 0.0,
            "final_selection_recall": (
                round(selected / expected, 4) if expected else 0.0
            ),
            "selection_regret": exposed - selected,
            "relevant_excluded_entries": relevant_excluded,
            "replaced_relevant_entries": replaced_relevant,
            "bibliographic_handoff_recall": (
                round(replaced_relevant / relevant_excluded, 4)
                if relevant_excluded
                else 1.0
            ),
            "cases_with_final_selection_gain": gained_cases,
            "duplicate_identity_count": duplicate_identities,
            "hard_constraint_violations": sum(
                int(record["hard_constraint_violations"])
                for record in variant_records
            ),
            "addition_rate": round(
                sum(bool(record["handoff"]["addition_count"]) for record in variant_records)
                / max(1, len(variant_records)),
                4,
            ),
            "additional_candidate_tokens": {
                "median": _percentile(
                    [
                        float(record["handoff"]["additional_candidate_tokens"])
                        for record in variant_records
                    ],
                    0.5,
                ),
                "p95": _percentile(
                    [
                        float(record["handoff"]["additional_candidate_tokens"])
                        for record in variant_records
                    ],
                    0.95,
                ),
            },
            "costs": {
                key: {
                    "median": _percentile(values, 0.5),
                    "p95": _percentile(values, 0.95),
                    "total": round(sum(values), 4),
                }
                for key, values in cost_values.items()
            },
        }

    baseline = summaries["current"]
    for variant, summary in summaries.items():
        candidate_base = baseline["costs"]["effective_candidate_spans"]["total"]
        latency_base = baseline["costs"]["total_latency_ms"]["p95"]
        candidate_increase = (
            0.0
            if candidate_base == 0
            else summary["costs"]["effective_candidate_spans"]["total"]
            / candidate_base
            - 1.0
        )
        latency_increase = (
            0.0
            if latency_base == 0
            else summary["costs"]["total_latency_ms"]["p95"] / latency_base - 1.0
        )
        reasons = []
        if summary["cases_with_final_selection_gain"] < HANDOFF_CORRECTION_RULE[
            "minimum_cases_with_handoff_gain"
        ]:
            reasons.append("no_observed_handoff_gain")
        if summary["final_selection_recall"] < baseline["final_selection_recall"]:
            reasons.append("final_selection_regression")
        if summary["duplicate_identity_count"] > HANDOFF_CORRECTION_RULE[
            "maximum_duplicate_identity_count"
        ]:
            reasons.append("bibliographic_duplication")
        if summary["hard_constraint_violations"]:
            reasons.append("hard_constraint_violation")
        if candidate_increase > HANDOFF_CORRECTION_RULE[
            "maximum_candidate_processing_increase_ratio"
        ]:
            reasons.append("candidate_cost_threshold_exceeded")
        if latency_increase > HANDOFF_CORRECTION_RULE[
            "maximum_total_latency_p95_increase_ratio"
        ]:
            reasons.append("latency_threshold_exceeded")
        summary["candidate_processing_increase_ratio"] = round(candidate_increase, 4)
        summary["total_latency_p95_increase_ratio"] = round(latency_increase, 4)
        summary["correctness_fix_candidate"] = variant != "current" and not reasons
        summary["production_promotion"] = {
            "eligible": False,
            "reasons": ["pilot_only", *reasons],
        }

    return {
        "report_version": 1,
        "benchmark_version": BIBTEX_HANDOFF_BENCHMARK_VERSION,
        "freeze_sha256": results["freeze_sha256"],
        "case_count": len(current_by_case),
        "cost_repetitions": HANDOFF_COST_REPETITIONS,
        "fixed_retrieval_fusion_composer": True,
        "correction_rule": HANDOFF_CORRECTION_RULE,
        "variants": summaries,
        "limitations": [
            "Expected-source labels are used only for offline handoff measurement.",
            "The pilot contains one observed relevant BibTeX handoff failure.",
            "A passing variant is a correctness-fix candidate, not a promoted algorithm.",
            "Latency is repeated local wall-clock telemetry, not a cross-machine estimate.",
        ],
    }


def write_bibtex_handoff_report(results_path: Path, output: Path) -> dict[str, Any]:
    import json

    from writing_context_rtfm.benchmark import _atomic_json

    results = json.loads(results_path.read_text(encoding="utf-8"))
    report = build_bibtex_handoff_report(results)
    _atomic_json(output, report)
    return report
