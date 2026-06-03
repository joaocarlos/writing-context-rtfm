"""Adapter for RTFM interactions."""

import json
import os
import shutil
import subprocess
import sys

from writing_context_rtfm.schemas import RTFMResult


class RTFMAdapterError(Exception):
    """Exception raised for errors in the RTFM Adapter."""

    pass


class RTFMAdapter:
    """Wrapper around the RTFM CLI."""

    def __init__(self, project_root: str | None = None) -> None:
        self.project_root = project_root
        self.resolved_rtfm = "rtfm"

        resolved = shutil.which("rtfm")
        if resolved:
            self.resolved_rtfm = resolved
        else:
            # Fallback to looking in the same directory as the current Python executable
            python_dir = os.path.dirname(sys.executable)
            candidate = os.path.join(python_dir, "rtfm")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                self.resolved_rtfm = candidate

    def _run_command(self, cmd: list[str], capture_output: bool = True) -> str:
        """Run a CLI command and return its standard output."""
        if cmd[0] == "rtfm":
            cmd[0] = self.resolved_rtfm

        try:
            if capture_output:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, check=True, cwd=self.project_root
                )
                return result.stdout
            else:
                subprocess.run(cmd, text=True, check=True, cwd=self.project_root)
                return ""
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr if e.stderr else "See console output above."
            raise RTFMAdapterError(f"Command '{' '.join(cmd)}' failed with error: {err_msg}") from e
        except FileNotFoundError as e:
            raise RTFMAdapterError(
                f"RTFM CLI not found (tried to run {cmd[0]}). Is it installed?"
            ) from e

    def search(self, query: str, *, corpus: str, limit: int = 10) -> list[RTFMResult]:
        """Search indexed content using RTFM."""
        cmd = [
            "rtfm",
            "search",
            query,
            "--corpus",
            corpus,
            "--limit",
            str(limit),
            "--format",
            "json",
        ]
        try:
            output = self._run_command(cmd)
            data = json.loads(output)
            results = []
            for item in data.get("results", []):
                chunk = item.get("chunk", {})
                results.append(
                    RTFMResult(
                        path=chunk.get("book_file", ""),
                        line_start=chunk.get("line_start"),
                        line_end=chunk.get("line_end"),
                        snippet=chunk.get("content"),
                        score=item.get("score"),
                        metadata={
                            "chapter_title": chunk.get("chapter_title"),
                            "book_title": chunk.get("book_title"),
                            "rank": item.get("rank"),
                        },
                    )
                )
            return results
        except json.JSONDecodeError as e:
            raise RTFMAdapterError(f"Failed to parse JSON output from RTFM search: {e}") from e

    def context(self, path: str, line_start: int, line_end: int) -> str:
        """Retrieve context around a file span."""
        cmd = [
            "rtfm",
            "context",
            path,
            "--line-start",
            str(line_start),
            "--line-end",
            str(line_end),
        ]
        return self._run_command(cmd)

    def expand(self, result_id: str) -> str:
        """Expand selected results."""
        cmd = ["rtfm", "expand", result_id]
        return self._run_command(cmd)

    def sync(
        self, path: str | None = None, *, corpus: str | None = None, capture_output: bool = True
    ) -> None:
        """Trigger RTFM sync.

        If path and corpus are not provided, runs `rtfm sync` without arguments
        to let RTFM use configurations defined in .rtfm/config.json.
        """
        cmd = ["rtfm", "sync"]
        if path:
            cmd.append(path)
        if corpus:
            cmd.extend(["--corpus", corpus])
        self._run_command(cmd, capture_output=capture_output)
