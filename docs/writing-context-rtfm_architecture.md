# writing-context-rtfm Architecture Documentation

## 1. Preliminary Analysis

`writing-context-rtfm` is a lightweight MCP extension designed to reduce token usage when an AI agent writes, rewrites, expands, or reviews a document that depends on prior project context. The extension does **not** replace RTFM, does **not** fork RTFM, and does **not** implement a full manuscript knowledge graph in its initial version.

The core architectural decision is:

```text
RTFM retrieves.
writing-context-rtfm decides what is enough context to write.
```

RTFM remains the indexing and retrieval substrate. It is responsible for parsing project files, storing indexed chunks, exposing search/context/expand capabilities, and maintaining the retrieval index. `writing-context-rtfm` sits above that layer and produces compact, task-specific **writing context packs** for CLI agents.

The main architectural areas are:

+ **RTFM integration**: use RTFM as the retrieval backend through a narrow adapter.
+ **Context-pack generation**: interpret the writing task, query RTFM, rank candidate spans, and fit them into a token budget.
+ **Hybrid storage**: keep author-maintained metadata in YAML and generated runtime state in a separate extension-owned SQLite database.
+ **MCP interface**: expose a small set of agent-facing tools, especially `get_writing_context_pack`.
+ **Section cards**: maintain lightweight, human-readable document guidance for section roles, dependencies, terms, and constraints.
+ **Verification**: measure token reduction, relevance, cache correctness, and agent behavior.

## 2. Architectural Principles

### 2.1 Keep RTFM as a Dependency, Not a Fork

The extension should depend on RTFM instead of copying or modifying its features. RTFM already solves file indexing, retrieval, sync, and source-span discovery. Rebuilding those capabilities would shift the project away from its real goal: reducing token usage during writing tasks.

The extension should interact with RTFM through one of the following, in order of preference:

1. RTFM CLI or official API.
2. RTFM MCP tools, when appropriate.
3. Direct SQLite reads only behind an experimental adapter.

The extension should **not** write to RTFM’s database.

### 2.2 Keep the First Version Narrow

The initial version should not attempt to build a full claim-evidence graph. That may become useful later, but it would increase scope and delay practical value.

The first useful version should answer this question:

```text
Given this writing task, what previous context does the agent need, and what can be ignored?
```

### 2.3 Use Hybrid Storage

The extension should use four storage layers with clearly separated responsibilities:

```text
RTFM SQLite database
  → retrieval index managed by RTFM

.writing-context/cards.generated.yaml
  → automatically scaffolded writing metadata and section cards

.writing-context/cards.overrides.yaml
  → human-maintained overrides, thesis, terminology, and specific section edits

.writing-context/cards.lock.json
  → lock file tracking versioning and checksum metadata for generation integrity

.writing-context/config.yaml
  → human-maintained project configuration

.writing-context/context_cache.sqlite
  → generated cache, run history, retrieval events, and evaluation records
```

YAML is appropriate for small, human-maintained information because it is easy to edit, inspect, diff, and version with the manuscript. SQLite is appropriate for generated data because it supports structured queries, caching, history, and performance monitoring.

## 3. Revised Storage Architecture

### 3.1 Storage Layout

The recommended project layout is:

```text
user-manuscript-repository/
│
├── .rtfm/
│   └── library.db
│       └── managed by RTFM
│
├── .writing-context/
│   ├── config.yaml
│   │   └── human-maintained configuration
│   │
│   ├── cards.generated.yaml
│   │   └── automatically scaffolded metadata and section cards
│   │
│   ├── cards.overrides.yaml
│   │   └── human-authored overrides, constraints, and thesis
│   │
│   ├── cards.lock.json
│   │   └── lock file for generation integrity
│   │
│   └── context_cache.sqlite
│       └── extension-generated cache and run history
│
└── manuscript files
    ├── sections/*.tex
    ├── *.bib
    ├── docs/*.md
    ├── notebooks/*.ipynb
    └── notes/*
```

