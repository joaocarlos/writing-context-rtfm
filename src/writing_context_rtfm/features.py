import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml


def sanitize_id(filename: str) -> str:
    """Removes extension and replaces non-alphanumeric characters with underscores."""
    name_without_ext = Path(filename).stem
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name_without_ext).lower()
    if not sanitized.startswith("section_"):
        sanitized = f"section_{sanitized}"
    return sanitized


def initialize_section_cards(project_root: str | None = None) -> dict[str, Any]:
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
    existing_cards: dict[str, Any] = {
        "version": 1,
        "document": {
            "title": "My Manuscript",
            "thesis": "",
            "writing_style": {"tone": "academic, formal", "avoid": []},
            "terminology": {
                "sample_term": {
                    "definition": "A sample technical term description.",
                    "variants": ["alternate phrasing 1"],
                    "avoid": ["deprecated variant"],
                }
            },
        },
        "sections": {},
    }
    if sc_path.exists():
        try:
            with open(sc_path) as fh:
                loaded = yaml.safe_load(fh)
                if isinstance(loaded, dict):
                    existing_cards = loaded
                    if "sections" not in existing_cards or not isinstance(
                        existing_cards["sections"], dict
                    ):
                        existing_cards["sections"] = {}
        except Exception:
            pass

    # Scan for files (.tex, .md)
    exclude_dirs = {
        ".git",
        ".venv",
        ".rtfm",
        ".writing-context",
        "node_modules",
        "__pycache__",
        "build",
        "dist",
    }
    found_files = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Filter directories in place
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for fname in filenames:
            if fname.endswith((".tex", ".md")) and fname not in ("README.md", "GEMINI.md"):
                full_path = Path(dirpath) / fname
                rel_path = full_path.relative_to(root)
                found_files.append(rel_path)

    added = []
    preserved = []

    # Map of rel_path to section cards in existing sections
    path_to_section_id = {}
    sections_dict = existing_cards.get("sections", {})
    if isinstance(sections_dict, dict):
        for sid, scard in sections_dict.items():
            if isinstance(scard, dict) and "path" in scard:
                path_to_section_id[Path(scard["path"])] = sid

    for rel_path in found_files:
        if rel_path in path_to_section_id:
            preserved.append(str(rel_path))
        else:
            base_sid = sanitize_id(rel_path.name)
            sid = base_sid
            counter = 1
            if isinstance(sections_dict, dict):
                while sid in sections_dict:
                    sid = f"{base_sid}_{counter}"
                    counter += 1

                title = rel_path.stem.replace("_", " ").replace("-", " ").title()
                sections_dict[sid] = {
                    "title": title,
                    "role": f"Draft and refine contents for {title}",
                    "path": str(rel_path),
                    "key_terms": [],
                    "depends_on": [],
                    "must_preserve": [],
                    "avoid": [],
                    "constraints": [],
                }
                added.append({"id": sid, "path": str(rel_path)})

    # Automatically resolve section dependencies via reference graph
    from writing_context_rtfm.latex import build_reference_graph

    def normalize_rel_path(p: Any, root_path: Path) -> str:
        path_obj = Path(p)
        if path_obj.is_absolute():
            try:
                return str(path_obj.relative_to(root_path))
            except ValueError:
                return str(path_obj)
        return str(path_obj)

    try:
        graph = build_reference_graph(str(root))

        # Build map of normalized relative path string -> section ID
        rel_path_to_sid = {}
        if isinstance(sections_dict, dict):
            for sid, scard in sections_dict.items():
                if isinstance(scard, dict) and "path" in scard:
                    norm_path = normalize_rel_path(scard["path"], root)
                    rel_path_to_sid[norm_path] = sid

            # Map from labels to their defining section IDs
            label_to_sid = {}
            for label, label_info in graph.get("labels", {}).items():
                def_file = label_info.get("file")
                if def_file and def_file in rel_path_to_sid:
                    label_to_sid[label] = rel_path_to_sid[def_file]

            # Update depends_on for each section card
            for sid, scard in sections_dict.items():
                if not isinstance(scard, dict) or "path" not in scard:
                    continue

                norm_path = normalize_rel_path(scard["path"], root)
                deps = set()

                # Existing dependencies in YAML (preserve them)
                existing_deps = scard.get("depends_on", [])
                if isinstance(existing_deps, list):
                    for d in existing_deps:
                        if isinstance(d, str):
                            deps.add(d)

                # 1. Add dependencies from cross-references (labels defined in other sections)
                file_refs = graph.get("references", {}).get(norm_path, [])
                for ref_key in file_refs:
                    target_sid = label_to_sid.get(ref_key)
                    if target_sid and target_sid != sid:
                        deps.add(target_sid)

                # 2. Add dependencies from file inclusions
                file_inc_deps = graph.get("file_dependencies", {}).get(norm_path, [])
                for inc_file in file_inc_deps:
                    target_sid = rel_path_to_sid.get(inc_file)
                    if target_sid and target_sid != sid:
                        deps.add(target_sid)

                # Store back sorted list of dependencies
                scard["depends_on"] = sorted(deps)
    except Exception:
        pass

    # Write back
    with open(sc_path, "w") as fh:
        yaml.safe_dump(existing_cards, fh, sort_keys=False)

    return {
        "status": "success",
        "sc_path": str(sc_path),
        "added": added,
        "preserved_count": len(preserved),
        "total_sections": len(sections_dict),
    }


