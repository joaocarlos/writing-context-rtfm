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

    snippet = entry.format_snippet()
    assert "## Attention Is All You Need" in snippet
    assert "**Citation Key:** `vaswani2017attention`" in snippet
    assert "**DOI:** 10.5555/3295222.3295349" in snippet


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