### 3.2 RTFM-Owned Storage

RTFM owns the retrieval index. The extension treats this database as an implementation detail of RTFM.

RTFM is responsible for:

+ indexing files;
+ chunking content;
+ maintaining search indexes;
+ tracking file state;
+ exposing retrieval results;
+ resolving source spans;
+ refreshing changed files.

The extension should not mutate RTFM tables.

### 3.3 Author-Owned and Generated Cards Layout

Card metadata is split to support both automated scaffolding and manual control:

+ **`.writing-context/config.yaml`**: Main configuration file (human-maintained).
+ **`.writing-context/cards.overrides.yaml`**: Author-owned overrides containing custom constraints, terminology, style definitions, and specific section edits.
+ **`.writing-context/cards.generated.yaml`**: Generated and updated automatically via the `cards build` subcommand. Do not edit directly.
+ **`.writing-context/cards.lock.json`**: Lock file for tracking versioning and checksum metadata of parsed manuscript files.

These files express intent and project structure, and are designed to be version-controlled.

### 3.4 Extension-Owned SQLite Database

The extension may create:

```text
.writing-context/context_cache.sqlite
```

This database is generated and can be deleted and rebuilt. It stores:

+ context-pack runs;
+ selected source spans;
+ context-pack payloads;
+ retrieval events;
+ token estimates;
+ evaluation records.

## 4. Agent-Facing Workflow

The desired workflow is:

```text
User asks agent to write or revise
  ↓
Agent calls writing-context-rtfm MCP tool
  ↓
Extension loads config.yaml
  ↓
Extension merges cards.generated.yaml and cards.overrides.yaml
  ↓
Extension queries RTFM through adapter
  ↓
Extension ranks and packs source spans
  ↓
Extension returns compact writing context pack
  ↓
Agent writes using only the returned context unless more is needed
```

The central behavior is that the agent must call `get_writing_context_pack` before reading broad project context. This is what reduces token usage.

## 5. Step-by-Step Architecture

## Step 1: Project Setup and Scope Control

### Objective

Create a standalone Python project that depends on RTFM and provides a small MCP extension for writing-context retrieval.

### Inputs

+ Existing manuscript or document repository.
+ RTFM installed and functional.
+ Python 3.11 or later.
+ `uv`, `pipx`, or a standard virtual environment.
+ Assumption: RTFM can index the project’s `.tex`, `.md`, `.bib`, notes, and related files with acceptable chunk quality.

### Requirements

Functional requirements:

+ The project must install independently from RTFM.
+ The project must call RTFM through a dedicated adapter.
+ The project must expose an MCP server.

Non-functional requirements:

+ Avoid modifying RTFM internals.
+ Keep the first version small and deterministic.
+ Avoid introducing a full argument graph in v0.1.

### Functionality

Build the initial repository scaffold:

```text
writing-context-rtfm/
├── pyproject.toml
├── README.md
├── src/writing_context_rtfm/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── rtfm_adapter.py
│   ├── section_cards.py
│   ├── context_pack.py
│   ├── storage.py
│   ├── hashing.py
│   └── server.py
└── tests/
```

### Architecture Notes

`writing-context-rtfm` should not become an indexing engine. Its boundary is context selection, not content ingestion.

### Artifacts

+ Repository scaffold.
+ `pyproject.toml`.
+ Initial README.
+ Example `.writing-context/config.yaml`.
+ Example split cards layout.

### Tests

+ Verify package imports.
+ Verify RTFM CLI is discoverable.
+ Verify test project can be indexed by RTFM.

### Progress Checklist

+ [x] Repository scaffold created.
+ [ ] RTFM installed and verified. (Skipped due to sandbox constraints)
+ [ ] Example manuscript indexed. (Skipped due to sandbox constraints)
+ [x] Package imports successfully.
+ [x] Basic tests pass.

