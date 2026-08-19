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
