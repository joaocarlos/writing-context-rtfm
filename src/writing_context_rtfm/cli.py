"""CLI module."""

import argparse
import contextlib
import json
import shutil
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from writing_context_rtfm import __version__
from writing_context_rtfm.config import load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.features import get_term_context
from writing_context_rtfm.hashing import compute_rtfm_fingerprint
from writing_context_rtfm.proofread import ProofreadPackGenerator
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import ContextPack
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.server import run_server
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.utils import resolve_rtfm_db_path


def _update_gitignore(root: Path) -> None:
    gitignore_file = root / ".gitignore"
    cache_path = ".writing-context/context_cache.sqlite"

    if not gitignore_file.exists():
        gitignore_file.write_text(cache_path + "\n", encoding="utf-8")
        print(f"Created {gitignore_file} with {cache_path} ignored.")
        return

    content = gitignore_file.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines()]

    ignored = False
    for line in lines:
        clean = line.split("#")[0].strip()
        if clean in (
            cache_path,
            ".writing-context/*.sqlite",
            ".writing-context/",
            ".writing-context/*",
        ):
            ignored = True
            break

    if not ignored:
        if content and not content.endswith("\n"):
            content += "\n"
        content += cache_path + "\n"
        gitignore_file.write_text(content, encoding="utf-8")
        print(f"Appended {cache_path} to {gitignore_file}")


