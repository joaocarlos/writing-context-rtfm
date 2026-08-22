# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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
