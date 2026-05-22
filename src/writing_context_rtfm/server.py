"""MCP server logic."""
import sys
import json
import os
from dataclasses import asdict
from typing import Any, Dict, Optional
from pathlib import Path

from writing_context_rtfm.config import load_config
from writing_context_rtfm.section_cards import load_section_cards, validate_section_cards
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.proofread import ProofreadPackGenerator
from writing_context_rtfm.hashing import compute_rtfm_fingerprint
import logging
from writing_context_rtfm.features import initialize_section_cards, audit_manuscript_terminology, get_term_context
from writing_context_rtfm.latex import build_reference_graph
from writing_context_rtfm.utils import resolve_rtfm_db_path
from writing_context_rtfm import __version__

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
    constraints_joined = "\n".join(f"- {c}" for c in pack.constraints) if pack.constraints else "None"

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
    terminology_txt = []
    for t in (pack.constraints.terminology or []):
        examples_str = "; ".join(f"'{ex}'" for ex in t.usage_examples) if t.usage_examples else "None"
        terminology_txt.append(f"- '{t.term}': used in: {examples_str}")
    terminology_joined = "\n".join(terminology_txt) if terminology_txt else "None"

    constraints_joined = "\n".join(f"- {c}" for c in pack.constraints.section_specific_rules) if pack.constraints.section_specific_rules else "None"
    general_joined = "\n".join(f"- {c}" for c in pack.constraints.general_rules) if pack.constraints.general_rules else "None"

    local_txt = ""
    if pack.local_context.previous_paragraph:
        local_txt += f"[Previous Context Paragraph]:\n{pack.local_context.previous_paragraph}\n\n"
    local_txt += f"[Target Text to Revise]:\n{pack.local_context.target_span}\n\n"
    if pack.local_context.next_paragraph:
        local_txt += f"[Next Context Paragraph]:\n{pack.local_context.next_paragraph}\n\n"

    return (
        f"You are proofreading and refining the following segment of the manuscript.\n\n"
        f"Target file: {pack.target.file_path} (Lines {pack.target.line_start}-{pack.target.line_end})\n"
        f"Mode: {pack.constraints.mode} | Strictness: {pack.constraints.strictness}\n\n"
        f"[Local Context surrounding Target]:\n{local_txt}"
        f"[General Rules for {pack.constraints.mode}]:\n{general_joined}\n\n"
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


def _error_response(code: str, message: str, detail: Optional[str] = None) -> Dict[str, Any]:
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


def _success_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def _sanitize_span_for_output(span_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Strip internal-only fields (e.g., raw snippet metadata) from a serialized SourceSpan.

    The `metadata` dict is used internally for token estimation and avoid-filtering
    but its contents already appear in `reason`, so emitting it doubles bytes.
    """
    out = dict(span_dict)
    out.pop("metadata", None)
    return out


def _sanitize_pack_for_output(pack_dict: Dict[str, Any]) -> Dict[str, Any]:
    spans = pack_dict.get("source_spans") or []
    pack_dict["source_spans"] = [_sanitize_span_for_output(s) for s in spans]
    return pack_dict


# --- Tool catalog -----------------------------------------------------------

def get_tools_list():
    return {
        "tools": [
            {
                "name": "get_writing_context_pack",
                "description": (
                    "Generate a compact, prioritized writing context pack for a specific writing task. "
                    "Use this BEFORE drafting, rewriting, or expanding any section of the manuscript. "
                    "Returns the document thesis, hard constraints from the section card, and a ranked "
                    "list of source spans (each tagged essential | supporting | background) — never full "
                    "files. Prefer this over reading the manuscript directly: the pack is scoped to the "
                    "task, deduplicated, and stays within the requested token budget. "
                    "Output shape: {task, target, document_thesis, prior_claims, terminology, constraints, "
                    "source_spans[], estimated_tokens, status ('complete' | 'degraded'), warnings[], quality, "
                    "summary}. When status='degraded', inspect warnings before proceeding."
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
                                "section_id from .writing-context/section_cards.yaml (e.g., 'section_approach'). "
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
                                "review"
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
                            "additionalProperties": {
                                "type": "number"
                            }
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
                    "the surrounding paragraphs (previous + next), mode/strictness rules, section-specific "
                    "constraints, and a terminology map showing how key terms have been used elsewhere "
                    "in the manuscript so edits remain consistent. "
                    "Output shape: {target, local_context, constraints{mode, strictness, general_rules, "
                    "section_specific_rules, terminology[]}, estimated_tokens, status}."
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
                                "Editing mode:\n"
                                "  surface — grammar, spelling, punctuation only; preserve structure and tone.\n"
                                "  academic_clarity — sharpen precision and formal vocabulary; improve logical flow.\n"
                                "  consistency — enforce terminology and formatting consistency across the manuscript.\n"
                                "  latex_safe — same as surface but treats \\cite, \\ref, \\label and math envs as immutable."
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
                                "Upper bound on tokens in the assembled pack. If the local context plus "
                                "constraints exceeds this, status becomes 'degraded' (the pack is still "
                                "returned, but the client should be aware)."
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
                            "description": "Custom project root path (optional). Defaults to current workspace."
                        }
                    }
                }
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
                            "description": "The unique UUID run_id returned from a prior get_writing_context_pack call."
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of extra context spans to fetch (default: 5)."
                        }
                    },
                    "required": ["run_id"]
                }
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
                            "description": "The unique UUID run_id from the context pack."
                        },
                        "metric_name": {
                            "type": "string",
                            "description": "Metric category being logged (e.g. helpfulness, hallucinations, constraint_violated)."
                        },
                        "metric_value": {
                            "type": "number",
                            "description": "Numeric evaluation value (e.g. 1.0 for positive/present, 0.0 for negative/absent)."
                        },
                        "metric_text": {
                            "type": "string",
                            "description": "Optional text details or description of issue/helpfulness."
                        }
                    },
                    "required": ["run_id", "metric_name", "metric_value"]
                }
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
                            "description": "Custom project root path (optional). Defaults to current workspace."
                        }
                    }
                }
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
                            "description": "The term (canonical, variant, or avoid phrase) to look up."
                        },
                        "project_root": {
                            "type": "string",
                            "description": "Optional project root path."
                        }
                    },
                    "required": ["term"]
                }
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
                            "description": "Optional custom project root path. Defaults to the workspace root."
                        }
                    }
                }
            }
        ]
    }

_RUNTIME_CACHE = None

def _load_runtime():
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

    if (_RUNTIME_CACHE is not None and
        _RUNTIME_CACHE["config_mtime"] == config_mtime and
        _RUNTIME_CACHE["config_size"] == config_size and
        _RUNTIME_CACHE["sc_mtime"] == sc_mtime and
        _RUNTIME_CACHE["sc_size"] == sc_size):
        return (
            _RUNTIME_CACHE["config"],
            _RUNTIME_CACHE["cards"],
            _RUNTIME_CACHE["card_warnings"],
            _RUNTIME_CACHE["adapter"],
            _RUNTIME_CACHE["store"]
        )

    config = load_config(str(WORKSPACE_ROOT))
    cards = load_section_cards(config.section_cards.path, required=config.section_cards.required)
    card_warnings = validate_section_cards(cards) if cards else []
    adapter = RTFMAdapter(project_root=str(config.rtfm.project_root))
    store = ExtensionStore(config.cache.path)
    store.init_db()

    if config.rtfm.sync_before_pack:
        try:
            adapter.sync(config.rtfm.project_root, corpus=config.rtfm.corpus)
            if config.cache.invalidate_on_refresh:
                rtfm_db = resolve_rtfm_db_path(Path(config.rtfm.project_root))
                fingerprint = compute_rtfm_fingerprint(rtfm_db)
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
        "store": store
    }

    return config, cards, card_warnings, adapter, store


def handle_get_writing_context_pack(args):
    if not args or "task" not in args:
        return _error_response(ERROR_INVALID_INPUT, "Missing required argument: task")

    try:
        config, cards, card_warnings, adapter, store = _load_runtime()
    except Exception as e:
        logger.exception("Failed to load runtime for get_writing_context_pack")
        return _error_response(ERROR_CONFIG, "Failed to load configuration or section cards.", str(e))

    generator = ContextPackGenerator(config, cards, adapter, store)
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
            role_budgets=role_budgets
        )
    except Exception as e:
        logger.exception("Pack generation failed")
        return _error_response(ERROR_RETRIEVAL, "Context pack generation failed.", type(e).__name__)

    payload = asdict(pack)
    all_warnings = list(pack.warnings or [])
    if card_warnings:
        all_warnings.extend(card_warnings)
    if all_warnings:
        payload["warnings"] = all_warnings
        has_degrading_warning = any(not w.startswith("LaTeX Safety:") for w in all_warnings)
        if has_degrading_warning and payload.get("status") == "complete":
            payload["status"] = "degraded"
    return _success_response(_sanitize_pack_for_output(payload))


def handle_get_proofreading_context_pack(args):
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
        return _error_response(ERROR_CONFIG, "Failed to load configuration or section cards.", str(e))

    generator = ProofreadPackGenerator(config, cards, adapter, store)
    try:
        pack = generator.generate(
            target_file=args.get("target_file"),
            line_start=args.get("line_start"),
            line_end=args.get("line_end"),
            mode=args.get("mode", "surface"),
            strictness=args.get("strictness", "moderate"),
            max_tokens=args.get("max_tokens", 4000),
        )
    except Exception as e:
        logger.exception("Proofreading pack generation failed")
        return _error_response(ERROR_RETRIEVAL, "Proofreading pack generation failed.", type(e).__name__)

    payload = asdict(pack)
    all_warnings = list(pack.warnings or [])
    if card_warnings:
        all_warnings.extend(card_warnings)
    if all_warnings:
        payload["warnings"] = all_warnings
        has_degrading_warning = any(not w.startswith("LaTeX Safety:") for w in all_warnings)
        if has_degrading_warning and payload.get("status") == "complete":
            payload["status"] = "degraded"
    return _success_response(payload)


def handle_refresh_index(args):
    try:
        config = load_config(str(WORKSPACE_ROOT))
    except Exception as e:
        logger.exception("Failed to load config for refresh_index")
        return _error_response(ERROR_CONFIG, "Failed to load configuration.", str(e))

    project_root = args.get("project_root", config.rtfm.project_root)
    corpus = args.get("corpus", config.rtfm.corpus)
    adapter = RTFMAdapter(project_root=project_root)

    try:
        adapter.sync(project_root, corpus=corpus)
    except Exception as e:
        logger.exception("RTFM sync raised")
        return _error_response(ERROR_RETRIEVAL, f"RTFM sync failed: {e}", type(e).__name__)

    if config.cache.invalidate_on_refresh:
        store = ExtensionStore(config.cache.path)
        rtfm_db = resolve_rtfm_db_path(Path(project_root))
        fingerprint = compute_rtfm_fingerprint(rtfm_db)
        store.invalidate_for_fingerprint(fingerprint)
    return _success_response({"status": "ok", "cache_invalidated": config.cache.invalidate_on_refresh})

def handle_initialize_section_cards(args):
    project_root = args.get("project_root") or str(WORKSPACE_ROOT)
    try:
        res = initialize_section_cards(project_root)
        return _success_response(res)
    except Exception as e:
        logger.exception("Failed to initialize section cards")
        return _error_response(ERROR_INTERNAL, f"Initialization failed: {e}", type(e).__name__)

def handle_request_more_context(args):
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
        return _error_response(ERROR_INTERNAL, f"Failed to request more context: {e}", type(e).__name__)

def handle_submit_generation_feedback(args):
    run_id = args.get("run_id")
    metric_name = args.get("metric_name")
    metric_value = args.get("metric_value")
    metric_text = args.get("metric_text")
    if not run_id or not metric_name or metric_value is None:
        return _error_response(ERROR_INVALID_INPUT, "Missing required arguments: run_id, metric_name, and metric_value")
    try:
        config = load_config(str(WORKSPACE_ROOT))
        store = ExtensionStore(config.cache.path)
        store.init_db()
        store.submit_feedback(run_id, metric_name, float(metric_value), metric_text)
        return _success_response({"status": "feedback_saved", "run_id": run_id})
    except Exception as e:
        logger.exception("Failed to submit feedback")
        return _error_response(ERROR_INTERNAL, f"Failed to submit feedback: {e}", type(e).__name__)

def handle_audit_manuscript_terminology(args):
    project_root = args.get("project_root") or str(WORKSPACE_ROOT)
    try:
        res = audit_manuscript_terminology(project_root)
        return _success_response(res)
    except Exception as e:
        logger.exception("Failed to audit terminology")
        return _error_response(ERROR_INTERNAL, f"Terminology audit failed: {e}", type(e).__name__)

def handle_get_term_context(args):
    if not args or "term" not in args:
        return _error_response(ERROR_INVALID_INPUT, "Missing required argument: term")
    term = args.get("term")
    project_root = args.get("project_root")
    try:
        if not project_root:
            config = load_config(str(WORKSPACE_ROOT))
            project_root = config.rtfm.project_root
        res = get_term_context(term, project_root)
        return _success_response(res)
    except Exception as e:
        logger.exception("Failed to lookup term context")
        return _error_response(ERROR_INTERNAL, f"Terminology lookup failed: {e}", type(e).__name__)

def handle_get_manuscript_reference_graph(args):
    project_root = args.get("project_root")
    try:
        if not project_root:
            project_root = str(WORKSPACE_ROOT)
        res = build_reference_graph(project_root)
        return _success_response(res)
    except Exception as e:
        logger.exception("Failed to build reference graph")
        return _error_response(ERROR_INTERNAL, f"Failed to build reference graph: {e}", type(e).__name__)

def process_message(line):
    global WORKSPACE_ROOT, _RUNTIME_CACHE
    try:
        logger.debug(f"Received: {line}")
        req = json.loads(line)
        if "method" in req:
            method = req["method"]
            result = None
            if method == "initialize":
                params = req.get("params", {})
                root_uri = params.get("rootUri")
                if root_uri:
                    try:
                        from urllib.parse import urlparse, unquote
                        parsed = urlparse(root_uri)
                        if parsed.scheme == "file":
                            path_str = unquote(parsed.path)
                            if os.name == 'nt' and path_str.startswith('/') and len(path_str) > 2 and path_str[2] == ':':
                                path_str = path_str[1:]
                            WORKSPACE_ROOT = Path(path_str).resolve()
                        else:
                            WORKSPACE_ROOT = Path(root_uri).resolve()
                        logger.info(f"Initialized workspace root dynamically to: {WORKSPACE_ROOT}")
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
                                {"name": "task", "description": "Natural-language description of the writing task", "required": True},
                                {"name": "target", "description": "section_id from section_cards.yaml (e.g. section_intro)", "required": True},
                                {"name": "token_budget", "description": "Maximum token budget for context (default: 6000)", "required": False},
                                {"name": "task_type", "description": "Writing task type (choices: write_new_section, revise_existing_section, proofread, expand, condense, align_with_previous_sections, review)", "required": False},
                                {"name": "line_start", "description": "Target start line range", "required": False},
                                {"name": "line_end", "description": "Target end line range", "required": False},
                                {"name": "pack_mode", "description": "Context pack mode (choices: minimal, standard, deep)", "required": False}
                            ]
                        },
                        {
                            "name": "proofread_section",
                            "description": "Pre-structure a prompt for proofreading or editing a specific line range of a file.",
                            "arguments": [
                                {"name": "target_file", "description": "Path to the file being proofread", "required": True},
                                {"name": "line_start", "description": "1-indexed starting line number", "required": True},
                                {"name": "line_end", "description": "1-indexed ending line number", "required": True},
                                {"name": "mode", "description": "Proofreading mode (surface, academic_clarity, consistency, latex_safe)", "required": False},
                                {"name": "strictness", "description": "Strictness level (conservative, moderate, assertive)", "required": False}
                            ]
                        }
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
                        budget = int(budget_arg) if budget_arg is not None else config.context.default_token_budget
                        line_start = int(line_start_val) if line_start_val is not None else None
                        line_end = int(line_end_val) if line_end_val is not None else None
                        generator = ContextPackGenerator(config, cards, adapter, store)
                        pack = generator.generate(
                            task=task,
                            target=target,
                            token_budget=budget,
                            task_type=task_type,
                            line_start=line_start,
                            line_end=line_end,
                            pack_mode=pack_mode
                        )
                        prompt_text = _format_write_section_prompt(pack)
                        result = {
                            "description": "Hydrated drafting/editing prompt with surgical context.",
                            "messages": [
                                {
                                    "role": "user",
                                    "content": {
                                        "type": "text",
                                        "text": prompt_text
                                    }
                                }
                            ]
                        }
                    except Exception as e:
                        logger.exception("Failed to hydrate prompt write_section")
                        response = json.dumps({
                            "jsonrpc": "2.0",
                            "id": req.get("id"),
                            "error": {"code": -32603, "message": f"Failed to hydrate prompt: {e}"}
                        })
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
                        generator = ProofreadPackGenerator(config, cards, adapter, store)
                        pack = generator.generate(
                            target_file=target_file,
                            line_start=line_start,
                            line_end=line_end,
                            mode=mode,
                            strictness=strictness
                        )
                        prompt_text = _format_proofread_section_prompt(pack)
                        result = {
                            "description": "Hydrated proofreading prompt with target text and terminology.",
                            "messages": [
                                {
                                    "role": "user",
                                    "content": {
                                        "type": "text",
                                        "text": prompt_text
                                    }
                                }
                            ]
                        }
                    except Exception as e:
                        logger.exception("Failed to hydrate prompt proofread_section")
                        response = json.dumps({
                            "jsonrpc": "2.0",
                            "id": req.get("id"),
                            "error": {"code": -32603, "message": f"Failed to hydrate prompt: {e}"}
                        })
                        return response
                else:
                    response = json.dumps({
                        "jsonrpc": "2.0",
                        "id": req.get("id"),
                        "error": {"code": -32601, "message": f"Prompt not found: {name}"}
                    })
                    return response
            elif method == "tools/call":
                params = req.get("params", {})
                name = params.get("name")
                args = params.get("arguments", {})
                if name == "get_writing_context_pack":
                    result = handle_get_writing_context_pack(args)
                elif name == "get_proofreading_context_pack":
                    result = handle_get_proofreading_context_pack(args)
                elif name == "refresh_index":
                    result = handle_refresh_index(args)
                elif name == "initialize_section_cards":
                    result = handle_initialize_section_cards(args)
                elif name == "request_more_context":
                    result = handle_request_more_context(args)
                elif name == "submit_generation_feedback":
                    result = handle_submit_generation_feedback(args)
                elif name == "audit_manuscript_terminology":
                    result = handle_audit_manuscript_terminology(args)
                elif name == "get_term_context":
                    result = handle_get_term_context(args)
                elif name == "get_manuscript_reference_graph":
                    result = handle_get_manuscript_reference_graph(args)
                else:
                    response = json.dumps({
                        "jsonrpc": "2.0",
                        "id": req.get("id"),
                        "error": {"code": -32601, "message": f"Tool not found: {name}"}
                    })
                    logger.debug(f"Responding (Error): {response}")
                    return response
            else:
                if "id" in req:
                    response = json.dumps({
                        "jsonrpc": "2.0",
                        "id": req.get("id"),
                        "error": {"code": -32601, "message": f"Method not found: {method}"}
                    })
                    logger.debug(f"Responding (Error): {response}")
                    return response
                return None
                
            response = json.dumps({
                "jsonrpc": "2.0",
                "id": req.get("id"),
                "result": result
            })
            logger.debug(f"Responding: {response}")
            return response
    except Exception as e:
        logger.exception("Error processing message")
        return json.dumps({
            "jsonrpc": "2.0",
            "error": {"code": -32000, "message": str(e)}
        })
    return None

def run_server():
    """Start standard IO JSON-RPC loop."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = process_message(line)
        if response:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()
