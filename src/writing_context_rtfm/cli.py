"""CLI module."""
import argparse
import sys
import json
import shutil
import yaml
from dataclasses import asdict, replace
from pathlib import Path

from writing_context_rtfm.config import load_config
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.proofread import ProofreadPackGenerator
from writing_context_rtfm.hashing import compute_rtfm_fingerprint
from writing_context_rtfm.features import get_term_context
from writing_context_rtfm.server import run_server
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
        clean = line.split('#')[0].strip()
        if clean in (cache_path, ".writing-context/*.sqlite", ".writing-context/", ".writing-context/*"):
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
        server_def = {
            "command": "uv",
            "args": ["run", "writing-context-rtfm", "serve"]
        }
    else:
        server_def = {
            "command": "writing-context-rtfm",
            "args": ["serve"]
        }
        
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
        "1. **Always retrieve context first**: Never read manuscript files raw. Use `get_writing_context_pack` or `get_proofreading_context_pack` before writing, rewriting, expanding, or proofreading text.\n"
        "2. **Specify Task & Depth**: Use `task_type` and `pack_mode` parameters when calling `get_writing_context_pack` to optimize context weightings and token budgets.\n"
        "3. **Respect LaTeX Safety Warnings**: Pay attention to safety warnings in the pack. Never edit/alter the detected LaTeX citations (`\\cite`), labels (`\\label`), references (`\\ref`), or math environments.\n"
        "4. **Use Terminology Lookup**: Use the `get_term_context` tool to retrieve definitions, variants, and words to avoid for specific terms.\n"
        "5. **Handle Pagination**: If you need more context, call `request_more_context` with the `run_id`. Do not guess or read files.\n"
        "6. **Log Feedback**: Always evaluate retrieved context using `submit_generation_feedback` so subsequent caching is optimized.\n"
        "7. **Initialize configurations**: Use `initialize_section_cards` to scaffold cards for untracked sections.\n"
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

