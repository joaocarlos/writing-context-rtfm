"""Context pack schemas and generation."""

import contextlib
import hashlib
import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.hashing import (
    compute_retrieval_fingerprint,
    compute_task_hash,
    stable_hash,
)
from writing_context_rtfm.local_models import SpanReranker
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import (
    CacheDiagnostics,
    ContextPack,
    PackQuality,
    QuerySpec,
    RTFMResult,
    SourceSpan,
)
from writing_context_rtfm.section_cards import (
    SectionCard,
    SectionCards,
    normalize_terminology,
)
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.token_budget import estimate_span_tokens, estimate_tokens
from writing_context_rtfm.utils import (
    extract_keywords,
    is_allowed_source,
    resolve_rtfm_db_path,
    scan_latex_commands,
)
from writing_context_rtfm.virtual_doc import VirtualDocumentParser

QueryStreamRetriever = Callable[
    [Sequence[QuerySpec], str, int, Sequence[str]],
    dict[int, Sequence[RTFMResult]],
]
BibliographyHandoff = Callable[
    [Sequence[SourceSpan], Sequence[SourceSpan]],
    Sequence[SourceSpan],
]


def _path_matches(path: str, card_path: str | None) -> bool:
    """Helper to check if path matches a card's path case-insensitively."""
    if not card_path:
        return False
    p_norm = path.replace("\\", "/").lower()
    c_norm = card_path.replace("\\", "/").lower().lstrip("./")
    return p_norm.endswith(c_norm)


def _lexical_signature(
    text: str, shingle_size: int = 2
) -> tuple[set[str], set[tuple[str, ...]]]:
    clean = re.sub(r"[^\w\s]", " ", text.lower()).split()
    words = set(clean)
    shingles = {
        tuple(clean[index : index + shingle_size])
        for index in range(len(clean) - shingle_size + 1)
    }
    return words, shingles


def _signature_similarity(
    signature_a: tuple[set[str], set[tuple[str, ...]]],
    signature_b: tuple[set[str], set[tuple[str, ...]]],
) -> float:
    words_a, shingles_a = signature_a
    words_b, shingles_b = signature_b
    if not words_a or not words_b:
        return 0.0

    word_jac = len(words_a & words_b) / max(1, len(words_a | words_b))
    if shingles_a and shingles_b:
        shingle_jac = len(shingles_a & shingles_b) / max(1, len(shingles_a | shingles_b))
    else:
        shingle_jac = word_jac
    return round(0.4 * word_jac + 0.6 * shingle_jac, 4)


def compute_lexical_similarity_v2(text_a: str, text_b: str, shingle_size: int = 2) -> float:
    """Compute character and word-shingle Jaccard similarity between two texts."""
    return _signature_similarity(
        _lexical_signature(text_a, shingle_size),
        _lexical_signature(text_b, shingle_size),
    )


def compute_jaccard_similarity(text_a: str, text_b: str) -> float:
    """Compute word-level Jaccard similarity between two texts."""
    clean_a = re.sub(r"[^\w\s]", " ", text_a.lower()).split()
    clean_b = re.sub(r"[^\w\s]", " ", text_b.lower()).split()
    words_a = {w for w in clean_a if len(w) >= 3} or set(clean_a)
    words_b = {w for w in clean_b if len(w) >= 3} or set(clean_b)
    if not words_a or not words_b:
        return 0.0
    return round(len(words_a & words_b) / max(1, len(words_a | words_b)), 4)


@dataclass(frozen=True)
class AtomicObligation:
    """One explicit piece of evidence that the returned packet must cover."""

    id: str
    kind: str
    label: str


_ATOM_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}


def _normalized_atom_text(text: str) -> str:
    return " ".join(re.findall(r"[\w-]+", text.casefold()))


def _atom_terms(text: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[\w-]+", text.casefold())
        if term not in _ATOM_STOPWORDS and len(term) > 1
    }


def _extract_task_citation_keys(task: str) -> list[str]:
    keys: list[str] = []
    latex_pattern = r"\\[A-Za-z]*cite[A-Za-z]*(?:\[[^\]]*\])*\{([^{}]+)\}"
    for group in re.findall(latex_pattern, task):
        keys.extend(key.strip() for key in group.split(",") if key.strip())
    keys.extend(
        match.group(1)
        for match in re.finditer(r"(?<![\w\\])@([A-Za-z0-9][\w:./+-]*)", task)
    )
    return list(dict.fromkeys(keys))


def _build_atomic_obligations(
    task: str, must_consider: list[str]
) -> list[AtomicObligation]:
    obligations = [
        AtomicObligation(
            id=f"must_consider:{index}",
            kind="must_consider",
            label=value.strip(),
        )
        for index, value in enumerate(must_consider, start=1)
        if value.strip()
    ]
    obligations.extend(
        AtomicObligation(id=f"citation:{key}", kind="citation", label=key)
        for key in _extract_task_citation_keys(task)
    )
    return obligations


def _uncovered_atomic_payload(
    obligations: list[AtomicObligation],
    requested_token_budget: int,
    effective_token_budget: int,
    minimum_coverage_tokens: int,
) -> dict[str, object]:
    return {
        "required": len(obligations),
        "covered": 0,
        "ratio": 0.0 if obligations else 1.0,
        "uncovered": [obligation.id for obligation in obligations],
        "obligations": [
            {
                "id": obligation.id,
                "kind": obligation.kind,
                "label": obligation.label,
                "covered": False,
                "source_paths": [],
            }
            for obligation in obligations
        ],
        "requested_token_budget": requested_token_budget,
        "effective_token_budget": effective_token_budget,
        "expanded_for_coverage": False,
        "minimum_coverage_tokens": minimum_coverage_tokens,
    }


def _obligation_matches_text(obligation: AtomicObligation, text: str) -> bool:
    if not text:
        return False
    normalized_text = _normalized_atom_text(text)
    if obligation.kind == "citation":
        return obligation.label.casefold() in {
            token.casefold() for token in re.findall(r"[A-Za-z0-9][\w:./+-]*", text)
        }

    normalized_label = _normalized_atom_text(obligation.label)
    if normalized_label and normalized_label in normalized_text:
        return True
    required_terms = _atom_terms(obligation.label)
    if not required_terms:
        return False
    present = len(required_terms & _atom_terms(text))
    threshold = len(required_terms) if len(required_terms) <= 2 else max(
        2, (4 * len(required_terms) + 4) // 5
    )
    return present >= threshold


def _span_evidence_text(span: SourceSpan) -> str:
    metadata = span.metadata or {}
    parts = [
        str(metadata.get("snippet") or ""),
        str(metadata.get("citekey") or ""),
        span.path,
    ]
    return "\n".join(part for part in parts if part)


def _span_obligation_ids(
    span: SourceSpan, obligations: list[AtomicObligation]
) -> set[str]:
    text = _span_evidence_text(span)
    return {
        obligation.id
        for obligation in obligations
        if _obligation_matches_text(obligation, text)
    }


def _greedy_atomic_cover(
    candidates: list[SourceSpan],
    obligations: list[AtomicObligation],
    already_covered: set[str],
) -> tuple[list[SourceSpan], dict[int, set[str]]]:
    """Choose a compact, high-ranked set of spans without issuing more searches."""
    span_hits = {
        id(span): _span_obligation_ids(span, obligations) - already_covered
        for span in candidates
    }
    uncovered = {obligation.id for obligation in obligations} - already_covered
    cover: list[SourceSpan] = []
    remaining = list(candidates)
    while uncovered:
        useful = [span for span in remaining if span_hits[id(span)] & uncovered]
        if not useful:
            break
        best = max(
            useful,
            key=lambda span: (
                len(span_hits[id(span)] & uncovered),
                span.score,
                -ContextPackGenerator._estimate_tokens(span),
            ),
        )
        cover.append(best)
        uncovered -= span_hits[id(best)]
        remaining.remove(best)
    return cover, span_hits



