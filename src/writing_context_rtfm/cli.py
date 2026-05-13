"""CLI module."""
import argparse
import sys
import json
from dataclasses import asdict
from pathlib import Path

from writing_context_rtfm.config import load_config
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.context_pack import ContextPackGenerator

def init_command(args):
    """Creates .writing-context/ directory with sample config and section cards if missing."""
    import os
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
    if adapter.sync(sync_path, corpus=corpus):
        store = ExtensionStore(config.cache.path)
        store.init_db()
        store.invalidate_for_fingerprint("new-fingerprint")
        print("Sync completed successfully.")
    else:
        print("Sync failed.", file=sys.stderr)
        sys.exit(1)

def pack_command(args):
    project_root = getattr(args, "project_root", ".")
    config = load_config(project_root)

    # Override corpus from CLI if provided
    if getattr(args, "corpus", None):
        from dataclasses import replace
        config = replace(config, rtfm=replace(config.rtfm, corpus=args.corpus))

    sc_path = config.section_cards.path
    cards = load_section_cards(sc_path, required=config.section_cards.required)

    adapter = RTFMAdapter()
    store = ExtensionStore(config.cache.path)
    store.init_db()
    generator = ContextPackGenerator(config, cards, adapter, store)

    pack = generator.generate(
        task=args.task,
        target=args.target,
        token_budget=args.budget,
        must_consider=args.must_consider or [],
        project_root=project_root
    )
    print(json.dumps(asdict(pack), indent=2))

def serve_command(args):
    from writing_context_rtfm.server import run_server
    run_server()

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

    subparsers.add_parser("serve", help="Start the MCP server")

    args = parser.parse_args()

    commands = {
        "init": init_command,
        "init-db": init_db_command,
        "sync": sync_command,
        "pack": pack_command,
        "serve": serve_command
    }

    commands[args.command](args)

if __name__ == "__main__":
    main()