## Step 2: RTFM Adapter

### Objective

Provide a narrow interface between the extension and RTFM. The rest of the extension should not depend on RTFM command syntax, output shape, or database details.

### Inputs

+ RTFM CLI or API.
+ RTFM corpus name.
+ Project path.
+ Configuration from `.writing-context/config.yaml`.

### Requirements

Functional requirements:

+ Search indexed content.
+ Retrieve context around a file span.
+ Expand selected results.
+ Trigger RTFM sync when requested.

Non-functional requirements:

+ Must not write to RTFM’s database.
+ Must be mockable for tests.
+ Must handle RTFM failures clearly.

### Functionality

Implement `RTFMAdapter` with methods:

```python
class RTFMAdapter:
    def search(self, query: str, *, corpus: str, limit: int) -> list[RTFMResult]: ...
    def context(self, path: str, line_start: int, line_end: int) -> str: ...
    def expand(self, result_id: str) -> str: ...
    def sync(self, project_root: str, *, corpus: str) -> SyncResult: ...
```

### Architecture Notes

Use the RTFM CLI or official API first. Direct SQL reads should be optional and experimental because they couple the extension to RTFM’s private schema.

### Artifacts

+ `rtfm_adapter.py`.
+ `RTFMResult` schema.
+ Adapter test fixtures.

### Tests

+ Mock RTFM output.
+ Simulate search failures.
+ Simulate empty results.
+ Run integration test against a mini indexed project.

### Progress Checklist

+ [x] Adapter class implemented.
+ [x] Search implemented.
+ [x] Context retrieval implemented.
+ [x] Sync wrapper implemented.
+ [x] Unit and integration tests pass.

## Step 3: Configuration and Section Cards

### Objective

Define the configuration and split-card writing metadata used by the context-pack generator.

### Inputs

+ `.writing-context/config.yaml`.
+ `.writing-context/cards.generated.yaml`.
+ `.writing-context/cards.overrides.yaml`.
+ `.writing-context/cards.lock.json`.
+ Project structure.
+ Author-maintained section information.

### Requirements

Functional requirements:

+ Load configuration from YAML.
+ Load generated and overrides cards from YAML, using a lock file for integrity checks.
+ Merge split cards at runtime prioritizing user-authored overrides.
+ Provide safe defaults and offline scan fallback when models or keys are absent.

Non-functional requirements:

+ Split cards must be readable by humans (YAML).
+ Files must be suitable for Git diffs.
+ Overrides should be cleanly separated from machine-generated output.

### Functionality

Recommended configuration:

```yaml
version: 1

rtfm:
  corpus: manuscript
  project_root: .
  sync_before_pack: false  # Opt in only after verifying the local RTFM lifecycle.

context:
  default_token_budget: 12000
  reserved_generation_margin: 0.10
  max_search_results_per_query: 10
  max_source_spans: 35
  include_source_excerpts: false

cache:
  enabled: true
  path: .writing-context/context_cache.sqlite
  invalidate_on_refresh: true

section_cards:
  path: .writing-context/section_cards.yaml  # Serves as base directory resolver for split files
  required: false
```

#### Cards Merge Dataflow

At runtime, the merge engine reads `cards.generated.yaml`, `cards.overrides.yaml`, and `cards.lock.json` from the target directory and resolves conflicts using the following priority flow:
1. **Document-Level properties**: Title, thesis, writing style, and terminology glossaries are loaded from `cards.overrides.yaml` first, falling back to `cards.generated.yaml`. Terminology glossaries merge definitions, variants, and words to avoid.
2. **Sections list**: Evaluates the union of section IDs between overrides and generated cards.
3. **Properties per Section**: Title, path, purpose/role are taken from overrides if present, falling back to generated values.
4. **Key Terms & Dependencies**: Overrides completely replace the generated lists if defined. If not defined, candidate values from the generated card are loaded, omitting any marked as `rejected`.
5. **Facts & Constraints**: High-confidence generated items (marked `accepted` or `verified`) are merged with author-defined facts/constraints in overrides.

