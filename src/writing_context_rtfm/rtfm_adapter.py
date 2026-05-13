"""Adapter for RTFM interactions."""
import subprocess
import json
from typing import List, Optional
from writing_context_rtfm.schemas import RTFMResult

class RTFMAdapterError(Exception):
    """Exception raised for errors in the RTFM Adapter."""
    pass

class RTFMAdapter:
    """Wrapper around the RTFM CLI."""
    
    def _run_command(self, cmd: List[str]) -> str:
        """Run a CLI command and return its standard output."""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout
        except subprocess.CalledProcessError as e:
            raise RTFMAdapterError(f"Command '{' '.join(cmd)}' failed with error: {e.stderr}") from e
        except FileNotFoundError as e:
            raise RTFMAdapterError("RTFM CLI not found. Is it installed and in PATH?") from e

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

    def sync(self, project_root: str, *, corpus: str) -> bool:
        """Trigger RTFM sync."""
        cmd = ["rtfm", "sync", project_root, "--corpus", corpus]
        try:
            self._run_command(cmd)
            return True
        except RTFMAdapterError:
            return False
