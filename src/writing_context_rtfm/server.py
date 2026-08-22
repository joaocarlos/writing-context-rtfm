"""MCP server logic."""

import contextlib
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from writing_context_rtfm import __version__
from writing_context_rtfm.config import AppConfig, load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.features import (
    audit_manuscript_terminology,
    get_term_context,
    initialize_section_cards,
)
from writing_context_rtfm.hashing import compute_rtfm_fingerprint
from writing_context_rtfm.latex import build_reference_graph
from writing_context_rtfm.proofread import ProofreadPackGenerator
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.section_cards import (
    SectionCards,
    load_section_cards,
    validate_section_cards,
)
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.utils import resolve_rtfm_db_path

_client_manager = None

# --- Prompt formatters ------------------------------------------------------


def _format_write_section_prompt(pack: Any) -> str:
    source_spans_txt = []
    for s in pack.source_spans:
        snippet = (s.metadata or {}).get("snippet") or ""
        source_spans_txt.append(
            f"--- File: {s.path} (Lines {s.line_start}-{s.line_end}) [{s.priority}]\n"
            f"Reason: {s.reason}\n"
            f"Content:\n{snippet}\n"
        )
    source_spans_joined = "\n".join(source_spans_txt)
    constraints_joined = (
        "\n".join(f"- {c}" for c in pack.constraints) if pack.constraints else "None"
    )

    return (
        f"You are writing/editing a manuscript section. Follow the task instructions below and stay aligned with the manuscript's thesis and constraints.\n\n"
        f"Task: {pack.task}\n"
        f"Target Section: {pack.target or 'Unknown'}\n\n"
        f"[Manuscript Thesis]:\n{pack.document_thesis or 'None'}\n\n"
        f"[Constraints & Rules]:\n{constraints_joined}\n\n"
        f"[Prior Source Spans (Surgical Context)]:\n{source_spans_joined}\n\n"
        f"Instruction: Draft or revise the section based strictly on the provided context spans and constraints above. Maintain academic tone and LaTeX/Markdown formatting consistency."
    )


def _format_proofread_section_prompt(pack: Any) -> str:
    constraints = getattr(pack, "constraints", None)
    mode = "surface"
    strictness = "moderate"
    general_rules: list[str] = []
    section_specific_rules: list[str] = []
    terminology: list[Any] = []

    if isinstance(constraints, dict):
        mode = constraints.get("mode", "surface")
        strictness = constraints.get("strictness", "moderate")
        general_rules = constraints.get("general_rules") or []
        section_specific_rules = constraints.get("section_specific_rules") or []
        terminology = constraints.get("terminology") or []
    elif hasattr(constraints, "mode"):
        mode = getattr(constraints, "mode", "surface")
        strictness = getattr(constraints, "strictness", "moderate")
        general_rules = getattr(constraints, "general_rules", []) or []
        section_specific_rules = getattr(constraints, "section_specific_rules", []) or []
        terminology = getattr(constraints, "terminology", []) or []
    elif isinstance(constraints, list):
        section_specific_rules = [str(c) for c in constraints]

    terminology_txt = []
    for t in terminology or []:
        if isinstance(t, dict):
            term = t.get("term", "")
            examples = t.get("usage_examples", [])
        elif hasattr(t, "term"):
            term = getattr(t, "term", "")
            examples = getattr(t, "usage_examples", [])
        else:
            term = str(t)
            examples = []
        examples_str = "; ".join(f"'{ex}'" for ex in examples) if examples else "None"
        terminology_txt.append(f"- '{term}': used in: {examples_str}")
    terminology_joined = "\n".join(terminology_txt) if terminology_txt else "None"

    constraints_joined = (
        "\n".join(f"- {c}" for c in section_specific_rules) if section_specific_rules else "None"
    )
    general_joined = "\n".join(f"- {c}" for c in general_rules) if general_rules else "None"

    local_ctx = getattr(pack, "local_context", None)
    local_txt = ""
    if local_ctx:
        prev_para = (
            getattr(local_ctx, "previous_paragraph", None)
            if not isinstance(local_ctx, dict)
            else local_ctx.get("previous_paragraph")
        )
        target_span = (
            getattr(local_ctx, "target_span", "")
            if not isinstance(local_ctx, dict)
            else local_ctx.get("target_span", "")
        )
        next_para = (
            getattr(local_ctx, "next_paragraph", None)
            if not isinstance(local_ctx, dict)
            else local_ctx.get("next_paragraph")
        )
        if prev_para:
            local_txt += f"[Previous Context Paragraph]:\n{prev_para}\n\n"
        if target_span:
            local_txt += f"[Target Text to Revise]:\n{target_span}\n\n"
        if next_para:
            local_txt += f"[Next Context Paragraph]:\n{next_para}\n\n"

    target = getattr(pack, "target", None)
    file_path: str = "Unknown"
    line_start = "?"
    line_end = "?"
    if target:
        if isinstance(target, dict):
            file_path = str(target.get("file_path", target.get("file", "Unknown")))
            line_start = target.get("line_start", "?")
            line_end = target.get("line_end", "?")
        elif hasattr(target, "file_path"):
            file_path = str(getattr(target, "file_path", "Unknown"))
            line_start = getattr(target, "line_start", "?")
            line_end = getattr(target, "line_end", "?")
        else:
            file_path = str(target)

    return (
        f"You are proofreading and refining the following segment of the manuscript.\n\n"
        f"Target file: {file_path} (Lines {line_start}-{line_end})\n"
        f"Mode: {mode} | Strictness: {strictness}\n\n"
        f"[Local Context surrounding Target]:\n{local_txt}"
        f"[General Rules for {mode}]:\n{general_joined}\n\n"
        f"[Section Constraints]:\n{constraints_joined}\n\n"
        f"[Terminology Usage Examples (Prior Context)]:\n{terminology_joined}\n\n"
        f"Instruction: Revise the target segment strictly following the mode and constraints above. Maintain terminology consistency as shown in the examples."
    )


# Log path can be overridden via WRITING_CONTEXT_LOG; falls back to a user-local
# directory when /tmp is not writable (e.g., locked-down systems).
_default_log = os.path.join(
    os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
    "writing-context-rtfm",
    "server.log",
)
log_path = os.environ.get("WRITING_CONTEXT_LOG", _default_log)
try:
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
except OSError:
    logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("mcp-server")

WORKSPACE_ROOT = Path(".").resolve()

# --- Error helpers ----------------------------------------------------------

ERROR_INVALID_INPUT = "invalid_input"
ERROR_CONFIG = "config_error"
ERROR_RETRIEVAL = "retrieval_failed"
ERROR_INTERNAL = "internal_error"


def _error_response(code: str, message: str, detail: str | None = None) -> dict[str, Any]:
    """Return a structured MCP tool error.

    The full traceback (if any) is logged but never returned to the client to
    avoid leaking internals.
    """
    payload = {"error_code": code, "message": message}
    if detail:
        payload["detail"] = detail
    return {
        "isError": True,
        "content": [{"type": "text", "text": json.dumps(payload)}],
    }


def _success_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def _sanitize_span_for_output(span_dict: dict[str, Any]) -> dict[str, Any]:
    """Strip internal-only fields (e.g., raw snippet metadata) from a serialized SourceSpan.

    The `metadata` dict is used internally for token estimation and avoid-filtering
    but its contents already appear in `reason`, so emitting it doubles bytes.
    """
    out = dict(span_dict)
    out.pop("metadata", None)
    return out


