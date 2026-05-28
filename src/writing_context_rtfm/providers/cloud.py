import json
import logging
import asyncio
from typing import List, Optional, Dict, Any
from mcp import ClientSession
from mcp.client.sse import sse_client

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.providers.base import BaseContextProvider

logger = logging.getLogger("mcp-server")

def run_async(coro):
    """Run an async coroutine, handling active event loop context if any."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)

def parse_search_results(text: str, query: str, provider_id: str) -> List[SourceSpan]:
    """Parse text/json returned by the search tool into standardized SourceSpans."""
    spans = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Plain text search results fallback
        spans.append(SourceSpan(
            path=f"{provider_id}:{query[:20].strip().replace(' ', '_')}",
            line_start=None,
            line_end=None,
            reason=f"{provider_id.capitalize()} search match for '{query}'",
            score=0.7,
            priority="supporting",
            source_role="reference",
            metadata={"snippet": text},
            query=query
        ))
        return spans

    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ["results", "papers", "hits", "citations", "data"]:
            if isinstance(data.get(key), list):
                items = data[key]
                break
        if not items:
            items = [data]

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue

        title = item.get("title") or item.get("paper_title") or item.get("headline")
        abstract = item.get("abstract") or item.get("snippet") or item.get("text") or item.get("content")
        doi = item.get("doi")
        url = item.get("url") or item.get("link")

        if doi:
            path = f"doi:{doi}"
        elif url:
            path = url
        elif title:
            normalized_title = title[:50].strip().replace(" ", "_")
            path = f"{provider_id}:{normalized_title}"
        else:
            path = f"{provider_id}:result_{idx}"

        score = item.get("score") or item.get("relevance")
        try:
            score = float(score) if score is not None else (0.9 - (idx * 0.05))
        except (ValueError, TypeError):
            score = 0.9 - (idx * 0.05)
        score = max(0.01, min(1.0, score))

        reason = f"{provider_id.capitalize()} search match for '{query}'"
        snippet = abstract or title or json.dumps(item)

        metadata = dict(item)
        metadata.setdefault("query", query)
        metadata.setdefault("provider", provider_id)
        if title:
            metadata["title"] = title

        spans.append(SourceSpan(
            path=path,
            line_start=None,
            line_end=None,
            reason=reason,
            score=score,
            priority="supporting" if idx < 3 else "background",
            source_role="reference",
            metadata={**metadata, "snippet": snippet},
            query=query
        ))
    return spans

async def fetch_all_queries(url: str, headers: Optional[Dict[str, str]], queries: List[str], limit: int, provider_id: str) -> List[SourceSpan]:
    """Execute connection and query endpoints concurrently."""
    headers_dict = dict(headers) if headers else None
    
    async with sse_client(url, headers=headers_dict) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            
            tools = await session.list_tools()
            target_tool = None
            for t in tools.tools:
                if t.name == "search":
                    target_tool = t
                    break
            if not target_tool:
                for t in tools.tools:
                    if "search" in t.name.lower():
                        target_tool = t
                        break
            
            if not target_tool:
                raise ValueError(f"No search tool found on server {url}")

            properties = target_tool.inputSchema.get("properties", {}) if target_tool.inputSchema else {}

            tasks = []
            for query in queries:
                arguments = {}
                if "query" in properties:
                    arguments["query"] = query
                elif "q" in properties:
                    arguments["q"] = query
                elif "searchText" in properties:
                    arguments["searchText"] = query
                else:
                    first_prop = list(properties.keys())[0] if properties else "query"
                    arguments[first_prop] = query

                if "limit" in properties:
                    arguments["limit"] = limit
                elif "max_results" in properties:
                    arguments["max_results"] = limit
                elif "count" in properties:
                    arguments["count"] = limit

                tasks.append(session.call_tool(target_tool.name, arguments))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            spans = []
            for q, res in zip(queries, results):
                if isinstance(res, Exception):
                    logger.error(f"Query '{q}' failed for provider '{provider_id}': {res}")
                    continue
                for content_block in res.content:
                    if content_block.type == "text":
                        text = content_block.text
                        extracted = parse_search_results(text, q, provider_id)
                        spans.extend(extracted)
            return spans

class SciteProvider(BaseContextProvider):
    def __init__(self, config: AppConfig):
        self.config = config

    @property
    def provider_id(self) -> str:
        return "scite"

    def is_available(self, config: AppConfig) -> bool:
        provider_cfg = config.providers.get("scite")
        return provider_cfg is not None and provider_cfg.enabled and bool(provider_cfg.sse_url)

    def fetch_context(self, queries: List[str], target: Optional[str], limit: int) -> List[SourceSpan]:
        provider_cfg = self.config.providers.get("scite")
        if not provider_cfg or not provider_cfg.enabled or not provider_cfg.sse_url:
            return []
        
        return run_async(fetch_all_queries(
            provider_cfg.sse_url,
            provider_cfg.headers,
            queries,
            limit,
            "scite"
        ))

class ConsensusProvider(BaseContextProvider):
    def __init__(self, config: AppConfig):
        self.config = config

    @property
    def provider_id(self) -> str:
        return "consensus"

    def is_available(self, config: AppConfig) -> bool:
        provider_cfg = config.providers.get("consensus")
        return provider_cfg is not None and provider_cfg.enabled and bool(provider_cfg.sse_url)

    def fetch_context(self, queries: List[str], target: Optional[str], limit: int) -> List[SourceSpan]:
        provider_cfg = self.config.providers.get("consensus")
        if not provider_cfg or not provider_cfg.enabled or not provider_cfg.sse_url:
            return []
        
        return run_async(fetch_all_queries(
            provider_cfg.sse_url,
            provider_cfg.headers,
            queries,
            limit,
            "consensus"
        ))
