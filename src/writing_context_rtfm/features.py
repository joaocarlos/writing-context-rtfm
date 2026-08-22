import contextlib
import os
import re
import sys
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


def find_entry_files(project_root: str) -> list[str]:
    root_path = Path(project_root).resolve()
    tex_files = []
    md_files = []
    exclude_dirs = {".git", ".venv", ".rtfm", ".writing-context", "node_modules", "build", "dist"}
    exclude_md_files = {
        "readme.md",
        "gemini.md",
        "claude.md",
        "agents.md",
        "license.md",
        "contributing.md",
        "task.md",
        "implementation_plan.md",
        "walkthrough.md",
    }
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for fname in filenames:
            if fname.endswith(".tex"):
                tex_files.append(Path(dirpath) / fname)
            elif fname.endswith(".md") and fname.lower() not in exclude_md_files:
                md_files.append(Path(dirpath) / fname)

    # For LaTeX, find which files are NOT included by others
    included_files = set()
    from writing_context_rtfm.latex import build_reference_graph

    try:
        graph = build_reference_graph(project_root)
        for deps in graph.get("file_dependencies", {}).values():
            for d in deps:
                included_files.add(str((root_path / d).resolve()))
    except Exception:
        pass

    entry_files = []
    for tf in tex_files:
        abs_str = str(tf.resolve())
        if abs_str not in included_files:
            entry_files.append(str(tf.relative_to(root_path)))

    for mf in md_files:
        entry_files.append(str(mf.relative_to(root_path)))

    return entry_files


