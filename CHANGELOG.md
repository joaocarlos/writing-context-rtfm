# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.30.4] - 2026-10-24

### Fixed
- **Zotero Hallucinated AI Search Fallback Rejection (`providers/local.py`)**:
  - Added strict rejection pattern matching (`_is_unresolved_zotero_result`) for missing citation keys. Out-of-domain AI-powered fallback papers returned by Zotero MCP when a cited key is not in the library are now completely rejected rather than accepted under `zotero:<citekey>`.
- **Cross-Provider Reference Deduplication (`context_pack.py`)**:
  - Implemented cross-provider deduplication between `bibtex:` and `zotero:` spans sharing the same citation key. If the Zotero item contains user notes, annotations, or highlights, the rich Zotero span is retained and the BibTeX span is deduplicated. If Zotero contains only generic metadata, the canonical offline BibTeX span is kept and the duplicate Zotero span is dropped.
- **Structural Scaffolding and Preamble Filtering (`context_pack.py`)**:
  - Excluded pure non-narrative acronym and notation files (`abbrevs.tex`, `acronyms.tex`, `symbols.tex`, etc.) from RTFM semantic content retrieval, preventing abbreviation tables from flooding token budgets.
  - Excluded book/thesis assembler preambles (`thesis.tex`) when dedicated chapter files are targeted. Single-file manuscripts (`main.tex`, `paper.tex`) remain fully preserved.

### Added
- **Zotero User Notes & Annotations Extraction (`providers/local.py`)**:
  - Extended citation resolution to query `zotero_get_notes(item_key=...)` in addition to `zotero_get_annotations`, injecting user-authored child notes directly under `### User Notes:`.
  - Added fallback search via `zotero_search_notes` when a cited paper lacks direct annotations or when local semantic search is unavailable, surfacing personal reading takeaways and highlighted quotes into the writing context.

## [0.30.3] - 2026-10-23

### Added
- **Unified Optional Dependency Group (`pyproject.toml`)**:
  - Added `all = ["tiktoken", "sentence-transformers>=3.4.1", "transformers>=4.48.0"]` under `[project.optional-dependencies]`, allowing single-command complete installation via `uv tool install "writing-context-rtfm[all]" --force`.
- **Native MCP Manuscript Audit Prompt (`server.py`)**:
  - Added `audit_manuscript` to `prompts/list` and `prompts/get`, combining `audit_manuscript_terminology` and `build_reference_graph` into an automated, high-density LLM prompt summarizing scanned files, defined labels, terminology violations, and unresolved LaTeX cross-references.

### Changed
- **Native MCP Prompt Modernization (`server.py`)**:
  - Updated `write_section` prompt: made `target` optional (`required: false`) to support automatic target inference from `task`, and added arguments for `profile` (`auto`, `fast`, `quality`), `must_consider`, `strict_budget`, and `git_diff`.
- **Closed-Loop Telemetry & Autonomous Agent Guidance (`server.py`)**:
  - Injected `run_id` into formatted prompts (`<task>` and `<instructions>`) and tool response `guidance` for both `get_writing_context_pack` and `get_proofreading_context_pack`.
  - Prompts and guidance now instruct LLM agents to automatically close the feedback loop by invoking `submit_generation_feedback(run_id=..., generation_tokens=...)` upon drafting or revising, ensuring session statistics remain fully populated without human intervention.

## [0.30.2] - 2026-10-23

### Fixed
- **Tokenomics Telemetry Fallback (`storage.py`, `cli.py`)**:
  - Replaced falsy `or` fallback with explicit `is not None` checks for `realistic_tokens_saved` and `realistic_savings_ratio` in `ExtensionStore.store_pack`, preventing zero-saving runs (e.g. packs larger than target neighborhood) from falling back to full-document naive savings and distorting aggregate statistics.
- **LaTeX Safety Math Environment Filtering (`latex.py`)**:
  - Restricted `scan_latex_commands` AST environment matching to mathematical environments (`equation`, `align`, `gather`, `multline`, etc.). Prose and list environments (`enumerate`, `itemize`, `description`) are no longer dumped as monolithic verbatim blocks into `warnings`, while nested citations, labels, and inline math inside items are still cleanly traversed and preserved.
- **Zotero Provider Discovery Search Optimization (`providers/local.py`)**:
  - Added automatic skip of open-ended discovery searches when target section has structural roles (`appendix`, `frontmatter`, `backmatter`), preserving explicit `\cite{...}` resolution while preventing multi-second sequential searches on non-content sections.
  - Filtered generic stopwords and short abbreviations (<3 chars) from external queries, and capped maximum external Zotero search requests per pack to eliminate cold-start latency spikes.

## [0.30.1] - 2026-10-19

### Fixed
- **Proofreading Token Budget & Status Classification (`proofread.py`, `server.py`)**:
  - Removed artificial token overflow warning check in `ProofreadPackGenerator.generate`, trusting the complete atomic context (target text, surrounding context, section constraints, and terminology) without truncation.
  - Aligned `handle_get_proofreading_context_pack` in `server.py` to use `_is_degrading_pack_warning(w)`, preventing informational notes or non-degrading warnings from falsely reclassifying the response status to `"degraded"`.
  - Updated tool schema description for `max_tokens` in `get_proofreading_context_pack` to clarify that full context is preserved to guarantee editorial precision.

## [0.30.0] - 2026-10-18

### Added
- **RetrievalEngine Boundary Extensions (`retrieval.py`, `rtfm_adapter.py`)**:
  - Extended `RetrievalEngineHealth` with `has_fts: bool` and added `get_chunks_by_ids(chunk_ids)` to the `RetrievalEngine` protocol plus `RTFMAdapter.list_indexed_chunks_from_db(...)`.
  - Added `profile` parameter (`"auto"`, `"fast"`, `"quality"`) to the `get_writing_context_pack` MCP tool schema (`server.py`) and updated `docs/mcp_tools_reference.md`.

### Changed
- **Architectural Boundary Enforcement (`doctor.py`, `storage.py`, `providers/`)**:
  - Eliminated direct `sqlite3.connect` and `ATTACH DATABASE` accesses to `.rtfm/library.db` outside `rtfm_adapter.py`, routing index diagnostics and embedding chunk syncs through `RTFMAdapter` and `RetrievalEngine`.
- **Realistic Counterfactual Token Baseline (`context_pack.py`, `token_budget.py`, `proofread.py`)**:
  - Removed hardcoded `8,000` / `25,000` token floor clamping in `compute_realistic_baseline_tokens` and `compute_counterfactual_savings`. Realistic baselines now reflect actual tokens of the target file plus 1-hop `depends_on` files without artificial inflation.
