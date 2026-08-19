"""Offline BibTeX Literature Provider for repository-local .bib files."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from writing_context_rtfm.config import AppConfig
from writing_context_rtfm.providers.base import BaseContextProvider
from writing_context_rtfm.schemas import SourceSpan

logger = logging.getLogger("mcp-server")


@dataclass
class BibEntry:
    entry_type: str
    citekey: str
    fields: dict[str, str] = field(default_factory=dict)
    raw: str = ""

    @property
    def title(self) -> str:
        return self.fields.get("title", "")

    @property
    def author(self) -> str:
        return self.fields.get("author", "")

    @property
    def year(self) -> str:
        return self.fields.get("year", "")

    @property
    def abstract(self) -> str:
        return self.fields.get("abstract", "")

    def format_snippet(self) -> str:
        """Format entry into a clean, markdown-formatted citation snippet."""
        lines = [f"## {self.title or self.citekey}"]
        lines.append(f"**Citation Key:** `{self.citekey}` ({self.entry_type})")
        if self.author:
            lines.append(f"**Authors:** {self.author}")
        if self.year:
            lines.append(f"**Year:** {self.year}")
        venue = (
            self.fields.get("journal")
            or self.fields.get("booktitle")
            or self.fields.get("publisher")
        )
        if venue:
            lines.append(f"**Venue:** {venue}")
        if self.fields.get("doi"):
            lines.append(f"**DOI:** {self.fields['doi']}")
        if self.abstract:
            lines.append(f"\n### Abstract\n{self.abstract}")
        return "\n".join(lines)


def parse_bibtex_file(file_path: Path) -> dict[str, BibEntry]:
    """Parse a .bib file into a dictionary of citekey -> BibEntry."""
    entries: dict[str, BibEntry] = {}
    if not file_path.is_file():
        return entries

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"Failed to read BibTeX file {file_path}: {e}")
        return entries

    # Match @type{citekey, ... }
    # Use regex scanning across entries
    pattern = re.compile(
        r"@(?P<type>[a-zA-Z]+)\s*\{\s*(?P<key>[^,\s]+)\s*,\s*(?P<body>.*?)\n\s*\}",
        re.DOTALL,
    )

    for match in pattern.finditer(content):
        entry_type = match.group("type").strip().lower()
        if entry_type in ("comment", "preamble", "string"):
            continue
        citekey = match.group("key").strip()
        body = match.group("body")

        fields: dict[str, str] = {}
        # Parse fields: name = {value} or name = "value" or name = 123
        field_pattern = re.compile(
            r"(?P<name>[a-zA-Z0-9_\-]+)\s*=\s*(?:\{(?P<val_brace>.*?)\}|\"(?P<val_quote>.*?)\"|(?P<val_bare>[^,]+))(?:\s*,|\s*$)",
            re.DOTALL,
        )
        for fmatch in field_pattern.finditer(body):
            fname = fmatch.group("name").strip().lower()
            fval = (
                fmatch.group("val_brace")
                if fmatch.group("val_brace") is not None
                else fmatch.group("val_quote")
                if fmatch.group("val_quote") is not None
                else fmatch.group("val_bare") or ""
            )
            clean_val = re.sub(r"\s+", " ", fval).strip()
            fields[fname] = clean_val

        entries[citekey] = BibEntry(
            entry_type=entry_type,
            citekey=citekey,
            fields=fields,
            raw=match.group(0),
        )

    return entries


class BibTeXProvider(BaseContextProvider):
    """Native offline provider that resolves references from local .bib files."""

    def __init__(self, config: AppConfig):
        self.config = config

    @property
    def provider_id(self) -> str:
        return "bibtex"

    def is_available(self, config: AppConfig) -> bool:
        provider_cfg = config.providers.get("bibtex")
        if provider_cfg is not None and not provider_cfg.enabled:
            return False
        # If enabled or not configured, check if any .bib file exists in project_root
        root = Path(config.rtfm.project_root)
        if not root.is_dir():
            return False
        try:
            return any(root.glob("*.bib")) or any(root.glob("**/*.bib"))
        except Exception:
            return False

    def _find_bib_files(self) -> list[Path]:
        root = Path(self.config.rtfm.project_root)
        provider_cfg = self.config.providers.get("bibtex")
        extra = (provider_cfg.extra or {}) if provider_cfg else {}
        custom_paths = extra.get("bib_files", [])

        files: list[Path] = []
        if custom_paths:
            for p in custom_paths:
                abs_p = Path(p) if Path(p).is_absolute() else (root / p)
                if abs_p.is_file():
                    files.append(abs_p)
            return files

        # Auto-discover .bib files in project root (excluding hidden dirs)
        for bib in root.rglob("*.bib"):
            if not any(part.startswith(".") for part in bib.parts):
                files.append(bib)
        return files

    def _load_all_entries(self) -> dict[str, BibEntry]:
        all_entries: dict[str, BibEntry] = {}
        for bib_file in self._find_bib_files():
            entries = parse_bibtex_file(bib_file)
            all_entries.update(entries)
        return all_entries

    def fetch_context(
        self,
        queries: list[str],
        target: str | None,
        limit: int,
        query_type_map: dict[str, str] | None = None,
        task_type: str | None = None,
    ) -> list[SourceSpan]:
        all_entries = self._load_all_entries()
        if not all_entries:
            return []

        spans: list[SourceSpan] = []
        matched_keys: set[str] = set()

        # 1. Parse citation keys from target file and dependencies
        cite_keys: list[str] = []
        target_files: list[Path] = []

        try:
            from writing_context_rtfm.section_cards import load_section_cards

            cards = load_section_cards(self.config.section_cards.path, required=False)
        except Exception:
            cards = None

        root = Path(self.config.rtfm.project_root)
        if cards and target and target in cards.sections:
            target_card = cards.sections[target]
            if target_card.path:
                target_files.append(root / target_card.path)
            for dep_id in target_card.depends_on or []:
                if dep_id in cards.sections:
                    dep_card = cards.sections[dep_id]
                    if dep_card.path:
                        target_files.append(root / dep_card.path)

        for fpath in target_files:
            if fpath.is_file():
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    # LaTeX \cite{...}
                    for match in re.findall(r"\\cite(?:[a-zA-Z]*)\{([^}]+)\}", content):
                        for k in match.split(","):
                            clean_k = k.strip()
                            if clean_k and clean_k not in cite_keys:
                                cite_keys.append(clean_k)
                    # Markdown [@key]
                    for match in re.findall(r"@([a-zA-Z0-9_\-]+)", content):
                        clean_k = match.strip()
                        if clean_k and clean_k not in cite_keys:
                            cite_keys.append(clean_k)
                except Exception as e:
                    logger.warning(f"BibTeXProvider failed to extract citations from {fpath}: {e}")

        # Resolve explicit citation keys
        for key in cite_keys:
            if key in all_entries and key not in matched_keys:
                entry = all_entries[key]
                matched_keys.add(key)
                spans.append(
                    SourceSpan(
                        path=f"bibtex:{key}",
                        line_start=None,
                        line_end=None,
                        reason=f"BibTeX reference for citation key '{key}'",
                        score=0.95,
                        priority="supporting",
                        source_role="reference",
                        metadata={"snippet": entry.format_snippet(), "citekey": key},
                    )
                )

        if task_type == "proofread":
            return spans[:limit]

        # 2. Match queries against entry titles, authors, and abstracts
        query_words: set[str] = set()
        for q in queries:
            for w in re.sub(r"[^\w\s]", " ", q.lower()).split():
                if len(w) > 3:
                    query_words.add(w)

        if query_words:
            scored_entries: list[tuple[float, str, BibEntry]] = []
            for key, entry in all_entries.items():
                if key in matched_keys:
                    continue
                searchable = (
                    f"{entry.citekey} {entry.title} {entry.author} {entry.abstract}".lower()
                )
                matches = sum(1 for w in query_words if w in searchable)
                if matches > 0:
                    score = round(min(0.9, 0.5 + (matches / max(1, len(query_words))) * 0.4), 3)
                    scored_entries.append((score, key, entry))

            scored_entries.sort(key=lambda x: x[0], reverse=True)
            for score, key, entry in scored_entries:
                if len(spans) >= limit:
                    break
                matched_keys.add(key)
                spans.append(
                    SourceSpan(
                        path=f"bibtex:{key}",
                        line_start=None,
                        line_end=None,
                        reason=f"BibTeX match for query terms in '{entry.title or key}'",
                        score=score,
                        priority="supporting",
                        source_role="reference",
                        metadata={"snippet": entry.format_snippet(), "citekey": key},
                    )
                )

        return spans[:limit]