### Architecture Notes

Section cards remain in YAML split files because they are author-maintained and machine-generated metadata. They should not be stored primarily in SQLite in v0.1.

### Artifacts

+ `config.py`.
+ `section_cards.py`.
+ Example config file.
+ Example generated cards file.
+ Example overrides cards file.

### Tests

+ Missing config uses defaults.
+ Legacy `section_cards.yaml` is migrated to the split structure automatically with backups.
+ Merging generated and overrides produces correct compiled cards.
+ Invalid YAML produces clear errors.

### Progress Checklist

+ [x] Config schema implemented.
+ [x] Split section-card schema implemented.
+ [x] Runtime merge logic implemented.
+ [x] Backup and migration paths implemented.
+ [x] Tests pass.

## Step 4: Extension-Owned SQLite Storage

### Objective

Store generated context-pack state without modifying RTFM’s database.

### Inputs

+ `.writing-context/context_cache.sqlite` path.
+ Generated context packs.
+ Retrieval events.
+ Token estimates.
+ Evaluation records.

### Requirements

Functional requirements:

+ Initialize the extension database.
+ Cache generated packs.
+ Store selected source spans.
+ Store retrieval events.
+ Invalidate cache after sync or metadata changes.

Non-functional requirements:

+ Database initialization must be idempotent.
+ Generated state must be safely deletable.
+ Schema must support migration.

### Functionality

Create `storage.py` with:

```python
class ExtensionStore:
    def init_db(self) -> None: ...
    def get_cached_pack(self, cache_key: CacheKey) -> ContextPack | None: ...
    def store_pack(self, run: ContextPackRun, pack: ContextPack) -> None: ...
    def invalidate_for_fingerprint(self, fingerprint: str) -> None: ...
    def clear(self) -> None: ...
```

