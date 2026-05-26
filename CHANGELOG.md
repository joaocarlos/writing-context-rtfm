# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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