def _update_mcp_json(root: Path) -> None:
    mcp_file = root / ".mcp.json"

    if (root / "uv.lock").exists():
        server_def = {"command": "uv", "args": ["run", "writing-context-rtfm", "serve"]}
    else:
        server_def = {"command": "writing-context-rtfm", "args": ["serve"]}

    mcp_data = {}
    if mcp_file.exists():
        try:
            mcp_data = json.loads(mcp_file.read_text(encoding="utf-8"))
            if not isinstance(mcp_data, dict):
                mcp_data = {}
        except Exception as e:
            print(f"Warning: Failed to parse existing {mcp_file}: {e}. Overwriting/re-creating.")
            mcp_data = {}

    mcp_data.setdefault("mcpServers", {})
    mcp_data["mcpServers"]["writing-context-rtfm"] = server_def

    try:
        mcp_file.write_text(json.dumps(mcp_data, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {mcp_file} with writing-context-rtfm MCP server configuration.")
    except Exception as e:
        print(f"Warning: Failed to write {mcp_file}: {e}")


def _update_markdown_rules(root: Path, file_name: str, default_title: str) -> None:
    md_file = root / file_name

    anchor_start = "<!-- writing-context-rtfm MCP tools -->"
    anchor_end = "<!-- end writing-context-rtfm MCP tools -->"

    rules = (
        f"{anchor_start}\n"
        "## Agent Rules of Thumb for Writing Context\n\n"
        "1. **Retrieve Curated Context First**: Call `get_writing_context_pack` or `get_proofreading_context_pack` before writing, rewriting, expanding, adapting, or compressing text to obtain section constraints, thesis, terminology definitions, and 1-hop reference graph snippets.\n"
        "2. **Autonomous Direct-Read Fallback**: If you need continuous prose flow, full-chapter narrative context, or the returned context pack is truncated, you are fully authorized to read the target and dependency files directly after inspecting the pack.\n"
        "3. **Specify Task, Mode & Depth**: Use `mode` (`write`, `rewrite`, `adapt`, `compress`), `task_type`, `pack_mode`, and `include_diagnostics` parameters when calling `get_writing_context_pack` to optimize context weightings and token budgets.\n"
        "4. **Respect Formatting Boundaries**: Pay attention to safety warnings in the pack. Never alter detected LaTeX citations (`\\cite`), Pandoc keys (`[@key]`), labels (`\\label`), references (`\\ref`), or math environments (`align`, `$$`).\n"
        '5. **Card Management & Terminology**: Use `manage_section_cards` with `action="term"` or `action="audit"` for glossary lookups, and `action="init"`, `action="review"`, `action="accept"`, `action="reject"`, or `action="edit"` to scaffold and maintain section cards.\n'
        '6. **Inspect Graph & Section Details**: Use `manage_section_cards` with `action="inspect"` (section details), `action="graph"` (reference graph), `action="diff"` (overrides diff), `action="history"` (audit log), or `action="explain"` (candidate provenance).\n'
        "7. **Handle Pagination**: If you need extra background spans, call `request_more_context` with the `run_id`.\n"
        "8. **Log Feedback**: Always evaluate retrieved context using `submit_generation_feedback` so subsequent caching is optimized.\n"
        f"{anchor_end}"
    )

    if not md_file.exists():
        content = f"# {default_title}\n\n{rules}\n"
        try:
            md_file.write_text(content, encoding="utf-8")
            print(f"Created {md_file} with Agent Rules of Thumb.")
        except Exception as e:
            print(f"Warning: Failed to write {md_file}: {e}")
        return

    try:
        content = md_file.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Warning: Failed to read existing {md_file}: {e}")
        return

    if anchor_start in content and anchor_end in content:
        start_idx = content.find(anchor_start)
        end_idx = content.find(anchor_end) + len(anchor_end)
        new_content = content[:start_idx] + rules + content[end_idx:]
        try:
            md_file.write_text(new_content, encoding="utf-8")
            print(f"Updated Agent Rules of Thumb in {md_file}")
        except Exception as e:
            print(f"Warning: Failed to update {md_file}: {e}")
    else:
        if content and not content.endswith("\n"):
            content += "\n"
        content += "\n" + rules + "\n"
        try:
            md_file.write_text(content, encoding="utf-8")
            print(f"Appended Agent Rules of Thumb to {md_file}")
        except Exception as e:
            print(f"Warning: Failed to append to {md_file}: {e}")


def _update_claude_settings(root: Path) -> None:
    claude_dir = root / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_file = claude_dir / "settings.json"

    settings_data = {}
    if settings_file.exists():
        try:
            settings_data = json.loads(settings_file.read_text(encoding="utf-8"))
            if not isinstance(settings_data, dict):
                settings_data = {}
        except Exception as e:
            print(
                f"Warning: Failed to parse existing {settings_file}: {e}. Overwriting/re-creating."
            )
            settings_data = {}

    # Ensure writing-context-rtfm is in enabledMcpjsonServers
    enabled_servers = settings_data.setdefault("enabledMcpjsonServers", [])
    if not isinstance(enabled_servers, list):
        enabled_servers = []
        settings_data["enabledMcpjsonServers"] = enabled_servers
    if "writing-context-rtfm" not in enabled_servers:
        enabled_servers.append("writing-context-rtfm")

    hooks = settings_data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        settings_data["hooks"] = hooks

    post_tool_use = hooks.setdefault("PostToolUse", [])
    if not isinstance(post_tool_use, list):
        post_tool_use = []
        hooks["PostToolUse"] = post_tool_use

    # Check if our hook already exists
    hook_exists = False
    for hook_entry in post_tool_use:
        if not isinstance(hook_entry, dict):
            continue
        inner_hooks = hook_entry.get("hooks", [])
        if not isinstance(inner_hooks, list):
            continue
        for inner in inner_hooks:
            if not isinstance(inner, dict):
                continue
            if (
                inner.get("type") == "mcp_tool"
                and inner.get("server") == "writing-context-rtfm"
                and inner.get("tool") == "refresh_index"
            ):
                # Ensure input key is used instead of deprecated arguments key
                if "arguments" in inner and "input" not in inner:
                    inner["input"] = inner.pop("arguments")
                elif "input" not in inner:
                    inner["input"] = {}
                hook_exists = True
                break
        if hook_exists:
            break

    if not hook_exists:
        new_hook = {
            "matcher": "write_to_file|replace_file_content|multi_replace_file_content|write_file|edit_file|edit_file_content|save_file|create_file",
            "hooks": [
                {
                    "type": "mcp_tool",
                    "server": "writing-context-rtfm",
                    "tool": "refresh_index",
                    "input": {},
                }
            ],
        }
        post_tool_use.append(new_hook)

    # SessionEnd hooks
    cleanup_cmd = "writing-context-rtfm cleanup"
    if (root / "uv.lock").exists():
        cleanup_cmd = "uv run writing-context-rtfm cleanup"

    session_end = hooks.setdefault("SessionEnd", [])
    if not isinstance(session_end, list):
        session_end = []
        hooks["SessionEnd"] = session_end

    sanitized_session_end = []
    session_hook_exists = False
    for entry in session_end:
        if not isinstance(entry, dict):
            continue
        if "hooks" in entry and isinstance(entry["hooks"], list):
            for inner in entry["hooks"]:
                if (
                    isinstance(inner, dict)
                    and inner.get("type") == "command"
                    and inner.get("command")
                    in ("writing-context-rtfm cleanup", "uv run writing-context-rtfm cleanup")
                ):
                    inner["command"] = cleanup_cmd
                    session_hook_exists = True
            sanitized_session_end.append(entry)
        elif entry.get("type") == "command" and entry.get("command") in (
            "writing-context-rtfm cleanup",
            "uv run writing-context-rtfm cleanup",
        ):
            sanitized_session_end.append({"hooks": [{"type": "command", "command": cleanup_cmd}]})
            session_hook_exists = True
        else:
            sanitized_session_end.append(entry)

    if not session_hook_exists:
        sanitized_session_end.append({"hooks": [{"type": "command", "command": cleanup_cmd}]})

    hooks["SessionEnd"] = sanitized_session_end

    try:
        settings_file.write_text(json.dumps(settings_data, indent=2) + "\n", encoding="utf-8")
        print(
            f"Updated {settings_file} with writing-context-rtfm PostToolUse and SessionEnd hooks."
        )
    except Exception as e:
        print(f"Warning: Failed to write {settings_file}: {e}")


def _update_codex_config() -> None:
    codex_dir = Path.home() / ".codex"
    if not codex_dir.exists():
        return
    config_file = codex_dir / "config.toml"
    if not config_file.exists():
        return

    try:
        content = config_file.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Warning: Failed to read {config_file}: {e}")
        return

    if "[mcp_servers.writing-context-rtfm]" not in content:
        return

    binary_path = shutil.which("writing-context-rtfm") or "writing-context-rtfm"
    lines = content.splitlines()
    new_lines = []
    in_wc_block = False
    updated = False
    for line in lines:
        if line.strip() == "[mcp_servers.writing-context-rtfm]":
            in_wc_block = True
            new_lines.append(line)
            continue
        elif in_wc_block and line.startswith("["):
            in_wc_block = False

        if in_wc_block and line.strip().startswith("command ="):
            current_cmd = line.split("=", 1)[1].strip().strip("\"'")
            if ".venv" in current_cmd or not Path(current_cmd).exists():
                new_lines.append(f'command = "{binary_path}"')
                updated = True
                continue
        new_lines.append(line)

    if updated:
        try:
            config_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            print(f"Auto-repaired stale writing-context-rtfm command in {config_file}")
        except Exception as e:
            print(f"Warning: Failed to update {config_file}: {e}")


def init_command(args: argparse.Namespace) -> None:
    """Creates .writing-context/ directory with sample config and section cards if missing."""
    root = Path(getattr(args, "project_root", ".")).resolve()
    wc = root / ".writing-context"
    wc.mkdir(exist_ok=True)
    config_file = wc / "config.yaml"
    sc_file = wc / "cards.overrides.yaml.example"
    if not config_file.exists():
        from writing_context_rtfm.providers.discovery import autodiscover_local_mcps

        discovered = autodiscover_local_mcps(str(root))

        def _format_mcp_server_config(mcp_server: dict[str, Any]) -> str:
            cmd = mcp_server.get("command", "")
            args_list = mcp_server.get("args") or []
            env = mcp_server.get("env")
            lines = [
                "    mcp_server:",
                f"      command: {cmd}",
                f"      args: {json.dumps(args_list)}",
            ]
            if env:
                lines.append("      env:")
                for k, v in env.items():
                    lines.append(f"        {k}: {json.dumps(v)}")
            return "\n".join(lines)

        zotero_cfg = discovered.get("zotero")
        if zotero_cfg:
            zotero_block = (
                "  zotero:\n"
                "    enabled: true\n"
                f"{_format_mcp_server_config(zotero_cfg['mcp_server'])}\n"
                "    extra:\n"
                '      library_name: "My Library"\n'
                "      collections: []\n"
                "      include_subcollections: true\n"
                "      include_abstract: false\n"
                "      similarity_threshold: -0.4"
            )
        else:
            zotero_block = (
                "  zotero:\n"
                "    enabled: false\n"
                "    mcp_server:\n"
                "      command: zotero-mcp\n"
                '      args: ["serve"]\n'
                "    extra:\n"
                '      library_name: "My Library"\n'
                "      collections: []\n"
                "      include_subcollections: true\n"
                "      include_abstract: false\n"
                "      similarity_threshold: -0.4"
            )

        openai_block = (
            "  openai_semantic:\n"
            "    enabled: false\n"
            "    # Set OPENAI_API_KEY environment variable to use this\n"
            '    model: "text-embedding-3-small"\n'
            "    # By default, embeddings are generated lazily when needed. Set to true to embed all files on sync.\n"
            "    auto_sync: false\n"
        )
        providers_block = f"providers:\n{openai_block}{zotero_block}"

        template = (
            "# writing-context-rtfm Configuration File\n"
            "version: 1\n\n"
            "# RTFM core indexing settings\n"
            "rtfm:\n"
            "  # The corpus name registered in RTFM for this manuscript project.\n"
            "  corpus: default\n"
            "  # Auto-sync is opt-in; use refresh_index explicitly for predictable foreground work.\n"
            "  # sync_before_pack: false\n\n"
            "# Context pack generation settings\n"
            "# context:\n"
            "  # The default input token budget for generating writing context packs.\n"
            "  # default_token_budget: 12000\n"
            "  # The margin (percentage as decimal) of the budget reserved for LLM response generation.\n"
            "  # reserved_generation_margin: 0.10\n"
            "  # Maximum number of source spans to include in a context pack.\n"
            "  # max_source_spans: 35\n"
            "  # Minimum similarity score required for a retrieved span to be considered.\n"
            "  # min_score: 0.01\n"
            "  # Distribution of the token budget among different types of context roles:\n"
            "  # role_budgets:\n"
            "  #   target_text: 0.35\n"
            "  #   local_context: 0.15\n"
            "  #   dependency: 0.30\n"
            "  #   reference: 0.20\n\n"
            "# Caching configuration\n"
            "# cache:\n"
            "  # Enable caching of generated context packs to reduce token and response overhead.\n"
            "  # enabled: true\n"
            "  # Invalidate cached context packs when the index is synced/refreshed.\n"
            "  # invalidate_on_refresh: true\n\n"
            "# Card scaffolding generator configuration\n"
            "# generator:\n"
            "  # The model to use (e.g. gpt-4o-mini, Qwen/Qwen2.5-Coder-7B-Instruct, phi3)\n"
            "  # model: gpt-4o-mini\n"
            "  # The API endpoint base URL\n"
            "  # api_base: https://api.openai.com/v1\n\n"
            "# External context providers configuration (Zotero)\n"
            f"{providers_block}\n"
        )
        config_file.write_text(template, encoding="utf-8")
        print(f"Created {config_file}")
    if not sc_file.exists() and not (wc / "cards.overrides.yaml").exists():
        template_sc = (
            "# writing-context-rtfm Section Cards Overrides Template\n"
            "# Use this file to define manual overrides for document settings and section metadata.\n"
            "version: 2\n\n"
            "# Document-level global context overrides\n"
            "document:\n"
            "  # The title of your project or paper\n"
            '  title: "A New Approach to Manuscript Curation"\n'
            "  # The central thesis statement (injected globally to keep the agent focused)\n"
            '  thesis: "Surgical context selection using a gatekeeping protocol reduces LLM token overhead and improves writing accuracy."\n'
            "  # Global writing style guide\n"
            "  writing_style:\n"
            '    tone: "Academic, precise, concise, third-person"\n'
            '    avoid_words: ["cliché", "groundbreaking", "revolutionary", "game-changing"]\n'
            "  # Global project terminology dictionary\n"
            "  terminology:\n"
            '    Context Pack: "A compact JSON structure containing prioritized source spans, token estimates, and constraints."\n'
            "    RTFM:\n"
            '      definition: "Read The Fine Manual: A semantic retrieval and indexing tool."\n'
            '      variants: ["rtfm-ai", "RTFM CLI"]\n'
            '      avoid: ["RTFM database write", "modifying library.db"]\n\n'
            "# Section overrides definitions. Edit these to override generated metadata.\n"
            "sections:\n"
            "  section_abstract:\n"
            '    title: "Abstract"\n'
            '    purpose: "Provide a standalone, 150-word summary of the thesis, approach, and primary results."\n'
            '    path: "sections/00_abstract.tex"  # Relative path to your draft file\n'
            '    key_terms: ["Surgical context", "Gatekeeping protocol"]\n'
            "    depends_on: []                    # Abstract usually has no direct dependencies\n"
            "    must_preserve: []                 # Specific claims or equations that must not change\n"
            '    avoid: ["detailed experimental setups", "citations"]\n'
            "    constraints:\n"
            '      - "Exactly one paragraph"\n'
            '      - "Max 150 words"\n\n'
            "  section_introduction:\n"
            '    title: "Introduction"\n'
            '    purpose: "Establish the problem context, outline the research gap, and state the main contributions."\n'
            '    path: "sections/01_introduction.tex"\n'
            '    key_terms: ["LLM token overhead", "context curation"]\n'
            "    depends_on:\n"
            "      - section_abstract             # Tells the agent to look at the abstract first\n"
            "    must_preserve: []\n"
            '    avoid: ["premature methodology details"]\n'
            "    constraints:\n"
            '      - "Ensure main contributions are listed as a bulleted list"\n\n'
            "  section_methodology:\n"
            '    title: "Proposed Methodology"\n'
            '    purpose: "Detail the system architecture, mathematical formulations, and context selection algorithms."\n'
            '    path: "sections/02_methodology.tex"\n'
            '    key_terms: ["Gatekeeping protocol", "Token budget"]\n'
            "    depends_on:\n"
            "      - section_introduction\n"
            "    must_preserve:\n"
            '      - "Token budget formula is B_usable = B_total * (1 - margin)"\n'
            "    avoid: []\n"
            "    constraints:\n"
            '      - "Write equations using LaTeX align or equation environments"\n'
        )
        sc_file.write_text(template_sc, encoding="utf-8")
        print(f"Created {sc_file}")

    # 1. Update .gitignore
    _update_gitignore(root)

    # 2. Update .mcp.json
    _update_mcp_json(root)

    # 3. Update markdown rule files
    _update_markdown_rules(root, "CLAUDE.md", "Developer & Agent Guidelines (CLAUDE.md)")
    _update_markdown_rules(root, "AGENTS.md", "Agent Guidelines (AGENTS.md)")
    _update_markdown_rules(root, "GEMINI.md", "Gemini Agent Guidelines (GEMINI.md)")

    # 4. Update Claude/Codex hooks configuration
    _update_claude_settings(root)

    # 5. Check and repair Codex global config.toml if present
    _update_codex_config()

    if getattr(args, "quickstart", False):
        print("\n[*] Quickstart: Bootstrapping manuscript section cards and index...")
        try:
            from writing_context_rtfm.features import cards_scan_command

            scan_res = cards_scan_command(str(root))
            sections_found = scan_res.get("sections_found", 0)
            print(f"[*] Scanned manuscript structure: {sections_found} section(s) discovered.")
        except Exception as e:
            print(f"[*] Note: Card scanning deferred ({e})")

        try:
            from writing_context_rtfm.rtfm_adapter import RTFMAdapter

            adapter = RTFMAdapter(project_root=str(root))
            print("[*] Synchronizing manuscript files into RTFM retrieval index...")
            adapter.sync(capture_output=True)
            print("[*] Retrieval index synchronized successfully.")
        except Exception as e:
            print(
                f"[*] Note: RTFM index sync deferred ({e}). You can run 'writing-context-rtfm sync' at any time."
            )
        print("\n[OK] Quickstart complete! Your manuscript is ready for writing-context retrieval.")


def init_cards_command(args: argparse.Namespace) -> None:
    """Scans the workspace and generates or appends section cards."""
    project_root = getattr(args, "project_root", ".")
    try:
        from writing_context_rtfm.features import initialize_section_cards

        res = initialize_section_cards(project_root)
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def init_db_command(args: argparse.Namespace) -> None:
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)
    with ExtensionStore(config.cache.path) as store:
        store.init_db()
    print(f"Initialized database at {config.cache.path}")


def sync_command(args: argparse.Namespace) -> None:
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)
    adapter = RTFMAdapter(project_root=str(Path(project_root).resolve()))

    if args.path == "." and args.corpus is None:
        sync_path = None
        corpus = None
    else:
        sync_path = args.path
        corpus = args.corpus

    try:
        print("[*] Synchronizing RTFM database and generating embeddings...")
        adapter.sync(sync_path, corpus=corpus, capture_output=False)
        with ExtensionStore(config.cache.path) as store:
            store.init_db()

            # Compute real library.db fingerprint after sync
            rtfm_db = resolve_rtfm_db_path(Path(project_root))
            fingerprint = compute_rtfm_fingerprint(rtfm_db)

            store.invalidate_for_fingerprint(fingerprint)

            from writing_context_rtfm.providers import get_active_providers

            for provider in get_active_providers(config):
                if provider.provider_id == "openai_semantic":
                    provider_cfg = config.providers.get("openai_semantic")
                    auto_sync = False
                    if provider_cfg is not None:
                        if isinstance(provider_cfg, dict):
                            auto_sync = bool(provider_cfg.get("auto_sync", False))
                        else:
                            extra = provider_cfg.extra or {}
                            auto_sync = bool(extra.get("auto_sync", False))
                    if auto_sync and hasattr(provider, "sync_chunks"):
                        print("[*] Synchronizing OpenAI semantic embeddings...")
                        provider.sync_chunks(store, str(rtfm_db))

        print("Sync completed successfully.")
    except Exception as e:
        print(f"Sync failed: {e}", file=sys.stderr)
        sys.exit(1)


