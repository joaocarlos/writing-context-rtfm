"""Regression tests for writing mode inference, task intent disambiguation, and LaTeX safety qualifications.

Ensures that:
1. Substrings such as 'cut' in Portuguese words ('executar', 'execução', 'executando') never falsely trigger 'compress'.
2. Substrings such as 'adapt' in technical terms ('adaptive', 'Adaptive Alarm Protection') never falsely trigger 'adapt'.
3. Explicit removal/deletion directives prioritize 'rewrite' over compression.
4. 'sem reescrever' / 'do not rewrite' directives prevent accidental pruning constraints.
5. LaTeX safety warnings are qualified to allow explicit user-instructed section deletions.
6. Target resolution in virtual_doc correctly matches by filename, stem, and labels.
7. Full end-to-end regression test replicating the author's production call.
"""

from __future__ import annotations

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
from writing_context_rtfm.proofread import ProofreadPackGenerator
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.section_cards import DocumentCard, SectionCard, SectionCards
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.virtual_doc import VirtualDocumentParser


@pytest.fixture
def thesis_env(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    chapter6 = workspace / "chapter6.tex"
    chapter6.write_text(
        r"""\chapter{TRIAGE/4: Multi-tier Message Scheduling for IoT-based Urban Emergency Systems}
\label{chap:triage4}
\section{Introduction}
Introduction text citing \cite{delay1,delay2} and discussing Section~\ref{sec:related-work}.

\section{Scheduling Requirements for Urban Emergency Monitoring}
\label{sec:related-work}
Old related work section that the author wants to delete completely.
Contains \label{sec:related-queue-scheduling} and citations \cite{costa2020}.

\section{TRIAGE/4 Methodology}
\label{sec:methodology}
Core methodology details.
""",
        encoding="utf-8",
    )

    cards = SectionCards(
        version=1,
        document=DocumentCard(thesis="Prioritization framework for urban emergency monitoring"),
        sections={
            "chapter6": SectionCard(
                id="chapter6",
                title="TRIAGE/4 Scheduling",
                path="chapter6.tex",
                role="Core scheduling contribution",
                constraints=["Write as a thesis chapter answering RQ3."],
            ),
        },
    )

    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(corpus="thesis", project_root=str(workspace)),
        section_cards=SectionCardsConfig(path=str(workspace / "section_cards.yaml")),
        context=ContextConfig(
            min_score=0.1,
            max_source_spans=10,
        ),
        cache=CacheConfig(enabled=False),
    )

    mock_adapter = MagicMock(spec=RTFMAdapter)
    mock_adapter.project_root = str(workspace)
    mock_adapter.search.return_value = []
    mock_store = MagicMock(spec=ExtensionStore)

    return workspace, config, cards, mock_adapter, mock_store


def test_portuguese_executar_does_not_infer_compress(thesis_env):
    """Verify Portuguese words with 'cut' (executar, execução, executando) do not trigger compress mode."""
    workspace, _, _, _, _ = thesis_env
    tasks = [
        "Executar decisao explicita do autor: remover integralmente a antiga secao 6.1",
        "Execução de refatoração no capítulo 6",
        "Executando o plano de edição",
        "Executa a reestruturação da introdução",
    ]
    for task in tasks:
        mode = ContextPackGenerator._infer_writing_mode(
            mode=None,
            task=task,
            target="chapter6.tex",
            target_path="chapter6.tex",
            pr=str(workspace),
            target_card=None,
        )
        assert mode == "rewrite", f"Task '{task}' falsely inferred mode '{mode}' instead of 'rewrite'"


def test_english_cut_substrings_do_not_infer_compress(thesis_env):
    """Verify English words containing 'cut' (shortcut, acute, cute) do not trigger compress mode."""
    workspace, _, _, _, _ = thesis_env
    tasks = [
        "Add a shortcut to the broker dispatcher",
        "An acute analysis of the queue latency",
        "Set up a cute benchmark testbed",
    ]
    for task in tasks:
        mode = ContextPackGenerator._infer_writing_mode(
            mode=None,
            task=task,
            target="chapter6.tex",
            target_path="chapter6.tex",
            pr=str(workspace),
            target_card=None,
        )
        assert mode == "rewrite", f"Task '{task}' falsely inferred mode '{mode}' instead of 'rewrite'"