- **Distribution Packaging Cleanup**:
  - Moved private benchmark and evaluation modules (`benchmark.py`, `candidate_exposure.py`, `rtfm_sync_explicit.py`) from `src/writing_context_rtfm/` to `tests/eval/`, keeping the published wheel strictly focused on runtime library and MCP server code.

### Fixed
- **Provider Registration & Fingerprint Invalidations (`providers/__init__.py`, `context_pack.py`)**:
  - Registered `LocalSemanticSearchProvider` in `get_active_providers()` so CLI and MCP runs instantiate local semantic search when enabled.
  - Included `must_consider`, `profile`, `strict_budget`, and `role_budgets` in `compute_task_hash` calls in `ContextPackGenerator.generate`, preventing stale cache hits across distinct request constraints.
- **Intent Classification & Execution Profile (`intent_classifier.py`, `context_pack.py`)**:
  - Added word-boundary token checks before short-token substring fallback in `_lexical_prototype_scores`, preventing false matches such as `"add"` inside `"address"` or `"cut"` inside `"executar"`.
  - `_infer_writing_mode` now respects `profile="fast"` by skipping `classify_task_intent(task)` model loading and using deterministic regex heuristics.
- **Tiered Budget Eviction & Structural Snapping (`context_pack.py`, `token_budget.py`, `section_cards.py`)**:
  - Updated `_select_spans_within_budget` so essential spans (`target_text`, `dependency`) evict lower-priority spans (`reference`, `local_context`) before falling back to partial truncation.
  - Preserved diagnostic stage tags during structural LaTeX AST snapping and recorded `snap_ast` events in the candidate trace tracker.
  - Resolved `preflight_budget_check` section keys across file stems and paths (`section_cards.py` / `token_budget.py`).
  - Excluded non-degrading advisory prefixes (`Note:`, `Card uncertainty:`, `Card audit:`, `Pre-flight budget check:`) from `warnings_triggered` in `features.py`.
- **Proofreading Cache Key & Dynamic Extension Version (`proofread.py`, `storage.py`)**:
  - Scoped proofreading cache keys with `project_root`, file `mtime_ns`/`size`, and `section_cards` hash, and replaced hardcoded `"0.1.0"` fallback version in `storage.py` with `writing_context_rtfm.__version__`.

## [0.23.10] - 2026-10-18

### Added
- **Local Semantic Intent Classifier (`intent_classifier.py`)**:
  - Implemented `LocalIntentClassifier` using local sentence embeddings (`sentence-transformers/all-MiniLM-L6-v2`) to infer task intent (`compress`, `adapt`, `rewrite`, `delete`, `write`) via cosine similarity against multilingual semantic prototypes.
  - Replaced brittle substring matching with a 4-level hybrid cascade (explicit parameter -> deterministic removal/preservation guardrails -> local semantic embeddings -> structural fallback).
  - Applied asymmetric confidence thresholding to protect against false compression: `compress` requires high semantic confidence (`score >= 0.65`, `margin >= 0.08`), whereas structural editing/deletion prioritizes conservative `rewrite` without pruning.

### Fixed
- **LaTeX Safety Warning Permissive Qualification**:
  - Qualified `LaTeX Safety:` warning in both `context_pack.py` and `proofread.py` with: `"unless explicitly instructed by the task to remove or refactor their enclosing section/content"`, preventing false prohibitions on intentional user deletions/removals.
- **Substring False Positives in Mode Inference & Target Resolution**:
  - Fixed bug where Portuguese verb `"executar"` (containing substring `"cut"`) falsely forced `mode="compress"`, deactivated retrieval providers, and injected an unintended 15% text pruning constraint.
  - Fixed bug where `"adaptive"` triggered `adapt` mode.
  - Fixed `card_stem` in `_resolve_target` to use word boundaries (`\b`), preventing unintended matches with substrings of common words.
- **Document Node Resolution in `virtual_doc.py`**:
  - Extended `find_section_node` to match exact filenames (`chapter6.tex`), relative file paths, and internal LaTeX section labels (`\label{...}`), enabling precise node lookup for chapter files.

## [0.23.9] - 2026-10-17

### Fixed
- **Resilient Target Inference (`_resolve_target`)**:
  - Automatically extracts explicit manuscript filenames (`.tex`, `.md`, `.txt`, `.org`, `.rst`) from the natural language task string.
  - Added numerical equivalence and zero-padding tolerance between task references and section cards (e.g. matching `chapter6.tex` or `chapter 6` with `chapter_06`, `chapter06.tex`, `ch06`).
  - Added filesystem fallback: if a referenced file exists in the workspace or standard subdirectories (`chapters/`, `sections/`, etc.), it is resolved as `target_path` even before a section card is created.
  - When target is resolved via file path without a card, properly reports `Note: Target was inferred from task description as '...'` rather than dropping into degraded unmapped mode.
- **Degraded Status Hygiene (`has_degrading`)**:
  - Excluded routine manuscript auditing and advisory warnings (`Card uncertainty:`, `Card audit:`, `Pre-flight budget check:`) from setting `status = "degraded"`.
  - Retains `status = "complete"` whenever retrieval executes successfully and returns valid spans within token budget.
- **MCP Tool Schema Guidance**:
  - Updated `get_writing_context_pack` schema in `server.py`: clearly documents that `target` accepts section IDs, filenames, and chapter identifiers, marking it STRONGLY RECOMMENDED.
  - Updated `token_budget` description with practical guidelines across task types (1k–2k local, 3k–5k drafting, 6k–10k review/adaptation).
  - Updated `AGENTS.md` and `GEMINI.md` Rules of Thumb to prioritize passing `target` and calibrating budget.

## [0.23.8] - 2026-10-16

### Fixed
- **Group-Only Library Scope & Footer Hint Filtering in Zotero Provider**:
  - Filtered out tool instruction footers (e.g. `Use zotero_switch_library...`) from being misparsed as library entries.
  - When only group libraries are returned by `zotero_list_libraries`, requesting `My Library` automatically and gracefully falls back to the already-active personal user library instead of raising a missing library error.

## [0.23.7] - 2026-10-16

### Fixed
- **Zotero Provider Localized User Library Auto-Fallback**:
  - Added smart fallback in `ZoteroProvider._ensure_library_selected` when default `library_name: "My Library"` is configured on non-English/localized Zotero Desktop installations (e.g., "Minha Biblioteca", "A Minha Biblioteca", or single personal user library).
  - Enhanced error messages to list all discovered library names if matching fails.
  - Relaxed library parser regex to support variations in `zotero-mcp` fastmcp output (`libraryID`, `library_id`, `ID`).

