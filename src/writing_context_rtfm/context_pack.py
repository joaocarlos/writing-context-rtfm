"""Context pack schemas and generation."""
import uuid
import hashlib
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
from collections import defaultdict

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.section_cards import SectionCards, SectionCard
from writing_context_rtfm.schemas import RTFMResult
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.hashing import compute_task_hash, stable_hash, compute_rtfm_fingerprint
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.token_budget import estimate_tokens, estimate_span_tokens

from writing_context_rtfm.utils import is_allowed_source, extract_keywords, scan_latex_commands, resolve_rtfm_db_path

def _path_matches(path: str, card_path: Optional[str]) -> bool:
    """Helper to check if path matches a card's path case-insensitively."""
    if not card_path:
        return False
    p_norm = path.replace("\\", "/").lower()
    c_norm = card_path.replace("\\", "/").lower().lstrip("./")
    return p_norm.endswith(c_norm)

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
    source_role: str = "reference"   # "target_text" | "local_context" | "dependency" | "reference"

@dataclass(frozen=True)
class CacheDiagnostics:
    enabled: bool
    hit: bool
    task_hash: Optional[str] = None
    config_hash: Optional[str] = None
    section_cards_hash: Optional[str] = None
    rtfm_index_fingerprint: Optional[str] = None

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
    dropped_for_budget: int = 0
    truncated: bool = False
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
    run_id: Optional[str] = None
    cache: Optional[CacheDiagnostics] = None
    task_type: Optional[str] = None
    pack_mode: Optional[str] = None


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
        self._hash_cache: Dict[Path, Tuple[float, int, str]] = {}

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
        except (OSError, IOError):
            return fallback_val

    def _resolve_target(self, target: Optional[str], pr: str) -> Tuple[Optional[str], Optional[SectionCard], Optional[str]]:
        """Resolves target string to (card_key, card, path)."""
        if not target:
            return None, None, None

        if not self.section_cards or not self.section_cards.sections:
            # Fallback path check if target is a path
            test_path = Path(pr) / target
            if test_path.is_file() or target.endswith((".tex", ".md", ".txt", ".py", ".json", ".yaml", ".yml", ".bib")):
                return None, None, target
            return None, None, None

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
            # Check card title (case-insensitive)
            if card.title and card.title.lower() == target_lower:
                return key, card, card.path
            
            # Check card path (stem or path matching)
            if card.path:
                card_path = Path(card.path)
                # match stem (e.g. abstract to abstract.tex) or exact path or name
                if card_path.stem.lower() == target_lower or card_path.name.lower() == target_lower or card.path.lower() == target_lower:
                    return key, card, card.path

        # 5. Check if target is a file path in the workspace
        test_path = Path(pr) / target
        if test_path.is_file() or target.endswith((".tex", ".md", ".txt", ".py", ".json", ".yaml", ".yml", ".bib")):
            return None, None, target

        return None, None, None

    # -----------------------------------------------------------------------
    # Fix 4: Query builder
    # -----------------------------------------------------------------------
    def _build_queries(self, task: str, target: Optional[str],
                       must_consider: List[str], task_type: Optional[str] = None,
                       pack_mode: Optional[str] = None,
                       has_line_range: bool = False) -> Tuple[List[str], Optional[SectionCard], List[SectionCard], Dict[str, str]]:
        """Returns (queries, target_card, dep_cards, query_type_map).

        query_type_map maps each query string to one of:
          'task'         — the raw task string
          'title'        — target section title
          'key_term'     — a key term from section cards (scoped to target file)
          'dep_title'    — dependency section title
          'task_keyword' — keyword extracted from raw task
          'must_consider'— explicit user override
          'thesis'       - document thesis
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

        # 2. Document thesis (if review)
        if task_type == "review" and self.section_cards and self.section_cards.document.thesis:
            _add(self.section_cards.document.thesis, "thesis")

        if self.section_cards and target and target in self.section_cards.sections:
            target_card = self.section_cards.sections[target]

            # 3. Target section title and key terms (skip key terms if minimal or line range exists)
            if not has_line_range and pack_mode != "minimal":
                if target_card.title:
                    _add(target_card.title, "title")
                
                # Align with previous sections scales down target key terms
                if task_type == "align_with_previous_sections":
                    max_kt = 2
                elif pack_mode == "deep":
                    max_kt = 12
                else:
                    max_kt = 6

                for kt in (target_card.key_terms or [])[:max_kt]:
                    _add(kt, "key_term")
            elif not has_line_range and pack_mode == "minimal":
                if target_card.title:
                    _add(target_card.title, "title")

            # 4. Dependency section titles and key terms (skip key terms if minimal)
            for dep_id in (target_card.depends_on or []):
                if dep_id in self.section_cards.sections:
                    dep_card = self.section_cards.sections[dep_id]
                    dep_cards.append(dep_card)
                    if dep_card.title:
                        _add(dep_card.title, "dep_title")
                    
                    if pack_mode != "minimal":
                        max_dep_kt = 6 if pack_mode == "deep" else 3
                        for kt in (dep_card.key_terms or [])[:max_dep_kt]:
                            _add(kt, "dep_key_term")

        # 5. Task keywords (skip if minimal or review)
        if pack_mode != "minimal" and task_type != "review":
            for kw in extract_keywords(task):
                _add(kw, "task_keyword")

        # 6. Explicit must-consider
        for mc in must_consider:
            _add(mc, "must_consider")

        return queries, target_card, dep_cards, query_type_map

    # -----------------------------------------------------------------------
    # Deduplication
    # -----------------------------------------------------------------------
    def _deduplicate_spans(self, candidates: List[SourceSpan]) -> List[SourceSpan]:
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

            current_merged: List[SourceSpan] = []
            for span in spans:
                if not current_merged:
                    current_merged.append(span)
                    continue

                prev = current_merged[-1]
                prev_start = prev.line_start or 1
                prev_end = prev.line_end or prev_start
                curr_start = span.line_start or 1
                curr_end = span.line_end or curr_start

                # Overlap or adjacency check:
                # If current interval starts within or adjacent to previous interval
                if curr_start <= prev_end + 1:
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
                    role_order = {"target_text": 4, "local_context": 3, "dependency": 2, "reference": 1}
                    
                    merged_priority = prev.priority if priority_order.get(prev.priority, 0) >= priority_order.get(span.priority, 0) else span.priority
                    merged_role = prev.source_role if role_order.get(prev.source_role, 0) >= role_order.get(span.source_role, 0) else span.source_role

                    # Replace the last element with the merged span
                    current_merged[-1] = SourceSpan(
                        path=path,
                        line_start=prev_start,
                        line_end=new_end,
                        reason=combined_reason,
                        score=max_score,
                        priority=merged_priority,
                        query=combined_query,
                        metadata=merged_meta,
                        source_role=merged_role
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
    def generate(self, task: str, target: Optional[str], token_budget: int,
                 must_consider: Optional[List[str]] = None,
                 project_root: Optional[str] = None,
                 task_type: Optional[str] = None,
                 line_start: Optional[int] = None,
                 line_end: Optional[int] = None,
                 pack_mode: Optional[str] = None,
                 role_budgets: Optional[Dict[str, float]] = None) -> ContextPack:
        must_consider = must_consider or []
        pr = project_root or self.config.rtfm.project_root or "."
        task_type = task_type or "write_new_section"
        pack_mode = pack_mode or "standard"

        # Apply pack mode defaults / overrides
        if pack_mode == "minimal":
            token_budget = min(token_budget, 2000)
            max_spans = 5
        elif pack_mode == "deep":
            max_spans = 60
        else:
            max_spans = self.config.context.max_source_spans

        # --- Diagnostics setup (Fix 7) ---
        warnings: List[str] = []
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
                warnings.append("No --target provided. Query expansion is limited to raw task string.")

        # --- Cache check ---
        task_hash = compute_task_hash(
            task, target, token_budget,
            task_type=task_type,
            line_start=line_start,
            line_end=line_end,
            pack_mode=pack_mode
        )
        
        # Calculate real config file content hash
        config_path = Path(self.config.rtfm.project_root) / ".writing-context" / "config.yaml"
        config_hash = self._get_file_hash(config_path, stable_hash(str(self.config.version)))

        # Calculate real section cards content hash
        sc_path = Path(self.config.section_cards.path)
        sc_fallback = stable_hash(str(self.section_cards.version) if self.section_cards else "none")
        sc_hash = self._get_file_hash(sc_path, sc_fallback)

        # Compute real RTFM database fingerprint based on mtime and size
        rtfm_db = resolve_rtfm_db_path(Path(self.config.rtfm.project_root))
        fingerprint = compute_rtfm_fingerprint(rtfm_db)

        if self.config.cache.enabled:
            cached = self.store.get_cached_pack(task_hash, config_hash, sc_hash, fingerprint)
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
                        rtfm_index_fingerprint=fingerprint
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
                    pack_mode=cached.get("pack_mode")
                )

        # --- Target Line Range Resolution ---
        resolved_key, target_card, target_path = self._resolve_target(target, pr)

        all_candidates: List[SourceSpan] = []
        lines_prepended = False
        if line_start is not None and line_end is not None:
            if not target_path:
                warnings.append("line_start and line_end provided but target file path could not be resolved.")
                status = "degraded"
            else:
                try:
                    full_path = Path(pr) / target_path
                    if full_path.exists() and full_path.is_file():
                        file_content = full_path.read_text(encoding="utf-8", errors="replace")
                        lines = file_content.splitlines()
                        num_lines = len(lines)
                        
                        start = max(1, min(line_start, num_lines))
                        end = max(1, min(line_end, num_lines))
                        if start > end:
                            start, end = end, start
                            
                        # Extract target text span
                        target_snippet = "\n".join(lines[start-1:end])
                        target_span = SourceSpan(
                            path=target_path,
                            line_start=start,
                            line_end=end,
                            reason="Target text range",
                            score=1.0,
                            priority="essential",
                            source_role="target_text",
                            metadata={"snippet": target_snippet}
                        )
                        all_candidates.append(target_span)
                        
                        # Local context before
                        if start > 1:
                            ctx_start = max(1, start - 15)
                            ctx_end = start - 1
                            before_snippet = "\n".join(lines[ctx_start-1:ctx_end])
                            before_span = SourceSpan(
                                path=target_path,
                                line_start=ctx_start,
                                line_end=ctx_end,
                                reason="Surrounding target context (before)",
                                score=0.9,
                                priority="supporting",
                                source_role="local_context",
                                metadata={"snippet": before_snippet}
                            )
                            all_candidates.append(before_span)
                            
                        # Local context after
                        if end < num_lines:
                            ctx_start = end + 1
                            ctx_end = min(num_lines, end + 15)
                            after_snippet = "\n".join(lines[ctx_start-1:ctx_end])
                            after_span = SourceSpan(
                                path=target_path,
                                line_start=ctx_start,
                                line_end=ctx_end,
                                reason="Surrounding target context (after)",
                                score=0.9,
                                priority="supporting",
                                source_role="local_context",
                                metadata={"snippet": after_snippet}
                            )
                            all_candidates.append(after_span)
                            
                        lines_prepended = True
                    else:
                        warnings.append(f"Target file '{target_path}' not found for line range extraction.")
                        status = "degraded"
                except Exception as e:
                    warnings.append(f"Failed to read target file '{target_path}': {e}")
                    status = "degraded"

        # --- Query expansion (Fix 4) ---
        queries, target_card, dep_cards, query_type_map = self._build_queries(
            task, resolved_key or target, must_consider,
            task_type=task_type,
            pack_mode=pack_mode,
            has_line_range=lines_prepended
        )
        quality.queries_issued = len(queries)

        # --- Retrieval ---
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
                    score = self._compute_final_score(r, target_card, dep_cards, must_consider, q, query_type, task_type=task_type)
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

        # Resolve role budgets (runtime override > config)
        resolved_budgets = dict(self.config.context.role_budgets)
        if role_budgets:
            resolved_budgets.update(role_budgets)

        # --- Classify priority and roles BEFORE selection so we have source_role populated ---
        filtered = self._classify_priority(
            filtered, target_card, dep_cards,
            target_path=target_path,
            line_start=line_start,
            line_end=line_end
        )

        # --- Token budget selection ---
        usable_budget = int(token_budget * (1.0 - self.config.context.reserved_generation_margin))
        selected: List[SourceSpan] = []
        current_tokens = 0
        tokens_by_role = {r: 0 for r in resolved_budgets}

        # Pass 1: Strict allocation based on role fractions
        pass2_candidates: List[SourceSpan] = []
        for span in filtered:
            role = span.source_role
            est = self._estimate_tokens(span)
            role_limit = int(resolved_budgets.get(role, 0.0) * usable_budget)

            if len(selected) < max_spans and tokens_by_role.get(role, 0) + est <= role_limit and current_tokens + est <= usable_budget:
                selected.append(span)
                tokens_by_role[role] = tokens_by_role.get(role, 0) + est
                current_tokens += est
            else:
                pass2_candidates.append(span)

        # Pass 2: Redistribution of remaining budget
        budget_dropped = 0
        cap_truncated = False
        unselected: List[SourceSpan] = []
        for span in pass2_candidates:
            est = self._estimate_tokens(span)
            if len(selected) >= max_spans:
                cap_truncated = True
                budget_dropped += 1
                unselected.append(span)
                continue
            if current_tokens + est <= usable_budget:
                selected.append(span)
                current_tokens += est
            else:
                budget_dropped += 1
                unselected.append(span)

        if budget_dropped > 0:
            quality.dropped_for_budget = budget_dropped
            quality.truncated = True
            if cap_truncated:
                warnings.append(
                    f"{budget_dropped} candidate span(s) dropped: token budget or "
                    f"max_source_spans={max_spans} cap reached. "
                    "Increase token_budget or refine `target`/`must_consider` for more coverage."
                )
            else:
                warnings.append(
                    f"{budget_dropped} candidate span(s) dropped to stay within token budget "
                    f"({usable_budget} usable of {token_budget}). Increase token_budget for more coverage."
                )

        quality.selected_count = len(selected)

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

        # --- Build pack metadata ---
        constraints: List[str] = []
        doc_thesis: Optional[str] = None
        if self.section_cards:
            doc_thesis = self.section_cards.document.thesis
            if target_card:
                constraints.extend(target_card.constraints or [])
                constraints.extend(target_card.must_preserve or [])

        # Include constraint serialization in token estimate so clients can
        # budget downstream generation accurately.
        constraint_tokens = estimate_tokens("\n".join(constraints)) if constraints else 0
        if doc_thesis:
            constraint_tokens += estimate_tokens(doc_thesis)

        terminology_pack: Dict[str, str] = {}
        if self.section_cards and self.section_cards.document and self.section_cards.document.terminology:
            glossary = self.section_cards.document.terminology
            key_terms = []
            if target_card and target_card.key_terms:
                key_terms.extend(target_card.key_terms)
            for dc in dep_cards:
                if dc.key_terms:
                    key_terms.extend(dc.key_terms)
            
            canonical_lookup = {}
            variant_lookup = {}
            for canonical_term, details in glossary.items():
                defn = details.get("definition") or ""
                canonical_lookup[canonical_term.lower()] = (canonical_term, defn)
                for variant in details.get("variants", []):
                    variant_lookup[variant.lower()] = (canonical_term, defn)

            for kt in key_terms:
                kt_lower = kt.lower()
                if kt_lower in canonical_lookup:
                    canonical_term, defn = canonical_lookup[kt_lower]
                    terminology_pack[canonical_term] = defn
                elif kt_lower in variant_lookup:
                    canonical_term, defn = variant_lookup[kt_lower]
                    terminology_pack[canonical_term] = defn

        if terminology_pack:
            term_str = "\n".join(f"{k}: {v}" for k, v in terminology_pack.items())
            constraint_tokens += estimate_tokens(term_str)

        total_tokens = current_tokens + constraint_tokens
        quality.estimated_tokens = total_tokens

        run_id = str(uuid.uuid4())
        has_degrading = any(not w.startswith("LaTeX Safety:") for w in warnings)
        status_str = "degraded" if (has_degrading or status == "degraded") else "complete"

        pack = ContextPack(
            task=task,
            target=target,
            document_thesis=doc_thesis,
            prior_claims=[],
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
                rtfm_index_fingerprint=fingerprint
            ),
            task_type=task_type,
            pack_mode=pack_mode
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
                "rtfm_index_fingerprint": fingerprint
            }
            payload = asdict(pack)
            
            sources_to_store = []
            for s in selected:
                d = asdict(s)
                d["selected"] = 1
                sources_to_store.append(d)
            for s in unselected:
                d = asdict(s)
                d["selected"] = 0
                sources_to_store.append(d)
                
            self.store.store_pack(run_id, run_data, payload, sources_to_store)

        return pack

    # -----------------------------------------------------------------------
    # Combined scoring with query-type scoping
    # -----------------------------------------------------------------------
    def _compute_final_score(self, result: RTFMResult, target_card: Optional[SectionCard],
                               dep_cards: List[SectionCard], must_consider: List[str],
                               query: str, query_type: str = "task",
                               task_type: Optional[str] = None) -> float:
        score = 0.0
        path_lower = result.path.replace("\\", "/").lower()
        content = result.snippet or ""
        metadata = result.metadata or {}

        is_target_file = _path_matches(result.path, target_card.path if target_card else None)
        is_dep_file = any(_path_matches(result.path, dc.path) for dc in dep_cards)

        # RTFM semantic relevance (weight 1.0)
        rtfm_score = result.score or 0.0
        score += 1.0 * rtfm_score

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
        result.path.replace("\\", "/").lower()
        chapter = (result.metadata or {}).get("chapter_title") or ""

        if target_card and _path_matches(result.path, target_card.path):
            return f"Target section — {chapter}" if chapter else "Target section"
        for dc in dep_cards:
            if _path_matches(result.path, dc.path):
                return f"Dependency section '{dc.title or dc.id}' — {chapter}" if chapter else f"Dependency '{dc.id}'"
        if query_type == "key_term":
            return f"Key term '{query}' match — {chapter}" if chapter else f"Key term '{query}'"
        if query_type == "dep_key_term":
            return f"Dependency key term '{query}' match — {chapter}" if chapter else f"Dependency key term '{query}'"
        if query_type in ("title", "dep_title"):
            return f"Section title match — {chapter}" if chapter else "Section title match"
        return f"Task query match — {chapter}" if chapter else f"Matched query: {query}"

    # -----------------------------------------------------------------------
    # Priority classification
    # -----------------------------------------------------------------------
    def _classify_priority(self, spans: List[SourceSpan],
                           target_card: Optional[SectionCard],
                           dep_cards: List[SectionCard],
                           target_path: Optional[str] = None,
                           line_start: Optional[int] = None,
                           line_end: Optional[int] = None) -> List[SourceSpan]:
        """Assign priority: essential | supporting | background and source_role: target_text | local_context | dependency | reference."""
        if not spans:
            return spans

        target_path_val = target_path
        if not target_path_val and target_card and target_card.path:
            target_path_val = target_card.path

        dep_paths = {dc.path for dc in dep_cards if dc.path}
        top_score = max(s.score for s in spans)
        high_threshold = top_score * 0.4   # top 40% of score range = essential

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
                if span.score >= high_threshold:
                    priority = "essential"
                else:
                    priority = "supporting"
            elif role == "local_context" or role == "dependency":
                priority = "supporting"
            else:
                if span.score >= high_threshold:
                    priority = "supporting"
                else:
                    priority = "background"

            if span.priority == "essential":
                priority = "essential"

            result.append(SourceSpan(
                path=span.path,
                line_start=span.line_start,
                line_end=span.line_end,
                reason=span.reason,
                score=span.score,
                priority=priority,
                query=span.query,
                metadata=span.metadata,
                source_role=role
            ))
        return result
