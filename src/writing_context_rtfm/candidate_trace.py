"""Candidate lifecycle tracing, deterministic identity resolution, and funnel metrics."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from writing_context_rtfm.schemas import (
    CandidateFunnel,
    CandidateTrace,
    CandidateTraceEvent,
    ContextPackDiagnostics,
    OwnershipAuditRecord,
    RTFMResult,
    SourceSpan,
)


def sanitize_path(path: str, project_root: str | None = None) -> str:
    """Sanitize and relativize path to protect against leaking absolute local machine paths."""
    if not path:
        return ""
    posix_path = Path(path).as_posix()
    if project_root:
        try:
            root_posix = Path(project_root).resolve().as_posix()
            resolved = Path(path).resolve().as_posix()
            if resolved.startswith(root_posix):
                rel = resolved[len(root_posix) :].lstrip("/")
                if rel:
                    return rel
        except Exception:
            pass

    # If path starts with leading slash or known user directory roots, strip user prefix
    if posix_path.startswith("/"):
        parts = [p for p in posix_path.split("/") if p]
        if len(parts) >= 3 and parts[0] in ("Users", "home", "private", "var"):
            return "/".join(parts[-2:])
        return posix_path.lstrip("/")
    return posix_path.lstrip("./")


def _normalize_snippet(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.split())


def _normalize_path(path: str, project_root: str | None = None) -> str:
    return sanitize_path(path, project_root).casefold()


def compute_candidate_id(
    path: str,
    line_start: int | None,
    line_end: int | None,
    snippet: str | None = None,
    provider: str | None = None,
) -> str:
    """Compute a deterministic 16-character SHA-256 identifier for a pipeline candidate instance."""
    normalized_path = _normalize_path(path)
    normalized_snip = _normalize_snippet(snippet)
    start_str = str(line_start) if line_start is not None else ""
    end_str = str(line_end) if line_end is not None else ""
    prov_str = (provider or "").strip().casefold()
    canonical_repr = f"{prov_str}:{normalized_path}:{start_str}:{end_str}:{normalized_snip}"
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()[:16]


def compute_span_candidate_id(span: SourceSpan | RTFMResult, provider: str | None = None) -> str:
    """Compute candidate_id from a SourceSpan or RTFMResult."""
    meta = span.metadata or {} if span.metadata else {}
    snippet = meta.get("snippet") if isinstance(span, SourceSpan) else span.snippet
    prov = provider or str(meta.get("provider_id") or "")
    return compute_candidate_id(
        path=span.path,
        line_start=span.line_start,
        line_end=span.line_end,
        snippet=snippet,
        provider=prov,
    )


def _normalize_doi(value: str) -> str:
    normalized = value.strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix).strip()
    return normalized


def _normalize_title(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def compute_evidence_id(
    span: SourceSpan | RTFMResult,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Resolve the canonical logical evidence identity when available.

    Resolution order:
    1. citekey:... (explicit citation key)
    2. doi:... (normalized DOI)
    3. title:... (normalized title from bib metadata)
    4. source:path#start-end (prose source coordinates)
    """
    meta = metadata or (span.metadata or {})
    citekey = meta.get("citekey")
    if citekey:
        return f"citekey:{str(citekey).strip().casefold()}"
    citekeys = meta.get("citekeys")
    if citekeys and isinstance(citekeys, (list, tuple)) and citekeys:
        return f"citekey:{str(citekeys[0]).strip().casefold()}"
    doi = meta.get("doi")
    if doi:
        clean_doi = _normalize_doi(str(doi))
        if clean_doi:
            return f"doi:{clean_doi}"
    title = meta.get("title")
    if title:
        clean_title = _normalize_title(str(title))
        if clean_title:
            return f"title:{clean_title}"

    norm_path = _normalize_path(span.path)
    if span.line_start is not None and span.line_end is not None:
        return f"source:{norm_path}#{span.line_start}-{span.line_end}"
    return f"source:{norm_path}"


