"""LaTeX AST Parsing and Reference Graph construction using pylatexenc."""

import os
import re
from pathlib import Path
from typing import Any

from pylatexenc.latexwalker import (  # type: ignore
    LatexCommentNode,
    LatexEnvironmentNode,
    LatexMacroNode,
    LatexMathNode,
    LatexWalker,
)

from writing_context_rtfm.ast_utils import get_braced_arg

MACRO_NAME_PAT = re.compile(
    r"^(cite[a-zA-Z]*|ref[a-zA-Z]*|eqref|label|cref|Cref|autoref|pageref|vref)$"
)


def scan_latex_commands(text: str) -> list[str]:
    """Scan text for LaTeX citations, labels, refs, and math environments using pylatexenc AST."""
    found = []

    walker = LatexWalker(text)
    try:
        nodes, _, _ = walker.get_latex_nodes()
    except Exception:
        # Fall back to empty on bad syntax
        return []

    def walk(node: Any) -> None:
        if node is None or node.isNodeType(LatexCommentNode):
            return

        if node.isNodeType(LatexMacroNode):
            if MACRO_NAME_PAT.match(node.macroname):
                m_clean = node.latex_verbatim().strip().replace("\n", " ")
                if m_clean not in found:
                    found.append(m_clean)
        elif node.isNodeType(LatexMathNode) or node.isNodeType(LatexEnvironmentNode):
            m_clean = node.latex_verbatim().strip().replace("\n", " ")
            if m_clean not in found:
                found.append(m_clean)

        if hasattr(node, "nodelist") and node.nodelist:
            for child in node.nodelist:
                walk(child)
        if hasattr(node, "nodeargs") and node.nodeargs:
            for arg in node.nodeargs:
                if arg is not None and hasattr(arg, "nodelist") and arg.nodelist:
                    for child in arg.nodelist:
                        walk(child)

    for node in nodes:
        walk(node)

    return found


def build_reference_graph(project_root: str) -> dict[str, Any]:
    """Parse all LaTeX files in the project root to build a cross-reference and dependency graph."""
    from writing_context_rtfm.utils import is_allowed_source, is_path_ignored, load_ignore_spec

    project_path = Path(project_root).resolve()
    ignore_spec = load_ignore_spec(project_path)

    tex_files: list[Path] = []
    labels: dict[str, Any] = {}
    references: dict[str, list[str]] = {}
    citations: dict[str, list[str]] = {}
    file_dependencies: dict[str, list[str]] = {}

    # 1. Scan for all allowed .tex files
    for root_dir, dirs, files in os.walk(project_path):
        dirs[:] = [
            d
            for d in dirs
            if is_allowed_source(str(Path(root_dir) / d))
            and not is_path_ignored(
                (Path(root_dir) / d).relative_to(project_path), ignore_spec, is_dir=True
            )
        ]
        for file in files:
            if file.endswith(".tex"):
                full_path = Path(root_dir) / file
                rel_file = full_path.relative_to(project_path)
                if is_path_ignored(rel_file, ignore_spec):
                    continue
                if is_allowed_source(str(full_path)):
                    tex_files.append(full_path)

    # Initialize entries for all files
    for f in tex_files:
        rel_path = str(f.relative_to(project_path))
        references[rel_path] = []
        citations[rel_path] = []
        file_dependencies[rel_path] = []

    # Helper to resolve included files
    def resolve_include(current_file_path: Path, target: str) -> str | None:
        target = target.strip()
        if not target:
            return None
        candidates = [
            current_file_path.parent / target,
            current_file_path.parent / (target + ".tex"),
            project_path / target,
            project_path / (target + ".tex"),
        ]
        for cand in candidates:
            if cand.is_file():
                try:
                    return str(cand.relative_to(project_path))
                except ValueError:
                    pass
        return None

    # 2. Parse each file
    for f in tex_files:
        rel_path = str(f.relative_to(project_path))
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            continue

        walker = LatexWalker(content)
        try:
            nodes, _, _ = walker.get_latex_nodes()
        except Exception:
            continue

        def walk(node: Any) -> None:
            if node is None or node.isNodeType(LatexCommentNode):
                return

            if node.isNodeType(LatexMacroNode):
                macro = node.macroname
                # Check for \label
                if macro == "label":
                    key = get_braced_arg(node)
                    if key:
                        line = content.count("\n", 0, node.pos) + 1
                        labels[key] = {"file": rel_path, "line": line}
                # Check for references
                elif macro in ("ref", "cref", "Cref", "autoref") or macro.startswith("ref"):
                    key_str = get_braced_arg(node)
                    if key_str:
                        keys = [k.strip() for k in key_str.split(",") if k.strip()]
                        for k in keys:
                            if k not in references[rel_path]:
                                references[rel_path].append(k)
                # Check for citations
                elif macro == "cite" or macro.startswith("cite"):
                    key_str = get_braced_arg(node)
                    if key_str:
                        keys = [k.strip() for k in key_str.split(",") if k.strip()]
                        for k in keys:
                            if k not in citations[rel_path]:
                                citations[rel_path].append(k)
                # Check for inputs / includes
                elif macro in ("input", "include"):
                    target = get_braced_arg(node)
                    if target:
                        resolved = resolve_include(f, target)
                        if resolved and resolved not in file_dependencies[rel_path]:
                            file_dependencies[rel_path].append(resolved)

            # Recurse children / arguments
            if hasattr(node, "nodelist") and node.nodelist:
                for child in node.nodelist:
                    walk(child)
            if hasattr(node, "nodeargs") and node.nodeargs:
                for arg in node.nodeargs:
                    if arg is not None:
                        if hasattr(arg, "nodelist") and arg.nodelist:
                            for child in arg.nodelist:
                                walk(child)

        for node in nodes:
            walk(node)

    return {
        "files": [str(f.relative_to(project_path)) for f in tex_files],
        "labels": labels,
        "references": references,
        "citations": citations,
        "file_dependencies": file_dependencies,
    }


