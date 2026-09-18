# Architecture Boundaries: writing-context-rtfm & rtfm-ai

This document defines the architectural boundary, ownership separation, and integration contract between **`writing-context-rtfm`** and the underlying retrieval engine **`rtfm-ai`**.

---

## 1. Context & Motivation

`writing-context-rtfm` is a specialized Model Context Protocol (MCP) server designed for academic writing workflows in LaTeX and Markdown. It curates minimal, task-focused context packs for AI agents, enforcing token budgets, document structure, and LaTeX syntactic safety.

To perform keyword and semantic retrieval across manuscripts and research notes, `writing-context-rtfm` delegates raw text indexing and search to `rtfm-ai`. However, directly coupling all internal operations to `rtfm-ai`'s internal data structures and CLI poses architectural risks:

1. Upstream schema or flag changes could break MCP operations.
2. In-process features (like LaTeX AST snapping and token budgeting) must not leak into the generic retrieval layer.
3. Users and developers must clearly understand where responsibilities lie.

To mitigate these risks, `writing-context-rtfm` implements an **Anti-Corruption Layer (ACL)** backed by a strict **Contract Testing Suite**.

---

## 2. Domain Responsibility Matrix

| Feature / Responsibility | Owned by `writing-context-rtfm` | Owned by `rtfm-ai` | Rationale |
| :--- | :---: | :---: | :--- |
| **LaTeX & Markdown AST Parsing** | **Yes** | No | Snapping chunk boundaries around equations, environments (`align`, `tabular`), and code blocks is specific to manuscript authoring. |
| **Section Hierarchy & Split Cards** | **Yes** | No | `cards.generated.yaml` and `cards.overrides.yaml` manage section purposes, dependencies, and constraints. |
| **Token Budget & Elastic Packing** | **Yes** | No | Prioritizing `essential` vs `supporting` context, unbroken target preservation, and budget enforcement are curation concerns. |
| **1-Hop Reference Graph Traversal** | **Yes** | No | Resolving `\ref{}`, `\label{}`, and math environments across document files belongs to authoring intelligence. |
| **Offline BibTeX Parser** | **Yes** | No | Native extraction of citations, abstracts, and DOIs from local `.bib` files without external servers. |
| **Zotero MCP Provider Bridge** | **Yes** | No | Bridging collection-scoped semantic literature search and citation key resolution into writing context. |
| **MCP Server & Tool Handlers** | **Yes** | No | Exposing `get_writing_context_pack`, `get_proofreading_context_pack`, `audit_manuscript_terminology`, etc. |
| **Diagnostic Doctor CLI** | **Yes** | No | Holistic diagnosis of Python, dependencies, index health, Zotero, API keys, and local model hardware. |
| **File Chunking & Storage** | No | **Yes** | Generic document chunking, book metadata tracking, and SQLite table management. |
| **SQLite FTS5 Full-Text Search** | No | **Yes** | High-performance BM25-ranked full-text search index (`chunks_fts`). |
| **FastEmbed Vector Embeddings** | No | **Yes** | Generating local dense vector representations and cosine similarity indexes. |
| **Corpus Synchronization (`rtfm sync`)** | No | **Yes** | Scanning workspace directories, hashing files, and updating the retrieval database. |

---

## 3. The Anti-Corruption Layer (`RetrievalEngine`)

All interactions between `writing-context-rtfm` and `rtfm-ai` are decoupled through the `RetrievalEngine` protocol defined in `src/writing_context_rtfm/retrieval.py`.

```
┌─────────────────────────────────────────────────────────────┐
│                    writing-context-rtfm                     │
│  (ContextPackGenerator, ProofreadPackGenerator, MCP Server) │
└──────────────────────────────┬──────────────────────────────┘
                               │ relies on
                               ▼
┌─────────────────────────────────────────────────────────────┐
│              <<protocol>> RetrievalEngine                   │
│   + search(query, limit) -> list[dict]                      │
│   + context(query, limit) -> list[dict]                     │
│   + expand(chunk_id, depth) -> list[dict]                   │
│   + sync() -> bool                                          │
│   + health_check() -> RetrievalEngineHealth                 │
│   + get_fingerprint() -> str                                │
│   + get_db_path() -> Path | None                            │
└──────────────────────────────▲──────────────────────────────┘
                               │ implements
┌──────────────────────────────┴──────────────────────────────┐
│                        RTFMAdapter                          │
│  • Direct SQLite FTS5 fast-path                             │
│  • CLI fallback execution (`rtfm search`, `rtfm sync`)      │
│  • Graceful degradation when index or CLI is absent         │
└──────────────────────────────┬──────────────────────────────┘
                               │ accesses
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                   rtfm-ai (library.db)                      │
│   Tables: chunks, books, chunks_fts                         │
└─────────────────────────────────────────────────────────────┘
```

### Key Guarantees of the Protocol

1. **Isolation from Subprocess Failures**: If the `rtfm` binary is not found on `PATH` or encounters an error, the adapter raises a typed `RetrievalEngineError` (or `RTFMAdapterError`), allowing generators to gracefully degrade (e.g. proofreading falls back to target text and glossary rules without crashing).
2. **Short-Circuit Query Hygiene**: Queries consisting solely of whitespace or empty strings are safely resolved to empty results without invoking external subshells or SQLite queries.
3. **Engine Health Diagnostic**: The engine reports structured `RetrievalEngineHealth` (chunk count, book count, embedding availability, and error strings) directly consumed by `writing-context-rtfm doctor`.
4. **Content Fingerprinting**: The engine provides `get_fingerprint()` derived from chunk/book counts and timestamps to invalidate cached context packs when the corpus changes.

---

## 4. Upstream Dependency & Version Maintenance Policy

### Open Upper-Bound Dependency (`>=`)
In `pyproject.toml`, `rtfm-ai` is pinned with an open minimum version:
```toml
dependencies = [
    "rtfm-ai[embeddings]>=0.46.1",
    ...
]
```

**Rationale**:
- `rtfm-ai` is actively developed. Restricting users to older versions creates friction when improvements or fixes are released upstream.
- Users can install or run `writing-context-rtfm` alongside the latest `rtfm-ai` without dependency conflicts in virtual environments or via `uvx`/`pipx`.

### Continuous Contract Testing (`tests/test_rtfm_contract.py`)
To ensure compatibility with upstream updates without silent regressions, the test suite includes a dedicated contract verification suite that executes against the latest installed `rtfm-ai`:

1. **CLI Flag Contract**: Verifies that `rtfm --help`, `rtfm sync --help`, `rtfm search --help`, and `rtfm embed --help` support the expected command interfaces.
2. **SQLite Schema Contract**: Verifies that `library.db` continues to provide the expected tables (`chunks`, `books`, `chunks_fts`) and column structures (`content`, `book_id`, `chunk_index`).
3. **Protocol Adherence**: Verifies that `RTFMAdapter` satisfies the `RetrievalEngine` protocol under both real and mocked database configurations.
4. **Degradation Contract**: Ensures that missing databases or corrupted files trigger clean, typed exceptions rather than unhandled tracebacks.

### Development Workflow for Upstream Releases
When a new version of `rtfm-ai` is published:
1. Run `uv sync --upgrade` or `pip install --upgrade rtfm-ai` in the development environment.
2. Run `pytest tests/test_rtfm_contract.py` to evaluate the retrieval contract.
3. If contract tests pass, run the full test suite (`pytest`) to guarantee end-to-end reliability before cutting a new release.
