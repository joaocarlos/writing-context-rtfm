from unittest.mock import MagicMock, patch

import pytest

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.providers.local import ZoteroProvider
from writing_context_rtfm.schemas import MCPServerConfig, ProviderConfig


@pytest.fixture
def base_config():
    return AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root="."),
        context=ContextConfig(),
        cache=CacheConfig(enabled=False),
        section_cards=SectionCardsConfig(path="dummy.yaml"),
        providers={
            "zotero": ProviderConfig(
                enabled=True, mcp_server=MCPServerConfig(command="zotero-mcp", args=["serve"])
            )
        },
    )


@pytest.fixture
def mock_manager():
    with patch("writing_context_rtfm.providers.local.get_shared_manager") as mock_get:
        manager = MagicMock()
        mock_get.return_value = manager
        yield manager


def _scoped_config(base_config, *, collections, library_name="Research Group"):
    base_config.providers["zotero"] = ProviderConfig(
        enabled=True,
        mcp_server=MCPServerConfig(command="zotero-mcp", args=["serve"]),
        extra={
            "library_name": library_name,
            "collections": collections,
            "include_subcollections": True,
        },
    )
    return base_config


def _scoped_call_result(tool_name, arguments):
    texts = {
        "zotero_list_libraries": (
            "## User Library\n"
            "- **My Library** — 100 items (libraryID=1)\n\n"
            "## Group Libraries\n"
            "- **Research Group** — 20 items (groupID=42)"
        ),
        "zotero_switch_library": "Successfully switched to library **ID** 42.",
        "zotero_get_collections": (
            "- **Projects** (Key: PROJ0001)\n"
            "  - **Urban** (Key: URBAN001)\n"
            "- **Methods** (Key: METHOD01)"
        ),
    }
    if tool_name == "zotero_get_collection_items":
        if arguments["collection_key"] == "URBAN001":
            text = (
                "- `KEEP0001` | Urban paper (2024) [PDF]\n"
                "- `DUPL0001` | Shared paper (2023) [PDF]"
            )
        else:
            text = (
                "- `KEEP0002` | Methods paper (2022) [PDF]\n"
                "- `DUPL0001` | Shared paper (2023) [PDF]"
            )
    else:
        text = texts[tool_name]
    result = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = text
    result.content = [block]
    return result


def test_semantic_routing(base_config, mock_manager):
    provider = ZoteroProvider(base_config)

    # Mock the semantic search tool response
    mock_result = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = (
        "## 1. Test Paper\n**Citation Key:** testKey2023\n**Matched Content:** Semantic match text."
    )
    mock_result.content = [block]
    mock_manager.call_tool.return_value = mock_result

    queries = ["Explain the architecture"]
    query_type_map = {"Explain the architecture": "intent"}

    spans = provider.fetch_context(queries, target=None, limit=5, query_type_map=query_type_map)

    # Verify manager was called with semantic_search tool
    mock_manager.call_tool.assert_called_with(
        command="zotero-mcp",
        args=["serve"],
        tool_name="zotero_semantic_search",
        arguments={"query": "Explain the architecture", "limit": 5},
        env=None,
    )

    assert len(spans) == 1
    assert spans[0].path == "zotero:testKey2023"
    assert "Semantic match text" in spans[0].metadata["snippet"]


def test_keyword_routing(base_config, mock_manager):
    provider = ZoteroProvider(base_config)

    # Mock the keyword search tool response
    mock_result = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = "## 1. Test Paper\n**Citation Key:** keywordKey2023\n**Matched Content:** Keyword match text."
    mock_result.content = [block]
    mock_manager.call_tool.return_value = mock_result

    queries = ["architecture"]
    query_type_map = {"architecture": "key_term"}

    spans = provider.fetch_context(queries, target=None, limit=5, query_type_map=query_type_map)

    # Verify manager was called with search_items tool
    mock_manager.call_tool.assert_called_with(
        command="zotero-mcp",
        args=["serve"],
        tool_name="zotero_search_items",
        arguments={"query": "architecture", "limit": 5},
        env=None,
    )

    assert len(spans) == 1
    assert spans[0].path == "zotero:keywordKey2023"