def cache_command(args: argparse.Namespace) -> None:
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)
    with ExtensionStore(config.cache.path) as store:
        store.init_db()

        if args.cache_action == "clear":
            store.clear()
            print("Cache cleared successfully.")
        elif args.cache_action == "stats":
            with store._connect() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM context_pack_runs")
                run_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM context_pack_sources")
                source_count = cursor.fetchone()[0]

            db_file = Path(config.cache.path)
            db_size = db_file.stat().st_size if db_file.exists() else 0

            print(f"Cache location: {config.cache.path}")
            print(f"Total runs:     {run_count}")
            print(f"Total sources:  {source_count}")
            print(f"File size:      {db_size} bytes")


def _print_pack_explanation(pack: ContextPack, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(asdict(pack), indent=2))
        return

    diag = pack.diagnostics
    if not diag:
        print(json.dumps(asdict(pack), indent=2))
        return

    funnel = diag.funnel
    print("=== Candidate Funnel ===")
    print(f"  Retrieved:     {funnel.retrieved}")
    print(f"  Normalized:    {funnel.normalized}")
    print(f"  Deduplicated:  {funnel.deduplicated}")
    print(f"  Excluded:      {funnel.excluded}")
    print(f"  Exposed:       {funnel.exposed}")
    print(f"  Filtered:      {funnel.filtered}")
    print(f"  Eligible:      {funnel.eligible}")
    print(f"  Selected:      {funnel.selected}\n")

    print(f"=== Selected Spans ({len(pack.source_spans)}) ===")
    for i, s in enumerate(pack.source_spans, 1):
        pos = f"{s.path}:{s.line_start or 1}-{s.line_end or '?'}"
        print(f"  [{i}] {pos} (role={s.source_role}, score={s.score:.2f}) -> {s.reason}")
    print()

    if diag.rejections_by_reason:
        print("=== Rejections by Reason ===")
        for reason, count in sorted(diag.rejections_by_reason.items()):
            print(f"  {reason}: {count}")
        print()

    if diag.ownership_audit:
        print(f"=== Excluded by Provider Ownership ({len(diag.ownership_audit)}) ===")
        for rec in diag.ownership_audit:
            pos = f"{rec.path}:{rec.line_start or 1}-{rec.line_end or '?'}"
            rep = (
                f"replaced by {rec.replacement_provider}"
                if rec.replacement_found
                else "no replacement"
            )
            print(f"  {pos} ({', '.join(rec.identities) if rec.identities else 'no id'}) -> {rep}")
        print()

    if pack.quality and isinstance(pack.quality, dict) and "tokenomics" in pack.quality:
        tok = pack.quality["tokenomics"]
        print("=== Tokenomics & Counterfactual Audit ===")
        print(f"  Model Family:               {tok.get('model_family', 'generic')}")
        b_mode = tok.get("baseline_mode", "section_neighborhood")
        real_tok = tok.get("baseline_realistic_tokens", tok.get("baseline_document_tokens", 0))
        raw_tok = tok.get("baseline_tokens_raw", real_tok)
        is_capped = tok.get("is_capped", False)
        capped_note = f" [Capped at doc ceiling; raw: {raw_tok:,} tok]" if is_capped else ""
        print(f"  Realistic Baseline ({b_mode}): {real_tok:,} tok{capped_note}")
        print(f"  Naive Full-Doc Baseline:    {tok.get('baseline_document_tokens', 0):,} tok")
        print(f"  Context Pack Tokens:        {tok.get('pack_tokens', 0):,}")
        if tok.get("instruction_tokens"):
            print(f"  Instruction Prompt Tokens:  {tok.get('instruction_tokens'):,} tok")
        print(f"  MCP Schema Overhead:        +{tok.get('schema_overhead_tokens', 0):,} tok")
        print(f"  Effective Pack Cost:        {tok.get('effective_pack_cost', 0):,} tok")
        real_saved = tok.get("realistic_tokens_saved", tok.get("tokens_saved", 0))
        real_pct = tok.get("realistic_savings_percentage", tok.get("savings_percentage", 0.0))
        print(f"  Realistic Savings (truth):  {real_saved:,} tok ({real_pct:.1f}% reduction)")
        naive_saved = tok.get("tokens_saved", 0)
        naive_pct = tok.get("savings_percentage", 0.0)
        print(f"  Naive Savings (ceiling):    {naive_saved:,} tok ({naive_pct:.1f}% reduction)")
        if "preflight" in tok and isinstance(tok["preflight"], dict):
            pf = tok["preflight"]
            feas = "FEASIBLE" if pf.get("feasible") else "DEFICIT WARNING"
            print(
                f"  Pre-flight Budget:          {feas} (Fixed: {pf.get('fixed_tokens', 0)} tok, Elastic: {pf.get('available_elastic_tokens', 0)} tok)"
            )
            sess = pf.get("session")
            if sess and isinstance(sess, dict):
                calls_used = sess.get("session_calls_used", 0)
                msg_limit = sess.get("session_message_limit", 25)
                rem_calls = sess.get(
                    "calls_remaining_by_message_limit", max(0, msg_limit - calls_used)
                )
                b_cause = sess.get("bottleneck_cause", "message_limit")
                print(
                    f"  5h Rolling Session:         {calls_used}/{msg_limit} msgs (Remaining: {rem_calls} msgs | Bottleneck: {b_cause})"
                )
        print()

    print("=== Summary ===")
    print(f"  Status:           {pack.status}")
    print(f"  Estimated Tokens: {pack.estimated_tokens}")
    if pack.warnings:
        print(f"  Warnings:         {len(pack.warnings)}")
        for w in pack.warnings:
            print(f"    - {w}")


