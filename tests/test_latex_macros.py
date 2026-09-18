"""Tests for LaTeX macro extraction and prompt boundary protection."""

from __future__ import annotations

import tempfile
from pathlib import Path

from writing_context_rtfm.latex import extract_latex_macros, find_preamble_macros
from writing_context_rtfm.schemas import ContextPack, SourceSpan
from writing_context_rtfm.server import _format_write_section_prompt


def test_extract_latex_macros_various_syntaxes() -> None:
    sample_latex = r"""
    \documentclass{article}
    \usepackage{amsmath}
    \newcommand{\R}{\mathbb{R}}
    \newcommand\loss[1]{\mathcal{L}(#1)}
    \renewcommand{\vec}[1]{\mathbf{#1}}
    \providecommand{\D}{\mathcal{D}}
    \DeclareMathOperator{\argmin}{arg\,min}
    \DeclareMathOperator*{\argmax}{arg\,max}
    \def\trace#1{\mathrm{Tr}(#1)}
    \def\const{3.1415}

    \begin{document}
    \newcommand{\ignored}{this should not be extracted}
    Some text here.
    \end{document}
    """
    macros = extract_latex_macros(sample_latex)

    assert r"\R" in macros
    assert macros[r"\R"] == r"\mathbb{R}"

    assert r"\loss" in macros
    assert macros[r"\loss"] == r"[1] \mathcal{L}(#1)"

    assert r"\vec" in macros
    assert macros[r"\vec"] == r"[1] \mathbf{#1}"

    assert r"\D" in macros
    assert macros[r"\D"] == r"\mathcal{D}"

    assert r"\argmin" in macros
    assert macros[r"\argmin"] == r"arg\,min"

    assert r"\argmax" in macros
    assert macros[r"\argmax"] == r"arg\,max"

    assert r"\trace" in macros
    assert "#1" in macros[r"\trace"]
    assert r"\mathrm{Tr}(#1)" in macros[r"\trace"]

    assert r"\const" in macros
    assert macros[r"\const"] == "3.1415"

    # Macros after \begin{document} must be ignored
    assert r"\ignored" not in macros


def test_find_preamble_macros_file_scanning_and_caching() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        macros_file = tmp_path / "macros.tex"
        macros_file.write_text(
            r"\newcommand{\embed}[1]{\mathbf{e}_{#1}}" "\n" r"\newcommand{\vocab}{\mathcal{V}}",
            encoding="utf-8",
        )

        main_file = tmp_path / "main.tex"
        main_file.write_text(
            r"\documentclass{article}"
            "\n"
            r"\DeclareMathOperator{\softmax}{softmax}"
            "\n"
            r"\begin{document}"
            "\n"
            "Hello"
            "\n"
            r"\end{document}",
            encoding="utf-8",
        )

        found = find_preamble_macros(tmp_path)
        assert r"\embed" in found
        assert r"\vocab" in found
        assert r"\softmax" in found

        # Verify caching returns same results
        found_again = find_preamble_macros(tmp_path)
        assert found_again == found


def test_format_write_section_prompt_renders_macros() -> None:
    pack = ContextPack(
        task="Write methodology",
        target="sec_method",
        document_thesis="Our model outperforms baselines.",
        prior_claims=[],
        terminology={},
        constraints=["Preserve LaTeX formatting"],
        source_spans=[
            SourceSpan(
                path="sec1.tex",
                line_start=1,
                line_end=10,
                reason="Context",
                score=0.9,
                priority="essential",
                metadata={"snippet": "Some text."},
            )
        ],
        estimated_tokens=50,
        quality={"custom_macros": {r"\loss": r"\mathcal{L}", r"\R": r"\mathbb{R}"}},
    )

    prompt = _format_write_section_prompt(pack)
    assert "[Author Defined LaTeX Macros (Do NOT redefine or alter)]:" in prompt
    assert r"- `\loss`: \mathcal{L}" in prompt
    assert r"- `\R`: \mathbb{R}" in prompt
