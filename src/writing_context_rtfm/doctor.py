"""Comprehensive environment, dependency, and project diagnostics."""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from writing_context_rtfm.config import load_config
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.utils import resolve_rtfm_db_path


@dataclass
class DiagnosticItem:
    name: str
    status: str  # "OK", "WARN", "FAIL"
    message: str
    remediation: str | None = None


@dataclass
class DiagnosticCategory:
    category: str
    items: list[DiagnosticItem] = field(default_factory=list)


@dataclass
class DoctorReport:
    healthy: bool
    has_critical_failures: bool
    categories: list[DiagnosticCategory] = field(default_factory=list)
    remediations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mask_secret(secret: str | None) -> str:
    if not secret:
        return "Not configured"
    clean = secret.strip()
    if len(clean) <= 8:
        return "****"
    return f"{clean[:4]}...{clean[-4:]}"


def check_python() -> DiagnosticCategory:
    cat = DiagnosticCategory(category="Python Environment")
    version_info = sys.version_info
    v_str = f"{version_info.major}.{version_info.minor}.{version_info.micro}"

    if version_info >= (3, 13):
        cat.items.append(
            DiagnosticItem(
                name="Python Version",
                status="OK",
                message=f"{v_str} ({platform.python_implementation()})",
            )
        )
    else:
        cat.items.append(
            DiagnosticItem(
                name="Python Version",
                status="FAIL",
                message=f"{v_str} (Python >= 3.13 is required)",
                remediation="Upgrade to Python 3.13 or newer: 'uv python install 3.13'",
            )
        )

    cat.items.append(
        DiagnosticItem(
            name="Python Binary",
            status="OK",
            message=sys.executable,
        )
    )
    cat.items.append(
        DiagnosticItem(
            name="Operating System",
            status="OK",
            message=f"{platform.system()} {platform.release()} ({platform.machine()})",
        )
    )
    return cat


def check_dependencies() -> DiagnosticCategory:
    cat = DiagnosticCategory(category="Dependencies")

    # Core dependencies
    core_pkgs = [
        ("rtfm", ["rtfm", "rtfm_ai"], True, "pip install rtfm-ai[embeddings]"),
        ("mcp", ["mcp"], True, "pip install mcp"),
        ("pyyaml", ["yaml"], True, "pip install pyyaml"),
        ("pylatexenc", ["pylatexenc"], True, "pip install pylatexenc"),
        ("pathspec", ["pathspec"], True, "pip install pathspec"),
    ]

    for display_name, mod_names, is_core, fix_cmd in core_pkgs:
        mod = None
        imported_name = None
        for m in mod_names:
            try:
                mod = __import__(m)
                imported_name = m
                break
            except ImportError:
                continue

        if mod:
            ver = getattr(mod, "__version__", "installed")
            cat.items.append(
                DiagnosticItem(
                    name=f"Package {display_name}",
                    status="OK",
                    message=f"v{ver} ({imported_name})",
                )
            )
        else:
            cat.items.append(
                DiagnosticItem(
                    name=f"Package {display_name}",
                    status="FAIL" if is_core else "WARN",
                    message="Not installed",
                    remediation=f"Install dependency: '{fix_cmd}'",
                )
            )

    # Check rtfm CLI binary
    rtfm_cli = shutil.which("rtfm")
    if not rtfm_cli:
        python_dir = os.path.dirname(sys.executable)
        cand = os.path.join(python_dir, "rtfm")
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            rtfm_cli = cand

    if rtfm_cli:
        cat.items.append(
            DiagnosticItem(
                name="RTFM CLI",
                status="OK",
                message=f"Found at {rtfm_cli}",
            )
        )
    else:
        cat.items.append(
            DiagnosticItem(
                name="RTFM CLI",
                status="WARN",
                message="Executable 'rtfm' not found in PATH",
                remediation="Ensure 'rtfm-ai' is installed globally or in virtualenv: 'uv tool install rtfm-ai[embeddings]'",
            )
        )

    # Optional dependencies
    opt_pkgs = [
        ("tiktoken", ["tiktoken"], "Fast token budgeting and accurate cl100k_base counting"),
        ("fastembed", ["fastembed"], "Local ONNX embeddings for RTFM semantic search"),
        ("sentence-transformers", ["sentence_transformers"], "Local models and reranker provider"),
        ("transformers", ["transformers"], "HuggingFace transformer model inference"),
    ]

    for display_name, mod_names, description in opt_pkgs:
        mod = None
        for m in mod_names:
            try:
                mod = __import__(m)
                break
            except ImportError:
                continue
        if mod:
            ver = getattr(mod, "__version__", "installed")
            cat.items.append(
                DiagnosticItem(
                    name=f"Optional {display_name}",
                    status="OK",
                    message=f"v{ver} ({description})",
                )
            )
        else:
            cat.items.append(
                DiagnosticItem(
                    name=f"Optional {display_name}",
                    status="OK",
                    message=f"Not installed (optional: {description})",
                )
            )

    return cat