def test_proofread_context_protection(base_config, mock_manager):
    provider = ZoteroProvider(base_config)

    queries = ["Explain the architecture", "architecture"]
    query_type_map = {"Explain the architecture": "intent", "architecture": "key_term"}

    spans = provider.fetch_context(
        queries, target=None, limit=5, query_type_map=query_type_map, task_type="proofread"
    )

    # Verify no searches were executed because it's a proofread task
    mock_manager.call_tool.assert_not_called()
    assert len(spans) == 0


def test_abstract_stripping(base_config, mock_manager):
    provider = ZoteroProvider(base_config)

    mock_result = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = (
        "## 1. Test Paper\n"
        "**Citation Key:** testKey2023\n"
        "**Matched Content:** Semantic match text.\n"
        "**Abstract:**\nThis is a long abstract that we want to strip out to save tokens.\n\n"
        "## 2. Another Paper"
    )
    mock_result.content = [block]
    mock_manager.call_tool.return_value = mock_result

    spans = provider.fetch_context(
        ["intent"], target=None, limit=5, query_type_map={"intent": "intent"}
    )

    assert len(spans) == 2
    snippet = spans[0].metadata["snippet"]
    assert "Semantic match text" in snippet
    assert "This is a long abstract" not in snippet


def test_include_abstract_config(base_config, mock_manager):
    # Enable include_abstract
    base_config.providers["zotero"] = ProviderConfig(
        enabled=True,
        mcp_server=MCPServerConfig(command="zotero-mcp", args=["serve"]),
        extra={"include_abstract": True},
    )

    provider = ZoteroProvider(base_config)

    mock_result = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = (
        "## 1. Test Paper\n"
        "**Citation Key:** testKey2023\n"
        "**Matched Content:** Semantic match text.\n"
        "**Abstract:**\nThis is a long abstract that we want to KEEP.\n\n"
        "## 2. Another Paper"
    )
    mock_result.content = [block]
    mock_manager.call_tool.return_value = mock_result

    spans = provider.fetch_context(
        ["intent"], target=None, limit=5, query_type_map={"intent": "intent"}
    )

    assert len(spans) == 2
    snippet = spans[0].metadata["snippet"]
    assert "This is a long abstract that we want to KEEP" in snippet


def test_similarity_score_filtering(base_config, mock_manager):
    provider = ZoteroProvider(base_config)

    mock_result = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = (
        "## 1. Good Paper\n"
        "**Citation Key:** goodKey2023\n"
        "**Similarity Score:** 0.85\n"
        "**Matched Content:** Highly relevant.\n\n"
        "## 2. Bad Paper\n"
        "**Citation Key:** badKey2023\n"
        "**Similarity Score:** -0.45\n"
        "**Matched Content:** Irrelevant match."
    )
    mock_result.content = [block]
    mock_manager.call_tool.return_value = mock_result

    spans = provider.fetch_context(
        ["intent"], target=None, limit=5, query_type_map={"intent": "intent"}
    )

    assert len(spans) == 1
    assert spans[0].path == "zotero:goodKey2023"


def test_similarity_score_filtering_config(base_config, mock_manager):
    # Configure custom threshold to -0.5
    base_config.providers["zotero"] = ProviderConfig(
        enabled=True,
        mcp_server=MCPServerConfig(command="zotero-mcp", args=["serve"]),
        extra={"similarity_threshold": -0.5},
    )
    provider = ZoteroProvider(base_config)

    mock_result = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = (
        "## 1. Good Paper\n"
        "**Citation Key:** goodKey2023\n"
        "**Similarity Score:** -0.45\n"
        "**Matched Content:** Keep this because -0.45 > -0.5.\n\n"
        "## 2. Bad Paper\n"
        "**Citation Key:** badKey2023\n"
        "**Similarity Score:** -0.55\n"
        "**Matched Content:** Drop this because -0.55 < -0.5."
    )
    mock_result.content = [block]
    mock_manager.call_tool.return_value = mock_result

    spans = provider.fetch_context(
        ["intent"], target=None, limit=5, query_type_map={"intent": "intent"}
    )

    assert len(spans) == 1
    assert spans[0].path == "zotero:goodKey2023"


