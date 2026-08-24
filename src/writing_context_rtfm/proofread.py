import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.section_cards import (
    SectionCard,
    SectionCards,
    normalize_terminology,
)
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.token_budget import estimate_tokens
from writing_context_rtfm.utils import extract_keywords, is_allowed_source, scan_latex_commands


@dataclass(frozen=True)
class TerminologyConstraint:
    term: str
    usage_examples: list[str]
    definition: str | None = None
    variants: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProofreadingConstraints:
    mode: str
    strictness: str
    general_rules: list[str]
    section_specific_rules: list[str]
    terminology: list[TerminologyConstraint]


@dataclass(frozen=True)
class LocalContext:
    target_span: str
    previous_paragraph: str | None
    next_paragraph: str | None


@dataclass(frozen=True)
class ProofreadingTarget:
    file_path: str
    line_start: int
    line_end: int
    section_id: str | None


@dataclass(frozen=True)
class ProofreadingContextPack:
    target: ProofreadingTarget
    local_context: LocalContext
    constraints: ProofreadingConstraints
    estimated_tokens: int
    guidance: str = ""
    status: str = "complete"
    warnings: list[str] = field(default_factory=list)


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
    ],
}

STRICTNESS_RULES = {
    "conservative": "Make only essential corrections. Preserve original phrasing where possible.",
    "moderate": "Balance original style with necessary improvements for clarity and flow.",
    "assertive": "Boldly rewrite for maximum impact, clarity, and adherence to academic standards.",
}