def test_adaptive_does_not_infer_adapt(thesis_env):
    """Verify technical mentions of 'adaptive' do not trigger narrative 'adapt' mode."""
    workspace, _, _, _, _ = thesis_env
    tasks = [
        "Implement Adaptive Alarm Protection in the scheduler",
        "Evaluate adaptive queueing thresholds under surge",
        "Configure adapter pattern for the broker interface",
    ]
    for task in tasks:
        mode = ContextPackGenerator._infer_writing_mode(
            mode=None,
            task=task,
            target="chapter6.tex",
            target_path="chapter6.tex",
            pr=str(workspace),
            target_card=None,
        )
        assert mode == "rewrite", f"Task '{task}' falsely inferred mode '{mode}' instead of 'rewrite'"


def test_removal_intent_overrides_compress(thesis_env):
    """Verify explicit removal directives prioritize 'rewrite' even if words like 'reduzir' or 'shorten' appear."""
    workspace, _, _, _, _ = thesis_env
    tasks = [
        "Remover a seção 6.1 para reduzir a extensão do capítulo",
        "Delete obsolete table to shorten the chapter",
        "Excluir subseção 6.2 sem alterar o restante",
        "Eliminar parágrafo redundante mantendo o núcleo técnico",
    ]
    for task in tasks:
        mode = ContextPackGenerator._infer_writing_mode(
            mode=None,
            task=task,
            target="chapter6.tex",
            target_path="chapter6.tex",
            pr=str(workspace),
            target_card=None,
        )
        assert mode == "rewrite", f"Task '{task}' with removal intent inferred '{mode}' instead of 'rewrite'"


def test_no_rewrite_intent_overrides_compress(thesis_env):
    """Verify 'sem reescrever' or 'do not rewrite' prevents compression mode."""
    workspace, _, _, _, _ = thesis_env
    tasks = [
        "Ajustar remissão sem reescrever os trechos do autor",
        "Update references without rewriting original text",
        "Manter texto do autor e apenas podar a tabela",
    ]
    for task in tasks:
        mode = ContextPackGenerator._infer_writing_mode(
            mode=None,
            task=task,
            target="chapter6.tex",
            target_path="chapter6.tex",
            pr=str(workspace),
            target_card=None,
        )
        assert mode == "rewrite", f"Task '{task}' inferred '{mode}' instead of 'rewrite'"


def test_latex_safety_warning_qualification(thesis_env):
    """Verify LaTeX safety warning explicitly permits authorized removal/refactoring."""
    workspace, config, cards, mock_adapter, mock_store = thesis_env

    generator = ContextPackGenerator(
        config=config,
        section_cards=cards,
        adapter=mock_adapter,
        store=mock_store,
    )

    pack = generator.generate(
        task="Revisar seção introdutória",
        target="chapter6",
        token_budget=2000,
        mode="rewrite",
    )

    safety_warnings = [w for w in pack.warnings if w.startswith("LaTeX Safety:")]
    assert len(safety_warnings) == 1
    warning_text = safety_warnings[0]
    assert "unless explicitly instructed by the task to remove or refactor" in warning_text


def test_proofread_latex_safety_warning_qualification(thesis_env):
    """Verify proofread generator also qualifies the LaTeX safety warning."""
    workspace, config, cards, mock_adapter, mock_store = thesis_env

    proof_gen = ProofreadPackGenerator(
        config=config,
        section_cards=cards,
        adapter=mock_adapter,
        store=mock_store,
    )

    proof_pack = proof_gen.generate(
        target_file=str(workspace / "chapter6.tex"),
        line_start=1,
        line_end=10,
        mode="surface",
    )

    safety_warnings = [w for w in proof_pack.warnings if w.startswith("LaTeX Safety:")]
    assert len(safety_warnings) == 1
    assert "unless explicitly instructed by the task to remove or refactor" in safety_warnings[0]