def cards_scan_command(project_root: str) -> dict[str, Any]:
    """Scan manuscript files and output cards.generated.yaml containing structural metadata."""
    print("Scanning manuscript structure...", file=sys.stderr, flush=True)
    root = Path(project_root).resolve()

    # Auto-migrate legacy cards if any
    from writing_context_rtfm.section_cards import migrate_legacy_cards

    migrate_legacy_cards(str(root))

    entry_files = find_entry_files(str(root))
    if not entry_files:
        return {"status": "error", "message": "No LaTeX or Markdown entry files found."}

    from writing_context_rtfm.virtual_doc import VirtualDocumentParser

    parser = VirtualDocumentParser(str(root))

    all_nodes = {}
    for ef in entry_files:
        nodes = parser.parse(ef)
        all_nodes.update(nodes)

    generated_path = root / ".writing-context" / "cards.generated.yaml"
    lock_path = root / ".writing-context" / "cards.lock.json"
    overrides_path = root / ".writing-context" / "cards.overrides.yaml"

    # Load existing generated cards
    existing_gen: dict[str, Any] = {}
    if generated_path.exists():
        try:
            with open(generated_path, encoding="utf-8") as f:
                loaded_gen = yaml.safe_load(f)
                if isinstance(loaded_gen, dict):
                    existing_gen = loaded_gen
        except Exception:
            pass

    existing_sections = existing_gen.get("sections", {}) or {}

    # Build new generated cards structure
    gen_cards = {
        "version": 2,
        "document": existing_gen.get(
            "document",
            {
                "title": "My Manuscript",
                "thesis": "",
                "writing_style": {"tone": "academic, formal", "avoid": []},
                "terminology": {},
            },
        ),
        "sections": {},
    }

    # Load lock
    lock: dict[str, Any] = {"generation_version": 2, "extractor_version": 1, "sections": {}}
    if lock_path.exists():
        try:
            import json

            with open(lock_path, encoding="utf-8") as f:
                loaded_lock = json.load(f)
                if isinstance(loaded_lock, dict):
                    lock = loaded_lock
        except Exception:
            pass

    lock_sections: dict[str, Any] = lock.setdefault("sections", {})

    added = []
    updated = []

    for sid, node in all_nodes.items():
        # Keep semantic fields from existing generated card if present
        ex_sec = existing_sections.get(sid, {}) or {}

        gen_cards["sections"][sid] = {
            "identity": {
                "source": node.source_path,
                "selector": node.selector,
                "content_hash": node.content_hash,
                "char_start": node.char_start,
                "char_end": node.char_end,
            },
            "structure": {
                "title": node.title,
                "parent": node.parent or "document_main",
                "level": node.level,
                "children": node.children,
            },
            "purpose": ex_sec.get(
                "purpose", {"value": "", "confidence": 0.0, "status": "generated"}
            ),
            "rhetorical_role": ex_sec.get("rhetorical_role", {"value": "", "confidence": 0.0}),
            "key_terms": ex_sec.get("key_terms", []),
            "facts": ex_sec.get("facts", []),
            "constraints": ex_sec.get("constraints", []),
            "dependencies": ex_sec.get(
                "dependencies", [{"target": dep} for dep in node.references]
            ),
            "citations": node.citations,
            "references": node.references,
            "figures": node.figures,
            "tables": node.tables,
            "equations": node.equations,
            "algorithms": node.algorithms,
        }

        # Rule-based acronym extraction
        try:
            source_file = root / node.source_path
            if source_file.is_file():
                text = source_file.read_text(encoding="utf-8")[node.char_start : node.char_end]
                acronyms = set(re.findall(r"\b[A-Z]{2,6}\b", text))
                if acronyms:
                    if not gen_cards["sections"][sid]["key_terms"]:
                        gen_cards["sections"][sid]["key_terms"] = []

                    existing_kt_vals = {
                        kt.get("value")
                        for kt in gen_cards["sections"][sid]["key_terms"]
                        if isinstance(kt, dict)
                    }
                    for acr in acronyms:
                        if acr not in existing_kt_vals:
                            gen_cards["sections"][sid]["key_terms"].append(
                                {
                                    "value": acr,
                                    "confidence": 0.95,
                                    "status": "generated",
                                    "evidence": "Rule-based acronym extraction",
                                }
                            )
        except Exception:
            pass

        # Update lock and track stale state
        ls: dict[str, Any] = lock_sections.setdefault(
            sid, {"content_hash": "", "decisions": {}, "stale_fields": []}
        )
        old_hash = ls.get("content_hash", "")
        if old_hash and old_hash != node.content_hash:
            stale: list[str] = ls.setdefault("stale_fields", [])
            for field in ("purpose", "key_terms", "facts"):
                if field not in stale:
                    stale.append(field)
            updated.append(sid)
        else:
            if not old_hash:
                added.append(sid)

        ls["content_hash"] = node.content_hash

    # Clean up lock/overrides of orphan section IDs (no longer in document)
    for orphan in list(lock_sections.keys()):
        if orphan not in all_nodes:
            lock_sections.pop(orphan, None)

    # Save files
    generated_path.parent.mkdir(exist_ok=True)
    with open(generated_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(gen_cards, f, sort_keys=False)

    import json

    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump(lock, f, indent=2)

    # Create default overrides skeleton as cards.overrides.yaml.example if it does not exist
    example_path = overrides_path.with_name("cards.overrides.yaml.example")
    if not overrides_path.exists() and not example_path.exists():
        default_overrides = {
            "version": 2,
            "document": {
                "title": gen_cards["document"]["title"],
                "thesis": "",
                "writing_style": {"tone": "academic, formal", "avoid": []},
                "terminology": {},
            },
            "sections": {},
        }
        with open(example_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(default_overrides, f, sort_keys=False)

    print("Scan completed successfully.", file=sys.stderr, flush=True)
    return {"status": "success", "added": added, "updated": updated, "total": len(all_nodes)}


def cards_infer_command(project_root: str, force: bool = False) -> dict[str, Any]:
    """Run model-assisted semantic extraction on section nodes."""
    print(
        "Running model-assisted semantic extraction on section nodes...",
        file=sys.stderr,
        flush=True,
    )
    root = Path(project_root).resolve()
    generated_path = root / ".writing-context" / "cards.generated.yaml"
    lock_path = root / ".writing-context" / "cards.lock.json"

    if not generated_path.exists():
        return {
            "status": "error",
            "message": "No generated cards found. Please run 'cards scan' first.",
        }

    try:
        with open(generated_path, encoding="utf-8") as f:
            gen_cards = yaml.safe_load(f) or {}
    except Exception as e:
        return {"status": "error", "message": f"Failed to load cards.generated.yaml: {e}"}

    from writing_context_rtfm.config import load_config

    try:
        config = load_config(str(root))
    except Exception as e:
        return {"status": "error", "message": f"Failed to load config: {e}"}

    lock: dict[str, Any] = {"sections": {}}
    if lock_path.exists():
        try:
            import json

            with open(lock_path, encoding="utf-8") as f:
                loaded_lock = json.load(f)
                if isinstance(loaded_lock, dict):
                    lock = loaded_lock
        except Exception:
            pass

    from writing_context_rtfm.semantic_extractor import (
        MissingAPIKeyError,
        extract_semantic_metadata,
    )

    sections = gen_cards.setdefault("sections", {})
    inferred_count = 0
    skipped_count = 0

    for sid, sdata in sections.items():
        purpose_val = sdata.get("purpose", {}).get("value", "")
        is_stale = sid in lock.get("sections", {}) and "purpose" in lock["sections"][sid].get(
            "stale_fields", []
        )

        if purpose_val and not is_stale and not force:
            skipped_count += 1
            continue

        source_rel = sdata.get("identity", {}).get("source")
        if not source_rel:
            skipped_count += 1
            continue

        source_abs = root / source_rel
        if not source_abs.is_file():
            skipped_count += 1
            continue

        try:
            char_start = sdata.get("identity", {}).get("char_start", 0)
            char_end = sdata.get("identity", {}).get("char_end", None)

            full_text = source_abs.read_text(encoding="utf-8")
            text = full_text[char_start:char_end] if char_end is not None else full_text
        except Exception:
            skipped_count += 1
            continue

        if not text.strip():
            skipped_count += 1
            continue

        print(
            f"  - Inferring semantic metadata for section '{sid}'...",
            file=sys.stderr,
            end="",
            flush=True,
        )
        try:
            metadata = extract_semantic_metadata(text, config)
            print(" Done.", file=sys.stderr, flush=True)
        except MissingAPIKeyError as e:
            print(" Failed (API Key missing).", file=sys.stderr, flush=True)
            raise e
        except Exception as e:
            print(f" Failed: {e}", file=sys.stderr, flush=True)
            print(f"Warning: Failed to extract metadata for '{sid}': {e}")
            skipped_count += 1
            continue

        sdata["rhetorical_role"] = {
            "value": metadata.get("rhetorical_role", ""),
            "confidence": 0.90,
        }
        sdata["purpose"] = {
            "value": metadata.get("purpose", ""),
            "confidence": 0.90,
            "status": "generated",
            "provenance": [f"{source_rel}"],
        }

        existing_terms = {
            kt.get("value") for kt in sdata.get("key_terms", []) if isinstance(kt, dict)
        }
        for kt in metadata.get("key_terms", []):
            val = kt.get("value")
            if val and val not in existing_terms:
                sdata["key_terms"].append(
                    {"value": val, "confidence": kt.get("confidence", 0.8), "status": "generated"}
                )

        sdata["facts"] = []
        for i, fact in enumerate(metadata.get("facts", [])):
            sdata["facts"].append(
                {
                    "id": f"fact_{i}",
                    "value": fact.get("value", ""),
                    "type": fact.get("type", "semantic_claim"),
                    "confidence": fact.get("confidence", 0.8),
                    "status": "generated",
                    "provenance": [f"{source_rel}"],
                }
            )

        sdata["constraints"] = []
        for i, const in enumerate(metadata.get("constraints", [])):
            sdata["constraints"].append(
                {
                    "id": f"constraint_{i}",
                    "value": const.get("value", ""),
                    "type": const.get("type", "rhetorical_boundary"),
                    "confidence": const.get("confidence", 0.8),
                    "status": "generated",
                }
            )

        if sid in lock.get("sections", {}):
            stale = lock["sections"][sid].setdefault("stale_fields", [])
            for field in ("purpose", "key_terms", "facts"):
                if field in stale:
                    stale.remove(field)

        inferred_count += 1

    with open(generated_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(gen_cards, f, sort_keys=False)

    import json

    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump(lock, f, indent=2)

    print(
        f"Inference completed. Inferred: {inferred_count}, Skipped: {skipped_count}",
        file=sys.stderr,
        flush=True,
    )
    return {"status": "success", "inferred": inferred_count, "skipped": skipped_count}


def cards_review_command(project_root: str) -> dict[str, Any]:
    """Interactively review candidate card fields in the terminal."""
    root = Path(project_root).resolve()
    generated_path = root / ".writing-context" / "cards.generated.yaml"
    overrides_path = root / ".writing-context" / "cards.overrides.yaml"

    example_path = overrides_path.with_name("cards.overrides.yaml.example")
    if not generated_path.exists() or (not overrides_path.exists() and not example_path.exists()):
        return {
            "status": "error",
            "message": "Section cards files not found. Run 'scan' and 'infer' first.",
        }

    try:
        with open(generated_path, encoding="utf-8") as f:
            gen_cards = yaml.safe_load(f) or {}

        overrides: dict[str, Any] = {"version": 2, "document": {}, "sections": {}}
        if overrides_path.exists():
            with open(overrides_path, encoding="utf-8") as f:
                loaded_ov = yaml.safe_load(f)
                if isinstance(loaded_ov, dict):
                    overrides = loaded_ov
        elif example_path.exists():
            with open(example_path, encoding="utf-8") as f:
                loaded_ov = yaml.safe_load(f)
                if isinstance(loaded_ov, dict):
                    overrides = loaded_ov
    except Exception as e:
        return {"status": "error", "message": f"Failed to load card files: {e}"}

    gen_sections: dict[str, Any] = gen_cards.get("sections", {}) or {}
    over_sections: dict[str, Any] = overrides.setdefault("sections", {})

    print("\n--- Section Cards Interactive Review ---")
    reviewed_count = 0

    for sid, gs in gen_sections.items():
        purpose = str(gs.get("purpose", {}).get("value", ""))
        role = str(gs.get("rhetorical_role", {}).get("value", ""))
        key_terms: list[str] = [
            str(kt.get("value"))
            for kt in gs.get("key_terms", [])
            if isinstance(kt, dict) and kt.get("value")
        ]
        facts: list[str] = [
            str(f.get("value"))
            for f in gs.get("facts", [])
            if isinstance(f, dict) and f.get("value")
        ]

        if sid in over_sections and (
            over_sections[sid].get("purpose") or over_sections[sid].get("role")
        ):
            continue

        print(f"\nSection ID: {sid}")
        print(f"Title: {gs.get('structure', {}).get('title')}")
        print(f"Rhetorical Role: {role}")
        print(f"Proposed Purpose: {purpose}")
        print(f"Proposed Key Terms: {', '.join(key_terms)}")
        print(f"Proposed Facts to Preserve: {facts}")

        ans = input("Accept these candidates? [y/n/e (edit)]: ").strip().lower()
        if ans == "y":
            os_data: dict[str, Any] = over_sections.setdefault(sid, {})
            os_data["purpose"] = purpose
            os_data["role"] = role
            os_data["key_terms"] = key_terms
            os_data["must_preserve"] = facts
            reviewed_count += 1
        elif ans == "e":
            os_data = over_sections.setdefault(sid, {})
            custom_purpose = input(f"Enter purpose [{purpose}]: ").strip()
            os_data["purpose"] = custom_purpose if custom_purpose else purpose
            custom_role = input(f"Enter rhetorical role [{role}]: ").strip()
            os_data["role"] = custom_role if custom_role else role
            os_data["key_terms"] = key_terms
            os_data["must_preserve"] = facts
            reviewed_count += 1

    with open(overrides_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(overrides, f, sort_keys=False)

    print(f"\nReview completed. Updated {reviewed_count} sections in cards.overrides.yaml.")
    return {"status": "success", "reviewed": reviewed_count}


def cards_update_command(project_root: str, changed_only: bool = False) -> dict[str, Any]:
    """Scan and update nodes tracking content hash changes."""
    return cards_scan_command(project_root)


def cards_validate_command(project_root: str) -> dict[str, Any]:
    """Check cards for stale marks, missing files, and inconsistencies."""
    root = Path(project_root).resolve()
    generated_path = root / ".writing-context" / "cards.generated.yaml"
    lock_path = root / ".writing-context" / "cards.lock.json"

    if not generated_path.exists():
        return {
            "status": "error",
            "message": "No generated cards found. Please run 'cards scan' first.",
        }

    lock: dict[str, Any] = {}
    if lock_path.exists():
        try:
            import json

            with open(lock_path, encoding="utf-8") as f:
                loaded_lock = json.load(f)
                if isinstance(loaded_lock, dict):
                    lock = loaded_lock
        except Exception:
            pass

    warnings = []
    stale_count = 0

    for sid, ls in lock.get("sections", {}).items():
        stale = ls.get("stale_fields", [])
        if stale:
            warnings.append(
                f"Section '{sid}' has stale fields: {', '.join(stale)}. Re-inference needed."
            )
            stale_count += 1

    from writing_context_rtfm.section_cards import load_section_cards, validate_section_cards

    cards = load_section_cards(str(generated_path))
    if cards:
        cons_warnings = validate_section_cards(cards)
        warnings.extend(cons_warnings)

    return {"status": "success", "stale_count": stale_count, "warnings": warnings}


def cards_build_command(project_root: str, review: bool = False) -> dict[str, Any]:
    """Combined workflow command running scan, infer, and optional review."""
    print("Starting cards build workflow...", file=sys.stderr, flush=True)
    scan_res = cards_scan_command(project_root)
    if scan_res.get("status") == "error":
        return scan_res

    try:
        infer_res = cards_infer_command(project_root)
    except Exception as e:
        infer_res = {"status": "warning", "message": f"Semantic inference skipped: {e}"}

    review_res = None
    if review:
        review_res = cards_review_command(project_root)

    print("Cards build workflow completed.", file=sys.stderr, flush=True)
    return {"status": "success", "scan": scan_res, "infer": infer_res, "review": review_res}


def cards_rebuild_command(project_root: str, review: bool = False) -> dict[str, Any]:
    """Cleanly rebuild main section cards from scratch (clears cards.generated.yaml and runs scan + infer --force)."""
    print("Rebuilding main section cards from scratch...", file=sys.stderr, flush=True)
    root = Path(project_root).resolve()
    generated_path = root / ".writing-context" / "cards.generated.yaml"
    lock_path = root / ".writing-context" / "cards.lock.json"

    if generated_path.exists():
        with contextlib.suppress(Exception):
            generated_path.unlink()

    if lock_path.exists():
        with contextlib.suppress(Exception):
            lock_path.unlink()

    scan_res = cards_scan_command(project_root)
    if scan_res.get("status") == "error":
        return scan_res

    try:
        infer_res = cards_infer_command(project_root, force=True)
    except Exception as e:
        infer_res = {"status": "warning", "message": f"Semantic inference skipped: {e}"}

    review_res = None
    if review:
        review_res = cards_review_command(project_root)

    print("Cards rebuild completed successfully.", file=sys.stderr, flush=True)
    return {"status": "success", "scan": scan_res, "infer": infer_res, "review": review_res}
