import logging
import re
from pathlib import Path
from typing import List, Optional, Dict
from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.providers.manager import get_shared_manager

logger = logging.getLogger("mcp-server")

class ZoteroProvider(BaseContextProvider):
    def __init__(self, config: AppConfig):
        self.config = config

    @property
    def provider_id(self) -> str:
        return "zotero"

    def is_available(self, config: AppConfig) -> bool:
        provider_cfg = config.providers.get("zotero")
        return provider_cfg is not None and provider_cfg.enabled and provider_cfg.mcp_server is not None

    def fetch_context(self, queries: List[str], target: Optional[str], limit: int, query_type_map: Optional[Dict[str, str]] = None, task_type: Optional[str] = None) -> List[SourceSpan]:
        provider_cfg = self.config.providers.get("zotero")
        if not provider_cfg or not provider_cfg.enabled or not provider_cfg.mcp_server:
            return []

        query_type_map = query_type_map or {}
        cmd = provider_cfg.mcp_server.command
        args = provider_cfg.mcp_server.args or []
        extra_cfg = provider_cfg.extra or {}
        include_abstract = extra_cfg.get("include_abstract", False)
        
        manager = get_shared_manager(self.config.rtfm.project_root)
        spans = []

        # 1. Parse citation keys from the target file and any dependency files
        cite_keys = []
        try:
            from writing_context_rtfm.section_cards import load_section_cards
            cards = load_section_cards(self.config.section_cards.path, required=False)
        except Exception:
            cards = None

        target_files = []
        if cards and target and target in cards.sections:
            target_card = cards.sections[target]
            if target_card.path:
                target_files.append(Path(self.config.rtfm.project_root) / target_card.path)
            for dep_id in (target_card.depends_on or []):
                if dep_id in cards.sections:
                    dep_card = cards.sections[dep_id]
                    if dep_card.path:
                        target_files.append(Path(self.config.rtfm.project_root) / dep_card.path)

        for fpath in target_files:
            if fpath.exists():
                try:
                    content = fpath.read_text(encoding="utf-8")
                    for match in re.findall(r'\\cite(?:[a-zA-Z]*)\{([^}]+)\}', content):
                        for k in match.split(','):
                            k_clean = k.strip()
                            if k_clean and k_clean not in cite_keys:
                                cite_keys.append(k_clean)
                except Exception as e:
                    logger.warning(f"ZoteroProvider failed to extract citations from {fpath}: {e}")

        # 2. Resolve extracted citation keys directly via Zotero
        resolved_keys = set()
        for key in cite_keys:
            try:
                res = manager.call_tool(
                    command=cmd,
                    args=args,
                    tool_name="zotero_search_by_citation_key",
                    arguments={"citekey": key},
                    env=provider_cfg.mcp_server.env
                )
                content_blocks = getattr(res, "content", [])
                block_text = ""
                for block in content_blocks:
                    if getattr(block, "type", None) == "text" and getattr(block, "text", None):
                        block_text += block.text

                # Smart fallback: if Better BibTeX is not running or key not in Extra,
                # extract author and year from citation key and search
                if "No item found with citation key" in block_text or not block_text.strip():
                    author_match = re.match(r'^([a-zA-Z]+)', key)
                    year_match = re.search(r'(\d{4})', key)
                    if author_match and year_match:
                        author = author_match.group(1)
                        year = year_match.group(1)
                        fallback_query = f"{author} {year}"
                        logger.info(f"Citation key '{key}' not found. Trying fallback search: '{fallback_query}'")
                        
                        fallback_res = manager.call_tool(
                            command=cmd,
                            args=args,
                            tool_name="zotero_search_items",
                            arguments={"query": fallback_query, "limit": 3},
                            env=provider_cfg.mcp_server.env
                        )
                        fallback_blocks = getattr(fallback_res, "content", [])
                        block_text = ""
                        for fb in fallback_blocks:
                            if getattr(fb, "type", None) == "text" and getattr(fb, "text", None):
                                block_text += fb.text

                if block_text.strip() and "No item found" not in block_text:
                    # Try to extract the 8-character Zotero item key to retrieve annotations/notes
                    item_key_match = re.search(r'Item Key:\s*(\w{8})', block_text, re.IGNORECASE)
                    ann_text = ""
                    if item_key_match:
                        z_key = item_key_match.group(1)
                        try:
                            ann_res = manager.call_tool(
                                command=cmd,
                                args=args,
                                tool_name="zotero_get_annotations",
                                arguments={"item_key": z_key},
                                env=provider_cfg.mcp_server.env
                            )
                            for ann_block in getattr(ann_res, "content", []):
                                if getattr(ann_block, "type", None) == "text" and getattr(ann_block, "text", None):
                                    ann_text += "\n" + ann_block.text
                        except Exception:
                            pass

                    snippet = block_text
                    if ann_text:
                        snippet += "\n\n### User Highlights & Annotations:\n" + ann_text

                    spans.append(SourceSpan(
                        path=f"zotero:{key}",
                        line_start=None,
                        line_end=None,
                        reason=f"Zotero reference for citation key '{key}'",
                        score=0.95,
                        priority="supporting",
                        source_role="reference",
                        metadata={"snippet": snippet, "citekey": key}
                    ))
                    resolved_keys.add(key)
            except Exception as e:
                logger.error(f"Zotero citation key lookup failed for '{key}': {e}")

        # 3. External Search (Semantic / Keyword)
        # Skip if proofreading to avoid context contamination
        if task_type == "proofread":
            logger.info("Task type is proofread; skipping external Zotero searches to avoid context contamination.")
            return spans

        def _parse_and_append_markdown_items(markdown_text: str, source_reason: str, score: float):
            # Split by markdown headers like "## 1. Title"
            parts = re.split(r'(?m)^## \d+\.\s+', markdown_text)
            if len(parts) <= 1:
                # No numbered items found, maybe just one big chunk or error
                if not include_abstract:
                    # Naively strip abstract from whole text if possible
                    markdown_text = re.sub(r'\*\*Abstract:\*\*\s*\n(?:.|\n)*?(?=\n\n|\Z)', '', markdown_text)
                spans.append(SourceSpan(
                    path=f"zotero:{q}",
                    line_start=None,
                    line_end=None,
                    reason=source_reason,
                    score=score,
                    priority="supporting",
                    source_role="reference",
                    metadata={"snippet": markdown_text}
                ))
                return

            # parts[0] is the intro ("# Search Results... Found N items:")
            for item_text in parts[1:]:
                item_text = item_text.strip()
                if not item_text:
                    continue
                
                # Re-add a visual header for the snippet
                snippet = f"## {item_text}"
                
                # Try to extract a specific citation key for the path
                cite_match = re.search(r'\*\*Citation Key:\*\*\s*([\w-]+)', snippet)
                item_key_match = re.search(r'\*\*Item Key:\*\*\s*([\w]+)', snippet)
                
                cite_key = cite_match.group(1) if cite_match else None
                item_key = item_key_match.group(1) if item_key_match else None
                
                # Determine path
                span_path = f"zotero:{cite_key}" if cite_key else (f"zotero:{item_key}" if item_key else f"zotero:{q}")
                
                # Exclude abstract if configured
                if not include_abstract:
                    snippet = re.sub(r'\*\*Abstract:\*\*\s*\n(?:.|\n)*?(?=\n\n##|\Z)', '', snippet).strip()

                spans.append(SourceSpan(
                    path=span_path,
                    line_start=None,
                    line_end=None,
                    reason=source_reason,
                    score=score,
                    priority="supporting",
                    source_role="reference",
                    metadata={"snippet": snippet, "citekey": cite_key}
                ))

        semantic_types = {"intent", "dep_intent", "thesis", "task", "title", "dep_title"}
        
        for q in queries:
            if q in cite_keys or q in resolved_keys:
                continue
                
            q_type = query_type_map.get(q, "task_keyword")
            is_semantic = q_type in semantic_types
            
            try:
                if is_semantic:
                    # Attempt Semantic Search for high-level intents
                    try:
                        res = manager.call_tool(
                            command=cmd,
                            args=args,
                            tool_name="zotero_semantic_search",
                            arguments={"query": q, "limit": limit},
                            env=provider_cfg.mcp_server.env
                        )
                        content_blocks = getattr(res, "content", [])
                        block_text = "".join([b.text for b in content_blocks if getattr(b, "type", None) == "text" and getattr(b, "text", None)])
                        
                        if "Semantic search is not available" in block_text or "Error" in block_text:
                            # Fallback if semantic fails
                            raise Exception(block_text)
                            
                        _parse_and_append_markdown_items(
                            markdown_text=block_text,
                            source_reason=f"Zotero semantic match for {q_type} '{q}'",
                            score=0.9
                        )
                        continue
                    except Exception as sem_e:
                        logger.info(f"Semantic search failed for '{q}', falling back to keyword search: {sem_e}")
                
                # Keyword search (for keywords, or as fallback)
                res = manager.call_tool(
                    command=cmd,
                    args=args,
                    tool_name="zotero_search_items",
                    arguments={"query": q, "limit": limit},
                    env=provider_cfg.mcp_server.env
                )
                content_blocks = getattr(res, "content", [])
                block_text = "".join([b.text for b in content_blocks if getattr(b, "type", None) == "text" and getattr(b, "text", None)])
                
                if block_text:
                    _parse_and_append_markdown_items(
                        markdown_text=block_text,
                        source_reason=f"Zotero metadata search match for '{q}'",
                        score=0.8
                    )
            except Exception as e:
                logger.error(f"Zotero search failed for query '{q}': {e}")
                
        return spans