class ProofreadPackGenerator:
    def __init__(
        self,
        config: AppConfig,
        section_cards: SectionCards | None,
        adapter: RTFMAdapter,
        store: ExtensionStore,
    ):
        self.config = config
        self.section_cards = section_cards
        # Proofreading already has the exact target text. Prior-usage retrieval is optional and
        # must never launch an external worker lifecycle when the local SQLite index is absent.
        self.adapter = (
            RTFMAdapter(project_root=adapter.project_root, allow_cli_fallback=False)
            if isinstance(adapter, RTFMAdapter)
            else adapter
        )
        self.store = store

    def generate(
        self,
        target_file: str,
        line_start: int,
        line_end: int,
        mode: str = "surface",
        strictness: str = "moderate",
        max_tokens: int = 4000,
    ) -> ProofreadingContextPack:
        warnings = []
        lines = []
        # Clamp early to valid 1-indexed range
        if os.path.exists(target_file):
            try:
                with open(target_file, encoding="utf-8") as f:
                    lines = f.readlines()
                num_lines = len(lines)
                if num_lines == 0:
                    line_start = 1
                    line_end = 1
                else:
                    line_start = max(1, min(line_start, num_lines))
                    line_end = max(1, min(line_end, num_lines))
                    if line_start > line_end:
                        line_start, line_end = line_end, line_start
            except Exception as e:
                warnings.append(f"Failed to read file bounds: {e}")

        # 1. Load target span and local context
        local_ctx = self._get_local_context(target_file, line_start, line_end, lines=lines)

        # 2. Find section card
        section_card = self._find_section_card(target_file)
        target_info = ProofreadingTarget(
            file_path=target_file,
            line_start=line_start,
            line_end=line_end,
            section_id=section_card.id if section_card else None,
        )

        # 3. Extract key terms and get prior usage
        key_terms = extract_keywords(local_ctx.target_span)
        card_terminology = self._get_card_terminology(local_ctx.target_span)
        search_terms = [constraint.term for constraint in card_terminology]
        search_terms.extend(key_terms)
        prior_usage = self._get_prior_usage(search_terms, target_file, warnings)
        terminology = self._combine_terminology(card_terminology, prior_usage)

        # LaTeX safety layer scanning
        latex_commands = scan_latex_commands(local_ctx.target_span)
        if latex_commands:
            warnings.append(
                "LaTeX Safety: The following LaTeX commands or math environments were detected in the target text "
                f"and must not be modified or deleted: {', '.join(latex_commands)}"
            )

        # 4. Generate constraints
        constraints = self._generate_constraints(
            mode, strictness, section_card, terminology, latex_commands=latex_commands
        )

        # 5. Assemble and estimate tokens
        total_text = (
            local_ctx.target_span
            + (local_ctx.previous_paragraph or "")
            + (local_ctx.next_paragraph or "")
        )
        est = estimate_tokens(total_text) + estimate_tokens(json.dumps(asdict(constraints)))

        if est > max_tokens:
            warnings.append(
                f"Note: Estimated tokens ({est}) exceeds the requested max_tokens ({max_tokens}). "
                f"The full context was preserved to prevent context bloat."
            )

        guidance = (
            f"Target: {target_file} (lines {line_start}-{line_end}). "
            f"Mode: '{mode}', Strictness: '{strictness}'. "
            f"Follow general rules and section constraints strictly. "
            f"Preserve immutable LaTeX commands: {', '.join(latex_commands)}."
            if latex_commands
            else f"Target: {target_file} (lines {line_start}-{line_end}). Mode: '{mode}', Strictness: '{strictness}'."
        )

        return ProofreadingContextPack(
            target=target_info,
            local_context=local_ctx,
            constraints=constraints,
            estimated_tokens=est,
            guidance=guidance,
            status="complete",
            warnings=warnings,
        )

    def _get_local_context(
        self, file_path: str, start: int, end: int, lines: list[str] | None = None
    ) -> LocalContext:
        if lines is None:
            if not os.path.exists(file_path):
                return LocalContext(f"[File not found: {file_path}]", None, None)

            try:
                with open(file_path, encoding="utf-8") as f:
                    lines = f.readlines()
            except Exception as e:
                return LocalContext(f"[Failed to read file: {e}]", None, None)

        num_lines = len(lines)
        if num_lines == 0:
            return LocalContext("", None, None)

        # Clamp inputs to valid 1-indexed range to prevent negative slicing bugs
        start = max(1, min(start, num_lines))
        end = max(1, min(end, num_lines))
        if start > end:
            start, end = end, start

        # Target span
        target_lines = lines[start - 1 : end]
        target_span = "".join(target_lines)

        # Previous paragraph
        prev_lines = lines[: start - 1]
        prev_para: list[str] = []
        for line in reversed(prev_lines):
            if not line.strip() and prev_para:
                break
            prev_para.append(line)
        previous_paragraph = "".join(reversed(prev_para)).strip() if prev_para else None

        # Next paragraph
        next_lines = lines[end:]
        next_para: list[str] = []
        for line in next_lines:
            if not line.strip() and next_para:
                break
            next_para.append(line)
        next_paragraph = "".join(next_para).strip() if next_para else None

        return LocalContext(target_span, previous_paragraph, next_paragraph)

    def _find_section_card(self, target_file: str) -> SectionCard | None:
        if not self.section_cards:
            return None

        # Normalize and resolve target path to absolute
        target_abs = Path(target_file).resolve()
        for card in self.section_cards.sections.values():
            if card.path:
                # Resolve card path relative to the project root
                card_abs = Path(self.config.rtfm.project_root).joinpath(card.path).resolve()
                if card_abs == target_abs:
                    return card
        return None

    def _get_prior_usage(
        self, terms: list[str], exclude_file: str, warnings: list[str]
    ) -> list[TerminologyConstraint]:
        constraints = []
        seen_terms = set()
        exclude_abs = Path(exclude_file).resolve()

        # Limit to top 5 terms to avoid token bloat
        for term in terms[:5]:
            if term.lower() in seen_terms:
                continue

            # Gracefully handle search failures (e.g. index not synced)
            try:
                results = self.adapter.search(term, corpus=self.config.rtfm.corpus, limit=5)
            except Exception as e:
                logging.getLogger("proofread").warning(
                    f"Failed to search for prior usage of '{term}': {e}"
                )
                warnings.append(f"Failed to search for prior usage of '{term}': {e}")
                continue

            examples = []
            for r in results:
                # Resolve RTFM path to absolute for robust comparison
                r_abs = Path(self.config.rtfm.project_root).joinpath(r.path).resolve()
                if r_abs == exclude_abs:
                    continue
                if not is_allowed_source(r.path):
                    continue
                if r.snippet:
                    examples.append(r.snippet.strip())

            if examples:
                constraints.append(TerminologyConstraint(term=term, usage_examples=examples[:2]))
                seen_terms.add(term.lower())

        return constraints

    @staticmethod
    def _term_occurs(text: str, term: str) -> bool:
        if not term:
            return False
        pattern = rf"(?<!\w){re.escape(term)}(?!\w)"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None

    def _get_card_terminology(self, target_text: str) -> list[TerminologyConstraint]:
        if not self.section_cards or not self.section_cards.document.terminology:
            return []

        constraints: list[TerminologyConstraint] = []
        glossary = normalize_terminology(self.section_cards.document.terminology)
        for canonical, details in glossary.items():
            forms = [canonical, *details["variants"], *details["avoid"]]
            if not any(self._term_occurs(target_text, form) for form in forms):
                continue
            constraints.append(
                TerminologyConstraint(
                    term=canonical,
                    usage_examples=[],
                    definition=details["definition"] or None,
                    variants=list(details["variants"]),
                    avoid=list(details["avoid"]),
                )
            )
            if len(constraints) >= 5:
                break
        return constraints

    @staticmethod
    def _combine_terminology(
        card_constraints: list[TerminologyConstraint],
        prior_constraints: list[TerminologyConstraint],
    ) -> list[TerminologyConstraint]:
        combined: list[TerminologyConstraint] = []
        consumed_prior: set[int] = set()
        for card_constraint in card_constraints:
            forms = {
                value.casefold()
                for value in [
                    card_constraint.term,
                    *card_constraint.variants,
                    *card_constraint.avoid,
                ]
            }
            examples: list[str] = []
            for index, prior in enumerate(prior_constraints):
                if prior.term.casefold() in forms:
                    examples.extend(prior.usage_examples)
                    consumed_prior.add(index)
            combined.append(
                TerminologyConstraint(
                    term=card_constraint.term,
                    usage_examples=list(dict.fromkeys(examples))[:2],
                    definition=card_constraint.definition,
                    variants=card_constraint.variants,
                    avoid=card_constraint.avoid,
                )
            )

        for index, prior in enumerate(prior_constraints):
            if index not in consumed_prior:
                combined.append(prior)
            if len(combined) >= 5:
                break
        return combined[:5]

    def _generate_constraints(
        self,
        mode: str,
        strictness: str,
        section_card: SectionCard | None,
        terminology: list[TerminologyConstraint],
        latex_commands: list[str] | None = None,
    ) -> ProofreadingConstraints:

        general_rules = MODE_CONSTRAINTS.get(mode, MODE_CONSTRAINTS["surface"]).copy()
        general_rules.append(STRICTNESS_RULES.get(strictness, STRICTNESS_RULES["moderate"]))

        section_rules = []
        if section_card:
            if section_card.must_preserve:
                section_rules.extend(
                    [f"Preserve key claim: {p}" for p in section_card.must_preserve]
                )
            if section_card.avoid:
                section_rules.extend([f"Avoid: {a}" for a in section_card.avoid])
            if section_card.constraints:
                section_rules.extend(section_card.constraints)

        if latex_commands:
            section_rules.append(
                f"Immutable LaTeX commands/math environments in target: {', '.join(latex_commands)}"
            )

        return ProofreadingConstraints(
            mode=mode,
            strictness=strictness,
            general_rules=general_rules,
            section_specific_rules=section_rules,
            terminology=terminology,
        )
