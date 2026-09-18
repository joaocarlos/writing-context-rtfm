"""Tests for git diff awareness and modified line range context boosting."""

from __future__ import annotations

import tempfile
from pathlib import Path

from writing_context_rtfm.config import load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.git_utils import get_git_modified_ranges, parse_git_diff_ranges
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import SourceSpan
from writing_context_rtfm.storage import ExtensionStore


def test_parse_git_diff_ranges() -> None:
    diff_output = """diff --git a/intro.tex b/intro.tex
index abcdef..123456 100644
--- a/intro.tex
+++ b/intro.tex
@@ -10,0 +11,5 @@
+New line 11
+New line 12
+New line 13
+New line 14
+New line 15
@@ -50,2 +55,1 @@
-Old line
-Old line 2
+Single replaced line
diff --git a/deleted.tex b/deleted.tex
deleted file mode 100644
--- a/deleted.tex
+++ /dev/null
@@ -1,10 +0,0 @@
diff --git a/model.py b/model.py
index 1111..2222 100644
--- a/model.py
+++ b/model.py
@@ -100 +100 @@
-def old():
+def new():
"""
    ranges = parse_git_diff_ranges(diff_output)

    assert "intro.tex" in ranges
    assert (11, 15) in ranges["intro.tex"]
    assert (55, 55) in ranges["intro.tex"]

    assert "deleted.tex" not in ranges

    assert "model.py" in ranges
    assert (100, 100) in ranges["model.py"]


def test_get_git_modified_ranges_non_repo_handled_gracefully() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        ranges = get_git_modified_ranges(tmp_dir)
        assert ranges == {}


def test_classify_priority_boosts_git_modified_spans() -> None:
    config = load_config(".")
    adapter = RTFMAdapter(project_root=".")
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = str(Path(tmp_dir) / "test.db")
        with ExtensionStore(db_path) as store:
            generator = ContextPackGenerator(config, None, adapter, store)

            spans = [
                SourceSpan(
                    path="section1.tex",
                    line_start=10,
                    line_end=20,
                    reason="Initial query match",
                    score=0.50,
                    priority="supporting",
                    source_role="reference",
                ),
                SourceSpan(
                    path="section2.tex",
                    line_start=50,
                    line_end=60,
                    reason="Unmodified section",
                    score=0.50,
                    priority="supporting",
                    source_role="reference",
                ),
            ]

            git_modified_ranges = {
                "section1.tex": [(15, 25)],
            }

            classified = generator._classify_priority(
                spans=spans,
                target_card=None,
                dep_cards=[],
                git_modified_ranges=git_modified_ranges,
            )

            s1 = next(s for s in classified if s.path == "section1.tex")
            s2 = next(s for s in classified if s.path == "section2.tex")

            # s1 overlaps with modified range (15, 25)
            assert s1.priority == "essential"
            assert s1.score > 0.50
            assert "[git-diff modified]" in s1.reason
            assert (s1.metadata or {}).get("git_modified") is True

            # s2 has no git modifications
            assert s2.priority != "essential"
            assert "[git-diff modified]" not in s2.reason
            assert not (s2.metadata or {}).get("git_modified")


def test_context_pack_generation_with_git_diff() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = str(tmp_path / "cache.db")

        # Create a mock section file
        sec_file = tmp_path / "method.tex"
        sec_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n", encoding="utf-8")

        config = load_config(str(tmp_path))

        adapter = RTFMAdapter(project_root=str(tmp_path))
        with ExtensionStore(db_path) as store:
            store.init_db()
            generator = ContextPackGenerator(config, None, adapter, store)

            # Generate with git_diff=True (in clean dir, no crash)
            pack = generator.generate(
                task="Draft methodology",
                target=None,
                token_budget=1000,
                git_diff=True,
                project_root=str(tmp_path),
            )

            assert pack.status in ("complete", "degraded")
            # Quality should have git_diff fields
            assert pack.quality is not None
            assert "git_diff_active" in pack.quality