def init_command(args):
    """Creates .writing-context/ directory with sample config and section cards if missing."""
    root = Path(getattr(args, "project_root", ".")).resolve()
    wc = root / ".writing-context"
    wc.mkdir(exist_ok=True)
    config_file = wc / "config.yaml"
    sc_file = wc / "section_cards.yaml"
    if not config_file.exists():
        template = (
            "# writing-context-rtfm Configuration File\n"
            "version: 1\n\n"
            "# RTFM core indexing settings\n"
            "rtfm:\n"
            "  # The corpus name registered in RTFM for this manuscript project.\n"
            "  corpus: manuscript\n"
            "  # Enable auto-sync before generating a context pack to ensure the index is up-to-date.\n"
            "  # sync_before_pack: true\n\n"
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
            "  # invalidate_on_refresh: true\n"
        )
        config_file.write_text(template, encoding="utf-8")
        print(f"Created {config_file}")
    if not sc_file.exists():
        template_sc = (
            "# writing-context-rtfm Section Cards Configuration\n"
            "# This file acts as your manuscript roadmap, helping the AI agent\n"
            "# keep track of the global thesis, terminology, section layout, and dependencies.\n"
            "version: 1\n\n"
            "# Document-level global context\n"
            "document:\n"
            "  # The title of your project or paper\n"
            "  title: \"A New Approach to Manuscript Curation\"\n"
            "  # The central thesis statement (injected globally to keep the agent focused)\n"
            "  thesis: \"Surgical context selection using a gatekeeping protocol reduces LLM token overhead and improves writing accuracy.\"\n"
            "  # Global writing style guide\n"
            "  writing_style:\n"
            "    tone: \"Academic, precise, concise, third-person\"\n"
            "    avoid_words: [\"cliché\", \"groundbreaking\", \"revolutionary\", \"game-changing\"]\n"
            "  # Global project terminology dictionary\n"
            "  terminology:\n"
            "    # Option A: Simple term-to-definition mapping\n"
            "    Context Pack: \"A compact JSON structure containing prioritized source spans, token estimates, and constraints.\"\n"
            "    # Option B: Advanced term definition with variants and avoid matches\n"
            "    RTFM:\n"
            "      definition: \"Read The Fine Manual: A semantic retrieval and indexing tool.\"\n"
            "      variants: [\"rtfm-ai\", \"RTFM CLI\"]\n"
            "      avoid: [\"RTFM database write\", \"modifying library.db\"]\n\n"
            "# Section cards definitions. Fill these in to outline your paper's structure.\n"
            "# If your files already exist, you can run `writing-context-rtfm init-cards` to auto-scaffold them.\n"
            "sections:\n"
            "  section_abstract:\n"
            "    title: \"Abstract\"\n"
            "    role: \"Provide a standalone, 150-word summary of the thesis, approach, and primary results.\"\n"
            "    path: \"sections/00_abstract.tex\"  # Relative path to your draft file\n"
            "    key_terms: [\"Surgical context\", \"Gatekeeping protocol\"]\n"
            "    depends_on: []                    # Abstract usually has no direct dependencies\n"
            "    must_preserve: []                 # Specific claims or equations that must not change\n"
            "    avoid: [\"detailed experimental setups\", \"citations\"]\n"
            "    constraints:\n"
            "      - \"Exactly one paragraph\"\n"
            "      - \"Max 150 words\"\n\n"
            "  section_introduction:\n"
            "    title: \"Introduction\"\n"
            "    role: \"Establish the problem context, outline the research gap, and state the main contributions.\"\n"
            "    path: \"sections/01_introduction.tex\"\n"
            "    key_terms: [\"LLM token overhead\", \"context curation\"]\n"
            "    depends_on:\n"
            "      - section_abstract             # Tells the agent to look at the abstract first\n"
            "    must_preserve: []\n"
            "    avoid: [\"premature methodology details\"]\n"
            "    constraints:\n"
            "      - \"Ensure main contributions are listed as a bulleted list\"\n\n"
            "  section_methodology:\n"
            "    title: \"Proposed Methodology\"\n"
            "    role: \"Detail the system architecture, mathematical formulations, and context selection algorithms.\"\n"
            "    path: \"sections/02_methodology.tex\"\n"
            "    key_terms: [\"Gatekeeping protocol\", \"Token budget\"]\n"
            "    depends_on:\n"
            "      - section_introduction\n"
            "    must_preserve:\n"
            "      - \"Token budget formula is B_usable = B_total * (1 - margin)\"\n"
            "    avoid: []\n"
            "    constraints:\n"
            "      - \"Write equations using LaTeX align or equation environments\"\n"
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

def init_cards_command(args):
    """Scans the workspace and generates or appends section cards."""
    project_root = getattr(args, "project_root", ".")
    try:
        from writing_context_rtfm.features import initialize_section_cards
        res = initialize_section_cards(project_root)
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

def init_db_command(args):
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)
    store = ExtensionStore(config.cache.path)
    store.init_db()
    print(f"Initialized database at {config.cache.path}")

def sync_command(args):
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
        adapter.sync(sync_path, corpus=corpus)
        store = ExtensionStore(config.cache.path)
        store.init_db()
        
        # Compute real library.db fingerprint after sync
        rtfm_db = resolve_rtfm_db_path(Path(project_root))
        fingerprint = compute_rtfm_fingerprint(rtfm_db)
            
        store.invalidate_for_fingerprint(fingerprint)
        print("Sync completed successfully.")
    except Exception as e:
        print(f"Sync failed: {e}", file=sys.stderr)
        sys.exit(1)

def cache_command(args):
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)
    store = ExtensionStore(config.cache.path)
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

def pack_command(args):
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)

    # Override corpus from CLI if provided
    if getattr(args, "corpus", None):
        config = replace(config, rtfm=replace(config.rtfm, corpus=args.corpus))

    sc_path = config.section_cards.path
    cards = load_section_cards(sc_path, required=config.section_cards.required)

    adapter = RTFMAdapter(project_root=str(Path(project_root).resolve()))
    store = ExtensionStore(config.cache.path)
    store.init_db()
    generator = ContextPackGenerator(config, cards, adapter, store)

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
        role_budgets=role_budgets
    )
    print(json.dumps(asdict(pack), indent=2))

def proofread_pack_command(args):
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)

    cards = load_section_cards(config.section_cards.path, required=config.section_cards.required)
    adapter = RTFMAdapter(project_root=str(Path(project_root).resolve()))
    store = ExtensionStore(config.cache.path)
    store.init_db()
    generator = ProofreadPackGenerator(config, cards, adapter, store)

    pack = generator.generate(
        target_file=args.target_file,
        line_start=args.line_start,
        line_end=args.line_end,
        mode=args.mode,
        strictness=args.strictness,
        max_tokens=args.max_tokens
    )
    print(json.dumps(asdict(pack), indent=2))