def extract_latex_macros(text: str) -> dict[str, str]:
    """Extract custom LaTeX macro and operator definitions from text or preamble.

    Supports:
    - \\newcommand{\\macro}{def} / \\newcommand\\macro{def}
    - \\renewcommand / \\providecommand
    - \\DeclareMathOperator{\\op}{name} / \\DeclareMathOperator*{\\op}{name}
    - \\def\\macro{def} / \\def\\macro#1{def}
    """
    macros: dict[str, str] = {}
    preamble = text.split(r"\begin{document}")[0] if r"\begin{document}" in text else text

    # 1. \newcommand, \renewcommand, \providecommand, \DeclareMathOperator
    p1 = re.compile(
        r"\\(?:newcommand|renewcommand|providecommand|DeclareMathOperator)\*?\s*"
        r"(?:\{\s*\\([a-zA-Z@]+)\s*\}|\\([a-zA-Z@]+))\s*"
        r"(?:\[(\d+)\])?\s*"
        r"\{((?:[^{}]|\{[^{}]*\})*)\}"
    )
    for m in p1.finditer(preamble):
        name = m.group(1) or m.group(2)
        args_count = m.group(3)
        body = m.group(4).strip()
        macro_key = f"\\{name}"
        if args_count:
            macros[macro_key] = f"[{args_count}] {body}"
        else:
            macros[macro_key] = body

    # 2. \def
    p2 = re.compile(r"\\def\s*\\([a-zA-Z@]+)\s*([^{]*)\s*\{((?:[^{}]|\{[^{}]*\})*)\}")
    for m in p2.finditer(preamble):
        name = m.group(1)
        params = m.group(2).strip()
        body = m.group(3).strip()
        macro_key = f"\\{name}"
        if macro_key not in macros:
            macros[macro_key] = f"{params} {body}".strip() if params else body

    return macros


_PREAMBLE_MACRO_CACHE: dict[str, tuple[float, dict[str, str]]] = {}


def find_preamble_macros(project_root: str | Path) -> dict[str, str]:
    """Discover preamble and macro files in project_root and extract all author-defined macros.

    Scans common macro files (macros.tex, preamble.tex, defs.tex, notation.tex)
    as well as the preambles of entry-point .tex files (main.tex, paper.tex).
    Results are cached by file modification timestamps.
    """
    global _PREAMBLE_MACRO_CACHE
    root = Path(project_root).resolve()

    # Priority candidate files
    candidate_names = (
        "macros.tex",
        "preamble.tex",
        "defs.tex",
        "definitions.tex",
        "notation.tex",
        "commands.tex",
        "main.tex",
        "paper.tex",
        "thesis.tex",
    )

    found_files: list[Path] = []
    for name in candidate_names:
        p = root / name
        if p.is_file():
            found_files.append(p)

    # If no standard entry/macro files found, inspect all top-level .tex files
    if not found_files:
        for p in root.glob("*.tex"):
            if p.is_file():
                found_files.append(p)

    all_macros: dict[str, str] = {}
    for f in found_files:
        cache_key = str(f)
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue

        cached_entry = _PREAMBLE_MACRO_CACHE.get(cache_key)
        if cached_entry is not None and cached_entry[0] == mtime:
            all_macros.update(cached_entry[1])
            continue

        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            file_macros = extract_latex_macros(content)
            _PREAMBLE_MACRO_CACHE[cache_key] = (mtime, file_macros)
            all_macros.update(file_macros)
        except Exception:
            continue

    return all_macros
