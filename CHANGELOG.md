# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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
