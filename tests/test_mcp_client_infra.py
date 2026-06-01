import pytest
import shutil
import json
import sys
import subprocess
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

from writing_context_rtfm.providers.discovery import autodiscover_local_mcps
from writing_context_rtfm.providers.manager import LocalMCPClientManager, register_pid, unregister_pid
from writing_context_rtfm.cli import cleanup_command

@pytest.fixture
def anyio_backend():
    return "asyncio"

def test_autodiscover_path_only():
    with patch("shutil.which") as mock_which:
        def side_effect(cmd):
            if cmd == "zotero-mcp":
                return "/usr/local/bin/zotero-mcp"
            return None
        mock_which.side_effect = side_effect
        
        discovered = autodiscover_local_mcps("/nonexistent/project")
        
        assert "zotero" in discovered
        assert discovered["zotero"]["enabled"] is True
        assert discovered["zotero"]["mcp_server"]["command"] == "zotero-mcp"

@pytest.mark.anyio
async def test_manager_pid_tracking(tmp_path):
    manager = LocalMCPClientManager(workspace_root=str(tmp_path))
    
    # Under the hood, _create_platform_compatible_process was patched
    import mcp.client.stdio
    
    proc = await mcp.client.stdio._create_platform_compatible_process(
        sys.executable,
        ["-c", "import time; time.sleep(5)"]
    )
    
    try:
        pid = proc.pid
        assert pid is not None
        
        # Verify it is in manager.registered_pids
        assert pid in manager.registered_pids
        
        # Verify it is in active_pids.json
        pid_file = tmp_path / ".writing-context" / "active_pids.json"
        assert pid_file.exists()
        pids = json.loads(pid_file.read_text(encoding="utf-8"))
        assert pid in pids
    finally:
        # Clean up process
        try:
            proc.terminate()
            await proc.wait()
        except Exception:
            pass
            
        manager.shutdown()
        
    # Verify active_pids.json empty after shutdown
    pid_file = tmp_path / ".writing-context" / "active_pids.json"
    if pid_file.exists():
        pids_after = json.loads(pid_file.read_text(encoding="utf-8"))
        assert pid not in pids_after

def test_cli_cleanup(tmp_path):
    # Start a dummy process
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    pid = proc.pid
    
    # Write to active_pids.json
    pid_dir = tmp_path / ".writing-context"
    pid_dir.mkdir(exist_ok=True)
    pid_file = pid_dir / "active_pids.json"
    pid_file.write_text(json.dumps([pid]), encoding="utf-8")
    
    from argparse import Namespace
    
    # Run cleanup
    cleanup_command(Namespace(project_root=str(tmp_path)))
    
    # Assert process is terminated
    # Wait up to 1s to be sure
    for _ in range(10):
        if proc.poll() is not None:
            break
        time.sleep(0.1)
        
    assert proc.poll() is not None
    
    # Assert active_pids.json is empty
    pids = json.loads(pid_file.read_text(encoding="utf-8"))
    assert len(pids) == 0

def test_zotero_provider_is_available():
    from writing_context_rtfm.config import AppConfig, RTFMConfig, ContextConfig, CacheConfig, SectionCardsConfig
    from writing_context_rtfm.schemas import ProviderConfig, MCPServerConfig
    from writing_context_rtfm.providers.local import ZoteroProvider
    
    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(),
        context=ContextConfig(),
        cache=CacheConfig(enabled=False),
        section_cards=SectionCardsConfig(),
        providers={
            "zotero": ProviderConfig(
                enabled=True,
                mcp_server=MCPServerConfig(command="zotero-mcp")
            )
        }
    )
    
    provider = ZoteroProvider(config)
    assert provider.is_available(config) is True
    assert provider.provider_id == "zotero"