def pack_command(args: argparse.Namespace) -> None:
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)

    # Override profile from CLI if provided
    if getattr(args, "profile", None):
        from writing_context_rtfm.config import apply_profile

        config = apply_profile(config, args.profile)

    # Override corpus from CLI if provided
    if getattr(args, "corpus", None):
        config = replace(config, rtfm=replace(config.rtfm, corpus=args.corpus))

    sc_path = config.section_cards.path
    cards = load_section_cards(sc_path, required=config.section_cards.required)

    adapter = RTFMAdapter(project_root=str(Path(project_root).resolve()))
    with ExtensionStore(config.cache.path) as store:
        store.init_db()
        from writing_context_rtfm.providers import get_active_providers, get_active_reranker

        providers = get_active_providers(config)
        reranker = get_active_reranker(config, store=store)
        generator = ContextPackGenerator(
            config, cards, adapter, store, providers=providers, reranker=reranker
        )

        role_budgets = None
        if getattr(args, "role_budgets", None):
            try:
                role_budgets = json.loads(args.role_budgets)
                if not isinstance(role_budgets, dict):
                    print("Error: --role-budgets must be a JSON dictionary.", file=sys.stderr)
                    sys.exit(1)
                role_budgets = {str(k): float(v) for k, v in role_budgets.items()}
            except Exception as e:
                print(f"Error parsing --role-budgets JSON: {e}", file=sys.stderr)
                sys.exit(1)

        include_diagnostics = (
            getattr(args, "explain", False) or getattr(args, "command", "") == "explain-pack"
        )

        pack = generator.generate(
            task=args.task,
            target=args.target,
            token_budget=args.budget,
            must_consider=args.must_consider or [],
            project_root=project_root,
            task_type=getattr(args, "task_type", None),
            line_start=getattr(args, "line_start", None),
            line_end=getattr(args, "line_end", None),
            pack_mode=getattr(args, "pack_mode", None),
            role_budgets=role_budgets,
            include_diagnostics=include_diagnostics,
            mode=getattr(args, "mode", None),
            git_diff=getattr(args, "git_diff", False),
        )
    if include_diagnostics:
        _print_pack_explanation(pack, as_json=getattr(args, "json", False))
    else:
        print(json.dumps(asdict(pack), indent=2))


def explain_pack_command(args: argparse.Namespace) -> None:
    pack_command(args)


def _render_pack_preview(pack: ContextPack, config: Any, no_color: bool = False) -> None:
    from writing_context_rtfm.server import _format_write_section_prompt

    lines: list[str] = []
    w = 80

    lines.append("=" * w)
    lines.append("WRITING CONTEXT PACK PREVIEW".center(w))
    lines.append("=" * w)
    lines.append(f"Task:         {pack.task}")
    lines.append(f"Target:       {pack.target or 'General / Whole Document'}")
    lines.append(f"Mode:         {pack.mode}")
    lines.append(f"Profile:      {config.profile}")
    lines.append(f"Status:       {pack.status}")
    budget_val = (pack.quality or {}).get("budget")
    if budget_val:
        lines.append(f"Token Budget: {budget_val}  (Estimated Tokens: {pack.estimated_tokens})")
    else:
        lines.append(f"Tokens:       {pack.estimated_tokens} estimated")
    if (pack.quality or {}).get("git_diff_active"):
        mod_files = (pack.quality or {}).get("git_modified_files") or []
        lines.append(f"Git-Diff:     Active ({len(mod_files)} modified file(s))")
    if (pack.quality or {}).get("custom_macros"):
        macro_count = len((pack.quality or {}).get("custom_macros") or {})
        lines.append(f"LaTeX Macros: {macro_count} custom macro(s) protected")
    lines.append("-" * w)

    if pack.document_thesis:
        lines.append(f"Thesis:       {pack.document_thesis}")
    target_role = getattr(pack, "target_role", None)
    if target_role:
        lines.append(f"Section Role: {target_role}")
    if pack.constraints:
        lines.append(f"Constraints ({len(pack.constraints)}):")
        for c in pack.constraints:
            lines.append(f"  • {c}")
    lines.append("-" * w)

    lines.append(f"SOURCE SPANS ({len(pack.source_spans)} selected):")
    if pack.source_spans:
        lines.append(f"{'#':<3} {'Role / Tier':<22} {'Lines':<10} {'Tokens':<8} {'Path & Reason'}")
        lines.append("-" * w)
        for idx, s in enumerate(pack.source_spans, 1):
            role_tier = s.source_role
            if s.metadata and s.metadata.get("tier") is not None:
                role_tier += f" [T{s.metadata['tier']}]"
            elif getattr(s, "is_explicit_citation", False):
                role_tier += " [Tier 1]"
            elif s.source_role == "target_text":
                role_tier += " [Tier 0]"

            lines_str = f"L{s.line_start}-L{s.line_end}" if s.line_start else "-"
            tokens_val = (
                getattr(s, "tokens", None)
                or (s.metadata or {}).get("tokens")
                or (s.metadata or {}).get("estimated_tokens", "-")
            )
            tokens_str = str(tokens_val)
            path_reason = f"{s.path} ({s.reason})"
            lines.append(f"{idx:<3} {role_tier:<22} {lines_str:<10} {tokens_str:<8} {path_reason}")
    else:
        lines.append("  (No source spans included)")
    lines.append("-" * w)

    if pack.diagnostics and pack.diagnostics.funnel:
        fn = pack.diagnostics.funnel
        lines.append(
            f"DIAGNOSTIC FUNNEL: {fn.retrieved} retrieved -> "
            f"{fn.deduplicated} deduplicated -> "
            f"{fn.eligible} eligible -> "
            f"{fn.selected} selected"
        )
        if pack.diagnostics.rejections_by_reason:
            rej_items = [f"{k}: {v}" for k, v in pack.diagnostics.rejections_by_reason.items()]
            lines.append(f"Rejections: {', '.join(rej_items)}")
        lines.append("-" * w)

    lines.append("RENDERED LLM PROMPT PREVIEW:")
    lines.append("-" * w)
    prompt_text = _format_write_section_prompt(pack)
    lines.append(prompt_text)
    lines.append("=" * w)

    print("\n".join(lines))


def preview_pack_command(args: argparse.Namespace) -> None:
    from writing_context_rtfm.config import apply_profile
    from writing_context_rtfm.server import _format_write_section_prompt

    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)

    # Override profile from CLI if provided
    if getattr(args, "profile", None):
        config = apply_profile(config, args.profile)

    # Override corpus from CLI if provided
    if getattr(args, "corpus", None):
        config = replace(config, rtfm=replace(config.rtfm, corpus=args.corpus))

    sc_path = config.section_cards.path
    cards = load_section_cards(sc_path, required=config.section_cards.required)

    adapter = RTFMAdapter(project_root=str(Path(project_root).resolve()))
    with ExtensionStore(config.cache.path) as store:
        store.init_db()
        from writing_context_rtfm.providers import get_active_providers, get_active_reranker

        providers = get_active_providers(config)
        reranker = get_active_reranker(config, store=store)
        generator = ContextPackGenerator(
            config, cards, adapter, store, providers=providers, reranker=reranker
        )

        role_budgets = None
        if getattr(args, "role_budgets", None):
            try:
                role_budgets = json.loads(args.role_budgets)
                role_budgets = {str(k): float(v) for k, v in role_budgets.items()}
            except Exception as e:
                print(f"Error parsing --role-budgets JSON: {e}", file=sys.stderr)
                sys.exit(1)

        pack = generator.generate(
            task=args.task,
            target=getattr(args, "target", None),
            token_budget=getattr(args, "budget", config.context.default_token_budget),
            must_consider=getattr(args, "must_consider", None) or [],
            project_root=project_root,
            task_type=getattr(args, "task_type", None),
            line_start=getattr(args, "line_start", None),
            line_end=getattr(args, "line_end", None),
            pack_mode=getattr(args, "pack_mode", None),
            role_budgets=role_budgets,
            include_diagnostics=True,
            mode=getattr(args, "mode", None),
            git_diff=getattr(args, "git_diff", False),
        )

    if getattr(args, "raw", False):
        print(_format_write_section_prompt(pack))
    else:
        _render_pack_preview(pack, config, no_color=getattr(args, "no_color", False))


