"""Proofreading context mode logic."""
import os
import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from pathlib import Path

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.section_cards import SectionCards, SectionCard
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.utils import is_allowed_source, extract_keywords
from writing_context_rtfm.token_budget import estimate_tokens

@dataclass(frozen=True)
class TerminologyConstraint:
    term: str
    usage_examples: List[str]
    definition: Optional[str] = None

@dataclass(frozen=True)
class ProofreadingConstraints:
    mode: str
    strictness: str
    general_rules: List[str]
    section_specific_rules: List[str]
    terminology: List[TerminologyConstraint]

@dataclass(frozen=True)
class LocalContext:
    target_span: str
    previous_paragraph: Optional[str]
    next_paragraph: Optional[str]

@dataclass(frozen=True)
class ProofreadingTarget:
    file_path: str
    line_start: int
    line_end: int
    section_id: Optional[str]

@dataclass(frozen=True)
class ProofreadingContextPack:
    target: ProofreadingTarget
    local_context: LocalContext
    constraints: ProofreadingConstraints
    estimated_tokens: int
    status: str = "complete"

MODE_CONSTRAINTS = {
    "surface": [
        "Focus on grammar, spelling, and punctuation.",
        "Maintain current sentence structure and tone.",
    ],
    "academic_clarity": [
        "Enhance precision and formal vocabulary.",
        "Ensure clear logical flow between sentences.",
        "Avoid colloquialisms and imprecise modifiers.",
    ],
    "consistency": [
        "Check for consistent use of technical terminology.",
        "Ensure formatting (e.g., italics, bold) is used consistently.",
    ],
    "latex_safe": [
        "Preserve LaTeX commands and math environments exactly.",
        "Do not alter \\cite, \\ref, or \\label commands.",
    ]
}

STRICTNESS_RULES = {
    "conservative": "Make only essential corrections. Preserve original phrasing where possible.",
    "moderate": "Balance original style with necessary improvements for clarity and flow.",
    "assertive": "Boldly rewrite for maximum impact, clarity, and adherence to academic standards."
}

class ProofreadPackGenerator:
    def __init__(self, config: AppConfig, section_cards: Optional[SectionCards],
                 adapter: RTFMAdapter, store: ExtensionStore):
        self.config = config
        self.section_cards = section_cards
        self.adapter = adapter
        self.store = store

    def generate(self, target_file: str, line_start: int, line_end: int,
                 mode: str = "surface", strictness: str = "moderate",
                 max_tokens: int = 4000) -> ProofreadingContextPack:
        
        # 1. Load target span and local context
        local_ctx = self._get_local_context(target_file, line_start, line_end)
        
        # 2. Find section card
        section_card = self._find_section_card(target_file)
        target_info = ProofreadingTarget(
            file_path=target_file,
            line_start=line_start,
            line_end=line_end,
            section_id=section_card.id if section_card else None
        )
        
        # 3. Extract key terms and get prior usage
        key_terms = extract_keywords(local_ctx.target_span)
        terminology = self._get_prior_usage(key_terms, target_file)
        
        # 4. Generate constraints
        constraints = self._generate_constraints(mode, strictness, section_card, terminology)
        
        # 5. Assemble and estimate tokens
        pack = ProofreadingContextPack(
            target=target_info,
            local_context=local_ctx,
            constraints=constraints,
            estimated_tokens=0  # placeholder
        )
        
        # Token budgeting (simple version for proofreading)
        total_text = local_ctx.target_span + (local_ctx.previous_paragraph or "") + (local_ctx.next_paragraph or "")
        est = estimate_tokens(total_text) + estimate_tokens(json.dumps(asdict(constraints)))
        
        return ProofreadingContextPack(
            target=target_info,
            local_context=local_ctx,
            constraints=constraints,
            estimated_tokens=est,
            status="complete" if est <= max_tokens else "degraded"
        )

    def _get_local_context(self, file_path: str, start: int, end: int) -> LocalContext:
        if not os.path.exists(file_path):
            return LocalContext(f"[File not found: {file_path}]", None, None)
            
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        # Target span
        target_lines = lines[start-1:end]
        target_span = "".join(target_lines)
        
        # Previous paragraph
        prev_lines = lines[:start-1]
        prev_para = []
        for line in reversed(prev_lines):
            if not line.strip() and prev_para:
                break
            prev_para.append(line)
        previous_paragraph = "".join(reversed(prev_para)).strip() if prev_para else None
        
        # Next paragraph
        next_lines = lines[end:]
        next_para = []
        for line in next_lines:
            if not line.strip() and next_para:
                break
            next_para.append(line)
        next_paragraph = "".join(next_para).strip() if next_para else None
        
        return LocalContext(target_span, previous_paragraph, next_paragraph)

    def _find_section_card(self, target_file: str) -> Optional[SectionCard]:
        if not self.section_cards:
            return None
        
        # Search for exact path or suffix match
        target_path = Path(target_file)
        for card in self.section_cards.sections.values():
            if card.path:
                card_path = Path(card.path)
                if card_path == target_path or target_file.endswith(card.path):
                    return card
        return None

    def _get_prior_usage(self, terms: List[str], exclude_file: str) -> List[TerminologyConstraint]:
        constraints = []
        seen_terms = set()
        
        # Limit to top 5 terms to avoid token bloat
        for term in terms[:5]:
            if term.lower() in seen_terms:
                continue
                
            results = self.adapter.search(term, corpus=self.config.rtfm.corpus, limit=5)
            examples = []
            for r in results:
                if r.path == exclude_file:
                    continue
                if not is_allowed_source(r.path):
                    continue
                if r.snippet:
                    examples.append(r.snippet.strip())
            
            if examples:
                constraints.append(TerminologyConstraint(term=term, usage_examples=examples[:2]))
                seen_terms.add(term.lower())
                
        return constraints

    def _generate_constraints(self, mode: str, strictness: str,
                            section_card: Optional[SectionCard],
                            terminology: List[TerminologyConstraint]) -> ProofreadingConstraints:
        
        general_rules = MODE_CONSTRAINTS.get(mode, MODE_CONSTRAINTS["surface"]).copy()
        general_rules.append(STRICTNESS_RULES.get(strictness, STRICTNESS_RULES["moderate"]))
        
        section_rules = []
        if section_card:
            if section_card.must_preserve:
                section_rules.extend([f"Preserve key claim: {p}" for p in section_card.must_preserve])
            if section_card.avoid:
                section_rules.extend([f"Avoid: {a}" for a in section_card.avoid])
            if section_card.constraints:
                section_rules.extend(section_card.constraints)
                
        return ProofreadingConstraints(
            mode=mode,
            strictness=strictness,
            general_rules=general_rules,
            section_specific_rules=section_rules,
            terminology=terminology
        )