def _sanitize_pack_for_output(pack_dict: dict[str, Any]) -> dict[str, Any]:
    spans = pack_dict.get("source_spans") or []
    pack_dict["source_spans"] = [_sanitize_span_for_output(s) for s in spans]
    return pack_dict


# --- Tool catalog -----------------------------------------------------------


def get_tools_list() -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": "get_writing_context_pack",
                "description": (
                    "Generate a compact, prioritized writing context pack for a specific writing task. "
                    "Use this BEFORE drafting, rewriting, or expanding any section of the manuscript. "
                    "Returns the document thesis, hard constraints from the section card, a ranked "
                    "list of source spans (each tagged essential | supporting | background), a pre-rendered "
                    "'formatted_prompt', and an execution 'guidance' string. Prefer this over reading the manuscript directly: "
                    "the pack is scoped to the task, deduplicated, and stays within token budgets. "
                    "Output shape: {task, target, document_thesis, prior_claims, terminology, constraints, "
                    "source_spans[], estimated_tokens, formatted_prompt, guidance, status ('complete' | 'degraded'), warnings[]}. "
                    "When status='degraded', inspect warnings before proceeding."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": (
                                "Natural-language description of the writing task (e.g., 'Write the "
                                "methodology section covering the MIMII dataset and quantization choices'). "
                                "Used as the primary retrieval query and seeds keyword extraction."
                            ),
                        },
                        "target": {
                            "type": "string",
                            "description": (
                                "section_id from .writing-context/section_cards.yaml (e.g., 'section_methodology'). "
                                "Activates: target-file score boost, dependency-section expansion, key-term scoping, "
                                "must_preserve/avoid constraints. Omitting this disables section-card-driven "
                                "expansion and the pack will have status='degraded'."
                            ),
                        },
                        "token_budget": {
                            "type": "integer",
                            "description": (
                                "Soft cap on tokens spent on source_spans. The generator reserves ~10% for "
                                "downstream generation and stops adding spans once the cap is hit. Defaults "
                                "to the value in config.yaml (typically 6000). Recommended: 800–2000 for "
                                "focused tasks, 3000–6000 for broad rewrites."
                            ),
                        },
                        "must_consider": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Substrings matched case-insensitively against retrieved paths. Any span "
                                "whose path contains a listed substring gets a +1.0 score boost. Use this "
                                "to force inclusion of specific files when the section card doesn't cover them."
                            ),
                        },
                        "task_type": {
                            "type": "string",
                            "enum": [
                                "write_new_section",
                                "revise_existing_section",
                                "proofread",
                                "expand",
                                "condense",
                                "align_with_previous_sections",
                                "review",
                            ],
                            "description": "The specific type of writing task.",
                        },
                        "line_start": {
                            "type": "integer",
                            "description": "Optional starting line range in the target file.",
                        },
                        "line_end": {
                            "type": "integer",
                            "description": "Optional ending line range in the target file.",
                        },
                        "pack_mode": {
                            "type": "string",
                            "enum": ["minimal", "standard", "deep"],
                            "description": "Override context pack depth level/budget.",
                        },
                        "role_budgets": {
                            "type": "object",
                            "description": (
                                "Optional dictionary overriding default budget allocations. Keys must be "
                                "source roles (target_text, local_context, dependency, reference) and values "
                                "must be float fractions summing to 1.0 (e.g. {'target_text': 0.40, 'reference': 0.10})."
                            ),
                            "additionalProperties": {"type": "number"},
                        },
                    },
                    "required": ["task"],
                },
            },
            {
                "name": "get_proofreading_context_pack",
                "description": (
                    "Generate a context pack for proofreading or editing a SPECIFIC line range of a "
                    "manuscript file. Use this instead of get_writing_context_pack when the task is "
                    "to refine existing text rather than write new text. Returns the target span, "
                    "surrounding paragraphs (previous + next co-text), mode/strictness rules, section "
                    "constraints, prior term usage examples, a pre-rendered 'formatted_prompt', and "
                    "an execution 'guidance' string. "
                    "Output shape: {target, local_context, constraints{mode, strictness, general_rules, "
                    "section_specific_rules, terminology[]}, estimated_tokens, formatted_prompt, guidance, status}."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_file": {
                            "type": "string",
                            "description": "Absolute or workspace-relative path to the file being proofread.",
                        },
                        "line_start": {
                            "type": "integer",
                            "description": "1-indexed first line of the span to proofread (inclusive).",
                        },
                        "line_end": {
                            "type": "integer",
                            "description": "1-indexed last line of the span to proofread (inclusive).",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["surface", "academic_clarity", "consistency", "latex_safe"],
                            "default": "surface",
                            "description": (
                                "Editing mode (auto-select based on task intent):\n"
                                "  surface — grammar, spelling, punctuation only; preserve structure and tone.\n"
                                "  academic_clarity — sharpen precision and formal vocabulary; improve logical flow.\n"
                                "  consistency — enforce terminology and formatting consistency across the manuscript.\n"
                                "  latex_safe — default when LaTeX commands, \\cite, \\ref, \\label or math environments exist."
                            ),
                        },
                        "strictness": {
                            "type": "string",
                            "enum": ["conservative", "moderate", "assertive"],
                            "default": "moderate",
                            "description": (
                                "How aggressively to rewrite:\n"
                                "  conservative — only essential corrections; preserve phrasing.\n"
                                "  moderate — balance original style with clarity improvements.\n"
                                "  assertive — rewrite boldly for maximum impact and academic polish."
                            ),
                        },
                        "max_tokens": {
                            "type": "integer",
                            "default": 4000,
                            "description": (
                                "Upper bound on tokens in the assembled pack. If local context plus "
                                "constraints exceeds this, status becomes 'degraded'."
                            ),
                        },
                    },
                    "required": ["target_file", "line_start", "line_end"],
                },
            },
            {
                "name": "refresh_index",
                "description": (
                    "Re-sync the RTFM index against the manuscript files and invalidate cached context "
                    "packs. Call this after meaningful edits to manuscript content so subsequent "
                    "get_writing_context_pack / get_proofreading_context_pack calls reflect the new state. "
                    "Returns {status: 'ok', cache_invalidated: bool} on success."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_root": {
                            "type": "string",
                            "description": "Path to the project root. Defaults to the configured project root.",
                        },
                        "corpus": {
                            "type": "string",
                            "description": "RTFM corpus name to refresh. Defaults to the configured corpus (usually 'manuscript').",
                        },
                    },
                },
            },
            {
                "name": "initialize_section_cards",
                "description": (
                    "Scan the workspace for .tex and .md files and generate or append missing section cards "
                    "to section_cards.yaml. Proposes section IDs, file paths, and default constraints."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_root": {
                            "type": "string",
                            "description": "Custom project root path (optional). Defaults to current workspace.",
                        }
                    },
                },
            },
            {
                "name": "request_more_context",
                "description": (
                    "Retrieve the next page/tier of unselected supporting/background context spans from a previous "
                    "context generation run. Useful when initial token budgets were too restrictive."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "The unique UUID run_id returned from a prior get_writing_context_pack call.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of extra context spans to fetch (default: 5).",
                        },
                    },
                    "required": ["run_id"],
                },
            },
            {
                "name": "submit_generation_feedback",
                "description": (
                    "Log quality evaluation feedback for a context pack generation run to improve subsequent context selection."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "run_id": {
                            "type": "string",
                            "description": "The unique UUID run_id from the context pack.",
                        },
                        "metric_name": {
                            "type": "string",
                            "description": "Metric category being logged (e.g. helpfulness, hallucinations, constraint_violated).",
                        },
                        "metric_value": {
                            "type": "number",
                            "description": "Numeric evaluation value (e.g. 1.0 for positive/present, 0.0 for negative/absent).",
                        },
                        "metric_text": {
                            "type": "string",
                            "description": "Optional text details or description of issue/helpfulness.",
                        },
                    },
                    "required": ["run_id", "metric_name", "metric_value"],
                },
            },
            {
                "name": "audit_manuscript_terminology",
                "description": (
                    "Scan all section cards key terms and run searches against the RTFM index to analyze usage "
                    "patterns, flagging undeclared usages, missing terms, and potential semantic drift."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_root": {
                            "type": "string",
                            "description": "Custom project root path (optional). Defaults to current workspace.",
                        }
                    },
                },
            },
            {
                "name": "get_term_context",
                "description": (
                    "Look up a term in the terminology glossary defined in section_cards.yaml. "
                    "Returns the term definition, allowed variants, and phrases to avoid."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "term": {
                            "type": "string",
                            "description": "The term (canonical, variant, or avoid phrase) to look up.",
                        },
                        "project_root": {
                            "type": "string",
                            "description": "Optional project root path.",
                        },
                    },
                    "required": ["term"],
                },
            },
            {
                "name": "get_manuscript_reference_graph",
                "description": (
                    "Build and return the LaTeX cross-reference and dependency graph of the manuscript. "
                    "This details defined labels, references to those labels, citations, and file inclusions."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_root": {
                            "type": "string",
                            "description": "Optional custom project root path. Defaults to the workspace root.",
                        }
                    },
                },
            },
            {
                "name": "review_card_candidates",
                "description": "List all pending section card candidates from cards.generated.yaml with status 'generated'.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_root": {
                            "type": "string",
                            "description": "Optional project root path. Defaults to current workspace.",
                        }
                    },
                },
            },
            {
                "name": "accept_card_candidate",
                "description": "Approve a candidate field value for a section. Writes the approved value to cards.overrides.yaml and updates the status to 'accepted' in lock.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "section_id": {
                            "type": "string",
                            "description": "The target section ID (e.g. 'section_introduction').",
                        },
                        "field": {
                            "type": "string",
                            "enum": ["purpose", "key_terms", "facts", "constraints"],
                            "description": "The field of the candidate to accept.",
                        },
                        "value": {
                            "type": "string",
                            "description": "The specific candidate value to accept. For list fields, specifies the item value.",
                        },
                        "project_root": {
                            "type": "string",
                            "description": "Optional project root path. Defaults to current workspace.",
                        },
                    },
                    "required": ["section_id", "field", "value"],
                },
            },
            {
                "name": "reject_card_candidate",
                "description": "Reject a candidate field value for a section. Marks the candidate status as 'rejected' in cards.lock.json and cards.generated.yaml.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "section_id": {"type": "string", "description": "The target section ID."},
                        "field": {
                            "type": "string",
                            "enum": ["purpose", "key_terms", "facts", "constraints"],
                            "description": "The field of the candidate to reject.",
                        },
                        "value": {
                            "type": "string",
                            "description": "The specific candidate value to reject. For list fields, specifies the item value.",
                        },
                        "project_root": {
                            "type": "string",
                            "description": "Optional project root path. Defaults to current workspace.",
                        },
                    },
                    "required": ["section_id", "field", "value"],
                },
            },
            {
                "name": "edit_card_field",
                "description": "Directly modify or set a field value in cards.overrides.yaml for a specific section.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "section_id": {"type": "string", "description": "The target section ID."},
                        "field": {
                            "type": "string",
                            "enum": [
                                "purpose",
                                "role",
                                "key_terms",
                                "depends_on",
                                "must_preserve",
                                "avoid",
                                "constraints",
                                "path",
                                "title",
                            ],
                            "description": "The overrides field to update.",
                        },
                        "value": {
                            "description": "The new value to assign to the overrides field. Can be a string or a list of strings depending on the field.",
                            "anyOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                                {"type": "null"},
                            ],
                        },
                        "project_root": {
                            "type": "string",
                            "description": "Optional project root path. Defaults to current workspace.",
                        },
                    },
                    "required": ["section_id", "field", "value"],
                },
            },
            {
                "name": "explain_card_candidate",
                "description": "Provide extraction details/evidence/provenance for a specific card candidate.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "section_id": {"type": "string", "description": "The target section ID."},
                        "field": {
                            "type": "string",
                            "enum": ["purpose", "key_terms", "facts", "constraints"],
                            "description": "The candidate field.",
                        },
                        "value": {
                            "type": "string",
                            "description": "The specific candidate value to explain.",
                        },
                        "project_root": {
                            "type": "string",
                            "description": "Optional project root path. Defaults to current workspace.",
                        },
                    },
                    "required": ["section_id", "field", "value"],
                },
            },
        ]
    }


