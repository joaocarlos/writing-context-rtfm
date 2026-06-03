import json
from unittest.mock import MagicMock, patch

import pytest

from writing_context_rtfm.server import (
    handle_audit_manuscript_terminology,
    handle_get_manuscript_reference_graph,
    handle_get_proofreading_context_pack,
    handle_get_term_context,
    handle_get_writing_context_pack,
    handle_initialize_section_cards,
    handle_request_more_context,
    handle_submit_generation_feedback,
)


@patch("writing_context_rtfm.server.initialize_section_cards")
def test_handle_initialize_section_cards(mock_init):
    mock_init.return_value = {"status": "ok"}
    res = handle_initialize_section_cards({"project_root": "."})
    
    assert res["content"][0]["text"] == '{"status": "ok"}'


@patch("writing_context_rtfm.server.audit_manuscript_terminology")
def test_handle_audit_manuscript_terminology(mock_audit):
    mock_audit.return_value = {"findings": []}
    res = handle_audit_manuscript_terminology({"project_root": "."})
    
    assert res["content"][0]["text"] == '{"findings": []}'


@patch("writing_context_rtfm.server.get_term_context")
def test_handle_get_term_context(mock_get_term):
    mock_get_term.return_value = {"term": "test"}
    res = handle_get_term_context({"term": "test", "project_root": "."})
    
    assert res["content"][0]["text"] == '{"term": "test"}'
    
    # Test missing term
    res_err = handle_get_term_context({})
    assert "error_code" in res_err["content"][0]["text"]


@patch("writing_context_rtfm.server.build_reference_graph")
def test_handle_get_manuscript_reference_graph(mock_build):
    mock_build.return_value = {"nodes": []}
    res = handle_get_manuscript_reference_graph({"project_root": "."})
    
    assert res["content"][0]["text"] == '{"nodes": []}'


@patch("writing_context_rtfm.server.ExtensionStore")
@patch("writing_context_rtfm.server.load_config")
def test_handle_request_more_context(mock_load_config, mock_store_class):
    mock_store = mock_store_class.return_value
    mock_store.get_more_context.return_value = [{"id": 1}]
    
    res = handle_request_more_context({"run_id": "test_id"})
    
    data = json.loads(res["content"][0]["text"])
    assert data["count"] == 1


@patch("writing_context_rtfm.server.ExtensionStore")
@patch("writing_context_rtfm.server.load_config")
def test_handle_submit_generation_feedback(mock_load_config, mock_store_class):
    mock_store = mock_store_class.return_value
    
    res = handle_submit_generation_feedback({
        "run_id": "test_id",
        "metric_name": "helpfulness",
        "metric_value": 1.0,
        "metric_text": "Good"
    })
    
    mock_store.submit_feedback.assert_called_once_with("test_id", "helpfulness", 1.0, "Good")
    data = json.loads(res["content"][0]["text"])
    assert data["status"] == "feedback_saved"


@patch("writing_context_rtfm.providers.get_active_providers")
@patch("writing_context_rtfm.server._load_runtime")
@patch("writing_context_rtfm.server.ContextPackGenerator")
def test_handle_get_writing_context_pack(mock_generator_class, mock_load_runtime, mock_get_providers):
    mock_get_providers.return_value = []
    mock_load_runtime.return_value = (MagicMock(), MagicMock(), [], MagicMock(), MagicMock())
    
    from writing_context_rtfm.schemas import ContextPack
    mock_pack = ContextPack(
        task="write it",
        target="sec1",
        document_thesis="",
        prior_claims=[],
        terminology={},
        constraints=[],
        source_spans=[],
        estimated_tokens=0,
    )
    mock_generator_class.return_value.generate.return_value = mock_pack
    
    res = handle_get_writing_context_pack({"task": "write it"})
    data = json.loads(res["content"][0]["text"])
    assert "source_spans" in data
    
    # Missing task
    res_err = handle_get_writing_context_pack({})
    assert "error_code" in json.loads(res_err["content"][0]["text"])


@patch("writing_context_rtfm.server._load_runtime")
@patch("writing_context_rtfm.server.ProofreadPackGenerator")
def test_handle_get_proofreading_context_pack(mock_generator_class, mock_load_runtime):
    mock_load_runtime.return_value = (MagicMock(), MagicMock(), [], MagicMock(), MagicMock())
    
    from writing_context_rtfm.schemas import ContextPack
    mock_pack = ContextPack(
        task="proofread",
        target="file.tex",
        document_thesis="",
        prior_claims=[],
        terminology={},
        constraints=[],
        source_spans=[],
        estimated_tokens=0,
    )
    mock_generator_class.return_value.generate.return_value = mock_pack
    
    res = handle_get_proofreading_context_pack({
        "target_file": "file.tex",
        "line_start": 1,
        "line_end": 10
    })
    
    assert "content" in res
    
    # Missing args
    res_err = handle_get_proofreading_context_pack({"target_file": "file.tex"})
    assert "error_code" in json.loads(res_err["content"][0]["text"])


@patch("writing_context_rtfm.server.load_config")
@patch("writing_context_rtfm.server.RTFMAdapter")
@patch("writing_context_rtfm.server.compute_rtfm_fingerprint")
@patch("writing_context_rtfm.server.ExtensionStore")
@patch("writing_context_rtfm.server.resolve_rtfm_db_path")
def test_handle_refresh_index(mock_resolve, mock_store_class, mock_fingerprint, mock_adapter_class, mock_load_config):
    mock_config = MagicMock()
    mock_config.cache.invalidate_on_refresh = True
    mock_load_config.return_value = mock_config
    
    mock_fingerprint.return_value = "fake"
    
    from writing_context_rtfm.server import handle_refresh_index
    res = handle_refresh_index({})
    
    data = json.loads(res["content"][0]["text"])
    assert data["status"] == "ok"
    assert data["cache_invalidated"] is True


@patch("writing_context_rtfm.server.handle_get_writing_context_pack")
@patch("writing_context_rtfm.server.sys.stdout")
def test_process_message_valid_json(mock_stdout, mock_handle):
    from writing_context_rtfm.server import process_message
    
    mock_handle.return_value = {"content": [{"text": "done"}]}
    
    # Test valid message
    msg = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "get_writing_context_pack",
            "arguments": {"task": "test"}
        }
    })
    process_message(msg)
    
    assert mock_handle.called
    
    # Test initialize message
    msg_init = json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "initialize",
        "params": {
            "rootUri": "file:///path/to/root"
        }
    })
    process_message(msg_init)


@patch("writing_context_rtfm.server.sys.stdin")
@patch("writing_context_rtfm.server.sys.stdout")
@patch("writing_context_rtfm.providers.manager.LocalMCPClientManager")
def test_run_server(mock_manager_class, mock_stdout, mock_stdin):
    from writing_context_rtfm.server import run_server
    
    mock_stdin.__iter__.return_value = [
        json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list"
        }) + "\n",
        "\n",
    ]
    
    run_server()
    assert mock_stdout.write.called