def audit_manuscript_terminology(project_root: str | None = None) -> dict[str, Any]:
    """Audits key terms from section cards against their actual occurrences in the RTFM index."""
    from writing_context_rtfm.config import load_config
    from writing_context_rtfm.rtfm_adapter import RTFMAdapter
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
        return {"status": "error", "message": f"Section cards file not found at '{sc_path}'"}

    section_cards = load_section_cards(str(sc_path), required=True)
    if not section_cards:
        return {"status": "error", "message": "Failed to load section cards"}
    adapter = RTFMAdapter(str(root))

    # Map each section path to its declared section card
    path_to_section = {}
    term_declarations: dict[str, list[str]] = {}  # term_lower -> list of section_ids
    for sid, scard in section_cards.sections.items():
        if scard.path:
            p = Path(scard.path)
            if p.is_absolute():
                p = p.relative_to(root)
            path_to_section[str(p)] = sid

        for term in scard.key_terms or []:
            term_lower = term.lower()
            if term_lower not in term_declarations:
                term_declarations[term_lower] = []
            term_declarations[term_lower].append(sid)

    report = {}
    futures = {}

    with ThreadPoolExecutor(max_workers=8) as executor:
        # Submit all search requests in parallel
        for term_lower in term_declarations:
            futures[term_lower] = executor.submit(
                adapter.search, term_lower, corpus=config.rtfm.corpus, limit=50
            )

        # Process results in original declaration order to maintain deterministic report order
        for term_lower, sids in term_declarations.items():
            try:
                results = futures[term_lower].result()
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
                occurrences.append(
                    {
                        "path": r.path,
                        "line_start": r.line_start,
                        "line_end": r.line_end,
                        "snippet": r.snippet,
                    }
                )

            if not occurrences:
                warnings.append(
                    f"Term '{term_lower}' is declared in {sids} but never found in the index."
                )

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
                "occurrences": occurrences[:5],
            }

    return {"status": "success", "audited_terms_count": len(term_declarations), "report": report}


def get_term_context(term: str, project_root: str | None = None) -> dict[str, Any]:
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
        return {
            "status": "not_found",
            "message": "No terminology dictionary defined in section cards.",
        }

    term_lower = term.lower()
    terminology = section_cards.document.terminology

    # Pre-build lookup maps for O(1) matching of direct, variant, and avoid terms
    direct_lookup = {}
    variant_lookup = {}
    avoid_lookup = {}

    for k, details in terminology.items():
        k_lower = k.lower()
        direct_lookup[k_lower] = (k, details)

        for v in details.get("variants", []):
            variant_lookup[v.lower()] = (k, details)

        for a in details.get("avoid", []):
            avoid_lookup[a.lower()] = (k, details)

    # Resolve match
    if term_lower in direct_lookup:
        canonical_term, details = direct_lookup[term_lower]
    elif term_lower in variant_lookup:
        canonical_term, details = variant_lookup[term_lower]
    elif term_lower in avoid_lookup:
        canonical_term, details = avoid_lookup[term_lower]
    else:
        return {
            "status": "not_found",
            "message": f"Term '{term}' not found in terminology dictionary.",
        }

    return {
        "status": "found",
        "term": canonical_term,
        "definition": details.get("definition", ""),
        "variants": details.get("variants", []),
        "avoid": details.get("avoid", []),
    }