_RUNTIME_CACHE = None


def _load_runtime() -> tuple[
    AppConfig, SectionCards | None, list[str], RTFMAdapter, ExtensionStore
]:
    """Load config, section cards, adapter, store. Returns (config, cards, card_warnings, adapter, store).

    Section-card validation issues are returned as warnings (not exceptions) so
    they surface in the pack's `warnings` field instead of aborting the call.
    """
    global _RUNTIME_CACHE

    config_path = WORKSPACE_ROOT / ".writing-context" / "config.yaml"

    config_mtime = None
    config_size = None
    if config_path.exists():
        try:
            stat = config_path.stat()
            config_mtime = stat.st_mtime
            config_size = stat.st_size
        except OSError:
            pass

    sc_path = WORKSPACE_ROOT / ".writing-context" / "section_cards.yaml"
    if config_path.exists():
        if _RUNTIME_CACHE is not None:
            sc_path = Path(_RUNTIME_CACHE["config"].section_cards.path)
        else:
            try:
                temp_config = load_config(str(WORKSPACE_ROOT))
                sc_path = Path(temp_config.section_cards.path)
            except Exception:
                pass

    sc_mtime = None
    sc_size = None
    if sc_path.exists():
        try:
            stat = sc_path.stat()
            sc_mtime = stat.st_mtime
            sc_size = stat.st_size
        except OSError:
            pass

    if (
        _RUNTIME_CACHE is not None
        and _RUNTIME_CACHE["config_mtime"] == config_mtime
        and _RUNTIME_CACHE["config_size"] == config_size
        and _RUNTIME_CACHE["sc_mtime"] == sc_mtime
        and _RUNTIME_CACHE["sc_size"] == sc_size
    ):
        return (
            _RUNTIME_CACHE["config"],
            _RUNTIME_CACHE["cards"],
            _RUNTIME_CACHE["card_warnings"],
            _RUNTIME_CACHE["adapter"],
            _RUNTIME_CACHE["store"],
        )

    config = load_config(str(WORKSPACE_ROOT))
    cards = load_section_cards(config.section_cards.path, required=config.section_cards.required)
    card_warnings = validate_section_cards(cards) if cards else []
    adapter = RTFMAdapter(project_root=str(config.rtfm.project_root))
    store = ExtensionStore(config.cache.path)
    store.init_db()

    if config.rtfm.sync_before_pack:
        try:
            adapter.sync()
            if config.cache.invalidate_on_refresh:
                rtfm_db = resolve_rtfm_db_path(Path(config.rtfm.project_root))
                fingerprint = compute_rtfm_fingerprint(rtfm_db)
                store.init_db()
                store.invalidate_for_fingerprint(fingerprint)
        except Exception as e:
            logger.warning(f"Auto-sync failed before pack generation: {e}")
            card_warnings.append(f"Auto-sync failed: {e}")

    try:
        final_sc_path = Path(config.section_cards.path)
        if final_sc_path.exists():
            stat = final_sc_path.stat()
            sc_mtime = stat.st_mtime
            sc_size = stat.st_size
    except OSError:
        pass

    _RUNTIME_CACHE = {
        "config_mtime": config_mtime,
        "config_size": config_size,
        "sc_mtime": sc_mtime,
        "sc_size": sc_size,
        "config": config,
        "cards": cards,
        "card_warnings": card_warnings,
        "adapter": adapter,
        "store": store,
    }

    return config, cards, card_warnings, adapter, store