def get_term_command(args):
    project_root = getattr(args, "project_root", ".")
    try:
        res = get_term_context(args.term, project_root)
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}), file=sys.stderr)
        sys.exit(1)

def serve_command(args):
    run_server()

def doctor_command(args):
    project_root = Path(getattr(args, "project_root", ".")).resolve()
    
    print("Writing Context RTFM Extension Doctor")
    print("======================================")
    
    # 1. RTFM CLI / Package check
    rtfm_cli = shutil.which("rtfm")
    rtfm_pkg = False
    try:
        import rtfm_ai  # type: ignore # noqa: F401
        rtfm_pkg = True
    except ImportError:
        try:
            import rtfm  # type: ignore # noqa: F401
            rtfm_pkg = True
        except ImportError:
            pass
            
    print(f"[*] RTFM CLI:         {'[OK] Found at ' + rtfm_cli if rtfm_cli else '[WARN] Not found in PATH'}")
    print(f"[*] RTFM Library:     {'[OK] Package rtfm/rtfm-ai importable' if rtfm_pkg else '[FAIL] Package not importable'}")
    
    # 2. Project config
    config_file = project_root / ".writing-context" / "config.yaml"
    sc_file = project_root / ".writing-context" / "section_cards.yaml"
    
    print(f"[*] Project Root:     {project_root}")
    
    config = None
    if config_file.exists():
        try:
            config = load_config(str(project_root))
            print(f"[*] Config:           [OK] Loaded from {config_file.relative_to(project_root)}")
        except Exception as e:
            print(f"[*] Config:           [FAIL] Failed to load {config_file.relative_to(project_root)}: {e}")
    else:
        print("[*] Config:           [WARN] config.yaml not found (using defaults)")

    if sc_file.exists():
        try:
            with open(sc_file, "r") as f:
                yaml.safe_load(f)
            cards = load_section_cards(str(sc_file), required=False)
            print(f"[*] Section Cards:    [OK] Parsed {len(cards.sections)} sections from {sc_file.relative_to(project_root)}")
        except Exception as e:
            print(f"[*] Section Cards:    [FAIL] Failed to parse {sc_file.relative_to(project_root)}: {e}")
    else:
        print("[*] Section Cards:    [WARN] section_cards.yaml not found")

    # 3. Database Check
    db_path = resolve_rtfm_db_path(project_root)
    try:
        rel_db = db_path.relative_to(project_root)
    except ValueError:
        rel_db = db_path

    if db_path.exists():
        print(f"[*] RTFM DB:          [OK] Found at {rel_db}")
    else:
        print(f"[*] RTFM DB:          [FAIL] No RTFM library database found at {rel_db} (Needs sync)")

    # 4. Cache Check
    if not config:
        try:
            config = load_config(str(project_root))
        except Exception:
            pass

    if config:
        cache_db = Path(config.cache.path)
        if cache_db.exists():
            try:
                store = ExtensionStore(str(cache_db))
                store.init_db()
                try:
                    rel_cache = cache_db.relative_to(project_root)
                except ValueError:
                    rel_cache = cache_db
                print(f"[*] Cache DB:         [OK] Found and initialized at {rel_cache}")
            except Exception as e:
                print(f"[*] Cache DB:         [FAIL] Cache database at {cache_db} exists but failed to initialize: {e}")
        else:
            try:
                rel_cache = cache_db.relative_to(project_root)
            except ValueError:
                rel_cache = cache_db
            print(f"[*] Cache DB:         [OK] Not found (will be automatically created at {rel_cache})")

def inspect_target_command(args):
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
    if target not in cards.sections:
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

def show_graph_command(args):
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
        payload = {
            "graph": graph,
            "sections": {}
        }
        if cards and cards.sections:
            for sid, scard in cards.sections.items():
                payload["sections"][sid] = {
                    "path": scard.path,
                    "depends_on": scard.depends_on
                }
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
    all_files = sorted(list(set(list(references.keys()) + list(citations.keys()))))
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

