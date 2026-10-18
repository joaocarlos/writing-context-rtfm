"""Section cards schema, loading, merging, and migration."""

import json
import os
from dataclasses import dataclass
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
    verified_facts: list[dict[str, Any]] | None = None
    unverified_key_terms: list[str] | None = None
    unverified_dependencies: list[str] | None = None
    selector: str | None = None


@dataclass(frozen=True)
class SectionCards:
    version: int
    document: DocumentCard
    sections: dict[str, SectionCard]


def normalize_terminology(raw: Any) -> dict[str, dict[str, Any]]:
    """Normalize legacy and structured glossary entries to one stable shape."""
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for raw_term, raw_details in raw.items():
        term = str(raw_term).strip()
        if not term:
            continue
        if isinstance(raw_details, str):
            definition = raw_details
            variants: list[str] = []
            avoid: list[str] = []
        elif isinstance(raw_details, dict):
            definition = str(raw_details.get("definition") or "")
            variants = [
                str(value).strip()
                for value in (raw_details.get("variants") or [])
                if str(value).strip()
            ]
            avoid = [
                str(value).strip()
                for value in (raw_details.get("avoid") or [])
                if str(value).strip()
            ]
        else:
            continue
        normalized[term] = {
            "definition": definition,
            "variants": list(dict.fromkeys(variants)),
            "avoid": list(dict.fromkeys(avoid)),
        }
    return normalized


def _merge_terminology(generated: Any, overrides: Any) -> dict[str, dict[str, Any]]:
    """Merge glossary entries while treating author overrides as canonical."""
    merged = normalize_terminology(generated)
    canonical_keys = {term.casefold(): term for term in merged}

    for override_term, override_details in normalize_terminology(overrides).items():
        previous_term = canonical_keys.get(override_term.casefold())
        previous = merged.pop(previous_term, None) if previous_term else None
        if previous:
            definition = override_details["definition"] or previous["definition"]
            variants = list(dict.fromkeys([*previous["variants"], *override_details["variants"]]))
            avoid = list(dict.fromkeys([*previous["avoid"], *override_details["avoid"]]))
            merged[override_term] = {
                "definition": definition,
                "variants": variants,
                "avoid": avoid,
            }
        else:
            merged[override_term] = override_details
        canonical_keys[override_term.casefold()] = override_term
    return merged


