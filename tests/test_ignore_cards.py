from pathlib import Path

import yaml

from writing_context_rtfm.features import find_entry_files, initialize_section_cards
from writing_context_rtfm.latex import build_reference_graph
from writing_context_rtfm.utils import is_path_ignored, load_ignore_spec


def test_load_ignore_spec_none(tmp_path: Path) -> None:
    spec = load_ignore_spec(tmp_path)
    assert spec is None
    assert not is_path_ignored("main.tex", spec)


def test_load_ignore_spec_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored.tex\nbuild/\n", encoding="utf-8")
    spec = load_ignore_spec(tmp_path)
    assert spec is not None
    assert is_path_ignored("ignored.tex", spec)
    assert is_path_ignored("build", spec, is_dir=True)
    assert is_path_ignored("build/output.tex", spec)
    assert not is_path_ignored("main.tex", spec)


def test_load_ignore_spec_rtfmignore(tmp_path: Path) -> None:
    (tmp_path / ".rtfmignore").write_text("scratch.tex\ndrafts/\n*.draft.md\n", encoding="utf-8")
    spec = load_ignore_spec(tmp_path)
    assert spec is not None
    assert is_path_ignored("scratch.tex", spec)
    assert is_path_ignored("drafts", spec, is_dir=True)
    assert is_path_ignored("drafts/chap1.tex", spec)
    assert is_path_ignored("notes.draft.md", spec)
    assert not is_path_ignored("notes.md", spec)
    assert not is_path_ignored("main.tex", spec)


def test_initialize_section_cards_honors_rtfmignore(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text("\\section{Main}\nHello main.\n", encoding="utf-8")
    (tmp_path / "ignored.tex").write_text("\\section{Ignored}\nHello ignored.\n", encoding="utf-8")
    (tmp_path / "draft.md").write_text("# Draft\nDraft notes.\n", encoding="utf-8")
    (tmp_path / "keep.md").write_text("# Keep\nKeep notes.\n", encoding="utf-8")

    (tmp_path / ".rtfmignore").write_text("ignored.tex\ndraft.md\n", encoding="utf-8")

    result = initialize_section_cards(str(tmp_path))
    assert result["status"] == "success"

    sc_path = tmp_path / ".writing-context" / "section_cards.yaml"
    assert sc_path.is_file()

    with open(sc_path, encoding="utf-8") as f:
        cards = yaml.safe_load(f)

    paths = [sec.get("path") for sec in cards.get("sections", {}).values()]
    assert "main.tex" in paths
    assert "keep.md" in paths
    assert "ignored.tex" not in paths
    assert "draft.md" not in paths


def test_initialize_section_cards_honors_directory_ignore(tmp_path: Path) -> None:
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()
    (drafts_dir / "chap1.tex").write_text("\\section{Chap 1}\n", encoding="utf-8")

    sections_dir = tmp_path / "sections"
    sections_dir.mkdir()
    (sections_dir / "01_intro.tex").write_text("\\section{Intro}\n", encoding="utf-8")

    (tmp_path / ".rtfmignore").write_text("drafts/\n", encoding="utf-8")

    result = initialize_section_cards(str(tmp_path))
    assert result["status"] == "success"

    sc_path = tmp_path / ".writing-context" / "section_cards.yaml"
    with open(sc_path, encoding="utf-8") as f:
        cards = yaml.safe_load(f)

    paths = [sec.get("path") for sec in cards.get("sections", {}).values()]
    assert "sections/01_intro.tex" in paths
    assert not any("drafts" in p for p in paths)


def test_find_entry_files_honors_rtfmignore_and_gitignore(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text("\\section{Main}\n", encoding="utf-8")
    (tmp_path / "ignored_by_git.tex").write_text("\\section{Git Ignore}\n", encoding="utf-8")
    (tmp_path / "ignored_by_rtfm.tex").write_text("\\section{RTFM Ignore}\n", encoding="utf-8")

    (tmp_path / ".gitignore").write_text("ignored_by_git.tex\n", encoding="utf-8")
    (tmp_path / ".rtfmignore").write_text("ignored_by_rtfm.tex\n", encoding="utf-8")

    entries = find_entry_files(str(tmp_path))
    assert entries == ["main.tex"]


def test_build_reference_graph_honors_rtfmignore(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text("\\section{Main}\\label{sec:main}\n", encoding="utf-8")
    (tmp_path / "draft.tex").write_text("\\section{Draft}\\label{sec:draft}\n", encoding="utf-8")

    (tmp_path / ".rtfmignore").write_text("draft.tex\n", encoding="utf-8")

    graph = build_reference_graph(str(tmp_path))
    assert "main.tex" in graph["references"]
    assert "sec:main" in graph["labels"]
    assert "draft.tex" not in graph["references"]
    assert "sec:draft" not in graph["labels"]
