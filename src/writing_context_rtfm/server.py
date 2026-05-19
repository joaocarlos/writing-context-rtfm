"""MCP server logic."""
import sys
import json
import os
import traceback
from dataclasses import asdict
from typing import Any, Dict, Optional

from writing_context_rtfm.config import load_config
from writing_context_rtfm.section_cards import load_section_cards, validate_section_cards
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.proofread import ProofreadPackGenerator
import logging

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
        ]
    }

def _load_runtime():
    """Load config, section cards, adapter, store. Returns (config, cards, card_warnings, adapter, store).

    Section-card validation issues are returned as warnings (not exceptions) so
    they surface in the pack's `warnings` field instead of aborting the call.
    """
    config = load_config()
    cards = load_section_cards(config.section_cards.path, required=config.section_cards.required)
    card_warnings = validate_section_cards(cards) if cards else []
    adapter = RTFMAdapter()
    store = ExtensionStore(config.cache.path)
    store.init_db()
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

    try:
        pack = generator.generate(task, target, budget, must_consider)
    except Exception as e:
        logger.exception("Pack generation failed")
        return _error_response(ERROR_RETRIEVAL, "Context pack generation failed.", type(e).__name__)

    payload = asdict(pack)
    if card_warnings:
        payload.setdefault("warnings", [])
        payload["warnings"].extend(card_warnings)
        if payload.get("status") == "complete":
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
    if card_warnings:
        payload["section_card_warnings"] = card_warnings
        if payload.get("status") == "complete":
            payload["status"] = "degraded"
    return _success_response(payload)


def handle_refresh_index(args):
    try:
        config = load_config()
    except Exception as e:
        logger.exception("Failed to load config for refresh_index")
        return _error_response(ERROR_CONFIG, "Failed to load configuration.", str(e))

    adapter = RTFMAdapter()
    project_root = args.get("project_root", config.rtfm.project_root)
    corpus = args.get("corpus", config.rtfm.corpus)

    try:
        success = adapter.sync(project_root, corpus=corpus)
    except Exception as e:
        logger.exception("RTFM sync raised")
        return _error_response(ERROR_RETRIEVAL, "RTFM sync raised an exception.", type(e).__name__)

    if not success:
        return _error_response(ERROR_RETRIEVAL, "RTFM sync failed.")

    if config.cache.invalidate_on_refresh:
        store = ExtensionStore(config.cache.path)
        store.invalidate_for_fingerprint("new_fingerprint_after_sync")
    return _success_response({"status": "ok", "cache_invalidated": config.cache.invalidate_on_refresh})

def process_message(line):
    try:
        logger.debug(f"Received: {line}")
        req = json.loads(line)
        if "method" in req:
            method = req["method"]
            result = None
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "writing-context-rtfm", "version": "0.1.0"},
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
                result = {"prompts": []}
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
