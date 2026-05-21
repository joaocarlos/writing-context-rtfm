# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-05-20

### Added

- **Interactive Scaffolding**: Added `initialize_section_cards` tool and CLI subcommand to auto-scan `.tex`/`.md` files and populate `.writing-context/section_cards.yaml`.
- **Context Pagination**: Added support for progressive retrieval with `request_more_context` and SQLite schema integration (`selected` flag) to load remaining background context spans.
- **Feedback Loops**: Added evaluation recording using `submit_generation_feedback` to persist metric scores (`helpfulness`, `hallucinations`, `constraint_violated`) into `evaluation_records`.
- **Terminology Auditing**: Added `audit_manuscript_terminology` tool to cross-examine key terms in section cards against index occurrences and detect undeclared usage or missing definitions.
- **Native Prompts Integration**: Exposed standard MCP prompt endpoints:
    - `write_section` (pre-formatted user prompt with surgical retrieval context, thesis, and constraints).
    - `proofread_section` (pre-formatted proofreading instructions with target content, local paragraphs, and terminology rules).
- **Cache Management CLI**: Added subcommands `writing-context-rtfm cache stats` and `writing-context-rtfm cache clear` for cache diagnostic analysis.

### Changed

- **Robust Cache Invalidation**: Migrated static caching hashes to SHA-256 content hashes of both `.writing-context/config.yaml` and `.writing-context/section_cards.yaml`.
- **Dynamic Fingerprinting**: Replaced the static index fingerprint with a dynamic SHA-256 value computed from the modification time and file size of `.rtfm/library.db` to automatically invalidate stale packs.
- **Dependency Classification**: Classify retrieved key terms for dependencies under `"dep_key_term"`, adjusting penalties and formatting reasons accordingly.

### Fixed

dency Classification\*\*: Classify retrieved key terms for dependencies under `"dep_key_term"`, adjusting penalties and formatting reasons accordingly.

### Fixed

- **Proofreading Bounds Clamping**: Swapped and clamped invalid line numbers early during proofread context generation to prevent negative python slicing index errors.
- **Path Normalization**: Resolved relative paths to absolute paths before validating file matches in proofreader/term lookup.
- **Search Resilience**: Wrapped terminology searches in try-except blocks to gracefully handle empty or un-indexed adapters.
