import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.providers.manager import get_shared_manager
from writing_context_rtfm.schemas import SourceSpan

logger = logging.getLogger("mcp-server")


@dataclass(frozen=True)
class _ZoteroLibrary:
    name: str
    library_id: str
    library_type: str


@dataclass(frozen=True)
class _ZoteroCollection:
    name: str
    path: str
    key: str


def _normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _result_text(result: Any) -> str:
    return "".join(
        block.text
        for block in getattr(result, "content", [])
        if getattr(block, "type", None) == "text" and getattr(block, "text", None)
    )


def _parse_libraries(markdown_text: str) -> list[_ZoteroLibrary]:
    libraries: list[_ZoteroLibrary] = []
    section_type = "user"
    pattern = re.compile(
        r"^- \*\*(?P<name>.+?)\*\*.*\((?P<label>libraryID|groupID)=(?P<id>\d+)\)"
    )
    for line in markdown_text.splitlines():
        if line.startswith("## User Library"):
            section_type = "user"
            continue
        if line.startswith("## Group Libraries"):
            section_type = "group"
            continue
        if line.startswith("## RSS Feeds"):
            section_type = "feed"
            continue
        match = pattern.match(line.strip())
        if not match:
            continue
        library_type = "group" if match.group("label") == "groupID" else section_type
        libraries.append(
            _ZoteroLibrary(
                name=match.group("name").strip(),
                library_id=match.group("id"),
                library_type=library_type,
            )
        )
    return libraries


def _parse_collections(markdown_text: str) -> list[_ZoteroCollection]:
    collections: list[_ZoteroCollection] = []
    ancestors: list[str] = []
    pattern = re.compile(
        r"^(?P<indent> *)- \*\*(?P<name>.+?)\*\* \(Key: (?P<key>[A-Za-z0-9]{8})\)"
    )
    for line in markdown_text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        indent = len(match.group("indent"))
        if indent % 2:
            raise ValueError("Zotero returned a collection tree with invalid indentation")
        depth = indent // 2
        if depth > len(ancestors):
            raise ValueError("Zotero returned a collection tree with a missing parent")
        name = match.group("name").strip()
        ancestors = ancestors[:depth]
        path = " / ".join([*ancestors, name])
        collections.append(_ZoteroCollection(name=name, path=path, key=match.group("key")))
        ancestors.append(name)
    return collections


def _parse_collection_item_keys(markdown_text: str) -> set[str]:
    return set(re.findall(r"(?m)^- `([A-Za-z0-9]{8})`\s+\|", markdown_text))


def _deduplicate_spans(spans: list[SourceSpan]) -> list[SourceSpan]:
    deduplicated: list[SourceSpan] = []
    positions: dict[str, int] = {}
    for span in spans:
        metadata = span.metadata or {}
        identity = str(metadata.get("item_key") or metadata.get("citekey") or span.path)
        if identity not in positions:
            positions[identity] = len(deduplicated)
            deduplicated.append(span)
            continue
        position = positions[identity]
        if span.score > deduplicated[position].score:
            deduplicated[position] = span
    return deduplicated


