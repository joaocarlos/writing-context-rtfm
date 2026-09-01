#!/usr/bin/env python3
"""Private foreground experiments for local embedding and reranking models."""

from __future__ import annotations

import argparse
import gc
import json
import platform
import resource
import statistics
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from writing_context_rtfm.benchmark import (
    PROMPT_VERSION,
    ArtifactStore,
    ProductionRetrievalBackend,
    _artifact_key,
    _atomic_json,
    _evidence_span,
    _within_budget,
    cases_for_stage,
    load_cases,
    load_prepared,
    retrieval_metrics,
    sha256_text,
)
from writing_context_rtfm.config import AppConfig, load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.local_models import LocalCrossEncoderReranker, LocalSentenceEncoder
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.providers.bibtex import BibTeXProvider
from writing_context_rtfm.providers.local_semantic import LocalSemanticSearchProvider
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import ContextPack, ProviderConfig
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore

EMBEDDING_MODELS = (
    "sentence-transformers/all-MiniLM-L6-v2",
    "mixedbread-ai/mxbai-embed-large-v1",
)
RERANKER_MODEL = "Alibaba-NLP/gte-reranker-modernbert-base"
RELEVANCE_METRICS = {
    "graded_source_recall",
    "graded_source_precision",
    "mrr",
    "ndcg",
    "irrelevant_source_rate",
    "obligation_coverage",
}


def _slug(model_id: str) -> str:
    return model_id.rsplit("/", 1)[-1].lower().replace("_", "-")


def condition_id(embedding_model: str, reranker_model: str | None) -> str:
    suffix = f"+{_slug(reranker_model)}" if reranker_model else ""
    return f"local-{_slug(embedding_model)}{suffix}"


def _peak_rss_mb() -> float:
    raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if platform.system() == "Darwin":
        return round(raw / (1024 * 1024), 3)
    return round(raw / 1024, 3)