def get_section_cards_files(
    path: str | Path = ".writing-context/section_cards.yaml",
) -> list[Path]:
    """Return all candidate files that make up the section cards state (legacy and v2 split)."""
    path_obj = Path(path)
    parent = path_obj.parent
    candidates = [
        path_obj,
        parent / "cards.generated.yaml",
        parent / "cards.overrides.yaml",
        parent / "cards.lock.json",
    ]
    seen: set[Path] = set()
    result: list[Path] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


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

        overrides: dict[str, Any] = {
            "version": 2,
            "document": legacy_data.get("document") or {},
            "sections": {},
        }

        legacy_sections = legacy_data.get("sections") or {}
        for sid, sdata in legacy_sections.items():
            sdata = sdata or {}
            sec_entry: dict[str, Any] = {
                "title": sdata.get("title"),
                "purpose": sdata.get("role") or sdata.get("purpose"),
                "key_terms": sdata.get("key_terms"),
                "depends_on": sdata.get("depends_on"),
                "must_preserve": sdata.get("must_preserve"),
                "avoid": sdata.get("avoid"),
                "constraints": sdata.get("constraints"),
            }
            if sdata.get("path"):
                sec_entry["path"] = sdata.get("path")
            if sdata.get("selector"):
                sec_entry["selector"] = sdata.get("selector")
            overrides["sections"][sid] = sec_entry

        # Save overrides
        overrides_path.parent.mkdir(exist_ok=True)
        with open(overrides_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(overrides, f, sort_keys=False)

        # Create lock
        lock: dict[str, Any] = {"generation_version": 2, "extractor_version": 1, "sections": {}}
        for sid in legacy_sections:
            lock["sections"][sid] = {"content_hash": "", "decisions": {}, "stale_fields": []}

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


def _merge_split_cards(
    generated: dict[str, Any], overrides: dict[str, Any], lock: dict[str, Any]
) -> SectionCards:
    """Merges cards.generated.yaml, cards.overrides.yaml, and cards.lock.json into SectionCards."""
    gen_doc = (generated.get("document") if isinstance(generated, dict) else None) or {}
    over_doc = (overrides.get("document") if isinstance(overrides, dict) else None) or {}

    doc_title = over_doc.get("title") or gen_doc.get("title")
    doc_thesis = over_doc.get("thesis") or gen_doc.get("thesis")
    doc_style = over_doc.get("writing_style") or gen_doc.get("writing_style")
    doc_term = _merge_terminology(gen_doc.get("terminology"), over_doc.get("terminology"))

    document = DocumentCard(
        title=doc_title, thesis=doc_thesis, writing_style=doc_style, terminology=doc_term
    )

    sections: dict[str, SectionCard] = {}
    gen_sections = (generated.get("sections") if isinstance(generated, dict) else None) or {}
    over_sections = (overrides.get("sections") if isinstance(overrides, dict) else None) or {}
    lock_sections = (lock.get("sections") if isinstance(lock, dict) else None) or {}

    all_section_ids = set(gen_sections.keys()).union(set(over_sections.keys()))

    for sid in all_section_ids:
        gs = gen_sections.get(sid) or {}
        os_data = over_sections.get(sid) or {}
        sec_lock = lock_sections.get(sid) or {}
        sec_decisions = (
            (sec_lock.get("decisions") if isinstance(sec_lock, dict) else None) or {}
        )

        structure_obj = gs.get("structure") or {}
        identity_obj = gs.get("identity") or {}
        title = (
            os_data.get("title")
            or (structure_obj.get("title") if isinstance(structure_obj, dict) else None)
            or sid.replace("section_", "").replace("_", " ").title()
        )
        path = os_data.get("path") or (
            identity_obj.get("source") if isinstance(identity_obj, dict) else None
        )
        selector = os_data.get("selector") or (
            identity_obj.get("selector") if isinstance(identity_obj, dict) else None
        )

        purpose_obj = gs.get("purpose") or {}
        purpose_status = sec_decisions.get("purpose") or (
            purpose_obj.get("status", "generated")
            if isinstance(purpose_obj, dict)
            else "generated"
        )
        gen_purpose = (
            purpose_obj.get("value")
            if isinstance(purpose_obj, dict) and purpose_status != "rejected"
            else None
        )
        role_obj = gs.get("rhetorical_role") or {}
        gen_role = role_obj.get("value") if isinstance(role_obj, dict) else None
        purpose = os_data.get("purpose") or gen_purpose or os_data.get("role") or gen_role

        unverified_key_terms: list[str] = []
        key_terms: list[str] = []
        over_kts = os_data.get("key_terms")
        over_kt_set: set[str] = set()
        if isinstance(over_kts, list):
            for item in over_kts:
                if item and str(item) not in key_terms:
                    key_terms.append(str(item))
                    over_kt_set.add(str(item))
        elif isinstance(over_kts, dict):
            for k in over_kts:
                if k and str(k) not in key_terms:
                    key_terms.append(str(k))
                    over_kt_set.add(str(k))

        for kt in gs.get("key_terms") or []:
            val = kt.get("value") if isinstance(kt, dict) else kt
            if not val:
                continue
            status = (
                sec_decisions.get(f"key_terms:{val}")
                or (kt.get("status") if isinstance(kt, dict) else None)
                or "generated"
            )
            if status != "rejected":
                if val not in key_terms:
                    key_terms.append(val)
                if status not in ("accepted", "verified", "override") and val not in over_kt_set:
                    unverified_key_terms.append(val)
            elif val in key_terms and val not in over_kt_set:
                key_terms.remove(val)

        unverified_dependencies: list[str] = []
        over_deps = os_data.get("depends_on")
        if isinstance(over_deps, list) and len(over_deps) > 0:
            depends_on = list(over_deps)
        else:
            depends_on = []
            for dep in gs.get("dependencies") or []:
                target = dep.get("target") if isinstance(dep, dict) else dep
                if not target:
                    continue
                status = (
                    sec_decisions.get(f"dependencies:{target}")
                    or (dep.get("status") if isinstance(dep, dict) else None)
                    or "generated"
                )
                if status != "rejected":
                    if target not in depends_on:
                        depends_on.append(target)
                    if status not in ("accepted", "verified", "override"):
                        unverified_dependencies.append(target)

        must_preserve = list(os_data.get("must_preserve") or [])
        verified_facts: list[dict[str, Any]] = []

        # User-overridden must_preserve are verified by definition
        for p in must_preserve:
            verified_facts.append({"value": p, "status": "override", "source": sid})

        for fact in gs.get("facts") or []:
            val = fact.get("value") if isinstance(fact, dict) else fact
            if not val:
                continue
            status = (
                sec_decisions.get(f"facts:{val}")
                or (fact.get("status") if isinstance(fact, dict) else None)
                or "generated"
            )
            if status in ("accepted", "verified") and val:
                if val not in must_preserve:
                    must_preserve.append(val)
                if not any(vf["value"] == val for vf in verified_facts):
                    verified_facts.append({"value": val, "status": status, "source": sid})

        avoid = list(os_data.get("avoid") or [])
        for constraint in gs.get("constraints") or []:
            val = constraint.get("value") if isinstance(constraint, dict) else constraint
            if not val:
                continue
            c_type = constraint.get("type") if isinstance(constraint, dict) else ""
            status = (
                sec_decisions.get(f"constraints:{val}")
                or (constraint.get("status") if isinstance(constraint, dict) else None)
                or "generated"
            )
            if (
                status in ("accepted", "verified")
                and c_type in ("terminology_avoidance", "prohibited_claims")
                and val
                and val not in avoid
            ):
                avoid.append(val)

        constraints = list(os_data.get("constraints") or [])
        for se in os_data.get("scope_exclusions") or []:
            if se not in constraints:
                constraints.append(se)
        for tp in os_data.get("terminology_preferences") or []:
            if tp not in constraints:
                constraints.append(tp)
        for constraint in gs.get("constraints") or []:
            val = constraint.get("value") if isinstance(constraint, dict) else constraint
            if not val:
                continue
            status = (
                sec_decisions.get(f"constraints:{val}")
                or (constraint.get("status") if isinstance(constraint, dict) else None)
                or "generated"
            )
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
            constraints=constraints,
            verified_facts=verified_facts,
            unverified_key_terms=unverified_key_terms,
            unverified_dependencies=unverified_dependencies,
            selector=selector,
        )

    version_val = (
        (generated.get("version") if isinstance(generated, dict) else None)
        or (overrides.get("version") if isinstance(overrides, dict) else None)
        or 2
    )
    return SectionCards(version=version_val, document=document, sections=sections)