def proofread_pack_command(args: argparse.Namespace) -> None:
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)

    cards = load_section_cards(config.section_cards.path, required=config.section_cards.required)
    adapter = RTFMAdapter(project_root=str(Path(project_root).resolve()))
    with ExtensionStore(config.cache.path) as store:
        store.init_db()
        generator = ProofreadPackGenerator(config, cards, adapter, store)

        pack = generator.generate(
            target_file=args.target_file,
            line_start=args.line_start,
            line_end=args.line_end,
            mode=args.mode,
            strictness=args.strictness,
            max_tokens=args.max_tokens,
        )
    print(json.dumps(asdict(pack), indent=2))


def get_term_command(args: argparse.Namespace) -> None:
    project_root = getattr(args, "project_root", ".")
    try:
        res = get_term_context(args.term, project_root)
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)


def auth_command(args: argparse.Namespace) -> None:
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)
    with ExtensionStore(config.cache.path) as store:
        store.init_db()
        store.set_provider_token(args.provider, args.token)
    print(f"Successfully saved API key for provider '{args.provider}' in local cache database.")


def serve_command(args: argparse.Namespace) -> None:
    run_server()


def doctor_command(args: argparse.Namespace) -> None:
    from writing_context_rtfm.doctor import (
        format_text_report,
        run_diagnostics,
        run_doctor_fix,
    )

    project_root = Path(getattr(args, "project_root", ".")).resolve()

    if getattr(args, "fix", False):
        actions = run_doctor_fix(project_root)
        if actions:
            print("Doctor Auto-Repair Applied:")
            for act in actions:
                print(f"  [+] {act}")
            print()
        else:
            print("No auto-repairs were needed.\n")

    report = run_diagnostics(project_root)

    if getattr(args, "json", False) is True:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_text_report(report))

    if report.has_critical_failures:
        sys.exit(1)


def inspect_target_command(args: argparse.Namespace) -> None:
    project_root = Path(getattr(args, "project_root", ".")).resolve()
    config = load_config(str(project_root))
    sc_path = Path(config.section_cards.path)
    if not sc_path.is_absolute():
        sc_path = project_root / sc_path

    if not sc_path.exists():
        print(f"Error: Section cards file not found at '{sc_path}'", file=sys.stderr)
        sys.exit(1)

    cards = load_section_cards(str(sc_path), required=True)
    target = args.target
    if not cards or not cards.sections or target not in cards.sections:
        print(f"Error: Section '{target}' not found in {sc_path}", file=sys.stderr)
        sys.exit(1)

    card = cards.sections[target]
    print(f"Target Section ID: {target}")
    print(f"Title:             {card.title}")
    print(f"Path:              {card.path}")
    print(f"Role:              {card.role}")
    print(f"Depends On:        {card.depends_on}")
    print(f"Key Terms:         {card.key_terms}")
    print(f"Must Preserve:     {getattr(card, 'must_preserve', [])}")
    print(f"Avoid:             {getattr(card, 'avoid', [])}")
    print(f"Constraints:       {getattr(card, 'constraints', [])}")


def show_graph_command(args: argparse.Namespace) -> None:
    from writing_context_rtfm.latex import build_reference_graph

    project_root = Path(args.project_root).resolve()

    # 1. Build LaTeX reference graph
    try:
        graph = build_reference_graph(str(project_root))
    except Exception as e:
        print(f"Error building LaTeX reference graph: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. Try loading section cards for showing section card dependencies
    cards = None
    try:
        config = load_config(str(project_root))
        sc_path = Path(config.section_cards.path)
        if not sc_path.is_absolute():
            sc_path = project_root / sc_path
        if sc_path.exists():
            cards = load_section_cards(str(sc_path), required=False)
    except Exception:
        pass

    if getattr(args, "format", "text") == "json":
        # Output as raw JSON if requested
        payload: dict[str, Any] = {"graph": graph, "sections": {}}
        if cards and cards.sections:
            for sid, scard in cards.sections.items():
                payload["sections"][sid] = {"path": scard.path, "depends_on": scard.depends_on}
        print(json.dumps(payload, indent=2))
        return

    # Text format output
    print("LaTeX Reference Graph & Section Dependencies")
    print("============================================")
    print(f"Project Root: {project_root}\n")

    print("LaTeX Files Scanned:")
    files = graph.get("files", [])
    if files:
        for f in sorted(files):
            print(f"  - {f}")
    else:
        print("  (No LaTeX files found)")
    print("")

    print("Defined Labels:")
    labels = graph.get("labels", {})
    if labels:
        for key in sorted(labels.keys()):
            info = labels[key]
            print(f"  - {key} (defined in {info.get('file')}:{info.get('line')})")
    else:
        print("  (No label definitions found)")
    print("")

    print("Cross-References & Citations:")
    references = graph.get("references", {})
    citations = graph.get("citations", {})

    has_refs_or_cites = False
    all_files = sorted(set(list(references.keys()) + list(citations.keys())))
    for f in all_files:
        file_refs = references.get(f, [])
        file_cites = citations.get(f, [])
        if file_refs or file_cites:
            has_refs_or_cites = True
            print(f"  - {f}:")
            if file_refs:
                print(f"    References: {', '.join(sorted(file_refs))}")
            if file_cites:
                print(f"    Citations:  {', '.join(sorted(file_cites))}")

    if not has_refs_or_cites:
        print("  (No cross-references or citations found)")
    print("")

    print("File Inclusions:")
    file_deps = graph.get("file_dependencies", {})
    has_inclusions = False
    for f in sorted(file_deps.keys()):
        inclusions = file_deps[f]
        if inclusions:
            has_inclusions = True
            print(f"  - {f} includes: {', '.join(sorted(inclusions))}")

    if not has_inclusions:
        print("  (No file inclusions found)")
    print("")

    print("Section Card Dependencies (section_cards.yaml):")
    if cards and cards.sections:
        for sid in sorted(cards.sections.keys()):
            scard = cards.sections[sid]
            deps = scard.depends_on or []
            print(f"  - {sid} ({scard.path}) depends on: {deps}")
    else:
        print("  (No section cards or section_cards.yaml not found/empty)")


def cleanup_command(args: argparse.Namespace) -> None:
    import json
    import os
    import time

    project_root = Path(getattr(args, "project_root", ".")).resolve()
    pid_file = project_root / ".writing-context" / "active_pids.json"

    if not pid_file.exists():
        print("No active processes to cleanup (active_pids.json not found).")
        return

    try:
        pids = json.loads(pid_file.read_text(encoding="utf-8"))
        if not isinstance(pids, list):
            pids = []
    except Exception as e:
        print(f"Error reading {pid_file}: {e}")
        pids = []

    if not pids:
        print("No active processes recorded in active_pids.json.")
        return

    print(f"Cleaning up {len(pids)} registered processes...")

    def terminate_process(pid: int) -> None:
        import sys

        if sys.platform == "win32":
            import subprocess

            with contextlib.suppress(Exception):
                subprocess.run(["taskkill", "/T", "/PID", str(pid)], capture_output=True)
            time.sleep(0.5)
            with contextlib.suppress(Exception):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        else:
            import signal

            # SIGTERM
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                print(f"Process {pid} already terminated.")
                return
            except Exception as ex:
                print(f"Error sending SIGTERM to {pid}: {ex}")

            # Wait up to 0.5s for process to die
            for _ in range(5):
                try:
                    os.kill(pid, 0)
                    time.sleep(0.1)
                except ProcessLookupError:
                    print(f"Process {pid} cleanly terminated.")
                    return

            # If still alive, SIGKILL
            try:
                os.kill(pid, signal.SIGKILL)
                print(f"Forcefully terminated process {pid} with SIGKILL.")
            except ProcessLookupError:
                print(f"Process {pid} terminated before SIGKILL.")
            except Exception as ex:
                print(f"Error sending SIGKILL to {pid}: {ex}")

    for pid in pids:
        try:
            terminate_process(pid)
        except Exception as e:
            print(f"Failed to cleanup process {pid}: {e}")

    # Write back empty list to clear
    try:
        pid_file.write_text(json.dumps([]), encoding="utf-8")
        print("Cleanup completed.")
    except Exception as e:
        print(f"Error writing empty list to {pid_file}: {e}")


def cards_command(args: argparse.Namespace) -> None:
    subcmd = args.subcommand
    project_root = getattr(args, "project_root", ".")

    from writing_context_rtfm.features import (
        cards_build_command,
        cards_infer_command,
        cards_rebuild_command,
        cards_review_command,
        cards_scan_command,
        cards_update_command,
        cards_validate_command,
    )

    try:
        if subcmd == "scan":
            res = cards_scan_command(project_root)
        elif subcmd == "infer":
            res = cards_infer_command(project_root, force=getattr(args, "force", False))
        elif subcmd == "review":
            res = cards_review_command(project_root)
        elif subcmd == "update":
            res = cards_update_command(
                project_root, changed_only=getattr(args, "changed_only", False)
            )
        elif subcmd == "validate":
            res = cards_validate_command(project_root)
        elif subcmd == "build":
            res = cards_build_command(project_root, review=getattr(args, "review", False))
        elif subcmd == "rebuild":
            res = cards_rebuild_command(project_root, review=getattr(args, "review", False))
        else:
            print(f"Error: Unknown cards subcommand '{subcmd}'")
            sys.exit(1)

        if res.get("status") == "error":
            print(f"Error: {res.get('message')}")
            sys.exit(1)
        elif res.get("status") == "warning":
            print(f"Warning: {res.get('message')}")
        else:
            print(json.dumps(res, indent=2))
    except Exception as e:
        from writing_context_rtfm.semantic_extractor import MissingAPIKeyError

        if isinstance(e, MissingAPIKeyError) or "MissingAPIKeyError" in type(e).__name__:
            print(f"Error: {e}")
            sys.exit(1)
        else:
            print(f"Error: Command failed: {e}")
            sys.exit(1)


def calibrate_command(args: argparse.Namespace) -> None:
    """Empirically calibrate the realistic counterfactual baseline for runs."""
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)
    with ExtensionStore(config.cache.path) as store:
        store.init_db()
        stats = store.get_tokenomics_stats()
        recent = stats.get("recent_runs", [])
        if not recent:
            print("No context pack runs recorded yet to calibrate.")
            return

        target_run = None
        run_id_arg = getattr(args, "run_id", None)
        if run_id_arg:
            target_run = next((r for r in recent if r["run_id"].startswith(run_id_arg)), None)
            if not target_run:
                with store._connect() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT * FROM context_pack_runs WHERE run_id LIKE ? LIMIT 1",
                        (f"{run_id_arg}%",),
                    )
                    row = cursor.fetchone()
                    if row:
                        target_run = dict(row)
        else:
            target_run = next((r for r in recent if not r.get("empirical_choice")), recent[0])

        if not target_run:
            print("Target run not found.")
            return

        rid = target_run["run_id"]
        target = target_run.get("target") or "Whole doc"
        mode = target_run.get("mode", "write")
        pack_tok = target_run.get("pack_tokens", 0)

        choice = getattr(args, "choice", None)
        if not choice:
            print(f"=== Empirical Counterfactual Calibration for Run [{rid[:8]}] ===")
            print(f"Target: '{target}' | Mode: '{mode}' | Pack Tokens: {pack_tok:,} tok")
            print("Without writing-context-rtfm, what would you have actually pasted into the LLM?")
            print("  [1] section       : Target section only (~1,500–4,000 tokens)")
            print("  [2] neighborhood  : Target section + adjacent context (~6,000–15,000 tokens)")
            print("  [3] chapter       : Entire chapter / major file (~20,000–50,000 tokens)")
            print("  [4] full_doc      : Entire document / workspace repository")
            try:
                user_inp = input("Choice (1-4 or name) [2]: ").strip().lower()
                choice = user_inp or "2"
            except (EOFError, KeyboardInterrupt):
                print("\nCalibration cancelled.")
                return

        choice_map = {
            "1": "section",
            "section": "section",
            "2": "neighborhood",
            "neighborhood": "neighborhood",
            "3": "chapter",
            "chapter": "chapter",
            "4": "full_doc",
            "full_doc": "full_doc",
        }
        canonical_choice = choice_map.get(str(choice).strip().lower(), "neighborhood")

        base_doc = target_run.get("baseline_doc_tokens", 0)
        if canonical_choice == "section":
            emp_tokens = min(base_doc, max(pack_tok, 2500))
        elif canonical_choice == "neighborhood":
            emp_tokens = min(base_doc, target_run.get("baseline_realistic_tokens") or 8000)
        elif canonical_choice == "chapter":
            emp_tokens = min(base_doc, target_run.get("baseline_tokens_raw") or 25000)
        else:
            emp_tokens = base_doc

        store.store_calibration(rid, canonical_choice, emp_tokens)
        saved = max(0, emp_tokens - (pack_tok + 420))
        ratio = round((saved / max(emp_tokens, 1)) * 100.0, 1)
        print(
            f"[OK] Calibrated run [{rid[:8]}] as '{canonical_choice}': {emp_tokens:,} tokens baseline."
        )
        print(f"     Empirical savings for this run: {saved:,} tokens ({ratio}% reduction).")