## [0.23.6] - 2026-10-16

### Fixed
- **Preview Pack Diagnostics & Warnings Display**:
  - `writing-context-rtfm preview-pack` now explicitly displays `Warnings (<count>):` directly in the preview header, surfacing any degradation reasons, provider notices, or budget caveats.
- **Reference Traversal Notice Hygiene**:
  - Prefixed 1-hop reference traversal notice in `context_pack.py` with `Note:` to prevent harmless traversal notices from falsely marking healthy packs as `degraded`.

## [0.23.5] - 2026-10-16

### Fixed
- **Inferred Target Status & Display Hygiene**:
  - `ContextPack.target` and telemetry `run_data["target"]` now receive the inferred target display string (e.g. `chapter5.tex (inferred)`) instead of preserving raw `None`.
  - Inferred target diagnostic warning is now prefixed with `Note:` (`Note: Target was inferred from task description as '...'`), preventing the pack status from erroneously being marked as `degraded` and ensuring it reports `complete`.
  - Rendered prompt preview in `<task>` now states `Target Section: <inferred_target>` instead of `Target Section: Unknown`.

## [0.23.4] - 2026-10-16

### Added
- **LaTeX Acronym & Abbreviation Discovery (`abbrevs.tex`)**:
  - Implemented `extract_acronyms_for_project` and `extract_acronyms_from_text` in `latex.py` to auto-discover glossary entries from `abbrevs.tex`, `acronyms.tex`, `symbols.tex`, etc.
  - Supports `\newacronym`, `\acro`, `\acrodef`, `\item[\textbf{...}]`, and `\nomenclature` syntax.
  - Automatically populates `document.terminology` in `cards.generated.yaml` during `writing-context-rtfm cards scan`.
- **Glossary Fallback to Section Key Terms**:
  - `get-term` / `get_term_context` now searches across section cards' `key_terms` when a term is not yet defined in the document dictionary, reporting which sections use it.

### Fixed
- **Section Card Loading for Modern Split Card Layouts**:
  - Fixed `show-graph`, `inspect-target`, and `audit` commands which previously required legacy `section_cards.yaml` to exist on disk before calling `load_section_cards()`.
  - Now transparently loads `cards.generated.yaml` and `cards.overrides.yaml`, displaying section card dependencies properly in `show-graph`.

## [0.23.3] - 2026-10-16

### Fixed
- **Cross-Reference & Asset Dependency Resolution in Section Cards**:
  - `cards scan` now maps reference labels (`\ref{...}`) through the document reference graph into canonical section IDs, discarding intra-section self-references.
  - Eliminated hundreds of spurious `depends_on unknown section 'eq:...', 'fig:...', 'tab:...', 'lst:...'` warnings in `writing-context-rtfm cards validate`.
  - Added section alias mapping in `validate_section_cards()` supporting section selectors, paths, stems, and lowercased titles.
- **Search Hygiene & Query Noise Filtering**:
  - Filtered out agent persona and instruction files (`CLAUDE.md`, `AGENTS.md`, `gemini.md`) as well as receipts (`receipt`) from candidate search results and source spans in `is_allowed_source()`.
  - Expanded `KEYWORD_STOPWORDS` with procedural prompt instructions and common writing task verbs (`expand`, `explain`, `avoid`, `only`, `preserve`, `claiming`, `merge`, `concise`, `scope`, `delivery`, `superiority`, `draft`, `chapter`, etc.), preventing prompt phrasing from polluting sparse/dense search queries with irrelevant single-word lookups.

### Added
- **Heuristic Target Section Inference**:
  - `ContextPackGenerator._resolve_target` now infers the target section or chapter automatically from the `task` prompt when `--target` is omitted or passed as `null` by an agent or client (e.g. recognizing `"Chapter 5"`, `"Capítulo 5"`, or explicit file/selector mentions).
  - Emits an informative diagnostic warning explaining the inferred target, preserving section-anchored relevance ranking and preventing degraded, unanchored pack generation.

## [0.23.2] - 2026-09-23

### Added
- **Proofreading Context Pack Telemetry & Storage**:
  - `ProofreadingContextPack` now generates and returns a unique `run_id`, allowing evaluation feedback (`submit_generation_feedback`) to be linked to proofreading sessions.
  - Instrumented `ProofreadPackGenerator.generate()` to record telemetry runs into SQLite (`context_pack_runs`, `context_pack_payloads`, `context_pack_sources`) with `mode="proofread"`.
  - Added counterfactual baseline calculations (`baseline_mode="target_file"`) tracking realistic tokens and tokens saved during surgical proofreading.
  - Added unit test `test_proofread_records_telemetry_run_in_store` in `tests/test_proofread.py`.

## [0.23.1] - 2026-09-21

### Fixed
- **CLI Quickstart Section Count Reporting**:
  - Fixed cosmetic display bug where `writing-context-rtfm init --quickstart` printed `0 section(s) discovered` even though manuscript structure was fully parsed and written to `.writing-context/cards.generated.yaml`.
  - Added `sections_found` key to `cards_scan_command` response payload alongside `total` for dual compatibility across CLI and API callers.
  - Made `cli.py` fallback gracefully across `total`, `sections_found`, and `len(added)` to ensure accurate manuscript statistics.

### Added
- **Quickstart End-to-End Regression Test**:
  - Added `test_init_quickstart_scans_and_reports_correct_section_count` in `tests/test_cli_init.py` verifying accurate section discovery logging and card persistence during quickstart initialization.

## [0.23.0] - 2026-09-20

### Added
- **Empirical Calibration & Tokenomics Governance**:
  - Added `writing-context-rtfm calibrate [RUN_ID] [--choice section|neighborhood|chapter|full_doc]` command allowing human authors to explicitly record the counterfactual baseline (what text they would have manually pasted without the MCP).
  - Added rolling 5-hour session tracking (`writing-context-rtfm stats --session`) modeling Astra's dual constraints: message quota limit (default 25 messages in 5h window) decoupled from the 272k-token single-call pricing threshold.
  - Added `bottleneck_calls_remaining` telemetry identifying whether the limiting factor is the message quota or token headroom.
  - Added dual baselines to `stats`: `baseline_realistic_tokens` (target section + neighborhood or chapter) vs. `baseline_document_tokens` (entire workspace ceiling), with explicit `[Capped from X tok]` reporting.
  - Added roundtrip token instrumentation tracking `pack_tokens`, `instruction_tokens`, `generation_tokens`, and MCP schema overhead (~420 tok).
