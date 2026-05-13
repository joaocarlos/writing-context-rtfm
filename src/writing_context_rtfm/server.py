"""MCP server logic."""
import sys
import json
from dataclasses import asdict

from writing_context_rtfm.config import load_config
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.context_pack import ContextPackGenerator

def get_tools_list():
    return {
        "tools": [
            {
                "name": "get_writing_context_pack",
                "description": "Generate a compact writing context pack for a specific task and section.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "The writing task description"},
                        "target": {"type": "string", "description": "The target section ID (optional)"},
                        "token_budget": {"type": "integer", "description": "Maximum token budget for source spans"},
                        "must_consider": {"type": "array", "items": {"type": "string"}, "description": "Files or concepts that must be considered"}
                    },
                    "required": ["task"]
                }
            },
            {
                "name": "refresh_index",
                "description": "Trigger a refresh of the RTFM index and invalidate cache.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_root": {"type": "string", "description": "Path to the project root"},
                        "corpus": {"type": "string", "description": "RTFM corpus name"}
                    }
                }
            }
        ]
    }

def handle_get_writing_context_pack(args):
    if not args or "task" not in args:
        return {"isError": True, "content": [{"type": "text", "text": "Missing required argument: task"}]}
        
    config = load_config()
    cards = load_section_cards(config.section_cards.path, required=config.section_cards.required)
    adapter = RTFMAdapter()
    store = ExtensionStore(config.cache.path)
    generator = ContextPackGenerator(config, cards, adapter, store)
    
    task = args.get("task", "")
    target = args.get("target")
    budget = args.get("token_budget", config.context.default_token_budget)
    must_consider = args.get("must_consider", [])
    
    try:
        pack = generator.generate(task, target, budget, must_consider)
        return {
            "content": [{"type": "text", "text": json.dumps(asdict(pack))}]
        }
    except Exception as e:
        return {"isError": True, "content": [{"type": "text", "text": str(e)}]}

def handle_refresh_index(args):
    config = load_config()
    adapter = RTFMAdapter()
    project_root = args.get("project_root", config.rtfm.project_root)
    corpus = args.get("corpus", config.rtfm.corpus)
    
    success = adapter.sync(project_root, corpus=corpus)
    if success:
        if config.cache.invalidate_on_refresh:
            store = ExtensionStore(config.cache.path)
            store.invalidate_for_fingerprint("new_fingerprint_after_sync")
        return {
            "content": [{"type": "text", "text": json.dumps({"status": "ok", "cache_invalidated": config.cache.invalidate_on_refresh})}]
        }
    else:
        return {"isError": True, "content": [{"type": "text", "text": "RTFM sync failed."}]}

def process_message(line):
    try:
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
            elif method == "tools/call":
                params = req.get("params", {})
                name = params.get("name")
                args = params.get("arguments", {})
                if name == "get_writing_context_pack":
                    result = handle_get_writing_context_pack(args)
                elif name == "refresh_index":
                    result = handle_refresh_index(args)
                else:
                    return json.dumps({
                        "jsonrpc": "2.0",
                        "id": req.get("id"),
                        "error": {"code": -32601, "message": f"Tool not found: {name}"}
                    })
            else:
                return None
                
            return json.dumps({
                "jsonrpc": "2.0",
                "id": req.get("id"),
                "result": result
            })
    except Exception as e:
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