def check_index(project_root: Path) -> DiagnosticCategory:
    cat = DiagnosticCategory(category="Retrieval Index (RTFM)")
    db_path = resolve_rtfm_db_path(project_root)

    try:
        rel_db = str(db_path.relative_to(project_root))
    except ValueError:
        rel_db = str(db_path)

    if not db_path.exists():
        cat.items.append(
            DiagnosticItem(
                name="RTFM DB",
                status="WARN",
                message=f"No RTFM library database found at {rel_db} (Needs sync)",
                remediation="Initialize and sync manuscript index: 'writing-context-rtfm sync'",
            )
        )
        return cat

    file_size_mb = db_path.stat().st_size / (1024 * 1024)
    cat.items.append(
        DiagnosticItem(
            name="RTFM DB",
            status="OK",
            message=f"Found at {rel_db} ({file_size_mb:.2f} MB)",
        )
    )

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM chunks")
            chunks_count = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM books")
            books_count = cursor.fetchone()[0]

            cat.items.append(
                DiagnosticItem(
                    name="Indexed Corpus Content",
                    status="OK" if chunks_count > 0 else "WARN",
                    message=f"{chunks_count} chunks across {books_count} document(s)",
                    remediation="Sync manuscript files into the index: 'writing-context-rtfm sync'"
                    if chunks_count == 0
                    else None,
                )
            )

            # Check FTS table
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
            )
            fts_exists = cursor.fetchone() is not None
            if fts_exists:
                cat.items.append(
                    DiagnosticItem(
                        name="FTS5 Full-Text Search",
                        status="OK",
                        message="FTS5 chunks_fts index active",
                    )
                )
            else:
                cat.items.append(
                    DiagnosticItem(
                        name="FTS5 Full-Text Search",
                        status="WARN",
                        message="chunks_fts table not found in database",
                        remediation="Rebuild RTFM index with: 'writing-context-rtfm sync'",
                    )
                )

            # Check embeddings coverage
            tables = {
                row[0]
                for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            has_embeds = False
            if "embeddings" in tables or "chunk_embeddings" in tables:
                has_embeds = True
            else:
                cols = {row[1] for row in cursor.execute("PRAGMA table_info(chunks)")}
                if "embedding" in cols or "vector" in cols:
                    has_embeds = True

            cat.items.append(
                DiagnosticItem(
                    name="Vector Embeddings",
                    status="OK" if has_embeds else "OK",
                    message="Available for semantic search"
                    if has_embeds
                    else "Not generated (FTS BM25 search will be used)",
                )
            )

    except Exception as e:
        cat.items.append(
            DiagnosticItem(
                name="Index Database Integrity",
                status="FAIL",
                message=f"Database query error: {e}",
                remediation="Re-sync library index: 'writing-context-rtfm sync'",
            )
        )

    return cat


def check_zotero_and_bibtex(project_root: Path) -> DiagnosticCategory:
    cat = DiagnosticCategory(category="Literature & Citations (BibTeX / Zotero)")

    # 1. Local .bib files
    bib_files = list(project_root.glob("*.bib")) + list(project_root.glob("references/*.bib"))
    if bib_files:
        bib_names = ", ".join(f.name for f in bib_files[:3])
        if len(bib_files) > 3:
            bib_names += f" (+{len(bib_files) - 3} more)"
        cat.items.append(
            DiagnosticItem(
                name="Offline BibTeX Files",
                status="OK",
                message=f"Found {len(bib_files)} file(s): {bib_names}",
            )
        )
    else:
        cat.items.append(
            DiagnosticItem(
                name="Offline BibTeX Files",
                status="OK",
                message="No local .bib files detected (BibTeX provider idle)",
            )
        )

    # 2. Local Zotero installation
    zotero_sqlite_locations = [
        Path.home() / "Zotero" / "zotero.sqlite",
        Path.home() / ".zotero" / "zotero.sqlite",
        Path.home() / "Library" / "Application Support" / "Zotero" / "Profiles",
    ]
    found_zotero_dir = any(p.exists() for p in zotero_sqlite_locations)
    if found_zotero_dir:
        cat.items.append(
            DiagnosticItem(
                name="Zotero Desktop Storage",
                status="OK",
                message="Local Zotero data directory detected",
            )
        )
    else:
        cat.items.append(
            DiagnosticItem(
                name="Zotero Desktop Storage",
                status="OK",
                message="Zotero desktop data not detected at default paths",
            )
        )

    # 3. Zotero MCP Server executable
    zmcp_path = shutil.which("zotero-mcp")
    if zmcp_path:
        cat.items.append(
            DiagnosticItem(
                name="zotero-mcp Executable",
                status="OK",
                message=f"Found at {zmcp_path}",
            )
        )
    else:
        cat.items.append(
            DiagnosticItem(
                name="zotero-mcp Executable",
                status="OK",
                message="Not on PATH (optional: required only if using live Zotero MCP search)",
            )
        )

    # 4. Check config provider status
    config_file = project_root / ".writing-context" / "config.yaml"
    if config_file.exists():
        try:
            config = load_config(str(project_root))
            zotero_cfg = config.providers.get("zotero")
            if zotero_cfg and zotero_cfg.enabled:
                cat.items.append(
                    DiagnosticItem(
                        name="Zotero Provider Config",
                        status="OK",
                        message="Enabled in config.yaml",
                    )
                )
            else:
                cat.items.append(
                    DiagnosticItem(
                        name="Zotero Provider Config",
                        status="OK",
                        message="Disabled in config.yaml",
                    )
                )
        except Exception:
            pass

    return cat


def check_api_keys(project_root: Path) -> DiagnosticCategory:
    cat = DiagnosticCategory(category="API Keys & Credentials")

    # Read stored keys from cache DB if present
    cache_keys: dict[str, str] = {}
    config_file = project_root / ".writing-context" / "config.yaml"
    if config_file.exists():
        try:
            config = load_config(str(project_root))
            cache_db = Path(config.cache.path)
            if cache_db.exists():
                with ExtensionStore(str(cache_db)) as store:
                    store.init_db()
                    for prov in ("openai_semantic", "huggingface"):
                        key = store.get_provider_token(prov)
                        if key:
                            cache_keys[prov] = key
        except Exception:
            pass

    # 1. OpenAI Key
    openai_env = os.environ.get("OPENAI_API_KEY")
    openai_stored = cache_keys.get("openai_semantic")
    if openai_env:
        cat.items.append(
            DiagnosticItem(
                name="OpenAI API Key",
                status="OK",
                message=f"Configured via environment ({_mask_secret(openai_env)})",
            )
        )
    elif openai_stored:
        cat.items.append(
            DiagnosticItem(
                name="OpenAI API Key",
                status="OK",
                message=f"Configured via local cache ({_mask_secret(openai_stored)})",
            )
        )
    else:
        cat.items.append(
            DiagnosticItem(
                name="OpenAI API Key",
                status="OK",
                message="Not configured (optional: needed for gpt-4o-mini section inference or openai_semantic provider)",
            )
        )

    # 2. HuggingFace Token
    hf_env = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    hf_stored = cache_keys.get("huggingface")
    if hf_env or hf_stored:
        token = hf_env or hf_stored
        cat.items.append(
            DiagnosticItem(
                name="Hugging Face Token",
                status="OK",
                message=f"Configured ({_mask_secret(token)})",
            )
        )
    else:
        cat.items.append(
            DiagnosticItem(
                name="Hugging Face Token",
                status="OK",
                message="Not configured (optional: needed for HuggingFace Serverless inference fallback)",
            )
        )

    # 3. Anthropic Key
    anthropic_env = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_env:
        cat.items.append(
            DiagnosticItem(
                name="Anthropic API Key",
                status="OK",
                message=f"Configured ({_mask_secret(anthropic_env)})",
            )
        )
    else:
        cat.items.append(
            DiagnosticItem(
                name="Anthropic API Key",
                status="OK",
                message="Not configured (optional)",
            )
        )

    return cat


def check_local_models() -> DiagnosticCategory:
    cat = DiagnosticCategory(category="Local Models & Hardware")

    # Hardware detection
    hw_details = []
    if sys.platform == "darwin":
        hw_details.append("Apple Silicon / macOS")
    hw_details.append(f"{os.cpu_count() or 1} CPU cores")

    cat.items.append(
        DiagnosticItem(
            name="Inference Hardware",
            status="OK",
            message=", ".join(hw_details),
        )
    )

    # FastEmbed cache
    fastembed_cache = Path.home() / ".cache" / "fastembed"
    if fastembed_cache.is_dir() and any(fastembed_cache.iterdir()):
        cached_models = [d.name for d in fastembed_cache.iterdir() if d.is_dir()]
        cat.items.append(
            DiagnosticItem(
                name="FastEmbed Cache",
                status="OK",
                message=f"Cached models: {', '.join(cached_models[:2])}",
            )
        )
    else:
        cat.items.append(
            DiagnosticItem(
                name="FastEmbed Cache",
                status="OK",
                message="No pre-cached models (downloaded on-demand when semantic sync runs)",
            )
        )

    # Hugging Face hub cache
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    if hf_cache.is_dir() and any(hf_cache.iterdir()):
        cat.items.append(
            DiagnosticItem(
                name="HuggingFace Hub Cache",
                status="OK",
                message=f"Cache active at {hf_cache.name}",
            )
        )
    else:
        cat.items.append(
            DiagnosticItem(
                name="HuggingFace Hub Cache",
                status="OK",
                message="No local model weights cached yet",
            )
        )

    return cat


def check_project_scaffolding(project_root: Path) -> DiagnosticCategory:
    cat = DiagnosticCategory(category="Project Configuration & Section Cards")

    # Config file
    config_file = project_root / ".writing-context" / "config.yaml"
    if config_file.exists():
        try:
            load_config(str(project_root))
            cat.items.append(
                DiagnosticItem(
                    name="Config",
                    status="OK",
                    message=f"Loaded from {config_file.relative_to(project_root)}",
                )
            )
        except Exception as e:
            cat.items.append(
                DiagnosticItem(
                    name="Config",
                    status="FAIL",
                    message=f"Failed to parse config: {e}",
                    remediation="Re-run 'writing-context-rtfm init' to regenerate config.yaml",
                )
            )
    else:
        cat.items.append(
            DiagnosticItem(
                name="Config",
                status="WARN",
                message="config.yaml not found (using default settings)",
                remediation="Run 'writing-context-rtfm init' to generate project configuration",
            )
        )

    # Section Cards
    split_gen = project_root / ".writing-context" / "cards.generated.yaml"
    sc_file = project_root / ".writing-context" / "section_cards.yaml"

    target_cards = split_gen if split_gen.exists() else sc_file
    if target_cards.exists():
        try:
            cards = load_section_cards(str(target_cards), required=False)
            count = len(cards.sections) if cards else 0
            cat.items.append(
                DiagnosticItem(
                    name="Section Cards",
                    status="OK",
                    message=f"Parsed {count} sections from {target_cards.relative_to(project_root)}",
                )
            )
        except Exception as e:
            cat.items.append(
                DiagnosticItem(
                    name="Section Cards",
                    status="FAIL",
                    message=f"Failed to parse section cards: {e}",
                    remediation="Rebuild section cards: 'writing-context-rtfm cards rebuild'",
                )
            )
    # Cache DB
    try:
        config = load_config(str(project_root))
        cache_db = Path(config.cache.path)
        if not cache_db.is_absolute():
            cache_db = project_root / cache_db
        try:
            rel_cache = str(cache_db.relative_to(project_root))
        except ValueError:
            rel_cache = str(cache_db)

        if cache_db.exists():
            cat.items.append(
                DiagnosticItem(
                    name="Cache DB",
                    status="OK",
                    message=f"Found and initialized at {rel_cache}",
                )
            )
        else:
            cat.items.append(
                DiagnosticItem(
                    name="Cache DB",
                    status="OK",
                    message=f"Not found (will be automatically created at {rel_cache})",
                )
            )
    except Exception:
        pass

    return cat


def run_diagnostics(project_root: str | Path = ".") -> DoctorReport:
    """Run all 6 diagnostic health check categories and compile report."""
    root = Path(project_root).resolve()

    categories = [
        check_python(),
        check_dependencies(),
        check_index(root),
        check_zotero_and_bibtex(root),
        check_api_keys(root),
        check_local_models(),
        check_project_scaffolding(root),
    ]

    has_critical_failures = False
    remediations: list[str] = []

    for cat in categories:
        for item in cat.items:
            if item.status == "FAIL":
                has_critical_failures = True
            if item.remediation and item.remediation not in remediations:
                remediations.append(item.remediation)

    healthy = not has_critical_failures

    return DoctorReport(
        healthy=healthy,
        has_critical_failures=has_critical_failures,
        categories=categories,
        remediations=remediations,
    )


def format_text_report(report: DoctorReport) -> str:
    """Format the diagnostic report as clear terminal text."""
    lines: list[str] = []
    lines.append("Writing Context RTFM Extension Doctor")
    lines.append("======================================")

    for cat in report.categories:
        lines.append(f"\n[{cat.category}]")
        for item in cat.items:
            tag = f"[{'OK' if item.status == 'OK' else item.status}]"
            prefix = f"[*] {item.name}:"
            lines.append(f"{prefix:<22}{tag} {item.message}")

    lines.append("\n" + "=" * 38)
    if report.has_critical_failures:
        lines.append("Doctor Summary: ❌ FAILURES DETECTED")
    elif report.remediations:
        lines.append("Doctor Summary: ⚠️  HEALTHY WITH SUGGESTIONS")
    else:
        lines.append("Doctor Summary: ✅ ALL CHECKS PASSED")

    if report.remediations:
        lines.append("\nSuggested Actions:")
        for idx, rem in enumerate(report.remediations, 1):
            lines.append(f"  {idx}. {rem}")

    return "\n".join(lines)