def handle_get_writing_context_pack(args: dict[str, Any]) -> dict[str, Any]:
    if not args or "task" not in args:
        return _error_response(ERROR_INVALID_INPUT, "Missing required argument: task")

    try:
        config, cards, card_warnings, adapter, store = _load_runtime()
    except Exception as e:
        logger.exception("Failed to load runtime for get_writing_context_pack")
        return _error_response(
            ERROR_CONFIG, "Failed to load configuration or section cards.", str(e)
        )

    from writing_context_rtfm.providers import get_active_providers

    providers = get_active_providers(config)
    generator = ContextPackGenerator(config, cards, adapter, store, providers=providers)
    task = args.get("task", "")
    target = args.get("target")
    budget = args.get("token_budget", config.context.default_token_budget)
    must_consider = args.get("must_consider", []) or []
    task_type = args.get("task_type")
    line_start_val = args.get("line_start")
    line_end_val = args.get("line_end")
    pack_mode = args.get("pack_mode")
    role_budgets = args.get("role_budgets")

    line_start = int(line_start_val) if line_start_val is not None else None
    line_end = int(line_end_val) if line_end_val is not None else None

    try:
        pack = generator.generate(
            task=task,
            target=target,
            token_budget=budget,
            must_consider=must_consider,
            task_type=task_type,
            line_start=line_start,
            line_end=line_end,
            pack_mode=pack_mode,
            role_budgets=role_budgets,
        )
    except Exception as e:
        logger.exception("Pack generation failed")
        return _error_response(ERROR_RETRIEVAL, "Context pack generation failed.", type(e).__name__)

    payload = asdict(pack)
    payload["formatted_prompt"] = _format_write_section_prompt(pack)
    payload["guidance"] = (
        f"Writing Context Pack generated for task: '{task}'. "
        f"Target section: '{target or 'General'}'. "
        f"Use formatted_prompt or source_spans to draft/revise content aligned with section constraints."
    )
    all_warnings = list(pack.warnings or [])
    if card_warnings:
        all_warnings.extend(card_warnings)
    if all_warnings:
        payload["warnings"] = all_warnings
        has_degrading_warning = any(not w.startswith("LaTeX Safety:") for w in all_warnings)
        if has_degrading_warning and payload.get("status") == "complete":
            payload["status"] = "degraded"
    return _success_response(_sanitize_pack_for_output(payload))


def handle_get_proofreading_context_pack(args: dict[str, Any]) -> dict[str, Any]:
    missing = [k for k in ("target_file", "line_start", "line_end") if not args or k not in args]
    if missing:
        return _error_response(
            ERROR_INVALID_INPUT,
            f"Missing required arguments: {', '.join(missing)}",
        )

    try:
        config, cards, card_warnings, adapter, store = _load_runtime()
    except Exception as e:
        logger.exception("Failed to load runtime for get_proofreading_context_pack")
        return _error_response(
            ERROR_CONFIG, "Failed to load configuration or section cards.", str(e)
        )

    generator = ProofreadPackGenerator(config, cards, adapter, store)
    target_file = str(args.get("target_file", ""))
    line_start = (
        int(args["line_start"]) if "line_start" in args and args["line_start"] is not None else 1
    )
    line_end = int(args["line_end"]) if "line_end" in args and args["line_end"] is not None else 100
    mode = str(args.get("mode", "surface"))
    strictness = str(args.get("strictness", "moderate"))
    max_tokens = int(args.get("max_tokens", 4000))
    try:
        pack = generator.generate(
            target_file=target_file,
            line_start=line_start,
            line_end=line_end,
            mode=mode,
            strictness=strictness,
            max_tokens=max_tokens,
        )
    except Exception as e:
        logger.exception("Proofreading pack generation failed")
        return _error_response(
            ERROR_RETRIEVAL, "Proofreading pack generation failed.", type(e).__name__
        )

    payload = asdict(pack)
    payload["formatted_prompt"] = _format_proofread_section_prompt(pack)
    if not payload.get("guidance"):
        payload["guidance"] = (
            f"Proofreading Context Pack generated for '{args.get('target_file')}' (lines {args.get('line_start')}-{args.get('line_end')}). "
            f"Mode: '{args.get('mode', 'surface')}', Strictness: '{args.get('strictness', 'moderate')}'. "
            f"Use formatted_prompt to execute inline revisions with exact line replacements."
        )
    all_warnings = list(pack.warnings or [])
    if card_warnings:
        all_warnings.extend(card_warnings)
    if all_warnings:
        payload["warnings"] = all_warnings
        has_degrading_warning = any(not w.startswith("LaTeX Safety:") for w in all_warnings)
        if has_degrading_warning and payload.get("status") == "complete":
            payload["status"] = "degraded"
    return _success_response(payload)


def handle_refresh_index(args: dict[str, Any]) -> dict[str, Any]:
    try:
        config = load_config(str(WORKSPACE_ROOT))
    except Exception as e:
        logger.exception("Failed to load config for refresh_index")
        return _error_response(ERROR_CONFIG, "Failed to load configuration.", str(e))

    project_root = args.get("project_root", config.rtfm.project_root)

    adapter = RTFMAdapter(project_root=project_root)

    sync_path = args.get("project_root")
    sync_corpus = args.get("corpus")

    try:
        adapter.sync(sync_path, corpus=sync_corpus)
    except Exception as e:
        logger.exception("RTFM sync raised")
        return _error_response(ERROR_RETRIEVAL, f"RTFM sync failed: {e}", type(e).__name__)

    if config.cache.invalidate_on_refresh:
        store = ExtensionStore(config.cache.path)
        store.init_db()
        rtfm_db = resolve_rtfm_db_path(Path(project_root))
        fingerprint = compute_rtfm_fingerprint(rtfm_db)
        store.invalidate_for_fingerprint(fingerprint)
    return _success_response(
        {"status": "ok", "cache_invalidated": config.cache.invalidate_on_refresh}
    )


def handle_initialize_section_cards(args: dict[str, Any]) -> dict[str, Any]:
    project_root = args.get("project_root") or str(WORKSPACE_ROOT)
    try:
        res = initialize_section_cards(project_root)
        return _success_response(res)
    except Exception as e:
        logger.exception("Failed to initialize section cards")
        return _error_response(ERROR_INTERNAL, f"Initialization failed: {e}", type(e).__name__)