def apply_reciprocal_rank_fusion(
    candidates_by_stream: dict[str, list[SourceSpan]],
    weights: dict[str, float] | None = None,
    k: int = 60,
) -> list[SourceSpan]:
    """Fuse multiple ranked streams of SourceSpan preserving retrieval_score, fusion_score, and structural_score."""
    weights = weights or {}
    rrf_scores: dict[tuple[str, int | None, int | None, str], float] = defaultdict(float)
    span_map: dict[tuple[str, int | None, int | None, str], SourceSpan] = {}

    for stream_name, stream_spans in candidates_by_stream.items():
        w = weights.get(stream_name, 1.0)
        for rank, span in enumerate(stream_spans, start=1):
            key = (span.path, span.line_start, span.line_end, span.source_role)
            rrf_scores[key] += w / (k + rank)
            if key not in span_map or span.score > span_map[key].score:
                span_map[key] = span

    if not span_map:
        return []

    max_rrf = max(rrf_scores.values()) if rrf_scores else 1.0
    fused: list[SourceSpan] = []
    for key, base_span in span_map.items():
        norm_fusion = round(min(1.0, (rrf_scores[key] / max(1e-6, max_rrf))), 3)
        struct_score: float | None
        if base_span.source_role in ("target_text", "local_context") or base_span.priority == "essential":
            final_score = base_span.score
            struct_score = base_span.structural_score or base_span.score
        else:
            raw_retrieval = base_span.retrieval_score if base_span.retrieval_score is not None else base_span.score
            final_score = round(0.5 * raw_retrieval + 0.5 * norm_fusion, 3)
            struct_score = base_span.structural_score

        updated_span = SourceSpan(
            path=base_span.path,
            line_start=base_span.line_start,
            line_end=base_span.line_end,
            reason=base_span.reason,
            score=final_score,
            priority=base_span.priority,
            query=base_span.query,
            metadata=base_span.metadata,
            source_role=base_span.source_role,
            retrieval_score=base_span.retrieval_score if base_span.retrieval_score is not None else base_span.score,
            fusion_score=norm_fusion,
            structural_score=struct_score,
        )
        fused.append(updated_span)

    fused.sort(key=lambda s: (-s.score, s.path, s.line_start or 0))
    return fused


