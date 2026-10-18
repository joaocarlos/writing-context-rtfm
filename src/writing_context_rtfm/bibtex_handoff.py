"""Passive bibliographic ownership audit for context packs."""

from __future__ import annotations

import re
from collections.abc import Sequence

from writing_context_rtfm.candidate_trace import (
    compute_evidence_id,
    compute_span_candidate_id,
)
from writing_context_rtfm.providers.bibtex import BibEntry, BibTeXProvider
from writing_context_rtfm.schemas import OwnershipAuditRecord, SourceSpan


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
    identities.update(f"citekey:{str(value).casefold()}" for value in citekeys if value)
    doi = _normalize_doi(str(metadata.get("doi") or ""))
    title = _normalize_title(str(metadata.get("title") or ""))
    if doi:
        identities.add(f"doi:{doi}")
    if title:
        identities.add(f"title:{title}")
    return identities


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
                entries = provider.entries_for_source_span(
                    span.path, span.line_start, span.line_end
                )
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
