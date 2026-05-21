import os
import re
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List

def sanitize_id(filename: str) -> str:
    """Removes extension and replaces non-alphanumeric characters with underscores."""
    name_without_ext = Path(filename).stem
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name_without_ext).lower()
    if not sanitized.startswith("section_"):
        sanitized = f"section_{sanitized}"
    return sanitized

def initialize_section_cards(project_root: Optional[str] = None) -> Dict[str, Any]:
    """Scans the project for .tex and .md files and generates/appends to section_cards.yaml."""
    from writing_context_rtfm.config import load_config
    root = Path(project_root or ".").resolve()

    # Load config
    try:
        config = load_config(str(root))
        sc_path = Path(config.section_cards.path)
        if not sc_path.is_absolute():
            sc_path = root / sc_path
    except Exception:
        sc_path = root / ".writing-context" / "section_cards.yaml"

    sc_path.parent.mkdir(exist_ok=True)

    # Load existing section cards
    existing_cards = {
        "version": 1,
        "document": {
            "title": "My Manuscript",
            "thesis": "",
            "writing_style": {
                "tone": "academic, formal",
                "avoid": []
            }
        },
        "sections": {}
    }
    if sc_path.exists():
        try:
            with open(sc_path, "r") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    existing_cards = loaded
                    if "sections" not in existing_cards or not isinstance(existing_cards["sections"], dict):
                        existing_cards["sections"] = {}
        except Exception:
            pass

    # Scan for files (.tex, .md)
    exclude_dirs = {".git", ".venv", ".rtfm", ".writing-context", "node_modules", "__pycache__", "build", "dist"}
    found_files = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Filter directories in place
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for f in filenames:
            if f.endswith((".tex", ".md")) and f not in ("README.md", "GEMINI.md"):
                full_path = Path(dirpath) / f
                rel_path = full_path.relative_to(root)
                found_files.append(rel_path)

    added = []
    preserved = []

    # Map of rel_path to section cards in existing sections
    path_to_section_id = {}
    for sid, scard in existing_cards["sections"].items():
        if isinstance(scard, dict) and "path" in scard:
            path_to_section_id[Path(scard["path"])] = sid

    for rel_path in found_files:
        if rel_path in path_to_section_id:
            preserved.append(str(rel_path))
        else:
            base_sid = sanitize_id(rel_path.name)
            sid = base_sid
            counter = 1
            while sid in existing_cards["sections"]:
                sid = f"{base_sid}_{counter}"
                counter += 1

            title = rel_path.stem.replace("_", " ").replace("-", " ").title()
            existing_cards["sections"][sid] = {
                "title": title,
                "role": f"Draft and refine contents for {title}",
                "path": str(rel_path),
                "key_terms": [],
                "depends_on": [],
                "must_preserve": [],
                "avoid": [],
                "constraints": []
            }
            added.append({"id": sid, "path": str(rel_path)})

    # Write back
    with open(sc_path, "w") as f:
        yaml.safe_dump(existing_cards, f, sort_keys=False)

    return {
        "status": "success",
        "sc_path": str(sc_path),
        "added": added,
        "preserved_count": len(preserved),
        "total_sections": len(existing_cards["sections"])
    }

