"""Context pack schemas and generation."""
import uuid
import sys
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict, Any, Tuple

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.section_cards import SectionCards, SectionCard
from writing_context_rtfm.schemas import RTFMResult
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.hashing import compute_task_hash, stable_hash
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.token_budget import estimate_tokens, estimate_span_tokens

# ---------------------------------------------------------------------------
# Exclusion rules (Fix 2)
# ---------------------------------------------------------------------------
EXCLUDED_SOURCE_PATTERNS = [
    ".writing-context/",
    ".rtfm/",
    ".git/",
    "__pycache__/",
]
EXCLUDED_SOURCE_EXTENSIONS = {".sqlite", ".db", ".lock"}

def is_allowed_source(path: str) -> bool:
    """Return True if the path should appear as a manuscript source span."""
    normalized = path.replace("\\", "/")
    for pat in EXCLUDED_SOURCE_PATTERNS:
        if pat in normalized:
            return False
    from pathlib import Path
    if Path(normalized).suffix.lower() in EXCLUDED_SOURCE_EXTENSIONS:
        return False
    return True

# ---------------------------------------------------------------------------
# Task keyword extraction (Fix 4)
# ---------------------------------------------------------------------------
KEYWORD_STOPWORDS = {
    "write", "the", "section", "detailing", "and", "of", "for", "a", "an",
    "in", "to", "that", "with", "this", "is", "are", "be", "from", "on",
    "how", "using", "about", "into", "each", "by", "our",
}

def _extract_task_keywords(task: str) -> List[str]:
    words = task.lower().split()
    return [w.strip(".,;:") for w in words
            if w.strip(".,;:") not in KEYWORD_STOPWORDS and len(w.strip(".,;:")) > 3]

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SourceSpan:
    path: str
    line_start: Optional[int]
    line_end: Optional[int]
    reason: str
    score: float
    priority: str = "background"     # "essential" | "supporting" | "background"
    query: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

@dataclass
class PackQuality:
    section_cards_loaded: bool = False
    section_cards_path: Optional[str] = None
    config_loaded: bool = False
    project_root: Optional[str] = None
    queries_issued: int = 0
    candidate_count: int = 0
    selected_count: int = 0
    discarded_low_score: int = 0
    discarded_excluded_path: int = 0
    discarded_avoid_match: int = 0
    estimated_tokens: int = 0

@dataclass(frozen=True)
class ContextPack:
    task: str
    target: Optional[str]
    document_thesis: Optional[str]
    prior_claims: List[str]
    terminology: Dict[str, str]
    constraints: List[str]
    source_spans: List[SourceSpan]
    estimated_tokens: int
    status: str = "complete"           # "complete" | "degraded"
    warnings: List[str] = field(default_factory=list)
    quality: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------