def main():
    parser = argparse.ArgumentParser(prog="writing-context-rtfm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Initialize configuration files")
    p_init.add_argument("--project-root", default=".", help="Project root path")

    # init-cards
    p_init_cards = subparsers.add_parser("init-cards", help="Scan project for .tex/.md files and auto-scaffold section cards")
    p_init_cards.add_argument("--project-root", default=".", help="Project root path")

    # init-db
    p_idb = subparsers.add_parser("init-db", help="Initialize SQLite cache database")
    p_idb.add_argument("--project-root", default=".", help="Project root path")

    # sync
    parser_sync = subparsers.add_parser("sync", help="Trigger RTFM sync")
    parser_sync.add_argument("--path", default=".", help="Project root path to sync")
    parser_sync.add_argument("--corpus", default=None, help="Corpus name")
    parser_sync.add_argument("--project-root", default=".", help="Project root for config resolution")

    # pack
    parser_pack = subparsers.add_parser("pack", help="Generate a context pack")
    parser_pack.add_argument("--project-root", default=".", help="Project root (resolves config and section_cards)")
    parser_pack.add_argument("--corpus", default=None, help="Override corpus name")
    parser_pack.add_argument("--task", required=True, help="Writing task description")
    parser_pack.add_argument("--target", help="Target section ID")
    parser_pack.add_argument("--budget", type=int, default=6000, help="Token budget")
    parser_pack.add_argument("--must-consider", nargs="*", help="Explicit files or concepts to consider")
    parser_pack.add_argument("--task-type", choices=["write_new_section", "revise_existing_section", "proofread", "expand", "condense", "align_with_previous_sections", "review"], help="Writing task type")
    parser_pack.add_argument("--line-start", type=int, help="Target start line range")
    parser_pack.add_argument("--line-end", type=int, help="Target end line range")
    parser_pack.add_argument("--pack-mode", choices=["minimal", "standard", "deep"], help="Context pack mode")
    parser_pack.add_argument("--role-budgets", help="Role budgets JSON string override")

    # proofread-pack
    parser_proof = subparsers.add_parser("proofread-pack", help="Generate a proofreading context pack")
    parser_proof.add_argument("target_file", help="The file to proofread")
    parser_proof.add_argument("--line-start", type=int, required=True, help="Start line number")
    parser_proof.add_argument("--line-end", type=int, required=True, help="End line number")
    parser_proof.add_argument("--mode", default="surface", choices=["surface", "academic_clarity", "consistency", "latex_safe"], help="Proofreading mode")
    parser_proof.add_argument("--strictness", default="moderate", choices=["conservative", "moderate", "assertive"], help="Correction strictness")
    parser_proof.add_argument("--max-tokens", type=int, default=4000, help="Maximum token budget")
    parser_proof.add_argument("--project-root", default=".", help="Project root for config resolution")

    # cache
    parser_cache = subparsers.add_parser("cache", help="Manage cache database")
    parser_cache.add_argument("cache_action", choices=["clear", "stats"], help="Action to perform")
    parser_cache.add_argument("--project-root", default=".", help="Project root path")

    # doctor
    p_doc = subparsers.add_parser("doctor", help="Run diagnostic health checks on extension environment")
    p_doc.add_argument("--project-root", default=".", help="Project root path")

    # inspect-target
    p_insp = subparsers.add_parser("inspect-target", help="Inspect configuration details for a target section")
    p_insp.add_argument("--target", required=True, help="Target section ID key")
    p_insp.add_argument("--project-root", default=".", help="Project root path")

    # get-term
    parser_get_term = subparsers.add_parser("get-term", help="Look up a term in the terminology glossary")
    parser_get_term.add_argument("term", help="The term to look up")
    parser_get_term.add_argument("--project-root", default=".", help="Project root path")

    # show-graph
    parser_show_graph = subparsers.add_parser("show-graph", help="Show LaTeX reference graph & section card dependencies")
    parser_show_graph.add_argument("--project-root", default=".", help="Project root path")
    parser_show_graph.add_argument("--format", default="text", choices=["text", "json"], help="Output format")

    subparsers.add_parser("serve", help="Start the MCP server")

    args = parser.parse_args()

    commands = {
        "init": init_command,
        "init-cards": init_cards_command,
        "init-db": init_db_command,
        "sync": sync_command,
        "pack": pack_command,
        "proofread-pack": proofread_pack_command,
        "serve": serve_command,
        "cache": cache_command,
        "doctor": doctor_command,
        "inspect-target": inspect_target_command,
        "get-term": get_term_command,
        "show-graph": show_graph_command
    }

    commands[args.command](args)

if __name__ == "__main__":
    main()
