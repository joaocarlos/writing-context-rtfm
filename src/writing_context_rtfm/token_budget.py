"""Token budgeting and multi-model tokenomics utilities."""

from __future__ import annotations

import re
from typing import Any

# Supported model family identifiers
SUPPORTED_MODEL_FAMILIES = ("openai", "anthropic", "gemini", "generic")

# Session & Rolling Window Limits (Astra, Claude, Gemini rolling constraints)
ASTRA_SESSION_WINDOW_HOURS = 5
ASTRA_EXPENSIVE_TIER_THRESHOLD = 272_000  # 272k tokens threshold where pricing/tiers increase


def _load_encoding() -> Any | None:
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        # tiktoken may be installed while its encoding data is unavailable offline.
        return None


_ENCODING: Any | None = _load_encoding()


def _load_tiktoken_encoding(encoding_name: str) -> Any | None:
    """Attempts to load a local tiktoken encoding without throwing on offline/network errors."""
    try:
        import tiktoken

        return tiktoken.get_encoding(encoding_name)
    except Exception:
        return None


# Lazy/cached encoding instances
_ENCODINGS: dict[str, Any | None] = {}


def _get_encoding(name: str) -> Any | None:
    if name == "cl100k_base" and _ENCODING is not None:
        return _ENCODING
    if name not in _ENCODINGS:
        _ENCODINGS[name] = _load_tiktoken_encoding(name)
    return _ENCODINGS[name]


def _is_latex_or_code_dense(text: str) -> bool:
    """Detects whether text contains high density of LaTeX commands, math environments, or code."""
    latex_patterns = (
        r"\\(?:cite|ref|eqref|label|begin|end|newcommand|def|item|section|subsection)\b",
        r"\$[^$]+\$",
        r"\\\[.*?\\\]",
        r"[{}\\_^#&]",
    )
    matches = sum(len(re.findall(pat, text)) for pat in latex_patterns)
    # If more than 5% of tokens are formatting tokens or matches > 5
    return matches >= 5 or (len(text) > 0 and (matches * 10 / len(text)) > 0.05)


