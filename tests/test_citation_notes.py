"""Tests for citation reading notes extraction from BibTeX fields and companion markdown files."""

from __future__ import annotations

import tempfile
from pathlib import Path

from writing_context_rtfm.config import load_config
from writing_context_rtfm.providers.bibtex import BibEntry, BibTeXProvider


def test_bibentry_notes_property() -> None:
    entry_annote = BibEntry(
        citekey="vaswani2017attention",
        entry_type="article",
        fields={
            "title": "Attention Is All You Need",
            "annote": "Key insight: Self-attention replaces RNNs.",
        },
    )
    assert entry_annote.notes == "Key insight: Self-attention replaces RNNs."

    entry_notes = BibEntry(
        citekey="devlin2018bert",
        entry_type="article",
        fields={
            "title": "BERT",
            "notes": "Masked language modeling enables deep bidirectionality.",
        },
    )
    assert entry_notes.notes == "Masked language modeling enables deep bidirectionality."

    entry_annotation = BibEntry(
        citekey="brown2020gpt3",
        entry_type="article",
        fields={
            "title": "GPT-3",
            "annotation": "Few-shot in-context learning without fine-tuning.",
        },
    )
    assert entry_annotation.notes == "Few-shot in-context learning without fine-tuning."


def test_format_snippet_includes_reading_notes() -> None:
    entry = BibEntry(
        citekey="smith2023deep",
        entry_type="article",
        fields={
            "title": "Deep Learning Survey",
            "author": "Smith, John",
            "year": "2023",
            "annote": "Crucial reading for section 2 on optimization techniques.",
        },
    )
    snippet = entry.format_snippet()
    assert "### Author Reading Notes" in snippet
    assert "Crucial reading for section 2 on optimization techniques." in snippet


def test_format_snippet_prefers_companion_notes() -> None:
    entry = BibEntry(
        citekey="smith2023deep",
        entry_type="article",
        fields={
            "title": "Deep Learning Survey",
            "annote": "Old bib note.",
        },
    )
    companion_note = "Updated local reading note with detailed critique and equation derivations."
    snippet = entry.format_snippet(companion_notes=companion_note)
    assert "### Author Reading Notes" in snippet
    assert "Updated local reading note with detailed critique" in snippet
    assert "Old bib note" not in snippet


def test_bibtex_provider_discovers_companion_note_file() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        note_file = notes_dir / "vaswani2017.md"
        note_file.write_text(
            "# Reading Notes for Vaswani et al. (2017)\n\nAttention is linear in compute with respect to key-value heads.",
            encoding="utf-8",
        )

        cfg = load_config(tmp_path)
        provider = BibTeXProvider(cfg)

        found_note = provider._find_companion_note("vaswani2017")
        assert found_note is not None
        assert "Attention is linear in compute" in found_note

        # Verify not found for nonexistent key
        assert provider._find_companion_note("nonexistent_key") is None
