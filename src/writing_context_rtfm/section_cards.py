"""Section cards schema and loading."""
import os
import yaml
from dataclasses import dataclass
from typing import Dict, List, Optional

@dataclass(frozen=True)
class DocumentCard:
    title: Optional[str] = None
    thesis: Optional[str] = None
    writing_style: Optional[Dict[str, object]] = None

@dataclass(frozen=True)
class SectionCard:
    id: str
    title: Optional[str] = None
    role: Optional[str] = None
    path: Optional[str] = None
    key_terms: Optional[List[str]] = None
    depends_on: Optional[List[str]] = None
    must_preserve: Optional[List[str]] = None
    avoid: Optional[List[str]] = None
    constraints: Optional[List[str]] = None

@dataclass(frozen=True)
class SectionCards:
    version: int
    document: DocumentCard
    sections: Dict[str, SectionCard]

def load_section_cards(path: str = ".writing-context/section_cards.yaml", required: bool = False) -> Optional[SectionCards]:
    if not os.path.exists(path):
        if required:
            raise FileNotFoundError(f"Section cards file {path} not found.")
        return None
        
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    doc_data = data.get("document", {})
    document = DocumentCard(
        title=doc_data.get("title"),
        thesis=doc_data.get("thesis"),
        writing_style=doc_data.get("writing_style")
    )
    
    sections = {}
    for sec_id, sec_data in data.get("sections", {}).items():
        sections[sec_id] = SectionCard(
            id=sec_id,
            title=sec_data.get("title"),
            role=sec_data.get("role"),
            path=sec_data.get("path"),
            key_terms=sec_data.get("key_terms"),
            depends_on=sec_data.get("depends_on"),
            must_preserve=sec_data.get("must_preserve"),
            avoid=sec_data.get("avoid"),
            constraints=sec_data.get("constraints")
        )
        
    return SectionCards(
        version=data.get("version", 1),
        document=document,
        sections=sections
    )


def validate_section_cards(cards: "SectionCards") -> List[str]:
    """Check section cards for self-consistency.

    Returns a list of human-readable warnings (broken depends_on refs, duplicate
    paths, missing target files). The empty list means everything is consistent.
    File existence is checked only when the path is relative — absolute paths
    pointing outside the project are not probed.
    """
    warnings: List[str] = []
    if not cards or not cards.sections:
        return warnings

    section_ids = set(cards.sections)
    seen_paths: Dict[str, str] = {}

    for sid, card in cards.sections.items():
        for dep in card.depends_on or []:
            if dep not in section_ids:
                warnings.append(
                    f"Section '{sid}' depends_on unknown section '{dep}'. "
                    "Query expansion will skip this dependency."
                )
        if card.path:
            prior = seen_paths.get(card.path)
            if prior and prior != sid:
                warnings.append(
                    f"Sections '{prior}' and '{sid}' share the same path '{card.path}'. "
                    "Path-based scoring may be ambiguous."
                )
            seen_paths[card.path] = sid
            if not os.path.isabs(card.path) and not os.path.exists(card.path):
                warnings.append(
                    f"Section '{sid}' path '{card.path}' does not exist on disk. "
                    "Target-file boosts won't fire for this section."
                )

    return warnings
