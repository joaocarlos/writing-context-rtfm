import pytest
import os
import json
from dataclasses import asdict
from unittest.mock import MagicMock

from writing_context_rtfm.config import AppConfig, RTFMConfig, CacheConfig, ContextConfig, SectionCardsConfig
from writing_context_rtfm.section_cards import SectionCards, DocumentCard, SectionCard
from writing_context_rtfm.proofread import ProofreadPackGenerator
from writing_context_rtfm.schemas import RTFMResult

@pytest.fixture
def mock_config(tmp_path):
    return AppConfig(
        version=1,
        rtfm=RTFMConfig(corpus="test_corpus", project_root=str(tmp_path)),
        context=ContextConfig(),
        cache=CacheConfig(path=str(tmp_path / "cache.sqlite")),
        section_cards=SectionCardsConfig(path=str(tmp_path / "sections.yaml"))
    )

@pytest.fixture
def mock_section_cards():
    return SectionCards(
        version=1,
        document=DocumentCard(title="Test Doc", thesis="Test Thesis"),
        sections={
            "intro": SectionCard(
                id="intro",
                title="Introduction",
                path="intro.tex",
                must_preserve=["Preserve this core claim."],
                constraints=["Use active voice."]
            )
        }
    )

@pytest.fixture
def mock_adapter():
    adapter = MagicMock()
    # Mock search results for prior usage
    adapter.search.return_value = [
        RTFMResult(
            path="other.tex",
            line_start=10,
            line_end=12,
            snippet="Prior usage of the term.",
            score=0.9,
            metadata={}
        )
    ]
    return adapter

@pytest.fixture
def mock_store():
    return MagicMock()

@pytest.fixture
def test_file(tmp_path):
    p = tmp_path / "intro.tex"
    content = (
        "This is the first paragraph.\n"
        "\n"
        "This is the target paragraph line 1.\n"
        "This is the target paragraph line 2 with LaTeX \\cite{ref}.\n"
        "\n"
        "This is the last paragraph.\n"
    )
    p.write_text(content)
    return str(p)

def test_proofread_pack_generation(mock_config, mock_section_cards, mock_adapter, mock_store, test_file):
    generator = ProofreadPackGenerator(mock_config, mock_section_cards, mock_adapter, mock_store)
    
    pack = generator.generate(
        target_file=test_file,
        line_start=3,
        line_end=4,
        mode="latex_safe",
        strictness="assertive"
    )
    
    assert pack.target.file_path == test_file
    assert pack.target.line_start == 3
    assert pack.target.line_end == 4
    assert pack.target.section_id == "intro"
    
    # Context extraction
    assert "target paragraph line 1" in pack.local_context.target_span
    assert "target paragraph line 2" in pack.local_context.target_span
    assert pack.local_context.previous_paragraph == "This is the first paragraph."
    assert pack.local_context.next_paragraph == "This is the last paragraph."
    
    # Constraints
    assert pack.constraints.mode == "latex_safe"
    assert "Preserve LaTeX commands" in pack.constraints.general_rules[0]
    assert "Preserve key claim: Preserve this core claim." in pack.constraints.section_specific_rules
    assert "Use active voice." in pack.constraints.section_specific_rules
    
    # Terminology
    assert len(pack.constraints.terminology) > 0
    assert pack.constraints.terminology[0].term in pack.local_context.target_span.lower()
    assert "Prior usage" in pack.constraints.terminology[0].usage_examples[0]

def test_token_budget_degraded(mock_config, mock_section_cards, mock_adapter, mock_store, test_file):
    generator = ProofreadPackGenerator(mock_config, mock_section_cards, mock_adapter, mock_store)
    
    # Set a very low max_tokens
    pack = generator.generate(
        target_file=test_file,
        line_start=3,
        line_end=4,
        max_tokens=10
    )
    
    assert pack.status == "degraded"
    assert pack.estimated_tokens > 10

def test_exclusion_rules(mock_config, mock_section_cards, mock_adapter, mock_store, test_file):
    generator = ProofreadPackGenerator(mock_config, mock_section_cards, mock_adapter, mock_store)
    
    # Mock search result with excluded path
    mock_adapter.search.return_value = [
        RTFMResult(
            path=".rtfm/index.db",
            line_start=1,
            line_end=1,
            snippet="Should be excluded",
            score=1.0,
            metadata={}
        ),
        RTFMResult(
            path="valid.tex",
            line_start=1,
            line_end=1,
            snippet="Should be included",
            score=0.9,
            metadata={}
        )
    ]
    
    pack = generator.generate(test_file, 3, 4)
    
    # Check that terminology usage only comes from valid.tex
    for term_const in pack.constraints.terminology:
        for usage in term_const.usage_examples:
            assert usage != "Should be excluded"

def test_proofread_clamping_bounds(mock_config, mock_section_cards, mock_adapter, mock_store, test_file):
    generator = ProofreadPackGenerator(mock_config, mock_section_cards, mock_adapter, mock_store)
    
    # 0 start line should clamp to 1; 999 end line should clamp to file line count (6)
    pack = generator.generate(
        target_file=test_file,
        line_start=0,
        line_end=999,
    )
    
    assert pack.target.line_start == 1
    assert pack.target.line_end == 6
    assert "This is the first paragraph." in pack.local_context.target_span
    assert "This is the last paragraph." in pack.local_context.target_span

def test_proofread_adapter_search_exception(mock_config, mock_section_cards, mock_adapter, mock_store, test_file):
    generator = ProofreadPackGenerator(mock_config, mock_section_cards, mock_adapter, mock_store)
    
    # Force search to raise an exception
    mock_adapter.search.side_effect = Exception("RTFM search failed")
    
    pack = generator.generate(test_file, 3, 4)
    
    # Verify the pack is generated successfully despite search failure
    assert pack.target.file_path == test_file
    assert len(pack.constraints.terminology) == 0
    assert any("RTFM search failed" in w for w in pack.warnings)