- **Frontier Prompt Engineering (Claude Opus 5, Gemini Astra/3.8, GPT-5)**:
  - Structured prompt templates in `server.py`, `semantic_extractor.py`, and `benchmark.py` using semantic XML delimiters (`<task>`, `<thesis>`, `<section_constraints>`, `<author_macros>`, `<context_spans>`, `<rules>`, `<instructions>`).
  - Added explicit positive and negative constraints guaranteeing verbatim preservation of citation keys (`\cite{...}`, `[@...]`), labels (`\label{...}`), cross-references (`\ref{...}`), math environments (`equation`, `align`, `$...$`, `$$...$$`), and author macros.
- **Adaptive Sequence Length & Neural Reranker Robustness**:
  - Implemented model-aware sequence length detection in `LocalCrossEncoderReranker`: automatically configures `max_length = 2048` for ModernBERT architectures (`Alibaba-NLP/gte-reranker-modernbert-base`) while defaulting to `512` for classical models (`MiniLM`, `BGE`).
  - Added `reranker_truncated: True` telemetry flag to `SourceSpan.metadata` when candidate passages exceed the cross-encoder context window.
  - Enforced strict pre-filtering candidate bounding in `_prioritize_and_bound_reranker_candidates` to cap neural evaluation to `candidate_limit` (default 20), ensuring $\le 15$ ms CPU latency even with 50+ raw candidates.
  - Added automatic WAL checkpointing (`PRAGMA wal_checkpoint(TRUNCATE)`) on SQLite connection close for atomic, single-file database portability.

### Changed
- **CI/CD Automation**:
  - Updated `.github/workflows/publish.yml` to automatically create matching GitHub Releases with attached wheel and sdist packages on tag push, backed by `contents: write` permissions.
  - Backfilled GitHub Releases for versions `v0.10.0`, `v0.11.3`, `v0.11.4`, `v0.11.5`, `v0.20.0`, `v0.21.0`, and `v0.22.0`.

## [0.22.0] - 2026-09-18

### Changed
- **MCP Tool Catalog Consolidation (Single Front-Door Pattern)**:
  - Compressed the runtime MCP tool catalog (`tools/list`) from 19 disparate tools down to **5 essential tools**, eliminating LLM tool choice paralysis and slashing static schema token bloat in agent prompts by ~75% (<1,000 tokens):
    1. `get_writing_context_pack`: Main front-door tool for drafting/revising text, consolidating functional modes (`write`, `rewrite`, `adapt`, `compress`) and on-demand explainability diagnostics (`include_diagnostics: true`).
    2. `get_proofreading_context_pack`: Surgical paragraph/line-level proofreading with strict LaTeX/Pandoc environment preservation.
    3. `request_more_context`: Context expansion and pagination via `run_id`.
    4. `submit_generation_feedback`: Retrieval evaluation feedback for cache scoring.
    5. `manage_section_cards`: Unified governance and structural inspection tool consolidating 12 previous tools into a single action-based interface (`init`, `review`, `accept`, `reject`, `edit`, `diff`, `history`, `explain`, `inspect`, `graph`, `term`, `audit`).
  - Implemented parameter harmonization in `manage_section_cards` (aliases for `target`/`section_id`/`card_id` and `reason`/`comment`).
  - Added `include_diagnostics` flag to `get_writing_context_pack`, cleanly superseding `explain_context_pack`.
- **Dependency Environment Synchronization**:
  - Upgraded lockfile and virtual environment to latest upstream dependencies on PyPI, notably `rtfm-ai==0.46.1`, `mcp==2.2.0`, `torch==2.14.0`, and `pytest==9.1.1`.
  - Validated 100% test pass rate (422 passed) against `rtfm-ai 0.46.1` in runtime with 0 regressions.

### Documentation & Guidelines
- Updated `docs/mcp_tools_reference.md` to reflect the 5-tool catalog matrix.
- Updated agent rules of thumb in `AGENTS.md` and `GEMINI.md` to guide agents toward `manage_section_cards` and consolidated modes.

## [0.21.0] - 2026-09-18

### Added
- **Tiered Eviction & Explicit Citation Protection (Tier 1)**:
  - References explicitly cited in target text or task (e.g. `\cite{...}` or `@citekey`) bypass provider quota caps even under strict budgeting.
  - Implemented Quota Spillover in budget allocation: non-reference roles (dependencies, local context) claim needed tokens first, and remaining unallocated budget cleanly spills over to general reference candidates, eliminating prompt token vacancy.
  - Added structured `budget_overflow` status with `quality.budget_overflow_details` (reporting missing spans, missing token counts, and suggested budgets).
- **Task-Specific Writing Modes (`mode`)**:
  - Added functional writing modes: `write` (new sections from scratch with previous-section transition bridges), `rewrite` (targeted revision), `adapt` (cross-genre/audience adaptation with citation and formula preservation), and `compress` (non-destructive volume reduction targeting ~15% word reduction while strictly preserving citations, formulas, numbers, and technical terms).
  - Implemented automatic mode inference when `mode` is omitted, inspecting task semantics and file existence.
  - Added `--mode` parameter to CLI commands `pack` and `explain-pack`, and `mode` argument to MCP tool `get_writing_context_pack` and prompt `write_section`.
- **LaTeX AST Equation & Cross-Reference Resolution**:
  - Extended native `pylatexenc` AST traversal in `latex.py` and `virtual_doc.py` to recognize `\eqref{...}`, `\pageref{...}`, and `\vref{...}` macros, connecting equation targets directly into the 1-hop reference graph.
- **Dedicated Test Suite**:
  - Added `tests/test_functional_modes_and_tiered_eviction.py` validating Tier 1 protection, spillover, budget overflow telemetry, and all task modes.

## [0.20.0] - 2026-09-18

### Added
- **Zero-Friction MCP Execution (`uvx writing-context-rtfm`)**: CLI automatically defaults to the `serve` subcommand when spawned non-interactively (piped stdio), enabling zero-install MCP integration in Claude Desktop, Cursor, and Cline without additional arguments.
- **One-Command Project Quickstart (`writing-context-rtfm init --quickstart`)**: Added `--quickstart` flag combining configuration initialization, section card discovery (`cards build`), and initial RTFM index synchronization (`library.db`) into a single step.
- **Comprehensive Diagnostic Doctor (`writing-context-rtfm doctor`)**:
  - Implemented 6-category diagnostic reporting: **Python** (version $\ge 3.13$), **Dependencies** (core and optional), **Index** (SQLite FTS5 chunks, books, vector embeddings), **Zotero & BibTeX** (offline `.bib` discovery and Zotero desktop storage), **API Keys** (masked OpenAI, Hugging Face, Anthropic), and **Local Models & Hardware** (CPU cores, Metal/CUDA acceleration, model cache directories).
  - Added structured `--json` export for programmatic and agent consumption.
  - Added actionable remediation hints for identified warnings or failures.
