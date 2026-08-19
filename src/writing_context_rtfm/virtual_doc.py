import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pylatexenc.latexwalker import (  # type: ignore
    LatexCommentNode,
    LatexEnvironmentNode,
    LatexMacroNode,
    LatexWalker,
)

from writing_context_rtfm.ast_utils import get_braced_arg
from writing_context_rtfm.hashing import stable_hash

# Module-level AST cache: (abs_path, mtime, size) -> parsed elements
_LATEX_AST_CACHE: dict[
    tuple[str, float, int],
    tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        str,
    ],
] = {}

_MARKDOWN_AST_CACHE: dict[
    tuple[str, float, int],
    tuple[list[dict[str, Any]], list[str], str],
] = {}


@dataclass
class DocumentNode:
    node_id: str
    title: str
    source_path: str
    line_start: int
    line_end: int
    char_start: int
    char_end: int
    level: int
    selector: str
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    content_hash: str = ""
    word_count: int = 0

    # Deterministic metadata
    citations: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    figures: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    equations: list[str] = field(default_factory=list)
    algorithms: list[str] = field(default_factory=list)


def sanitize_node_id(title: str) -> str:
    """Sanitize title into a stable node ID."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", title).strip("_").lower()
    if not sanitized.startswith("section_"):
        sanitized = f"section_{sanitized}"
    return sanitized


class VirtualDocumentParser:
    """Parses manuscripts (LaTeX or Markdown) and constructs a Virtual Document Tree."""

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self.nodes: dict[str, DocumentNode] = {}
        self.node_order: list[str] = []
        self.visited_files: set[str] = set()

        self.latex_levels = {
            "part": 0,
            "chapter": 1,
            "section": 2,
            "subsection": 3,
            "subsubsection": 4,
            "paragraph": 5,
            "subparagraph": 6,
        }
        self.main_level: int | None = None
        self.environments_by_file: dict[str, list[dict[str, Any]]] = {}

    def snap_to_environment(self, file_rel: str, line_start: int, line_end: int) -> tuple[int, int]:
        """Expands line_start and line_end outwards if they intersect a structural LaTeX environment."""
        envs = self.environments_by_file.get(file_rel, [])
        if not envs:
            return line_start, line_end

        structural_envs = {
            "equation",
            "equation*",
            "align",
            "align*",
            "gather",
            "gather*",
            "multline",
            "multline*",
            "table",
            "table*",
            "figure",
            "figure*",
            "algorithm",
            "algorithm*",
            "lstlisting",
            "minted",
            "tabular",
            "proof",
            "theorem",
            "lemma",
            "definition",
        }

        cur_start, cur_end = line_start, line_end
        for env in envs:
            env_name = env.get("env_name", "")
            if env_name in structural_envs:
                e_start = env.get("line_start", 1)
                e_end = env.get("line_end", e_start)
                # Check if requested line range overlaps with this environment
                if not (cur_end < e_start or cur_start > e_end):
                    cur_start = min(cur_start, e_start)
                    cur_end = max(cur_end, e_end)

        return cur_start, cur_end

    def parse(self, entry_file: str) -> dict[str, DocumentNode]:
        """Entry point to build the tree starting from a root file."""
        self.nodes.clear()
        self.node_order.clear()
        self.visited_files.clear()
        self.environments_by_file.clear()
        self.main_level = None

        rel_path = self._to_rel_path(entry_file)
        if not rel_path:
            return {}

        if rel_path.endswith(".tex"):
            self._parse_latex(rel_path)
        elif rel_path.endswith(".md"):
            self._parse_markdown(rel_path)

        # Resolve parent-child links globally based on heading levels
        self._resolve_hierarchy()
        return self.nodes

    def _to_rel_path(self, path_str: str) -> str | None:
        try:
            p = Path(path_str)
            if p.is_absolute():
                return str(p.relative_to(self.project_root))
            return str(p)
        except ValueError:
            return None

    def _resolve_include(self, current_file_rel: str, target: str) -> str | None:
        target = target.strip()
        if not target:
            return None
        current_abs = self.project_root / current_file_rel
        candidates = [
            current_abs.parent / target,
            current_abs.parent / (target + ".tex"),
            self.project_root / target,
            self.project_root / (target + ".tex"),
        ]
        for cand in candidates:
            if cand.is_file():
                try:
                    return str(cand.relative_to(self.project_root))
                except ValueError:
                    pass
        return None

    def _parse_latex(self, file_rel: str, parent_node_id: str | None = None) -> list[str]:
        """Recursively parses a LaTeX file and extracts section nodes."""
        file_abs = self.project_root / file_rel
        if not file_abs.is_file() or file_rel in self.visited_files:
            return []
        self.visited_files.add(file_rel)

        try:
            stat = file_abs.stat()
            cache_key = (str(file_abs), stat.st_mtime, stat.st_size)
        except Exception:
            cache_key = None

        if cache_key and cache_key in _LATEX_AST_CACHE:
            (
                headings,
                inclusions,
                labels,
                citations,
                references,
                environments,
                content,
            ) = _LATEX_AST_CACHE[cache_key]
        else:
            try:
                content = file_abs.read_text(encoding="utf-8")
            except Exception:
                return []

            walker = LatexWalker(content)
            try:
                nodes, _, _ = walker.get_latex_nodes()
            except Exception:
                return []

            # Find all structural and meta elements
            headings = []
            inclusions = []
            labels = []
            citations = []
            references = []
            environments = []

            def walk(node: Any) -> None:
                if node is None or node.isNodeType(LatexCommentNode):
                    return

                if node.isNodeType(LatexMacroNode):
                    macro = node.macroname
                    if macro in self.latex_levels:
                        title = get_braced_arg(node) or "Untitled"
                        headings.append(
                            {
                                "type": "heading",
                                "macro": macro,
                                "title": title,
                                "char_start": node.pos,
                                "char_end": node.pos + (node.len or 0),
                            }
                        )
                    elif macro in ("input", "include"):
                        target = get_braced_arg(node)
                        if target:
                            inclusions.append(
                                {
                                    "type": "inclusion",
                                    "target": target,
                                    "char_start": node.pos,
                                    "char_end": node.pos + (node.len or 0),
                                }
                            )
                    elif macro == "label":
                        key = get_braced_arg(node)
                        if key:
                            labels.append({"key": key, "pos": node.pos})
                    elif macro in ("ref", "cref", "Cref", "autoref") or macro.startswith("ref"):
                        key_str = get_braced_arg(node)
                        if key_str:
                            keys = [k.strip() for k in key_str.split(",") if k.strip()]
                            for k in keys:
                                references.append({"key": k, "pos": node.pos})
                    elif macro == "cite" or macro.startswith("cite"):
                        key_str = get_braced_arg(node)
                        if key_str:
                            keys = [k.strip() for k in key_str.split(",") if k.strip()]
                            for k in keys:
                                citations.append({"key": k, "pos": node.pos})

                elif node.isNodeType(LatexEnvironmentNode):
                    env_name = node.envname
                    # Look for label inside environment
                    env_label = None

                    def find_label(n: Any) -> None:
                        nonlocal env_label
                        if n is None or env_label:
                            return
                        if n.isNodeType(LatexMacroNode) and n.macroname == "label":
                            env_label = get_braced_arg(n)
                        if hasattr(n, "nodelist") and n.nodelist:
                            for c in n.nodelist:
                                find_label(c)

                    if node.nodelist:
                        for child in node.nodelist:
                            find_label(child)

                    environments.append(
                        {
                            "env_name": env_name,
                            "label": env_label or f"unlabeled_{env_name}",
                            "char_start": node.pos,
                            "char_end": node.pos + (node.len or 0),
                        }
                    )

                # Recurse node children
                if hasattr(node, "nodelist") and node.nodelist:
                    for child in node.nodelist:
                        walk(child)
                if hasattr(node, "nodeargs") and node.nodeargs:
                    for arg in node.nodeargs:
                        if arg is not None and hasattr(arg, "nodelist") and arg.nodelist:
                            for child in arg.nodelist:
                                walk(child)

            for n in nodes:
                walk(n)

            if cache_key:
                _LATEX_AST_CACHE[cache_key] = (
                    headings,
                    inclusions,
                    labels,
                    citations,
                    references,
                    environments,
                    content,
                )

        # Ensure environment line numbers are computed
        for env in environments:
            if "line_start" not in env:
                env["line_start"] = content.count("\n", 0, env["char_start"]) + 1
                env["line_end"] = content.count("\n", 0, env["char_end"]) + 1
        self.environments_by_file[file_rel] = environments

        # Sort headings and inclusions by char_start
        structural = sorted(headings + inclusions, key=lambda x: x["char_start"])

        # Filter headings to main sections only (minimum heading level found in file, e.g. \section or \chapter)
        all_headings = [item for item in structural if item["type"] == "heading"]
        if all_headings:
            if self.main_level is None:
                self.main_level = min(self.latex_levels[h["macro"]] for h in all_headings)
            main_headings = [
                h for h in all_headings if self.latex_levels[h["macro"]] <= self.main_level
            ]
        else:
            main_headings = []

        # Determine boundaries of sections within this file
        file_nodes = []
        first_main = main_headings[0] if main_headings else None
        preamble_end = first_main["char_start"] if first_main else len(content)

        # Preamble node (if there are elements before the first main section heading and no parent node)
        if preamble_end > 0 and not parent_node_id:
            preamble_level = self.main_level if self.main_level is not None else 1
            preamble_title = "Preamble"
            preamble_id = sanitize_node_id(f"{Path(file_rel).stem}_preamble")
            preamble_node = DocumentNode(
                node_id=preamble_id,
                title=preamble_title,
                source_path=file_rel,
                line_start=1,
                line_end=content.count("\n", 0, preamble_end) + 1,
                char_start=0,
                char_end=preamble_end,
                level=preamble_level,
                selector=f"/{preamble_title}",
            )
            preamble_node.content_hash = stable_hash(content[0:preamble_end])
            preamble_node.word_count = len(content[0:preamble_end].split())
            self.nodes[preamble_id] = preamble_node
            self.node_order.append(preamble_id)
            file_nodes.append(preamble_id)
            current_active_id = preamble_id
        else:
            current_active_id = parent_node_id or "document_main"

        # Generate nodes for each main section heading
        for _idx, item in enumerate(structural):
            if item["type"] == "heading" and item in main_headings:
                # Find end of this main section (at next main heading or end of content)
                char_start = item["char_start"]
                next_mains = [h for h in main_headings if h["char_start"] > char_start]
                char_end = next_mains[0]["char_start"] if next_mains else len(content)

                node_id = sanitize_node_id(item["title"])
                # Handle ID collisions
                base_id = node_id
                counter = 1
                while node_id in self.nodes:
                    node_id = f"{base_id}_{counter}"
                    counter += 1

                # Find label defined inside this section
                sec_label = None
                for lbl in labels:
                    if char_start <= lbl["pos"] < char_end:
                        sec_label = lbl["key"]
                        break

                selector = sec_label if sec_label else f"/{item['title']}"

                node = DocumentNode(
                    node_id=node_id,
                    title=item["title"],
                    source_path=file_rel,
                    line_start=content.count("\n", 0, char_start) + 1,
                    line_end=content.count("\n", 0, char_end) + 1,
                    char_start=char_start,
                    char_end=char_end,
                    level=self.latex_levels[item["macro"]],
                    selector=selector,
                )
                node.content_hash = stable_hash(content[char_start:char_end])
                node.word_count = len(content[char_start:char_end].split())

                self.nodes[node_id] = node
                self.node_order.append(node_id)
                file_nodes.append(node_id)
                current_active_id = node_id

            elif item["type"] == "inclusion":
                # Included file inherits parent context
                resolved = self._resolve_include(file_rel, item["target"])
                if resolved:
                    # Recursively parse included file
                    self._parse_latex(resolved, parent_node_id=current_active_id)

        # Assign elements (citations, labels, refs, envs) to sections
        for node_id in file_nodes:
            node = self.nodes[node_id]
            # Citations
            node.citations = [
                c["key"] for c in citations if node.char_start <= c["pos"] < node.char_end
            ]
            # References
            node.references = [
                r["key"] for r in references if node.char_start <= r["pos"] < node.char_end
            ]
            # Labels
            node.labels = [
                lbl["key"] for lbl in labels if node.char_start <= lbl["pos"] < node.char_end
            ]
            # Environments
            for env in environments:
                if node.char_start <= env["char_start"] < node.char_end:
                    lbl_key = env["label"]
                    name = env["env_name"].lower()
                    if "figure" in name:
                        node.figures.append(lbl_key)
                    elif "table" in name:
                        node.tables.append(lbl_key)
                    elif "equation" in name or "align" in name:
                        node.equations.append(lbl_key)
                    elif "algorithm" in name:
                        node.algorithms.append(lbl_key)

        # If a file has no main headings of its own, we assign its entire contents to the parent section
        if not main_headings and parent_node_id and parent_node_id in self.nodes:
            parent_node = self.nodes[parent_node_id]
            parent_node.citations.extend([c["key"] for c in citations])
            parent_node.references.extend([r["key"] for r in references])
            parent_node.labels.extend([lbl["key"] for lbl in labels])
            for env in environments:
                lbl = env["label"]
                name = env["env_name"].lower()
                if "figure" in name:
                    parent_node.figures.append(lbl)
                elif "table" in name:
                    parent_node.tables.append(lbl)
                elif "equation" in name or "align" in name:
                    parent_node.equations.append(lbl)
                elif "algorithm" in name:
                    parent_node.algorithms.append(lbl)

        return file_nodes

    def _parse_markdown(self, file_rel: str) -> None:
        """Parses a Markdown file and extracts main section nodes."""
        file_abs = self.project_root / file_rel
        if not file_abs.is_file():
            return

        try:
            stat = file_abs.stat()
            cache_key = (str(file_abs), stat.st_mtime, stat.st_size)
        except Exception:
            cache_key = None

        if cache_key and cache_key in _MARKDOWN_AST_CACHE:
            headings, lines, content = _MARKDOWN_AST_CACHE[cache_key]
        else:
            try:
                content = file_abs.read_text(encoding="utf-8")
            except Exception:
                return

            lines = content.splitlines()
            headings = []

            # Find heading lines e.g. "## Introduction"
            for idx, line in enumerate(lines):
                match = re.match(r"^(\#{1,6})\s+(.+)$", line)
                if match:
                    level = len(match.group(1))
                    title_raw = match.group(2).strip()
                    # Parse markdown anchor if exists, e.g. "Heading {#anchor}"
                    anchor_match = re.search(r"\{\#([a-zA-Z0-9_-]+)\}$", title_raw)
                    title = title_raw
                    selector = ""
                    if anchor_match:
                        selector = anchor_match.group(1)
                        title = title_raw[: anchor_match.start()].strip()
                    headings.append(
                        {
                            "line_idx": idx,
                            "level": level,
                            "title": title,
                            "selector": selector,
                        }
                    )

            if cache_key:
                _MARKDOWN_AST_CACHE[cache_key] = (headings, lines, content)

        if not headings:
            # Whole file is one node
            node_id = sanitize_node_id(Path(file_rel).stem)
            node = DocumentNode(
                node_id=node_id,
                title=Path(file_rel).stem.replace("_", " ").title(),
                source_path=file_rel,
                line_start=1,
                line_end=len(lines),
                char_start=0,
                char_end=len(content),
                level=1,
                selector=f"/{Path(file_rel).stem}",
            )
            node.content_hash = stable_hash(content)
            node.word_count = len(content.split())
            self.nodes[node_id] = node
            self.node_order.append(node_id)
            return

        # Main section headings only (minimum heading level e.g. # or ##)
        min_level = min(int(h["level"]) for h in headings)
        main_headings = [h for h in headings if int(h["level"]) == min_level]

        for idx, h in enumerate(main_headings):
            line_start = int(h["line_idx"]) + 1
            # Next main heading boundary
            line_end = (
                int(main_headings[idx + 1]["line_idx"])
                if idx + 1 < len(main_headings)
                else len(lines)
            )

            sec_lines = lines[line_start - 1 : line_end]
            sec_text = "\n".join(sec_lines)

            h_title = str(h["title"])
            node_id = sanitize_node_id(h_title)
            base_id = node_id
            counter = 1
            while node_id in self.nodes:
                node_id = f"{base_id}_{counter}"
                counter += 1

            h_sel = h.get("selector")
            selector = str(h_sel) if h_sel else f"/{h_title}"

            # Calculate char offsets
            char_start = sum(len(line_str) + 1 for line_str in lines[: line_start - 1])
            char_end = char_start + len(sec_text)

            node = DocumentNode(
                node_id=node_id,
                title=h_title,
                source_path=file_rel,
                line_start=line_start,
                line_end=line_end,
                char_start=char_start,
                char_end=char_end,
                level=int(h["level"]),
                selector=selector,
            )
            node.content_hash = stable_hash(sec_text)
            node.word_count = len(sec_text.split())

            # Parse citations e.g. [@smith2020] or cite{smith2020}
            node.citations = re.findall(r"@([a-zA-Z0-9_-]+)", sec_text)

            self.nodes[node_id] = node
            self.node_order.append(node_id)

    def _resolve_hierarchy(self) -> None:
        """Walks the parsed nodes and sets parent to document_main for all main section nodes."""
        for nid in self.node_order:
            if nid in self.nodes:
                node = self.nodes[nid]
                node.parent = "document_main"
                node.children = []
