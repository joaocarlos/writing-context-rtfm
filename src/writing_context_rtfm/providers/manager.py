import asyncio
import threading
import logging
import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from contextlib import AsyncExitStack
from mcp import ClientSession

logger = logging.getLogger("mcp-server")

def register_pid(pid: int, workspace_root: Path) -> None:
    wc_dir = workspace_root / ".writing-context"
    wc_dir.mkdir(exist_ok=True)
    pid_file = wc_dir / "active_pids.json"
    pids = []
    if pid_file.exists():
        try:
            pids = json.loads(pid_file.read_text(encoding="utf-8"))
            if not isinstance(pids, list):
                pids = []
        except Exception:
            pass
    if pid not in pids:
        pids.append(pid)
    try:
        pid_file.write_text(json.dumps(pids), encoding="utf-8")
    except Exception:
        pass

def unregister_pid(pid: int, workspace_root: Path) -> None:
    pid_file = workspace_root / ".writing-context" / "active_pids.json"
    if pid_file.exists():
        try:
            pids = json.loads(pid_file.read_text(encoding="utf-8"))
            if isinstance(pids, list) and pid in pids:
                pids.remove(pid)
                pid_file.write_text(json.dumps(pids), encoding="utf-8")
        except Exception:
            pass

_shared_manager = None

def get_shared_manager(workspace_root: str = ".") -> "LocalMCPClientManager":
    global _shared_manager
    if _shared_manager is None:
        _shared_manager = LocalMCPClientManager(workspace_root)
    return _shared_manager

class LocalMCPClientManager:
    def __init__(self, workspace_root: str = "."):
        self.workspace_root = Path(workspace_root).resolve()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        
        import atexit
        atexit.register(self.shutdown)
        
        self.sessions: Dict[Tuple[str, Tuple[str, ...]], ClientSession] = {}
        self.exit_stacks: Dict[Tuple[str, Tuple[str, ...]], AsyncExitStack] = {}
        self.registered_pids: List[int] = []
        self._lock = threading.Lock()
        self._apply_patch()
        
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _apply_patch(self) -> None:
        import mcp.client.stdio
        _original = mcp.client.stdio._create_platform_compatible_process
        
        # Avoid double-patching if manager is re-instantiated
        if getattr(_original, "__patched__", False):
            return
            
        async def custom_create_process(command, args, env=None, errlog=sys.stderr, cwd=None):
            proc = await _original(command, args, env, errlog, cwd)
            pid = None
            if hasattr(proc, "pid"):
                pid = proc.pid
            elif hasattr(proc, "_process"):
                p = getattr(proc, "_process")
                if hasattr(p, "pid"):
                    pid = p.pid
                elif hasattr(p, "_proc") and hasattr(p._proc, "pid"):
                    pid = p._proc.pid
            if pid:
                register_pid(pid, self.workspace_root)
                with self._lock:
                    self.registered_pids.append(pid)
            return proc
            
        custom_create_process.__patched__ = True
        mcp.client.stdio._create_platform_compatible_process = custom_create_process

    async def _get_or_create_session(self, command: str, args: List[str], env: Optional[Dict[str, str]] = None) -> ClientSession:
        key = (command, tuple(args))
        with self._lock:
            if key in self.sessions:
                return self.sessions[key]
                
        from mcp.client.stdio import stdio_client, StdioServerParameters
        
        stack = AsyncExitStack()
        try:
            server_params = StdioServerParameters(command=command, args=args, env=env)
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(server_params)
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            
            with self._lock:
                self.sessions[key] = session
                self.exit_stacks[key] = stack
            return session
        except Exception as e:
            await stack.aclose()
            logger.error(f"Failed to start local MCP server {command} with args {args}: {e}")
            raise

    def call_tool(self, command: str, args: List[str], tool_name: str, arguments: Dict[str, Any], env: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> Any:
        """Call an MCP tool on the given subprocess server thread-safely."""
        async def _call():
            session = await self._get_or_create_session(command, args, env)
            return await session.call_tool(tool_name, arguments)
            
        future = asyncio.run_coroutine_threadsafe(_call(), self.loop)
        return future.result(timeout=timeout)

    def shutdown(self) -> None:
        """Cleanly close all active sessions, unregister PIDs, and stop background loop."""
        # 1. Cleanly close sessions inside loop
        async def _close_all():
            for key, stack in list(self.exit_stacks.items()):
                try:
                    await stack.aclose()
                except Exception:
                    pass
            self.sessions.clear()
            self.exit_stacks.clear()
            
        if self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(_close_all(), self.loop)
            try:
                future.result(timeout=5)
            except Exception:
                pass
            
            # 2. Stop the loop
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            
        # 3. Unregister all PIDs registered by this manager instance
        with self._lock:
            for pid in self.registered_pids:
                unregister_pid(pid, self.workspace_root)
            self.registered_pids.clear()