Minimum tables:

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS context_pack_runs (
    run_id TEXT PRIMARY KEY,
    task_hash TEXT NOT NULL,
    task TEXT NOT NULL,
    target TEXT,
    corpus TEXT,
    token_budget INTEGER NOT NULL,
    config_hash TEXT,
    section_cards_hash TEXT,
    rtfm_index_fingerprint TEXT,
    extension_version TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS context_pack_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    path TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    score REAL,
    reason TEXT,
    rank INTEGER,
    query TEXT,
    metadata_json TEXT,
    FOREIGN KEY (run_id) REFERENCES context_pack_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS context_pack_payloads (
    run_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    estimated_tokens INTEGER,
    source_count INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES context_pack_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS retrieval_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    query TEXT NOT NULL,
    result_count INTEGER,
    elapsed_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES context_pack_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evaluation_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    metric_text TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES context_pack_runs(run_id) ON DELETE CASCADE
);
```

### Architecture Notes

This SQLite database is not a source of truth for author intent. It is generated state. It may be deleted and rebuilt.

### Artifacts

+ `storage.py`.
+ SQL migration file or embedded migration constants.
+ `init-db` CLI command.

### Tests

+ Database initialization creates all tables.
+ Initialization is idempotent.
+ Packs are cached and retrieved.
+ Cache invalidation removes stale rows.

### Progress Checklist

+ [x] `ExtensionStore` implemented.
+ [x] SQLite schema implemented.
+ [x] `init-db` command implemented.
+ [x] Cache read/write implemented.
+ [x] Tests pass.

## Step 5: Context-Pack Generation

### Objective

Generate compact writing context packs from RTFM results, section cards, and task-specific constraints.

### Inputs

+ Writing task.
+ Target section or file.
+ Token budget.
+ RTFM search results.
+ Section cards.
+ Cache state.

### Requirements

Functional requirements:

+ Build search queries from the task and section metadata.
+ Retrieve candidate spans through RTFM.
+ Rank and deduplicate spans.
+ Fit selected spans within token budget.
+ Return structured context-pack output.
+ Use cache when valid.

Non-functional requirements:

+ Deterministic output for identical inputs.
+ Clear source-span provenance.
+ No broad project reading unless explicitly requested.

### Functionality

Context-pack schema:

```python
@dataclass
class SourceSpan:
    path: str
    line_start: int | None
    line_end: int | None
    reason: str
    score: float = 0.0
    query: str | None = None


@dataclass
class ContextPack:
    task: str
    target: str | None
    document_thesis: str | None
    prior_claims: list[str]
    terminology: dict[str, str]
    constraints: list[str]
    source_spans: list[SourceSpan]
    estimated_tokens: int
    summary: str | None = None
```

Generation algorithm:

```text
1. Normalize task and target.
2. Load config and section cards.
3. Compute task_hash, config_hash, section_cards_hash, and index fingerprint.
4. Check context_cache.sqlite.
5. If cache hit, return cached context pack.
6. Build retrieval queries from:
   - task text;
   - target section title;
   - key terms;
   - dependent sections;
   - explicit must-consider sources.
7. Query RTFM through adapter.
8. Normalize RTFM results into candidate source spans.
9. Score candidates.
10. Deduplicate overlapping spans.
11. Select highest-value spans within token budget.
12. Assemble context pack.
13. Store run, retrieval events, sources, and payload.
14. Return context pack to the MCP client.
```

Suggested scoring:

```text
score =
  + 2.0 if result path matches target section
  + 1.5 if result path matches a dependency section
  + 1.0 per key-term match
  + 1.0 if result is from outline/report file explicitly mentioned by task
  + 0.1 × RTFM relevance score, if available
  - 1.0 if result is from generated or ignored directory
```

### Architecture Notes

Token budgeting may begin with a simple approximation. A later version can use model-specific tokenizers.

### Artifacts

+ `context_pack.py`.
+ `hashing.py`.
+ Context-pack schema.
+ Ranking test fixtures.

### Tests

+ Cache hit avoids RTFM retrieval.
+ Cache miss queries RTFM.
+ Changed section cards invalidate cache.
+ Selected spans fit the token budget.
+ Duplicate spans are merged or removed.

### Progress Checklist

+ [x] Context-pack schema implemented.
+ [x] Query builder implemented.
+ [x] Ranking implemented.
+ [x] Token budgeting implemented.
+ [x] Cache integration implemented.
+ [x] Tests pass.

## Step 6: MCP Tools and CLI

### Objective

Expose the context-pack workflow to CLI agents through MCP tools and provide local CLI utilities for setup and debugging.

### Inputs

+ RTFM adapter.
+ Context-pack generator.
+ Extension store.
+ Config and section cards.

### Requirements

Functional requirements:

+ Expose `get_writing_context_pack`.
+ Expose `refresh_index`.
+ Expose `get_section_context` as an optional convenience tool.
+ Provide CLI commands for `serve`, `init`, `init-db`, `sync`, and `pack`.

Non-functional requirements:

+ Tool responses must be compact.
+ Invalid input must produce actionable errors.
+ The agent should be guided to call the pack tool before broad file reads.

### Functionality

Core MCP tools:

```text
get_writing_context_pack
  Input: task, target, token_budget, must_consider, avoid
  Output: ContextPack

refresh_index
  Input: project_root, corpus
  Output: sync status and cache invalidation status

get_section_context
  Input: section_id, token_budget
  Output: ContextPack scoped to one section
```

CLI commands:

```bash
writing-context-rtfm init
writing-context-rtfm init-db
writing-context-rtfm serve
writing-context-rtfm sync --path . --corpus manuscript
writing-context-rtfm pack --task "write section 6" --target section_6
```

### Architecture Notes

The MCP server should not expose raw RTFM internals. Agents should use the higher-level context-pack tools instead of manually chaining low-level retrieval calls.

### Artifacts

+ `server.py`.
+ `cli.py`.
+ MCP tool schemas.
+ Agent instruction file.

### Tests

+ MCP tool input validation.
+ CLI command execution.
+ End-to-end pack generation via CLI.
+ Error handling for missing RTFM index.

### Progress Checklist

+ [x] MCP server implemented.
+ [x] CLI implemented.
+ [x] Tool schemas documented.
+ [x] Agent instruction drafted.
+ [x] Tests pass.

## Step 7: Verification and Evaluation

### Objective

Verify that the extension reduces token usage without losing necessary writing context.

### Inputs

+ Test manuscripts.
+ Typical writing prompts.
+ Baseline agent behavior.
+ Generated context packs.

### Requirements

Functional requirements:

+ Context packs must include required source spans.
+ Agents must be able to write from the pack without reading the full repository.
+ Cache behavior must be correct.

Non-functional requirements:

+ Context-pack generation should be fast enough for interactive use.
+ Token savings should be measurable.
+ Output should be reproducible.

### Functionality

Evaluate three conditions:

```text
A. Agent reads project freely.
B. Agent uses raw RTFM retrieval.
C. Agent uses writing-context-rtfm context pack.
```

Compare:

+ token usage;
+ number of files read;
+ relevance of selected context;
+ writing coherence;
+ unsupported claims;
+ task completion time.

### Architecture Notes

The project succeeds if it reduces unnecessary context loading while preserving enough context for accurate writing.

### Artifacts

+ Evaluation script.
+ Test manuscript fixture.
+ Token usage report.
+ Manual validation checklist.

### Tests

+ Unit tests for all modules.
+ Integration test with RTFM-indexed fixture.
+ Cache invalidation tests.
+ Manual agent writing comparison.

### Progress Checklist

+ [ ] Evaluation fixture created.
+ [ ] Token metrics implemented.
+ [ ] Baseline comparison performed.
+ [ ] Cache tests pass.
+ [ ] Evaluation report drafted.

## Step 8: Maintenance and Future Enhancements

### Objective

Keep the extension stable while leaving room for future graph-like capabilities.

### Inputs

+ User feedback.
+ RTFM updates.
+ Evaluation metrics.
+ New writing workflows.

### Requirements

Functional requirements:

+ Track upstream RTFM changes.
+ Keep adapter isolated.
+ Maintain backward-compatible config and section-card schemas.

Non-functional requirements:

+ Avoid scope creep.
+ Keep the system inspectable.
+ Keep v0.1 focused on token-efficient writing context.

### Functionality

Future enhancements may include:

+ better tokenizers;
+ section-card generation assistance;
+ lightweight dependency maps;
+ claim cards;
+ optional argument graph;
+ VS Code extension;
+ context-pack visualization.

### Architecture Notes

A future SQLite-backed section-card store is justified only if YAML becomes insufficient. Until then, YAML remains the human-authored source of truth.

### Artifacts

+ Roadmap.
+ CHANGELOG.
+ Upgrade guide.
+ Backlog tickets.

### Tests

+ Regression tests.
+ Backward-compatibility tests for config and section cards.
+ Adapter tests against supported RTFM versions.

### Progress Checklist

+ [ ] Roadmap maintained.
+ [ ] RTFM compatibility tracked.
+ [ ] Schema changes versioned.
+ [ ] Regression suite maintained.

## 6. Final Architecture Decision

The updated methodology is:

```text
Use RTFM as the retrieval backend.
Use YAML for human-authored project guidance.
Use extension-owned SQLite for generated runtime state.
Expose a compact MCP layer for writing-context packs.
Avoid full manuscript graph functionality in v0.1.
```

This design is consistent, focused, and directly aligned with the project’s primary goal: reducing token usage when agents need prior context for writing.