- **Stable Anti-Corruption Layer (`RetrievalEngine`)**:
  - Defined `@runtime_checkable` `RetrievalEngine` protocol in `writing_context_rtfm.retrieval` standardizing retrieval operations (`search`, `context`, `expand`, `sync`, `health_check`, `get_fingerprint`, `get_db_path`).
  - Added structured `RetrievalEngineHealth` dataclass and typed `RetrievalEngineError` exception hierarchy.
- **Dedicated RTFM Contract Test Suite (`tests/test_rtfm_contract.py`)**: Added upstream contract validation verifying CLI flags (`--help`, `search --help`, `sync --help`, `embed --help`), SQLite schemas (`chunks`, `books`, `chunks_fts`), protocol adherence, and failure isolation against the latest upstream RTFM release.
- **Dedicated Doctor Test Suite (`tests/test_doctor.py`)**: Added unit tests covering all diagnostic checks, text formatting, and JSON serialization.
- **Architecture Boundaries Specification (`docs/architecture_boundaries.md`)**: Documented domain separation, ownership matrix, Anti-Corruption Layer, and upstream continuous testing policies.

### Changed
- **Upstream Retrieval Engine Compatibility**: Upgraded `rtfm-ai[embeddings]` dependency to `>=0.46.1` and validated 100% test pass rate against upstream release `0.46.1`.
- **Decoupled Internal Architecture**: Updated `ContextPackGenerator`, `ProofreadPackGenerator`, and MCP server tools to depend strictly on the `RetrievalEngine` protocol rather than concrete adapter classes.
- **Refactored RTFM Adapter**: `RTFMAdapter` now formally implements `RetrievalEngine`, with query hygiene short-circuiting empty strings and robust fallback isolation.
- **Canonical Three-Workflow Documentation**: Restructured `README.md` and `docs/writing-context-rtfm_workflow_guide.md` around three clear user flows: Primeiro Uso (First Use), Uso Diário (Daily Usage), and Troubleshooting.

### Fixed
- Fixed `tests/test_token_budget.py` offline fallback test to isolate `tiktoken` cleanly without relying on ambient virtual environment state.
- Fixed query short-circuiting in `RTFMAdapter` for whitespace-only search strings.

## [0.11.5] - 2026-09-16

### Added
- Added support for `.rtfmignore` and `.gitignore` file and directory exclusion patterns during section card discovery (`initialize_section_cards`, `cards scan` / `find_entry_files`).
- Added ignore pattern support to LaTeX reference graph construction (`build_reference_graph`), excluding ignored `.tex` files from dependency, reference, and label indexing.
- Added `load_ignore_spec` and `is_path_ignored` utilities using `pathspec` to load root `.gitignore` and `.rtfmignore` specifications.
- Declared explicit `pathspec>=0.10.0` dependency in `pyproject.toml`.

## [0.11.4] - 2026-09-01

### Fixed
- Fixed packaging build configuration in `pyproject.toml` where `src/writing_context_rtfm/bibtex_handoff.py` and benchmark modules were incorrectly excluded by hatchling, causing `ModuleNotFoundError: No module named 'writing_context_rtfm.bibtex_handoff'` when running CLI entrypoints installed from PyPI wheels.

## [0.11.3] - 2026-08-30

### Added
- Added `explain_context_pack` MCP tool and `writing-context-rtfm explain-pack` CLI command (and `--explain` option to `pack`) providing complete retrieval-to-pack lifecycle observability without altering generation or selection behavior.
- Added deterministic candidate identity hashing (`candidate_id`, `evidence_id`) and immutable per-candidate event tracing tracking candidate lifecycle: `retrieved` -> `normalized` (AST snapping) -> `deduplicated` -> `provider_owned` (ownership exclusion) -> `exposed` -> `filtered` (score & avoid) -> `eligible` -> `selected` / `rejected`.
- Added structured candidate funnel aggregation and canonical reason taxonomy (`FILTER_*`, `EXCLUDE_*`, `REJECT_*`) explicitly recording rejection reasons including `REJECT_PROVIDER_REFERENCE_QUOTA`, `REJECT_MAX_SOURCE_SPANS`, and `REJECT_TOKEN_BUDGET`.
- Added passive bibliographic ownership auditing (`audit_passive_bibtex_ownership`) linking excluded `.bib` candidate spans with structured provider replacements and preserving provenance.
- Added unified benchmark regression verification script (`scripts/benchmark_regression.py`) dynamically reading and verifying baseline invariants from frozen Pilot v1 manifests.
- Added Benchmark v2 specification (`benchmark/BENCHMARK_V2_SPEC.md`) detailing adversarial phenomena categories and non-regression gate criteria.
- Added GitHub Actions continuous integration workflow (`.github/workflows/ci.yml`) verifying linting (ruff), typing (mypy), full unit test suite (pytest), and benchmark regression.

### Changed
- Preserved production retrieval fusion, provider ownership rules, reference quotas, and composition policies strictly unchanged while surfacing transparent diagnostic explanations.
- Documented that diagnostics expose internal pipeline filtering decisions for observability and debugging, but do not constitute a semantic quality score or guarantee that omitted evidence is irrelevant.

### Added
- Added Zotero library selection by visible name, defaulting new configurations to `My Library`, and collection scoping through a list of unique names or full `Parent / Child` paths.
- Added frozen context-quality, candidate-exposure, and BibTeX-provider-handoff benchmarks with deterministic evaluation proxies, structured bibliography provenance, ownership audit telemetry, repeated cost measurements, and anonymized aggregate reports.
- Added optional private candidate-stage diagnostics and benchmark-only query/handoff hooks without changing production retrieval or composition defaults.

### Changed
- Scoped Zotero metadata searches to the union of configured collections and applied bounded semantic overfetch with strict item-key post-filtering, prioritizing context quality without allowing out-of-collection results into packs.
- Updated Zotero setup guidance to install the current `zotero-mcp-server[semantic]` package and initialize its semantic index explicitly.
- Retired the fixed two-condition generation script in favor of the case-driven, resumable context-quality benchmark.
- Formally closed the Pilot v1 retrieval diagnosis without promoting broad candidate expansion, BibTeX fallback/reconstruction, reference-quota changes, or a budget-aware composer.

### Fixed
- Isolated local MCP subprocess sessions by library scope and server environment, preventing one provider configuration from inheriting another library's mutable Zotero session.
- Parsed current Zotero semantic `Relevance` fields and deduplicated multi-collection results by Zotero item key.
- Replaced the masked-section identity check with deterministic anchor-group coverage, constraint, and citation-preservation proxies.
- Excluded private benchmark runners and analysis modules from distribution artifacts while retaining their source and regression fixtures in the repository.

