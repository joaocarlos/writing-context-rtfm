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

def init_command(args):
    """Creates .writing-context/ directory with sample config and section cards if missing."""
    root = Path(getattr(args, "project_root", ".")).resolve()
    wc = root / ".writing-context"
    wc.mkdir(exist_ok=True)
    config_file = wc / "config.yaml"
    sc_file = wc / "section_cards.yaml"
    if not config_file.exists():
        config_file.write_text("version: 1\nrtfm:\n  corpus: manuscript\n")
        print(f"Created {config_file}")
    if not sc_file.exists():
        sc_file.write_text("version: 1\ndocument:\n  title: Example\nsections:\n")
        print(f"Created {sc_file}")

def init_db_command(args):
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)
    store = ExtensionStore(config.cache.path)
    store.init_db()
    print(f"Initialized database at {config.cache.path}")

def sync_command(args):
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)
    adapter = RTFMAdapter()
    sync_path = args.path if args.path != "." else project_root
    corpus = args.corpus or config.rtfm.corpus
    try:
        adapter.sync(sync_path, corpus=corpus)
        store = ExtensionStore(config.cache.path)
        store.init_db()
        
        # Compute real library.db fingerprint after sync
        rtfm_db = Path(project_root) / ".rtfm" / "library.db"
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

    adapter = RTFMAdapter()
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
    adapter = RTFMAdapter()
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
    db_path = project_root / ".rtfm" / "library.db"
    if db_path.exists():
        print(f"[*] RTFM DB:          [OK] Found at {db_path.relative_to(project_root)}")
    else:
        print(f"[*] RTFM DB:          [FAIL] No RTFM library database found at {db_path.relative_to(project_root)} (Needs sync)")

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

def main():
    parser = argparse.ArgumentParser(prog="writing-context-rtfm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = subparsers.add_parser("init", help="Initialize configuration files")
    p_init.add_argument("--project-root", default=".", help="Project root path")

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

    subparsers.add_parser("serve", help="Start the MCP server")

    args = parser.parse_args()

    commands = {
        "init": init_command,
        "init-db": init_db_command,
        "sync": sync_command,
        "pack": pack_command,
        "proofread-pack": proofread_pack_command,
        "serve": serve_command,
        "cache": cache_command,
        "doctor": doctor_command,
        "inspect-target": inspect_target_command,
        "get-term": get_term_command
    }

    commands[args.command](args)

if __name__ == "__main__":
    main()