merge_cards = _merge_split_cards


def load_section_cards(
    path: str = ".writing-context/section_cards.yaml", required: bool = False
) -> SectionCards | None:
    """Loads section cards, transparently supporting both legacy yaml and new split structure."""
    path_obj = Path(path)
    parent = path_obj.parent

    generated_path = parent / "cards.generated.yaml"
    overrides_path = parent / "cards.overrides.yaml"
    lock_path = parent / "cards.lock.json"

    if generated_path.exists() or overrides_path.exists():
        try:
            gen_data: dict[str, Any] = {}
            if generated_path.exists():
                with open(generated_path, encoding="utf-8") as f:
                    gen_data = yaml.safe_load(f) or {}

            overrides_data: dict[str, Any] = {}
            if overrides_path.exists():
                with open(overrides_path, encoding="utf-8") as f:
                    overrides_data = yaml.safe_load(f) or {}

            lock_data: dict[str, Any] = {}
            if lock_path.exists():
                with open(lock_path, encoding="utf-8") as f:
                    lock_data = json.load(f) or {}

            return _merge_split_cards(gen_data, overrides_data, lock_data)
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
    terminology = normalize_terminology(doc_data.get("terminology"))

    document = DocumentCard(
        title=doc_data.get("title"),
        thesis=doc_data.get("thesis"),
        writing_style=doc_data.get("writing_style"),
        terminology=terminology,
    )

    sections = {}
    for sec_id, sec_data in data.get("sections", {}).items():
        mp = list(sec_data.get("must_preserve") or [])
        vf = [{"value": val, "status": "verified", "source": sec_id} for val in mp]
        sec_selector = (
            sec_data.get("selector")
            or (sec_data.get("identity", {}).get("selector") if isinstance(sec_data.get("identity"), dict) else None)
        )
        sections[sec_id] = SectionCard(
            id=sec_id,
            title=sec_data.get("title"),
            role=sec_data.get("role"),
            path=sec_data.get("path"),
            key_terms=sec_data.get("key_terms"),
            depends_on=sec_data.get("depends_on"),
            must_preserve=mp,
            avoid=sec_data.get("avoid"),
            constraints=sec_data.get("constraints"),
            verified_facts=vf,
            unverified_key_terms=[],
            unverified_dependencies=[],
            selector=sec_selector,
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

    # Build known aliases for sections: selector -> sid, path stem/name -> sid, title -> sid
    known_aliases: dict[str, str] = {}
    for sid, card in cards.sections.items():
        if card.selector:
            known_aliases[card.selector] = sid
            known_aliases[card.selector.lstrip("/")] = sid
        if card.path:
            p = Path(card.path)
            known_aliases[card.path] = sid
            known_aliases[p.name] = sid
            known_aliases[p.stem] = sid
        if card.title:
            known_aliases[card.title.lower()] = sid

    for sid, card in cards.sections.items():
        for dep in card.depends_on or []:
            if dep in section_ids:
                continue
            if dep in known_aliases:
                continue
            # Intra-document asset labels (equations, figures, tables, code listings) are not sections
            if dep.startswith(("eq:", "fig:", "tab:", "lst:")):
                continue
            warnings.append(
                f"Section '{sid}' depends_on unknown section '{dep}'. "
                "Query expansion will skip this dependency."
            )

    return warnings