def handle_request_more_context(args: dict[str, Any]) -> dict[str, Any]:
    run_id = args.get("run_id")
    if not run_id:
        return _error_response(ERROR_INVALID_INPUT, "Missing required argument: run_id")
    limit = args.get("limit", 5)
    try:
        config = load_config(str(WORKSPACE_ROOT))
        store = ExtensionStore(config.cache.path)
        store.init_db()
        results = store.get_more_context(run_id, limit)
        return _success_response({"run_id": run_id, "source_spans": results, "count": len(results)})
    except Exception as e:
        logger.exception("Failed to request more context")
        return _error_response(
            ERROR_INTERNAL, f"Failed to request more context: {e}", type(e).__name__
        )


def handle_submit_generation_feedback(args: dict[str, Any]) -> dict[str, Any]:
    run_id = args.get("run_id")
    metric_name = args.get("metric_name")
    metric_value = args.get("metric_value")
    metric_text = args.get("metric_text")
    if not run_id or not metric_name or metric_value is None:
        return _error_response(
            ERROR_INVALID_INPUT, "Missing required arguments: run_id, metric_name, and metric_value"
        )
    try:
        config = load_config(str(WORKSPACE_ROOT))
        store = ExtensionStore(config.cache.path)
        store.init_db()
        store.submit_feedback(run_id, metric_name, float(metric_value), metric_text)
        return _success_response({"status": "feedback_saved", "run_id": run_id})
    except Exception as e:
        logger.exception("Failed to submit feedback")
        return _error_response(ERROR_INTERNAL, f"Failed to submit feedback: {e}", type(e).__name__)


def handle_audit_manuscript_terminology(args: dict[str, Any]) -> dict[str, Any]:
    project_root = args.get("project_root") or str(WORKSPACE_ROOT)
    try:
        res = audit_manuscript_terminology(project_root)
        return _success_response(res)
    except Exception as e:
        logger.exception("Failed to audit terminology")
        return _error_response(ERROR_INTERNAL, f"Terminology audit failed: {e}", type(e).__name__)


def handle_get_term_context(args: dict[str, Any]) -> dict[str, Any]:
    if not args or "term" not in args:
        return _error_response(ERROR_INVALID_INPUT, "Missing required argument: term")
    term = str(args.get("term", ""))
    project_root = args.get("project_root")
    try:
        if not project_root:
            config = load_config(str(WORKSPACE_ROOT))
            project_root_str = config.rtfm.project_root
        else:
            project_root_str = str(project_root)
        res = get_term_context(term, project_root_str)
        return _success_response(res)
    except Exception as e:
        logger.exception("Failed to lookup term context")
        return _error_response(ERROR_INTERNAL, f"Terminology lookup failed: {e}", type(e).__name__)


def handle_get_manuscript_reference_graph(args: dict[str, Any]) -> dict[str, Any]:
    project_root = args.get("project_root")
    try:
        if not project_root:
            project_root = str(WORKSPACE_ROOT)
        res = build_reference_graph(project_root)
        return _success_response(res)
    except Exception as e:
        logger.exception("Failed to build reference graph")
        return _error_response(
            ERROR_INTERNAL, f"Failed to build reference graph: {e}", type(e).__name__
        )


def handle_review_card_candidates(args: dict[str, Any]) -> dict[str, Any]:
    project_root = args.get("project_root")
    try:
        root = Path(project_root) if project_root else WORKSPACE_ROOT
        generated_path = root / ".writing-context" / "cards.generated.yaml"
        if not generated_path.exists():
            return _error_response(
                ERROR_CONFIG, "No generated cards found. Please run 'cards scan' first."
            )

        import yaml

        with open(generated_path, encoding="utf-8") as f:
            gen_data = yaml.safe_load(f) or {}

        candidates = []
        sections = gen_data.get("sections", {}) or {}
        for sid, sdata in sections.items():
            # Check purpose candidate
            purpose = sdata.get("purpose")
            if isinstance(purpose, dict) and purpose.get("status") == "generated":
                candidates.append(
                    {
                        "section_id": sid,
                        "field": "purpose",
                        "value": purpose.get("value"),
                        "confidence": purpose.get("confidence", 0.0),
                        "provenance": purpose.get("provenance", []),
                    }
                )

            # Check key_terms candidates
            for kt in sdata.get("key_terms", []):
                if isinstance(kt, dict) and kt.get("status") == "generated":
                    candidates.append(
                        {
                            "section_id": sid,
                            "field": "key_terms",
                            "value": kt.get("value"),
                            "confidence": kt.get("confidence", 0.0),
                            "evidence": kt.get("evidence"),
                        }
                    )

            # Check facts candidates
            for fact in sdata.get("facts", []):
                if isinstance(fact, dict) and fact.get("status") == "generated":
                    candidates.append(
                        {
                            "section_id": sid,
                            "field": "facts",
                            "value": fact.get("value"),
                            "confidence": fact.get("confidence", 0.0),
                            "provenance": fact.get("provenance", []),
                        }
                    )

            # Check constraints candidates
            for const in sdata.get("constraints", []):
                if isinstance(const, dict) and const.get("status") == "generated":
                    candidates.append(
                        {
                            "section_id": sid,
                            "field": "constraints",
                            "value": const.get("value"),
                            "confidence": const.get("confidence", 0.0),
                        }
                    )

        return _success_response({"candidates": candidates})
    except Exception as e:
        logger.exception("Failed to review card candidates")
        return _error_response(
            ERROR_INTERNAL, f"Failed to review card candidates: {e}", type(e).__name__
        )