## [0.11.1] - 2026-08-24

### Changed
- Required `rtfm-ai[embeddings]` 0.28.1 or newer for the current local index schema and retrieval behavior.

### Fixed
- Changed direct SQLite full-text queries from prefix matching to exact-token matching, preventing unrelated longer words from adding retrieval noise.
- Normalized FTS5 BM25 values relative to the strongest match and exposed stable retrieval-rank and raw-score metadata for downstream context ranking.

## [0.11.0] - 2026-08-24

### Added
- Added opt-in, in-process local semantic retrieval with content-hash/model-scoped SQLite caching and retrieval-correct query prompting for `mixedbread-ai/mxbai-embed-large-v1`.
- Added an opt-in bounded local cross-encoder reranker and a `local-models` dependency extra. Both local-model features remain disabled by default.
- Added canonical terminology guidance to writing and proofreading packs, including definitions, accepted variants, and forbidden forms that remain available when indexed prior usage cannot be retrieved.
- Expanded terminology audits to combine document glossaries with section key terms and report canonical, accepted-variant, and forbidden-form occurrences separately.

### Changed
- Selected conservative experimental defaults from local engineering canaries: cosine threshold `0.5`, reranker candidate limit `40`, and reranker blend weight `0.25`.
- Disabled automatic RTFM sync before every context pack by default. Explicit `sync` and `refresh_index` remain available, and existing configurations can opt in.

### Fixed
- Scoped OpenAI semantic cache reads to the configured embedding model instead of mixing cached vectors from different models.
- Normalized legacy scalar and structured glossary entries consistently in split cards and merged author terminology overrides without dropping unrelated generated terms.
- Prevented proofreading from falling back to external RTFM processes when optional prior-usage lookup cannot use the local SQLite index.

## [0.10.1] - 2026-08-24

### Added
- Added coverage-first context selection for explicit `must_consider` concepts and citation keys, including per-obligation coverage, provenance, and actionable recovery diagnostics in `quality.atomic_coverage`.
- Added bounded elastic expansion up to `context.max_token_budget`, allowing required evidence to take priority over token reduction without introducing repeated retrieval loops.
- Added decomposed retrieval, fusion, and structural scores; card-uncertainty telemetry; verified prior-claim provenance; source-attributed feedback summaries; and structured context-pack output modes.

### Changed
- Improved query-family ranking, MMR diversity, provider quotas, BibTeX filtering, target-range scoring, and optional RRF behavior. RRF remains disabled by default.
- Expanded cache identity and invalidation to include retrieval policy, required evidence, provider fingerprints, output mode, and model-specific embedding state.
- Updated the personal-tool roadmap to reserve `0.11.0` for local hybrid retrieval experiments using `mixedbread-ai/mxbai-embed-large-v1`, `sentence-transformers/all-MiniLM-L6-v2`, and `Alibaba-NLP/gte-reranker-modernbert-base`.

### Fixed
- Enforced strict token caps for retrieved spans while preserving one-shot elastic behavior for normal writing packs.
- Prevented informational elastic-budget notices from incorrectly degrading MCP results and made `refresh_index` report a truthful completion marker.
- Made token estimation fall back deterministically when `tiktoken` encoding data is unavailable offline.
- Restored strict `mypy` compliance across the modified context-pack, cache, server, CLI, and schema paths.

## [0.10.0] - 2026-08-23

### Added
- Implemented the documented `inspect_target_section`, `get_card_field_diff`, and `get_section_card_history` MCP tools.
- Added append-only card mutation history for accepted, rejected, edited, and deleted values.
- Exposed the existing strict token-budget mode through `get_writing_context_pack`.

### Fixed
- Advertised native MCP prompt support during server initialization.
- Aligned the MCP reference with the runtime tool schemas and removed unsupported resource claims.

## [0.9.1] - 2026-08-22

### Changed
- **Template & Build Artifact Filtering**: Extended `EXCLUDED_SOURCE_EXTENSIONS` in `utils.py` to automatically exclude LaTeX class and style files (`.cls`, `.sty`), bibliography styles (`.bst`), docstrip files (`.dtx`, `.ins`), and compilation logs/auxiliary files (`.aux`, `.log`, `.out`, `.toc`, `.synctex.gz`) from context packs. This prevents template macro definitions from polluting writing context spans when searching for terms like "draft", "subsection", or "table".

### Fixed
- **Split-Card Terminology Resolution**: Updated `get_term_context` in `features.py` to load section cards via `load_section_cards`, seamlessly resolving terminology definitions from split `cards.generated.yaml` / `cards.overrides.yaml` architectures when legacy `section_cards.yaml` is absent.

## [0.9.0] - 2026-08-22