def stats_command(args: argparse.Namespace) -> None:
    if getattr(args, "calibrate", False):
        calibrate_command(args)
        return

    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)
    with ExtensionStore(config.cache.path) as store:
        store.init_db()

        if getattr(args, "session", False):
            msg_limit = getattr(args, "message_limit", 25) or 25
            sess = store.get_session_tokenomics(window_hours=5, message_limit=msg_limit)
            if getattr(args, "json", False):
                print(json.dumps(sess, indent=2))
                return

            print("=== Writing Context RTFM — 5-Hour Rolling Session Audit (Astra) ===")
            print(f"Rolling Window:               {sess['window_hours']} hours")
            print(f"Message Limit (Session):      {sess['session_message_limit']} msgs")
            print(f"Messages Consumed:            {sess['session_calls_used']} msgs")
            print(f"Calls Remaining (by msgs):    {sess['calls_remaining_by_message_limit']} msgs")
            print(
                f"Total Roundtrip Tokens:       {sess['session_tokens_used']:,} tok "
                f"(Pack: {sess['session_pack_tokens']:,}, Task: {sess['session_instruction_tokens']:,}, Gen: {sess['session_generation_tokens']:,})"
            )
            print(
                f"Single-Call Threshold:        {sess['single_call_threshold']:,} tok (pricing doubles if exceeded)"
            )
            print(f"Max Pack Observed:            {sess['max_single_pack_observed']:,} tok")
            b_cause = sess["bottleneck_cause"].replace("_", " ")
            b_rem = sess["bottleneck_calls_remaining"]
            print(f"Bottleneck Headroom:          ~{b_rem} calls remaining (limited by {b_cause})")
            tier_status = (
                "ALERT: EXCEEDED 272K TIER ON SINGLE PACK"
                if sess["threshold_exceeded"]
                else "HEALTHY (Within normal single-call tier)"
            )
            print(f"Single-Call Status:           {tier_status}")
            msg_status = (
                "EXHAUSTED (Hit session message quota)"
                if sess["message_limit_exceeded"]
                else "AVAILABLE"
            )
            print(f"Message Quota Status:         {msg_status}\n")
            return

        stats = store.get_tokenomics_stats()

    if getattr(args, "json", False):
        print(json.dumps(stats, indent=2))
        return

    print("=== Writing Context RTFM — Tokenomics & Financial Audit ===")
    print(f"Total Context Pack Runs:      {stats['total_runs']}")
    print(f"Total Context Pack Tokens:    {stats['total_pack_tokens']:,}")
    if stats.get("total_instruction_tokens"):
        print(f"Total Instruction Tokens:     {stats['total_instruction_tokens']:,}")
    print(f"Average Pipeline Latency:     {stats['avg_latency_ms']:.1f} ms")
    print(f"Average MCP Schema Overhead:  {stats['avg_schema_overhead']:.0f} tokens/call\n")

    print("--- Counterfactual Comparison ---")
    real_base = stats.get("total_realistic_baseline_tokens", 0)
    real_saved = stats.get("total_realistic_tokens_saved", 0)
    real_pct = stats.get("avg_realistic_savings_percentage", 0.0)
    print(
        f"Realistic Baseline (Truth):   {real_base:>9,} tok | Saved: {real_saved:>9,} tok ({real_pct:>5.1f}% reduction)"
    )

    empirical_runs = stats.get("empirical_calibrated_runs", 0)
    if empirical_runs > 0:
        emp_pct = stats.get("avg_empirical_savings_percentage", 0.0)
        print(
            f"Empirical User Baseline:      ({empirical_runs} runs calibrated) | Saved: {emp_pct:>5.1f}% reduction"
        )

    naive_base = stats.get("total_baseline_tokens", 0)
    naive_saved = stats.get("total_tokens_saved", 0)
    naive_pct = stats.get("avg_savings_percentage", 0.0)
    print(
        f"Naive Baseline (Full-Doc):    {naive_base:>9,} tok | Saved: {naive_saved:>9,} tok ({naive_pct:>5.1f}% reduction)\n"
    )

    by_mode = stats.get("by_mode", {})
    if by_mode:
        print("--- Breakdown by Task Mode ---")
        for mode_name, m_data in sorted(by_mode.items()):
            runs = m_data.get("runs", 0)
            avg_pack = m_data.get("avg_pack_tokens", 0)
            avg_real = m_data.get("avg_realistic_baseline", 0)
            avg_raw = m_data.get("avg_raw_baseline", avg_real)
            m_pct = m_data.get("avg_savings_percentage", 0.0)
            capped_note = f" ({m_data['capped_runs']} capped)" if m_data.get("capped_runs") else ""
            print(
                f"  Mode: {mode_name:<10} | Runs: {runs:>3} | Avg Pack: {avg_pack:>6.0f} tok | "
                f"Realistic Base: {avg_real:>6.0f} tok (raw: {avg_raw:>6.0f}){capped_note} | Saved: {m_pct:>5.1f}%"
            )
        print()

    recent = stats.get("recent_runs", [])
    if recent:
        print("--- Recent Context Pack Runs ---")
        for r in recent:
            rid = (r.get("run_id") or "")[:8]
            target_str = r.get("target") or "Whole Doc"
            t_mode = r.get("mode", "write")
            b_mode = r.get("baseline_mode", "section_neighborhood")
            saved = r.get("realistic_tokens_saved", r.get("tokens_saved", 0))
            ratio = r.get("realistic_savings_ratio", r.get("savings_ratio", 0.0)) * 100.0
            pack_tok = r.get("pack_tokens", 0)
            base_tok = r.get("baseline_realistic_tokens", r.get("baseline_doc_tokens", 0))
            raw_tok = r.get("baseline_tokens_raw", base_tok)
            capped_str = f" [Capped from {raw_tok:,}]" if r.get("is_capped") else ""
            emp_str = f" [Emp: {r.get('empirical_choice')}]" if r.get("empirical_choice") else ""
            lat = r.get("latency_ms", 0.0)
            print(
                f"  [{rid}] {target_str:<18} [{t_mode:<7}] | Pack: {pack_tok:>5} tok | "
                f"Base ({b_mode[:8]}): {base_tok:>6} tok{capped_str} | Saved: {saved:>6} tok ({ratio:>5.1f}%){emp_str} | {lat:.0f}ms"
            )
        print()


