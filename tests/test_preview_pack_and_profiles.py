from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from writing_context_rtfm.cli import (
    doctor_command,
    preview_pack_command,
)
from writing_context_rtfm.config import apply_profile, load_config
from writing_context_rtfm.doctor import run_doctor_fix
from writing_context_rtfm.schemas import ContextPack, SourceSpan


def test_apply_profile_presets():
    base = load_config("nonexistent.yaml")

    # Fast: disable neural/dense providers
    fast = apply_profile(base, "fast")
    assert fast.profile == "fast"
    assert (
        fast.providers.get("local_embeddings") is None
        or not fast.providers["local_embeddings"].enabled
    )
    assert (
        fast.providers.get("local_reranker") is None or not fast.providers["local_reranker"].enabled
    )
    assert (
        fast.providers.get("openai_semantic") is None
        or not fast.providers["openai_semantic"].enabled
    )

    # Balanced: enable local embeddings, disable reranker
    balanced = apply_profile(base, "balanced")
    assert balanced.profile == "balanced"
    assert balanced.providers["local_embeddings"].enabled is True
    assert (
        balanced.providers.get("local_reranker") is None
        or not balanced.providers["local_reranker"].enabled
    )

    # Thorough: enable both local embeddings and local reranker
    thorough = apply_profile(base, "thorough")
    assert thorough.profile == "thorough"
    assert thorough.providers["local_embeddings"].enabled is True
    assert thorough.providers["local_reranker"].enabled is True

    # Unknown profile raises ValueError
    with pytest.raises(ValueError, match="Unknown profile"):
        apply_profile(base, "hyperdrive")


def test_load_config_with_profile(tmp_path: Path):
    cfg_file = tmp_path / ".writing-context" / "config.yaml"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text("profile: thorough\n", encoding="utf-8")

    cfg = load_config(tmp_path)
    assert cfg.profile == "thorough"
    assert cfg.providers["local_embeddings"].enabled is True
    assert cfg.providers["local_reranker"].enabled is True


def test_doctor_fix_repairs_missing_environment(tmp_path: Path):
    # Empty directory
    actions = run_doctor_fix(tmp_path)
    assert len(actions) > 0
    assert any("config.yaml" in a for a in actions)
    assert any("section cards" in a for a in actions)
    assert any("cache database" in a for a in actions)

    # Verify context_cache.sqlite has schema
    db_path = tmp_path / ".writing-context" / "context_cache.sqlite"
    assert db_path.exists()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()

    assert "context_pack_runs" in tables
    assert "context_pack_sources" in tables

    # Running fix again should be idempotent and perform no unneeded actions
    actions2 = run_doctor_fix(tmp_path)
    assert actions2 == []


def test_cli_doctor_fix_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    args = argparse.Namespace(project_root=str(tmp_path), fix=True, json=False)
    doctor_command(args)

    out = capsys.readouterr().out
    assert "Doctor Auto-Repair Applied:" in out
    assert (tmp_path / ".writing-context" / "config.yaml").exists()


@patch("writing_context_rtfm.cli.ContextPackGenerator")
@patch("writing_context_rtfm.cli.load_section_cards")
@patch("writing_context_rtfm.cli.load_config")
@patch("writing_context_rtfm.cli.RTFMAdapter")
@patch("writing_context_rtfm.cli.ExtensionStore")
@patch("writing_context_rtfm.providers.get_active_providers")
def test_preview_pack_command_rendering(
    mock_get_providers,
    mock_store_class,
    mock_adapter_class,
    mock_load_config,
    mock_load_sc,
    mock_generator_class,
    capsys: pytest.CaptureFixture[str],
):
    mock_config = load_config("nonexistent.yaml")
    mock_load_config.return_value = mock_config
    mock_get_providers.return_value = []
    mock_store_class.return_value.__enter__.return_value = mock_store_class.return_value

    mock_pack = ContextPack(
        run_id="preview-run-123",
        task="Write an introduction",
        target="intro",
        status="complete",
        document_thesis="A study on context optimization.",
        prior_claims=[],
        constraints=["Max 500 words", "Academic tone"],
        terminology={"RTFM": "Read The Fantastic Manual"},
        source_spans=[
            SourceSpan(
                path="ch1.md",
                line_start=1,
                line_end=10,
                reason="Anchor context",
                score=0.95,
                priority="essential",
                source_role="reference",
                metadata={"tier": 1, "tokens": 120},
            ),
            SourceSpan(
                path="ch2.md",
                line_start=1,
                line_end=5,
                reason="Supporting info",
                score=0.75,
                priority="supporting",
                source_role="reference",
                metadata={"tier": 2, "tokens": 80},
            ),
        ],
        estimated_tokens=200,
        warnings=["Sample warning"],
        quality={"score": 0.92, "budget": 800},
    )
    mock_generator_class.return_value.generate.return_value = mock_pack

    args = argparse.Namespace(
        task="Write an introduction",
        target="intro",
        budget=800,
        profile="balanced",
        mode="write",
        task_type="writing",
        pack_mode="focused",
        role_budgets=None,
        project_root=".",
        raw=False,
        no_color=True,
    )

    preview_pack_command(args)

    out = capsys.readouterr().out
    assert "WRITING CONTEXT PACK PREVIEW" in out
    assert "Task:         Write an introduction" in out
    assert "Profile:      balanced" in out
    assert "Token Budget: 800  (Estimated Tokens: 200)" in out
    assert "Thesis:       A study on context optimization." in out
    assert "Max 500 words" in out
    assert "ch1.md" in out
    assert "ch2.md" in out
    assert "RENDERED LLM PROMPT PREVIEW:" in out


@patch("writing_context_rtfm.cli.ContextPackGenerator")
@patch("writing_context_rtfm.cli.load_section_cards")
@patch("writing_context_rtfm.cli.load_config")
@patch("writing_context_rtfm.cli.RTFMAdapter")
@patch("writing_context_rtfm.cli.ExtensionStore")
@patch("writing_context_rtfm.providers.get_active_providers")
def test_preview_pack_raw_output(
    mock_get_providers,
    mock_store_class,
    mock_adapter_class,
    mock_load_config,
    mock_load_sc,
    mock_generator_class,
    capsys: pytest.CaptureFixture[str],
):
    mock_config = load_config("nonexistent.yaml")
    mock_load_config.return_value = mock_config
    mock_get_providers.return_value = []
    mock_store_class.return_value.__enter__.return_value = mock_store_class.return_value

    mock_pack = ContextPack(
        run_id="preview-raw-456",
        task="Draft methodology",
        target="methods",
        status="complete",
        document_thesis=None,
        prior_claims=[],
        terminology={},
        constraints=[],
        estimated_tokens=50,
        source_spans=[],
    )
    mock_generator_class.return_value.generate.return_value = mock_pack

    args = argparse.Namespace(
        task="Draft methodology",
        target="methods",
        budget=None,
        profile=None,
        mode="write",
        task_type="writing",
        pack_mode="focused",
        role_budgets=None,
        project_root=".",
        raw=True,
        no_color=True,
    )

    preview_pack_command(args)

    out = capsys.readouterr().out
    assert "Task: Draft methodology" in out
    assert "Target Section: methods" in out
    assert "WRITING CONTEXT PACK PREVIEW" not in out
