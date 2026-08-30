import asyncio
import json
import logging
import sys
import threading
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any

from mcp import ClientSession

logger = logging.getLogger("mcp-server")

SessionKey = tuple[
    str,
    tuple[str, ...],
    tuple[tuple[str, str], ...],
    str | None,
]


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
    with suppress(Exception):
        pid_file.write_text(json.dumps(pids), encoding="utf-8")


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

        self.sessions: dict[SessionKey, ClientSession] = {}
        self.exit_stacks: dict[SessionKey, AsyncExitStack] = {}
        self.registered_pids: list[int] = []
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

        async def custom_create_process(
            command: Any, args: Any, env: Any = None, errlog: Any = sys.stderr, cwd: Any = None
        ) -> Any:
            proc = await _original(command, args, env, errlog, cwd)
            pid = None
            if hasattr(proc, "pid"):
                pid = proc.pid
            elif hasattr(proc, "_process"):
                p = proc._process
                if hasattr(p, "pid"):
                    pid = p.pid
                elif hasattr(p, "_proc") and hasattr(p._proc, "pid"):
                    pid = p._proc.pid
            if pid:
                register_pid(pid, self.workspace_root)
                with self._lock:
                    self.registered_pids.append(pid)
            return proc

        custom_create_process.__patched__ = True  # type: ignore[attr-defined]
        mcp.client.stdio._create_platform_compatible_process = custom_create_process

    @staticmethod
    def _make_session_key(
        command: str,
        args: list[str],
        env: dict[str, str] | None,
        session_scope: str | None,
    ) -> SessionKey:
        return command, tuple(args), tuple(sorted((env or {}).items())), session_scope

    async def _get_or_create_session(
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        session_scope: str | None = None,
    ) -> ClientSession:
        key = self._make_session_key(command, args, env, session_scope)
        with self._lock:
            if key in self.sessions:
                return self.sessions[key]

        from mcp.client.stdio import StdioServerParameters, stdio_client

        stack = AsyncExitStack()
        try:
            server_params = StdioServerParameters(command=command, args=args, env=env)
            read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()

            with self._lock:
                self.sessions[key] = session
                self.exit_stacks[key] = stack
            return session
        except Exception as e:
            await stack.aclose()
            logger.error(f"Failed to start local MCP server {command} with args {args}: {e}")
            raise

    def call_tool(
        self,
        command: str,
        args: list[str],
        tool_name: str,
        arguments: dict[str, Any],
        env: dict[str, str] | None = None,
        timeout: float = 30.0,
        session_scope: str | None = None,
    ) -> Any:
        """Call an MCP tool on the given subprocess server thread-safely."""

        async def _call() -> Any:
            session = await self._get_or_create_session(command, args, env, session_scope)
            return await session.call_tool(tool_name, arguments)

        future = asyncio.run_coroutine_threadsafe(_call(), self.loop)
        return future.result(timeout=timeout)

    def shutdown(self) -> None:
        """Cleanly close all active sessions, unregister PIDs, and stop background loop."""

        # 1. Cleanly close sessions inside loop
        async def _close_all() -> None:
            for _key, stack in list(self.exit_stacks.items()):
                with suppress(Exception):
                    await stack.aclose()
            self.sessions.clear()
            self.exit_stacks.clear()

        if self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(_close_all(), self.loop)
            with suppress(Exception):
                future.result(timeout=5)

            # 2. Stop the loop
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)

        # 3. Unregister all PIDs registered by this manager instance
        with self._lock:
            for pid in self.registered_pids:
                unregister_pid(pid, self.workspace_root)
            self.registered_pids.clear()