### Added
- **Unbroken Target Section Atomicity**: When generating a context pack for a target section (e.g., in drafting or editing workflows), the complete unbroken text of the target section is extracted as high-priority (`essential`, score 1.0) `target_text`, ensuring target prose continuity without fragmented chunking.
- **Elastic Token Auto-Scaling**: Implemented intelligent dynamic budget expansion in `ContextPackGenerator` and `ProofreadPackGenerator`. When essential target prose and mandatory local context exceed an undersized budget, the generator automatically scales the budget to fit the essential context and returns `status: "complete"` with an informative expansion notice, preventing truncated prompts.
- **Markdown AST Snapping Parity**: Extended AST-aware environment snapping in `VirtualDocumentParser` and `ContextPackGenerator` to Markdown files, automatically preserving display math blocks (`$$...$$`), fenced code blocks (``` / ~~~), and tables without breaking syntax across chunk boundaries.
- **Single-File Virtual Section Resolution**: Added `find_section_node` helper to resolve and target virtual sections within monolithic files (`paper.md`, `main.tex`) by header or section ID, enabling full section card targeting for single-file manuscripts without splitting the document.
- **Comprehensive Quality Test Suites**: Added deep test suites covering AST utilities, multi-layer section card merging, MCP JSON-RPC server handlers, CLI subcommands, and semantic extractor fallback chains, elevating total test coverage past 80% with 249 unit tests.

### Changed
- **Two-Tier Agent Protocol & Soft-Gatekeeping**: Updated agent guidelines across CLI initialization (`cli.py`), `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` to establish a clear two-tier protocol: agents retrieve curated context first via `get_writing_context_pack` / `get_proofreading_context_pack`, with authorized autonomous fallback to direct file reading when whole-chapter narrative flow or continuous prose synthesis is required.

### Fixed
- **Cache Invalidation on Fresh Workspaces**: Handled uninitialized SQLite tables gracefully during `invalidate_for_fingerprint` and `clear` operations in `ExtensionStore`.
- **Global Workspace Root Isolation in MCP Tests**: Added proper test isolation to prevent module-level `WORKSPACE_ROOT` leakage across test executions.

## [0.8.1] - 2026-08-19

### Added
- **Native Offline BibTeX Provider**: Introduced `BibTeXProvider` to resolve local bibliography `.bib` files directly without requiring an external Zotero instance or active network connection. Automatically extracts title, authors, year, abstract, venue, and DOI metadata.
- **1-Hop Reference Graph Traversal**: Automatically resolves `\ref{...}` labels in the target section, querying the manuscript AST to inject defining snippets for referenced figures, tables, equations, and subsections into the context pack.
- **AST-Aware Environment Snapping**: Automatically detects and expands retrieved LaTeX slices outward if they intersect structural environments (`equation`, `align`, `table`, `figure`, `algorithm`, `lstlisting`, `proof`), preventing severed syntax in LLM prompts.
- **Reciprocal Rank Fusion (RRF)**: Implemented `apply_reciprocal_rank_fusion` to unify multi-stream candidate rankings across FTS5 keyword search, dense embeddings, BibTeX/Zotero literature, and reference graph links.
- **Maximal Marginal Relevance (MMR) Diversity**: Added Jaccard-based MMR re-ranking to penalize semantic overlap in literature and background snippets, maximizing information diversity within the token budget.
- **In-Process SQLite FTS5 Fast-Path**: Added direct SQLite FTS5 query execution with BM25 ranking (`_direct_sqlite_search`) in `RTFMAdapter` when `library.db` exists, bypassing subprocess overhead for sub-millisecond local search.
- **AST Parse Memoization**: Added in-memory AST caching in `VirtualDocumentParser` to skip redundant AST traversals on unchanged `.tex` and `.md` files.
- **Parallel Citation Resolution**: Parallelized multi-citation key lookups in `ZoteroProvider` using `ThreadPoolExecutor`.
- **Strict Budget Control**: Added `strict_budget: bool` parameter to `ContextPackGenerator.generate` for hard token cap enforcement.

### Changed
- **Split Section Cards Support (Version 2)**: Fully integrated `cards.overrides.yaml` (version 2) and `cards.generated.yaml` merging across CLI commands and MCP server tools.
- **Standardized Content Hashing**: Replaced MD5 hashing in `virtual_doc.py` with SHA-256 (`stable_hash`).
- **Resource Lifecycle Safety**: Implemented deterministic `__enter__`, `__exit__`, and `__del__` connection cleanup on `ExtensionStore` to prevent SQLite connection leaks under Python 3.14 GC, and optimized inserts with `executemany`.
- **Type Safety**: Achieved 100% strict `mypy` type annotation coverage across all 25 source files.

### Deprecated
- **`init-cards` CLI Command**: Marked `init-cards` as deprecated in favor of `cards scan` and `cards build`.

## [0.7.6] - 2026-08-13

### Changed
- **Modernized Prompt Template (`prompts/generate_section_cards.md`)**: Updated the prompt template to use `version: 2` main-section card schema, incorporating in-workspace file auto-discovery and optional targeted file parameter overrides.

## [0.7.5] - 2026-08-13

### Added
- **Auto-Repair Codex Config**: `writing-context-rtfm init` now automatically inspects `~/.codex/config.toml` and repairs stale `.venv` command paths for `writing-context-rtfm`, ensuring seamless MCP tool loading in Codex sessions.

## [0.7.4] - 2026-08-13

### Added
- **Module Execution (`__main__.py`)**: Added package `__main__.py` entrypoint allowing direct execution via `python -m writing_context_rtfm`.
- **Comprehensive Agent Rules**: Updated `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` rule generators to document the complete 17-tool MCP capability suite.

## [0.7.3] - 2026-08-12

### Fixed
- **Direct API Provider Configuration**: Exempted non-MCP direct API providers (such as `openai_semantic` and `huggingface`) from requiring `mcp_server` or `sse_url` configuration when `enabled: true` in `.writing-context/config.yaml`.

## [0.7.2] - 2026-08-07

### Fixed
- **Claude MCP Server Auto-Approval**: `writing-context-rtfm init` now automatically registers `"writing-context-rtfm"` in `.claude/settings.json` under `enabledMcpjsonServers`, ensuring MCP tools are exposed and authorized without manual configuration.
- **MCP Tool Hook Schema (`input` Key)**: Updated `PostToolUse` MCP hook generation in `.claude/settings.json` to use `"input": {}` instead of `"arguments"`, matching Claude Code hook payload specifications and defaulting cleanly to the project root.
- **SessionEnd Hook Clean Schema**: Omitted unnecessary `matcher` from `SessionEnd` hook definitions in `settings.json`.

## [0.7.1] - 2026-08-07

### Fixed
- **Claude Settings Hooks Format**: Fixed `SessionEnd` hook schema generated in `.claude/settings.json` during project initialization (`init`). Every hook array item now includes a `matcher` string and a `hooks` array, resolving Claude Code schema validation errors (`hooks.SessionEnd.0.hooks: Expected array, but received undefined`) and auto-repairing existing flat legacy entries.

## [0.7.0] - 2026-08-07

### Added
- **Main-Section Card Architecture**: Consolidated section card creation to exclusively organize cards by top-level main sections (`\section` in papers/articles, `\chapter` in books/theses). Each main section card's character span encompasses its full content including all child subsections, concentrating context and eliminating subsection duplication.
- **`cards rebuild` Command**: Added `writing-context-rtfm cards rebuild` command to cleanly clear existing generated section cards and perform a fresh scan + inference pass.
- **Pre-Formatted Prompt & Guidance Returns**: MCP tools `get_writing_context_pack` and `get_proofreading_context_pack` now return a pre-rendered `formatted_prompt` template string and high-level execution `guidance` hints for instant downstream AI execution.
- **Explicit LaTeX Immutability Rules**: Scanned LaTeX commands (`\cite`, `\ref`, `\label`, math environments) in proofread target spans are now explicitly embedded as immutable rules in section constraints.

## [0.6.1] - 2026-06-25

### Added
- **Card Scaffolding Model Fallback Chain**: Implemented an automated fallback chain for `cards build` / `cards infer` semantic extraction. If no OpenAI API key is configured, the system now cascades gracefully from OpenAI -> Hugging Face Serverless Inference API (requires `HF_TOKEN` / `HF_API_TOKEN`, defaulting to `Qwen/Qwen2.5-Coder-7B-Instruct`) -> Local Ollama instance (auto-detects local running server at `http://localhost:11434`, defaulting to `qwen2.5-coder` or `phi3`) -> Deterministic Offline Scan (LaTeX document tree scan) as a final resort.
- **Hugging Face Auth CLI Support**: Added support for authenticating and caching Hugging Face API tokens via `writing-context-rtfm auth huggingface <token>`.
- **Custom Card Scaffolding Generator Configuration**: Exposed the `generator` block in `config.yaml` to allow users to explicitly specify the model name, API endpoint base URL, and credentials for card inference.

## [0.6.0] - 2026-06-01

### Added
- **OpenAI Semantic Search Provider**: Integrated `OpenAISemanticSearchProvider` as an optional extension to overlay semantic search on top of RTFM's lexical index. This uses `numpy` for zero-friction in-memory cosine similarity instead of complex SQLite VSS extensions.
- **CLI Auth Command**: Added `writing-context-rtfm auth <provider> <token>` command to securely store API keys directly in the local `.writing-context/context_cache.sqlite` database, bypassing the need for environment variables.
- **Configurable Sync Strategies**: `openai_semantic` provider configuration now supports `auto_sync` (defaults to lazy loading) to balance context latency and API costs.

## [0.5.5] - 2026-05-27

### Added
- **Automated Hook Installation**: Integrated client-side lifecycle hook configuration into the `init` command. When initializing the project, it automatically registers a `PostToolUse` hook in `.claude/settings.json` that calls `refresh_index` on the MCP server upon successful agent writes/edits, ensuring the index and cache remain perfectly fresh.

## [0.5.4] - 2026-05-26

### Added
- **Completed CLI Reference**: Fully documented all previously missing commands (`init-db`, `inspect-target`, `get-term`, `show-graph`, `cache clear`/`stats`) in the main README.
- **Onboarding Guides for Remote Workflows**: Documented instructions for starting a new project in empty repositories and bridging Overleaf projects to local workspaces.
- **Interactive Sync Progress**: Enabled streaming of the RTFM sync process output directly to the console when run from the CLI. This provides real-time progress indicators during file crawling and embedding computation.

### Changed
- **Replaced Folder Exclusions with Gitignore**: Removed hardcoded path exclusions (`.codex/`, `.claude/`, `.github/`, etc.) from the source code, delegating all user-directory exclusions to `.gitignore` patterns while keeping minimal system-directory ignores (`.writing-context/`, `.rtfm/`, `.git/`, `__pycache__/`).
- **Silent Adapter Execution in MCP Server**: Kept command execution silent (`capture_output=True`) by default when running under the MCP server to prevent stdio stream pollution and protocol corruption.

### Documented
- **RTFM & Local Embeddings Onboarding**: Added clear instructions for installing the `rtfm-ai` CLI dependency and described the local, offline behavior of the default embedding model (`MiniLM`).

## [0.5.3] - 2026-05-25

### Added
- **CLI Version Option**: Added `-V`/`--version` option to the command-line interface to easily print the installed package version.

## [0.5.2] - 2026-05-25

### Changed
- **Default Embeddings Extra**: Updated the `rtfm-ai` dependency to default to `rtfm-ai[embeddings]` so that semantic and hybrid search capability works out-of-the-box.

### Fixed
- **RTFM Sync Override**: Refactored the `RTFMAdapter.sync` method, CLI command, and MCP server handlers to not pass explicit path and corpus overrides by default. This ensures the configuration inside `.rtfm/config.json` governs the sync process instead of being ignored, preventing the ingestion of non-manuscript files from the project root.

## [0.5.1] - 2026-05-25

### Added
- **Detailed Section Cards Template**: Updated the `init` command to generate a fully-commented template in `section_cards.yaml`, including pre-scaffolded abstract/introduction/methodology structures and guidance on global thesis, style guidelines, and terminology definitions.
- **Improved GitHub Actions CI/CD Caching**: Opted into Node.js 24 environment in GitHub Actions runner and mapped uv caching strategy to depend on `pyproject.toml` instead of the gitignored `uv.lock`.

## [0.5.0] - 2026-05-22

### Added
- **Enhanced Project Initialization**: `init` command now auto-configures and updates `.gitignore` to ignore the local database, `.mcp.json` to register the MCP server (auto-detecting `uv`), and guideline files (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`) with rules blocks.
- **Rules Anchoring**: Guidelines are updated non-destructively using comments `<!-- writing-context-rtfm MCP tools -->` to prevent duplication.
- **Self-Documenting Configuration**: The generated config template is fully commented, explaining how to configure default budgets, reserved margins, and role budget allocations.
- **Unified Token Budget Tuning**: Spans dropped due to token limit caps calculate and recommend a minimum target budget size to allow LLM client agents to self-correct dynamically.

### Changed
- **Dynamic Server Versioning**: Server initialization dynamically returns the package version instead of a hardcoded string.

### Fixed
- **UnboundLocalError in Context Packer**: Corrected variable naming to prevent referencing unbound variables when checking target source spans.

---

## [0.1.0] - 2026-05-20

### Added
- **Interactive Scaffolding**: Added `initialize_section_cards` tool and CLI subcommand to auto-scan `.tex`/`.md` files and populate `.writing-context/section_cards.yaml`.
- **Context Pagination**: Added support for progressive retrieval with `request_more_context` and SQLite schema integration (`selected` flag) to load remaining background context spans.
- **Feedback Loops**: Added evaluation recording using `submit_generation_feedback` to persist metric scores (`helpfulness`, `hallucinations`, `constraint_violated`) into `evaluation_records`.
- **Terminology Auditing**: Added `audit_manuscript_terminology` tool to cross-examine key terms in section cards against index occurrences and detect undeclared usage or missing definitions.
- **Native Prompts Integration**: Exposed standard MCP prompt endpoints (`write_section`, `proofread_section`).
- **Cache Management CLI**: Added subcommands `writing-context-rtfm cache stats` and `writing-context-rtfm cache clear`.

### Changed
- **Robust Cache Invalidation**: Migrated static caching hashes to SHA-256 content hashes.
- **Dynamic Fingerprinting**: Replaced the static index fingerprint with a dynamic SHA-256 value computed from the modification time and file size of `.rtfm/library.db`.
- **Dependency Classification**: Classify retrieved key terms for dependencies under `"dep_key_term"`.

### Fixed
- **Proofreading Bounds Clamping**: Swapped and clamped invalid line numbers early during proofread context generation.
- **Path Normalization**: Resolved relative paths to absolute paths before validating file matches.
- **Search Resilience**: Wrapped terminology searches in try-except blocks.
