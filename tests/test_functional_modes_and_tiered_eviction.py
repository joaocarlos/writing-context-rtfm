from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards
from writing_context_rtfm.server import (
    handle_explain_context_pack,
    handle_get_writing_context_pack,
)
from writing_context_rtfm.storage import ExtensionStore


@pytest.fixture
def test_env(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    main_tex = workspace / "main.tex"
    main_tex.write_text(
        r"""\documentclass{article}
\begin{document}
\section{Introduction}
Introduction text with \cite{knuth1984} and @vaswani2017.
\section{Methods}
Methods body.
\end{document}
""",
        encoding="utf-8",
    )

    cards = SectionCards(
        version=1,
        document=DocumentCard(thesis="Test thesis on modular context packing"),
        sections={
            "section_intro": SectionCard(
                id="section_intro",
                title="Introduction",
                path="main.tex",
                role="Set up problem context",
                constraints=["Do not exceed 500 words."],
            ),
            "section_new": SectionCard(
                id="section_new",
                title="Future Work",
                path="future.tex",  # Does not exist yet
                role="Outline next steps",
            ),
        },
    )

    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(corpus="test", project_root=str(workspace)),
        section_cards=SectionCardsConfig(path=str(workspace / "section_cards.yaml")),
        context=ContextConfig(
            min_score=0.1,
            max_source_spans=10,
            role_budgets={
                "target_text": 0.4,
                "local_context": 0.2,
                "dependency": 0.2,
                "reference": 0.2,
            },
        ),
        cache=CacheConfig(enabled=False),
    )

    mock_adapter = MagicMock(spec=RTFMAdapter)
    mock_adapter.search.return_value = []
    mock_store = MagicMock(spec=ExtensionStore)

    return workspace, config, cards, mock_adapter, mock_store


def test_tier1_explicit_citation_protection(test_env):
    """Explicit citation keys mentioned in text/task bypass reference quota under strict_budget."""
    workspace, config, cards, mock_adapter, mock_store = test_env

    mock_provider = MagicMock()
    mock_provider.provider_id = "bibtex"
    mock_provider.is_available.return_value = True

    mock_provider.fetch_context.return_value = [
        SourceSpan(
            path="refs.bib",
            line_start=1,
            line_end=20,
            reason="Reference for 'knuth1984'",
            score=0.95,
            source_role="reference",
            metadata={
                "provider_id": "bibtex",
                "citekey": "knuth1984",
                "snippet": "@article{knuth1984,\n" + "title={Literate Programming},\n" * 20 + "}",
            },
        ),
        SourceSpan(
            path="refs.bib",
            line_start=21,
            line_end=40,
            reason="Reference for 'random2020'",
            score=0.90,
            source_role="reference",
            metadata={
                "provider_id": "bibtex",
                "citekey": "random2020",
                "snippet": "@article{random2020,\n" + "title={Random Work},\n" * 20 + "}",
            },
        ),
    ]

    generator = ContextPackGenerator(
        config=config,
        section_cards=cards,
        adapter=mock_adapter,
        store=mock_store,
        providers=[mock_provider],
    )

    pack = generator.generate(
        task=r"Discuss literate programming using \cite{knuth1984}",
        target="section_intro",
        token_budget=600,
        strict_budget=True,
        include_diagnostics=True,
    )

    selected_paths_or_keys = [
        (s.metadata or {}).get("citekey") for s in pack.source_spans if s.source_role == "reference"
    ]
    assert "knuth1984" in selected_paths_or_keys
    assert "random2020" not in selected_paths_or_keys