def test_virtual_doc_find_section_node_matches(thesis_env):
    """Verify find_section_node correctly resolves filename, stem, and labels."""
    workspace, _, _, _, _ = thesis_env
    parser = VirtualDocumentParser(str(workspace))
    parser.parse("chapter6.tex")

    # Match by exact filename
    node_by_filename = parser.find_section_node("chapter6.tex")
    assert node_by_filename is not None
    assert node_by_filename.source_path == "chapter6.tex"

    # Match by stem
    node_by_stem = parser.find_section_node("chapter6")
    assert node_by_stem is not None

    # Match by label
    node_by_label = parser.find_section_node("chap:triage4")
    assert node_by_label is not None


def test_end_to_end_author_removal_task_pack(thesis_env):
    """End-to-end regression test replicating the exact production call from the user request."""
    workspace, config, cards, mock_adapter, mock_store = thesis_env

    generator = ContextPackGenerator(
        config=config,
        section_cards=cards,
        adapter=mock_adapter,
        store=mock_store,
    )

    production_task = (
        "Executar decisao explicita do autor: remover integralmente a antiga secao 6.1 "
        "Scheduling Requirements for Urban Emergency Monitoring e sua tabela comparativa, "
        "preservando copia para futura reavaliacao. Ajustar apenas a remissao da introducao "
        "a sec:related-work e a abertura de sec:methodology para passagem direta ao metodo, "
        "sem reescrever os trechos do autor ou nucleo tecnico. Registrar nova etapa CP3A no "
        "plano para reavaliar comparacao na discussao, com trabalhos sem sobreposicao ao "
        "capitulo2 e sem garantir tabela nova."
    )

    pack = generator.generate(
        task=production_task,
        target="chapter6.tex",
        token_budget=2000,
        pack_mode="minimal",
        task_type="revise_existing_section",
    )

    # 1. Mode must be inferred as 'rewrite', NEVER 'compress'
    assert pack.mode == "rewrite"

    # 2. No 15% text pruning constraints injected
    assert not any("PODA NÃO-DESTRUTIVA" in c for c in pack.constraints)
    assert not any("15%" in c for c in pack.constraints)

    # 3. Status must remain complete
    assert pack.status == "complete"

    # 4. LaTeX safety warning must permit explicit removal
    safety_warnings = [w for w in pack.warnings if w.startswith("LaTeX Safety:")]
    if safety_warnings:
        assert "unless explicitly instructed by the task to remove or refactor" in safety_warnings[0]


def test_local_intent_classifier_direct():
    """Verify LocalIntentClassifier correctly maps semantic intent via local embeddings."""
    from writing_context_rtfm.intent_classifier import (
        LocalIntentClassifier,
        classify_task_intent,
    )

    classifier = LocalIntentClassifier()

    # 1. Compress intent in English
    res_comp_en = classifier.classify("Please shorten and condense this text to fit under page limit")
    assert res_comp_en is not None
    assert res_comp_en.intent == "compress"
    assert res_comp_en.is_confident

    # 2. Compress intent in Portuguese
    res_comp_pt = classifier.classify("Enxugar o texto eliminando pleonasmos e orações de preenchimento")
    assert res_comp_pt is not None
    assert res_comp_pt.intent == "compress"
    assert res_comp_pt.is_confident

    # 3. Adapt intent in English
    res_adapt_en = classifier.classify("Adapt this chapter section into a conference paper section")
    assert res_adapt_en is not None
    assert res_adapt_en.intent == "adapt"
    assert res_adapt_en.is_confident

    # 4. Delete intent in Portuguese
    res_del_pt = classifier.classify(
        "Executar decisao explicita do autor: remover integralmente a antiga secao 6.1 e sua tabela"
    )
    assert res_del_pt is not None
    assert res_del_pt.intent == "delete"

    # 5. Global helper classify_task_intent uses cached classifier
    helper_res = classify_task_intent("Please shorten and condense this text to fit under page limit")
    assert helper_res is not None
    assert helper_res.intent == "compress"


def test_local_intent_classifier_graceful_fallback_when_model_fails():
    """Verify that if the local model fails to load, classifier gracefully returns None."""
    from writing_context_rtfm.intent_classifier import LocalIntentClassifier

    broken_classifier = LocalIntentClassifier(model_id="non-existent/fake-model")
    res = broken_classifier.classify("Some task")
    assert res is None
