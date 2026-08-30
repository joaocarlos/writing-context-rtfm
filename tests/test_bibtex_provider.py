from unittest.mock import MagicMock, patch

from writing_context_rtfm.config import (
    AppConfig,
    CacheConfig,
    ContextConfig,
    RTFMConfig,
    SectionCardsConfig,
)
from writing_context_rtfm.providers.bibtex import BibTeXProvider, parse_bibtex_file
from writing_context_rtfm.schemas import ProviderConfig

SAMPLE_BIB = r"""
@article{vaswani2017attention,
  title = {Attention Is All You Need},
  author = {Vaswani, Ashish and Shazeer, Noam and Parmar, Niki and Uszkoreit, Jakob},
  journal = {Advances in Neural Information Processing Systems},
  year = {2017},
  doi = {10.5555/3295222.3295349},
  abstract = {The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.}
}

@inproceedings{devlin2019bert,
  title = {BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding},
  author = {Devlin, Jacob and Chang, Ming-Wei and Lee, Kenton and Toutanova, Kristina},
  booktitle = {NAACL-HLT},
  year = {2019},
  abstract = {We introduce a new language representation model called BERT.}
}
"""


def test_parse_bibtex_file(tmp_path):
    bib_file = tmp_path / "references.bib"
    bib_file.write_text(SAMPLE_BIB, encoding="utf-8")

    entries = parse_bibtex_file(bib_file)
    assert len(entries) == 2
    assert "vaswani2017attention" in entries
    assert "devlin2019bert" in entries

    entry = entries["vaswani2017attention"]
    assert entry.title == "Attention Is All You Need"
    assert "Vaswani" in entry.author
    assert entry.year == "2017"
    assert "sequence transduction" in entry.abstract
    assert entry.fields.get("doi") == "10.5555/3295222.3295349"
    assert entry.line_start == 2
    assert entry.line_end >= entry.line_start

    snippet = entry.format_snippet()
    assert "## Attention Is All You Need" in snippet
    assert "**Citation Key:** `vaswani2017attention`" in snippet
    assert "**DOI:** 10.5555/3295222.3295349" in snippet


def test_bibtex_provider_resolves_and_reconstructs_source_range(tmp_path):
    bib_file = tmp_path / "references.bib"
    bib_file.write_text(SAMPLE_BIB, encoding="utf-8")
    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root=str(tmp_path)),
        context=ContextConfig(),
        cache=CacheConfig(enabled=False),
        section_cards=SectionCardsConfig(path="dummy.yaml"),
        providers={"bibtex": ProviderConfig(enabled=True)},
    )
    provider = BibTeXProvider(config)

    entries = provider.entries_for_source_span("references.bib", 2, 9)
    span = provider.reconstruct_entry(entries[0], score=0.73)

    assert [entry.citekey for entry in entries] == ["vaswani2017attention"]
    assert span.path == "references.bib"
    assert span.line_start == entries[0].line_start
    assert span.line_end == entries[0].line_end
    assert span.score == 0.73
    assert span.metadata["citekey"] == "vaswani2017attention"
    assert span.metadata["doi"] == "10.5555/3295222.3295349"
    assert span.metadata["title"] == "Attention Is All You Need"
    assert "Attention Is All You Need" in span.metadata["snippet"]


def test_parse_bibtex_file_accepts_same_line_entry_closure(tmp_path):
    bib_file = tmp_path / "references.bib"
    bib_file.write_text(
        """@ARTICLE{sameLine,
  author={Doe, Jane},
  title={A nested {BibTeX} title},
  year={2026},
  doi={10.1000/example}}
""",
        encoding="utf-8",
    )

    entries = parse_bibtex_file(bib_file)

    assert set(entries) == {"sameLine"}
    assert entries["sameLine"].year == "2026"


def test_bibtex_provider_availability(tmp_path):
    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root=str(tmp_path)),
        context=ContextConfig(),
        cache=CacheConfig(enabled=False),
        section_cards=SectionCardsConfig(path="dummy.yaml"),
        providers={},
    )
    provider = BibTeXProvider(config)

    # No .bib files present
    assert not provider.is_available(config)

    # Create a .bib file
    (tmp_path / "refs.bib").write_text(SAMPLE_BIB, encoding="utf-8")
    assert provider.is_available(config)

    # Explicitly disabled in config
    config.providers["bibtex"] = ProviderConfig(enabled=False)
    assert not provider.is_available(config)