def main() -> None:
    parser = argparse.ArgumentParser(prog="writing-context-rtfm")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    # init
    p_init = subparsers.add_parser("init", help="Initialize configuration files")
    p_init.add_argument("--project-root", default=".", help="Project root path")
    p_init.add_argument(
        "--quickstart",
        action="store_true",
        help="One-command complete bootstrap: config, section cards scan, and initial index sync",
    )

    # init-cards (deprecated in favor of 'cards scan')
    p_init_cards = subparsers.add_parser(
        "init-cards",
        help="[Deprecated: use 'cards scan'] Scan project for .tex/.md files and auto-scaffold section cards",
    )
    p_init_cards.add_argument("--project-root", default=".", help="Project root path")

    # init-db
    p_idb = subparsers.add_parser("init-db", help="Initialize SQLite cache database")
    p_idb.add_argument("--project-root", default=".", help="Project root path")

    # sync
    parser_sync = subparsers.add_parser("sync", help="Trigger RTFM sync")
    parser_sync.add_argument("--path", default=".", help="Project root path to sync")
    parser_sync.add_argument("--corpus", default=None, help="Corpus name")
    parser_sync.add_argument(
        "--project-root", default=".", help="Project root for config resolution"
    )

    # pack
    parser_pack = subparsers.add_parser("pack", help="Generate a context pack")
    parser_pack.add_argument(
        "--project-root", default=".", help="Project root (resolves config and section_cards)"
    )
    parser_pack.add_argument("--corpus", default=None, help="Override corpus name")
    parser_pack.add_argument("--task", required=True, help="Writing task description")
    parser_pack.add_argument("--target", help="Target section ID")
    parser_pack.add_argument("--budget", type=int, default=6000, help="Token budget")
    parser_pack.add_argument(
        "--must-consider",
        nargs="*",
        help="Required concepts, facts, literals, or citation keys the context must cover",
    )
    parser_pack.add_argument(
        "--task-type",
        choices=[
            "write_new_section",
            "revise_existing_section",
            "proofread",
            "expand",
            "condense",
            "align_with_previous_sections",
            "review",
        ],
        help="Writing task type",
    )
    parser_pack.add_argument("--line-start", type=int, help="Target start line range")
    parser_pack.add_argument("--line-end", type=int, help="Target end line range")
    parser_pack.add_argument(
        "--pack-mode", choices=["minimal", "standard", "deep"], help="Context pack mode"
    )
    parser_pack.add_argument("--role-budgets", help="Role budgets JSON string override")
    parser_pack.add_argument(
        "--mode",
        choices=["write", "rewrite", "adapt", "compress"],
        help="Functional writing mode (write, rewrite, adapt, compress)",
    )
    parser_pack.add_argument(
        "--profile",
        choices=["fast", "balanced", "thorough", "auto"],
        help="Execution profile preset (fast, balanced, thorough, auto)",
    )
    parser_pack.add_argument(
        "--git-diff",
        action="store_true",
        help="Prioritize and boost lines modified in active git branch/working tree",
    )
    parser_pack.add_argument(
        "--explain",
        action="store_true",
        help="Print structured diagnostic funnel and candidate explanation",
    )

    # explain-pack
    parser_exp_pack = subparsers.add_parser(
        "explain-pack", help="Generate and explain a context pack candidate trace"
    )
    parser_exp_pack.add_argument(
        "--project-root", default=".", help="Project root (resolves config and section_cards)"
    )
    parser_exp_pack.add_argument("--corpus", default=None, help="Override corpus name")
    parser_exp_pack.add_argument("--task", required=True, help="Writing task description")
    parser_exp_pack.add_argument("--target", help="Target section ID")
    parser_exp_pack.add_argument("--budget", type=int, default=6000, help="Token budget")
    parser_exp_pack.add_argument(
        "--must-consider",
        nargs="*",
        help="Required concepts, facts, literals, or citation keys the context must cover",
    )
    parser_exp_pack.add_argument(
        "--task-type",
        choices=[
            "write_new_section",
            "revise_existing_section",
            "proofread",
            "expand",
            "condense",
            "align_with_previous_sections",
            "review",
        ],
        help="Writing task type",
    )
    parser_exp_pack.add_argument("--line-start", type=int, help="Target start line range")
    parser_exp_pack.add_argument("--line-end", type=int, help="Target end line range")
    parser_exp_pack.add_argument(
        "--pack-mode", choices=["minimal", "standard", "deep"], help="Context pack mode"
    )
    parser_exp_pack.add_argument("--role-budgets", help="Role budgets JSON string override")
    parser_exp_pack.add_argument(
        "--mode",
        choices=["write", "rewrite", "adapt", "compress"],
        help="Functional writing mode (write, rewrite, adapt, compress)",
    )
    parser_exp_pack.add_argument(
        "--profile",
        choices=["fast", "balanced", "thorough", "auto"],
        help="Execution profile preset (fast, balanced, thorough, auto)",
    )
    parser_exp_pack.add_argument(
        "--git-diff",
        action="store_true",
        help="Prioritize and boost lines modified in active git branch/working tree",
    )
    parser_exp_pack.add_argument(
        "--json", action="store_true", help="Output full JSON containing diagnostics"
    )

    # preview-pack
    parser_preview = subparsers.add_parser(
        "preview-pack", help="Preview the exact formatted context pack and prompt rendered for LLMs"
    )
    parser_preview.add_argument(
        "--project-root", default=".", help="Project root (resolves config and section_cards)"
    )
    parser_preview.add_argument("--corpus", default=None, help="Override corpus name")
    parser_preview.add_argument("--task", required=True, help="Writing task description")
    parser_preview.add_argument("--target", help="Target section ID")
    parser_preview.add_argument("--budget", type=int, default=6000, help="Token budget")
    parser_preview.add_argument(
        "--must-consider",
        nargs="*",
        help="Required concepts, facts, literals, or citation keys the context must cover",
    )
    parser_preview.add_argument(
        "--task-type",
        choices=[
            "write_new_section",
            "revise_existing_section",
            "proofread",
            "expand",
            "condense",
            "align_with_previous_sections",
            "review",
        ],
        help="Writing task type",
    )
    parser_preview.add_argument("--line-start", type=int, help="Target start line range")
    parser_preview.add_argument("--line-end", type=int, help="Target end line range")
    parser_preview.add_argument(
        "--pack-mode", choices=["minimal", "standard", "deep"], help="Context pack mode"
    )
    parser_preview.add_argument("--role-budgets", help="Role budgets JSON string override")
    parser_preview.add_argument(
        "--mode",
        choices=["write", "rewrite", "adapt", "compress"],
        help="Functional writing mode (write, rewrite, adapt, compress)",
    )
    parser_preview.add_argument(
        "--profile",
        choices=["fast", "balanced", "thorough", "auto"],
        help="Execution profile preset (fast, balanced, thorough, auto)",
    )
    parser_preview.add_argument(
        "--git-diff",
        action="store_true",
        help="Prioritize and boost lines modified in active git branch/working tree",
    )
    parser_preview.add_argument(
        "--raw",
        action="store_true",
        help="Output raw prompt text without inspection decorations",
    )
    parser_preview.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI styling in terminal preview",
    )

    # proofread-pack
    parser_proof = subparsers.add_parser(
        "proofread-pack", help="Generate a proofreading context pack"
    )
    parser_proof.add_argument("target_file", help="The file to proofread")
    parser_proof.add_argument("--line-start", type=int, required=True, help="Start line number")
    parser_proof.add_argument("--line-end", type=int, required=True, help="End line number")
    parser_proof.add_argument(
        "--mode",
        default="surface",
        choices=["surface", "academic_clarity", "consistency", "latex_safe"],
        help="Proofreading mode",
    )
    parser_proof.add_argument(
        "--strictness",
        default="moderate",
        choices=["conservative", "moderate", "assertive"],
        help="Correction strictness",
    )
    parser_proof.add_argument("--max-tokens", type=int, default=4000, help="Maximum token budget")
    parser_proof.add_argument(
        "--project-root", default=".", help="Project root for config resolution"
    )

    # cache
    parser_cache = subparsers.add_parser("cache", help="Manage cache database")
    parser_cache.add_argument("cache_action", choices=["clear", "stats"], help="Action to perform")
    parser_cache.add_argument("--project-root", default=".", help="Project root path")

    # doctor
    p_doc = subparsers.add_parser(
        "doctor", help="Run diagnostic health checks on extension environment"
    )
    p_doc.add_argument("--project-root", default=".", help="Project root path")
    p_doc.add_argument(
        "--json",
        action="store_true",
        help="Output diagnostic report in JSON format",
    )
    p_doc.add_argument(
        "--fix",
        action="store_true",
        help="Safely auto-repair missing configs, section cards, cache DB, and sync index",
    )

    # inspect-target
    p_insp = subparsers.add_parser(
        "inspect-target", help="Inspect configuration details for a target section"
    )
    p_insp.add_argument("--target", required=True, help="Target section ID key")
    p_insp.add_argument("--project-root", default=".", help="Project root path")

    # get-term
    parser_get_term = subparsers.add_parser(
        "get-term", help="Look up a term in the terminology glossary"
    )
    parser_get_term.add_argument("term", help="The term to look up")
    parser_get_term.add_argument("--project-root", default=".", help="Project root path")

    # show-graph
    parser_show_graph = subparsers.add_parser(
        "show-graph", help="Show LaTeX reference graph & section card dependencies"
    )
    parser_show_graph.add_argument("--project-root", default=".", help="Project root path")
    parser_show_graph.add_argument(
        "--format", default="text", choices=["text", "json"], help="Output format"
    )

    # auth
    parser_auth = subparsers.add_parser(
        "auth", help="Store an API key for a provider in the local cache database"
    )
    parser_auth.add_argument(
        "provider", choices=["openai_semantic", "huggingface"], help="The provider to authenticate"
    )
    parser_auth.add_argument("token", help="The API key/token")
    parser_auth.add_argument("--project-root", default=".", help="Project root path")

    # cleanup
    parser_cleanup = subparsers.add_parser(
        "cleanup", help="Cleanly terminate all tracked background MCP processes"
    )
    parser_cleanup.add_argument("--project-root", default=".", help="Project root path")

    # cards
    p_cards = subparsers.add_parser("cards", help="Manage section cards build workflow")
    cards_sub = p_cards.add_subparsers(dest="subcommand", required=True)

    # cards scan
    p_scan = cards_sub.add_parser(
        "scan", help="Scan manuscript structure and extract deterministic metadata"
    )
    p_scan.add_argument("--project-root", default=".", help="Project root path")

    # cards infer
    p_infer = cards_sub.add_parser(
        "infer", help="Run model-assisted semantic extraction on section nodes"
    )
    p_infer.add_argument("--project-root", default=".", help="Project root path")
    p_infer.add_argument("--force", action="store_true", help="Force re-inference of all sections")

    # cards review
    p_review = cards_sub.add_parser("review", help="Interactively review candidate card fields")
    p_review.add_argument("--project-root", default=".", help="Project root path")

    # cards update
    p_update = cards_sub.add_parser(
        "update", help="Update cards following manuscript changes, marking modified fields as stale"
    )
    p_update.add_argument("--project-root", default=".", help="Project root path")
    p_update.add_argument("--changed-only", action="store_true", help="Only scan changed files")

    # cards validate
    p_validate = cards_sub.add_parser(
        "validate", help="Check for stale fields, missing references, and inconsistencies"
    )
    p_validate.add_argument("--project-root", default=".", help="Project root path")

    # cards build
    p_build = cards_sub.add_parser("build", help="Run scan, infer, and update in sequence")
    p_build.add_argument("--project-root", default=".", help="Project root path")
    p_build.add_argument("--review", action="store_true", help="Run review after build")

    # cards rebuild
    p_rebuild = cards_sub.add_parser(
        "rebuild", help="Cleanly rebuild main section cards from scratch"
    )
    p_rebuild.add_argument("--project-root", default=".", help="Project root path")
    p_rebuild.add_argument("--review", action="store_true", help="Run review after rebuild")

    # stats
    p_stats = subparsers.add_parser("stats", help="Show tokenomics and cumulative token savings")
    p_stats.add_argument("--project-root", default=".", help="Project root path")
    p_stats.add_argument("--json", action="store_true", help="Output raw JSON")
    p_stats.add_argument(
        "--session",
        action="store_true",
        help="Audit rolling 5-hour session consumption against the 272K Astra tier and message quota",
    )
    p_stats.add_argument(
        "--message-limit",
        type=int,
        default=25,
        help="Session message limit (default: 25, Astra range: 5–45)",
    )
    p_stats.add_argument(
        "--calibrate",
        action="store_true",
        help="Interactively calibrate the realistic baseline for recent runs",
    )

    # calibrate
    p_cal = subparsers.add_parser(
        "calibrate", help="Empirically calibrate the realistic baseline for recent runs"
    )
    p_cal.add_argument(
        "run_id",
        nargs="?",
        default=None,
        help="Optional run ID to calibrate (default: latest uncalibrated run)",
    )
    p_cal.add_argument(
        "--choice",
        choices=["1", "2", "3", "4", "section", "neighborhood", "chapter", "full_doc"],
        default=None,
        help="Choice of baseline without prompting",
    )
    p_cal.add_argument("--project-root", default=".", help="Project root path")

    subparsers.add_parser("serve", help="Start the MCP server")

    args = parser.parse_args()

    if not getattr(args, "command", None):
        if not sys.stdin.isatty():
            args.command = "serve"
        else:
            print(
                f"Writing Context RTFM v{__version__} — Surgical Context for Writing Agents\n\n"
                "Usage: writing-context-rtfm <command> [options]\n\n"
                "Key Commands:\n"
                "  doctor        Diagnose Python, dependencies, index, Zotero, API keys, models (--fix to auto-repair)\n"
                "  init          Initialize configuration (--quickstart for complete 1-step bootstrap)\n"
                "  sync          Synchronize manuscript files into RTFM retrieval index\n"
                "  cards         Manage section cards (build, update, validate)\n"
                "  pack          Generate a targeted writing context pack\n"
                "  preview-pack  Preview exact formatted context pack and prompt rendered for LLMs\n"
                "  stats         Show tokenomics and cumulative token savings (--session, --calibrate)\n"
                "  calibrate     Empirically calibrate counterfactual baseline for recent runs\n"
                "  serve         Start MCP server (STDIO mode for Claude Desktop / Cursor)\n\n"
                "Tip: Run 'writing-context-rtfm --help' for full command list."
            )
            return

    commands = {
        "init": init_command,
        "init-cards": init_cards_command,
        "init-db": init_db_command,
        "sync": sync_command,
        "pack": pack_command,
        "explain-pack": explain_pack_command,
        "preview-pack": preview_pack_command,
        "proofread-pack": proofread_pack_command,
        "serve": serve_command,
        "cache": cache_command,
        "doctor": doctor_command,
        "inspect-target": inspect_target_command,
        "get-term": get_term_command,
        "show-graph": show_graph_command,
        "auth": auth_command,
        "cleanup": cleanup_command,
        "cards": cards_command,
        "stats": stats_command,
        "calibrate": calibrate_command,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
