import json
import os
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from writing_context_rtfm.providers.manager import (
    LocalMCPClientManager,
    get_shared_manager,
    register_pid,
    unregister_pid,
)


def test_register_unregister_pid(tmp_path):
    # Test register
    register_pid(12345, tmp_path)
    
    pid_file = tmp_path / ".writing-context" / "active_pids.json"
    assert pid_file.exists()
    pids = json.loads(pid_file.read_text())
    assert 12345 in pids
    
    # Test duplicate register
    register_pid(12345, tmp_path)
    pids = json.loads(pid_file.read_text())
    assert pids.count(12345) == 1
    
    # Test unregister
    unregister_pid(12345, tmp_path)
    pids = json.loads(pid_file.read_text())
    assert 12345 not in pids


def test_get_shared_manager(tmp_path):
    manager1 = get_shared_manager(str(tmp_path))
    manager2 = get_shared_manager(str(tmp_path))
    
    assert manager1 is manager2
    assert isinstance(manager1, LocalMCPClientManager)


@patch("mcp.client.stdio.stdio_client")
@patch("writing_context_rtfm.providers.manager.ClientSession")
def test_manager_call_tool(mock_session_cls, mock_stdio, tmp_path):
    manager = LocalMCPClientManager(str(tmp_path))
    
    # Setup mocks
    mock_stdio_cm = AsyncMock()
    mock_stdio_cm.__aenter__.return_value = (MagicMock(), MagicMock())
    mock_stdio.return_value = mock_stdio_cm
    
    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_session.call_tool = AsyncMock(return_value="tool_result")
    
    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__.return_value = mock_session
    mock_session_cls.return_value = mock_session_cm
    
    # Call tool
    result = manager.call_tool(
        command="test_cmd",
        args=["arg1"],
        tool_name="test_tool",
        arguments={"param": "value"}
    )
    
    assert result == "tool_result"
    
    # Call again to test caching session
    result2 = manager.call_tool(
        command="test_cmd",
        args=["arg1"],
        tool_name="test_tool2",
        arguments={}
    )
    assert result2 == "tool_result"
    assert mock_session.initialize.call_count == 1  # only initialized once
    
    # Shutdown
    manager.shutdown()
    assert not manager.thread.is_alive()


def test_manager_shutdown(tmp_path):
    manager = LocalMCPClientManager(str(tmp_path))
    manager.registered_pids = [999999]
    register_pid(999999, tmp_path)
    
    # Shutdown
    manager.shutdown()
    
    assert not manager.thread.is_alive()
    assert 999999 not in manager.registered_pids
    
    # Check pid file
    pid_file = tmp_path / ".writing-context" / "active_pids.json"
    pids = json.loads(pid_file.read_text())
    assert 999999 not in pids