class ContextPackGenerator:
    def __init__(self, config: AppConfig, section_cards: Optional[SectionCards],
                 adapter: RTFMAdapter, store: ExtensionStore):
        self.config = config
        self.section_cards = section_cards
        self.adapter = adapter
        self.store = store

    # -----------------------------------------------------------------------
    # Fix 4: Query builder
    # -----------------------------------------------------------------------
    def _build_queries(self, task: str, target: Optional[str],
                       must_consider: List[str]) -> Tuple[List[str], Optional[SectionCard], List[SectionCard], Dict[str, str]]:
        """Returns (queries, target_card, dep_cards, query_type_map).

        query_type_map maps each query string to one of:
          'task'         — the raw task string
          'title'        — target section title
          'key_term'     — a key term from section cards (scoped to target file)
          'dep_title'    — dependency section title
          'task_keyword' — keyword extracted from raw task
          'must_consider'— explicit user override
        """
        queries: List[str] = []
        query_type_map: Dict[str, str] = {}
        target_card: Optional[SectionCard] = None
        dep_cards: List[SectionCard] = []

        def _add(q: str, qtype: str):
            if q and q not in query_type_map:
                queries.append(q)
                query_type_map[q] = qtype

        # 1. Raw task
        _add(task, "task")

        if self.section_cards and target and target in self.section_cards.sections:
            target_card = self.section_cards.sections[target]

            # 2. Target section title
            if target_card.title:
                _add(target_card.title, "title")

            # 3. Key terms (up to 6)
            for kt in (target_card.key_terms or [])[:6]:
                _add(kt, "key_term")

            # 4. Dependency section titles
            for dep_id in (target_card.depends_on or []):
                if dep_id in self.section_cards.sections:
                    dep_card = self.section_cards.sections[dep_id]
                    dep_cards.append(dep_card)
                    if dep_card.title:
                        _add(dep_card.title, "dep_title")
                    for kt in (dep_card.key_terms or [])[:3]:
                        _add(kt, "dep_title")

        # 5. Task keywords
        for kw in _extract_task_keywords(task):
            _add(kw, "task_keyword")

        # 6. Explicit must-consider
        for mc in must_consider:
            _add(mc, "must_consider")

        return queries, target_card, dep_cards, query_type_map

    # -----------------------------------------------------------------------
    # Deduplication
    # -----------------------------------------------------------------------
    def _deduplicate_spans(self, candidates: List[SourceSpan]) -> List[SourceSpan]:
        grouped: Dict[Tuple, SourceSpan] = {}
        for c in candidates:
            key = (c.path, c.line_start, c.line_end)
            if key not in grouped or c.score > grouped[key].score:
                grouped[key] = c
        final = list(grouped.values())
        final.sort(key=lambda x: (-x.score, x.path, x.line_start or 0))
        return final

    # -----------------------------------------------------------------------
    # Fix 3: Score filtering with structural override
    # -----------------------------------------------------------------------
    def _filter_by_score(self, candidates: List[SourceSpan],
                         target_card: Optional[SectionCard],
                         dep_cards: List[SectionCard]) -> Tuple[List[SourceSpan], int]:
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

        kept: List[SourceSpan] = []
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
    def _filter_avoid(self, candidates: List[SourceSpan],
                      target_card: Optional[SectionCard]) -> Tuple[List[SourceSpan], int]:
        """Remove spans whose snippet matches any phrase in the section's avoid list."""
        avoid_phrases = (target_card.avoid or []) if target_card else []
        if not avoid_phrases:
            return candidates, 0

        kept: List[SourceSpan] = []
        discarded = 0
        for span in candidates:
            snippet = ((span.metadata or {}).get("snippet") or "").lower()
            path_lower = span.path.lower()
            # Only apply avoid filter to spans NOT from the target file
            # (target file content stays regardless — we need it to write)
            if target_card and target_card.path and path_lower.endswith(target_card.path.lower().lstrip("./")):
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
    def generate(self, task: str, target: Optional[str], token_budget: int,
                 must_consider: Optional[List[str]] = None,
                 project_root: Optional[str] = None) -> ContextPack:
        must_consider = must_consider or []
        pr = project_root or self.config.rtfm.project_root or "."

        # --- Diagnostics setup (Fix 7) ---
        warnings: List[str] = []
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
                warnings.append("No --target provided. Query expansion is limited to raw task string.")

        # --- Cache check ---
        task_hash = compute_task_hash(task, target, token_budget)
        config_hash = stable_hash(str(self.config.version))
        sc_hash = stable_hash(str(self.section_cards.version) if self.section_cards else "none")
        fingerprint = "v0.1-fingerprint"

        if self.config.cache.enabled:
            cached = self.store.get_cached_pack(task_hash, config_hash, sc_hash, fingerprint)
            if cached:
                spans = [SourceSpan(**s) for s in cached.get("source_spans", [])]
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
                )

        # --- Query expansion (Fix 4) ---
        queries, target_card, dep_cards, query_type_map = self._build_queries(task, target, must_consider)
        quality.queries_issued = len(queries)

        # --- Retrieval ---
        all_candidates: List[SourceSpan] = []
        for q in queries:
            try:
                results = self.adapter.search(
                    q,
                    corpus=self.config.rtfm.corpus,
                    limit=self.config.context.max_search_results_per_query
                )
                for r in results:
                    if not is_allowed_source(r.path):          # Fix 2
                        quality.discarded_excluded_path += 1
                        continue
                    # Combined scoring with query-type scoping
                    query_type = query_type_map.get(q, "task_keyword")
                    score = self._compute_final_score(r, target_card, dep_cards, must_consider, q, query_type)
                    span = SourceSpan(
                        path=r.path,
                        line_start=r.line_start,
                        line_end=r.line_end,
                        reason=self._build_reason(r, target_card, dep_cards, query_type, q),
                        score=score,
                        query=q,
                        metadata={**(r.metadata or {}), "snippet": r.snippet}
                    )
                    all_candidates.append(span)
            except Exception as e:
                warnings.append(f"Search failed for query '{q}': {e}")

        quality.candidate_count = len(all_candidates)

        # --- Dedup ---
        deduped = self._deduplicate_spans(all_candidates)

        # --- Score filtering ---
        filtered, discarded_count = self._filter_by_score(deduped, target_card, dep_cards)
        quality.discarded_low_score = discarded_count

        # --- Avoid filter ---
        filtered, avoid_count = self._filter_avoid(filtered, target_card)
        quality.discarded_avoid_match = avoid_count

        # --- Token budget selection ---
        usable_budget = int(token_budget * (1.0 - self.config.context.reserved_generation_margin))
        selected: List[SourceSpan] = []
        current_tokens = 0
        for span in filtered:
            est = self._estimate_tokens(span)
            if current_tokens + est <= usable_budget:
                selected.append(span)
                current_tokens += est
                if len(selected) >= self.config.context.max_source_spans:
                    break

        # --- Classify priority ---
        selected = self._classify_priority(selected, target_card, dep_cards)

        quality.selected_count = len(selected)
        quality.estimated_tokens = current_tokens

        # --- Build pack metadata ---
        constraints: List[str] = []
        doc_thesis: Optional[str] = None
        if self.section_cards:
            doc_thesis = self.section_cards.document.thesis
            if target_card:
                constraints.extend(target_card.constraints or [])
                constraints.extend(target_card.must_preserve or [])

        status = "degraded" if warnings else "complete"

        pack = ContextPack(
            task=task,
            target=target,
            document_thesis=doc_thesis,
            prior_claims=[],
            terminology={},
            constraints=constraints,
            source_spans=selected,
            estimated_tokens=current_tokens,
            status=status,
            warnings=warnings,
            quality=asdict(quality),
            summary=f"Context pack generated with {len(selected)} source spans."
        )

        # --- Cache write ---
        if self.config.cache.enabled:
            run_id = str(uuid.uuid4())
            run_data = {
                "task_hash": task_hash,
                "task": task,
                "target": target,
                "corpus": self.config.rtfm.corpus,
                "token_budget": token_budget,
                "config_hash": config_hash,
                "section_cards_hash": sc_hash,
                "rtfm_index_fingerprint": fingerprint
            }
            payload = asdict(pack)
            sources = [asdict(s) for s in selected]
            self.store.store_pack(run_id, run_data, payload, sources)

        return pack

    # -----------------------------------------------------------------------
    # Combined scoring with query-type scoping
    # -----------------------------------------------------------------------
    def _compute_final_score(self, result: RTFMResult, target_card: Optional[SectionCard],
                              dep_cards: List[SectionCard], must_consider: List[str],
                              query: str, query_type: str = "task") -> float:
        score = 0.0
        path_lower = result.path.replace("\\", "/").lower()
        content = result.snippet or ""
        metadata = result.metadata or {}

        is_target_file = (
            target_card is not None
            and target_card.path is not None
            and path_lower.endswith(target_card.path.lower().lstrip("./"))
        )
        is_dep_file = any(
            dc.path and path_lower.endswith(dc.path.lower().lstrip("./"))
            for dc in dep_cards
        )

        # RTFM semantic relevance (weight 1.0)
        rtfm_score = result.score or 0.0
        score += 1.0 * rtfm_score

        # Key-term scoping penalty: if this query is a key_term and the result
        # is NOT from the target or dependency file, penalize heavily.
        # Key terms should retrieve content *about* that concept in the target section,
        # not every paper section that happens to mention the term.
        if query_type == "key_term" and not is_target_file and not is_dep_file:
            score *= 0.25

        # Target file match (+0.8)
        if is_target_file:
            score += 0.8

        # Dependency file match (+0.4)
        if is_dep_file:
            score += 0.4

        # Chapter title contains query terms (+0.5, up to 3 hits)
        chapter_title = (metadata.get("chapter_title") or "").lower()
        task_kws = _extract_task_keywords(query)
        kw_hits = sum(1 for kw in task_kws if kw in chapter_title)
        score += 0.5 * min(kw_hits, 3)

        # Key-term overlap in content (+0.3 each, up to 3)
        all_key_terms: List[str] = []
        if target_card and target_card.key_terms:
            all_key_terms.extend(target_card.key_terms)
        for dc in dep_cards:
            if dc.key_terms:
                all_key_terms.extend(dc.key_terms)
        kt_hits = sum(1 for t in all_key_terms
                      if t.lower() in content.lower() or t.lower() in path_lower)
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
    def _build_reason(result: RTFMResult, target_card: Optional[SectionCard],
                       dep_cards: List[SectionCard], query_type: str, query: str) -> str:
        path_lower = result.path.replace("\\", "/").lower()
        chapter = (result.metadata or {}).get("chapter_title") or ""

        if target_card and target_card.path and path_lower.endswith(target_card.path.lower().lstrip("./")):
            return f"Target section — {chapter}" if chapter else "Target section"
        for dc in dep_cards:
            if dc.path and path_lower.endswith(dc.path.lower().lstrip("./")):
                return f"Dependency section '{dc.title or dc.id}' — {chapter}" if chapter else f"Dependency '{dc.id}'"
        if query_type == "key_term":
            return f"Key term '{query}' match — {chapter}" if chapter else f"Key term '{query}'"
        if query_type in ("title", "dep_title"):
            return f"Section title match — {chapter}" if chapter else "Section title match"
        return f"Task query match — {chapter}" if chapter else f"Matched query: {query}"

    # -----------------------------------------------------------------------
    # Priority classification
    # -----------------------------------------------------------------------
    def _classify_priority(self, spans: List[SourceSpan],
                           target_card: Optional[SectionCard],
                           dep_cards: List[SectionCard]) -> List[SourceSpan]:
        """Assign priority: essential | supporting | background."""
        if not spans:
            return spans

        target_path = target_card.path if target_card else None
        dep_paths = {dc.path for dc in dep_cards if dc.path}
        top_score = max(s.score for s in spans)
        high_threshold = top_score * 0.4   # top 40% of score range = essential

        result = []
        for span in spans:
            path_norm = span.path.replace("\\", "/")
            is_target = target_path and path_norm.endswith(target_path.lstrip("./"))
            is_dep = any(path_norm.endswith(dp.lstrip("./")) for dp in dep_paths)

            if is_target and span.score >= high_threshold:
                priority = "essential"
            elif is_target or is_dep:
                priority = "supporting"
            elif span.score >= high_threshold:
                priority = "supporting"
            else:
                priority = "background"

            # Replace frozen dataclass with updated priority
            result.append(SourceSpan(
                path=span.path,
                line_start=span.line_start,
                line_end=span.line_end,
                reason=span.reason,
                score=span.score,
                priority=priority,
                query=span.query,
                metadata=span.metadata,
            ))
        return result