def test_bibtex_provider_fetch_context_explicit_citations(tmp_path):
    (tmp_path / "refs.bib").write_text(SAMPLE_BIB, encoding="utf-8")

    # Create manuscript citing vaswani2017attention
    tex_file = tmp_path / "intro.tex"
    tex_file.write_text(
        r"We use transformers \cite{vaswani2017attention} for NLP.", encoding="utf-8"
    )

    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root=str(tmp_path)),
        context=ContextConfig(),
        cache=CacheConfig(enabled=False),
        section_cards=SectionCardsConfig(path="dummy.yaml"),
        providers={"bibtex": ProviderConfig(enabled=True)},
    )
    provider = BibTeXProvider(config)

    mock_cards = MagicMock()
    mock_card = MagicMock()
    mock_card.path = "intro.tex"
    mock_card.depends_on = []
    mock_cards.sections = {"intro": mock_card}

    with patch("writing_context_rtfm.section_cards.load_section_cards", return_value=mock_cards):
        spans = provider.fetch_context(queries=[], target="intro", limit=5)

    assert len(spans) == 1
    assert spans[0].path == "bibtex:vaswani2017attention"
    assert spans[0].source_role == "reference"
    assert spans[0].metadata["citekey"] == "vaswani2017attention"
    assert "Attention Is All You Need" in spans[0].metadata["snippet"]
    assert spans[0].score == 0.9


def test_bibtex_provider_scopes_citations_to_selected_section(tmp_path):
    (tmp_path / "refs.bib").write_text(SAMPLE_BIB, encoding="utf-8")
    (tmp_path / "main.tex").write_text(
        r"""\section{Target}
Local evidence \cite{vaswani2017attention}.
\section{Other}
Unrelated evidence \cite{devlin2019bert}.
""",
        encoding="utf-8",
    )
    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root=str(tmp_path)),
        context=ContextConfig(),
        cache=CacheConfig(enabled=False),
        section_cards=SectionCardsConfig(path="dummy.yaml"),
        providers={"bibtex": ProviderConfig(enabled=True)},
    )
    provider = BibTeXProvider(config)
    mock_cards = MagicMock()
    mock_card = MagicMock()
    mock_card.path = "main.tex"
    mock_card.depends_on = []
    mock_cards.sections = {"section_target": mock_card}

    with patch("writing_context_rtfm.section_cards.load_section_cards", return_value=mock_cards):
        spans = provider.fetch_context(queries=[], target="section_target", limit=5)

    assert [span.metadata["citekey"] for span in spans] == ["vaswani2017attention"]


def test_bibtex_provider_fetch_context_keyword_query(tmp_path):
    (tmp_path / "refs.bib").write_text(SAMPLE_BIB, encoding="utf-8")

    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root=str(tmp_path)),
        context=ContextConfig(),
        cache=CacheConfig(enabled=False),
        section_cards=SectionCardsConfig(path="dummy.yaml"),
        providers={"bibtex": ProviderConfig(enabled=True)},
    )
    provider = BibTeXProvider(config)

    # Query for BERT
    spans = provider.fetch_context(
        queries=["bidirectional transformers BERT"], target=None, limit=5
    )

    assert len(spans) == 1
    assert spans[0].path == "bibtex:devlin2019bert"
    assert "BERT" in spans[0].metadata["snippet"]
    assert spans[0].score <= 0.8


def test_bibtex_provider_rejects_single_word_keyword_noise(tmp_path):
    (tmp_path / "refs.bib").write_text(SAMPLE_BIB, encoding="utf-8")

    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root=str(tmp_path)),
        context=ContextConfig(),
        cache=CacheConfig(enabled=False),
        section_cards=SectionCardsConfig(path="dummy.yaml"),
        providers={"bibtex": ProviderConfig(enabled=True)},
    )
    provider = BibTeXProvider(config)

    spans = provider.fetch_context(queries=["transformers"], target=None, limit=5)

    assert spans == []


def test_bibtex_provider_rejects_low_fraction_keyword_overlap(tmp_path):
    (tmp_path / "refs.bib").write_text(SAMPLE_BIB, encoding="utf-8")

    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root=str(tmp_path)),
        context=ContextConfig(),
        cache=CacheConfig(enabled=False),
        section_cards=SectionCardsConfig(path="dummy.yaml"),
        providers={"bibtex": ProviderConfig(enabled=True)},
    )
    provider = BibTeXProvider(config)

    spans = provider.fetch_context(
        queries=[
            "academic writing bidirectional transformers benchmark analysis evidence manuscript context"
        ],
        target=None,
        limit=5,
    )

    assert spans == []
