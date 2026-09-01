"""Private, opt-in real-corpus context-quality benchmark.

The committed module contains no corpus text or model credentials. Runtime inputs and all
non-anonymized artifacts are expected beneath Git-ignored ``benchmark/*.local`` paths.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import yaml

from writing_context_rtfm.config import AppConfig, load_config
from writing_context_rtfm.context_pack import ContextPackGenerator
from writing_context_rtfm.hashing import compute_rtfm_fingerprint, stable_hash
from writing_context_rtfm.providers import get_active_providers
from writing_context_rtfm.providers.bibtex import parse_bibtex_file
from writing_context_rtfm.rtfm_adapter import RTFMAdapter
from writing_context_rtfm.schemas import ContextPack, SourceSpan
from writing_context_rtfm.section_cards import load_section_cards
from writing_context_rtfm.storage import ExtensionStore
from writing_context_rtfm.token_budget import estimate_tokens
from writing_context_rtfm.utils import resolve_rtfm_db_path
from writing_context_rtfm.virtual_doc import VirtualDocumentParser, sanitize_node_id

STRATEGIES = ("full_visible", "rtfm_topk", "pack_baseline", "pack_rrf")
CRITERIA = (
    "evidence_support",
    "completeness",
    "constraint_adherence",
    "citation_correctness",
    "writing_fitness",
)
PROMPT_VERSION = "writing-context-quality-v1"
RETRIEVAL_POLICY_VERSION = "four-strategy-candidate-diagnostics-v3"
JUDGE_PROMPT_VERSION = "blinded-dual-judge-v1"
ARTIFACT_VERSION = 1
INDEX_COMMAND_VERSION = "rtfm-sync-explicit-in-process-v2"
MASK_TEMPLATE = "% BENCHMARK_TARGET_MASKED:{case_id}"


class BenchmarkError(RuntimeError):
    """Raised when a benchmark invariant is violated."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects silently overwritten mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def canonical_json(value: Any) -> str:
    """Return a stable JSON representation suitable for content addressing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Any, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if mode is not None:
        tmp.chmod(mode)
    tmp.replace(path)


def _atomic_text(path: Path, value: str, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(value, encoding="utf-8")
    if mode is not None:
        tmp.chmod(mode)
    tmp.replace(path)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise BenchmarkError(f"Cannot read YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BenchmarkError(f"Expected a mapping in {path}")
    return value


def _resolve_input_path(value: str, manifest_path: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    cwd_candidate = (Path.cwd() / path).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return (manifest_path.parent / path).resolve()


@dataclass(frozen=True)
class CaseManifest:
    """Validated private case definition."""

    id: str
    project_id: str
    stages: tuple[str, ...]
    archive: Path
    archive_sha256: str
    archive_root: str
    entry_point: str
    bibliography_files: tuple[str, ...]
    target_selector: str
    target_heading: str
    target_content_hash: str
    task: str
    task_type: str
    context_budget: int
    output_tokens: int
    expected_output_range: tuple[int, int]
    gold_sha256: str
    required_ideas: tuple[dict[str, Any], ...]
    anchor_aliases: dict[str, list[str]]
    required_terminology: tuple[str, ...]
    prohibited_claims: tuple[str, ...]
    protected_literals: tuple[str, ...]
    required_citation_keys: tuple[str, ...]
    valid_citation_keys: tuple[str, ...]
    expected_source_spans: tuple[dict[str, Any], ...]
    annotations: dict[str, Any]
    raw: dict[str, Any] = field(repr=False)

    @property
    def case_hash(self) -> str:
        payload = {**self.raw, "archive_sha256": self.archive_sha256}
        payload.pop("archive", None)
        payload.pop("annotations", None)
        return sha256_text(canonical_json(payload))

    @property
    def annotations_resolved(self) -> bool:
        disagreements = self.annotations.get("disagreements") or []
        statuses = [
            self.annotations.get("curator"),
            self.annotations.get("card_author"),
            self.annotations.get("auditor"),
        ]
        return not disagreements and all(value in {"complete", "approved"} for value in statuses)

    def rubric_for_judge(self) -> dict[str, Any]:
        return {
            "required_ideas": list(self.required_ideas),
            "required_terminology": list(self.required_terminology),
            "prohibited_claims": list(self.prohibited_claims),
            "protected_literals": list(self.protected_literals),
            "required_citation_keys": list(self.required_citation_keys),
            "valid_citation_keys": list(self.valid_citation_keys),
            "expected_output_range": list(self.expected_output_range),
        }


def _require_fields(data: dict[str, Any], fields: tuple[str, ...], context: str) -> None:
    missing = [name for name in fields if name not in data]
    if missing:
        raise BenchmarkError(f"{context} is missing required fields: {', '.join(missing)}")


def load_cases(path: Path) -> list[CaseManifest]:
    """Load and strictly validate a private case manifest."""
    document = _read_yaml(path)
    _require_fields(document, ("version", "cases"), str(path))
    if document["version"] != 1:
        raise BenchmarkError(f"Unsupported case manifest version: {document['version']}")
    defaults = document.get("defaults") or {}
    if not isinstance(defaults, dict) or not isinstance(document["cases"], list):
        raise BenchmarkError("Manifest defaults must be a mapping and cases must be a list")

    required = (
        "id",
        "project_id",
        "stages",
        "archive",
        "archive_sha256",
        "entry_point",
        "bibliography_files",
        "target",
        "task",
        "task_type",
        "expected_output_range",
        "gold_sha256",
        "required_ideas",
        "anchor_aliases",
        "required_terminology",
        "prohibited_claims",
        "protected_literals",
        "required_citation_keys",
        "valid_citation_keys",
        "expected_source_spans",
        "annotations",
    )
    cases: list[CaseManifest] = []
    seen_ids: set[str] = set()
    for index, raw_value in enumerate(document["cases"]):
        if not isinstance(raw_value, dict):
            raise BenchmarkError(f"Case {index} must be a mapping")
        raw = dict(raw_value)
        _require_fields(raw, required, f"Case {index}")
        case_id = str(raw["id"])
        if not re.fullmatch(r"P\d+-[A-Za-z0-9_-]+", case_id):
            raise BenchmarkError(f"Invalid anonymized case id: {case_id}")
        if case_id in seen_ids:
            raise BenchmarkError(f"Duplicate case id: {case_id}")
        seen_ids.add(case_id)
        if not re.fullmatch(r"P\d+", str(raw["project_id"])):
            raise BenchmarkError(f"Invalid anonymized project id for {case_id}")
        stages = tuple(str(value) for value in raw["stages"])
        if not stages or not set(stages) <= {"pilot", "confirmation"}:
            raise BenchmarkError(f"Invalid stages for {case_id}: {stages}")
        target = raw["target"]
        if not isinstance(target, dict):
            raise BenchmarkError(f"Target for {case_id} must be a mapping")
        _require_fields(target, ("selector", "heading", "content_hash"), f"Target {case_id}")
        expected_range = raw["expected_output_range"]
        if (
            not isinstance(expected_range, list)
            or len(expected_range) != 2
            or not all(isinstance(value, int) for value in expected_range)
            or expected_range[0] < 1
            or expected_range[1] < expected_range[0]
        ):
            raise BenchmarkError(f"Invalid expected_output_range for {case_id}")
        archive_hash = str(raw["archive_sha256"]).lower()
        gold_hash = str(raw["gold_sha256"]).lower()
        if not re.fullmatch(r"[a-f0-9]{64}", archive_hash):
            raise BenchmarkError(f"Invalid archive_sha256 for {case_id}")
        if not re.fullmatch(r"[a-f0-9]{64}", gold_hash):
            raise BenchmarkError(f"Invalid gold_sha256 for {case_id}")
        raw_context_budget = raw.get("context_budget", defaults.get("context_budget", 6000))
        raw_output_tokens = raw.get("output_tokens", defaults.get("output_tokens", 1500))
        if type(raw_context_budget) is not int or type(raw_output_tokens) is not int:
            raise BenchmarkError(f"Token budgets must be integers for {case_id}")
        context_budget = raw_context_budget
        output_tokens = raw_output_tokens
        if context_budget != 6000:
            raise BenchmarkError(f"Primary benchmark context_budget must be 6000 for {case_id}")
        if output_tokens < 1:
            raise BenchmarkError(f"output_tokens must be positive for {case_id}")
        annotations = raw["annotations"]
        if not isinstance(annotations, dict):
            raise BenchmarkError(f"annotations must be a mapping for {case_id}")
        _require_fields(
            annotations,
            ("curator", "card_author", "auditor", "disagreements"),
            f"Annotations {case_id}",
        )
        cases.append(
            CaseManifest(
                id=case_id,
                project_id=str(raw["project_id"]),
                stages=stages,
                archive=_resolve_input_path(str(raw["archive"]), path),
                archive_sha256=archive_hash,
                archive_root=str(raw.get("archive_root") or ""),
                entry_point=str(raw["entry_point"]),
                bibliography_files=tuple(str(value) for value in raw["bibliography_files"]),
                target_selector=str(target["selector"]),
                target_heading=str(target["heading"]),
                target_content_hash=str(target["content_hash"]),
                task=str(raw["task"]),
                task_type=str(raw["task_type"]),
                context_budget=context_budget,
                output_tokens=output_tokens,
                expected_output_range=(expected_range[0], expected_range[1]),
                gold_sha256=gold_hash,
                required_ideas=tuple(dict(value) for value in raw["required_ideas"]),
                anchor_aliases={str(k): list(v) for k, v in raw["anchor_aliases"].items()},
                required_terminology=tuple(str(value) for value in raw["required_terminology"]),
                prohibited_claims=tuple(str(value) for value in raw["prohibited_claims"]),
                protected_literals=tuple(str(value) for value in raw["protected_literals"]),
                required_citation_keys=tuple(str(value) for value in raw["required_citation_keys"]),
                valid_citation_keys=tuple(str(value) for value in raw["valid_citation_keys"]),
                expected_source_spans=tuple(dict(value) for value in raw["expected_source_spans"]),
                annotations=annotations,
                raw=raw,
            )
        )
    return cases


def validate_zip_members(archive: Path) -> list[zipfile.ZipInfo]:
    """Reject absolute paths, traversal, drive paths, and links before extraction."""
    try:
        zip_file = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BenchmarkError(f"Invalid ZIP archive {archive}: {exc}") from exc
    with zip_file:
        members = zip_file.infolist()
        for info in members:
            normalized = info.filename.replace("\\", "/")
            path = PurePosixPath(normalized)
            unix_mode = info.external_attr >> 16
            if (
                not normalized
                or normalized.startswith("/")
                or re.match(r"^[A-Za-z]:", normalized)
                or ".." in path.parts
                or stat.S_ISLNK(unix_mode)
            ):
                raise BenchmarkError(f"Unsafe ZIP member in {archive.name}: {info.filename!r}")
        return members


def safe_extract_zip(archive: Path, destination: Path) -> None:
    """Extract a validated ZIP without using ZipFile.extract()."""
    members = validate_zip_members(archive)
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zip_file:
        for info in members:
            relative = PurePosixPath(info.filename.replace("\\", "/"))
            output = destination.joinpath(*relative.parts)
            resolved = output.resolve()
            if root != resolved and root not in resolved.parents:
                raise BenchmarkError(f"ZIP member escaped destination: {info.filename!r}")
            if info.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with zip_file.open(info) as source, output.open("wb") as target:
                shutil.copyfileobj(source, target)


def _balanced_group_end(text: str, start: int, opening: str, closing: str) -> int:
    if start >= len(text) or text[start] != opening:
        raise BenchmarkError(f"Expected {opening!r} while parsing target heading")
    depth = 0
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index + 1
    raise BenchmarkError(f"Unbalanced {opening}{closing} group in target heading")


def heading_and_label_prefix(section_text: str) -> str:
    """Return the leading LaTeX heading and optional immediately following label."""
    match = re.match(
        r"\s*\\(?:part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?",
        section_text,
    )
    if not match:
        raise BenchmarkError("Target section does not begin with a supported LaTeX heading")
    cursor = match.end()
    if cursor < len(section_text) and section_text[cursor] == "[":
        cursor = _balanced_group_end(section_text, cursor, "[", "]")
    cursor = _balanced_group_end(section_text, cursor, "{", "}")
    whitespace_end = cursor
    while whitespace_end < len(section_text) and section_text[whitespace_end].isspace():
        whitespace_end += 1
    label_match = re.match(r"\\label\s*", section_text[whitespace_end:])
    if label_match:
        label_start = whitespace_end + label_match.end()
        cursor = _balanced_group_end(section_text, label_start, "{", "}")
    return section_text[:cursor]


@dataclass(frozen=True)
class BenchmarkTarget:
    node_id: str
    title: str
    selector: str
    source_path: str
    line_start: int
    line_end: int
    char_start: int
    char_end: int
    content_hash: str
    labels: tuple[str, ...]


def _latex_headings(content: str) -> list[dict[str, Any]]:
    levels = {
        "part": 0,
        "chapter": 1,
        "section": 2,
        "subsection": 3,
        "subsubsection": 4,
        "paragraph": 5,
        "subparagraph": 6,
    }
    headings: list[dict[str, Any]] = []
    pattern = re.compile(
        r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?"
    )
    for match in pattern.finditer(content):
        cursor = match.end()
        if cursor < len(content) and content[cursor] == "[":
            cursor = _balanced_group_end(content, cursor, "[", "]")
        if cursor >= len(content) or content[cursor] != "{":
            continue
        title_end = _balanced_group_end(content, cursor, "{", "}")
        title = content[cursor + 1 : title_end - 1]
        headings.append(
            {
                "char_start": match.start(),
                "heading_end": title_end,
                "level": levels[match.group(1)],
                "title": title,
            }
        )
    for index, heading in enumerate(headings):
        title = str(heading["title"])
        later = [
            candidate
            for candidate in headings[index + 1 :]
            if candidate["level"] <= heading["level"]
        ]
        heading["char_end"] = later[0]["char_start"] if later else len(content)
        section_text = content[heading["char_start"] : heading["char_end"]]
        prefix = heading_and_label_prefix(section_text)
        labels = re.findall(r"\\label\{([^{}]+)\}", prefix)
        heading["labels"] = labels
        heading["selector"] = labels[0] if labels else f"/{title}"
        heading["node_id"] = sanitize_node_id(title)
    return headings


def locate_benchmark_target(
    project: Path,
    entry_point: str,
    selector: str,
    heading: str,
) -> BenchmarkTarget:
    """Locate top-level or nested LaTeX targets within the virtual document file graph."""
    parser = VirtualDocumentParser(str(project))
    parser.parse(entry_point)
    candidates: list[BenchmarkTarget] = []
    normalized_selector = selector.casefold().strip()
    normalized_heading = heading.casefold().strip()
    for relative in sorted(parser.visited_files | {entry_point}):
        path = project / relative
        if not path.is_file() or path.suffix.lower() != ".tex":
            continue
        content = path.read_text(encoding="utf-8")
        for item in _latex_headings(content):
            title = str(item["title"])
            node_id = str(item["node_id"])
            item_selector = str(item["selector"])
            labels = tuple(str(value) for value in item["labels"])
            selector_matches = normalized_selector in {
                node_id.casefold(),
                item_selector.casefold(),
                title.casefold(),
            } or normalized_selector in {label.casefold() for label in labels}
            if not selector_matches or title.casefold().strip() != normalized_heading:
                continue
            char_start = int(item["char_start"])
            char_end = int(item["char_end"])
            section_text = content[char_start:char_end]
            candidates.append(
                BenchmarkTarget(
                    node_id=node_id,
                    title=title,
                    selector=item_selector,
                    source_path=relative,
                    line_start=content.count("\n", 0, char_start) + 1,
                    line_end=content.count("\n", 0, char_end) + 1,
                    char_start=char_start,
                    char_end=char_end,
                    content_hash=stable_hash(section_text),
                    labels=labels,
                )
            )
    if not candidates:
        raise BenchmarkError(f"Target {selector!r} with heading {heading!r} was not found")
    if len(candidates) > 1:
        paths = sorted({candidate.source_path for candidate in candidates})
        raise BenchmarkError(f"Ambiguous target {selector!r}; matches: {paths}")
    return candidates[0]


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[\w.-]+", text.casefold())


def audit_duplicate_prose(
    gold: str, workspace: Path, allowed_files: list[str], *, minimum_run: int = 51
) -> dict[str, Any]:
    """Report exact normalized runs longer than 50 tokens without deleting overlap."""
    gold_tokens = _word_tokens(gold)
    if len(gold_tokens) < minimum_run:
        return {"minimum_run_tokens": minimum_run, "matches": []}
    needles: dict[tuple[str, ...], int] = {}
    for index in range(len(gold_tokens) - minimum_run + 1):
        needles.setdefault(tuple(gold_tokens[index : index + minimum_run]), index)
    matches: list[dict[str, Any]] = []
    for relative in allowed_files:
        path = workspace / relative
        if not path.is_file() or path.suffix.lower() not in {".tex", ".md"}:
            continue
        tokens = _word_tokens(path.read_text(encoding="utf-8", errors="replace"))
        for index in range(len(tokens) - minimum_run + 1):
            needle = tuple(tokens[index : index + minimum_run])
            if needle in needles:
                matches.append(
                    {
                        "path": relative,
                        "gold_token_start": needles[needle],
                        "visible_token_start": index,
                        "run_hash": sha256_text(" ".join(needle)),
                    }
                )
                break
    return {"minimum_run_tokens": minimum_run, "matches": matches}


def _create_frozen_cards(
    workspace: Path,
    entry_point: str,
    target_selector: str,
    target_heading: str,
    task: str,
) -> tuple[Path, str]:
    parser = VirtualDocumentParser(str(workspace))
    parser.parse(entry_point)
    sections: dict[str, Any] = {}
    for node_id in parser.node_order:
        node = parser.nodes[node_id]
        sections[node_id] = {
            "title": node.title,
            "role": task if node_id == target_selector or node.selector == target_selector else "",
            "path": node.source_path,
            "key_terms": [],
            "depends_on": [],
            "must_preserve": list(node.labels),
            "avoid": [],
            "constraints": [],
        }
    target = locate_benchmark_target(workspace, entry_point, target_selector, target_heading)
    sections[target_selector] = {
        "title": target.title,
        "role": task,
        "path": target.source_path,
        "key_terms": [],
        "depends_on": [],
        "must_preserve": list(target.labels),
        "avoid": [],
        "constraints": [],
    }
    cards = {
        "version": 1,
        "document": {
            "title": "Anonymized benchmark manuscript",
            "thesis": "",
            "writing_style": {"tone": "academic, formal", "avoid": []},
            "terminology": {},
        },
        "sections": sections,
    }
    cards_path = workspace / ".writing-context" / "section_cards.yaml"
    cards_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(cards, sort_keys=False, allow_unicode=True)
    _atomic_text(cards_path, serialized, mode=0o444)
    return cards_path, sha256_text(serialized)


def _write_benchmark_config(workspace: Path, *, final_workspace: Path | None = None) -> None:
    configured_root = final_workspace or workspace
    config = {
        "version": 1,
        "rtfm": {
            "corpus": "manuscript",
            "project_root": str(configured_root),
            "sync_before_pack": False,
        },
        "context": {
            "default_token_budget": 6000,
            "max_token_budget": 6000,
            "include_source_excerpts": True,
            "output_mode": "structured",
            "enable_rrf": False,
        },
        "cache": {"enabled": False, "path": ".writing-context/context_cache.sqlite"},
        "section_cards": {"path": ".writing-context/section_cards.yaml", "required": True},
        "providers": {
            "bibtex": {"enabled": True},
            "zotero": {"enabled": False},
            "openai_semantic": {"enabled": False},
        },
    }
    path = workspace / ".writing-context" / "config.yaml"
    _atomic_text(path, yaml.safe_dump(config, sort_keys=False), mode=0o444)


class ExplicitFileIndexer(Protocol):
    def index_files(self, workspace: Path, files: list[str]) -> None:
        """Index only the validated manuscript and bibliography files."""


class RTFMExplicitFileIndexer:
    """Index explicit files synchronously without starting RTFM's global daemon."""

    def index_files(self, workspace: Path, files: list[str]) -> None:
        workspace = workspace.resolve()
        if importlib.util.find_spec("rtfm.core.sync") is None:
            raise BenchmarkError("RTFM Python library is unavailable")
        helper = Path(__file__).with_name("rtfm_sync_explicit.py")
        db_path = workspace / ".rtfm" / "library.db"
        result = subprocess.run(
            [
                sys.executable,
                str(helper),
                "--workspace",
                str(workspace),
                "--db",
                str(db_path),
                "--corpus",
                "manuscript",
                "--files",
                *files,
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise BenchmarkError(f"Synchronous RTFM indexing failed in {workspace}: {detail}")
        try:
            summary = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise BenchmarkError(f"Malformed synchronous RTFM result: {result.stdout!r}") from exc
        if int(summary.get("books", 0)) < 1 or int(summary.get("chunks", 0)) < 1:
            raise BenchmarkError(f"Synchronous RTFM index is empty in {workspace}: {summary}")


def _write_rtfm_source_config(workspace: Path, final_workspace: Path) -> None:
    """Record the final content-addressed root without registering a worker."""
    _atomic_json(
        workspace / ".rtfm" / "config.json",
        {"sources": [{"path": str(final_workspace.resolve()), "corpus": "manuscript"}]},
    )


def _locate_project_root(extracted: Path, archive_root: str) -> Path:
    project = extracted / archive_root if archive_root else extracted
    project = project.resolve()
    if not project.is_dir() or extracted.resolve() not in {project, *project.parents}:
        raise BenchmarkError(f"Invalid or missing archive_root: {archive_root!r}")
    return project


def prepare_case(
    case: CaseManifest,
    *,
    workspaces_root: Path,
    private_root: Path,
    indexer: ExplicitFileIndexer | None = None,
) -> dict[str, Any]:
    """Prepare one masked case while keeping gold outside the indexed workspace."""
    if not case.archive.is_file():
        raise BenchmarkError(f"Archive not found for {case.id}: {case.archive}")
    before_hash = file_sha256(case.archive)
    if before_hash != case.archive_sha256:
        raise BenchmarkError(
            f"Archive hash mismatch for {case.id}: expected {case.archive_sha256}, got {before_hash}"
        )
    validate_zip_members(case.archive)
    destination = workspaces_root / case.case_hash
    metadata_path = private_root / "prepared" / f"{case.case_hash}.json"
    if destination.is_dir() and metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise BenchmarkError(f"Prepared metadata must be an object: {metadata_path}")
        if (
            metadata.get("case_hash") == case.case_hash
            and metadata.get("archive_sha256") == before_hash
        ):
            if (
                metadata.get("index_command_version") != INDEX_COMMAND_VERSION
                and indexer is not None
            ):
                allowed_files = [str(value) for value in metadata.get("allowed_files", [])]
                indexer.index_files(destination, allowed_files)
                _write_rtfm_source_config(destination, destination)
                metadata["rtfm_fingerprint"] = compute_rtfm_fingerprint(
                    resolve_rtfm_db_path(destination)
                )
                metadata["index_command_version"] = INDEX_COMMAND_VERSION
                _atomic_json(metadata_path, metadata, mode=0o600)
            return metadata

    workspaces_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{case.id}-", dir=workspaces_root) as tmp_name:
        extracted = Path(tmp_name) / "extracted"
        safe_extract_zip(case.archive, extracted)
        project = _locate_project_root(extracted, case.archive_root)
        entry = project / case.entry_point
        if not entry.is_file():
            raise BenchmarkError(f"Entry point missing for {case.id}: {case.entry_point}")

        node = locate_benchmark_target(
            project,
            case.entry_point,
            case.target_selector,
            case.target_heading,
        )
        if node.title.strip() != case.target_heading.strip():
            raise BenchmarkError(
                f"Target heading mismatch for {case.id}: expected {case.target_heading!r}, got {node.title!r}"
            )
        if node.content_hash != case.target_content_hash:
            raise BenchmarkError(
                f"Target content hash mismatch for {case.id}: expected {case.target_content_hash}, got {node.content_hash}"
            )
        source_path = project / node.source_path
        source = source_path.read_text(encoding="utf-8")
        gold = source[node.char_start : node.char_end]
        actual_gold_hash = sha256_text(gold)
        if actual_gold_hash != case.gold_sha256:
            raise BenchmarkError(
                f"Gold hash mismatch for {case.id}: expected {case.gold_sha256}, got {actual_gold_hash}"
            )
        prefix = heading_and_label_prefix(gold)
        masked_section = f"{prefix}\n{MASK_TEMPLATE.format(case_id=case.id)}\n"
        source_path.write_text(
            source[: node.char_start] + masked_section + source[node.char_end :],
            encoding="utf-8",
        )

        gold_path = private_root / "gold" / f"{case.case_hash}.tex"
        _atomic_text(gold_path, gold, mode=0o600)

        masked_parser = VirtualDocumentParser(str(project))
        masked_parser.parse(case.entry_point)
        allowed_files = sorted(
            {
                *masked_parser.visited_files,
                case.entry_point,
                *case.bibliography_files,
            }
        )
        for relative in allowed_files:
            path = project / relative
            if not path.is_file():
                raise BenchmarkError(f"Allowlisted file missing for {case.id}: {relative}")
            if project != path.resolve() and project not in path.resolve().parents:
                raise BenchmarkError(
                    f"Allowlisted file escapes workspace for {case.id}: {relative}"
                )

        leakage = audit_duplicate_prose(gold, project, allowed_files)
        masked_target = locate_benchmark_target(
            project,
            case.entry_point,
            case.target_selector,
            case.target_heading,
        )
        cards_path, cards_hash = _create_frozen_cards(
            project,
            case.entry_point,
            case.target_selector,
            case.target_heading,
            case.task,
        )
        _write_benchmark_config(project, final_workspace=destination.resolve())
        card_text = cards_path.read_text(encoding="utf-8")
        if gold in card_text or sha256_text(card_text) == actual_gold_hash:
            raise BenchmarkError(f"Gold leaked into frozen cards for {case.id}")
        for relative in allowed_files:
            if gold in (project / relative).read_text(encoding="utf-8", errors="replace"):
                raise BenchmarkError(
                    f"Complete gold leaked into visible file {relative} for {case.id}"
                )

        staged_destination = Path(tmp_name) / "workspace"
        project.rename(staged_destination)
        if destination.exists():
            raise BenchmarkError(f"Prepared destination unexpectedly exists: {destination}")
        if indexer is not None:
            indexer.index_files(staged_destination, allowed_files)
            _write_rtfm_source_config(staged_destination, destination)
        staged_destination.rename(destination)
    rtfm_path = resolve_rtfm_db_path(destination)
    rtfm_fingerprint = compute_rtfm_fingerprint(rtfm_path)
    after_hash = file_sha256(case.archive)
    if after_hash != before_hash:
        raise BenchmarkError(f"Source archive changed during preparation for {case.id}")

    metadata = {
        "artifact_version": ARTIFACT_VERSION,
        "case_id": case.id,
        "project_id": case.project_id,
        "case_hash": case.case_hash,
        "archive_sha256": before_hash,
        "entry_point": case.entry_point,
        "target_selector": case.target_selector,
        "target_source_path": node.source_path,
        "target_line_start": masked_target.line_start,
        "target_line_end": masked_target.line_end,
        "masked_target_hash": sha256_text(masked_section),
        "gold_sha256": actual_gold_hash,
        "gold_path": str(gold_path.resolve()),
        "workspace": str(destination.resolve()),
        "allowed_files": allowed_files,
        "cards_hash": cards_hash,
        "cards_provenance": "masked_workspace_and_task_only",
        "cards_frozen": True,
        "rtfm_fingerprint": rtfm_fingerprint,
        "index_scope": "explicit_files_only",
        "index_command_version": INDEX_COMMAND_VERSION,
        "leakage_audit": leakage,
        "annotations_resolved": case.annotations_resolved,
    }
    _atomic_json(metadata_path, metadata, mode=0o600)
    return metadata


def current_code_revision() -> str:
    """Fingerprint Git HEAD and all possibly-dirty package Python sources."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "unknown"
    package_root = Path(__file__).parent
    implementation = {
        path.relative_to(package_root).as_posix(): file_sha256(path)
        for path in sorted(package_root.rglob("*.py"))
    }
    implementation_hash = sha256_text(canonical_json(implementation))
    return f"{head}+package.{implementation_hash[:16]}"


@dataclass(frozen=True)
class ArtifactKey:
    kind: str
    case_hash: str
    strategy: str
    repetition: int
    model: str
    code_revision: str
    prompt_version: str
    rtfm_fingerprint: str
    cards_hash: str
    retrieval_policy_version: str
    parent_hash: str = ""

    @property
    def digest(self) -> str:
        return sha256_text(canonical_json(asdict(self)))


class ArtifactStore:
    """Atomic JSON artifacts that resume only on an exact key match."""

    def __init__(self, root: Path):
        self.root = root

    def path_for(self, key: ArtifactKey) -> Path:
        return self.root / key.kind / f"{key.digest}.json"

    def load(self, key: ArtifactKey) -> dict[str, Any] | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BenchmarkError(f"Malformed artifact {path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise BenchmarkError(f"Artifact must be a JSON object: {path}")
        artifact: dict[str, Any] = loaded
        if artifact.get("key") != asdict(key) or artifact.get("key_digest") != key.digest:
            raise BenchmarkError(f"Artifact key mismatch: {path}")
        return artifact

    def save(self, key: ArtifactKey, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.load(key)
        if existing is not None:
            return existing
        artifact = {
            "artifact_version": ARTIFACT_VERSION,
            "key": asdict(key),
            "key_digest": key.digest,
            "payload": payload,
        }
        _atomic_json(self.path_for(key), artifact, mode=0o600)
        return artifact

    def iter_kind(self, kind: str) -> list[dict[str, Any]]:
        directory = self.root / kind
        if not directory.is_dir():
            return []
        artifacts: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BenchmarkError(f"Malformed artifact {path}: {exc}") from exc
            if not isinstance(loaded, dict):
                raise BenchmarkError(f"Artifact must be a JSON object: {path}")
            artifacts.append(loaded)
        return artifacts


def load_prepared(case: CaseManifest, private_root: Path) -> dict[str, Any]:
    path = private_root / "prepared" / f"{case.case_hash}.json"
    if not path.is_file():
        raise BenchmarkError(f"Case {case.id} has not been prepared: {path}")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise BenchmarkError(f"Prepared metadata must be an object: {path}")
    if metadata.get("case_hash") != case.case_hash:
        raise BenchmarkError(f"Prepared metadata does not match case {case.id}")
    return metadata


def _evidence_span(
    *,
    path: str,
    text: str,
    line_start: int | None,
    line_end: int | None,
    score: float,
    rank: int,
) -> dict[str, Any]:
    identity = {
        "path": path.replace("\\", "/"),
        "line_start": line_start,
        "line_end": line_end,
        "text_sha256": sha256_text(text),
    }
    return {
        "id": f"S{rank:03d}-{sha256_text(canonical_json(identity))[:10]}",
        **identity,
        "text": text,
        "score": round(float(score), 6),
        "tokens": estimate_tokens(text),
    }


def _within_budget(spans: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used = 0
    for span in spans:
        tokens = int(span["tokens"])
        if used + tokens > budget:
            continue
        selected.append(span)
        used += tokens
    return selected


def _diagnostic_span(span: SourceSpan) -> dict[str, Any]:
    metadata = span.metadata or {}
    return {
        "path": span.path,
        "line_start": span.line_start,
        "line_end": span.line_end,
        "score": round(float(span.score), 6),
        "retrieval_score": span.retrieval_score,
        "fusion_score": span.fusion_score,
        "structural_score": span.structural_score,
        "priority": span.priority,
        "source_role": span.source_role,
        "query": span.query,
        "provider_id": metadata.get("provider_id"),
        "retrieval_rank": metadata.get("retrieval_rank"),
        "reason": span.reason,
        "tokens": estimate_tokens(str(metadata.get("snippet") or "")),
    }


class _PackDiagnosticRecorder:
    def __init__(self) -> None:
        self.trace: dict[str, list[dict[str, Any]]] = {}
        self.streams: dict[str, list[dict[str, Any]]] = {}
        self.rejections: dict[str, list[dict[str, Any]]] = {}
        self.elapsed_ms: dict[str, float] = {}
        self.started_at = time.perf_counter()

    def __call__(self, stage: str, spans: Sequence[SourceSpan]) -> None:
        self.elapsed_ms[stage] = round((time.perf_counter() - self.started_at) * 1000, 3)
        snapshot = [_diagnostic_span(span) for span in spans]
        if stage.startswith("stream:"):
            self.streams[stage.removeprefix("stream:")] = snapshot
        elif stage.startswith("rejected:"):
            self.rejections[stage.removeprefix("rejected:")] = snapshot
        else:
            self.trace[stage] = snapshot


class RetrievalBackend(Protocol):
    def retrieve(
        self, case: CaseManifest, prepared: dict[str, Any], strategy: str
    ) -> dict[str, Any]:
        """Return a private evidence packet for one strategy."""


class ProductionRetrievalBackend:
    """Local RTFM retrieval with only the native BibTeX provider enabled."""

    def _full_visible(self, case: CaseManifest, prepared: dict[str, Any]) -> dict[str, Any]:
        workspace = Path(prepared["workspace"])
        spans = []
        for rank, relative in enumerate(prepared["allowed_files"], start=1):
            text = (workspace / relative).read_text(encoding="utf-8", errors="replace")
            spans.append(
                _evidence_span(
                    path=relative,
                    text=text,
                    line_start=1,
                    line_end=max(1, text.count("\n") + 1),
                    score=1.0,
                    rank=rank,
                )
            )
        return {
            "spans": spans,
            "candidate_spans": spans,
            "diagnostic_trace": {"retrieved": spans, "selected": spans},
            "pack_metadata": {},
            "budget": None,
        }

    def _topk(self, case: CaseManifest, prepared: dict[str, Any]) -> dict[str, Any]:
        adapter = RTFMAdapter(project_root=prepared["workspace"], allow_cli_fallback=False)
        results = adapter.search(case.task, corpus="manuscript", limit=100)
        spans = []
        for rank, result in enumerate(results, start=1):
            if not result.snippet:
                continue
            spans.append(
                _evidence_span(
                    path=result.path,
                    text=result.snippet,
                    line_start=result.line_start,
                    line_end=result.line_end,
                    score=result.score or 0.0,
                    rank=rank,
                )
            )
        selected = _within_budget(spans, case.context_budget)
        selected_ids = {id(span) for span in selected}
        rejected = [span for span in spans if id(span) not in selected_ids]
        return {
            "spans": selected,
            "candidate_spans": spans,
            "diagnostic_trace": {"retrieved": spans, "selected": selected},
            "selection_rejections": {"token_budget": rejected} if rejected else {},
            "pack_metadata": {},
            "budget": case.context_budget,
        }

    def _pack(
        self,
        case: CaseManifest,
        prepared: dict[str, Any],
        *,
        enable_rrf: bool,
        query_stream_retriever: Any | None = None,
        bibliography_handoff: Any | None = None,
    ) -> dict[str, Any]:
        workspace = Path(prepared["workspace"])
        base_config = load_config(str(workspace))
        config: AppConfig = replace(
            base_config,
            context=replace(base_config.context, enable_rrf=enable_rrf),
            cache=replace(base_config.cache, enabled=False),
        )
        cards = load_section_cards(config.section_cards.path, required=True)
        adapter = RTFMAdapter(project_root=str(workspace), allow_cli_fallback=False)
        providers = [
            provider
            for provider in get_active_providers(config)
            if provider.provider_id == "bibtex"
        ]
        diagnostics = _PackDiagnosticRecorder()
        with ExtensionStore(":memory:") as store:
            store.init_db()
            generator = ContextPackGenerator(
                config,
                cards,
                adapter,
                store,
                providers=providers,
                diagnostic_recorder=diagnostics,
                query_stream_retriever=query_stream_retriever,
                bibliography_handoff=bibliography_handoff,
            )
            pack: ContextPack = generator.generate(
                task=case.task,
                target=case.target_selector,
                token_budget=case.context_budget,
                project_root=str(workspace),
                task_type=case.task_type,
                line_start=int(prepared["target_line_start"]),
                line_end=int(prepared["target_line_end"]),
                pack_mode="standard",
                strict_budget=True,
                output_mode="structured",
            )
        spans = []
        for rank, source in enumerate(pack.source_spans, start=1):
            text = str((source.metadata or {}).get("snippet") or "")
            if not text:
                continue
            spans.append(
                _evidence_span(
                    path=source.path,
                    text=text,
                    line_start=source.line_start,
                    line_end=source.line_end,
                    score=source.score,
                    rank=rank,
                )
            )
        spans = _within_budget(spans, case.context_budget)
        metadata = {
            "document_thesis": pack.document_thesis,
            "constraints": pack.constraints,
            "terminology": pack.terminology,
            "prior_claims": pack.prior_claims,
            "status": pack.status,
            "warnings": pack.warnings,
        }
        packet = {
            "spans": spans,
            "candidate_spans": diagnostics.trace.get(
                "deduplicated", diagnostics.trace.get("retrieved", [])
            ),
            "diagnostic_trace": diagnostics.trace,
            "candidate_streams": diagnostics.streams,
            "selection_rejections": diagnostics.rejections,
            "pack_metadata": metadata,
            "budget": case.context_budget,
        }
        if query_stream_retriever is not None:
            telemetry = dict(getattr(query_stream_retriever, "telemetry", {}))
            retrieved_elapsed = diagnostics.elapsed_ms.get("retrieved", 0.0)
            selected_elapsed = diagnostics.elapsed_ms.get("selected", retrieved_elapsed)
            query_latency = float(telemetry.get("retrieval_latency_ms", 0.0))
            packet["exposure_telemetry"] = telemetry
            packet["phase_latency_ms"] = {
                "retrieval": round(query_latency, 3),
                "fusion": round(max(0.0, retrieved_elapsed - query_latency), 3),
                "composer": round(max(0.0, selected_elapsed - retrieved_elapsed), 3),
            }
        return packet

    def retrieve_exposure(
        self,
        case: CaseManifest,
        prepared: dict[str, Any],
        query_stream_retriever: Any,
        bibliography_handoff: Any | None = None,
    ) -> dict[str, Any]:
        """Run a benchmark-only exposure policy with the production composer unchanged."""
        started = time.perf_counter()
        packet = self._pack(
            case,
            prepared,
            enable_rrf=False,
            query_stream_retriever=query_stream_retriever,
            bibliography_handoff=bibliography_handoff,
        )
        packet["strategy"] = str(getattr(query_stream_retriever, "name", "exposure"))
        packet["context_tokens"] = sum(int(span["tokens"]) for span in packet["spans"])
        packet["retrieval_latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if packet["context_tokens"] > case.context_budget:
            raise BenchmarkError(f"Context budget exceeded for {case.id}/{packet['strategy']}")
        return packet

    def retrieve(
        self, case: CaseManifest, prepared: dict[str, Any], strategy: str
    ) -> dict[str, Any]:
        if strategy not in STRATEGIES:
            raise BenchmarkError(f"Unknown strategy: {strategy}")
        started = time.perf_counter()
        if strategy == "full_visible":
            packet = self._full_visible(case, prepared)
        elif strategy == "rtfm_topk":
            packet = self._topk(case, prepared)
        else:
            packet = self._pack(case, prepared, enable_rrf=strategy == "pack_rrf")
        packet["strategy"] = strategy
        packet["context_tokens"] = sum(int(span["tokens"]) for span in packet["spans"])
        packet["retrieval_latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if packet["budget"] is not None and packet["context_tokens"] > packet["budget"]:
            raise BenchmarkError(f"Context budget exceeded for {case.id}/{strategy}")
        return packet


SYSTEM_INSTRUCTION = (
    "You are an academic writing assistant. Write only the requested manuscript section using "
    "the supplied evidence. Do not invent evidence, citations, results, labels, or numerical values."
)


def render_generation_prompt(case: CaseManifest, evidence: dict[str, Any]) -> str:
    """Render the sole benchmark-owned generation template for every strategy."""
    lines = [
        f"SYSTEM\n{SYSTEM_INSTRUCTION}",
        f"TASK\n{case.task}",
        f"TARGET\n{case.target_selector}",
        (
            "OUTPUT RANGE\n"
            f"{case.expected_output_range[0]}-{case.expected_output_range[1]} words; "
            f"hard ceiling {case.output_tokens} tokens"
        ),
    ]
    pack_metadata = evidence.get("pack_metadata") or {}
    if pack_metadata:
        lines.append("PACK GUIDANCE\n" + canonical_json(pack_metadata))
    span_blocks = []
    for span in evidence.get("spans", []):
        location = f"{span['path']}:{span.get('line_start')}-{span.get('line_end')}"
        span_blocks.append(f"[{span['id']}] {location}\n{span['text']}")
    lines.append("EVIDENCE\n" + "\n\n".join(span_blocks))
    lines.append("OUTPUT\nReturn only the manuscript section body.")
    return "\n\n".join(lines)


def prompt_invariant_sections(prompt: str) -> dict[str, str]:
    """Expose condition-independent prompt sections for equivalence tests."""
    parts = re.split(r"\n\n(?=[A-Z][A-Z ]+\n)", prompt)
    result: dict[str, str] = {}
    for part in parts:
        heading = part.split("\n", 1)[0]
        if heading in {"SYSTEM", "TASK", "TARGET", "OUTPUT RANGE", "OUTPUT"}:
            result[heading] = part
    return result


def _span_relevance(span: dict[str, Any], expected: tuple[dict[str, Any], ...]) -> int:
    path = str(span["path"]).replace("\\", "/")
    start = span.get("line_start")
    end = span.get("line_end")
    best = 0
    for source in expected:
        expected_path = str(source.get("path", "")).replace("\\", "/")
        if not (path == expected_path or path.endswith("/" + expected_path)):
            continue
        source_start = source.get("line_start")
        source_end = source.get("line_end")
        overlaps = (
            start is None
            or end is None
            or source_start is None
            or source_end is None
            or not (end < source_start or start > source_end)
        )
        if overlaps:
            best = max(best, int(source.get("grade", 1)))
    return best


def _duplicate_context_ratio(spans: list[dict[str, Any]]) -> float:
    seen: set[tuple[str, ...]] = set()
    duplicate = 0
    total = 0
    for span in spans:
        tokens = _word_tokens(str(span.get("text", "")))
        shingles = [tuple(tokens[i : i + 5]) for i in range(max(0, len(tokens) - 4))]
        for shingle in shingles:
            total += 1
            if shingle in seen:
                duplicate += 1
            seen.add(shingle)
    return round(duplicate / total, 4) if total else 0.0


_CANDIDATE_RECALL_CUTOFFS = (1, 3, 5, 10, 25, 50, 100)
_PACK_DIAGNOSTIC_STAGES = (
    "retrieved",
    "deduplicated",
    "score_filtered",
    "diversified",
    "budget_candidates",
    "selected",
)


def _covered_expected_sources(
    spans: list[dict[str, Any]], expected: tuple[dict[str, Any], ...]
) -> set[int]:
    return {
        index
        for index, source in enumerate(expected)
        if any(_span_relevance(span, (source,)) > 0 for span in spans)
    }


def _source_first_ranks(
    spans: list[dict[str, Any]], expected: tuple[dict[str, Any], ...]
) -> list[int | None]:
    return [
        next(
            (
                rank
                for rank, span in enumerate(spans, start=1)
                if _span_relevance(span, (source,)) > 0
            ),
            None,
        )
        for source in expected
    ]


def _candidate_diagnostics(
    evidence: dict[str, Any], expected: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    diagnostics_available = "candidate_spans" in evidence and "diagnostic_trace" in evidence
    selected = list(evidence.get("spans", []))
    candidates = list(evidence.get("candidate_spans", selected))
    trace_value = evidence.get("diagnostic_trace") or {}
    trace = {
        stage: list(trace_value[stage]) for stage in _PACK_DIAGNOSTIC_STAGES if stage in trace_value
    }
    if "retrieved" not in trace:
        trace["retrieved"] = candidates
    if "selected" not in trace:
        trace["selected"] = selected

    relevant_expected = len(expected)
    candidate_metrics = {
        f"candidate_recall_at_{cutoff}": round(
            len(_covered_expected_sources(candidates[:cutoff], expected)) / relevant_expected,
            4,
        )
        if relevant_expected
        else 1.0
        for cutoff in _CANDIDATE_RECALL_CUTOFFS
    }
    first_ranks = _source_first_ranks(candidates, expected)
    selected_covered = _covered_expected_sources(selected, expected)
    candidate_covered = _covered_expected_sources(candidates, expected)
    stage_coverage = {
        stage: _covered_expected_sources(spans, expected) for stage, spans in trace.items()
    }
    rejection_coverage = {
        reason: _covered_expected_sources(list(spans), expected)
        for reason, spans in (evidence.get("selection_rejections") or {}).items()
    }
    ordered_stages = [stage for stage in _PACK_DIAGNOSTIC_STAGES if stage in trace]
    loss_stage_counts: dict[str, int] = defaultdict(int)
    selection_loss_reason_counts: dict[str, int] = defaultdict(int)
    outcomes: list[dict[str, Any]] = []
    for source_index, first_rank in enumerate(first_ranks):
        present_stages = [
            stage for stage in ordered_stages if source_index in stage_coverage[stage]
        ]
        last_present = present_stages[-1] if present_stages else None
        if last_present is None:
            loss_stage_counts["retrieved"] += 1
        elif source_index not in selected_covered:
            last_index = ordered_stages.index(last_present)
            loss_stage = (
                ordered_stages[last_index + 1]
                if last_index + 1 < len(ordered_stages)
                else "selected"
            )
            loss_stage_counts[loss_stage] += 1
        selection_loss_reason = (
            next(
                (
                    reason
                    for reason in sorted(rejection_coverage)
                    if source_index in rejection_coverage[reason]
                ),
                None,
            )
            if source_index not in selected_covered
            else None
        )
        if selection_loss_reason is not None:
            selection_loss_reason_counts[selection_loss_reason] += 1
        outcomes.append(
            {
                "source_index": source_index,
                "first_candidate_rank": first_rank,
                "selected": source_index in selected_covered,
                "lost_after": None if source_index in selected_covered else last_present,
                "selection_loss_reason": selection_loss_reason,
            }
        )

    candidate_recall = len(candidate_covered) / relevant_expected if relevant_expected else 1.0
    selected_recall = len(selected_covered) / relevant_expected if relevant_expected else 1.0
    return {
        **candidate_metrics,
        "candidate_diagnostics_available": diagnostics_available,
        "expected_source_first_ranks": first_ranks,
        "candidate_to_selected_recall_delta": round(selected_recall - candidate_recall, 4),
        "diagnostic_stage_recall": {
            stage: round(len(covered) / relevant_expected, 4) if relevant_expected else 1.0
            for stage, covered in stage_coverage.items()
        },
        "loss_stage_counts": dict(loss_stage_counts),
        "selection_loss_reason_counts": dict(selection_loss_reason_counts),
        "expected_source_outcomes": outcomes,
    }


def retrieval_metrics(case: CaseManifest, evidence: dict[str, Any]) -> dict[str, Any]:
    spans = evidence.get("spans", [])
    grades = [_span_relevance(span, case.expected_source_spans) for span in spans]
    relevant_expected = len(case.expected_source_spans)
    relevant_retrieved = sum(grade > 0 for grade in grades)
    precision = relevant_retrieved / len(spans) if spans else 0.0
    covered_expected: set[int] = set()
    credited_expected: set[int] = set()
    unique_grades: list[int] = []
    for span in spans:
        matching = [
            index
            for index, source in enumerate(case.expected_source_spans)
            if _span_relevance(span, (source,)) > 0
        ]
        covered_expected.update(matching)
        uncredited = [index for index in matching if index not in credited_expected]
        if not uncredited:
            unique_grades.append(0)
            continue
        selected = max(
            uncredited,
            key=lambda index: int(case.expected_source_spans[index].get("grade", 1)),
        )
        credited_expected.add(selected)
        unique_grades.append(int(case.expected_source_spans[selected].get("grade", 1)))
    recall = len(covered_expected) / relevant_expected if relevant_expected else 1.0
    first = next((index for index, grade in enumerate(grades, start=1) if grade > 0), None)
    dcg = sum(grade / math.log2(index + 2) for index, grade in enumerate(unique_grades))
    ideal_grades = sorted(
        (int(source.get("grade", 1)) for source in case.expected_source_spans), reverse=True
    )[: len(grades)]
    idcg = sum(grade / math.log2(index + 2) for index, grade in enumerate(ideal_grades))
    expected_obligations = {
        str(obligation)
        for source in case.expected_source_spans
        for obligation in source.get("obligations", [])
    }
    covered_obligations: set[str] = set()
    for span, grade in zip(spans, grades, strict=True):
        if grade <= 0:
            continue
        for source in case.expected_source_spans:
            if _span_relevance(span, (source,)) > 0:
                covered_obligations.update(str(value) for value in source.get("obligations", []))
    return {
        "graded_source_recall": round(recall, 4),
        "graded_source_precision": round(precision, 4),
        "mrr": round(1.0 / first, 4) if first else 0.0,
        "ndcg": round(dcg / idcg, 4) if idcg else 0.0,
        "irrelevant_source_rate": round(1.0 - precision, 4) if spans else 0.0,
        "duplicate_context_ratio": _duplicate_context_ratio(spans),
        "obligation_coverage": (
            round(len(covered_obligations) / len(expected_obligations), 4)
            if expected_obligations
            else 1.0
        ),
        "context_tokens": int(evidence.get("context_tokens", 0)),
        "retrieval_latency_ms": float(evidence.get("retrieval_latency_ms", 0.0)),
        "annotation_resolved": case.annotations_resolved,
        **_candidate_diagnostics(evidence, case.expected_source_spans),
    }


def _strip_tex_comments(text: str) -> str:
    """Remove unescaped TeX comments while preserving line coordinates."""
    stripped: list[str] = []
    for line in text.splitlines(keepends=True):
        comment_at: int | None = None
        for index, char in enumerate(line):
            if char != "%":
                continue
            preceding = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                preceding += 1
                cursor -= 1
            if preceding % 2 == 0:
                comment_at = index
                break
        if comment_at is None:
            stripped.append(line)
        else:
            newline = "\n" if line.endswith("\n") else ""
            stripped.append(line[:comment_at] + newline)
    return "".join(stripped)


def parse_citation_keys(text: str, *, source_format: str | None = None) -> set[str]:
    """Extract LaTeX and, where applicable, Pandoc citation keys."""
    if source_format == "tex":
        text = _strip_tex_comments(text)
    keys: set[str] = set()
    for match in re.finditer(r"\\(?:[A-Za-z]*cite[A-Za-z*]*)(?:\[[^\]]*\])*\{([^{}]+)\}", text):
        keys.update(key.strip() for key in match.group(1).split(",") if key.strip())
    if source_format != "tex":
        for match in re.finditer(r"(?<!\w)@([A-Za-z0-9_:.+/-]+)", text):
            keys.add(match.group(1))
    return keys


_BIBTEX_HEADER = re.compile(r"(?im)^\s*@(?P<type>[a-z]+)\s*[({]\s*(?P<key>[^,\s]+)\s*,")


def bibliography_key_inventory(paths: list[Path]) -> dict[str, Any]:
    """Inventory authoritative BibTeX keys and parser/duplicate discrepancies."""
    raw_keys: list[str] = []
    parsed_keys: set[str] = set()
    for path in paths:
        content = path.read_text(encoding="utf-8", errors="replace")
        raw_keys.extend(
            match.group("key").strip()
            for match in _BIBTEX_HEADER.finditer(content)
            if match.group("type").casefold() not in {"comment", "preamble", "string"}
        )
        parsed_keys.update(parse_bibtex_file(path))
    counts: dict[str, int] = defaultdict(int)
    for key in raw_keys:
        counts[key] += 1
    authoritative = set(raw_keys)
    return {
        "keys": sorted(authoritative),
        "key_count": len(authoritative),
        "duplicate_keys": sorted(key for key, count in counts.items() if count > 1),
        "unparsed_keys": sorted(authoritative - parsed_keys),
        "parser_only_keys": sorted(parsed_keys - authoritative),
    }


def remap_source_spans_after_mask(
    spans: tuple[dict[str, Any], ...],
    *,
    target_path: str,
    target_line_start: int,
    gold_text: str,
    masked_target_line_end: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Map original source labels to the line coordinates of the masked workspace."""
    original_target_end = target_line_start + gold_text.count("\n")
    removed_lines = original_target_end - masked_target_line_end
    corrected: list[dict[str, Any]] = []
    issues: list[str] = []
    normalized_target = target_path.replace("\\", "/")
    for index, span in enumerate(spans):
        updated = dict(span)
        path = str(updated.get("path", "")).replace("\\", "/")
        start = updated.get("line_start")
        end = updated.get("line_end")
        if path == normalized_target and isinstance(start, int) and isinstance(end, int):
            if end < target_line_start:
                pass
            elif start >= original_target_end:
                updated["line_start"] = start - removed_lines
                updated["line_end"] = end - removed_lines
            else:
                issues.append(f"source_span_{index}_overlaps_masked_target")
        corrected.append(updated)
    return corrected, issues


def audit_case_annotations(
    cases: list[CaseManifest],
    *,
    private_root: Path,
    artifacts: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Audit private rubrics, citations, spans, cards, and leakage without emitting prose."""
    auditor_reviews: dict[str, dict[str, Any]] = {}
    reviews_root = private_root / "annotation-reviews"
    for review_path in sorted(reviews_root.glob("auditor-*.json")):
        try:
            review_document = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BenchmarkError(f"Cannot read auditor review {review_path}: {exc}") from exc
        if (
            not isinstance(review_document, dict)
            or review_document.get("role") != "auditor"
            or not isinstance(review_document.get("cases"), list)
        ):
            raise BenchmarkError(f"Malformed auditor review: {review_path}")
        for review_value in review_document["cases"]:
            if not isinstance(review_value, dict):
                raise BenchmarkError(f"Malformed case review in {review_path}")
            case_id = str(review_value.get("case_id", ""))
            if not case_id or case_id in auditor_reviews:
                raise BenchmarkError(f"Missing or duplicate auditor case review: {case_id!r}")
            decision = review_value.get("decision")
            review_issues = review_value.get("issues")
            if decision not in {"approved", "needs_revision"} or not isinstance(
                review_issues, list
            ):
                raise BenchmarkError(f"Invalid auditor decision for {case_id}")
            auditor_reviews[case_id] = {
                "decision": decision,
                "issues": [str(value) for value in review_issues],
            }

    project_inventories: dict[str, dict[str, Any]] = {}
    case_results: dict[str, dict[str, Any]] = {}
    for case in cases:
        prepared = load_prepared(case, private_root)
        workspace = Path(prepared["workspace"])
        bibliography_paths = [workspace / relative for relative in case.bibliography_files]
        inventory = project_inventories.setdefault(
            case.project_id,
            bibliography_key_inventory(bibliography_paths),
        )
        valid_keys = {str(value) for value in inventory["keys"]}
        issues: list[str] = []
        if inventory["duplicate_keys"]:
            issues.append("duplicate_bibliography_keys")
        if inventory["unparsed_keys"] or inventory["parser_only_keys"]:
            issues.append("bibtex_parser_key_mismatch")

        gold = Path(prepared["gold_path"]).read_text(encoding="utf-8")
        visible_citations: set[str] = set()
        for relative in prepared["allowed_files"]:
            path = workspace / relative
            if path.suffix.lower() in {".tex", ".md"}:
                visible_citations.update(
                    parse_citation_keys(
                        path.read_text(encoding="utf-8"),
                        source_format=path.suffix.lower().lstrip("."),
                    )
                )
        target_format = Path(str(prepared["target_source_path"])).suffix.lower().lstrip(".")
        manuscript_citations = visible_citations | parse_citation_keys(
            gold,
            source_format=target_format or None,
        )
        missing_manuscript = sorted(manuscript_citations - valid_keys)
        missing_required = sorted(set(case.required_citation_keys) - valid_keys)
        if missing_manuscript:
            issues.append("manuscript_citation_missing_from_bibliography")
        if missing_required:
            issues.append("required_citation_missing_from_bibliography")
        if set(case.valid_citation_keys) != valid_keys:
            issues.append("declared_valid_keys_incomplete")

        corrected_spans = [dict(span) for span in case.expected_source_spans]
        idea_ids = [str(idea.get("id", "")) for idea in case.required_ideas]
        if len(idea_ids) != len(set(idea_ids)) or any(not value for value in idea_ids):
            issues.append("required_idea_ids_not_unique")
        if any(not idea.get("anchors") for idea in case.required_ideas):
            issues.append("required_idea_without_anchors")
        idea_gold_presence: dict[str, dict[str, bool]] = {}
        for idea in case.required_ideas:
            idea_id = str(idea.get("id", ""))
            candidates: set[str] = set()
            for anchor in idea.get("anchors", []):
                anchor_text = str(anchor)
                candidates.add(anchor_text)
                candidates.update(str(value) for value in case.anchor_aliases.get(anchor_text, []))
            idea_gold_presence[idea_id] = {
                candidate: _anchor_hit(gold, candidate) for candidate in sorted(candidates)
            }
        term_gold_presence = {term: _anchor_hit(gold, term) for term in case.required_terminology}
        protected_gold_presence = {literal: literal in gold for literal in case.protected_literals}
        gold_citations = parse_citation_keys(gold, source_format=target_format or None)
        required_citation_gold_presence = {
            key: key in gold_citations for key in case.required_citation_keys
        }
        obligation_ids = {
            str(value) for span in corrected_spans for value in span.get("obligations", [])
        }
        if not obligation_ids <= set(idea_ids):
            issues.append("unknown_source_span_obligation")
        source_obligation_presence: list[dict[str, Any]] = []
        for index, span in enumerate(corrected_spans):
            source = workspace / str(span.get("path", ""))
            if not source.is_file():
                issues.append(f"source_span_{index}_path_missing")
                continue
            source_lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
            line_count = len(source_lines)
            start = span.get("line_start")
            end = span.get("line_end")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 1
                or end < start
                or end > line_count
            ):
                issues.append(f"source_span_{index}_bounds_invalid")
                continue
            span_text = "\n".join(source_lines[start - 1 : end])
            obligation_presence: dict[str, bool] = {}
            for obligation in span.get("obligations", []):
                obligation_id = str(obligation)
                obligation_idea: dict[str, Any] | None = None
                for value in case.required_ideas:
                    if value.get("id") == obligation_id:
                        obligation_idea = value
                        break
                anchor_candidates = (
                    []
                    if obligation_idea is None
                    else [str(value) for value in obligation_idea.get("anchors", [])]
                )
                expanded = {
                    candidate
                    for anchor in anchor_candidates
                    for candidate in (anchor, *case.anchor_aliases.get(anchor, []))
                }
                obligation_presence[obligation_id] = any(
                    _anchor_hit(span_text, candidate) for candidate in expanded
                )
            source_obligation_presence.append(
                {"span_index": index, "obligations": obligation_presence}
            )

        cards_path = workspace / ".writing-context" / "section_cards.yaml"
        cards_text = cards_path.read_text(encoding="utf-8", errors="replace")
        leakage_files = [
            relative
            for relative in prepared["allowed_files"]
            if gold in (workspace / relative).read_text(encoding="utf-8", errors="replace")
        ]
        if gold in cards_text:
            issues.append("gold_in_cards")
        if leakage_files:
            issues.append("gold_in_visible_workspace")
        if prepared.get("leakage_audit", {}).get("matches"):
            issues.append("long_duplicate_prose_flag")
        if prepared.get("cards_provenance") != "masked_workspace_and_task_only":
            issues.append("invalid_cards_provenance")
        if prepared.get("cards_frozen") is not True:
            issues.append("cards_not_frozen")

        generated_citations: set[str] = set()
        if artifacts is not None:
            for artifact in artifacts.iter_kind("generation"):
                if artifact.get("payload", {}).get("case_id") == case.id:
                    metrics = artifact.get("payload", {}).get("metrics", {})
                    generated_citations.update(
                        str(value) for value in metrics.get("citation_keys", [])
                    )
        review = auditor_reviews.get(case.id)
        review_issues = [] if review is None else review["issues"]
        review_decision = None if review is None else review["decision"]
        manifest_disagreements = [
            str(value) for value in (case.annotations.get("disagreements") or [])
        ]
        auditor_issue_codes = sorted({value.split(":", 1)[0] for value in review_issues if value})
        if review_decision == "approved":
            review_manifest_consistent = not review_issues and not manifest_disagreements
        elif review_decision == "needs_revision":
            review_manifest_consistent = case.annotations.get("auditor") == "complete" and sorted(
                review_issues
            ) == sorted(manifest_disagreements)
        else:
            review_manifest_consistent = False
        annotation_resolved = bool(
            case.annotations_resolved
            and review_decision == "approved"
            and review_manifest_consistent
        )
        corpus_warnings = sorted(
            {str(value) for value in (case.annotations.get("corpus_warnings") or [])}
        )

        case_results[case.id] = {
            "project_id": case.project_id,
            "issues": sorted(set(issues)),
            "mechanical_audit_pass": not issues,
            "auditor_decision": review_decision,
            "auditor_issue_codes": auditor_issue_codes,
            "review_manifest_consistent": review_manifest_consistent,
            "annotation_resolved": annotation_resolved,
            "corpus_warnings": corpus_warnings,
            "bibliography_key_count": len(valid_keys),
            "missing_manuscript_citation_keys": missing_manuscript,
            "missing_required_citation_keys": missing_required,
            "generated_keys_absent_from_bibliography": sorted(generated_citations - valid_keys),
            "corrected_valid_citation_keys": sorted(valid_keys),
            "corrected_expected_source_spans": corrected_spans,
            "rubric": {
                "required_idea_count": len(case.required_ideas),
                "required_term_count": len(case.required_terminology),
                "protected_literal_count": len(case.protected_literals),
                "source_span_count": len(corrected_spans),
                "gold_word_count": len(_word_tokens(gold)),
                "expected_output_range": list(case.expected_output_range),
                "idea_gold_presence": idea_gold_presence,
                "term_gold_presence": term_gold_presence,
                "protected_gold_presence": protected_gold_presence,
                "required_citation_gold_presence": required_citation_gold_presence,
                "source_obligation_anchor_presence": source_obligation_presence,
            },
            "privacy": {
                "gold_in_cards": gold in cards_text,
                "gold_visible_files": leakage_files,
                "long_overlap_count": len(prepared.get("leakage_audit", {}).get("matches", [])),
            },
        }
    unresolved_annotation_case_ids = sorted(
        case_id for case_id, result in case_results.items() if not result["annotation_resolved"]
    )
    return {
        "audit_version": 1,
        "cases": case_results,
        "projects": project_inventories,
        "unresolved_annotation_case_ids": unresolved_annotation_case_ids,
        "all_annotations_resolved": not unresolved_annotation_case_ids,
        "all_mechanical_checks_pass": all(
            result["mechanical_audit_pass"] for result in case_results.values()
        ),
    }


def _anchor_hit(text: str, anchor: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(anchor.casefold())}(?!\w)", text.casefold()) is not None


def _balanced_braces(text: str) -> bool:
    depth = 0
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _protected_structure_tokens(text: str) -> dict[str, list[str]]:
    return {
        "labels": re.findall(r"\\label\{[^{}]+\}", text),
        "references": re.findall(r"\\(?:ref|cref|Cref|autoref)\{[^{}]+\}", text),
        "equations": re.findall(
            r"\\begin\{(?:equation\*?|align\*?|gather\*?|multline\*?)\}.*?"
            r"\\end\{(?:equation\*?|align\*?|gather\*?|multline\*?)\}|\$\$.*?\$\$",
            text,
            flags=re.DOTALL,
        ),
    }


def deterministic_output_metrics(
    case: CaseManifest,
    output: str,
    *,
    gold: str | None = None,
    candidate_structure: str | None = None,
) -> dict[str, Any]:
    idea_hits: dict[str, bool] = {}
    for index, idea in enumerate(case.required_ideas):
        idea_id = str(idea.get("id") or f"idea_{index + 1}")
        anchors = [str(value) for value in idea.get("anchors", [])]
        expanded = []
        for anchor in anchors:
            expanded.append(anchor)
            expanded.extend(case.anchor_aliases.get(anchor, []))
        idea_hits[idea_id] = any(_anchor_hit(output, anchor) for anchor in expanded)
    term_hits = {term: _anchor_hit(output, term) for term in case.required_terminology}
    prohibited_hits = [claim for claim in case.prohibited_claims if _anchor_hit(output, claim)]
    citations = parse_citation_keys(output)
    valid = set(case.valid_citation_keys)
    required = set(case.required_citation_keys)
    label_tokens = re.findall(r"\\label\{[^{}]+\}", output)
    reference_tokens = re.findall(r"\\(?:ref|cref|Cref|autoref)\{[^{}]+\}", output)
    begin_envs = re.findall(r"\\begin\{([^{}]+)\}", output)
    end_envs = re.findall(r"\\end\{([^{}]+)\}", output)
    structural_ok = _balanced_braces(output) and sorted(begin_envs) == sorted(end_envs)
    structure_coverage: dict[str, float] = {}
    if gold is not None:
        expected_structure = _protected_structure_tokens(gold)
        actual_structure = candidate_structure if candidate_structure is not None else output
        for kind, expected_tokens in expected_structure.items():
            structure_coverage[kind] = (
                round(
                    sum(token in actual_structure for token in expected_tokens)
                    / len(expected_tokens),
                    4,
                )
                if expected_tokens
                else 1.0
            )
    else:
        structure_coverage = {"labels": 1.0, "references": 1.0, "equations": 1.0}
    return {
        "idea_hits": idea_hits,
        "idea_coverage": round(sum(idea_hits.values()) / len(idea_hits), 4) if idea_hits else 1.0,
        "terminology_hits": term_hits,
        "terminology_coverage": (
            round(sum(term_hits.values()) / len(term_hits), 4) if term_hits else 1.0
        ),
        "prohibited_claim_hits": prohibited_hits,
        "prohibited_claim_hit_count": len(prohibited_hits),
        "citation_keys": sorted(citations),
        "invalid_citation_keys": sorted(citations - valid),
        "citation_validity": 1.0 if citations <= valid else 0.0,
        "required_citation_recall": (
            round(len(citations & required) / len(required), 4) if required else 1.0
        ),
        "protected_literal_hits": {
            literal: literal in output for literal in case.protected_literals
        },
        "protected_literal_preservation": (
            round(
                sum(literal in output for literal in case.protected_literals)
                / len(case.protected_literals),
                4,
            )
            if case.protected_literals
            else 1.0
        ),
        "label_count": len(label_tokens),
        "reference_count": len(reference_tokens),
        "protected_structure_coverage": structure_coverage,
        "protected_structure_preservation": min(structure_coverage.values()),
        "structural_validity": 1.0 if structural_ok else 0.0,
        "balanced_braces": _balanced_braces(output),
        "latex_parse_status": "valid" if structural_ok else "invalid",
        "compile_status": "unavailable",
        "output_tokens": estimate_tokens(output),
    }


def compile_candidate(case: CaseManifest, prepared: dict[str, Any], output: str) -> str:
    """Compile a private masked-workspace copy when a compiler is available."""
    compiler = shutil.which("latexmk") or shutil.which("pdflatex")
    if compiler is None:
        return "unavailable"
    workspace = Path(prepared["workspace"])
    with tempfile.TemporaryDirectory(prefix=f"benchmark-compile-{case.id}-") as tmp_name:
        candidate_root = Path(tmp_name) / "workspace"
        shutil.copytree(workspace, candidate_root)
        source_path = candidate_root / prepared["target_source_path"]
        source = source_path.read_text(encoding="utf-8")
        marker = MASK_TEMPLATE.format(case_id=case.id)
        if marker not in source:
            return "fail"
        source_path.write_text(source.replace(marker, output, 1), encoding="utf-8")
        entry = candidate_root / prepared["entry_point"]
        if Path(compiler).name == "latexmk":
            command = [compiler, "-pdf", "-interaction=nonstopmode", "-halt-on-error", entry.name]
        else:
            command = [compiler, "-interaction=nonstopmode", "-halt-on-error", entry.name]
        try:
            result = subprocess.run(
                command,
                cwd=candidate_root,
                capture_output=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "fail"
        return "pass" if result.returncode == 0 else "fail"


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    api_key_env: str = ""
    command: str = ""
    cli_version: str = ""
    timeout_seconds: int = 600

    @property
    def id(self) -> str:
        suffix = f"@{self.cli_version}" if self.cli_version else ""
        return f"{self.provider}:{self.model}{suffix}"

    @property
    def family(self) -> str:
        return {
            "gemini": "gemini",
            "gemini_cli": "gemini",
            "agy_cli": "gemini",
            "openai": "openai",
            "codex_cli": "openai",
            "claude_cli": "anthropic",
        }[self.provider]

    @property
    def is_cli(self) -> bool:
        return self.provider.endswith("_cli")


@dataclass(frozen=True)
class ModelsConfig:
    temperature: float
    repetitions: int
    generators: dict[str, tuple[ModelSpec, ...]]
    judges: tuple[ModelSpec, ...]


def _parse_model_spec(value: Any, context: str) -> ModelSpec:
    if not isinstance(value, dict):
        raise BenchmarkError(f"{context} must be a mapping")
    _require_fields(value, ("provider", "model"), context)
    provider = str(value["provider"])
    if provider not in {
        "gemini",
        "openai",
        "gemini_cli",
        "agy_cli",
        "claude_cli",
        "codex_cli",
    }:
        raise BenchmarkError(f"Unsupported provider in {context}: {provider}")
    model = str(value["model"]).strip()
    if not model or model.startswith("replace-with-"):
        raise BenchmarkError(f"A concrete model identifier is required in {context}")
    if provider.endswith("_cli"):
        cli_version = str(value.get("cli_version", "")).strip()
        if cli_version.startswith("replace-with-"):
            raise BenchmarkError(f"A concrete CLI version is required in {context}")
        default_command = "agy" if provider == "agy_cli" else provider.removesuffix("_cli")
        command = str(value.get("command") or default_command).strip()
        timeout_seconds = int(value.get("timeout_seconds", 600))
        if timeout_seconds <= 0:
            raise BenchmarkError(f"timeout_seconds must be positive in {context}")
        return ModelSpec(
            provider=provider,
            model=model,
            command=command,
            cli_version=cli_version,
            timeout_seconds=timeout_seconds,
        )
    _require_fields(value, ("api_key_env",), context)
    return ModelSpec(provider=provider, model=model, api_key_env=str(value["api_key_env"]))


def load_models(path: Path) -> ModelsConfig:
    document = _read_yaml(path)
    _require_fields(
        document, ("version", "temperature", "repetitions", "generators", "judges"), str(path)
    )
    if document["version"] != 1:
        raise BenchmarkError(f"Unsupported models config version: {document['version']}")
    temperature = float(document["temperature"])
    repetitions = int(document["repetitions"])
    if temperature != 0.2 or repetitions != 3:
        raise BenchmarkError("Primary benchmark requires temperature 0.2 and exactly 3 repetitions")
    generators_raw = document["generators"]
    if not isinstance(generators_raw, dict):
        raise BenchmarkError("generators must be a mapping")
    generators = {
        stage: tuple(
            _parse_model_spec(value, f"generators.{stage}[{index}]")
            for index, value in enumerate(generators_raw.get(stage, []))
        )
        for stage in ("pilot", "confirmation")
    }
    if len(generators["pilot"]) != 1 or generators["pilot"][0].family != "gemini":
        raise BenchmarkError("Pilot requires exactly one Gemini generator")
    if len(generators["confirmation"]) != 2 or {
        spec.family for spec in generators["confirmation"]
    } != {"gemini", "openai"}:
        raise BenchmarkError("Confirmation requires one Gemini and one OpenAI generator")
    judges = tuple(
        _parse_model_spec(value, f"judges[{index}]")
        for index, value in enumerate(document["judges"])
    )
    if len(judges) != 2 or {judge.family for judge in judges} != {"gemini", "openai"}:
        raise BenchmarkError("Judging requires exactly one Gemini and one OpenAI model")
    return ModelsConfig(
        temperature=temperature,
        repetitions=repetitions,
        generators=generators,
        judges=judges,
    )


class ModelClient(Protocol):
    spec: ModelSpec

    def check_available(self) -> None:
        """Raise if the configured identifier is unavailable."""

    def generate(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        """Generate one text response without silently changing models."""


class HTTPModelClient:
    """Minimal Gemini/OpenAI adapter with explicit model identifiers."""

    def __init__(self, spec: ModelSpec):
        self.spec = spec
        key = os.environ.get(spec.api_key_env)
        if not key:
            raise BenchmarkError(f"Missing credential environment variable: {spec.api_key_env}")
        self.api_key = key

    def _request(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = canonical_json(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            raise BenchmarkError(f"{self.spec.id} request failed: {exc}") from exc
        if not isinstance(result, dict):
            raise BenchmarkError(f"{self.spec.id} returned a non-object response")
        return result

    def check_available(self) -> None:
        encoded = urllib.parse.quote(self.spec.model, safe="")
        if self.spec.provider == "gemini":
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{encoded}?key={urllib.parse.quote(self.api_key, safe='')}"
            )
            result = self._request(url)
            returned = str(result.get("name", "")).removeprefix("models/")
        else:
            url = f"https://api.openai.com/v1/models/{encoded}"
            result = self._request(url, headers={"Authorization": f"Bearer {self.api_key}"})
            returned = str(result.get("id", ""))
        if returned != self.spec.model:
            raise BenchmarkError(
                f"Configured model unavailable or mismatched: requested {self.spec.model}, got {returned!r}"
            )

    def generate(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        encoded = urllib.parse.quote(self.spec.model, safe="")
        if self.spec.provider == "gemini":
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{encoded}:generateContent?key={urllib.parse.quote(self.api_key, safe='')}"
            )
            result = self._request(
                url,
                method="POST",
                payload={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": temperature,
                        "maxOutputTokens": max_tokens,
                    },
                },
            )
            try:
                return "".join(
                    str(part.get("text", ""))
                    for part in result["candidates"][0]["content"]["parts"]
                )
            except (KeyError, IndexError, TypeError) as exc:
                raise BenchmarkError(f"Malformed Gemini response from {self.spec.id}") from exc
        result = self._request(
            "https://api.openai.com/v1/responses",
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}"},
            payload={
                "model": self.spec.model,
                "input": prompt,
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            },
        )
        if isinstance(result.get("output_text"), str):
            return str(result["output_text"])
        texts = []
        for item in result.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    texts.append(str(content.get("text", "")))
        if not texts:
            raise BenchmarkError(f"Malformed OpenAI response from {self.spec.id}")
        return "".join(texts)


def _model_cli_environment() -> dict[str, str]:
    """Keep subscription CLI auth while hiding the evaluated RTFM executable."""
    environment = os.environ.copy()
    path_entries = []
    for entry in environment.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        if (Path(entry).expanduser() / "rtfm").is_file():
            continue
        path_entries.append(entry)
    environment["PATH"] = os.pathsep.join(path_entries)
    environment.pop("VIRTUAL_ENV", None)
    return environment


def _run_cli_process_group(
    command: list[str],
    *,
    prompt: str | None,
    cwd: str,
    timeout: int,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one CLI invocation and reap every process in its isolated process group."""
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        text=True,
        start_new_session=True,
        env=environment,
    )

    def terminate_group() -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)

    try:
        stdout, stderr = process.communicate(input=prompt, timeout=timeout)
    except BaseException:
        terminate_group()
        with contextlib.suppress(Exception):
            process.wait(timeout=5)
        raise
    terminate_group()
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


class CLIModelClient:
    """Run an authenticated model CLI in an isolated, disposable working directory."""

    def __init__(self, spec: ModelSpec):
        self.spec = spec
        executable = shutil.which(spec.command)
        if executable is None:
            raise BenchmarkError(f"CLI executable unavailable for {spec.id}: {spec.command}")
        self.executable = executable

    def _run(
        self, command: list[str], prompt: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            with tempfile.TemporaryDirectory(prefix="writing-context-model-") as tmp_name:
                return _run_cli_process_group(
                    command,
                    prompt=prompt,
                    cwd=tmp_name,
                    timeout=self.spec.timeout_seconds,
                    environment=_model_cli_environment(),
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BenchmarkError(f"{self.spec.id} CLI invocation failed: {exc}") from exc

    def check_available(self) -> None:
        result = self._run([self.executable, "--version"])
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise BenchmarkError(f"Cannot inspect {self.spec.id} CLI version: {detail}")
        observed = result.stdout.strip() or result.stderr.strip()
        if self.spec.cli_version and self.spec.cli_version not in observed:
            raise BenchmarkError(
                f"CLI version mismatch for {self.spec.id}: expected {self.spec.cli_version!r}, "
                f"got {observed!r}"
            )
        if self.spec.provider == "agy_cli":
            models = self._run([self.executable, "models"])
            if models.returncode != 0:
                detail = (models.stderr or models.stdout).strip()
                raise BenchmarkError(f"Cannot list models for {self.spec.id}: {detail}")
            available = {
                line.split("\t", 1)[0].strip()
                for line in models.stdout.splitlines()
                if "\t" in line
            }
            if self.spec.model not in available:
                raise BenchmarkError(
                    f"Configured Antigravity model unavailable: {self.spec.model}; "
                    f"available models: {sorted(available)}"
                )

    def _antigravity_response(self, stream: str) -> str:
        terminal: dict[str, Any] | None = None
        observed_model: str | None = None
        for line in stream.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BenchmarkError(
                    f"{self.spec.id} returned malformed stream JSON: {exc}"
                ) from exc
            if isinstance(event, dict) and event.get("event") == "result":
                result = event.get("result")
                if isinstance(result, dict):
                    terminal = result
            if isinstance(event, dict) and event.get("event") == "init":
                init = event.get("init")
                if isinstance(init, dict) and isinstance(init.get("model"), str):
                    observed_model = str(init["model"])
        if observed_model is None:
            raise BenchmarkError(f"{self.spec.id} did not report its model identifier")
        if observed_model != self.spec.model:
            raise BenchmarkError(
                f"{self.spec.id} model mismatch: requested {self.spec.model!r}, "
                f"got {observed_model!r}"
            )
        if terminal is None:
            raise BenchmarkError(f"{self.spec.id} stream ended without a result event")
        if terminal.get("status") != "SUCCESS":
            detail = str(terminal.get("error") or terminal.get("status") or "unknown error")
            raise BenchmarkError(f"{self.spec.id} generation failed: {detail}")
        response = terminal.get("response")
        if not isinstance(response, str) or not response.strip():
            raise BenchmarkError(f"{self.spec.id} returned an empty response")
        return response

    def generate(self, prompt: str, *, temperature: float, max_tokens: int) -> str:
        # These subscription-backed CLIs do not expose stable sampling or output-token flags.
        # Preserve the requested values in the prompt and artifact configuration rather than
        # pretending the transport enforces controls it does not support.
        transport_note = (
            "\n\n[Benchmark transport controls: requested temperature="
            f"{temperature}; maximum output tokens={max_tokens}. Do not exceed that ceiling.]"
        )
        effective_prompt = prompt + transport_note
        if self.spec.provider == "agy_cli":
            command = [
                self.executable,
                "--input-format",
                "stream-json",
                "--model",
                self.spec.model,
                "--output-format",
                "stream-json",
                "--mode",
                "plan",
                "--sandbox",
                "--disable-slash-commands",
                "--print-timeout",
                f"{self.spec.timeout_seconds}s",
            ]
            message = canonical_json({"event": "user", "message": {"content": effective_prompt}})
            result = self._run(command, message + "\n")
            output = result.stdout
        elif self.spec.provider == "gemini_cli":
            command = [
                self.executable,
                "--model",
                self.spec.model,
                "--prompt",
                "",
                "--output-format",
                "text",
                "--approval-mode",
                "plan",
            ]
            result = self._run(command, effective_prompt)
            output = result.stdout
        elif self.spec.provider == "claude_cli":
            command = [
                self.executable,
                "--print",
                "--model",
                self.spec.model,
                "--output-format",
                "text",
                "--no-session-persistence",
                "--tools",
                "",
            ]
            result = self._run(command, effective_prompt)
            output = result.stdout
        elif self.spec.provider == "codex_cli":
            with tempfile.TemporaryDirectory(prefix="writing-context-codex-output-") as tmp_name:
                output_path = Path(tmp_name) / "last-message.txt"
                command = [
                    self.executable,
                    "exec",
                    "-",
                    "--model",
                    self.spec.model,
                    "--ephemeral",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--sandbox",
                    "read-only",
                    "--skip-git-repo-check",
                    "--output-last-message",
                    str(output_path),
                ]
                result = self._run(command, effective_prompt)
                output = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
        else:
            raise BenchmarkError(f"Unsupported CLI provider: {self.spec.provider}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise BenchmarkError(f"{self.spec.id} generation failed: {detail}")
        if self.spec.provider == "agy_cli":
            return self._antigravity_response(output).strip()
        output = output.strip()
        if not output:
            raise BenchmarkError(f"{self.spec.id} returned an empty response")
        return output


def build_model_client(spec: ModelSpec) -> ModelClient:
    if spec.is_cli:
        return CLIModelClient(spec)
    return HTTPModelClient(spec)


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise BenchmarkError("Judge response contains no JSON object") from None
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise BenchmarkError(f"Malformed judge JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BenchmarkError("Judge response must be a JSON object")
    return value


def validate_judgment(
    value: dict[str, Any], *, candidate_id: str, valid_span_ids: set[str]
) -> dict[str, Any]:
    required = {"candidate_id", "ratings", "pass", "evidence_span_ids"}
    allowed = required | {"notes"}
    if set(value) - allowed or not required <= set(value):
        raise BenchmarkError("Judgment has missing or unknown top-level fields")
    if value["candidate_id"] != candidate_id:
        raise BenchmarkError("Judgment candidate_id does not match the blinded candidate")
    ratings = value["ratings"]
    if not isinstance(ratings, dict) or set(ratings) != set(CRITERIA):
        raise BenchmarkError("Judgment ratings must contain exactly the five declared criteria")
    if any(type(ratings[name]) is not int or not 0 <= ratings[name] <= 4 for name in CRITERIA):
        raise BenchmarkError("Judgment ratings must be integer values from 0 through 4")
    if type(value["pass"]) is not bool:
        raise BenchmarkError("Judgment pass must be a boolean")
    span_ids = value["evidence_span_ids"]
    if not isinstance(span_ids, list) or any(type(span_id) is not str for span_id in span_ids):
        raise BenchmarkError("Judgment evidence_span_ids must be a string list")
    invalid_ids = set(span_ids) - valid_span_ids
    if invalid_ids:
        raise BenchmarkError(f"Judgment cites invalid evidence span IDs: {sorted(invalid_ids)}")
    return value


def render_judge_prompt(
    *, candidate_id: str, case: CaseManifest, evidence: dict[str, Any], output: str
) -> str:
    """Render a blinded prompt containing no strategy identifier or gold prose."""
    packet = [
        {
            "id": span["id"],
            "path": span["path"],
            "line_start": span.get("line_start"),
            "line_end": span.get("line_end"),
            "text": span["text"],
        }
        for span in evidence.get("spans", [])
    ]
    schema = {
        "candidate_id": candidate_id,
        "ratings": dict.fromkeys(CRITERIA, "integer 0-4"),
        "pass": "boolean",
        "evidence_span_ids": ["valid supplied span IDs only"],
        "notes": "optional short explanation",
    }
    return (
        "Evaluate the blinded candidate only against the task, rubric, and condition evidence. "
        "Do not infer missing evidence. Return only strict JSON.\n\n"
        f"CANDIDATE ID\n{candidate_id}\n\n"
        f"TASK\n{case.task}\n\n"
        f"RUBRIC\n{canonical_json(case.rubric_for_judge())}\n\n"
        f"EVIDENCE PACKET\n{canonical_json(packet)}\n\n"
        f"CANDIDATE OUTPUT\n{output}\n\n"
        f"RESPONSE SCHEMA\n{canonical_json(schema)}"
    )


def reconcile_judgments(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    if first["pass"] != second["pass"]:
        reasons.append("pass_fail_conflict")
    averages: dict[str, float] = {}
    for criterion in CRITERIA:
        difference = abs(first["ratings"][criterion] - second["ratings"][criterion])
        if difference >= 2:
            reasons.append(f"{criterion}_difference_{difference}")
        else:
            averages[criterion] = round(
                (first["ratings"][criterion] + second["ratings"][criterion]) / 2,
                3,
            )
    return {
        "resolved": not reasons,
        "unresolved_reasons": reasons,
        "criterion_averages": averages,
        "pass": first["pass"] if first["pass"] == second["pass"] else None,
    }


def _artifact_key(
    *,
    kind: str,
    case: CaseManifest,
    prepared: dict[str, Any],
    strategy: str,
    repetition: int,
    model: str,
    prompt_version: str,
    parent_hash: str = "",
    code_revision: str | None = None,
) -> ArtifactKey:
    return ArtifactKey(
        kind=kind,
        case_hash=case.case_hash,
        strategy=strategy,
        repetition=repetition,
        model=model,
        code_revision=code_revision or current_code_revision(),
        prompt_version=prompt_version,
        rtfm_fingerprint=str(prepared["rtfm_fingerprint"]),
        cards_hash=str(prepared["cards_hash"]),
        retrieval_policy_version=RETRIEVAL_POLICY_VERSION,
        parent_hash=parent_hash,
    )


def cases_for_stage(cases: list[CaseManifest], stage: str) -> list[CaseManifest]:
    if stage not in {"pilot", "confirmation"}:
        raise BenchmarkError(f"Unknown stage: {stage}")
    return [case for case in cases if stage in case.stages]


def run_retrieval(
    cases: list[CaseManifest],
    *,
    stage: str,
    private_root: Path,
    artifacts: ArtifactStore,
    backend: RetrievalBackend,
    limit_cases: int | None = None,
) -> list[dict[str, Any]]:
    results = []
    selected_cases = cases_for_stage(cases, stage)[:limit_cases]
    for case in selected_cases:
        prepared = load_prepared(case, private_root)
        for strategy in STRATEGIES:
            key = _artifact_key(
                kind="retrieval",
                case=case,
                prepared=prepared,
                strategy=strategy,
                repetition=0,
                model="retrieval-only",
                prompt_version=PROMPT_VERSION,
            )
            artifact = artifacts.load(key)
            if artifact is None:
                evidence = backend.retrieve(case, prepared, strategy)
                payload = {
                    "case_id": case.id,
                    "project_id": case.project_id,
                    "evidence": evidence,
                    "metrics": retrieval_metrics(case, evidence),
                }
                artifact = artifacts.save(key, payload)
            results.append(artifact)
    return results


def _retrieval_artifact(
    case: CaseManifest,
    strategy: str,
    prepared: dict[str, Any],
    artifacts: ArtifactStore,
    *,
    source_code_revision: str | None = None,
) -> dict[str, Any]:
    key = _artifact_key(
        kind="retrieval",
        case=case,
        prepared=prepared,
        strategy=strategy,
        repetition=0,
        model="retrieval-only",
        prompt_version=PROMPT_VERSION,
        code_revision=source_code_revision,
    )
    artifact = artifacts.load(key)
    if artifact is None:
        raise BenchmarkError(f"Missing retrieval artifact for {case.id}/{strategy}")
    return artifact


def generation_request_count(
    cases: list[CaseManifest],
    models: ModelsConfig,
    *,
    stage: str,
    repetitions: int | None = None,
    limit_cases: int | None = None,
) -> int:
    repeat_count = repetitions if repetitions is not None else models.repetitions
    selected_cases = cases_for_stage(cases, stage)[:limit_cases]
    return len(selected_cases) * len(STRATEGIES) * repeat_count * len(models.generators[stage])


def judgment_request_count(
    cases: list[CaseManifest],
    models: ModelsConfig,
    *,
    stage: str,
    repetitions: int | None = None,
    limit_cases: int | None = None,
) -> int:
    return generation_request_count(
        cases,
        models,
        stage=stage,
        repetitions=repetitions,
        limit_cases=limit_cases,
    ) * len(models.judges)


def _assert_gold_absent(case: CaseManifest, prepared: dict[str, Any], *texts: str) -> None:
    gold = Path(prepared["gold_path"]).read_text(encoding="utf-8")
    for text in texts:
        if gold and gold in text:
            raise BenchmarkError(f"Complete gold text leaked into a benchmark prompt for {case.id}")


def run_generation(
    cases: list[CaseManifest],
    models: ModelsConfig,
    *,
    stage: str,
    private_root: Path,
    artifacts: ArtifactStore,
    client_factory: Any = build_model_client,
    repetitions: int | None = None,
    limit_cases: int | None = None,
) -> list[dict[str, Any]]:
    repeat_count = repetitions if repetitions is not None else models.repetitions
    clients = {spec.id: client_factory(spec) for spec in models.generators[stage]}
    generated = []
    for case in cases_for_stage(cases, stage)[:limit_cases]:
        prepared = load_prepared(case, private_root)
        for strategy in STRATEGIES:
            retrieval = _retrieval_artifact(case, strategy, prepared, artifacts)
            evidence = retrieval["payload"]["evidence"]
            prompt = render_generation_prompt(case, evidence)
            _assert_gold_absent(case, prepared, prompt)
            for spec in models.generators[stage]:
                for repetition in range(1, repeat_count + 1):
                    key = _artifact_key(
                        kind="generation",
                        case=case,
                        prepared=prepared,
                        strategy=strategy,
                        repetition=repetition,
                        model=spec.id,
                        prompt_version=PROMPT_VERSION,
                        parent_hash=retrieval["key_digest"],
                    )
                    artifact = artifacts.load(key)
                    if artifact is None:
                        started = time.perf_counter()
                        output = clients[spec.id].generate(
                            prompt,
                            temperature=models.temperature,
                            max_tokens=case.output_tokens,
                        )
                        latency = round((time.perf_counter() - started) * 1000, 3)
                        gold = Path(prepared["gold_path"]).read_text(encoding="utf-8")
                        retained_prefix = heading_and_label_prefix(gold)
                        metrics = deterministic_output_metrics(
                            case,
                            output,
                            gold=gold,
                            candidate_structure=retained_prefix + "\n" + output,
                        )
                        metrics["compile_status"] = compile_candidate(case, prepared, output)
                        artifact = artifacts.save(
                            key,
                            {
                                "case_id": case.id,
                                "project_id": case.project_id,
                                "generator_family": spec.family,
                                "prompt": prompt,
                                "prompt_sha256": sha256_text(prompt),
                                "output": output,
                                "generation_latency_ms": latency,
                                "metrics": {**metrics, "generation_latency_ms": latency},
                            },
                        )
                    generated.append(artifact)
    return generated


def _stage_generations(
    cases: list[CaseManifest],
    models: ModelsConfig,
    *,
    stage: str,
    private_root: Path,
    artifacts: ArtifactStore,
    repetitions: int,
    source_code_revision: str | None = None,
    limit_cases: int | None = None,
) -> list[tuple[CaseManifest, dict[str, Any], dict[str, Any]]]:
    records = []
    for case in cases_for_stage(cases, stage)[:limit_cases]:
        prepared = load_prepared(case, private_root)
        for strategy in STRATEGIES:
            retrieval = _retrieval_artifact(
                case,
                strategy,
                prepared,
                artifacts,
                source_code_revision=source_code_revision,
            )
            for spec in models.generators[stage]:
                for repetition in range(1, repetitions + 1):
                    key = _artifact_key(
                        kind="generation",
                        case=case,
                        prepared=prepared,
                        strategy=strategy,
                        repetition=repetition,
                        model=spec.id,
                        prompt_version=PROMPT_VERSION,
                        parent_hash=retrieval["key_digest"],
                        code_revision=source_code_revision,
                    )
                    artifact = artifacts.load(key)
                    if artifact is None:
                        raise BenchmarkError(
                            f"Missing generation for {case.id}/{strategy}/{spec.id}/run-{repetition}"
                        )
                    records.append((case, retrieval, artifact))
    return records


def run_judging(
    cases: list[CaseManifest],
    models: ModelsConfig,
    *,
    stage: str,
    private_root: Path,
    artifacts: ArtifactStore,
    client_factory: Any = build_model_client,
    repetitions: int | None = None,
    limit_cases: int | None = None,
) -> list[dict[str, Any]]:
    repeat_count = repetitions if repetitions is not None else models.repetitions
    clients = {spec.id: client_factory(spec) for spec in models.judges}
    judgments = []
    for case, retrieval, generation in _stage_generations(
        cases,
        models,
        stage=stage,
        private_root=private_root,
        artifacts=artifacts,
        repetitions=repeat_count,
        limit_cases=limit_cases,
    ):
        prepared = load_prepared(case, private_root)
        evidence = retrieval["payload"]["evidence"]
        output = generation["payload"]["output"]
        candidate_id = f"C-{sha256_text(generation['key_digest'])[:16]}"
        prompt = render_judge_prompt(
            candidate_id=candidate_id,
            case=case,
            evidence=evidence,
            output=output,
        )
        if generation["key"]["strategy"] in prompt:
            raise BenchmarkError("Strategy name leaked into blinded judge prompt")
        _assert_gold_absent(case, prepared, prompt)
        valid_span_ids = {str(span["id"]) for span in evidence.get("spans", [])}
        for judge in models.judges:
            key = _artifact_key(
                kind="judgment",
                case=case,
                prepared=prepared,
                strategy=generation["key"]["strategy"],
                repetition=generation["key"]["repetition"],
                model=judge.id,
                prompt_version=JUDGE_PROMPT_VERSION,
                parent_hash=generation["key_digest"],
            )
            artifact = artifacts.load(key)
            if artifact is None:
                started = time.perf_counter()
                raw_response = clients[judge.id].generate(
                    prompt,
                    temperature=0.0,
                    max_tokens=800,
                )
                latency = round((time.perf_counter() - started) * 1000, 3)
                try:
                    judgment = validate_judgment(
                        extract_json_object(raw_response),
                        candidate_id=candidate_id,
                        valid_span_ids=valid_span_ids,
                    )
                    validity: dict[str, Any] = {"valid": True, "error": None}
                except BenchmarkError as exc:
                    judgment = None
                    validity = {"valid": False, "error": str(exc)}
                artifact = artifacts.save(
                    key,
                    {
                        "case_id": case.id,
                        "candidate_id": candidate_id,
                        "judge_family": judge.family,
                        "generation_model": generation["key"]["model"],
                        "judgment": judgment,
                        **validity,
                        "judgment_latency_ms": latency,
                    },
                )
            judgments.append(artifact)
    return judgments


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 4)
    result = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(result, 4)


def _summary(values: list[float]) -> dict[str, float | None]:
    return {
        "median": _percentile(values, 0.5),
        "q1": _percentile(values, 0.25),
        "q3": _percentile(values, 0.75),
    }


def _case_bootstrap_interval(
    values: dict[str, list[float]], samples: int = 2000
) -> list[float] | None:
    """Deterministic case-level bootstrap; repetitions stay nested within each case."""
    if not values:
        return None
    import random

    case_ids = sorted(values)
    case_medians = {
        case_id: float(_percentile(items, 0.5) or 0.0) for case_id, items in values.items()
    }
    rng = random.Random(20260823)
    estimates = []
    for _ in range(samples):
        draw = [case_medians[rng.choice(case_ids)] for _ in case_ids]
        estimates.append(float(_percentile(draw, 0.5) or 0.0))
    low = _percentile(estimates, 0.025)
    high = _percentile(estimates, 0.975)
    return [float(low or 0.0), float(high or 0.0)]


CONDITION_DEFINITIONS = {
    "full_visible": "All allowlisted visible manuscript and bibliography text; hidden gold excluded; no context cap.",
    "rtfm_topk": "One raw task query; ranked local RTFM chunks, with the raw top-100 candidate pool diagnosed before the 6,000-token selection.",
    "pack_baseline": "Writing context pack with strict 6,000-token budget and RRF disabled.",
    "pack_rrf": "Writing context pack with strict 6,000-token budget and experimental RRF enabled.",
}


def build_retrieval_diagnostic_report(
    cases: list[CaseManifest],
    *,
    stage: str,
    private_root: Path,
    artifacts: ArtifactStore,
    source_code_revision: str | None = None,
    limit_cases: int | None = None,
) -> dict[str, Any]:
    stage_cases = cases_for_stage(cases, stage)[:limit_cases]
    values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    first_ranks: dict[str, list[float]] = defaultdict(list)
    loss_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    selection_reason_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for case in stage_cases:
        prepared = load_prepared(case, private_root)
        for strategy in STRATEGIES:
            artifact = _retrieval_artifact(
                case,
                strategy,
                prepared,
                artifacts,
                source_code_revision=source_code_revision,
            )
            metrics = retrieval_metrics(case, artifact["payload"]["evidence"])
            if not case.annotations_resolved or not metrics["candidate_diagnostics_available"]:
                continue
            for cutoff in _CANDIDATE_RECALL_CUTOFFS:
                name = f"candidate_recall_at_{cutoff}"
                values[strategy][name].append(float(metrics[name]))
            for name in ("graded_source_recall", "candidate_to_selected_recall_delta"):
                values[strategy][name].append(float(metrics[name]))
            for stage_name, stage_recall in metrics["diagnostic_stage_recall"].items():
                values[strategy][f"stage_recall_{stage_name}"].append(float(stage_recall))

            counts[strategy]["cases_with_diagnostics"] += 1
            for outcome in metrics["expected_source_outcomes"]:
                counts[strategy]["expected_sources_evaluated"] += 1
                first_rank = outcome["first_candidate_rank"]
                if not outcome["selected"] and outcome["lost_after"] is None:
                    counts[strategy]["never_retrieved"] += 1
                else:
                    if first_rank is not None:
                        first_ranks[strategy].append(float(first_rank))
                    key = "selected" if outcome["selected"] else "retrieved_not_selected"
                    counts[strategy][key] += 1
            for loss_stage, count in metrics["loss_stage_counts"].items():
                loss_counts[strategy][loss_stage] += int(count)
            for reason, count in metrics["selection_loss_reason_counts"].items():
                selection_reason_counts[strategy][reason] += int(count)

    strategy_summaries = {
        strategy: {
            metric: _summary(metric_values)
            for metric, metric_values in sorted(values[strategy].items())
        }
        for strategy in STRATEGIES
    }
    candidate_diagnostics = {
        strategy: {
            "cases_with_diagnostics": counts[strategy].get("cases_with_diagnostics", 0),
            "expected_sources_evaluated": counts[strategy].get("expected_sources_evaluated", 0),
            "never_retrieved": counts[strategy].get("never_retrieved", 0),
            "retrieved_not_selected": counts[strategy].get("retrieved_not_selected", 0),
            "selected": counts[strategy].get("selected", 0),
            "first_candidate_rank": _summary(first_ranks[strategy]),
            "loss_stage_counts": dict(sorted(loss_counts[strategy].items())),
            "selection_loss_reason_counts": dict(sorted(selection_reason_counts[strategy].items())),
        }
        for strategy in STRATEGIES
    }
    analysis_revision = current_code_revision()
    return {
        "report_version": 1,
        "report_type": "candidate_diagnostics",
        "stage": stage,
        "case_count": len(stage_cases),
        "analysis_code_revision": analysis_revision,
        "source_code_revision": source_code_revision or analysis_revision,
        "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
        "strategies": strategy_summaries,
        "candidate_diagnostics": candidate_diagnostics,
        "annotation_disagreement_cases_excluded_from_relevance": [
            case.id for case in stage_cases if not case.annotations_resolved
        ],
        "limitations": [
            "Candidate diagnostics measure annotated source-span retrieval, not writing quality.",
            "Conclusions apply only to the configured corpus and retrieval policy.",
        ],
    }


def build_report(
    cases: list[CaseManifest],
    models: ModelsConfig,
    *,
    stage: str,
    private_root: Path,
    artifacts: ArtifactStore,
    repetitions: int | None = None,
    source_code_revision: str | None = None,
    limit_cases: int | None = None,
) -> dict[str, Any]:
    repeat_count = repetitions if repetitions is not None else models.repetitions
    stage_cases = cases_for_stage(cases, stage)[:limit_cases]
    retrieval_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    output_values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    retrieval_by_case: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    candidate_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    candidate_first_ranks: dict[str, list[float]] = defaultdict(list)
    candidate_loss_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    candidate_selection_reason_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    case_metric_values: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    output_by_pair: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    seen_retrievals: set[str] = set()
    generation_by_digest: dict[str, tuple[CaseManifest, dict[str, Any], dict[str, Any]]] = {}
    for case, retrieval, generation in _stage_generations(
        cases,
        models,
        stage=stage,
        private_root=private_root,
        artifacts=artifacts,
        repetitions=repeat_count,
        source_code_revision=source_code_revision,
        limit_cases=limit_cases,
    ):
        strategy = generation["key"]["strategy"]
        retrieval_metrics_value = retrieval_metrics(case, retrieval["payload"]["evidence"])
        if retrieval["key_digest"] not in seen_retrievals:
            seen_retrievals.add(retrieval["key_digest"])
            if case.annotations_resolved:
                for name in (
                    "graded_source_recall",
                    "graded_source_precision",
                    "mrr",
                    "ndcg",
                    "irrelevant_source_rate",
                    "obligation_coverage",
                ):
                    value = float(retrieval_metrics_value[name])
                    retrieval_values[strategy][name].append(value)
                    retrieval_by_case[strategy][name][case.id].append(value)
                if retrieval_metrics_value["candidate_diagnostics_available"]:
                    for cutoff in _CANDIDATE_RECALL_CUTOFFS:
                        name = f"candidate_recall_at_{cutoff}"
                        value = float(retrieval_metrics_value[name])
                        retrieval_values[strategy][name].append(value)
                        retrieval_by_case[strategy][name][case.id].append(value)
                    name = "candidate_to_selected_recall_delta"
                    value = float(retrieval_metrics_value[name])
                    retrieval_values[strategy][name].append(value)
                    retrieval_by_case[strategy][name][case.id].append(value)
                    for stage_name, stage_value in retrieval_metrics_value[
                        "diagnostic_stage_recall"
                    ].items():
                        name = f"stage_recall_{stage_name}"
                        value = float(stage_value)
                        retrieval_values[strategy][name].append(value)
                        retrieval_by_case[strategy][name][case.id].append(value)
                    candidate_counts[strategy]["cases_with_diagnostics"] += 1
                    for outcome in retrieval_metrics_value["expected_source_outcomes"]:
                        candidate_counts[strategy]["expected_sources_evaluated"] += 1
                        first_rank = outcome["first_candidate_rank"]
                        if not outcome["selected"] and outcome["lost_after"] is None:
                            candidate_counts[strategy]["never_retrieved"] += 1
                        else:
                            if first_rank is not None:
                                candidate_first_ranks[strategy].append(float(first_rank))
                            if outcome["selected"]:
                                candidate_counts[strategy]["selected"] += 1
                            else:
                                candidate_counts[strategy]["retrieved_not_selected"] += 1
                    for loss_stage, count in retrieval_metrics_value["loss_stage_counts"].items():
                        candidate_loss_counts[strategy][loss_stage] += int(count)
                    for reason, count in retrieval_metrics_value[
                        "selection_loss_reason_counts"
                    ].items():
                        candidate_selection_reason_counts[strategy][reason] += int(count)
            for name in ("duplicate_context_ratio", "context_tokens", "retrieval_latency_ms"):
                value = float(retrieval_metrics_value[name])
                retrieval_values[strategy][name].append(value)
                retrieval_by_case[strategy][name][case.id].append(value)
        pair_id = f"{case.id}|{generation['key']['model']}|{generation['key']['repetition']}"
        for name, value in generation["payload"]["metrics"].items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                output_values[strategy][name].append(float(value))
                case_metric_values[strategy][name][case.id].append(float(value))
                output_by_pair[strategy][name][pair_id] = float(value)
        generation_by_digest[generation["key_digest"]] = (case, retrieval, generation)

    judgment_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stage_generation_digests = set(generation_by_digest)
    for artifact in artifacts.iter_kind("judgment"):
        if artifact.get("key", {}).get("parent_hash") in stage_generation_digests:
            judgment_groups[artifact["key"]["parent_hash"]].append(artifact)
    resolved_scores: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    case_judge_values: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    disagreements_by_generator: dict[str, dict[str, int]] = defaultdict(
        lambda: {"resolved": 0, "unresolved": 0}
    )
    unresolved = 0
    judged_pairs = 0
    for digest, generation_record in generation_by_digest.items():
        pair = judgment_groups.get(digest, [])
        if len(pair) != 2:
            unresolved += 1
            judged_pairs += 1
            continue
        judged_pairs += 1
        if not all(item["payload"].get("valid") is True for item in pair):
            unresolved += 1
            generator_family = generation_record[2]["payload"]["generator_family"]
            disagreements_by_generator[generator_family]["unresolved"] += 1
            continue
        reconciliation = reconcile_judgments(
            pair[0]["payload"]["judgment"], pair[1]["payload"]["judgment"]
        )
        generator_family = generation_record[2]["payload"]["generator_family"]
        status = "resolved" if reconciliation["resolved"] else "unresolved"
        disagreements_by_generator[generator_family][status] += 1
        if not reconciliation["resolved"]:
            unresolved += 1
        strategy = generation_record[2]["key"]["strategy"]
        case_id = generation_record[0].id
        for criterion, value in reconciliation["criterion_averages"].items():
            resolved_scores[strategy][criterion].append(float(value))
            case_judge_values[strategy][criterion][case_id].append(float(value))

    strategies: dict[str, Any] = {}
    for strategy in STRATEGIES:
        strategies[strategy] = {
            "retrieval": {
                metric: _summary(values)
                for metric, values in sorted(retrieval_values[strategy].items())
            },
            "output": {
                metric: {
                    **_summary(values),
                    "case_bootstrap_95": _case_bootstrap_interval(
                        case_metric_values[strategy][metric]
                    ),
                }
                for metric, values in sorted(output_values[strategy].items())
            },
            "judges": {
                criterion: _summary(values)
                for criterion, values in sorted(resolved_scores[strategy].items())
            },
        }

    candidate_diagnostics = {}
    for strategy in STRATEGIES:
        counts = candidate_counts[strategy]
        candidate_diagnostics[strategy] = {
            "cases_with_diagnostics": counts.get("cases_with_diagnostics", 0),
            "expected_sources_evaluated": counts.get("expected_sources_evaluated", 0),
            "never_retrieved": counts.get("never_retrieved", 0),
            "retrieved_not_selected": counts.get("retrieved_not_selected", 0),
            "selected": counts.get("selected", 0),
            "first_candidate_rank": _summary(candidate_first_ranks[strategy]),
            "loss_stage_counts": dict(sorted(candidate_loss_counts[strategy].items())),
            "selection_loss_reason_counts": dict(
                sorted(candidate_selection_reason_counts[strategy].items())
            ),
        }

    paired_differences: dict[str, Any] = {}
    for strategy in ("pack_baseline", "pack_rrf"):
        paired_differences[strategy] = {}
        for comparator in ("full_visible", "rtfm_topk"):
            comparison: dict[str, Any] = {"output": {}, "retrieval": {}}
            for metric in sorted(set(output_by_pair[strategy]) & set(output_by_pair[comparator])):
                common = set(output_by_pair[strategy][metric]) & set(
                    output_by_pair[comparator][metric]
                )
                by_case: dict[str, list[float]] = defaultdict(list)
                for pair_id in common:
                    case_id = pair_id.split("|", 1)[0]
                    by_case[case_id].append(
                        output_by_pair[strategy][metric][pair_id]
                        - output_by_pair[comparator][metric][pair_id]
                    )
                differences = [value for values in by_case.values() for value in values]
                comparison["output"][metric] = {
                    **_summary(differences),
                    "case_bootstrap_95": _case_bootstrap_interval(by_case),
                }
            for metric in sorted(
                set(retrieval_by_case[strategy]) & set(retrieval_by_case[comparator])
            ):
                common_cases = set(retrieval_by_case[strategy][metric]) & set(
                    retrieval_by_case[comparator][metric]
                )
                by_case = {
                    case_id: [
                        float(
                            (_percentile(retrieval_by_case[strategy][metric][case_id], 0.5) or 0.0)
                            - (
                                _percentile(retrieval_by_case[comparator][metric][case_id], 0.5)
                                or 0.0
                            )
                        )
                    ]
                    for case_id in common_cases
                }
                differences = [values[0] for values in by_case.values()]
                comparison["retrieval"][metric] = {
                    **_summary(differences),
                    "case_bootstrap_95": _case_bootstrap_interval(by_case),
                }
            paired_differences[strategy][f"minus_{comparator}"] = comparison

    def median(strategy: str, group: str, metric: str) -> float:
        value = strategies[strategy][group].get(metric, {}).get("median")
        return float(value) if value is not None else 0.0

    best_nonpack_ideas = max(
        median(strategy, "output", "idea_coverage") for strategy in ("full_visible", "rtfm_topk")
    )
    best_nonpack_support = max(
        median(strategy, "judges", "evidence_support") for strategy in ("full_visible", "rtfm_topk")
    )
    full_tokens = median("full_visible", "retrieval", "context_tokens")
    gates: dict[str, dict[str, Any]] = {}

    def case_median(
        values: dict[str, dict[str, dict[str, list[float]]]],
        strategy: str,
        metric: str,
        case_id: str,
    ) -> float | None:
        items = values[strategy][metric].get(case_id, [])
        result = _percentile(items, 0.5)
        return float(result) if result is not None else None

    for strategy in ("pack_baseline", "pack_rrf"):
        gates[strategy] = {
            "idea_noninferiority": median(strategy, "output", "idea_coverage")
            >= best_nonpack_ideas - 0.05,
            "protected_literals_no_regression": median(
                strategy, "output", "protected_literal_preservation"
            )
            >= min(
                median("full_visible", "output", "protected_literal_preservation"),
                median("rtfm_topk", "output", "protected_literal_preservation"),
            ),
            "citation_no_regression": median(strategy, "output", "citation_validity")
            >= min(
                median("full_visible", "output", "citation_validity"),
                median("rtfm_topk", "output", "citation_validity"),
            ),
            "structure_no_regression": median(strategy, "output", "structural_validity")
            >= min(
                median("full_visible", "output", "structural_validity"),
                median("rtfm_topk", "output", "structural_validity"),
            ),
            "prohibited_claims_no_increase": median(
                strategy, "output", "prohibited_claim_hit_count"
            )
            <= max(
                median("full_visible", "output", "prohibited_claim_hit_count"),
                median("rtfm_topk", "output", "prohibited_claim_hit_count"),
            ),
            "context_reduction_25pct": (
                median(strategy, "retrieval", "context_tokens") <= full_tokens * 0.75
                if full_tokens
                else False
            ),
            "judge_support_noninferiority": median(strategy, "judges", "evidence_support")
            >= best_nonpack_support - 0.25,
        }
        passing_cases = 0
        evaluated_cases = 0
        for case in stage_cases:
            case_id = case.id
            pack_idea = case_median(case_metric_values, strategy, "idea_coverage", case_id)
            pack_literals = case_median(
                case_metric_values, strategy, "protected_literal_preservation", case_id
            )
            pack_structure = case_median(
                case_metric_values, strategy, "protected_structure_preservation", case_id
            )
            pack_citations = case_median(case_metric_values, strategy, "citation_validity", case_id)
            pack_syntax = case_median(case_metric_values, strategy, "structural_validity", case_id)
            pack_prohibited = case_median(
                case_metric_values, strategy, "prohibited_claim_hit_count", case_id
            )
            pack_support = case_median(case_judge_values, strategy, "evidence_support", case_id)
            pack_tokens = case_median(retrieval_by_case, strategy, "context_tokens", case_id)
            full_case_tokens = case_median(
                retrieval_by_case, "full_visible", "context_tokens", case_id
            )
            required_values = (
                pack_idea,
                pack_literals,
                pack_structure,
                pack_citations,
                pack_syntax,
                pack_prohibited,
                pack_support,
                pack_tokens,
                full_case_tokens,
            )
            evaluated_cases += 1
            if any(value is None for value in required_values):
                continue
            assert pack_idea is not None
            assert pack_literals is not None
            assert pack_structure is not None
            assert pack_citations is not None
            assert pack_syntax is not None
            assert pack_prohibited is not None
            assert pack_support is not None
            assert pack_tokens is not None
            assert full_case_tokens is not None

            def nonpack(metric: str, *, group: str = "output") -> list[float]:
                source = case_metric_values if group == "output" else case_judge_values
                values = [
                    case_median(source, condition, metric, case_id)
                    for condition in ("full_visible", "rtfm_topk")
                ]
                return [value for value in values if value is not None]

            idea_reference = nonpack("idea_coverage")
            literal_reference = nonpack("protected_literal_preservation")
            structure_reference = nonpack("protected_structure_preservation")
            citation_reference = nonpack("citation_validity")
            syntax_reference = nonpack("structural_validity")
            prohibited_reference = nonpack("prohibited_claim_hit_count")
            support_reference = nonpack("evidence_support", group="judges")
            if not all(
                (
                    idea_reference,
                    literal_reference,
                    structure_reference,
                    citation_reference,
                    syntax_reference,
                    prohibited_reference,
                    support_reference,
                )
            ):
                continue
            case_passes = (
                pack_idea >= max(idea_reference) - 0.05
                and pack_literals >= max(literal_reference)
                and pack_structure >= max(structure_reference)
                and pack_citations >= max(citation_reference)
                and pack_syntax >= max(syntax_reference)
                and pack_prohibited <= min(prohibited_reference)
                and pack_tokens <= full_case_tokens * 0.75
                and pack_support >= max(support_reference) - 0.25
            )
            passing_cases += int(case_passes)
        case_rate = round(passing_cases / evaluated_cases, 4) if evaluated_cases else 0.0
        gates[strategy]["cases_passing_all_gates"] = passing_cases
        gates[strategy]["cases_evaluated"] = evaluated_cases
        gates[strategy]["case_all_gate_rate"] = case_rate
        gates[strategy]["at_least_75_percent_cases"] = case_rate >= 0.75

    improved_families: list[str] = []
    for family in sorted({case.task_type for case in stage_cases}):
        family_cases = [case.id for case in stage_cases if case.task_type == family]
        improves = []
        for metric, direction in (
            ("graded_source_recall", 1.0),
            ("ndcg", 1.0),
            ("irrelevant_source_rate", -1.0),
        ):
            differences = []
            for case_id in family_cases:
                rrf = case_median(retrieval_by_case, "pack_rrf", metric, case_id)
                baseline = case_median(retrieval_by_case, "pack_baseline", metric, case_id)
                if rrf is not None and baseline is not None:
                    differences.append((rrf - baseline) * direction)
            improves.append(bool(differences) and float(_percentile(differences, 0.5) or 0.0) > 0)
        if any(improves):
            improved_families.append(family)
    protected_regression_cases = []
    materially_worse_cases = []
    for case in stage_cases:
        case_id = case.id
        rrf_literals = case_median(
            case_metric_values, "pack_rrf", "protected_literal_preservation", case_id
        )
        base_literals = case_median(
            case_metric_values, "pack_baseline", "protected_literal_preservation", case_id
        )
        rrf_structure = case_median(
            case_metric_values, "pack_rrf", "protected_structure_preservation", case_id
        )
        base_structure = case_median(
            case_metric_values, "pack_baseline", "protected_structure_preservation", case_id
        )
        if (
            rrf_literals is not None and base_literals is not None and rrf_literals < base_literals
        ) or (
            rrf_structure is not None
            and base_structure is not None
            and rrf_structure < base_structure
        ):
            protected_regression_cases.append(case_id)
        rrf_idea = case_median(case_metric_values, "pack_rrf", "idea_coverage", case_id)
        base_idea = case_median(case_metric_values, "pack_baseline", "idea_coverage", case_id)
        rrf_support = case_median(case_judge_values, "pack_rrf", "evidence_support", case_id)
        base_support = case_median(case_judge_values, "pack_baseline", "evidence_support", case_id)
        if (rrf_idea is not None and base_idea is not None and rrf_idea < base_idea - 0.05) or (
            rrf_support is not None
            and base_support is not None
            and rrf_support < base_support - 0.25
        ):
            materially_worse_cases.append(case_id)
    rrf_promotion = {
        "confirmation_only": stage == "confirmation",
        "improved_task_families": improved_families,
        "improves_at_least_two_task_families": len(improved_families) >= 2,
        "protected_regression_cases": protected_regression_cases,
        "no_protected_content_regression": not protected_regression_cases,
        "materially_worse_cases": materially_worse_cases,
        "not_materially_worse_in_more_than_two_cases": len(materially_worse_cases) <= 2,
    }
    rrf_promotion["eligible"] = all(
        (
            rrf_promotion["confirmation_only"],
            rrf_promotion["improves_at_least_two_task_families"],
            rrf_promotion["no_protected_content_regression"],
            rrf_promotion["not_materially_worse_in_more_than_two_cases"],
        )
    )
    analysis_code_revision = current_code_revision()
    report = {
        "report_version": 2,
        "stage": stage,
        "corpus_hashes": sorted({case.archive_sha256 for case in stage_cases}),
        "case_ids": [case.id for case in stage_cases],
        "code_revision": analysis_code_revision,
        "analysis_code_revision": analysis_code_revision,
        "source_code_revision": source_code_revision or analysis_code_revision,
        "generator_model_ids": [spec.id for spec in models.generators[stage]],
        "judge_model_ids": [spec.id for spec in models.judges],
        "prompt_version": PROMPT_VERSION,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
        "condition_definitions": CONDITION_DEFINITIONS,
        "strategies": strategies,
        "candidate_diagnostics": candidate_diagnostics,
        "paired_case_run_differences": paired_differences,
        "validity_gates": gates,
        "rrf_promotion_decision": rrf_promotion,
        "unresolved_judge_rate": round(unresolved / judged_pairs, 4) if judged_pairs else 1.0,
        "judge_disagreement_by_generator_family": dict(disagreements_by_generator),
        "annotation_disagreement_cases_excluded_from_relevance": [
            case.id for case in stage_cases if not case.annotations_resolved
        ],
        "limitations": [
            "Conclusions apply only to the configured corpus and model identifiers.",
            "Deterministic scores are proxies for writing quality.",
            "Dual-model judgments are not human ground truth.",
            "Generation repetitions are nested within manuscript cases, not independent manuscript samples.",
            "Case-level bootstrap intervals are descriptive and do not establish universal statistical significance.",
        ],
    }
    return report


def _is_git_ignored(path: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def privacy_preflight(cases_path: Path, models_path: Path, cases: list[CaseManifest]) -> None:
    """Fail closed if local configuration or corpus archives could be tracked."""
    for path in (cases_path, models_path):
        if not _is_git_ignored(path):
            raise BenchmarkError(f"Private local configuration is not ignored by Git: {path}")
    for case in cases:
        try:
            case.archive.relative_to(Path.cwd().resolve())
        except ValueError:
            continue
        if not _is_git_ignored(case.archive):
            raise BenchmarkError(f"Private archive is not ignored by Git: {case.archive}")
    result = subprocess.run(
        ["git", "ls-files", "benchmark"],
        check=True,
        capture_output=True,
        text=True,
    )
    forbidden = re.compile(
        r"(?:\.zip$|\.local\.ya?ml$|(?:^|/)(?:workspaces|private|artifacts|reports)\.local/|"
        r"(?:prompt|generation|judgment).*\.json$)"
    )
    tracked_private = [path for path in result.stdout.splitlines() if forbidden.search(path)]
    if tracked_private:
        raise BenchmarkError(f"Private benchmark paths are tracked: {tracked_private}")


def run_preflight(
    cases_path: Path,
    models_path: Path,
    *,
    client_factory: Any = build_model_client,
    enforce_stage_sizes: bool = True,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    models = load_models(models_path)
    privacy_preflight(cases_path, models_path, cases)
    if enforce_stage_sizes:
        sizes = {stage: len(cases_for_stage(cases, stage)) for stage in ("pilot", "confirmation")}
        if sizes != {"pilot": 8, "confirmation": 12}:
            raise BenchmarkError(f"Expected 8 pilot and 12 confirmation cases, got {sizes}")
    for case in cases:
        if not case.archive.is_file():
            raise BenchmarkError(f"Archive not found: {case.archive}")
        if file_sha256(case.archive) != case.archive_sha256:
            raise BenchmarkError(f"Archive hash mismatch: {case.id}")
        validate_zip_members(case.archive)
    if importlib.util.find_spec("rtfm.core.sync") is None:
        raise BenchmarkError("RTFM Python library is unavailable")
    if importlib.util.find_spec("tiktoken") is None:
        raise BenchmarkError("tiktoken is required for the benchmark")
    required_bytes = max(1_000_000_000, sum(case.archive.stat().st_size for case in cases) * 4)
    free_bytes = shutil.disk_usage(Path.cwd()).free
    if free_bytes < required_bytes:
        raise BenchmarkError(
            f"Insufficient free disk: need {required_bytes} bytes, have {free_bytes} bytes"
        )
    unique_specs = {
        spec.id: spec
        for spec in (
            *models.generators["pilot"],
            *models.generators["confirmation"],
            *models.judges,
        )
    }
    for spec in unique_specs.values():
        client_factory(spec).check_available()
    return {
        "status": "ok",
        "cases": len(cases),
        "pilot_generations": generation_request_count(cases, models, stage="pilot"),
        "pilot_judgments": judgment_request_count(cases, models, stage="pilot"),
        "confirmation_generations": generation_request_count(cases, models, stage="confirmation"),
        "confirmation_judgments": judgment_request_count(cases, models, stage="confirmation"),
        "models": sorted(unique_specs),
        "free_disk_bytes": free_bytes,
    }


def _confirm_paid_run(confirmed: bool, count: int, noun: str) -> None:
    print(f"Exact planned {noun} request count: {count}")
    if not confirmed:
        raise BenchmarkError("Paid run not confirmed; pass --confirm-paid-run to proceed")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="benchmark_context_quality.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_paths(command: argparse.ArgumentParser, *, include_models: bool = True) -> None:
        command.add_argument("--cases", default="benchmark/cases.local.yaml")
        if include_models:
            command.add_argument("--models", default="benchmark/models.local.yaml")
        command.add_argument("--private-root", default="benchmark/private.local")
        command.add_argument("--artifacts-root", default="benchmark/artifacts.local")

    preflight = subparsers.add_parser(
        "preflight", help="Validate privacy, corpus, tools, and models"
    )
    add_paths(preflight)
    prepare = subparsers.add_parser("prepare", help="Safely mask and explicitly index cases")
    add_paths(prepare, include_models=False)
    prepare.add_argument("--workspaces-root", default="benchmark/workspaces.local")
    retrieve = subparsers.add_parser("retrieve", help="Retrieve all four context conditions")
    add_paths(retrieve, include_models=False)
    retrieve.add_argument("--stage", required=True, choices=("pilot", "confirmation"))
    retrieve.add_argument(
        "--limit-cases", type=_positive_int, help="Retrieval dry-run limit; use 2 first"
    )
    retrieval_report = subparsers.add_parser(
        "retrieval-report", help="Aggregate anonymized candidate diagnostics without model calls"
    )
    add_paths(retrieval_report, include_models=False)
    retrieval_report.add_argument("--stage", required=True, choices=("pilot", "confirmation"))
    retrieval_report.add_argument("--anonymized", action="store_true", required=True)
    retrieval_report.add_argument(
        "--limit-cases", type=_positive_int, help="Bounded retrieval diagnostic case set"
    )
    retrieval_report.add_argument(
        "--source-code-revision",
        help="Reanalyze exact historical retrieval artifacts",
    )
    retrieval_report.add_argument("--output")
    generate = subparsers.add_parser("generate", help="Run paid generators")
    add_paths(generate)
    generate.add_argument("--stage", required=True, choices=("pilot", "confirmation"))
    generate.add_argument("--confirm-paid-run", action="store_true")
    generate.add_argument("--limit-repetitions", type=int, choices=(1, 2, 3))
    generate.add_argument("--limit-cases", type=_positive_int, help="Bounded smoke-test case count")
    judge = subparsers.add_parser("judge", help="Run paid blinded dual-model judges")
    add_paths(judge)
    judge.add_argument("--stage", required=True, choices=("pilot", "confirmation"))
    judge.add_argument("--confirm-paid-run", action="store_true")
    judge.add_argument("--limit-repetitions", type=int, choices=(1, 2, 3))
    judge.add_argument("--limit-cases", type=_positive_int, help="Bounded smoke-test case count")
    report = subparsers.add_parser("report", help="Aggregate paired case/run results")
    add_paths(report)
    report.add_argument("--stage", required=True, choices=("pilot", "confirmation"))
    report.add_argument("--anonymized", action="store_true", required=True)
    report.add_argument("--limit-repetitions", type=int, choices=(1, 2, 3))
    report.add_argument(
        "--limit-cases", type=_positive_int, help="Report a bounded smoke-test case set"
    )
    report.add_argument(
        "--source-code-revision",
        help="Reanalyze exact historical artifacts without rerunning model calls",
    )
    report.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_cli_parser().parse_args(argv)
    cases_path = Path(args.cases).resolve()
    private_root = Path(args.private_root).resolve()
    artifacts = ArtifactStore(Path(args.artifacts_root).resolve())
    try:
        if args.command == "preflight":
            result = run_preflight(cases_path, Path(args.models).resolve())
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        cases = load_cases(cases_path)
        if args.command == "prepare":
            for case in cases:
                metadata = prepare_case(
                    case,
                    workspaces_root=Path(args.workspaces_root).resolve(),
                    private_root=private_root,
                    indexer=RTFMExplicitFileIndexer(),
                )
                print(
                    f"Prepared {case.id}: {len(metadata['allowed_files'])} explicit files, "
                    f"{len(metadata['leakage_audit']['matches'])} long-overlap flags"
                )
            return 0
        if args.command == "retrieve":
            records = run_retrieval(
                cases,
                stage=args.stage,
                private_root=private_root,
                artifacts=artifacts,
                backend=ProductionRetrievalBackend(),
                limit_cases=args.limit_cases,
            )
            print(f"Retrieval artifacts ready: {len(records)}")
            return 0
        if args.command == "retrieval-report":
            report_value = build_retrieval_diagnostic_report(
                cases,
                stage=args.stage,
                private_root=private_root,
                artifacts=artifacts,
                source_code_revision=args.source_code_revision,
                limit_cases=args.limit_cases,
            )
            output = (
                Path(args.output).resolve()
                if args.output
                else Path(f"benchmark/anonymized_aggregates/{args.stage}-retrieval.json").resolve()
            )
            _atomic_json(output, report_value)
            print(f"Anonymized retrieval report written to {output}")
            return 0
        models = load_models(Path(args.models).resolve())
        repetitions = args.limit_repetitions or models.repetitions
        if args.command == "generate":
            count = generation_request_count(
                cases,
                models,
                stage=args.stage,
                repetitions=repetitions,
                limit_cases=args.limit_cases,
            )
            _confirm_paid_run(args.confirm_paid_run, count, "generation")
            records = run_generation(
                cases,
                models,
                stage=args.stage,
                private_root=private_root,
                artifacts=artifacts,
                repetitions=repetitions,
                limit_cases=args.limit_cases,
            )
            print(f"Generation artifacts ready: {len(records)}")
            return 0
        if args.command == "judge":
            count = judgment_request_count(
                cases,
                models,
                stage=args.stage,
                repetitions=repetitions,
                limit_cases=args.limit_cases,
            )
            _confirm_paid_run(args.confirm_paid_run, count, "judgment")
            records = run_judging(
                cases,
                models,
                stage=args.stage,
                private_root=private_root,
                artifacts=artifacts,
                repetitions=repetitions,
                limit_cases=args.limit_cases,
            )
            print(f"Judgment artifacts ready: {len(records)}")
            return 0
        if args.command == "report":
            report_value = build_report(
                cases,
                models,
                stage=args.stage,
                private_root=private_root,
                artifacts=artifacts,
                repetitions=repetitions,
                source_code_revision=args.source_code_revision,
                limit_cases=args.limit_cases,
            )
            output = (
                Path(args.output).resolve()
                if args.output
                else Path(f"benchmark/anonymized_aggregates/{args.stage}.json").resolve()
            )
            _atomic_json(output, report_value)
            print(f"Anonymized report written to {output}")
            return 0
    except BenchmarkError as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