class LocalModelRetrievalBackend:
    def __init__(
        self,
        *,
        embedding_model: str,
        reranker_model: str | None,
        device: str,
        model_cache_root: Path,
        embedding_batch_size: int,
        embedding_min_score: float,
        reranker_batch_size: int,
        reranker_max_length: int,
        reranker_candidate_limit: int,
        reranker_blend_weight: float,
    ):
        self.embedding_model = embedding_model
        self.reranker_model = reranker_model
        self.device = device
        self.model_cache_root = model_cache_root
        self.embedding_min_score = embedding_min_score
        self.encoder = LocalSentenceEncoder(
            embedding_model,
            device=device,
            batch_size=embedding_batch_size,
        )
        self.reranker = (
            LocalCrossEncoderReranker(
                reranker_model,
                device=device,
                batch_size=reranker_batch_size,
                max_length=reranker_max_length,
                candidate_limit=reranker_candidate_limit,
                blend_weight=reranker_blend_weight,
            )
            if reranker_model
            else None
        )

    def _config(self, prepared: dict[str, Any]) -> AppConfig:
        workspace = Path(prepared["workspace"])
        base = load_config(str(workspace))
        providers = dict(base.providers)
        providers["local_embeddings"] = ProviderConfig(
            enabled=True,
            extra={
                "model": self.embedding_model,
                "device": self.device,
                "sync_on_query": False,
                "min_score": self.embedding_min_score,
            },
        )
        cache_path = self.model_cache_root / str(prepared["case_hash"]) / "embeddings.sqlite"
        return replace(
            base,
            context=replace(base.context, enable_rrf=False),
            cache=replace(base.cache, enabled=False, path=str(cache_path)),
            providers=providers,
        )

    def retrieve(self, case: Any, prepared: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        workspace = Path(prepared["workspace"])
        config = self._config(prepared)
        cards = load_section_cards(config.section_cards.path, required=True)
        adapter = RTFMAdapter(project_root=str(workspace), allow_cli_fallback=False)
        local_provider = LocalSemanticSearchProvider(config, encoder=self.encoder)
        bibtex = BibTeXProvider(config)
        providers: list[BaseContextProvider] = [local_provider]
        if bibtex.is_available(config):
            providers.insert(0, bibtex)

        with ExtensionStore(config.cache.path) as store:
            store.init_db()
            sync_started = time.perf_counter()
            from writing_context_rtfm.utils import resolve_rtfm_db_path

            local_provider.sync_chunks(store, str(resolve_rtfm_db_path(workspace)))
            embedding_sync_ms = round((time.perf_counter() - sync_started) * 1000, 3)
            retrieval_started = time.perf_counter()
            generator = ContextPackGenerator(
                config,
                cards,
                adapter,
                store,
                providers=providers,
                reranker=self.reranker,
            )
            pack: ContextPack = generator.generate(
                task=case.task,
                target=case.target_selector,
                token_budget=case.context_budget,
                project_root=str(workspace),
                task_type=case.task_type,
                line_start=int(prepared["target_line_start"]),
                line_end=int(prepared["target_line_end"]),
                pack_mode="standard",
                strict_budget=True,
                output_mode="structured",
            )
            retrieval_latency_ms = round((time.perf_counter() - retrieval_started) * 1000, 3)

        spans = []
        for rank, source in enumerate(pack.source_spans, start=1):
            text = str((source.metadata or {}).get("snippet") or "")
            if text:
                spans.append(
                    _evidence_span(
                        path=source.path,
                        text=text,
                        line_start=source.line_start,
                        line_end=source.line_end,
                        score=source.score,
                        rank=rank,
                    )
                )
        spans = _within_budget(spans, case.context_budget)
        return {
            "strategy": condition_id(self.embedding_model, self.reranker_model),
            "spans": spans,
            "pack_metadata": {
                "status": pack.status,
                "warnings": list(pack.warnings),
                "atomic_coverage": (pack.quality or {}).get("atomic_coverage"),
            },
            "budget": case.context_budget,
            "context_tokens": sum(int(span["tokens"]) for span in spans),
            "retrieval_latency_ms": retrieval_latency_ms,
            "embedding_sync_ms": embedding_sync_ms,
            "total_latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "peak_rss_mb": _peak_rss_mb(),
        }


def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    metric_names = (
        "graded_source_recall",
        "graded_source_precision",
        "mrr",
        "ndcg",
        "irrelevant_source_rate",
        "duplicate_context_ratio",
        "obligation_coverage",
        "context_tokens",
        "retrieval_latency_ms",
        "embedding_sync_ms",
        "total_latency_ms",
        "peak_rss_mb",
    )
    aggregates: dict[str, Any] = {}
    for name in metric_names:
        values = [
            float(record["metrics"][name])
            for record in records
            if name in record["metrics"]
            and (
                name not in RELEVANCE_METRICS
                or bool(record["metrics"].get("annotation_resolved", False))
            )
        ]
        if values:
            aggregates[name] = {
                "case_count": len(values),
                "median": round(statistics.median(values), 4),
                "mean": round(statistics.fmean(values), 4),
                "min": round(min(values), 4),
                "max": round(max(values), 4),
            }
    return aggregates


def run(args: argparse.Namespace) -> dict[str, Any]:
    import torch

    torch.set_num_threads(args.torch_threads)
    torch.set_num_interop_threads(1)
    cases = cases_for_stage(load_cases(Path(args.cases).resolve()), args.stage)
    if args.limit_cases:
        cases = cases[: args.limit_cases]
    artifacts = ArtifactStore(Path(args.artifacts_root).resolve())
    private_root = Path(args.private_root).resolve()
    condition = condition_id(args.embedding_model, args.reranker_model)
    model_spec = json.dumps(
        {
            "embedding": args.embedding_model,
            "reranker": args.reranker_model,
            "device": args.device,
            "embedding_batch_size": args.embedding_batch_size,
            "embedding_min_score": args.embedding_min_score,
            "reranker_batch_size": args.reranker_batch_size,
            "reranker_max_length": args.reranker_max_length,
            "reranker_candidate_limit": args.reranker_candidate_limit,
            "reranker_blend_weight": args.reranker_blend_weight,
            "torch_threads": args.torch_threads,
        },
        sort_keys=True,
    )
    backend = LocalModelRetrievalBackend(
        embedding_model=args.embedding_model,
        reranker_model=args.reranker_model,
        device=args.device,
        model_cache_root=private_root / "local-model-cache",
        embedding_batch_size=args.embedding_batch_size,
        embedding_min_score=args.embedding_min_score,
        reranker_batch_size=args.reranker_batch_size,
        reranker_max_length=args.reranker_max_length,
        reranker_candidate_limit=args.reranker_candidate_limit,
        reranker_blend_weight=args.reranker_blend_weight,
    )
    control_backend = ProductionRetrievalBackend()
    grouped: dict[str, list[dict[str, Any]]] = {"control-pack-baseline": [], condition: []}
    for case in cases:
        prepared = load_prepared(case, private_root)
        for strategy in ("control-pack-baseline", condition):
            key = _artifact_key(
                kind="local-retrieval",
                case=case,
                prepared=prepared,
                strategy=strategy,
                repetition=0,
                model="none" if strategy == "control-pack-baseline" else model_spec,
                prompt_version=PROMPT_VERSION,
                code_revision=args.source_code_revision,
            )
            artifact = artifacts.load(key)
            if artifact is None:
                if args.source_code_revision:
                    raise RuntimeError(
                        f"Missing exact historical artifact for {case.id}/{strategy} at "
                        f"{args.source_code_revision}"
                    )
                if strategy == "control-pack-baseline":
                    evidence = control_backend.retrieve(case, prepared, "pack_baseline")
                else:
                    evidence = backend.retrieve(case, prepared)
                metrics = retrieval_metrics(case, evidence)
                for name in (
                    "embedding_sync_ms",
                    "total_latency_ms",
                    "peak_rss_mb",
                ):
                    if name in evidence:
                        metrics[name] = evidence[name]
                artifact = artifacts.save(
                    key,
                    {
                        "case_id": case.id,
                        "project_id": case.project_id,
                        "evidence": evidence,
                        "metrics": metrics,
                    },
                )
            grouped[strategy].append(artifact["payload"])

    report = {
        "report_version": "local-model-retrieval-v1",
        "stage": args.stage,
        "case_count": len(cases),
        "condition": condition,
        "source_code_revision": args.source_code_revision,
        "model_spec_sha256": sha256_text(model_spec),
        "conditions": {
            name: {
                "case_ids": [record["case_id"] for record in records],
                "aggregate": _aggregate(records),
            }
            for name, records in grouped.items()
        },
    }
    report_digest = sha256_text(json.dumps(report, sort_keys=True))
    report_path = (
        Path(args.artifacts_root).resolve() / "local-model-reports" / f"{report_digest}.json"
    )
    _atomic_json(report_path, report, mode=0o600)
    del backend
    gc.collect()
    return {**report, "private_report": str(report_path)}


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="benchmark_local_retrieval.py")
    command.add_argument("--cases", default="benchmark/cases.local.yaml")
    command.add_argument("--private-root", default="benchmark/private.local")
    command.add_argument("--artifacts-root", default="benchmark/artifacts.local")
    command.add_argument("--stage", default="pilot", choices=("pilot", "confirmation"))
    command.add_argument("--limit-cases", type=int)
    command.add_argument("--embedding-model", required=True, choices=EMBEDDING_MODELS)
    command.add_argument("--reranker-model", choices=(RERANKER_MODEL,))
    command.add_argument("--device", default="auto", choices=("auto", "cpu", "mps", "cuda"))
    command.add_argument("--embedding-batch-size", type=int, default=16)
    command.add_argument("--embedding-min-score", type=float, default=0.5)
    command.add_argument("--reranker-batch-size", type=int, default=8)
    command.add_argument("--reranker-max-length", type=int, default=512)
    command.add_argument("--reranker-candidate-limit", type=int, default=40)
    command.add_argument("--reranker-blend-weight", type=float, default=0.25)
    command.add_argument("--torch-threads", type=int, default=4)
    command.add_argument(
        "--source-code-revision",
        help="Reanalyze exact existing artifacts without running model inference",
    )
    return command


def main() -> int:
    result = run(parser().parse_args())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