def apply_mmr_diversity(spans: list[SourceSpan], lambda_param: float = 0.75) -> list[SourceSpan]:
    """Re-rank candidate spans using Maximal Marginal Relevance (MMR) for lexical/semantic diversity."""
    if len(spans) <= 1:
        return spans

    selected: list[SourceSpan] = []
    remaining = list(spans)
    snippets = {id(span): str((span.metadata or {}).get("snippet", "")) for span in spans}
    signatures = {
        span_id: _lexical_signature(snippet)
        for span_id, snippet in snippets.items()
        if snippet
    }
    similarity_cache: dict[tuple[int, int], float] = {}

    remaining.sort(key=lambda s: -s.score)
    selected.append(remaining.pop(0))

    while remaining:
        best_score = -float("inf")
        best_idx = 0

        for idx, cand in enumerate(remaining):
            cand_id = id(cand)
            cand_snippet = snippets[cand_id]
            max_sim = 0.0
            for sel in selected:
                sel_id = id(sel)
                sel_snippet = snippets[sel_id]
                if cand_snippet and sel_snippet:
                    pair = (min(cand_id, sel_id), max(cand_id, sel_id))
                    sim = similarity_cache.get(pair)
                    if sim is None:
                        sim = _signature_similarity(signatures[cand_id], signatures[sel_id])
                        similarity_cache[pair] = sim
                else:
                    sim = 1.0 if cand.path == sel.path else 0.0
                if sim > max_sim:
                    max_sim = sim

            mmr_val = lambda_param * cand.score - (1.0 - lambda_param) * max_sim
            if mmr_val > best_score:
                best_score = mmr_val
                best_idx = idx

        selected.append(remaining.pop(best_idx))

    return selected


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
# Imported from writing_context_rtfm.schemas


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------
class ContextPackGenerator:
    def __init__(
        self,
        config: AppConfig,
        section_cards: SectionCards | None,
        adapter: RTFMAdapter,
        store: ExtensionStore,
        providers: list[BaseContextProvider] | None = None,
        reranker: SpanReranker | None = None,
        diagnostic_recorder: Callable[[str, Sequence[SourceSpan]], None] | None = None,
        query_stream_retriever: QueryStreamRetriever | None = None,
        bibliography_handoff: BibliographyHandoff | None = None,
    ):
        self.config = config
        self.section_cards = section_cards
        self.adapter = adapter
        self.store = store
        self.providers = providers or []
        self.reranker = reranker
        self.diagnostic_recorder = diagnostic_recorder
        self.query_stream_retriever = query_stream_retriever
        self.bibliography_handoff = bibliography_handoff
        self._hash_cache: dict[Path, tuple[float, int, str]] = {}

    def _record_diagnostic(self, stage: str, spans: Sequence[SourceSpan]) -> None:
        if self.diagnostic_recorder is not None:
            self.diagnostic_recorder(stage, tuple(spans))

    def _get_file_hash(self, path: Path, fallback_val: str) -> str:
        if not path.exists():
            return fallback_val
        try:
            stat = path.stat()
            mtime = stat.st_mtime
            size = stat.st_size

            cached = self._hash_cache.get(path)
            if cached and cached[0] == mtime and cached[1] == size:
                return cached[2]

            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            self._hash_cache[path] = (mtime, size, file_hash)
            return file_hash
        except OSError:
            return fallback_val

    def _resolve_target(
        self, target: str | None, pr: str
    ) -> tuple[str | None, SectionCard | None, str | None]:
        """Resolves target string to (card_key, card, path)."""
        if not target:
            return None, None, None

        if self.section_cards and self.section_cards.sections:
            # 1. Exact match in sections
            if target in self.section_cards.sections:
                card = self.section_cards.sections[target]
                return target, card, card.path

            # 2. Check f"section_{target}"
            if f"section_{target}" in self.section_cards.sections:
                card = self.section_cards.sections[f"section_{target}"]
                return f"section_{target}", card, card.path

            # 3. Check target[8:] if target starts with "section_"
            if target.startswith("section_") and target[8:] in self.section_cards.sections:
                card = self.section_cards.sections[target[8:]]
                return target[8:], card, card.path

            # 4. Case-insensitive title scan or path stem scan
            target_lower = target.lower()
            for key, card in self.section_cards.sections.items():
                if card.title and card.title.lower() == target_lower:
                    return key, card, card.path
                if card.path:
                    card_path = Path(card.path)
                    if (
                        card_path.stem.lower() == target_lower
                        or card_path.name.lower() == target_lower
                        or card.path.lower() == target_lower
                    ):
                        return key, card, card.path

        # 5. Check if target matches a virtual section node in root document files (single-file or multi-file)
        try:
            doc_parser = VirtualDocumentParser(pr)
            for entry in [
                "main.tex",
                "paper.tex",
                "manuscript.tex",
                "document.tex",
                "main.md",
                "paper.md",
                "README.md",
            ]:
                if (Path(pr) / entry).is_file():
                    with contextlib.suppress(Exception):
                        doc_parser.parse(entry)
                    node = doc_parser.find_section_node(target)
                    if node:
                        card = SectionCard(
                            id=node.node_id,
                            title=node.title,
                            path=node.source_path,
                            role=f"Section: {node.title}",
                        )
                        return node.node_id, card, node.source_path
        except Exception:
            pass

        # 6. Check if target is a file path in the workspace
        test_path = Path(pr) / target
        if test_path.is_file() or target.endswith(
            (".tex", ".md", ".txt", ".py", ".json", ".yaml", ".yml", ".bib")
        ):
            return None, None, target

        return None, None, None

    # -----------------------------------------------------------------------
    # Fix 4: Query builder
    # -----------------------------------------------------------------------
    def _build_queries(
        self,
        task: str,
        target: str | None,
        must_consider: list[str],
        task_type: str | None = None,
        pack_mode: str | None = None,
        has_line_range: bool = False,
    ) -> tuple[list[QuerySpec], SectionCard | None, list[SectionCard], dict[str, str]]:
        """Returns (query_specs, target_card, dep_cards, query_type_map)."""
        specs: list[QuerySpec] = []
        queries: list[str] = []
        query_type_map: dict[str, str] = {}
        target_card: SectionCard | None = None
        dep_cards: list[SectionCard] = []

        unverified_terms_set: set[str] = set()
        unverified_deps_set: set[str] = set()

        if self.section_cards and target and target in self.section_cards.sections:
            target_card = self.section_cards.sections[target]
            unverified_terms_set = set(target_card.unverified_key_terms or [])
            unverified_deps_set = set(target_card.unverified_dependencies or [])

        def _add(
            q: str,
            qtype: str,
            family: str = "task",
            weight: float = 1.0,
            is_verified: bool = True,
        ) -> None:
            if q and q not in query_type_map:
                queries.append(q)
                query_type_map[q] = qtype
                specs.append(
                    QuerySpec(
                        text=q,
                        query_type=qtype,
                        family=family,
                        weight=weight,
                        is_verified=is_verified,
                    )
                )

        # 1. Raw task
        _add(task, "task", family="task", weight=1.0, is_verified=True)

        # 2. Document thesis (if review)
        if task_type == "review" and self.section_cards and self.section_cards.document.thesis:
            _add(self.section_cards.document.thesis, "thesis", family="thesis", weight=0.9, is_verified=True)

        if target_card:
            # 3. Target section intent, title, and key terms (skip key terms if minimal or line range exists)
            if not has_line_range and pack_mode != "minimal":
                if target_card.role:
                    _add(target_card.role, "intent", family="intent", weight=0.95, is_verified=True)
                if target_card.title:
                    _add(target_card.title, "title", family="intent", weight=0.9, is_verified=True)

                # Align with previous sections scales down target key terms
                if task_type == "align_with_previous_sections":
                    max_kt = 2
                elif pack_mode == "deep":
                    max_kt = 12
                else:
                    max_kt = 6

                for kt in (target_card.key_terms or [])[:max_kt]:
                    is_ver = kt not in unverified_terms_set
                    kt_weight = 0.85 if is_ver else 0.65
                    _add(kt, "key_term", family="terms", weight=kt_weight, is_verified=is_ver)
            elif not has_line_range and pack_mode == "minimal":
                if target_card.role:
                    _add(target_card.role, "intent", family="intent", weight=0.95, is_verified=True)
                if target_card.title:
                    _add(target_card.title, "title", family="intent", weight=0.9, is_verified=True)

            # 4. Dependency section intents, titles and key terms (skip key terms if minimal)
            for dep_id in target_card.depends_on or []:
                if self.section_cards and dep_id in self.section_cards.sections:
                    dep_card = self.section_cards.sections[dep_id]
                    dep_cards.append(dep_card)
                    dep_is_ver = dep_id not in unverified_deps_set
                    dep_weight = 0.85 if dep_is_ver else 0.65
                    if dep_card.role:
                        _add(dep_card.role, "dep_intent", family="deps", weight=dep_weight, is_verified=dep_is_ver)
                    if dep_card.title:
                        _add(dep_card.title, "dep_title", family="deps", weight=dep_weight * 0.95, is_verified=dep_is_ver)

                    if pack_mode != "minimal":
                        max_dep_kt = 6 if pack_mode == "deep" else 3
                        for kt in (dep_card.key_terms or [])[:max_dep_kt]:
                            _add(kt, "dep_key_term", family="deps", weight=0.75, is_verified=True)

        # 5. Task keywords (skip if minimal or review)
        if pack_mode != "minimal" and task_type != "review":
            for kw in extract_keywords(task):
                _add(kw, "task_keyword", family="task", weight=0.7, is_verified=True)

        # 6. Explicit must-consider
        for mc in must_consider:
            _add(mc, "must_consider", family="task", weight=1.0, is_verified=True)

        return specs, target_card, dep_cards, query_type_map


    # -----------------------------------------------------------------------
    # Deduplication
    # -----------------------------------------------------------------------
    def _deduplicate_spans(self, candidates: list[SourceSpan]) -> list[SourceSpan]:
        if not candidates:
            return []

        # 1. Group spans by file path
        by_file = defaultdict(list)
        for c in candidates:
            by_file[c.path].append(c)

        merged_candidates = []

        for path, spans in by_file.items():
            # Sort spans by line_start
            spans.sort(key=lambda x: (x.line_start or 1, x.line_end or x.line_start or 1))

            current_merged: list[SourceSpan] = []
            for span in spans:
                if not current_merged:
                    current_merged.append(span)
                    continue

                prev = current_merged[-1]
                prev_start = prev.line_start or 1
                prev_end = prev.line_end or prev_start
                curr_start = span.line_start or 1
                curr_end = span.line_end or curr_start

                overlap_lines = max(
                    0,
                    min(prev_end, curr_end) - max(prev_start, curr_start) + 1,
                )
                shorter_span_lines = min(
                    prev_end - prev_start + 1,
                    curr_end - curr_start + 1,
                )
                overlap_ratio = overlap_lines / max(1, shorter_span_lines)
                roles = {prev.source_role, span.source_role}
                is_target_context_union = roles == {"target_text", "local_context"}
                is_adjacent_or_overlapping = curr_start <= prev_end + 1

                # Merge duplicates and meaningfully overlapping chunks, but do not
                # transitively chain large retrieved spans that merely share a boundary
                # line. Target text and its deliberately adjacent local context remain
                # one structural span.
                if overlap_ratio >= 0.25 or (
                    is_target_context_union and is_adjacent_or_overlapping
                ):
                    new_end = max(prev_end, curr_end)

                    # Combine reasons
                    reasons = []
                    for r in [prev.reason, span.reason]:
                        if r and r not in reasons:
                            reasons.append(r)
                    combined_reason = "; ".join(reasons)

                    # Select max score
                    max_score = max(prev.score, span.score)

                    # Combine query names
                    queries = []
                    for q in [prev.query, span.query]:
                        if q and q not in queries:
                            queries.append(q)
                    combined_query = ", ".join(queries) if queries else None

                    # Reconstruct merged snippet
                    prev_snippet = (prev.metadata or {}).get("snippet", "") if prev.metadata else ""
                    curr_snippet = (span.metadata or {}).get("snippet", "") if span.metadata else ""

                    prev_lines = prev_snippet.splitlines() if prev_snippet else []
                    curr_lines = curr_snippet.splitlines() if curr_snippet else []

                    # Calculate offset union of lines
                    if curr_start == prev_end + 1:
                        merged_lines = prev_lines + curr_lines
                    elif curr_start > prev_start:
                        non_overlap_start = max(0, len(curr_lines) - (curr_end - prev_end))
                        merged_lines = prev_lines + curr_lines[non_overlap_start:]
                    else:
                        if curr_end >= prev_end:
                            merged_lines = curr_lines
                        else:
                            non_overlap_end = max(0, prev_start - curr_start)
                            merged_lines = curr_lines[:non_overlap_end] + prev_lines

                    merged_snippet = "\n".join(merged_lines)

                    # Update metadata
                    merged_meta = {}
                    if prev.metadata:
                        merged_meta.update(prev.metadata)
                    if span.metadata:
                        merged_meta.update(span.metadata)
                    merged_meta["snippet"] = merged_snippet

                    # Combine priority and role based on hierarchy
                    priority_order = {"essential": 3, "supporting": 2, "background": 1}
                    role_order = {
                        "target_text": 4,
                        "local_context": 3,
                        "dependency": 2,
                        "reference": 1,
                    }

                    merged_priority = (
                        prev.priority
                        if priority_order.get(prev.priority, 0)
                        >= priority_order.get(span.priority, 0)
                        else span.priority
                    )
                    merged_role = (
                        prev.source_role
                        if role_order.get(prev.source_role, 0)
                        >= role_order.get(span.source_role, 0)
                        else span.source_role
                    )

                    # Replace the last element with the merged span
                    merged_retrieval = (
                        max(prev.retrieval_score or 0.0, span.retrieval_score or 0.0)
                        if (prev.retrieval_score is not None or span.retrieval_score is not None)
                        else None
                    )
                    merged_fusion = (
                        max(prev.fusion_score or 0.0, span.fusion_score or 0.0)
                        if (prev.fusion_score is not None or span.fusion_score is not None)
                        else None
                    )
                    merged_structural = (
                        max(prev.structural_score or 0.0, span.structural_score or 0.0)
                        if (prev.structural_score is not None or span.structural_score is not None)
                        else None
                    )

                    current_merged[-1] = SourceSpan(
                        path=path,
                        line_start=prev_start,
                        line_end=new_end,
                        reason=combined_reason,
                        score=max_score,
                        priority=merged_priority,
                        query=combined_query,
                        metadata=merged_meta,
                        source_role=merged_role,
                        retrieval_score=merged_retrieval,
                        fusion_score=merged_fusion,
                        structural_score=merged_structural,
                    )

                else:
                    current_merged.append(span)

            merged_candidates.extend(current_merged)

        # Sort the final candidates by descending score
        merged_candidates.sort(key=lambda x: (-x.score, x.path, x.line_start or 0))
        return merged_candidates

    # -----------------------------------------------------------------------
    # Fix 3: Score filtering with structural override
    # -----------------------------------------------------------------------
    def _filter_by_score(
        self,
        candidates: list[SourceSpan],
        target_card: SectionCard | None,
        dep_cards: list[SectionCard],
    ) -> tuple[list[SourceSpan], int]:
        if not candidates:
            return [], 0

        min_score = self.config.context.min_score
        min_rel = self.config.context.min_relative_score
        top_score = max(c.score for c in candidates)
        threshold = max(min_score, top_score * min_rel)

        target_paths = set()
        if target_card and target_card.path:
            target_paths.add(target_card.path)
        for dc in dep_cards:
            if dc.path:
                target_paths.add(dc.path)

        kept: list[SourceSpan] = []
        discarded = 0
        for c in candidates:
            if c.score >= threshold:
                kept.append(c)
            elif c.path in target_paths:
                # Structural override: keep low-score spans from the target file
                kept.append(c)
            else:
                discarded += 1

        return kept, discarded

    # -----------------------------------------------------------------------
    # Avoid filter (from section_card.avoid list)
    # -----------------------------------------------------------------------
    def _filter_avoid(
        self, candidates: list[SourceSpan], target_card: SectionCard | None
    ) -> tuple[list[SourceSpan], int]:
        """Remove spans whose snippet matches any phrase in the section's avoid list."""
        avoid_phrases = (target_card.avoid or []) if target_card else []
        if not avoid_phrases:
            return candidates, 0

        kept: list[SourceSpan] = []
        discarded = 0
        for span in candidates:
            snippet = ((span.metadata or {}).get("snippet") or "").lower()
            span.path.lower()
            # Only apply avoid filter to spans NOT from the target file
            # (target file content stays regardless — we need it to write)
            if target_card and _path_matches(span.path, target_card.path):
                kept.append(span)
                continue
            if any(phrase.lower() in snippet for phrase in avoid_phrases):
                discarded += 1
            else:
                kept.append(span)
        return kept, discarded

    # -----------------------------------------------------------------------
    # Fix 6: Token estimation using snippet when available
    # -----------------------------------------------------------------------
    @staticmethod
    def _estimate_tokens(span: SourceSpan) -> int:
        snippet = (span.metadata or {}).get("snippet") if span.metadata else None
        if snippet:
            return estimate_tokens(snippet)
        line_s = span.line_start or 1
        line_e = span.line_end if span.line_end is not None else (line_s + 30)
        return max(1, estimate_span_tokens(line_s, line_e))

    # -----------------------------------------------------------------------
    # Main generate()
    # -----------------------------------------------------------------------
    def generate(
        self,
        task: str,
        target: str | None,
        token_budget: int,
        must_consider: list[str] | None = None,
        project_root: str | None = None,
        task_type: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        pack_mode: str | None = None,
        role_budgets: dict[str, float] | None = None,
        strict_budget: bool | None = None,
        output_mode: str | None = None,
    ) -> ContextPack:
        must_consider = must_consider or []
        pr = project_root or self.config.rtfm.project_root or "."
        task_type = task_type or "write_new_section"
        pack_mode = pack_mode or "standard"
        is_strict = strict_budget if strict_budget is not None else (pack_mode == "minimal")
        output_mode = str(
            output_mode or getattr(self.config.context, "output_mode", "prompt") or "prompt"
        )
        obligations = _build_atomic_obligations(task, must_consider)

        # Apply pack mode defaults / overrides
        if pack_mode == "minimal":
            token_budget = min(token_budget, 2000)
            max_spans = 5
        elif pack_mode == "deep":
            max_spans = 60
        else:
            max_spans = self.config.context.max_source_spans

        # --- Diagnostics setup (Fix 7) ---
        warnings: list[str] = []
        status = "complete"
        quality = PackQuality(
            project_root=pr,
            config_loaded=True,
            section_cards_loaded=self.section_cards is not None,
            section_cards_path=self.config.section_cards.path,
        )
        if not self.section_cards:
            warnings.append(
                f"No section cards loaded from '{self.config.section_cards.path}'. "
                "Ranking used retrieval score only."
            )
            if not target:
                warnings.append(
                    "No --target provided. Query expansion is limited to raw task string."
                )

        # --- Cache check ---
        task_hash = compute_task_hash(
            task,
            target,
            token_budget,
            must_consider=must_consider,
            task_type=task_type,
            line_start=line_start,
            line_end=line_end,
            pack_mode=pack_mode,
            strict_budget=is_strict,
            role_budgets=role_budgets,
            output_mode=output_mode,
        )

        # Calculate real config file content hash
        config_path = Path(self.config.rtfm.project_root) / ".writing-context" / "config.yaml"
        config_hash = self._get_file_hash(config_path, stable_hash(str(self.config.version)))

        # Calculate real section cards content hash
        sc_path = Path(self.config.section_cards.path)
        sc_fallback = stable_hash(str(self.section_cards.version) if self.section_cards else "none")
        sc_hash = self._get_file_hash(sc_path, sc_fallback)

        # Compute combined retrieval fingerprint (RTFM DB + provider fingerprints)
        rtfm_db = resolve_rtfm_db_path(Path(self.config.rtfm.project_root))
        provider_fps = []
        for p in self.providers:
            with contextlib.suppress(Exception):
                fp = p.get_fingerprint(self.config)
                if fp:
                    provider_fps.append(f"{p.provider_id}:{fp}")
        if self.reranker is not None:
            with contextlib.suppress(Exception):
                provider_fps.append(f"reranker:{self.reranker.get_fingerprint()}")
        retrieval_fingerprint = compute_retrieval_fingerprint(rtfm_db, provider_fps)

        if self.config.cache.enabled:
            cached = self.store.get_cached_pack(task_hash, config_hash, sc_hash, retrieval_fingerprint)
            if cached:
                spans = [SourceSpan(**s) for s in cached.get("source_spans", [])]
                cd_data = cached.get("cache")
                if isinstance(cd_data, dict):
                    cd_data_copy = dict(cd_data)
                    cd_data_copy["hit"] = True
                    cd = CacheDiagnostics(**cd_data_copy)
                else:
                    cd = CacheDiagnostics(
                        enabled=True,
                        hit=True,
                        task_hash=task_hash,
                        config_hash=config_hash,
                        section_cards_hash=sc_hash,
                        rtfm_index_fingerprint=retrieval_fingerprint,
                    )
                return ContextPack(
                    task=cached["task"],
                    target=cached.get("target"),
                    document_thesis=cached.get("document_thesis"),
                    prior_claims=cached.get("prior_claims", []),
                    terminology=cached.get("terminology", {}),
                    constraints=cached.get("constraints", []),
                    source_spans=spans,
                    estimated_tokens=cached.get("estimated_tokens", 0),
                    status=cached.get("status", "complete"),
                    warnings=cached.get("warnings", []),
                    quality=cached.get("quality"),
                    summary=cached.get("summary"),
                    run_id=cached.get("run_id"),
                    cache=cd,
                    task_type=cached.get("task_type"),
                    pack_mode=cached.get("pack_mode"),
                )

        # --- Target Line Range Resolution (Phase 2 & Phase 5) ---
        resolved_key, target_card, target_path = self._resolve_target(target, pr)

        all_candidates: list[SourceSpan] = []
        initial_token_budget = token_budget
        has_explicit_line_range = line_start is not None and line_end is not None

        if has_explicit_line_range and not target_path:
            warnings.append(
                "line_start and line_end provided but target file path could not be resolved."
            )
            status = "degraded"

        if target_path:
            try:
                full_path = Path(pr) / target_path
                if full_path.exists() and full_path.is_file():
                    file_content = full_path.read_text(encoding="utf-8", errors="replace")
                    lines = file_content.splitlines()
                    num_lines = len(lines)

                    # Case A: Explicit line range requested
                    if line_start is not None and line_end is not None:
                        start = max(1, min(line_start, num_lines))
                        end = max(1, min(line_end, num_lines))
                        if start > end:
                            start, end = end, start

                        target_snippet = "\n".join(lines[start - 1 : end])
                        target_span = SourceSpan(
                            path=target_path,
                            line_start=start,
                            line_end=end,
                            reason="Target text range",
                            score=1.0,
                            structural_score=1.0,
                            priority="essential",
                            source_role="target_text",
                            metadata={"snippet": target_snippet},
                        )
                        all_candidates.append(target_span)

                        # Local context before
                        if start > 1:
                            ctx_start = max(1, start - 15)
                            ctx_end = start - 1
                            before_snippet = "\n".join(lines[ctx_start - 1 : ctx_end])
                            all_candidates.append(
                                SourceSpan(
                                    path=target_path,
                                    line_start=ctx_start,
                                    line_end=ctx_end,
                                    reason="Surrounding target context (before)",
                                    score=0.9,
                                    structural_score=0.9,
                                    priority="supporting",
                                    source_role="local_context",
                                    metadata={"snippet": before_snippet},
                                )
                            )

                        # Local context after
                        if end < num_lines:
                            ctx_start = end + 1
                            ctx_end = min(num_lines, end + 15)
                            after_snippet = "\n".join(lines[ctx_start - 1 : ctx_end])
                            all_candidates.append(
                                SourceSpan(
                                    path=target_path,
                                    line_start=ctx_start,
                                    line_end=ctx_end,
                                    reason="Surrounding target context (after)",
                                    score=0.9,
                                    structural_score=0.9,
                                    priority="supporting",
                                    source_role="local_context",
                                    metadata={"snippet": after_snippet},
                                )
                            )

                    # Case B: Target section specified without line numbers -> Atomically extract target section
                    elif target is not None:
                        doc_parser_target = VirtualDocumentParser(pr)
                        with contextlib.suppress(Exception):
                            doc_parser_target.parse(target_path)

                        target_node = doc_parser_target.find_section_node(target or resolved_key or "")
                        if target_node and target_node.source_path == target_path:
                            start = max(1, min(target_node.line_start, num_lines))
                            end = max(1, min(target_node.line_end, num_lines))
                        else:
                            start = 1
                            end = num_lines

                        if start <= end and num_lines > 0:
                            target_snippet = "\n".join(lines[start - 1 : end])
                            target_span = SourceSpan(
                                path=target_path,
                                line_start=start,
                                line_end=end,
                                reason=f"Target section text (unbroken: {target_card.title if target_card and target_card.title else target})",
                                score=1.0,
                                structural_score=1.0,
                                priority="essential",
                                source_role="target_text",
                                metadata={"snippet": target_snippet},
                            )
                            all_candidates.append(target_span)
                else:
                    if has_explicit_line_range:
                        warnings.append(
                            f"Target file '{target_path}' not found for line range extraction."
                        )
                        status = "degraded"
            except Exception as e:
                warnings.append(f"Failed to read target file '{target_path}': {e}")
                if has_explicit_line_range:
                    status = "degraded"

        # --- Baseline Tokens & Strict Overflow Check (Stop before retrieval) ---
        essential_tokens = sum(
            self._estimate_tokens(s)
            for s in all_candidates
            if s.source_role in ("target_text", "local_context") or s.priority == "essential"
        )
        thesis_text = (
            (self.section_cards.document.thesis if self.section_cards and self.section_cards.document else "")
            or ""
        )
        constraints_text = " ".join(target_card.constraints if target_card and target_card.constraints else [])
        baseline_tokens = essential_tokens + self._estimate_tokens(
            SourceSpan(
                path="",
                line_start=0,
                line_end=0,
                reason="",
                score=1.0,
                metadata={"snippet": f"{thesis_text} {constraints_text}"},
            )
        ) + 150

        if is_strict and baseline_tokens > token_budget:
            quality.minimum_required_tokens = baseline_tokens
            quality.reason = "budget_too_small_for_atomic_target"
            quality.atomic_coverage = _uncovered_atomic_payload(
                obligations,
                initial_token_budget,
                token_budget,
                baseline_tokens,
            )
            quality.dropped_for_budget = 0
            quality.truncated = False
            quality.selected_count = 0
            overflow_msg = (
                f"The atomic target section requires a minimum of {baseline_tokens} tokens, "
                f"which exceeds the strict token_budget of {token_budget}. "
                f"Please retry with token_budget >= {baseline_tokens}."
            )
            warnings.append(overflow_msg)
            run_id = str(uuid.uuid4())
            return ContextPack(
                task=task,
                target=target,
                document_thesis=self.section_cards.document.thesis if self.section_cards and self.section_cards.document else None,
                prior_claims=[],
                terminology={},
                constraints=target_card.constraints if target_card and target_card.constraints else [],
                source_spans=[],
                estimated_tokens=0,
                status="degraded",
                warnings=warnings,
                quality=asdict(quality),
                summary=overflow_msg,
                run_id=run_id,
                cache=CacheDiagnostics(
                    enabled=self.config.cache.enabled,
                    hit=False,
                    task_hash=task_hash,
                    config_hash=config_hash,
                    section_cards_hash=sc_hash,
                    rtfm_index_fingerprint=retrieval_fingerprint,
                ),
                task_type=task_type,
                pack_mode=pack_mode,
            )

        if not is_strict:
            max_budget = getattr(self.config.context, "max_token_budget", 32000)
            if baseline_tokens > max_budget:
                quality.minimum_required_tokens = baseline_tokens
                quality.reason = "budget_too_small_for_atomic_target"
                quality.atomic_coverage = _uncovered_atomic_payload(
                    obligations,
                    initial_token_budget,
                    max_budget,
                    baseline_tokens,
                )
                quality.dropped_for_budget = 0
                quality.truncated = False
                quality.selected_count = 0
                overflow_msg = (
                    f"The atomic target section requires a minimum of {baseline_tokens} tokens, "
                    f"which exceeds the maximum configured token budget of {max_budget}. "
                    f"Please retry with max_token_budget >= {baseline_tokens}."
                )
                warnings.append(overflow_msg)
                run_id = str(uuid.uuid4())
                return ContextPack(
                    task=task,
                    target=target,
                    document_thesis=self.section_cards.document.thesis if self.section_cards and self.section_cards.document else None,
                    prior_claims=[],
                    terminology={},
                    constraints=target_card.constraints if target_card and target_card.constraints else [],
                    source_spans=[],
                    estimated_tokens=0,
                    status="degraded",
                    warnings=warnings,
                    quality=asdict(quality),
                    summary=overflow_msg,
                    run_id=run_id,
                    cache=CacheDiagnostics(
                        enabled=self.config.cache.enabled,
                        hit=False,
                        task_hash=task_hash,
                        config_hash=config_hash,
                        section_cards_hash=sc_hash,
                        rtfm_index_fingerprint=retrieval_fingerprint,
                    ),
                    task_type=task_type,
                    pack_mode=pack_mode,
                )
            elif token_budget < baseline_tokens:
                auto_budget = min(max_budget, int(baseline_tokens * 1.2) + 500)
                warnings.append(
                    f"Note: The retrieved context ({baseline_tokens} tokens) exceeded the requested budget ({initial_token_budget}). "
                    f"Auto-expanded token budget from {initial_token_budget} to {auto_budget} to accommodate unbroken target section and reference elements."
                )
                token_budget = auto_budget

        # --- Query expansion & telemetry ---
        query_specs, target_card, dep_cards, query_type_map = self._build_queries(
            task,
            resolved_key or target,
            must_consider,
            task_type=task_type,
            pack_mode=pack_mode,
            has_line_range=has_explicit_line_range,
        )
        queries = [qs.text for qs in query_specs]
        quality.queries_issued = len(queries)
        quality.card_uncertainties = {
            "unverified_key_terms": list(target_card.unverified_key_terms or []) if target_card else [],
            "unverified_dependencies": list(target_card.unverified_dependencies or []) if target_card else [],
        }

        # --- Retrieval & Stream Fusion ---
        stream_candidates: dict[str, list[SourceSpan]] = defaultdict(list)
        enable_rrf = getattr(self.config.context, "enable_rrf", False)
        active_providers = [
            provider for provider in self.providers if provider.is_available(self.config)
        ]
        structured_bibtex_active = any(
            provider.provider_id == "bibtex" for provider in active_providers
        )
        excluded_bibtex_candidates: list[SourceSpan] = []

        prefetched_results: dict[int, Sequence[RTFMResult]] | None = None
        if self.query_stream_retriever is not None:
            prefetched_results = self.query_stream_retriever(
                tuple(query_specs),
                self.config.rtfm.corpus,
                self.config.context.max_search_results_per_query,
                tuple(obligation.label for obligation in obligations),
            )
            invalid_indexes = sorted(set(prefetched_results) - set(range(len(query_specs))))
            if invalid_indexes:
                raise ValueError(
                    f"Query stream retriever returned invalid indexes: {invalid_indexes}"
                )

        for i, qs in enumerate(query_specs):
            q = qs.text
            stream_key = f"query_{qs.family}_{i}"
            try:
                if prefetched_results is None:
                    results = self.adapter.search(
                        q,
                        corpus=self.config.rtfm.corpus,
                        limit=self.config.context.max_search_results_per_query,
                    )
                else:
                    results = prefetched_results.get(i, ())
                for retrieval_rank, r in enumerate(results, start=1):
                    if not is_allowed_source(r.path):
                        quality.discarded_excluded_path += 1
                        continue
                    query_type = qs.query_type
                    raw_score = r.score if r.score is not None else 0.5
                    score = self._compute_final_score(
                        r,
                        target_card,
                        dep_cards,
                        must_consider,
                        q,
                        query_type,
                        task_type=task_type,
                        target_line_start=line_start,
                        target_line_end=line_end,
                        retrieval_rank=retrieval_rank,
                    ) * qs.weight
                    span = SourceSpan(
                        path=r.path,
                        line_start=r.line_start,
                        line_end=r.line_end,
                        reason=self._build_reason(r, target_card, dep_cards, query_type, q),
                        score=score,
                        retrieval_score=raw_score,
                        query=q,
                        metadata={
                            **(r.metadata or {}),
                            "snippet": r.snippet,
                            "retrieval_rank": retrieval_rank,
                        },
                    )
                    if structured_bibtex_active and Path(r.path).suffix.lower() == ".bib":
                        quality.discarded_excluded_path += 1
                        excluded_bibtex_candidates.append(span)
                        continue
                    stream_candidates[stream_key].append(span)
                    if not enable_rrf:
                        all_candidates.append(span)
            except Exception as e:
                warnings.append(f"Search failed for query '{q}': {e}")

        # --- Providers Context Retrieval ---
        provider_role_budgets = dict(self.config.context.role_budgets)
        if role_budgets:
            provider_role_budgets.update(role_budgets)
        provider_limit = max(
            0,
            min(
                max_spans,
                int(max_spans * provider_role_budgets.get("reference", 0.0)),
            ),
        )
        bibtex_provider_candidates: list[SourceSpan] = []
        for provider in active_providers:
            if provider_limit:
                try:
                    p_spans = provider.fetch_context(
                        queries,
                        target,
                        limit=provider_limit,
                        query_type_map=query_type_map,
                        task_type=task_type,
                    )
                    stream_key = f"provider_{provider.provider_id}"
                    for ps in p_spans:
                        span_with_scores = replace(
                            ps,
                            retrieval_score=ps.retrieval_score if ps.retrieval_score is not None else ps.score,
                            metadata={
                                **(ps.metadata or {}),
                                "provider_id": provider.provider_id,
                            },
                        )
                        stream_candidates[stream_key].append(span_with_scores)
                        if provider.provider_id == "bibtex":
                            bibtex_provider_candidates.append(span_with_scores)
                        if not enable_rrf:
                            all_candidates.append(span_with_scores)
                except Exception as e:
                    warnings.append(f"Provider '{provider.provider_id}' failed: {e}")
                    status = "degraded"

        if excluded_bibtex_candidates:
            self._record_diagnostic("stream:excluded_bibtex", excluded_bibtex_candidates)
        if self.bibliography_handoff is not None:
            handoff_spans = self.bibliography_handoff(
                tuple(excluded_bibtex_candidates),
                tuple(bibtex_provider_candidates),
            )
            stream_key = "provider_bibtex_handoff"
            for handoff_span in handoff_spans:
                span_with_provider = replace(
                    handoff_span,
                    metadata={
                        **(handoff_span.metadata or {}),
                        "provider_id": (handoff_span.metadata or {}).get(
                            "provider_id", "bibtex"
                        ),
                    },
                )
                stream_candidates[stream_key].append(span_with_provider)
                if not enable_rrf:
                    all_candidates.append(span_with_provider)

        for stream_key, spans in stream_candidates.items():
            self._record_diagnostic(f"stream:{stream_key}", spans)

        # Apply multi-stream RRF when enabled
        if enable_rrf and stream_candidates:
            family_weights = {"task": 1.0, "intent": 0.9, "terms": 0.8, "deps": 0.8, "thesis": 0.8}
            stream_weights = {}
            for sk in stream_candidates:
                weight = 1.0
                for fam, w in family_weights.items():
                    if f"_{fam}_" in sk:
                        weight = w
                        break
                stream_weights[sk] = weight
            fused_spans = apply_reciprocal_rank_fusion(stream_candidates, weights=stream_weights)
            all_candidates.extend(fused_spans)

        # --- 1-Hop Reference Graph Resolution (Figures, Equations, Tables, Subsections) ---
        if target_card and target_path:
            try:
                doc_parser = VirtualDocumentParser(pr)
                doc_parser.parse(target_path)

                ref_labels: list[str] = []
                if resolved_key and resolved_key in doc_parser.nodes:
                    ref_labels.extend(doc_parser.nodes[resolved_key].references)

                seen_env_keys = set()
                for ref_label in ref_labels:
                    for file_rel, envs in doc_parser.environments_by_file.items():
                        for env in envs:
                            if env.get("label") == ref_label:
                                env_key = (file_rel, env.get("line_start"), env.get("line_end"))
                                if env_key not in seen_env_keys:
                                    seen_env_keys.add(env_key)
                                    f_abs = Path(pr) / file_rel
                                    if f_abs.is_file():
                                        f_lines = f_abs.read_text(
                                             encoding="utf-8", errors="replace"
                                        ).splitlines()
                                        e_start = max(1, env.get("line_start", 1))
                                        e_end = min(len(f_lines), env.get("line_end", e_start))
                                        env_snippet = "\n".join(f_lines[e_start - 1 : e_end])
                                        env_span = SourceSpan(
                                            path=file_rel,
                                            line_start=e_start,
                                            line_end=e_end,
                                            reason=f"Referenced {env.get('env_name', 'element')} (\\ref{{{ref_label}}})",
                                            score=0.92,
                                            structural_score=0.92,
                                            priority="supporting",
                                            source_role="dependency",
                                            metadata={
                                                "snippet": env_snippet,
                                                "env_name": env.get("env_name"),
                                                "label": ref_label,
                                            },
                                        )
                                        all_candidates.append(env_span)
            except Exception as e:
                warnings.append(f"1-Hop reference traversal notice: {e}")

        self._record_diagnostic("retrieved", all_candidates)

        # --- AST Environment Snapping ---
        doc_parser_snap = VirtualDocumentParser(pr)
        snapped_candidates: list[SourceSpan] = []
        for span in all_candidates:
            if (
                (span.path.endswith(".tex") or span.path.endswith(".md"))
                and span.line_start is not None
                and span.line_end is not None
            ):
                if span.path not in doc_parser_snap.environments_by_file:
                    with contextlib.suppress(Exception):
                        doc_parser_snap.parse(span.path)

                snapped_start, snapped_end = doc_parser_snap.snap_to_environment(
                    span.path, span.line_start, span.line_end
                )
                if (snapped_start, snapped_end) != (span.line_start, span.line_end):
                    f_abs = Path(pr) / span.path
                    new_snippet = None
                    if f_abs.is_file():
                        with contextlib.suppress(Exception):
                            f_lines = f_abs.read_text(
                                encoding="utf-8", errors="replace"
                            ).splitlines()
                            new_snippet = "\n".join(f_lines[snapped_start - 1 : snapped_end])

                    updated_meta = dict(span.metadata or {})
                    if new_snippet is not None:
                        updated_meta["snippet"] = new_snippet

                    snapped_candidates.append(
                        SourceSpan(
                            path=span.path,
                            line_start=snapped_start,
                            line_end=snapped_end,
                            reason=span.reason + " [AST snapped]",
                            score=span.score,
                            priority=span.priority,
                            query=span.query,
                            metadata=updated_meta,
                            source_role=span.source_role,
                            retrieval_score=span.retrieval_score,
                            fusion_score=span.fusion_score,
                            structural_score=span.structural_score,
                        )
                    )
                else:
                    snapped_candidates.append(span)
            else:
                snapped_candidates.append(span)

        # --- Deduplication ---
        deduped = self._deduplicate_spans(snapped_candidates)
        self._record_diagnostic("deduplicated", deduped)

        # --- Optional bounded local cross-encoder reranking ---
        if self.reranker is not None:
            try:
                deduped = self.reranker.rerank(task, deduped)
            except Exception as e:
                warnings.append(f"Local reranker failed: {e}")
                status = "degraded"

        # --- Score filtering ---
        filtered, discarded_count = self._filter_by_score(deduped, target_card, dep_cards)
        quality.discarded_low_score = discarded_count

        # --- Avoid filter ---
        filtered, avoid_count = self._filter_avoid(filtered, target_card)
        quality.discarded_avoid_match = avoid_count
        self._record_diagnostic("score_filtered", filtered)

        # Resolve role budgets (runtime override > config)
        resolved_budgets = dict(self.config.context.role_budgets)
        if role_budgets:
            resolved_budgets.update(role_budgets)

        # --- Classify priority and roles BEFORE selection ---
        filtered = self._classify_priority(
            filtered,
            target_card,
            dep_cards,
            target_path=target_path,
            line_start=line_start,
            line_end=line_end,
        )

        # --- Apply MMR Diversity Re-ranking ---
        filtered = apply_mmr_diversity(filtered, lambda_param=0.75)

        # Prioritize essential target text spans at the top
        filtered = sorted(
            filtered,
            key=lambda s: (
                0 if (s.priority == "essential" or s.source_role == "target_text") else (1 if s.priority == "supporting" else 2),
                -s.score,
            ),
        )
        self._record_diagnostic("diversified", filtered)

        # --- Atomic evidence coverage (single pass; no retrieval retry loop) ---
        fixed_packet_parts = [
            str((span.metadata or {}).get("snippet") or "")
            for span in filtered
            if span.source_role == "target_text"
        ]
        if self.section_cards and self.section_cards.document.thesis:
            fixed_packet_parts.append(self.section_cards.document.thesis)
        if target_card:
            fixed_packet_parts.extend(target_card.constraints or [])
            fixed_packet_parts.extend(target_card.must_preserve or [])
        for dep_card in dep_cards:
            for fact in dep_card.verified_facts or []:
                fixed_packet_parts.append(
                    str(fact.get("value") if isinstance(fact, dict) else fact)
                )
        fixed_packet_text = "\n".join(fixed_packet_parts)
        fixed_covered = {
            obligation.id
            for obligation in obligations
            if _obligation_matches_text(obligation, fixed_packet_text)
        }
        atomic_cover, atomic_hits = _greedy_atomic_cover(
            filtered, obligations, fixed_covered
        )
        atomic_span_ids = {id(span) for span in atomic_cover}
        original_order = {id(span): rank for rank, span in enumerate(filtered)}
        filtered = sorted(
            filtered,
            key=lambda span: (
                0
                if span.priority == "essential" or span.source_role == "target_text"
                else (1 if id(span) in atomic_span_ids else 2),
                original_order[id(span)],
            ),
        )
        self._record_diagnostic("budget_candidates", filtered)

        expanded_for_coverage = False
        required_atomic_spans = [
            span
            for span in filtered
            if span.source_role == "target_text" or id(span) in atomic_span_ids
        ]
        minimum_atomic_tokens = sum(
            self._estimate_tokens(span) for span in required_atomic_spans
        )
        if not is_strict and minimum_atomic_tokens > token_budget:
            max_budget = getattr(self.config.context, "max_token_budget", 32000)
            effective_budget = min(max_budget, minimum_atomic_tokens)
            if effective_budget > token_budget:
                previous_budget = token_budget
                token_budget = effective_budget
                expanded_for_coverage = True
                warnings.append(
                    "Note: Atomic evidence coverage expanded the token budget once "
                    f"from {previous_budget} to {token_budget} tokens "
                    f"(configured maximum: {max_budget})."
                )
        filtered_order = {id(span): rank for rank, span in enumerate(filtered)}

        # --- Token budget selection ---
        usable_budget = int(token_budget * (1.0 - self.config.context.reserved_generation_margin))
        selected: list[SourceSpan] = []
        current_tokens = 0
        tokens_by_role = dict.fromkeys(resolved_budgets, 0)
        provider_reference_tokens = 0
        provider_reference_limit = int(
            resolved_budgets.get("reference", 0.0) * usable_budget
        )

        def is_provider_reference(span: SourceSpan) -> bool:
            return span.source_role == "reference" and bool(
                (span.metadata or {}).get("provider_id")
            )

        # Pass 1: Strict allocation based on role fractions (soft guidance)
        pass2_candidates: list[SourceSpan] = []
        selection_rejections: dict[str, list[SourceSpan]] = defaultdict(list)
        for span in filtered:
            role = span.source_role
            est = self._estimate_tokens(span)
            role_limit = int(resolved_budgets.get(role, 0.0) * usable_budget)

            if len(selected) < max_spans and tokens_by_role.get(role, 0) + est <= role_limit:
                selected.append(span)
                tokens_by_role[role] = tokens_by_role.get(role, 0) + est
                if is_provider_reference(span):
                    provider_reference_tokens += est
                current_tokens += est
            else:
                pass2_candidates.append(span)

        # Pass 2: Fill remaining spans up to max_spans with strict ceiling bounding
        budget_dropped = 0
        cap_truncated = False
        for span in pass2_candidates:
            if len(selected) >= max_spans:
                cap_truncated = True
                budget_dropped += 1
                selection_rejections["max_source_spans"].append(span)
                continue

            est = self._estimate_tokens(span)
            role_limit = int(resolved_budgets.get(span.source_role, 0.0) * usable_budget)
            if (
                is_strict
                and is_provider_reference(span)
                and provider_reference_tokens + est > provider_reference_limit
            ):
                budget_dropped += 1
                selection_rejections["provider_reference_quota"].append(span)
                continue
            fits_budget = current_tokens + est <= token_budget
            if not fits_budget and not is_strict and not selected:
                max_budget = getattr(self.config.context, "max_token_budget", 32000)
                if est <= max_budget:
                    previous_budget = token_budget
                    token_budget = est
                    fits_budget = True
                    warnings.append(
                        f"Note: The retrieved context ({est} tokens) exceeded the requested "
                        f"budget ({previous_budget}). Auto-expanded token budget once to "
                        f"{token_budget} tokens (configured maximum: {max_budget})."
                    )
            if fits_budget:
                selected.append(span)
                current_tokens += est
                tokens_by_role[span.source_role] = tokens_by_role.get(span.source_role, 0) + est
                if is_provider_reference(span):
                    provider_reference_tokens += est
            else:
                budget_dropped += 1
                selection_rejections["token_budget"].append(span)

        quality.dropped_for_budget = budget_dropped
        quality.truncated = cap_truncated or (budget_dropped > 0)
        quality.selected_count = len(selected)
        selected.sort(key=lambda span: filtered_order[id(span)])
        for reason, rejected_spans in selection_rejections.items():
            self._record_diagnostic(f"rejected:{reason}", rejected_spans)
        self._record_diagnostic("selected", selected)

        selected_paths_by_obligation: dict[str, list[str]] = {
            obligation.id: [] for obligation in obligations
        }
        covered_ids = set(fixed_covered)
        for span in selected:
            for obligation_id in atomic_hits.get(id(span), set()):
                covered_ids.add(obligation_id)
                selected_paths_by_obligation[obligation_id].append(span.path)
        obligation_records = [
            {
                "id": obligation.id,
                "kind": obligation.kind,
                "label": obligation.label,
                "covered": obligation.id in covered_ids,
                "source_paths": list(
                    dict.fromkeys(selected_paths_by_obligation[obligation.id])
                ),
            }
            for obligation in obligations
        ]
        uncovered_ids = [
            obligation.id for obligation in obligations if obligation.id not in covered_ids
        ]
        quality.atomic_coverage = {
            "required": len(obligations),
            "covered": len(covered_ids),
            "ratio": round(len(covered_ids) / len(obligations), 4)
            if obligations
            else 1.0,
            "uncovered": uncovered_ids,
            "obligations": obligation_records,
            "requested_token_budget": initial_token_budget,
            "effective_token_budget": token_budget,
            "expanded_for_coverage": expanded_for_coverage,
            "minimum_coverage_tokens": minimum_atomic_tokens,
        }
        if uncovered_ids:
            quality.reason = "atomic_coverage_incomplete"
            warnings.append(
                "Atomic evidence coverage is incomplete for "
                f"{', '.join(uncovered_ids)}. Use request_more_context or a direct-read "
                "of the target dependencies before drafting."
            )

        if current_tokens > token_budget or cap_truncated or budget_dropped > 0:
            if current_tokens > token_budget:
                msg = f"Note: The retrieved context ({current_tokens} tokens) exceeded the requested budget ({token_budget}). All highly-relevant spans up to max_spans were included to prevent context bloat."
                warnings.append(msg)
            if cap_truncated:
                warnings.append(
                    f"{budget_dropped} candidate span(s) were dropped because the max_source_spans={max_spans} cap was reached."
                )
            elif budget_dropped > 0 and current_tokens <= token_budget:
                warnings.append(
                    f"{budget_dropped} candidate span(s) were dropped to strictly respect the token budget ({current_tokens}/{token_budget} tokens)."
                )

        # LaTeX safety layer scanning
        latex_commands = []
        for span in selected:
            if span.source_role == "target_text":
                snippet = (span.metadata or {}).get("snippet") or ""
                if snippet:
                    latex_commands.extend(scan_latex_commands(snippet))
        unique_latex = []
        for cmd in latex_commands:
            if cmd not in unique_latex:
                unique_latex.append(cmd)
        if unique_latex:
            warnings.append(
                "LaTeX Safety: The following LaTeX commands or math environments were detected in the target text "
                f"and must not be modified or deleted: {', '.join(unique_latex)}"
            )

        # --- Build pack metadata & prior claims ---
        constraints: list[str] = []
        doc_thesis: str | None = None
        if self.section_cards:
            doc_thesis = self.section_cards.document.thesis
            if target_card:
                constraints.extend(target_card.constraints or [])
                constraints.extend(target_card.must_preserve or [])

        # Include constraint serialization in token estimate
        constraint_tokens = estimate_tokens("\n".join(constraints)) if constraints else 0
        if doc_thesis:
            constraint_tokens += estimate_tokens(doc_thesis)

        # Populate prior claims from dependency verified facts with section provenance
        prior_claims: list[str] = []
        for dc in dep_cards:
            if dc.verified_facts:
                for vf in dc.verified_facts:
                    fact_val = vf.get("value") if isinstance(vf, dict) else str(vf)
                    claim_str = f"[{dc.id}] {fact_val}"
                    if claim_str not in prior_claims:
                        prior_claims.append(claim_str)

        if prior_claims:
            constraint_tokens += estimate_tokens("\n".join(prior_claims))

        terminology_pack: dict[str, str] = {}
        if (
            self.section_cards
            and self.section_cards.document
            and self.section_cards.document.terminology
        ):
            glossary = normalize_terminology(self.section_cards.document.terminology)
            key_terms = []
            if target_card and target_card.key_terms:
                key_terms.extend(target_card.key_terms)
            for dc in dep_cards:
                if dc.key_terms:
                    key_terms.extend(dc.key_terms)

            canonical_lookup: dict[str, tuple[str, dict[str, Any]]] = {}
            variant_lookup: dict[str, tuple[str, dict[str, Any]]] = {}
            for canonical_term, details in glossary.items():
                canonical_lookup[canonical_term.casefold()] = (canonical_term, details)
                for variant in details.get("variants", []):
                    variant_lookup[variant.casefold()] = (canonical_term, details)

            selected_terminology: dict[str, dict[str, Any]] = {}
            for kt in key_terms:
                kt_folded = kt.casefold()
                match = canonical_lookup.get(kt_folded) or variant_lookup.get(kt_folded)
                if match:
                    canonical_term, details = match
                    terminology_pack[canonical_term] = details["definition"]
                    selected_terminology[canonical_term] = details

            terminology_rules: list[str] = []
            for canonical_term, details in selected_terminology.items():
                parts = [f"Terminology: prefer the canonical term '{canonical_term}'"]
                if details["variants"]:
                    parts.append(f"accepted variants: {', '.join(details['variants'])}")
                if details["avoid"]:
                    parts.append(f"avoid: {', '.join(details['avoid'])}")
                terminology_rules.append("; ".join(parts) + ".")
            constraints.extend(rule for rule in terminology_rules if rule not in constraints)

        if terminology_pack:
            term_str = "\n".join(f"{k}: {v}" for k, v in terminology_pack.items())
            constraint_tokens += estimate_tokens(term_str)
            constraint_tokens += estimate_tokens("\n".join(terminology_rules))

        total_tokens = current_tokens + constraint_tokens
        quality.estimated_tokens = total_tokens

        run_id = str(uuid.uuid4())
        has_degrading = any(
            not w.startswith("LaTeX Safety:")
            and not w.startswith("Note:")
            and "candidate span(s) were dropped" not in w
            for w in warnings
        )
        status_str = "degraded" if (has_degrading or status == "degraded") else "complete"

        pack = ContextPack(
            task=task,
            target=target,
            document_thesis=doc_thesis,
            prior_claims=prior_claims,
            terminology=terminology_pack,
            constraints=constraints,
            source_spans=selected,
            estimated_tokens=total_tokens,
            status=status_str,
            warnings=warnings,
            quality=asdict(quality),
            summary=f"Context pack generated with {len(selected)} source spans.",
            run_id=run_id,
            cache=CacheDiagnostics(
                enabled=self.config.cache.enabled,
                hit=False,
                task_hash=task_hash,
                config_hash=config_hash,
                section_cards_hash=sc_hash,
                rtfm_index_fingerprint=retrieval_fingerprint,
            ),
            task_type=task_type,
            pack_mode=pack_mode,
        )

        # --- Cache write ---
        if self.config.cache.enabled:
            run_data = {
                "task_hash": task_hash,
                "task": task,
                "target": target,
                "corpus": self.config.rtfm.corpus,
                "token_budget": token_budget,
                "config_hash": config_hash,
                "section_cards_hash": sc_hash,
                "rtfm_index_fingerprint": retrieval_fingerprint,
                "retrieval_fingerprint": retrieval_fingerprint,
            }
            payload = asdict(pack)

            sources_to_store = []
            for s in selected:
                d = asdict(s)
                d["selected"] = 1
                sources_to_store.append(d)
            unselected = [s for s in filtered if s not in selected]
            for s in unselected:
                d = asdict(s)
                d["selected"] = 0
                sources_to_store.append(d)

            self.store.store_pack(run_id, run_data, payload, sources_to_store)

        return pack

    # -----------------------------------------------------------------------
    # Combined scoring with query-type scoping
    # -----------------------------------------------------------------------
    def _compute_final_score(
        self,
        result: RTFMResult,
        target_card: SectionCard | None,
        dep_cards: list[SectionCard],
        must_consider: list[str],
        query: str,
        query_type: str = "task",
        task_type: str | None = None,
        target_line_start: int | None = None,
        target_line_end: int | None = None,
        retrieval_rank: int | None = None,
    ) -> float:

        score = 0.0
        path_lower = result.path.replace("\\", "/").lower()
        content = result.snippet or ""
        metadata = result.metadata or {}

        is_target_file = _path_matches(result.path, target_card.path if target_card else None)
        if (
            is_target_file
            and target_line_start is not None
            and target_line_end is not None
            and result.line_start is not None
            and result.line_end is not None
        ):
            local_start = max(1, target_line_start - 15)
            local_end = target_line_end + 15
            is_target_file = max(local_start, result.line_start) <= min(
                local_end, result.line_end
            )
        is_dep_file = any(_path_matches(result.path, dc.path) for dc in dep_cards)

        # RTFM semantic relevance (weight 1.0)
        rtfm_score = result.score or 0.0
        score += 1.0 * rtfm_score
        if retrieval_rank is not None and retrieval_rank > 0:
            score += 0.1 / retrieval_rank

        # Key-term scoping penalty: if this query is a key_term and the result
        # is NOT from the target or dependency file, penalize heavily.
        # Key terms should retrieve content *about* that concept in the target section,
        # not every paper section that happens to mention the term.
        if query_type in ("key_term", "dep_key_term") and not is_target_file and not is_dep_file:
            score *= 0.25

        # Target file match boost
        if is_target_file:
            if task_type in ("revise_existing_section", "expand", "condense", "proofread"):
                score += 1.5
            elif task_type == "align_with_previous_sections":
                score += 0.2
            else:
                score += 0.8

        # Dependency file match boost
        if is_dep_file:
            if task_type == "align_with_previous_sections":
                score += 1.2
            else:
                score += 0.4

        # Chapter title contains query terms (+0.5, up to 3 hits)
        chapter_title = (metadata.get("chapter_title") or "").lower()
        task_kws = extract_keywords(query)
        kw_hits = sum(1 for kw in task_kws if kw in chapter_title)
        score += 0.5 * min(kw_hits, 3)

        # Key-term overlap in content (+0.3 each, up to 3)
        all_key_terms: list[str] = []
        if target_card and target_card.key_terms:
            all_key_terms.extend(target_card.key_terms)
        for dc in dep_cards:
            if dc.key_terms:
                all_key_terms.extend(dc.key_terms)
        kt_hits = sum(
            1 for t in all_key_terms if t.lower() in content.lower() or t.lower() in path_lower
        )
        score += 0.3 * min(kt_hits, 3)

        # Explicit must-consider (+1.0)
        if any(m.lower() in path_lower for m in must_consider):
            score += 1.0

        # Penalty: irrelevant directories
        if any(x in path_lower for x in ["generated", "build", "ignored"]):
            score -= 1.0

        return max(0.0, score)

    # -----------------------------------------------------------------------
    # Semantic reason string
    # -----------------------------------------------------------------------
    @staticmethod
    def _build_reason(
        result: RTFMResult,
        target_card: SectionCard | None,
        dep_cards: list[SectionCard],
        query_type: str,
        query: str,
    ) -> str:
        result.path.replace("\\", "/").lower()
        chapter = (result.metadata or {}).get("chapter_title") or ""

        if target_card and _path_matches(result.path, target_card.path):
            return f"Target section — {chapter}" if chapter else "Target section"
        for dc in dep_cards:
            if _path_matches(result.path, dc.path):
                return (
                    f"Dependency section '{dc.title or dc.id}' — {chapter}"
                    if chapter
                    else f"Dependency '{dc.id}'"
                )
        if query_type == "key_term":
            return f"Key term '{query}' match — {chapter}" if chapter else f"Key term '{query}'"
        if query_type == "dep_key_term":
            return (
                f"Dependency key term '{query}' match — {chapter}"
                if chapter
                else f"Dependency key term '{query}'"
            )
        if query_type in ("title", "dep_title"):
            return f"Section title match — {chapter}" if chapter else "Section title match"
        return f"Task query match — {chapter}" if chapter else f"Matched query: {query}"

    # -----------------------------------------------------------------------
    # Priority classification
    # -----------------------------------------------------------------------
    def _classify_priority(
        self,
        spans: list[SourceSpan],
        target_card: SectionCard | None,
        dep_cards: list[SectionCard],
        target_path: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
    ) -> list[SourceSpan]:
        """Assign priority: essential | supporting | background and source_role: target_text | local_context | dependency | reference."""
        if not spans:
            return spans

        target_path_val = target_path
        if not target_path_val and target_card and target_card.path:
            target_path_val = target_card.path

        dep_paths = {dc.path for dc in dep_cards if dc.path}
        top_score = max(s.score for s in spans)
        high_threshold = top_score * 0.4  # top 40% of score range = essential

        result = []
        for span in spans:
            span.path.replace("\\", "/")
            is_target = _path_matches(span.path, target_path_val)
            is_dep = any(_path_matches(span.path, dp) for dp in dep_paths)

            # Determine source_role
            if span.source_role in ("target_text", "local_context"):
                role = span.source_role
            elif is_target:
                if line_start is not None and line_end is not None:
                    span_start = span.line_start if span.line_start is not None else 1
                    span_end = span.line_end if span.line_end is not None else (span_start + 30)

                    target_s = line_start
                    target_e = line_end
                    local_s = max(1, line_start - 15)
                    local_e = line_end + 15

                    if max(target_s, span_start) <= min(target_e, span_end):
                        role = "target_text"
                    elif max(local_s, span_start) <= min(local_e, span_end):
                        role = "local_context"
                    else:
                        role = "reference"
                else:
                    role = "target_text"
            elif is_dep:
                role = "dependency"
            else:
                role = "reference"

            # Determine priority
            if role == "target_text":
                priority = "essential" if span.score >= high_threshold else "supporting"
            elif role == "local_context" or role == "dependency":
                priority = "supporting"
            else:
                priority = "supporting" if span.score >= high_threshold else "background"

            if span.priority == "essential":
                priority = "essential"

            result.append(
                SourceSpan(
                    path=span.path,
                    line_start=span.line_start,
                    line_end=span.line_end,
                    reason=span.reason,
                    score=span.score,
                    priority=priority,
                    query=span.query,
                    metadata=span.metadata,
                    source_role=role,
                )
            )
        return result