class ZoteroProvider(BaseContextProvider):
    def __init__(self, config: AppConfig):
        self.config = config
        self._library_scope_initialized = False
        self._library: _ZoteroLibrary | None = None
        self._resolved_collections: tuple[_ZoteroCollection, ...] | None = None
        self._allowed_item_keys: set[str] | None = None

    @property
    def provider_id(self) -> str:
        return "zotero"

    def is_available(self, config: AppConfig) -> bool:
        provider_cfg = config.providers.get("zotero")
        return (
            provider_cfg is not None
            and provider_cfg.enabled
            and provider_cfg.mcp_server is not None
        )

    def get_fingerprint(self, config: AppConfig) -> str | None:
        """Return lightweight revision token or fingerprint for Zotero provider if configured."""
        if not self.is_available(config):
            return None
        provider_cfg = config.providers.get("zotero")
        extra_cfg = (provider_cfg.extra or {}) if provider_cfg else {}
        revision = extra_cfg.get("revision_token") or extra_cfg.get("library_version")
        library_name = extra_cfg.get("library_name")
        collections = extra_cfg.get("collections")
        include_subcollections = extra_cfg.get("include_subcollections", True)
        if revision or library_name or collections:
            from writing_context_rtfm.hashing import stable_hash

            return stable_hash(
                "zotero",
                str(revision or ""),
                _normalize_name(str(library_name or ("My Library" if collections else ""))),
                "\n".join(_normalize_name(str(item)) for item in (collections or [])),
                str(bool(include_subcollections)),
            )
        return None

    def _collection_names(self) -> tuple[str, ...]:
        provider_cfg = self.config.providers.get("zotero")
        extra_cfg = (provider_cfg.extra or {}) if provider_cfg else {}
        configured = extra_cfg.get("collections", [])
        if configured is None:
            return ()
        if not isinstance(configured, list) or any(
            not isinstance(name, str) or not name.strip() for name in configured
        ):
            raise ValueError("providers.zotero.extra.collections must be a list of names")
        return tuple(name.strip() for name in configured)

    def _requested_library_name(self) -> str | None:
        provider_cfg = self.config.providers.get("zotero")
        extra_cfg = (provider_cfg.extra or {}) if provider_cfg else {}
        configured = extra_cfg.get("library_name")
        if configured is None:
            return "My Library" if self._collection_names() else None
        if not isinstance(configured, str) or not configured.strip():
            raise ValueError("providers.zotero.extra.library_name must be a non-empty name")
        return configured.strip()

    def _session_scope(self) -> str | None:
        requested = self._requested_library_name()
        return f"zotero:{_normalize_name(requested)}" if requested else None

    def _call_tool(
        self,
        manager: Any,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        provider_cfg = self.config.providers["zotero"]
        mcp_server = provider_cfg.mcp_server
        if mcp_server is None:
            raise RuntimeError("Zotero MCP server is not configured")
        kwargs: dict[str, Any] = {
            "command": mcp_server.command,
            "args": mcp_server.args or [],
            "tool_name": tool_name,
            "arguments": arguments,
            "env": mcp_server.env,
        }
        session_scope = self._session_scope()
        if session_scope:
            kwargs["session_scope"] = session_scope
        return manager.call_tool(**kwargs)

    def _ensure_library_selected(self, manager: Any) -> _ZoteroLibrary | None:
        if self._library_scope_initialized:
            return self._library
        requested = self._requested_library_name()
        if requested is None:
            self._library_scope_initialized = True
            return None

        response = self._call_tool(manager, "zotero_list_libraries", {})
        response_text = _result_text(response)
        candidates = [
            library
            for library in _parse_libraries(response_text)
            if _normalize_name(library.name) == _normalize_name(requested)
        ]
        if not candidates:
            raise ValueError(f"Zotero library '{requested}' was not found")
        if len(candidates) > 1:
            raise ValueError(f"Zotero library name '{requested}' is ambiguous")

        selected = candidates[0]
        switch_response = self._call_tool(
            manager,
            "zotero_switch_library",
            {"library_id": selected.library_id, "library_type": selected.library_type},
        )
        switch_text = _result_text(switch_response)
        if "error" in switch_text.casefold() or "successfully switched" not in switch_text.casefold():
            raise RuntimeError(
                f"Could not switch Zotero to library '{selected.name}': {switch_text.strip()}"
            )
        self._library = selected
        self._library_scope_initialized = True
        return selected

    def _ensure_collection_scope(self, manager: Any) -> tuple[_ZoteroCollection, ...]:
        if self._resolved_collections is not None:
            return self._resolved_collections
        configured_names = self._collection_names()
        if not configured_names:
            self._resolved_collections = ()
            self._allowed_item_keys = None
            return ()

        self._ensure_library_selected(manager)
        response = self._call_tool(
            manager,
            "zotero_get_collections",
            {"limit": 5000, "include_trashed": False},
        )
        available = _parse_collections(_result_text(response))
        resolved: list[_ZoteroCollection] = []
        for configured_name in configured_names:
            if "/" in configured_name:
                matches = [
                    item
                    for item in available
                    if _normalize_name(item.path) == _normalize_name(configured_name)
                ]
            else:
                matches = [
                    item
                    for item in available
                    if _normalize_name(item.name) == _normalize_name(configured_name)
                ]
            if not matches:
                raise ValueError(
                    f"Zotero collection '{configured_name}' was not found in the selected library"
                )
            if len(matches) > 1:
                raise ValueError(
                    f"ambiguous collection name '{configured_name}'; use its full path"
                )
            if matches[0] not in resolved:
                resolved.append(matches[0])

        provider_cfg = self.config.providers["zotero"]
        include_subcollections = bool(
            (provider_cfg.extra or {}).get("include_subcollections", True)
        )
        allowed_item_keys: set[str] = set()
        for collection in resolved:
            offset = 0
            seen_offsets: set[int] = set()
            while True:
                if offset in seen_offsets:
                    raise RuntimeError(
                        f"Zotero repeated collection pagination offset {offset} for '{collection.path}'"
                    )
                seen_offsets.add(offset)
                arguments: dict[str, Any] = {
                    "collection_key": collection.key,
                    "detail": "keys_only",
                    "limit": 1000,
                    "include_subcollections": include_subcollections,
                }
                if offset:
                    arguments["offset"] = offset
                item_response = self._call_tool(
                    manager, "zotero_get_collection_items", arguments
                )
                item_text = _result_text(item_response)
                if item_text.lstrip().casefold().startswith("error"):
                    raise RuntimeError(
                        f"Could not enumerate Zotero collection '{collection.path}': {item_text.strip()}"
                    )
                allowed_item_keys.update(_parse_collection_item_keys(item_text))
                next_match = re.search(r"Pass offset=(\d+) for the next page", item_text)
                if not next_match:
                    break
                offset = int(next_match.group(1))

        self._resolved_collections = tuple(resolved)
        self._allowed_item_keys = allowed_item_keys
        return self._resolved_collections


    def fetch_context(
        self,
        queries: list[str],
        target: str | None,
        limit: int,
        query_type_map: dict[str, str] | None = None,
        task_type: str | None = None,
    ) -> list[SourceSpan]:
        provider_cfg = self.config.providers.get("zotero")
        if not provider_cfg or not provider_cfg.enabled or not provider_cfg.mcp_server:
            return []

        query_type_map = query_type_map or {}
        extra_cfg = provider_cfg.extra or {}
        include_abstract = extra_cfg.get("include_abstract", False)
        similarity_threshold = extra_cfg.get("similarity_threshold", -0.4)

        manager = get_shared_manager(self.config.rtfm.project_root)
        self._ensure_library_selected(manager)
        spans: list[SourceSpan] = []

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
            for dep_id in target_card.depends_on or []:
                if dep_id in cards.sections:
                    dep_card = cards.sections[dep_id]
                    if dep_card.path:
                        target_files.append(Path(self.config.rtfm.project_root) / dep_card.path)

        for fpath in target_files:
            if fpath.exists():
                try:
                    content = fpath.read_text(encoding="utf-8")
                    for match in re.findall(r"\\cite(?:[a-zA-Z]*)\{([^}]+)\}", content):
                        for k in match.split(","):
                            k_clean = k.strip()
                            if k_clean and k_clean not in cite_keys:
                                cite_keys.append(k_clean)
                except Exception as e:
                    logger.warning(f"ZoteroProvider failed to extract citations from {fpath}: {e}")

        # 2. Resolve extracted citation keys directly via Zotero
        resolved_keys: set[str] = set()

        def _resolve_citation_key(key: str) -> SourceSpan | None:
            try:
                res = self._call_tool(
                    manager,
                    "zotero_search_by_citation_key",
                    {"citekey": key},
                )
                content_blocks = getattr(res, "content", [])
                block_text = ""
                for block in content_blocks:
                    if getattr(block, "type", None) == "text" and getattr(block, "text", None):
                        block_text += block.text

                # Smart fallback: if Better BibTeX is not running or key not in Extra,
                # extract author and year from citation key and search
                if "No item found with citation key" in block_text or not block_text.strip():
                    author_match = re.match(r"^([a-zA-Z]+)", key)
                    year_match = re.search(r"(\d{4})", key)
                    if author_match and year_match:
                        author = author_match.group(1)
                        year = year_match.group(1)
                        fallback_query = f"{author} {year}"
                        logger.info(
                            f"Citation key '{key}' not found. Trying fallback search: '{fallback_query}'"
                        )

                        fallback_res = self._call_tool(
                            manager,
                            "zotero_search_items",
                            {"query": fallback_query, "limit": 3},
                        )
                        fallback_blocks = getattr(fallback_res, "content", [])
                        block_text = ""
                        for fb in fallback_blocks:
                            if getattr(fb, "type", None) == "text" and getattr(fb, "text", None):
                                block_text += fb.text

                if block_text.strip() and "No item found" not in block_text:
                    # Try to extract the 8-character Zotero item key to retrieve annotations/notes
                    item_key_match = re.search(r"Item Key:\s*(\w{8})", block_text, re.IGNORECASE)
                    ann_text = ""
                    if item_key_match:
                        z_key = item_key_match.group(1)
                        try:
                            ann_res = self._call_tool(
                                manager,
                                "zotero_get_annotations",
                                {"item_key": z_key},
                            )
                            for ann_block in getattr(ann_res, "content", []):
                                if getattr(ann_block, "type", None) == "text" and getattr(
                                    ann_block, "text", None
                                ):
                                    ann_text += "\n" + ann_block.text
                        except Exception:
                            pass

                    snippet = block_text
                    if ann_text:
                        snippet += "\n\n### User Highlights & Annotations:\n" + ann_text

                    return SourceSpan(
                        path=f"zotero:{key}",
                        line_start=None,
                        line_end=None,
                        reason=f"Zotero reference for citation key '{key}'",
                        score=0.95,
                        priority="supporting",
                        source_role="reference",
                        metadata={"snippet": snippet, "citekey": key},
                    )
            except Exception as e:
                logger.error(f"Zotero citation key lookup failed for '{key}': {e}")
            return None

        if cite_keys:
            from concurrent.futures import ThreadPoolExecutor

            max_workers = min(len(cite_keys), 5)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                resolved_spans = list(executor.map(_resolve_citation_key, cite_keys))
                for span_item in resolved_spans:
                    if span_item is not None:
                        spans.append(span_item)
                        if span_item.metadata and "citekey" in span_item.metadata:
                            resolved_keys.add(span_item.metadata["citekey"])

        # 3. External Search (Semantic / Keyword)
        # Skip if proofreading to avoid context contamination
        if task_type == "proofread":
            logger.info(
                "Task type is proofread; skipping external Zotero searches to avoid context contamination."
            )
            return _deduplicate_spans(spans)

        def _parse_markdown_items(
            markdown_text: str,
            query: str,
            source_reason: str,
            score: float,
            allowed_item_keys: set[str] | None = None,
        ) -> list[SourceSpan]:
            parsed_spans: list[SourceSpan] = []
            # Split by markdown headers like "## 1. Title"
            parts = re.split(r"(?m)^## \d+\.\s+", markdown_text)
            if len(parts) <= 1:
                # No numbered items found, maybe just one big chunk or error
                if allowed_item_keys is not None or not markdown_text.strip():
                    return parsed_spans
                if not include_abstract:
                    # Naively strip abstract from whole text if possible
                    markdown_text = re.sub(
                        r"\*\*Abstract:\*\*\s*\n(?:.|\n)*?(?=\n\n|\Z)", "", markdown_text
                    )
                parsed_spans.append(
                    SourceSpan(
                        path=f"zotero:{query}",
                        line_start=None,
                        line_end=None,
                        reason=source_reason,
                        score=score,
                        priority="supporting",
                        source_role="reference",
                        metadata={"snippet": markdown_text},
                    )
                )
                return parsed_spans

            # parts[0] is the intro ("# Search Results... Found N items:")
            for item_text in parts[1:]:
                item_text = item_text.strip()
                if not item_text:
                    continue

                # Re-add a visual header for the snippet
                snippet = f"## {item_text}"

                # Filter out low-confidence semantic matches (e.g. negative similarity score)
                score_match = re.search(
                    r"\*\*(?:Similarity Score|Relevance):\*\*\s*([-\d.]+)", snippet
                )
                if score_match:
                    try:
                        sim_score = float(score_match.group(1))
                        if sim_score < similarity_threshold:
                            continue
                    except ValueError:
                        pass

                # Try to extract a specific citation key for the path
                cite_match = re.search(r"\*\*Citation Key:\*\*\s*([\w-]+)", snippet)
                item_key_match = re.search(r"\*\*Item Key:\*\*\s*([\w]+)", snippet)

                cite_key = cite_match.group(1) if cite_match else None
                item_key = item_key_match.group(1) if item_key_match else None
                if allowed_item_keys is not None and (
                    item_key is None or item_key.upper() not in allowed_item_keys
                ):
                    continue

                # Determine path
                span_path = (
                    f"zotero:{cite_key}"
                    if cite_key
                    else (f"zotero:{item_key}" if item_key else f"zotero:{query}")
                )

                # Exclude abstract if configured
                if not include_abstract:
                    snippet = re.sub(
                        r"\*\*Abstract:\*\*\s*\n(?:.|\n)*?(?=\n\n##|\Z)", "", snippet
                    ).strip()

                parsed_spans.append(
                    SourceSpan(
                        path=span_path,
                        line_start=None,
                        line_end=None,
                        reason=source_reason,
                        score=score,
                        priority="supporting",
                        source_role="reference",
                        metadata={
                            "snippet": snippet,
                            "citekey": cite_key,
                            "item_key": item_key,
                        },
                    )
                )
            return parsed_spans

        semantic_types = {"intent", "dep_intent", "thesis", "task", "title", "dep_title"}

        collections = self._ensure_collection_scope(manager)
        allowed_item_keys = self._allowed_item_keys if collections else None
        include_subcollections = bool(extra_cfg.get("include_subcollections", True))

        def _metadata_search(query: str, query_type: str) -> list[SourceSpan]:
            matched: list[SourceSpan] = []
            collection_targets: tuple[_ZoteroCollection | None, ...] = (
                tuple(collections) if collections else (None,)
            )
            for collection in collection_targets:
                arguments: dict[str, Any] = {"query": query, "limit": limit}
                if collection is not None:
                    arguments.update(
                        {
                            "collection_key": collection.key,
                            "include_subcollections": include_subcollections,
                        }
                    )
                result = self._call_tool(manager, "zotero_search_items", arguments)
                block_text = _result_text(result)
                if block_text:
                    matched.extend(
                        _parse_markdown_items(
                            markdown_text=block_text,
                            query=query,
                            source_reason=f"Zotero metadata search match for '{query}'",
                            score=0.8,
                            allowed_item_keys=allowed_item_keys,
                        )
                    )
            return _deduplicate_spans(matched)

        for q in queries:
            if q in cite_keys or q in resolved_keys:
                continue

            q_type = query_type_map.get(q, "task_keyword")
            is_semantic = q_type in semantic_types

            try:
                if is_semantic:
                    # Attempt Semantic Search for high-level intents
                    try:
                        semantic_limit = (
                            min(max(limit * 10, 50), 200) if collections else limit
                        )
                        res = self._call_tool(
                            manager,
                            "zotero_semantic_search",
                            {"query": q, "limit": semantic_limit},
                        )
                        block_text = _result_text(res)

                        if (
                            "Semantic search is not available" in block_text
                            or "Error" in block_text
                        ):
                            # Fallback if semantic fails
                            raise Exception(block_text)

                        semantic_spans = _parse_markdown_items(
                            block_text,
                            q,
                            f"Zotero semantic match for {q_type} '{q}'",
                            0.9,
                            allowed_item_keys,
                        )
                        if collections:
                            semantic_spans = semantic_spans[:limit]
                            if len(semantic_spans) < limit:
                                semantic_spans.extend(_metadata_search(q, q_type))
                            spans.extend(_deduplicate_spans(semantic_spans)[:limit])
                        else:
                            spans.extend(semantic_spans)
                        continue
                    except Exception as sem_e:
                        logger.info(
                            f"Semantic search failed for '{q}', falling back to keyword search: {sem_e}"
                        )

                # Keyword search (for keywords, or as fallback)
                spans.extend(_metadata_search(q, q_type))
            except Exception as e:
                logger.error(f"Zotero search failed for query '{q}': {e}")

        return _deduplicate_spans(spans)