def test_budget_overflow_status_and_telemetry(test_env):
    """When an essential or explicit span cannot fit in token_budget, status is budget_overflow."""
    workspace, config, cards, mock_adapter, mock_store = test_env

    mock_provider = MagicMock()
    mock_provider.provider_id = "bibtex"
    mock_provider.is_available.return_value = True
    mock_provider.fetch_context.return_value = [
        SourceSpan(
            path="refs.bib",
            line_start=1,
            line_end=50,
            reason="Reference for 'knuth1984'",
            score=0.95,
            source_role="reference",
            metadata={
                "provider_id": "bibtex",
                "citekey": "knuth1984",
                "snippet": "@article{knuth1984,\n" + "title={Literate Programming},\n" * 150 + "}",
            },
        )
    ]

    generator = ContextPackGenerator(
        config=config,
        section_cards=cards,
        adapter=mock_adapter,
        store=mock_store,
        providers=[mock_provider],
    )

    # Budget of 200 tokens: baseline (~150) fits, but knuth1984 (~1500 tokens) exceeds budget
    pack = generator.generate(
        task=r"Discuss \cite{knuth1984}",
        target=None,
        token_budget=200,
        strict_budget=True,
    )

    assert pack.status == "budget_overflow"
    assert pack.quality.get("reason") == "budget_overflow"
    assert pack.quality.get("budget_overflow_details") is not None
    assert pack.quality["budget_overflow_details"]["missing_tokens"] > 0


def test_mode_compress_disables_search_and_sets_constraints(test_env):
    """Mode 'compress' disables retrieval queries and injects compression constraints."""
    workspace, config, cards, mock_adapter, mock_store = test_env

    generator = ContextPackGenerator(
        config=config,
        section_cards=cards,
        adapter=mock_adapter,
        store=mock_store,
    )

    pack = generator.generate(
        task="Condense the introduction section to remove redundancies",
        target="section_intro",
        token_budget=2000,
        mode="compress",
    )

    assert pack.mode == "compress"
    assert any("PODA NÃO-DESTRUTIVA" in c for c in pack.constraints)
    assert any("preenchimento" in c.lower() for c in pack.constraints)
    assert mock_adapter.search.call_count == 0


def test_mode_auto_inference(test_env):
    """Auto-inference detects write, compress, adapt, and rewrite based on target & task text."""
    workspace, config, cards, mock_adapter, mock_store = test_env

    generator = ContextPackGenerator(
        config=config,
        section_cards=cards,
        adapter=mock_adapter,
        store=mock_store,
    )

    # 1. Compress via keywords
    p_comp = generator.generate(
        task="Please shorten and condense this text",
        target="section_intro",
        token_budget=2000,
    )
    assert p_comp.mode == "compress"

    # 2. Adapt via keywords
    p_adapt = generator.generate(
        task="Adapt this chapter section into a conference paper section",
        target="section_intro",
        token_budget=2000,
    )
    assert p_adapt.mode == "adapt"
    assert any("ADAPTAÇÃO NARRATIVA" in c for c in p_adapt.constraints)

    # 3. Write via non-existent file or explicit new section
    p_write = generator.generate(
        task="Write section from scratch",
        target="section_new",
        token_budget=2000,
    )
    assert p_write.mode == "write"
    assert any("NOVA SEÇÃO" in c for c in p_write.constraints)

    # 4. Rewrite for existing file
    p_rewrite = generator.generate(
        task="Revise prose to be more active voice",
        target="section_intro",
        token_budget=2000,
    )
    assert p_rewrite.mode == "rewrite"


def test_server_handlers_forward_mode(monkeypatch, test_env):
    """MCP server handler forwards mode to ContextPackGenerator."""
    workspace, config, cards, mock_adapter, mock_store = test_env

    monkeypatch.setattr(
        "writing_context_rtfm.server._load_runtime",
        lambda: (config, cards, [], mock_adapter, mock_store),
    )

    args = {
        "task": "Test compress task",
        "target": "section_intro",
        "mode": "compress",
        "output_mode": "structured",
    }
    res = handle_get_writing_context_pack(args)
    assert not res.get("isError")
    pack_data = json.loads(res["content"][0]["text"])
    assert pack_data.get("mode") == "compress"
    assert any("PODA NÃO-DESTRUTIVA" in c for c in pack_data.get("constraints", []))

    res_exp = handle_explain_context_pack(args)
    assert not res_exp.get("isError")
    exp_data = json.loads(res_exp["content"][0]["text"])
    assert exp_data.get("mode") == "compress"