def test_parallel_citation_resolution(base_config, mock_manager, tmp_path):
    # Create manuscript file with multiple citation keys
    manuscript = tmp_path / "paper.tex"
    manuscript.write_text(
        r"\cite{refA, refB, refC} describes prior work. \cite{refD} is another reference.",
        encoding="utf-8",
    )

    from dataclasses import replace

    config = replace(base_config, rtfm=replace(base_config.rtfm, project_root=str(tmp_path)))
    provider = ZoteroProvider(config)

    def fake_call_tool(command, args, tool_name, arguments, env=None):
        mock_res = MagicMock()
        citekey = arguments.get("citekey", "unknown")
        block = MagicMock()
        block.type = "text"
        block.text = f"Item Key: ABCDEFGH\nTitle: Paper for {citekey}\nCitation Key: {citekey}"
        mock_res.content = [block]
        return mock_res

    mock_manager.call_tool.side_effect = fake_call_tool

    # Mock load_section_cards to return card with target path
    mock_cards = MagicMock()
    mock_card = MagicMock()
    mock_card.path = "paper.tex"
    mock_card.depends_on = []
    mock_cards.sections = {"intro": mock_card}

    with patch("writing_context_rtfm.section_cards.load_section_cards", return_value=mock_cards):
        spans = provider.fetch_context(
            queries=[], target="intro", limit=10, query_type_map={}, task_type="proofread"
        )

    # All 4 keys resolved concurrently
    assert len(spans) == 4
    resolved_paths = [s.path for s in spans]
    assert resolved_paths == ["zotero:refA", "zotero:refB", "zotero:refC", "zotero:refD"]


def test_named_library_collection_scope_filters_semantic_results(base_config, mock_manager):
    config = _scoped_config(
        base_config,
        collections=["Projects / Urban", "Methods"],
    )
    provider = ZoteroProvider(config)

    def fake_call_tool(command, args, tool_name, arguments, env=None, session_scope=None):
        assert session_scope == "zotero:research group"
        if tool_name in {
            "zotero_list_libraries",
            "zotero_switch_library",
            "zotero_get_collections",
            "zotero_get_collection_items",
        }:
            return _scoped_call_result(tool_name, arguments)
        assert tool_name == "zotero_semantic_search"
        assert arguments == {"query": "urban resilience", "limit": 50}
        result = MagicMock()
        block = MagicMock()
        block.type = "text"
        block.text = (
            "## 1. In-scope urban paper\n"
            "**Item Key:** KEEP0001\n"
            "**Citation Key:** keepUrban2024\n"
            "**Relevance:** 0.92\n"
            "**Matched Content:** Relevant urban evidence.\n\n"
            "## 2. Out-of-scope paper\n"
            "**Item Key:** DROP0001\n"
            "**Citation Key:** dropPaper2024\n"
            "**Relevance:** 0.99\n"
            "**Matched Content:** Must not escape collection scoping.\n\n"
            "## 3. In-scope methods paper\n"
            "**Item Key:** KEEP0002\n"
            "**Citation Key:** keepMethods2022\n"
            "**Relevance:** 0.81\n"
            "**Matched Content:** Relevant methods evidence."
        )
        result.content = [block]
        return result

    mock_manager.call_tool.side_effect = fake_call_tool

    spans = provider.fetch_context(
        ["urban resilience"],
        target=None,
        limit=2,
        query_type_map={"urban resilience": "intent"},
    )

    assert [span.path for span in spans] == [
        "zotero:keepUrban2024",
        "zotero:keepMethods2022",
    ]
    assert all("DROP0001" not in span.metadata["snippet"] for span in spans)
    mock_manager.call_tool.assert_any_call(
        command="zotero-mcp",
        args=["serve"],
        tool_name="zotero_switch_library",
        arguments={"library_id": "42", "library_type": "group"},
        env=None,
        session_scope="zotero:research group",
    )


