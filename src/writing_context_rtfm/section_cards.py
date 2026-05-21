"""Section cards schema and loading."""
import os
import yaml
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

@dataclass(frozen=True)
class DocumentCard:
    title: Optional[str] = None
    thesis: Optional[str] = None
    writing_style: Optional[Dict[str, object]] = None
    terminology: Optional[Dict[str, Any]] = None

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
    
    terminology_raw = doc_data.get("terminology") or {}
    terminology = {}
    if isinstance(terminology_raw, dict):
        for term, val in terminology_raw.items():
            if isinstance(val, str):
                terminology[term] = {
                    "definition": val,
                    "variants": [],
                    "avoid": []
                }
            elif isinstance(val, dict):
                terminology[term] = {
                    "definition": val.get("definition") or "",
                    "variants": val.get("variants") or [],
                    "avoid": val.get("avoid") or []
                }

    document = DocumentCard(
        title=doc_data.get("title"),
        thesis=doc_data.get("thesis"),
        writing_style=doc_data.get("writing_style"),
        terminology=terminology
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

    Returns a list of human-readable warnings (e.g., broken depends_on refs). 
    The empty list means everything is consistent.
    """
    warnings: List[str] = []
    if not cards or not cards.sections:
        return warnings

    section_ids = set(cards.sections)

    for sid, card in cards.sections.items():
        for dep in card.depends_on or []:
            if dep not in section_ids:
                warnings.append(
                    f"Section '{sid}' depends_on unknown section '{dep}'. "
                    "Query expansion will skip this dependency."
                )

    return warnings