class CandidateTraceTracker:
    """In-memory event collector for candidate lifecycle diagnostics."""

    def __init__(self) -> None:
        self._traces: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[CandidateTraceEvent]] = {}
        self._rejections_by_reason: Counter[str] = Counter()

        # Funnel count trackers
        self._retrieved_ids: set[str] = set()
        self._normalized_ids: set[str] = set()
        self._deduplicated_ids: set[str] = set()
        self._excluded_ids: set[str] = set()
        self._exposed_ids: set[str] = set()
        self._filtered_ids: set[str] = set()
        self._eligible_ids: set[str] = set()
        self._selected_ids: set[str] = set()

    def _ensure_candidate(
        self,
        candidate_id: str,
        evidence_id: str | None,
        path: str,
        line_start: int | None,
        line_end: int | None,
        source_role: str,
    ) -> None:
        if candidate_id not in self._traces:
            self._traces[candidate_id] = {
                "candidate_id": candidate_id,
                "evidence_id": evidence_id,
                "path": sanitize_path(path),
                "line_start": line_start,
                "line_end": line_end,
                "source_role": source_role,
            }
            self._events[candidate_id] = []

    def record_retrieved(
        self,
        span: SourceSpan,
        query: str | None = None,
        stream_key: str | None = None,
        retrieval_rank: int | None = None,
    ) -> str:
        cid = compute_span_candidate_id(span)
        eid = compute_evidence_id(span)
        self._ensure_candidate(
            cid, eid, span.path, span.line_start, span.line_end, span.source_role
        )
        self._retrieved_ids.add(cid)
        meta: dict[str, Any] = {}
        if query:
            meta["query"] = query
        if stream_key:
            meta["stream_key"] = stream_key
        if retrieval_rank is not None:
            meta["retrieval_rank"] = retrieval_rank
        self._events[cid].append(
            CandidateTraceEvent(
                stage="retrieved",
                action="ingest",
                score=span.retrieval_score if span.retrieval_score is not None else span.score,
                metadata=meta,
            )
        )
        return cid

    def record_normalized(
        self,
        original_cid: str,
        normalized_span: SourceSpan,
        snapped: bool = False,
    ) -> str:
        new_cid = compute_span_candidate_id(normalized_span)
        eid = compute_evidence_id(normalized_span)
        self._ensure_candidate(
            new_cid,
            eid,
            normalized_span.path,
            normalized_span.line_start,
            normalized_span.line_end,
            normalized_span.source_role,
        )
        self._normalized_ids.add(new_cid)

        meta: dict[str, Any] = {"snapped": snapped}
        if original_cid != new_cid:
            meta["previous_candidate_id"] = original_cid
            if original_cid in self._events and original_cid != new_cid:
                for ev in self._events[original_cid]:
                    if ev not in self._events[new_cid]:
                        self._events[new_cid].append(ev)

        self._events[new_cid].append(
            CandidateTraceEvent(
                stage="normalized",
                action="snap_ast" if snapped else "normalize",
                score=normalized_span.score,
                metadata=meta,
            )
        )
        return new_cid

    def record_deduplicated(
        self,
        span: SourceSpan,
        kept: bool,
        canonical_cid: str | None = None,
    ) -> str:
        cid = compute_span_candidate_id(span)
        if kept:
            self._deduplicated_ids.add(cid)
        meta: dict[str, Any] = {}
        if not kept and canonical_cid:
            meta["canonical_candidate_id"] = canonical_cid
        if cid in self._events:
            self._events[cid].append(
                CandidateTraceEvent(
                    stage="deduplicated",
                    action="dedup_keep" if kept else "dedup_drop",
                    score=span.score,
                    metadata=meta,
                )
            )
        return cid

    def record_excluded_ownership(
        self,
        span: SourceSpan,
        record: OwnershipAuditRecord,
    ) -> str:
        cid = compute_span_candidate_id(span)
        self._excluded_ids.add(cid)
        eid = compute_evidence_id(span)
        self._ensure_candidate(
            cid, eid, span.path, span.line_start, span.line_end, span.source_role
        )
        self._events[cid].append(
            CandidateTraceEvent(
                stage="provider_owned",
                action="exclude_ownership",
                reason="EXCLUDE_PROVIDER_OWNERSHIP",
                score=span.score,
                metadata={
                    "replacement_found": record.replacement_found,
                    "replacement_candidate_id": record.replacement_candidate_id,
                    "replacement_provider": record.replacement_provider,
                    "identities": record.identities,
                },
            )
        )
        return cid

    def record_exposed(self, span: SourceSpan) -> str:
        cid = compute_span_candidate_id(span)
        self._exposed_ids.add(cid)
        if cid in self._events:
            self._events[cid].append(
                CandidateTraceEvent(
                    stage="exposed",
                    action="expose",
                    score=span.score,
                )
            )
        return cid

    def record_filtered(
        self,
        span: SourceSpan,
        reason: str,
        action: str = "filter_drop",
    ) -> str:
        cid = compute_span_candidate_id(span)
        self._filtered_ids.add(cid)
        self._rejections_by_reason[reason] += 1
        if cid in self._events:
            self._events[cid].append(
                CandidateTraceEvent(
                    stage="filtered",
                    action=action,
                    reason=reason,
                    score=span.score,
                )
            )
        return cid

    def record_eligible(self, span: SourceSpan) -> str:
        cid = compute_span_candidate_id(span)
        self._eligible_ids.add(cid)
        return cid

    def record_selected(
        self,
        span: SourceSpan,
        reason: str | None = None,
    ) -> str:
        cid = compute_span_candidate_id(span)
        self._selected_ids.add(cid)
        if cid in self._events:
            self._events[cid].append(
                CandidateTraceEvent(
                    stage="selected",
                    action="select_quota",
                    reason=reason or span.reason,
                    score=span.score,
                )
            )
        return cid

    def record_rejected(
        self,
        span: SourceSpan,
        reason: str,
    ) -> str:
        cid = compute_span_candidate_id(span)
        self._rejections_by_reason[reason] += 1
        if cid in self._events:
            self._events[cid].append(
                CandidateTraceEvent(
                    stage="rejected",
                    action="reject_quota",
                    reason=reason,
                    score=span.score,
                )
            )
        return cid

    def build_diagnostics(
        self,
        ownership_audit: Sequence[OwnershipAuditRecord] = (),
        max_traces: int = 150,
    ) -> ContextPackDiagnostics:
        # Sanitize ownership audit records
        sanitized_audit: list[OwnershipAuditRecord] = []
        for rec in ownership_audit:
            sanitized_audit.append(
                OwnershipAuditRecord(
                    candidate_id=rec.candidate_id,
                    evidence_id=rec.evidence_id,
                    path=sanitize_path(rec.path),
                    line_start=rec.line_start,
                    line_end=rec.line_end,
                    identities=rec.identities,
                    replacement_found=rec.replacement_found,
                    replacement_candidate_id=rec.replacement_candidate_id,
                    replacement_provider=rec.replacement_provider,
                )
            )

        # Build candidate traces sorted deterministically by candidate_id
        # When exceeding max_traces, preserve selected, excluded, then rejected traces up to max_traces
        sorted_cids = sorted(self._traces.keys())
        if len(sorted_cids) > max_traces:
            selected = [cid for cid in sorted_cids if cid in self._selected_ids]
            excluded = [
                cid
                for cid in sorted_cids
                if cid in self._excluded_ids and cid not in self._selected_ids
            ]
            rejected = [
                cid
                for cid in sorted_cids
                if cid not in self._selected_ids
                and cid not in self._excluded_ids
                and any(ev.stage == "rejected" for ev in self._events.get(cid, []))
            ]
            others = [
                cid
                for cid in sorted_cids
                if cid not in self._selected_ids
                and cid not in self._excluded_ids
                and not any(ev.stage == "rejected" for ev in self._events.get(cid, []))
            ]
            ordered = selected + excluded + rejected + others
            sorted_cids = sorted(ordered[:max_traces])

        candidate_traces: list[CandidateTrace] = []
        for cid in sorted_cids:
            info = self._traces[cid]
            events_tuple = tuple(self._events.get(cid, []))
            candidate_traces.append(
                CandidateTrace(
                    candidate_id=info["candidate_id"],
                    evidence_id=info["evidence_id"],
                    path=info["path"],
                    line_start=info["line_start"],
                    line_end=info["line_end"],
                    source_role=info["source_role"],
                    events=events_tuple,
                )
            )

        funnel = CandidateFunnel(
            retrieved=len(self._retrieved_ids),
            normalized=len(self._normalized_ids),
            deduplicated=len(self._deduplicated_ids),
            excluded=len(self._excluded_ids),
            exposed=len(self._exposed_ids),
            filtered=len(self._filtered_ids),
            eligible=len(self._eligible_ids),
            selected=len(self._selected_ids),
        )

        return ContextPackDiagnostics(
            funnel=funnel,
            candidates=candidate_traces,
            ownership_audit=sanitized_audit,
            rejections_by_reason=dict(self._rejections_by_reason),
        )
