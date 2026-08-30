from unittest.mock import MagicMock, patch

from writing_context_rtfm.cli import (
    auth_command,
    cache_command,
    get_term_command,
    init_cards_command,
    init_db_command,
    inspect_target_command,
)


class MockArgs:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


@patch("writing_context_rtfm.features.initialize_section_cards")
def test_init_cards_command(mock_init_cards, capsys):
    mock_init_cards.return_value = {"status": "ok"}
    args = MockArgs(project_root=".")

    init_cards_command(args)

    captured = capsys.readouterr()
    assert '"status": "ok"' in captured.out


@patch("writing_context_rtfm.cli.load_config")
@patch("writing_context_rtfm.cli.ExtensionStore")
def test_init_db_command(mock_store_class, mock_load_config, capsys):
    mock_store_class.return_value.__enter__.return_value = mock_store_class.return_value
    mock_config = MagicMock()
    mock_config.cache.path = "test.sqlite"
    mock_load_config.return_value = mock_config

    args = MockArgs(project_root=".")
    init_db_command(args)

    mock_store_class.return_value.init_db.assert_called_once()
    captured = capsys.readouterr()
    assert "Initialized database at test.sqlite" in captured.out


@patch("writing_context_rtfm.cli.load_config")
@patch("writing_context_rtfm.cli.ExtensionStore")
def test_cache_command_clear(mock_store_class, mock_load_config, capsys):
    mock_store_class.return_value.__enter__.return_value = mock_store_class.return_value
    mock_config = MagicMock()
    mock_load_config.return_value = mock_config

    args = MockArgs(project_root=".", cache_action="clear")
    cache_command(args)

    mock_store_class.return_value.clear.assert_called_once()
    captured = capsys.readouterr()
    assert "Cache cleared successfully." in captured.out


@patch("writing_context_rtfm.cli.get_term_context")
def test_get_term_command(mock_get_term, capsys):
    mock_get_term.return_value = {"term": "test"}

    args = MockArgs(project_root=".", term="test")
    get_term_command(args)

    captured = capsys.readouterr()
    assert '"term": "test"' in captured.out


@patch("writing_context_rtfm.cli.load_config")
@patch("writing_context_rtfm.cli.ExtensionStore")
def test_auth_command(mock_store_class, mock_load_config, capsys):
    mock_store_class.return_value.__enter__.return_value = mock_store_class.return_value
    mock_config = MagicMock()
    mock_load_config.return_value = mock_config

    args = MockArgs(project_root=".", provider="openai", token="secret")
    auth_command(args)

    mock_store_class.return_value.set_provider_token.assert_called_once_with("openai", "secret")


@patch("writing_context_rtfm.cli.load_config")
@patch("writing_context_rtfm.cli.load_section_cards")
def test_inspect_target_command(mock_load_sc, mock_load_config, capsys, tmp_path):
    # Fake config
    mock_config = MagicMock()
    mock_config.section_cards.path = str(tmp_path / "section_cards.yaml")
    mock_load_config.return_value = mock_config

    # Touch the file so it exists
    (tmp_path / "section_cards.yaml").touch()

    mock_cards = MagicMock()
    mock_card = MagicMock()
    mock_card.title = "Test Section"
    mock_cards.sections = {"sec1": mock_card}
    mock_load_sc.return_value = mock_cards

    args = MockArgs(project_root=".", target="sec1")
    inspect_target_command(args)

    captured = capsys.readouterr()
    assert "Target Section ID: sec1" in captured.out
    assert "Title:             Test Section" in captured.out


@patch("writing_context_rtfm.providers.get_active_providers")
@patch("writing_context_rtfm.cli.RTFMAdapter")
@patch("writing_context_rtfm.cli.ExtensionStore")
@patch("writing_context_rtfm.cli.load_config")
@patch("writing_context_rtfm.cli.compute_rtfm_fingerprint")
@patch("writing_context_rtfm.cli.resolve_rtfm_db_path")
def test_sync_command(
    mock_resolve,
    mock_fingerprint,
    mock_load_config,
    mock_store_class,
    mock_adapter_class,
    mock_get_providers,
    capsys,
):
    mock_store_class.return_value.__enter__.return_value = mock_store_class.return_value
    mock_config = MagicMock()
    mock_load_config.return_value = mock_config
    mock_get_providers.return_value = []

    mock_adapter = mock_adapter_class.return_value
    mock_fingerprint.return_value = "fake_fingerprint"

    args = MockArgs(project_root=".", path=".", corpus=None)

    from writing_context_rtfm.cli import sync_command

    sync_command(args)

    mock_adapter.sync.assert_called_once()
    mock_store_class.return_value.invalidate_for_fingerprint.assert_called_once_with(
        "fake_fingerprint"
    )

    captured = capsys.readouterr()
    assert "Sync completed successfully." in captured.out