def handle_accept_card_candidate(args: dict[str, Any]) -> dict[str, Any]:
    section_id = args.get("section_id", "")
    field = args.get("field", "")
    value = args.get("value")
    project_root = args.get("project_root")

    if not section_id or not field or value is None:
        return _error_response(
            ERROR_INVALID_INPUT, "Missing required arguments: section_id, field, value"
        )
    if field not in ("purpose", "key_terms", "facts", "constraints"):
        return _error_response(ERROR_INVALID_INPUT, f"Invalid candidate field: {field}")

    try:
        root = Path(project_root) if project_root else WORKSPACE_ROOT
        generated_path = root / ".writing-context" / "cards.generated.yaml"
        overrides_path = root / ".writing-context" / "cards.overrides.yaml"
        lock_path = root / ".writing-context" / "cards.lock.json"

        if not generated_path.exists():
            return _error_response(
                ERROR_CONFIG, "No generated cards found. Please run 'cards scan' first."
            )

        import yaml

        with open(generated_path, encoding="utf-8") as f:
            gen_data: dict[str, Any] = yaml.safe_load(f) or {}

        sections = gen_data.get("sections", {}) or {}
        if section_id not in sections:
            return _error_response(
                ERROR_INVALID_INPUT, f"Section ID '{section_id}' not found in generated cards."
            )

        sdata = sections[section_id]
        found = False

        # Find and update candidate status in generated
        if field == "purpose":
            purpose = sdata.get("purpose")
            if isinstance(purpose, dict) and purpose.get("value") == value:
                purpose["status"] = "accepted"
                found = True
        elif field == "key_terms":
            for kt in sdata.get("key_terms", []):
                if isinstance(kt, dict) and kt.get("value") == value:
                    kt["status"] = "accepted"
                    found = True
                    break
        elif field == "facts":
            for fact in sdata.get("facts", []):
                if isinstance(fact, dict) and fact.get("value") == value:
                    fact["status"] = "accepted"
                    found = True
                    break
        elif field == "constraints":
            for const in sdata.get("constraints", []):
                if isinstance(const, dict) and const.get("value") == value:
                    const["status"] = "accepted"
                    found = True
                    break

        if not found:
            return _error_response(
                ERROR_INVALID_INPUT,
                f"No matching candidate found for section '{section_id}', field '{field}', value '{value}'.",
            )

        # Update overrides
        overrides_data: dict[str, Any] = {"version": 2, "document": {}, "sections": {}}
        if overrides_path.exists():
            try:
                with open(overrides_path, encoding="utf-8") as f:
                    loaded_ov = yaml.safe_load(f)
                    if isinstance(loaded_ov, dict):
                        overrides_data = loaded_ov
            except Exception:
                pass

        over_sections: dict[str, Any] = overrides_data.setdefault("sections", {})
        sec_over: dict[str, Any] = over_sections.setdefault(section_id, {})

        if field == "purpose":
            sec_over["purpose"] = value
        elif field == "key_terms":
            sec_over.setdefault("key_terms", [])
            if sec_over["key_terms"] is None:
                sec_over["key_terms"] = []
            if value not in sec_over["key_terms"]:
                sec_over["key_terms"].append(value)
        elif field == "facts":
            sec_over.setdefault("must_preserve", [])
            if sec_over["must_preserve"] is None:
                sec_over["must_preserve"] = []
            if value not in sec_over["must_preserve"]:
                sec_over["must_preserve"].append(value)
        elif field == "constraints":
            sec_over.setdefault("constraints", [])
            if sec_over["constraints"] is None:
                sec_over["constraints"] = []
            if value not in sec_over["constraints"]:
                sec_over["constraints"].append(value)

        # Update lock decisions
        lock_data: dict[str, Any] = {"sections": {}}
        if lock_path.exists():
            try:
                with open(lock_path, encoding="utf-8") as f:
                    loaded_lock = json.load(f)
                    if isinstance(loaded_lock, dict):
                        lock_data = loaded_lock
            except Exception:
                pass

        lock_sections: dict[str, Any] = lock_data.setdefault("sections", {})
        sec_lock: dict[str, Any] = lock_sections.setdefault(
            section_id, {"content_hash": "", "decisions": {}, "stale_fields": []}
        )
        decisions: dict[str, Any] = sec_lock.setdefault("decisions", {})

        if field == "purpose":
            decisions["purpose"] = "accepted"
        else:
            decisions[f"{field}:{value}"] = "accepted"

        # Save files
        overrides_path.parent.mkdir(exist_ok=True, parents=True)
        with open(generated_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(gen_data, f, sort_keys=False)
        with open(overrides_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(overrides_data, f, sort_keys=False)
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump(lock_data, f, indent=2)

        # Invalidate runtime cache
        global _RUNTIME_CACHE
        _RUNTIME_CACHE = None

        return _success_response(
            {"status": "accepted", "section_id": section_id, "field": field, "value": value}
        )

    except Exception as e:
        logger.exception("Failed to accept card candidate")
        return _error_response(
            ERROR_INTERNAL, f"Failed to accept card candidate: {e}", type(e).__name__
        )


def handle_reject_card_candidate(args: dict[str, Any]) -> dict[str, Any]:
    section_id = args.get("section_id", "")
    field = args.get("field", "")
    value = args.get("value")
    project_root = args.get("project_root")

    if not section_id or not field or value is None:
        return _error_response(
            ERROR_INVALID_INPUT, "Missing required arguments: section_id, field, value"
        )
    if field not in ("purpose", "key_terms", "facts", "constraints"):
        return _error_response(ERROR_INVALID_INPUT, f"Invalid candidate field: {field}")

    try:
        root = Path(project_root) if project_root else WORKSPACE_ROOT
        generated_path = root / ".writing-context" / "cards.generated.yaml"
        overrides_path = root / ".writing-context" / "cards.overrides.yaml"
        lock_path = root / ".writing-context" / "cards.lock.json"

        if not generated_path.exists():
            return _error_response(
                ERROR_CONFIG, "No generated cards found. Please run 'cards scan' first."
            )

        import yaml

        with open(generated_path, encoding="utf-8") as f:
            gen_data: dict[str, Any] = yaml.safe_load(f) or {}

        sections = gen_data.get("sections", {}) or {}
        if section_id not in sections:
            return _error_response(
                ERROR_INVALID_INPUT, f"Section ID '{section_id}' not found in generated cards."
            )

        sdata = sections[section_id]
        found = False

        # Find and update candidate status in generated
        if field == "purpose":
            purpose = sdata.get("purpose")
            if isinstance(purpose, dict) and purpose.get("value") == value:
                purpose["status"] = "rejected"
                found = True
        elif field == "key_terms":
            for kt in sdata.get("key_terms", []):
                if isinstance(kt, dict) and kt.get("value") == value:
                    kt["status"] = "rejected"
                    found = True
                    break
        elif field == "facts":
            for fact in sdata.get("facts", []):
                if isinstance(fact, dict) and fact.get("value") == value:
                    fact["status"] = "rejected"
                    found = True
                    break
        elif field == "constraints":
            for const in sdata.get("constraints", []):
                if isinstance(const, dict) and const.get("value") == value:
                    const["status"] = "rejected"
                    found = True
                    break

        if not found:
            return _error_response(
                ERROR_INVALID_INPUT,
                f"No matching candidate found for section '{section_id}', field '{field}', value '{value}'.",
            )

        # Update overrides (remove if present)
        overrides_data: dict[str, Any] = {"version": 2, "document": {}, "sections": {}}
        if overrides_path.exists():
            try:
                with open(overrides_path, encoding="utf-8") as f:
                    loaded_ov = yaml.safe_load(f)
                    if isinstance(loaded_ov, dict):
                        overrides_data = loaded_ov
            except Exception:
                pass

        over_sections = overrides_data.setdefault("sections", {})
        sec_over = over_sections.setdefault(section_id, {})

        if field == "purpose":
            if "purpose" in sec_over:
                del sec_over["purpose"]
        elif field == "key_terms":
            if "key_terms" in sec_over and sec_over["key_terms"]:
                if value in sec_over["key_terms"]:
                    sec_over["key_terms"].remove(value)
        elif field == "facts":
            if "must_preserve" in sec_over and sec_over["must_preserve"]:
                if value in sec_over["must_preserve"]:
                    sec_over["must_preserve"].remove(value)
        elif field == "constraints":
            if "constraints" in sec_over and sec_over["constraints"]:
                if value in sec_over["constraints"]:
                    sec_over["constraints"].remove(value)

        # Update lock decisions
        lock_data: dict[str, Any] = {"sections": {}}
        if lock_path.exists():
            try:
                with open(lock_path, encoding="utf-8") as f:
                    loaded_lock = json.load(f)
                    if isinstance(loaded_lock, dict):
                        lock_data = loaded_lock
            except Exception:
                pass

        lock_sections = lock_data.setdefault("sections", {})
        sec_lock = lock_sections.setdefault(
            section_id, {"content_hash": "", "decisions": {}, "stale_fields": []}
        )
        decisions = sec_lock.setdefault("decisions", {})

        if field == "purpose":
            decisions["purpose"] = "rejected"
        else:
            decisions[f"{field}:{value}"] = "rejected"

        # Save files
        with open(generated_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(gen_data, f, sort_keys=False)
        with open(overrides_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(overrides_data, f, sort_keys=False)
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump(lock_data, f, indent=2)

        # Invalidate runtime cache
        global _RUNTIME_CACHE
        _RUNTIME_CACHE = None

        return _success_response(
            {"status": "rejected", "section_id": section_id, "field": field, "value": value}
        )

    except Exception as e:
        logger.exception("Failed to reject card candidate")
        return _error_response(
            ERROR_INTERNAL, f"Failed to reject card candidate: {e}", type(e).__name__
        )


def handle_edit_card_field(args: dict[str, Any]) -> dict[str, Any]:
    section_id = args.get("section_id", "")
    field = args.get("field", "")
    value = args.get("value")
    project_root = args.get("project_root")

    if not section_id or not field:
        return _error_response(ERROR_INVALID_INPUT, "Missing required arguments: section_id, field")

    try:
        root = Path(project_root) if project_root else WORKSPACE_ROOT
        overrides_path = root / ".writing-context" / "cards.overrides.yaml"

        import yaml

        overrides_data: dict[str, Any] = {"version": 2, "document": {}, "sections": {}}
        if overrides_path.exists():
            try:
                with open(overrides_path, encoding="utf-8") as f:
                    loaded_ov = yaml.safe_load(f)
                    if isinstance(loaded_ov, dict):
                        overrides_data = loaded_ov
            except Exception:
                pass

        over_sections = overrides_data.setdefault("sections", {})
        sec_over = over_sections.setdefault(section_id, {})

        if value is None:
            # Delete field if value is null
            if field in sec_over:
                del sec_over[field]
        else:
            sec_over[field] = value

        overrides_path.parent.mkdir(exist_ok=True, parents=True)
        with open(overrides_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(overrides_data, f, sort_keys=False)

        # Invalidate runtime cache
        global _RUNTIME_CACHE
        _RUNTIME_CACHE = None

        return _success_response(
            {"status": "updated", "section_id": section_id, "field": field, "value": value}
        )

    except Exception as e:
        logger.exception("Failed to edit card field")
        return _error_response(ERROR_INTERNAL, f"Failed to edit card field: {e}", type(e).__name__)


def handle_explain_card_candidate(args: dict[str, Any]) -> dict[str, Any]:
    section_id = args.get("section_id", "")
    field = args.get("field", "")
    value = args.get("value")
    project_root = args.get("project_root")

    if not section_id or not field or value is None:
        return _error_response(
            ERROR_INVALID_INPUT, "Missing required arguments: section_id, field, value"
        )
    if field not in ("purpose", "key_terms", "facts", "constraints"):
        return _error_response(ERROR_INVALID_INPUT, f"Invalid candidate field: {field}")

    try:
        root = Path(project_root) if project_root else WORKSPACE_ROOT
        generated_path = root / ".writing-context" / "cards.generated.yaml"

        if not generated_path.exists():
            return _error_response(
                ERROR_CONFIG, "No generated cards found. Please run 'cards scan' first."
            )

        import yaml

        with open(generated_path, encoding="utf-8") as f:
            gen_data = yaml.safe_load(f) or {}

        sections = gen_data.get("sections", {}) or {}
        if section_id not in sections:
            return _error_response(
                ERROR_INVALID_INPUT, f"Section ID '{section_id}' not found in generated cards."
            )

        sdata = sections[section_id]
        candidate_info = None

        if field == "purpose":
            purpose = sdata.get("purpose")
            if isinstance(purpose, dict) and purpose.get("value") == value:
                candidate_info = purpose
        elif field == "key_terms":
            for kt in sdata.get("key_terms", []):
                if isinstance(kt, dict) and kt.get("value") == value:
                    candidate_info = kt
                    break
        elif field == "facts":
            for fact in sdata.get("facts", []):
                if isinstance(fact, dict) and fact.get("value") == value:
                    candidate_info = fact
                    break
        elif field == "constraints":
            for const in sdata.get("constraints", []):
                if isinstance(const, dict) and const.get("value") == value:
                    candidate_info = const
                    break

        if not candidate_info:
            return _error_response(
                ERROR_INVALID_INPUT,
                f"No matching candidate found for section '{section_id}', field '{field}', value '{value}'.",
            )

        confidence = candidate_info.get("confidence", 0.0)
        evidence = candidate_info.get("evidence")
        provenance = candidate_info.get("provenance", [])

        explanation = f"Candidate '{value}' for field '{field}' in section '{section_id}' was extracted with confidence {confidence}."
        if evidence:
            explanation += f" Evidence: {evidence}."
        if provenance:
            explanation += f" Provenance: {provenance}."

        return _success_response(
            {
                "section_id": section_id,
                "field": field,
                "value": value,
                "confidence": confidence,
                "evidence": evidence,
                "provenance": provenance,
                "explanation": explanation,
            }
        )

    except Exception as e:
        logger.exception("Failed to explain card candidate")
        return _error_response(
            ERROR_INTERNAL, f"Failed to explain card candidate: {e}", type(e).__name__
        )


def process_message(line: str) -> str | None:
    global WORKSPACE_ROOT, _RUNTIME_CACHE
    try:
        logger.debug(f"Received: {line}")
        req = json.loads(line)
        if "method" in req:
            method = req["method"]
            result: Any = None
            if method == "initialize":
                params = req.get("params", {})
                root_uri = params.get("rootUri")
                if root_uri:
                    try:
                        from urllib.parse import unquote, urlparse

                        parsed = urlparse(root_uri)
                        if parsed.scheme == "file":
                            path_str = unquote(parsed.path)
                            if (
                                os.name == "nt"
                                and path_str.startswith("/")
                                and len(path_str) > 2
                                and path_str[2] == ":"
                            ):
                                path_str = path_str[1:]
                            WORKSPACE_ROOT = Path(path_str).resolve()
                        else:
                            WORKSPACE_ROOT = Path(root_uri).resolve()
                        logger.info(f"Initialized workspace root dynamically to: {WORKSPACE_ROOT}")
                        if _client_manager is not None:
                            _client_manager.workspace_root = WORKSPACE_ROOT
                        # Invalidate runtime cache to force config reload on new workspace
                        _RUNTIME_CACHE = None
                    except Exception:
                        logger.exception(f"Failed to parse rootUri: {root_uri}")
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "writing-context-rtfm", "version": __version__},
                }
            elif method.startswith("notifications/"):
                return None  # notifications get no response, no error
            elif method == "tools/list":
                result = get_tools_list()
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "resources/templates/list":
                result = {"resourceTemplates": []}
            elif method == "prompts/list":
                result = {
                    "prompts": [
                        {
                            "name": "write_section",
                            "description": "Pre-structure a prompt for drafting or editing a manuscript section with surgical context.",
                            "arguments": [
                                {
                                    "name": "task",
                                    "description": "Natural-language description of the writing task",
                                    "required": True,
                                    "type": "string",
                                },
                                {
                                    "name": "target",
                                    "description": "section_id from section_cards.yaml (e.g. section_intro)",
                                    "required": True,
                                    "type": "string",
                                },
                                {
                                    "name": "token_budget",
                                    "description": "Maximum token budget for context (default: 6000)",
                                    "required": False,
                                    "type": "integer",
                                },
                                {
                                    "name": "task_type",
                                    "description": "Writing task type (choices: write_new_section, revise_existing_section, proofread, expand, condense, align_with_previous_sections, review)",
                                    "required": False,
                                    "type": "string",
                                },
                                {
                                    "name": "line_start",
                                    "description": "Target start line range",
                                    "required": False,
                                    "type": "integer",
                                },
                                {
                                    "name": "line_end",
                                    "description": "Target end line range",
                                    "required": False,
                                    "type": "integer",
                                },
                                {
                                    "name": "pack_mode",
                                    "description": "Context pack mode (choices: minimal, standard, deep)",
                                    "required": False,
                                    "type": "string",
                                },
                            ],
                        },
                        {
                            "name": "proofread_section",
                            "description": "Pre-structure a prompt for proofreading or editing a specific line range of a file.",
                            "arguments": [
                                {
                                    "name": "target_file",
                                    "description": "Path to the file being proofread",
                                    "required": True,
                                    "type": "string",
                                },
                                {
                                    "name": "line_start",
                                    "description": "1-indexed starting line number",
                                    "required": True,
                                    "type": "integer",
                                },
                                {
                                    "name": "line_end",
                                    "description": "1-indexed ending line number",
                                    "required": True,
                                    "type": "integer",
                                },
                                {
                                    "name": "mode",
                                    "description": "Proofreading mode (surface, academic_clarity, consistency, latex_safe)",
                                    "required": False,
                                    "type": "string",
                                },
                                {
                                    "name": "strictness",
                                    "description": "Strictness level (conservative, moderate, assertive)",
                                    "required": False,
                                    "type": "string",
                                },
                            ],
                        },
                    ]
                }
            elif method == "prompts/get":
                params = req.get("params", {})
                name = params.get("name")
                arguments = params.get("arguments", {})
                if name == "write_section":
                    task = arguments.get("task", "")
                    target = arguments.get("target")
                    budget_arg = arguments.get("token_budget")
                    task_type = arguments.get("task_type")
                    line_start_val = arguments.get("line_start")
                    line_end_val = arguments.get("line_end")
                    pack_mode = arguments.get("pack_mode")
                    try:
                        config, cards, card_warnings, adapter, store = _load_runtime()
                        budget = (
                            int(budget_arg)
                            if budget_arg is not None
                            else config.context.default_token_budget
                        )
                        line_start = int(line_start_val) if line_start_val is not None else None
                        line_end = int(line_end_val) if line_end_val is not None else None
                        from writing_context_rtfm.providers import get_active_providers

                        providers = get_active_providers(config)
                        generator = ContextPackGenerator(
                            config, cards, adapter, store, providers=providers
                        )
                        pack = generator.generate(
                            task=task,
                            target=target,
                            token_budget=budget,
                            task_type=task_type,
                            line_start=line_start,
                            line_end=line_end,
                            pack_mode=pack_mode,
                        )
                        prompt_text = _format_write_section_prompt(pack)
                        result = {
                            "description": "Hydrated drafting/editing prompt with surgical context.",
                            "messages": [
                                {"role": "user", "content": {"type": "text", "text": prompt_text}}
                            ],
                        }
                    except Exception as e:
                        logger.exception("Failed to hydrate prompt write_section")
                        response = json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": req.get("id"),
                                "error": {
                                    "code": -32603,
                                    "message": f"Failed to hydrate prompt: {e}",
                                },
                            }
                        )
                        return response
                elif name == "proofread_section":
                    target_file = arguments.get("target_file", "")
                    line_start_arg = arguments.get("line_start")
                    line_end_arg = arguments.get("line_end")
                    mode = arguments.get("mode", "surface")
                    strictness = arguments.get("strictness", "moderate")
                    try:
                        config, cards, card_warnings, adapter, store = _load_runtime()
                        line_start = int(line_start_arg)
                        line_end = int(line_end_arg)
                        proof_gen = ProofreadPackGenerator(config, cards, adapter, store)
                        proof_pack = proof_gen.generate(
                            target_file=target_file,
                            line_start=line_start,
                            line_end=line_end,
                            mode=mode,
                            strictness=strictness,
                        )
                        prompt_text = _format_proofread_section_prompt(proof_pack)
                        result = {
                            "description": "Hydrated proofreading prompt with target text and terminology.",
                            "messages": [
                                {"role": "user", "content": {"type": "text", "text": prompt_text}}
                            ],
                        }
                    except Exception as e:
                        logger.exception("Failed to hydrate prompt proofread_section")
                        response = json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": req.get("id"),
                                "error": {
                                    "code": -32603,
                                    "message": f"Failed to hydrate prompt: {e}",
                                },
                            }
                        )
                        return response
                else:
                    response = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": req.get("id"),
                            "error": {"code": -32601, "message": f"Prompt not found: {name}"},
                        }
                    )
                    return response
            elif method == "tools/call":
                params = req.get("params", {})
                name = params.get("name")
                call_args = params.get("arguments", {})
                if name == "get_writing_context_pack":
                    result = handle_get_writing_context_pack(call_args)
                elif name == "get_proofreading_context_pack":
                    result = handle_get_proofreading_context_pack(call_args)
                elif name == "refresh_index":
                    result = handle_refresh_index(call_args)
                elif name == "initialize_section_cards":
                    result = handle_initialize_section_cards(call_args)
                elif name == "request_more_context":
                    result = handle_request_more_context(call_args)
                elif name == "submit_generation_feedback":
                    result = handle_submit_generation_feedback(call_args)
                elif name == "audit_manuscript_terminology":
                    result = handle_audit_manuscript_terminology(call_args)
                elif name == "get_term_context":
                    result = handle_get_term_context(call_args)
                elif name == "get_manuscript_reference_graph":
                    result = handle_get_manuscript_reference_graph(call_args)
                elif name == "review_card_candidates":
                    result = handle_review_card_candidates(call_args)
                elif name == "accept_card_candidate":
                    result = handle_accept_card_candidate(call_args)
                elif name == "reject_card_candidate":
                    result = handle_reject_card_candidate(call_args)
                elif name == "edit_card_field":
                    result = handle_edit_card_field(call_args)
                elif name == "explain_card_candidate":
                    result = handle_explain_card_candidate(call_args)
                else:
                    response = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": req.get("id"),
                            "error": {"code": -32601, "message": f"Tool not found: {name}"},
                        }
                    )
                    logger.debug(f"Responding (Error): {response}")
                    return response
            else:
                if "id" in req:
                    response = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": req.get("id"),
                            "error": {"code": -32601, "message": f"Method not found: {method}"},
                        }
                    )
                    logger.debug(f"Responding (Error): {response}")
                    return response
                return None

            response = json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": result})
            logger.debug(f"Responding: {response}")
            return response
    except Exception as e:
        logger.exception("Error processing message")
        return json.dumps({"jsonrpc": "2.0", "error": {"code": -32000, "message": str(e)}})
    return None


def run_server() -> None:
    """Start standard IO JSON-RPC loop."""
    global _client_manager
    import atexit
    import signal

    from writing_context_rtfm.providers.manager import LocalMCPClientManager

    _client_manager = LocalMCPClientManager(workspace_root=str(WORKSPACE_ROOT))

    def cleanup_handler(*args: Any) -> None:
        global _client_manager
        if _client_manager is not None:
            with contextlib.suppress(Exception):
                _client_manager.shutdown()
            _client_manager = None
        try:
            from argparse import Namespace

            from writing_context_rtfm.cli import cleanup_command

            cleanup_command(Namespace(project_root=str(WORKSPACE_ROOT)))
        except Exception:
            pass

    atexit.register(cleanup_handler)

    try:
        signal.signal(signal.SIGTERM, lambda _sig, _frame: sys.exit(0))
        signal.signal(signal.SIGINT, lambda _sig, _frame: sys.exit(0))
    except (ValueError, OSError):
        pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = process_message(line)
        if response:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()