def test_zotero_provider_fetch_context():
    from writing_context_rtfm.config import AppConfig, RTFMConfig, ContextConfig, CacheConfig, SectionCardsConfig
    from writing_context_rtfm.schemas import ProviderConfig, MCPServerConfig
    from writing_context_rtfm.providers.local import ZoteroProvider
    
    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(),
        context=ContextConfig(),
        cache=CacheConfig(enabled=False),
        section_cards=SectionCardsConfig(),
        providers={
            "zotero": ProviderConfig(
                enabled=True,
                mcp_server=MCPServerConfig(command="zotero-mcp")
            )
        }
    )
    
    provider = ZoteroProvider(config)
    
    # Mock manager call_tool
    mock_manager = MagicMock()
    mock_tool_result = MagicMock()
    mock_content_block = MagicMock()
    mock_content_block.type = "text"
    mock_content_block.text = "Sample citation metadata"
    mock_tool_result.content = [mock_content_block]
    mock_manager.call_tool.return_value = mock_tool_result
    
    with patch("writing_context_rtfm.providers.local.get_shared_manager", return_value=mock_manager):
        spans = provider.fetch_context(["mindfulness"], target=None, limit=5)
        
        assert len(spans) == 1
        assert spans[0].path == "zotero:mindfulness"
        assert spans[0].metadata["snippet"] == "Sample citation metadata"
        assert spans[0].score == 0.8
        
        mock_manager.call_tool.assert_called_once_with(
            command="zotero-mcp",
            args=[],
            tool_name="zotero_search_items",
            arguments={"query": "mindfulness", "limit": 5},
            env=None
        )

def test_zotero_provider_citekey_fallback(tmp_path):
    from writing_context_rtfm.config import AppConfig, RTFMConfig, ContextConfig, CacheConfig, SectionCardsConfig
    from writing_context_rtfm.schemas import ProviderConfig, MCPServerConfig
    from writing_context_rtfm.providers.local import ZoteroProvider
    import tempfile
    
    # 1. Create a dummy LaTeX target file containing a citation
    target_file = tmp_path / "section_intro.tex"
    target_file.write_text("This is a claim \\cite{smith2023}.", encoding="utf-8")
    
    # 2. Create section_cards.yaml
    sc_yaml = tmp_path / "section_cards.yaml"
    sc_yaml.write_text(
        "version: 1\n"
        "sections:\n"
        "  section_intro:\n"
        "    path: \"section_intro.tex\"\n",
        encoding="utf-8"
    )
    
    config = AppConfig(
        version=1,
        rtfm=RTFMConfig(project_root=str(tmp_path)),
        context=ContextConfig(),
        cache=CacheConfig(enabled=False),
        section_cards=SectionCardsConfig(path=str(sc_yaml)),
        providers={
            "zotero": ProviderConfig(
                enabled=True,
                mcp_server=MCPServerConfig(command="zotero-mcp")
            )
        }
    )
    
    provider = ZoteroProvider(config)
    
    # Mock manager call_tool
    mock_manager = MagicMock()
    
    # Setup call_tool side_effect:
    # 1. zotero_search_by_citation_key returns "No item found"
    # 2. zotero_search_items for "smith 2023" returns "Item Key: ABCDEFGH"
    # 3. zotero_get_annotations for "ABCDEFGH" returns "User note content"
    def mock_call_tool(command, args, tool_name, arguments, env=None):
        mock_res = MagicMock()
        mock_block = MagicMock()
        mock_block.type = "text"
        if tool_name == "zotero_search_by_citation_key":
            mock_block.text = "No item found with citation key: 'smith2023'"
        elif tool_name == "zotero_search_items" and arguments.get("query") == "smith 2023":
            mock_block.text = "Title: Smart paper\nItem Key: ABCDEFGH\nAuthors: Smith, A."
        elif tool_name == "zotero_get_annotations" and arguments.get("item_key") == "ABCDEFGH":
            mock_block.text = "User note content"
        else:
            mock_block.text = "empty"
        mock_res.content = [mock_block]
        return mock_res
        
    mock_manager.call_tool.side_effect = mock_call_tool
    
    with patch("writing_context_rtfm.providers.local.get_shared_manager", return_value=mock_manager):
        spans = provider.fetch_context([], target="section_intro", limit=5)
        
        # Verify fallback lookup succeeded
        assert len(spans) == 1
        assert spans[0].path == "zotero:smith2023"
        assert "Smart paper" in spans[0].metadata["snippet"]
        assert "User note content" in spans[0].metadata["snippet"]
        assert spans[0].score == 0.95
        
        # Verify correct tools called
        mock_manager.call_tool.assert_any_call(
            command="zotero-mcp",
            args=[],
            tool_name="zotero_search_by_citation_key",
            arguments={"citekey": "smith2023"},
            env=None
        )
        mock_manager.call_tool.assert_any_call(
            command="zotero-mcp",
            args=[],
            tool_name="zotero_search_items",
            arguments={"query": "smith 2023", "limit": 3},
            env=None
        )
        mock_manager.call_tool.assert_any_call(
            command="zotero-mcp",
            args=[],
            tool_name="zotero_get_annotations",
            arguments={"item_key": "ABCDEFGH"},
            env=None
        )