@patch("writing_context_rtfm.cli.ContextPackGenerator")
@patch("writing_context_rtfm.cli.load_section_cards")
@patch("writing_context_rtfm.cli.load_config")
@patch("writing_context_rtfm.cli.RTFMAdapter")
@patch("writing_context_rtfm.cli.ExtensionStore")
@patch("writing_context_rtfm.providers.get_active_providers")
def test_pack_command(
    mock_get_providers,
    mock_store_class,
    mock_adapter_class,
    mock_load_config,
    mock_load_sc,
    mock_generator_class,
    capsys,
):
    mock_config = MagicMock()
    mock_load_config.return_value = mock_config
    mock_get_providers.return_value = []

    from writing_context_rtfm.schemas import ContextPack

    mock_pack = ContextPack(
        task="write",
        target="sec1",
        document_thesis="",
        prior_claims=[],
        terminology={},
        constraints=[],
        source_spans=[],
        estimated_tokens=0,
    )
    mock_generator_class.return_value.generate.return_value = mock_pack

    args = MockArgs(
        project_root=".",
        target="sec1",
        task="write",
        budget=1000,
        must_consider=[],
        task_type="rewrite",
        line_start=None,
        line_end=None,
        pack_mode=None,
        role_budgets=None,
    )

    from writing_context_rtfm.cli import pack_command

    pack_command(args)

    mock_generator_class.return_value.generate.assert_called_once()


@patch("writing_context_rtfm.cli.run_server")
def test_serve_command(mock_run_server):
    from writing_context_rtfm.cli import serve_command

    args = MockArgs()
    serve_command(args)
    mock_run_server.assert_called_once()


@patch("shutil.which")
@patch("writing_context_rtfm.cli.load_config")
def test_doctor_command(mock_load_config, mock_which, capsys, tmp_path):
    mock_which.return_value = "/fake/rtfm"
    args = MockArgs(project_root=str(tmp_path))

    from writing_context_rtfm.cli import doctor_command

    doctor_command(args)

    captured = capsys.readouterr()
    assert "Writing Context RTFM Extension Doctor" in captured.out


@patch("writing_context_rtfm.latex.build_reference_graph")
def test_show_graph_command(mock_build, capsys):
    from writing_context_rtfm.cli import show_graph_command

    args = MockArgs(project_root=".", out="term")
    mock_build.return_value = {"nodes": [], "edges": []}
    show_graph_command(args)
    captured = capsys.readouterr()
    assert "LaTeX Reference Graph" in captured.out


@patch("writing_context_rtfm.cli._update_gitignore")
@patch("writing_context_rtfm.cli._update_mcp_json")
@patch("writing_context_rtfm.cli._update_markdown_rules")
@patch("writing_context_rtfm.cli._update_claude_settings")
def test_init_command(mock_claude, mock_md, mock_mcp, mock_git, capsys, tmp_path):
    from writing_context_rtfm.cli import init_command

    args = MockArgs(project_root=str(tmp_path))

    init_command(args)
    assert mock_git.called


@patch("os.kill")
def test_cleanup_command(mock_kill, capsys, tmp_path):
    from writing_context_rtfm.cli import cleanup_command

    pids_file = tmp_path / ".writing-context" / "active_pids.json"
    pids_file.parent.mkdir(parents=True, exist_ok=True)
    pids_file.write_text("[12345]")

    args = MockArgs(project_root=str(tmp_path))

    cleanup_command(args)

    captured = capsys.readouterr()
    assert "Cleaning up" in captured.out
    assert mock_kill.call_count >= 1


@patch("writing_context_rtfm.cli.ContextPackGenerator")
@patch("writing_context_rtfm.cli.ExtensionStore")
@patch("writing_context_rtfm.cli.RTFMAdapter")
@patch("writing_context_rtfm.cli.load_section_cards")
@patch("writing_context_rtfm.cli.load_config")
def test_explain_pack_command(
    mock_load_config,
    mock_load_cards,
    mock_adapter,
    mock_store,
    mock_generator_class,
    capsys,
):
    from writing_context_rtfm.cli import explain_pack_command
    from writing_context_rtfm.schemas import CandidateFunnel, ContextPack, ContextPackDiagnostics

    mock_pack = ContextPack(
        task="Test task",
        target="intro",
        document_thesis=None,
        prior_claims=[],
        terminology={},
        constraints=[],
        source_spans=[],
        estimated_tokens=150,
        status="complete",
        diagnostics=ContextPackDiagnostics(
            funnel=CandidateFunnel(retrieved=5, selected=2),
            candidates=[],
            ownership_audit=[],
            rejections_by_reason={"REJECT_TOKEN_BUDGET": 3},
        ),
    )
    mock_generator_class.return_value.generate.return_value = mock_pack

    args = MockArgs(
        command="explain-pack",
        task="Explain something",
        target="intro",
        budget=4000,
        must_consider=None,
        project_root=".",
        task_type=None,
        line_start=None,
        line_end=None,
        pack_mode=None,
        role_budgets=None,
        explain=False,
        json=False,
    )

    explain_pack_command(args)

    call_kwargs = mock_generator_class.return_value.generate.call_args[1]
    assert call_kwargs["include_diagnostics"] is True

    captured = capsys.readouterr()
    assert "=== Candidate Funnel ===" in captured.out
    assert "Retrieved:     5" in captured.out
    assert "REJECT_TOKEN_BUDGET: 3" in captured.out