def test_multiple_collections_are_unioned_and_deduplicated(base_config, mock_manager):
    config = _scoped_config(
        base_config,
        collections=["Projects / Urban", "Methods"],
    )
    provider = ZoteroProvider(config)

    def fake_call_tool(command, args, tool_name, arguments, env=None, session_scope=None):
        assert session_scope == "zotero:research group"
        if tool_name in {
            "zotero_list_libraries",
            "zotero_switch_library",
            "zotero_get_collections",
            "zotero_get_collection_items",
        }:
            return _scoped_call_result(tool_name, arguments)
        assert tool_name == "zotero_search_items"
        result = MagicMock()
        block = MagicMock()
        block.type = "text"
        if arguments["collection_key"] == "URBAN001":
            block.text = (
                "## 1. Urban paper\n**Item Key:** KEEP0001\n**Matched Content:** Urban.\n\n"
                "## 2. Shared paper\n**Item Key:** DUPL0001\n**Matched Content:** Shared."
            )
        else:
            block.text = (
                "## 1. Shared paper\n**Item Key:** DUPL0001\n**Matched Content:** Shared.\n\n"
                "## 2. Methods paper\n**Item Key:** KEEP0002\n**Matched Content:** Methods."
            )
        return_value = result
        return_value.content = [block]
        return return_value

    mock_manager.call_tool.side_effect = fake_call_tool

    spans = provider.fetch_context(
        ["resilience"],
        target=None,
        limit=5,
        query_type_map={"resilience": "key_term"},
    )

    assert [span.path for span in spans] == [
        "zotero:KEEP0001",
        "zotero:DUPL0001",
        "zotero:KEEP0002",
    ]
    search_calls = [
        call.kwargs["arguments"]
        for call in mock_manager.call_tool.call_args_list
        if call.kwargs["tool_name"] == "zotero_search_items"
    ]
    assert search_calls == [
        {
            "query": "resilience",
            "limit": 5,
            "collection_key": "URBAN001",
            "include_subcollections": True,
        },
        {
            "query": "resilience",
            "limit": 5,
            "collection_key": "METHOD01",
            "include_subcollections": True,
        },
    ]


def test_ambiguous_bare_collection_name_fails_closed(base_config, mock_manager):
    config = _scoped_config(base_config, collections=["Methods"], library_name="My Library")
    provider = ZoteroProvider(config)

    def fake_call_tool(command, args, tool_name, arguments, env=None, session_scope=None):
        result = MagicMock()
        block = MagicMock()
        block.type = "text"
        if tool_name == "zotero_list_libraries":
            block.text = "## User Library\n- **My Library** — 100 items (libraryID=1)"
        elif tool_name == "zotero_switch_library":
            block.text = "Successfully switched to library **ID** 1."
        else:
            assert tool_name == "zotero_get_collections"
            block.text = (
                "- **Methods** (Key: METHOD01)\n"
                "- **Archive** (Key: ARCHIVE1)\n"
                "  - **Methods** (Key: METHOD02)"
            )
        result.content = [block]
        return result

    mock_manager.call_tool.side_effect = fake_call_tool

    with pytest.raises(ValueError, match="ambiguous collection name 'Methods'"):
        provider.fetch_context(["methods"], target=None, limit=5)


def test_zotero_fingerprint_includes_library_and_collection_scope(base_config):
    first = _scoped_config(base_config, collections=["Methods"])
    first_fingerprint = ZoteroProvider(first).get_fingerprint(first)

    second = _scoped_config(base_config, collections=["Projects / Urban"])
    second_fingerprint = ZoteroProvider(second).get_fingerprint(second)

    assert first_fingerprint is not None
    assert second_fingerprint is not None
    assert first_fingerprint != second_fingerprint
