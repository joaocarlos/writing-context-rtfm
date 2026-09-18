"""Git integration utilities for detecting modified files and line ranges."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_git_diff_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Parse git diff output with -U0 to extract modified line ranges per file.

    Returns a mapping of relative file path to list of (start_line, end_line) tuples (1-indexed, inclusive).
    """
    modified_ranges: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            if current_file not in modified_ranges:
                modified_ranges[current_file] = []
        elif line.startswith("+++ /dev/null"):
            # File was deleted
            current_file = None
        elif line.startswith("@@ ") and current_file is not None:
            match = _HUNK_RE.match(line)
            if match:
                new_start = int(match.group(3))
                new_count = int(match.group(4)) if match.group(4) is not None else 1
                if new_count > 0:
                    start_line = new_start
                    end_line = new_start + new_count - 1
                    modified_ranges[current_file].append((start_line, end_line))

    # Clean up empty file entries
    return {f: ranges for f, ranges in modified_ranges.items() if ranges}


def get_git_modified_ranges(
    project_root: str | Path, base_ref: str | None = None
) -> dict[str, list[tuple[int, int]]]:
    """Inspect git repository at project_root and extract line ranges of modified code.

    If base_ref is provided (e.g. 'main', 'HEAD~1'), compares against that ref.
    Otherwise, compares working tree against HEAD (including unstaged and staged changes).
    Returns an empty dict if project_root is not a git repo or if git fails.
    """
    root = Path(project_root).resolve()
    cmd = ["git", "diff", "-U0"]
    if base_ref:
        cmd.append(base_ref)
    else:
        cmd.append("HEAD")

    try:
        res = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            # Fallback to plain git diff if HEAD ref does not exist yet (e.g. unborn repo)
            res2 = subprocess.run(
                ["git", "diff", "-U0"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if res2.returncode == 0:
                return parse_git_diff_ranges(res2.stdout)
            return {}

        return parse_git_diff_ranges(res.stdout)
    except (OSError, subprocess.SubprocessError):
        return {}
