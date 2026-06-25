"""Section cards schema, loading, merging, and migration."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class DocumentCard:
    title: str | None = None
    thesis: str | None = None
    writing_style: dict[str, object] | None = None
    terminology: dict[str, Any] | None = None


@dataclass(frozen=True)
class SectionCard:
    id: str
    title: str | None = None
    role: str | None = None
    path: str | None = None
    key_terms: list[str] | None = None
    depends_on: list[str] | None = None
    must_preserve: list[str] | None = None
    avoid: list[str] | None = None
    constraints: list[str] | None = None


@dataclass(frozen=True)
class SectionCards:
    version: int
    document: DocumentCard
    sections: dict[str, SectionCard]


def migrate_legacy_cards(project_root: str) -> bool:
    """Migrates legacy section_cards.yaml to split cards structure."""
    root = Path(project_root).resolve()
    legacy_path = root / ".writing-context" / "section_cards.yaml"
    generated_path = root / ".writing-context" / "cards.generated.yaml"
    overrides_path = root / ".writing-context" / "cards.overrides.yaml"
    lock_path = root / ".writing-context" / "cards.lock.json"

    if legacy_path.exists() and not generated_path.exists():
        try:
            with open(legacy_path, encoding="utf-8") as f:
                legacy_data = yaml.safe_load(f) or {}
        except Exception:
            return False

        overrides = {
            "version": 2,
            "document": legacy_data.get("document", {}),
            "sections": {}
        }
        
        legacy_sections = legacy_data.get("sections", {})
        for sid, sdata in legacy_sections.items():
            overrides["sections"][sid] = {
                "title": sdata.get("title"),
                "purpose": sdata.get("role"),
                "key_terms": sdata.get("key_terms"),
                "depends_on": sdata.get("depends_on"),
                "must_preserve": sdata.get("must_preserve"),
                "avoid": sdata.get("avoid"),
                "constraints": sdata.get("constraints"),
            }

        # Save overrides
        overrides_path.parent.mkdir(exist_ok=True)
        with open(overrides_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(overrides, f, sort_keys=False)

        # Create lock
        lock = {
            "generation_version": 2,
            "extractor_version": 1,
            "sections": {}
        }
        for sid in legacy_sections:
            lock["sections"][sid] = {
                "content_hash": "",
                "decisions": {},
                "stale_fields": []
            }
        
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump(lock, f, indent=2)

        # Backup legacy file
        backup_path = legacy_path.with_suffix(".yaml.backup")
        try:
            if backup_path.exists():
                os.remove(backup_path)
            legacy_path.rename(backup_path)
        except Exception:
            pass

        return True
    return False


def merge_cards(generated: dict[str, Any], overrides: dict[str, Any], lock: dict[str, Any]) -> SectionCards:
    """Merges generated cards, overrides and lock data into legacy SectionCards format."""
    doc_gen = generated.get("document", {}) or {}
    doc_over = overrides.get("document", {}) or {}
    
    doc_title = doc_over.get("title") or doc_gen.get("title") or "My Manuscript"
    doc_thesis = doc_over.get("thesis") or doc_gen.get("thesis")
    doc_writing_style = doc_over.get("writing_style") or doc_gen.get("writing_style")
    
    terminology = {}
    term_gen = doc_gen.get("terminology") or {}
    term_over = doc_over.get("terminology") or {}
    all_terms = set(term_gen.keys()) | set(term_over.keys())
    for term in all_terms:
        details_gen = term_gen.get(term) or {}
        details_over = term_over.get(term) or {}
        
        if isinstance(details_over, str):
            details_over = {"definition": details_over}
        if isinstance(details_gen, str):
            details_gen = {"definition": details_gen}
            
        terminology[term] = {
            "definition": details_over.get("definition") or details_gen.get("definition") or "",
            "variants": list(set(details_over.get("variants", []) or []) | set(details_gen.get("variants", []) or [])),
            "avoid": list(set(details_over.get("avoid", []) or []) | set(details_gen.get("avoid", []) or [])),
        }

    document = DocumentCard(
        title=doc_title,
        thesis=doc_thesis,
        writing_style=doc_writing_style,
        terminology=terminology
    )

    sections = {}
    gen_sections = generated.get("sections", {}) or {}
    over_sections = overrides.get("sections", {}) or {}

    all_section_ids = set(gen_sections.keys()) | set(over_sections.keys())

    for sid in all_section_ids:
        gs = gen_sections.get(sid, {}) or {}
        os_data = over_sections.get(sid, {}) or {}

        title = os_data.get("title") or gs.get("structure", {}).get("title") or sid.replace("section_", "").replace("_", " ").title()
        path = gs.get("identity", {}).get("source") or os_data.get("path")
        purpose = os_data.get("purpose") or gs.get("purpose", {}).get("value") or os_data.get("role") or gs.get("rhetorical_role", {}).get("value")
        
        if "key_terms" in os_data and os_data["key_terms"] is not None:
            key_terms = os_data["key_terms"]
        else:
            key_terms = []
            for kt in gs.get("key_terms", []):
                val = kt.get("value") if isinstance(kt, dict) else kt
                status = kt.get("status") if isinstance(kt, dict) else "generated"
                if status != "rejected" and val:
                    key_terms.append(val)

        if "depends_on" in os_data and os_data["depends_on"] is not None:
            depends_on = os_data["depends_on"]
        else:
            depends_on = []
            for dep in gs.get("dependencies", []):
                target = dep.get("target") if isinstance(dep, dict) else dep
                status = dep.get("status") if isinstance(dep, dict) else "generated"
                if status != "rejected" and target:
                    depends_on.append(target)

        must_preserve = list(os_data.get("must_preserve") or [])
        for fact in gs.get("facts", []):
            val = fact.get("value") if isinstance(fact, dict) else fact
            status = fact.get("status") if isinstance(fact, dict) else "generated"
            if status in ("accepted", "verified") and val and val not in must_preserve:
                must_preserve.append(val)

        avoid = list(os_data.get("avoid") or [])
        for constraint in gs.get("constraints", []):
            val = constraint.get("value") if isinstance(constraint, dict) else constraint
            c_type = constraint.get("type") if isinstance(constraint, dict) else ""
            status = constraint.get("status") if isinstance(constraint, dict) else "generated"
            if status in ("accepted", "verified") and c_type in ("terminology_avoidance", "prohibited_claims") and val and val not in avoid:
                avoid.append(val)

        constraints = list(os_data.get("constraints") or [])
        for se in os_data.get("scope_exclusions", []):
            if se not in constraints:
                constraints.append(se)
        for tp in os_data.get("terminology_preferences", []):
            if tp not in constraints:
                constraints.append(tp)
        for constraint in gs.get("constraints", []):
            val = constraint.get("value") if isinstance(constraint, dict) else constraint
            status = constraint.get("status") if isinstance(constraint, dict) else "generated"
            if status in ("accepted", "verified") and val and val not in constraints:
                constraints.append(val)

        sections[sid] = SectionCard(
            id=sid,
            title=title,
            role=purpose,
            path=path,
            key_terms=key_terms,
            depends_on=depends_on,
            must_preserve=must_preserve,
            avoid=avoid,
            constraints=constraints
        )

    return SectionCards(version=generated.get("version", 2), document=document, sections=sections)


def load_section_cards(
    path: str = ".writing-context/section_cards.yaml", required: bool = False
) -> SectionCards | None:
    """Loads section cards, transparently supporting both legacy yaml and new split structure."""
    path_obj = Path(path)
    parent = path_obj.parent
    
    generated_path = parent / "cards.generated.yaml"
    overrides_path = parent / "cards.overrides.yaml"
    lock_path = parent / "cards.lock.json"
    
    if generated_path.exists():
        try:
            with open(generated_path, encoding="utf-8") as f:
                gen_data = yaml.safe_load(f) or {}
            
            overrides_data = {}
            if overrides_path.exists():
                with open(overrides_path, encoding="utf-8") as f:
                    overrides_data = yaml.safe_load(f) or {}
            
            lock_data = {}
            if lock_path.exists():
                with open(lock_path, encoding="utf-8") as f:
                    lock_data = json.load(f) or {}
            
            return merge_cards(gen_data, overrides_data, lock_data)
        except Exception:
            pass

    # Legacy file fallback
    if not os.path.exists(path):
        if required:
            raise FileNotFoundError(f"Section cards file {path} not found.")
        return None

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    doc_data = data.get("document", {})
    terminology_raw = doc_data.get("terminology") or {}
    terminology = {}
    if isinstance(terminology_raw, dict):
        for term, val in terminology_raw.items():
            if isinstance(val, str):
                terminology[term] = {"definition": val, "variants": [], "avoid": []}
            elif isinstance(val, dict):
                terminology[term] = {
                    "definition": val.get("definition") or "",
                    "variants": val.get("variants") or [],
                    "avoid": val.get("avoid") or [],
                }

    document = DocumentCard(
        title=doc_data.get("title"),
        thesis=doc_data.get("thesis"),
        writing_style=doc_data.get("writing_style"),
        terminology=terminology,
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
            constraints=sec_data.get("constraints"),
        )

    return SectionCards(version=data.get("version", 1), document=document, sections=sections)


def validate_section_cards(cards: "SectionCards") -> list[str]:
    """Check section cards for self-consistency.

    Returns a list of human-readable warnings (e.g., broken depends_on refs).
    The empty list means everything is consistent.
    """
    warnings: list[str] = []
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
