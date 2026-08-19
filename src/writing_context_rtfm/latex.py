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

MACRO_NAME_PAT = re.compile(r"^(cite[a-zA-Z]*|ref[a-zA-Z]*|label|cref|Cref|autoref)$")


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
    from writing_context_rtfm.utils import is_allowed_source

    project_path = Path(project_root).resolve()

    tex_files: list[Path] = []
    labels: dict[str, Any] = {}
    references: dict[str, list[str]] = {}
    citations: dict[str, list[str]] = {}
    file_dependencies: dict[str, list[str]] = {}

    # 1. Scan for all allowed .tex files
    for root_dir, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if is_allowed_source(str(Path(root_dir) / d))]
        for file in files:
            if file.endswith(".tex"):
                full_path = Path(root_dir) / file
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
