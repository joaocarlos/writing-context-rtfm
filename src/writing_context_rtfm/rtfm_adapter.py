"""Adapter for RTFM interactions."""
import subprocess
import json
import sys
import os
import shutil
from typing import List, Optional
from writing_context_rtfm.schemas import RTFMResult

class RTFMAdapterError(Exception):
    """Exception raised for errors in the RTFM Adapter."""
    pass

class RTFMAdapter:
    """Wrapper around the RTFM CLI."""
    
    def __init__(self, project_root: Optional[str] = None) -> None:
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

    def _run_command(self, cmd: List[str]) -> str:
        """Run a CLI command and return its standard output."""
        if cmd[0] == "rtfm":
            cmd[0] = self.resolved_rtfm

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout
        except subprocess.CalledProcessError as e:
            raise RTFMAdapterError(f"Command '{' '.join(cmd)}' failed with error: {e.stderr}") from e
        except FileNotFoundError as e:
            raise RTFMAdapterError(f"RTFM CLI not found (tried to run {cmd[0]}). Is it installed?") from e

    def search(self, query: str, *, corpus: str, limit: int = 10) -> List[RTFMResult]:
        """Search indexed content using RTFM."""
        cmd = ["rtfm", "search", query, "--corpus", corpus, "--limit", str(limit), "--format", "json"]
        try:
            output = self._run_command(cmd)
            data = json.loads(output)
            results = []
            for item in data.get("results", []):
                chunk = item.get("chunk", {})
                results.append(RTFMResult(
                    path=chunk.get("book_file", ""),
                    line_start=chunk.get("line_start"),
                    line_end=chunk.get("line_end"),
                    snippet=chunk.get("content"),
                    score=item.get("score"),
                    metadata={
                        "chapter_title": chunk.get("chapter_title"),
                        "book_title": chunk.get("book_title"),
                        "rank": item.get("rank"),
                    }
                ))
            return results
        except json.JSONDecodeError as e:
            raise RTFMAdapterError(f"Failed to parse JSON output from RTFM search: {e}") from e

    def context(self, path: str, line_start: int, line_end: int) -> str:
        """Retrieve context around a file span."""
        cmd = ["rtfm", "context", path, "--line-start", str(line_start), "--line-end", str(line_end)]
        return self._run_command(cmd)

    def expand(self, result_id: str) -> str:
        """Expand selected results."""
        cmd = ["rtfm", "expand", result_id]
        return self._run_command(cmd)

    def sync(self, project_root: str, *, corpus: str) -> None:
        """Trigger RTFM sync."""
        cmd = ["rtfm", "sync", project_root, "--corpus", corpus]
        self._run_command(cmd)

