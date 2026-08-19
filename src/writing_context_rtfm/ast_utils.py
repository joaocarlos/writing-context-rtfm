"""Consolidated AST utilities for LaTeX and Markdown parsing."""

from typing import Any

try:
    from pylatexenc.latexwalker import LatexCharsNode  # type: ignore[import-untyped]
except ImportError:
    LatexCharsNode = Any


def get_clean_arg_text(group_node: Any) -> str:
    """Extract clean plain text from a pylatexenc group or node argument."""
    if group_node is None:
        return ""
    if not hasattr(group_node, "nodelist") or not group_node.nodelist:
        val = group_node.latex_verbatim() if hasattr(group_node, "latex_verbatim") else ""
        if val.startswith("{") and val.endswith("}"):
            return str(val[1:-1]).strip()
        return str(val).strip()
    parts = []
    for child in group_node.nodelist:
        if (
            child is not None
            and getattr(child, "isNodeType", None)
            and child.isNodeType(LatexCharsNode)
        ):
            parts.append(child.chars)
        elif child is not None and hasattr(child, "latex_verbatim"):
            parts.append(child.latex_verbatim())
    return "".join(parts).strip()


def get_braced_arg(node: Any) -> str | None:
    """Extract the first braced argument ({...}) from a macro or environment node."""
    if not getattr(node, "nodeargs", None):
        return None
    for arg in node.nodeargs:
        if arg is not None and getattr(arg, "delimiters", None) == ("{", "}"):
            return get_clean_arg_text(arg)
    return None
