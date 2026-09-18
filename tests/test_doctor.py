"""Tests for the doctor diagnostic module."""

import json
from pathlib import Path
from unittest.mock import patch

from writing_context_rtfm.doctor import (
    DoctorReport,
    check_api_keys,
    check_dependencies,
    check_index,
    check_local_models,
    check_project_scaffolding,
    check_python,
    check_zotero_and_bibtex,
    format_text_report,
    run_diagnostics,
)


def test_check_python():
    cat = check_python()
    assert cat.category == "Python Environment"
    item_names = [item.name for item in cat.items]
    assert "Python Version" in item_names
    assert "Python Binary" in item_names
    assert "Operating System" in item_names

    ver_item = next(i for i in cat.items if i.name == "Python Version")
    assert ver_item.status in ("OK", "FAIL")


def test_check_dependencies():
    cat = check_dependencies()
    assert cat.category == "Dependencies"
    item_names = [item.name for item in cat.items]
    assert "Package rtfm" in item_names
    assert "Package mcp" in item_names
    assert "RTFM CLI" in item_names


def test_check_index_uninitialized(tmp_path: Path):
    cat = check_index(tmp_path)
    assert cat.category == "Retrieval Index (RTFM)"
    item = next(i for i in cat.items if i.name == "RTFM DB")
    assert item.status == "WARN"
    assert "database found" in item.message.lower() or "not found" in item.message.lower()


def test_check_zotero_and_bibtex(tmp_path: Path):
    # Create sample .bib file
    (tmp_path / "sample.bib").write_text("@article{key, title={Test}}")
    cat = check_zotero_and_bibtex(tmp_path)

    assert cat.category == "Literature & Citations (BibTeX / Zotero)"
    bib_item = next(i for i in cat.items if i.name == "Offline BibTeX Files")
    assert bib_item.status == "OK"
    assert "Found 1 file(s)" in bib_item.message


def test_check_api_keys(tmp_path: Path):
    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test12345678"}):
        cat = check_api_keys(tmp_path)
        openai_item = next(i for i in cat.items if i.name == "OpenAI API Key")
        assert openai_item.status == "OK"
        assert "sk-t...5678" in openai_item.message


def test_check_local_models():
    cat = check_local_models()
    assert cat.category == "Local Models & Hardware"
    item_names = [i.name for i in cat.items]
    assert "Inference Hardware" in item_names
    assert "FastEmbed Cache" in item_names


def test_check_project_scaffolding(tmp_path: Path):
    cat = check_project_scaffolding(tmp_path)
    assert cat.category == "Project Configuration & Section Cards"
    config_item = next(i for i in cat.items if i.name == "Config")
    assert config_item.status == "WARN"
    assert "config.yaml not found" in config_item.message


def test_run_diagnostics_and_format_report(tmp_path: Path):
    report = run_diagnostics(tmp_path)
    assert isinstance(report, DoctorReport)
    assert len(report.categories) >= 6

    text = format_text_report(report)
    assert "Writing Context RTFM Extension Doctor" in text
    assert "[Python Environment]" in text
    assert "[Dependencies]" in text
    assert "Doctor Summary:" in text

    report_dict = report.to_dict()
    assert "healthy" in report_dict
    assert "categories" in report_dict
    json_str = json.dumps(report_dict)
    assert len(json_str) > 50