def count_tokens(text: str, model_family: str = "generic") -> int:
    """Accurately count or estimate tokens for a given text across model families.

    Supported model families:
    - 'openai': Targets GPT-4o, GPT-5, GPT-5.6, GPT-6 via o200k_base (or cl100k_base)
      with calibrated BPE fallback (~3.7 char/token in prose, ~3.2 in LaTeX/code).
    - 'anthropic': Targets Claude 3.5, Claude 5, Opus 5 with calibrated Claude BPE
      vocabulary (~3.4 char/token in prose, ~3.0 in LaTeX/code).
    - 'gemini': Targets Gemini 1.5, Gemini 2.0, Gemini 3.8 with calibrated SentencePiece
      256k vocabulary (~3.6 char/token in prose, ~3.2 in LaTeX/code).
    - 'generic': Fallback to cl100k_base or length // 4.
    """
    if not text or not text.strip():
        return 0

    norm_family = (model_family or "generic").lower().strip()
    latex_dense = _is_latex_or_code_dense(text)

    if norm_family in ("openai", "gpt", "gpt5", "gpt6"):
        # Try o200k_base first (GPT-4o/5/6 standard), then cl100k_base
        enc = _get_encoding("o200k_base") or _get_encoding("cl100k_base")
        if enc is not None:
            try:
                return len(enc.encode(text, disallowed_special=()))
            except Exception:
                pass
        # Calibrated offline BPE ratio for OpenAI 200k vocab
        ratio = 3.2 if latex_dense else 3.7
        return max(1, int(round(len(text) / ratio)))

    if norm_family in ("anthropic", "claude", "claude5", "opus", "opus5"):
        # Claude uses ~65k BPE vocabulary with higher token density in technical LaTeX
        ratio = 3.0 if latex_dense else 3.4
        return max(1, int(round(len(text) / ratio)))

    if norm_family in ("gemini", "google", "gemini3.8", "gemini38"):
        # Gemini uses 256k SentencePiece vocabulary
        ratio = 3.2 if latex_dense else 3.6
        return max(1, int(round(len(text) / ratio)))

    # Generic / Default (cl100k_base or len // 4)
    enc = _get_encoding("cl100k_base")
    if enc is not None:
        try:
            return len(enc.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, len(text) // 4)


def estimate_tokens(text: str) -> int:
    """Estimate tokens for a given text.

    Uses tiktoken's cl100k_base encoding if available, falling back to
    the char_count // 4 heuristic otherwise.
    """
    if _ENCODING is not None:
        try:
            return len(_ENCODING.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, len(text) // 4)


def estimate_span_tokens(line_start: int, line_end: int, avg_tokens_per_line: int = 15) -> int:
    """Estimate token count based on line span."""
    lines = max(1, line_end - line_start + 1)
    return lines * avg_tokens_per_line


def preflight_budget_check(
    *,
    budget: int,
    target_text: str = "",
    thesis: str = "",
    macros: dict[str, str] | None = None,
    constraints: list[str] | None = None,
    model_family: str = "generic",
    session_tokens_used: int = 0,
    session_calls_used: int = 0,
    session_message_limit: int = 25,
) -> dict[str, Any]:
    """Evaluates fixed non-negotiable token overhead and 5-hour rolling session limits.

    Prevents wasteful retrieval and reranking when fixed requirements already exceed
    the allocated context budget or when the cumulative session consumption approaches
    the Astra 272K token threshold or the rolling session message limit.
    """
    target_tokens = count_tokens(target_text, model_family) if target_text else 0
    thesis_tokens = count_tokens(thesis, model_family) if thesis else 0

    macros_text = "\n".join(f"{k}: {v}" for k, v in macros.items()) if macros else ""
    macro_tokens = count_tokens(macros_text, model_family) if macros_text else 0

    constraints_text = "\n".join(constraints) if constraints else ""
    constraint_tokens = count_tokens(constraints_text, model_family) if constraints_text else 0

    fixed_tokens = target_tokens + thesis_tokens + macro_tokens + constraint_tokens
    available_elastic = budget - fixed_tokens
    feasible = available_elastic > 0

    # Recommended minimum budget allows at least 600 tokens for dependencies and references
    recommended_min_budget = fixed_tokens + 600

    # 5-hour rolling session constraints (Astra tier)
    if not isinstance(session_tokens_used, (int, float)):
        session_tokens_used = 0
    else:
        session_tokens_used = int(session_tokens_used)

    if not isinstance(session_calls_used, (int, float)):
        session_calls_used = 0
    else:
        session_calls_used = int(session_calls_used)

    if not isinstance(session_message_limit, (int, float)):
        session_message_limit = 25
    else:
        session_message_limit = int(session_message_limit)

    projected_session_tokens = session_tokens_used + budget
    remaining_session_headroom = max(0, ASTRA_EXPENSIVE_TIER_THRESHOLD - session_tokens_used)
    session_tier_warning = projected_session_tokens >= ASTRA_EXPENSIVE_TIER_THRESHOLD
    single_call_tier_warning = budget >= ASTRA_EXPENSIVE_TIER_THRESHOLD

    calls_remaining_by_message_limit = max(0, session_message_limit - session_calls_used)
    calls_remaining_by_token_headroom = (
        int(remaining_session_headroom // max(budget, 2500))
        if remaining_session_headroom > 0
        else 0
    )
    bottleneck_calls_remaining = min(
        calls_remaining_by_message_limit, calls_remaining_by_token_headroom
    )
    bottleneck_cause = (
        "message_limit"
        if calls_remaining_by_message_limit <= calls_remaining_by_token_headroom
        else "token_headroom"
    )

    return {
        "feasible": feasible,
        "total_budget": budget,
        "fixed_tokens": fixed_tokens,
        "available_elastic_tokens": max(0, available_elastic),
        "deficit": abs(available_elastic) if available_elastic < 0 else 0,
        "recommended_min_budget": recommended_min_budget,
        "model_family": model_family,
        "breakdown": {
            "target_text": target_tokens,
            "thesis": thesis_tokens,
            "macros": macro_tokens,
            "constraints": constraint_tokens,
        },
        "session": {
            "session_tokens_used": session_tokens_used,
            "projected_session_tokens": projected_session_tokens,
            "remaining_headroom": remaining_session_headroom,
            "threshold": ASTRA_EXPENSIVE_TIER_THRESHOLD,
            "threshold_exceeded": session_tier_warning,
            "single_call_tier_warning": single_call_tier_warning,
            "session_calls_used": session_calls_used,
            "session_message_limit": session_message_limit,
            "calls_remaining_by_message_limit": calls_remaining_by_message_limit,
            "calls_remaining_by_token_headroom": calls_remaining_by_token_headroom,
            "bottleneck_calls_remaining": bottleneck_calls_remaining,
            "bottleneck_cause": bottleneck_cause,
        },
    }


def compute_counterfactual_savings(
    *,
    baseline_doc_tokens: int,
    pack_tokens: int,
    baseline_realistic_tokens: int | None = None,
    baseline_tokens_raw: int | None = None,
    is_capped: bool = False,
    baseline_mode: str = "section_neighborhood",
    schema_overhead_tokens: int = 420,
) -> dict[str, Any]:
    """Computes dual counterfactual token savings achieved by writing-context-rtfm.

    Compares the compact Context Pack against:
    1. Realistic Baseline (truth): what the user actually pastes without MCP:
       - 'section_neighborhood': Target section + adjacent context (~5k–15k tokens).
       - 'chapter': Full target file / chapter (~20k–50k tokens).
    2. Naive Baseline (theoretical upper bound): reading the full project manuscript.
    """
    effective_pack_cost = pack_tokens + schema_overhead_tokens

    # 1. Realistic baseline
    if baseline_realistic_tokens is None or baseline_realistic_tokens <= 0:
        if baseline_mode == "chapter":
            baseline_realistic_tokens = min(
                baseline_doc_tokens, max(effective_pack_cost * 4, 25000)
            )
        elif baseline_mode == "full_doc":
            baseline_realistic_tokens = baseline_doc_tokens
        else:
            baseline_realistic_tokens = min(baseline_doc_tokens, max(effective_pack_cost * 2, 8000))

    safe_realistic = max(baseline_realistic_tokens, 1)
    safe_raw = baseline_tokens_raw if baseline_tokens_raw is not None else safe_realistic
    realistic_saved = max(0, safe_realistic - effective_pack_cost)
    realistic_ratio = max(0.0, round(float(realistic_saved) / float(safe_realistic), 4))

    # 2. Naive full-manuscript baseline (theoretical ceiling)
    safe_naive = max(baseline_doc_tokens, 1)
    naive_saved = max(0, safe_naive - effective_pack_cost)
    naive_ratio = max(0.0, round(float(naive_saved) / float(safe_naive), 4))

    return {
        "baseline_realistic_tokens": safe_realistic,
        "baseline_tokens_raw": safe_raw,
        "is_capped": is_capped,
        "realistic_tokens_saved": realistic_saved,
        "realistic_savings_ratio": realistic_ratio,
        "realistic_savings_percentage": round(realistic_ratio * 100.0, 2),
        "baseline_document_tokens": safe_naive,
        "tokens_saved": naive_saved,
        "savings_ratio": naive_ratio,
        "savings_percentage": round(naive_ratio * 100.0, 2),
        "baseline_mode": baseline_mode,
        "pack_tokens": pack_tokens,
        "schema_overhead_tokens": schema_overhead_tokens,
        "effective_pack_cost": effective_pack_cost,
    }