def audit_manuscript_terminology(project_root: Optional[str] = None) -> Dict[str, Any]:
    """Audits key terms from section cards against their actual occurrences in the RTFM index."""
    from writing_context_rtfm.config import load_config
    from writing_context_rtfm.section_cards import load_section_cards
    from writing_context_rtfm.rtfm_adapter import RTFMAdapter

    root = Path(project_root or ".").resolve()
    try:
        config = load_config(str(root))
    except Exception as e:
        return {"status": "error", "message": f"Config load failed: {e}"}

    sc_path = Path(config.section_cards.path)
    if not sc_path.is_absolute():
        sc_path = root / sc_path

    if not sc_path.exists():
        return {"status": "error", "message": f"Section cards file not found at '{sc_path}'"}

    section_cards = load_section_cards(str(sc_path), required=True)
    adapter = RTFMAdapter(str(root))

    # Map each section path to its declared section card
    path_to_section = {}
    term_declarations = {} # term_lower -> list of section_ids
    for sid, scard in section_cards.sections.items():
        if scard.path:
            p = Path(scard.path)
            if p.is_absolute():
                p = p.relative_to(root)
            path_to_section[str(p)] = sid

        for term in (scard.key_terms or []):
            term_lower = term.lower()
            if term_lower not in term_declarations:
                term_declarations[term_lower] = []
            term_declarations[term_lower].append(sid)

    report = {}

    for term_lower, sids in term_declarations.items():
        try:
            results = adapter.search(term_lower, corpus=config.rtfm.corpus, limit=50)
        except Exception as e:
            return {"status": "error", "message": f"RTFM search failed during audit: {e}"}

        occurrences = []
        warnings = []
        occurring_paths = set()

        for r in results:
            p = Path(r.path)
            if p.is_absolute():
                p = p.relative_to(root)
            p_str = str(p)
            occurring_paths.add(p_str)
            occurrences.append({
                "path": r.path,
                "line_start": r.line_start,
                "line_end": r.line_end,
                "snippet": r.snippet
            })

        if not occurrences:
            warnings.append(f"Term '{term_lower}' is declared in {sids} but never found in the index.")

        for p_str in occurring_paths:
            sid_occurrence = path_to_section.get(p_str)
            if sid_occurrence:
                declares = False
                for sid in sids:
                    if sid == sid_occurrence:
                        declares = True
                        break
                    occ_card = section_cards.sections.get(sid_occurrence)
                    if occ_card and sid in (occ_card.depends_on or []):
                        declares = True
                        break
                if not declares:
                    warnings.append(
                        f"Term '{term_lower}' is used in '{p_str}' (Section '{sid_occurrence}'), "
                        f"but '{sid_occurrence}' neither declares it nor depends on sections that do ({sids})."
                    )
            else:
                warnings.append(f"Term '{term_lower}' is used in unmapped file '{p_str}'.")

        report[term_lower] = {
            "declared_in_sections": sids,
            "occurrence_count": len(occurrences),
            "warnings": warnings,
            "occurrences": occurrences[:5]
        }

    return {
        "status": "success",
        "audited_terms_count": len(term_declarations),
        "report": report
    }

def get_term_context(term: str, project_root: Optional[str] = None) -> Dict[str, Any]:
    """Look up a term in the terminology glossary defined in section_cards.yaml.
    
    Returns the term definition, allowed variants, and phrases to avoid.
    """
    from writing_context_rtfm.config import load_config
    from writing_context_rtfm.section_cards import load_section_cards

    root = Path(project_root or ".").resolve()
    try:
        config = load_config(str(root))
    except Exception as e:
        return {"status": "error", "message": f"Config load failed: {e}"}

    sc_path = Path(config.section_cards.path)
    if not sc_path.is_absolute():
        sc_path = root / sc_path

    if not sc_path.exists():
        return {"status": "not_found", "message": f"Section cards file not found at '{sc_path}'"}

    section_cards = load_section_cards(str(sc_path), required=False)
    if not section_cards or not section_cards.document or not section_cards.document.terminology:
        return {"status": "not_found", "message": "No terminology dictionary defined in section cards."}

    term_lower = term.lower()
    terminology = section_cards.document.terminology

    # 1. Direct match (case-insensitive)
    for k, details in terminology.items():
        if k.lower() == term_lower:
            return {
                "status": "found",
                "term": k,
                "definition": details.get("definition", ""),
                "variants": details.get("variants", []),
                "avoid": details.get("avoid", [])
            }

    # 2. Check variants (case-insensitive)
    for k, details in terminology.items():
        variants = [v.lower() for v in details.get("variants", [])]
        if term_lower in variants:
            return {
                "status": "found",
                "term": k,
                "definition": details.get("definition", ""),
                "variants": details.get("variants", []),
                "avoid": details.get("avoid", [])
            }

    # 3. Check avoid list (case-insensitive)
    for k, details in terminology.items():
        avoids = [a.lower() for a in details.get("avoid", [])]
        if term_lower in avoids:
            return {
                "status": "found",
                "term": k,
                "definition": details.get("definition", ""),
                "variants": details.get("variants", []),
                "avoid": details.get("avoid", [])
            }

    return {
        "status": "not_found",
        "message": f"Term '{term}' not found in terminology dictionary."
    }
