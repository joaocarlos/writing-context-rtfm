# writing-context-rtfm Detailed Design Document

## 1. Introduction

This document provides the implementation-level design for `writing-context-rtfm`. It revises the previous detailed design by embedding the storage addendum directly into the methodology. The revised design uses a **hybrid storage strategy**:

```text
RTFM SQLite database
  → retrieval index managed by RTFM

.writing-context/config.yaml
  → human-maintained configuration

.writing-context/cards.generated.yaml
  → automatically scaffolded writing metadata and section cards

.writing-context/cards.overrides.yaml
  → human-maintained overrides, thesis, terminology, and specific section edits

.writing-context/cards.lock.json
  → lock file tracking versioning and checksum metadata for generation integrity

.writing-context/context_cache.sqlite
  → extension-generated cache, run history, retrieval events, and evaluation records
```

The extension’s purpose remains narrow: reduce token usage when an AI agent writes or revises text that depends on prior project context. It does this by generating compact **context packs** from an RTFM-indexed project.

The system does not implement a full argument graph in v0.1. It does not copy RTFM features. It does not write to RTFM’s internal database.

## 2. System Responsibilities

## 2.1 RTFM Responsibilities

RTFM remains responsible for:

+ indexing project files;
+ parsing supported formats;
+ chunking documents;
+ maintaining retrieval metadata;
+ storing the retrieval index;
+ exposing search, context, expansion, and sync operations.

`writing-context-rtfm` treats RTFM as an external dependency and retrieval backend.

## 2.2 writing-context-rtfm Responsibilities

The extension is responsible for:

+ interpreting a writing task;
+ loading project-specific writing guidance;
+ querying RTFM for relevant source spans;
+ ranking and deduplicating candidates;
+ fitting selected context into a token budget;
+ returning a structured context pack through MCP;
+ caching generated packs and retrieval events in its own SQLite database.

## 3. Project Scaffold

## 3.1 Repository Layout

Recommended structure:

```text
writing-context-rtfm/
├── pyproject.toml
├── README.md
├── LICENSE
├── CHANGELOG.md
├── docs/
│   ├── architecture.md
│   ├── detailed_design.md
│   └── agent_instructions.md
├── src/
│   └── writing_context_rtfm/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── hashing.py
│       ├── rtfm_adapter.py
│       ├── section_cards.py
│       ├── storage.py
│       ├── context_pack.py
│       ├── ranking.py
│       ├── token_budget.py
│       ├── server.py
│       └── schemas.py
├── tests/
│   ├── fixtures/
│   │   └── mini_manuscript/
│   ├── test_config.py
│   ├── test_hashing.py
│   ├── test_rtfm_adapter.py
│   ├── test_section_cards.py
│   ├── test_storage.py
│   ├── test_context_pack.py
│   └── test_server.py
└── examples/
    └── .writing-context/
        ├── config.yaml
        ├── cards.generated.yaml
        ├── cards.overrides.yaml
        └── cards.lock.json
```

## 3.2 Local Project Layout

Inside a manuscript repository that uses the extension:

```text
my-paper/
├── .rtfm/
│   └── library.db
├── .writing-context/
│   ├── config.yaml
│   ├── cards.generated.yaml
│   ├── cards.overrides.yaml
│   ├── cards.lock.json
│   └── context_cache.sqlite
├── sections/
│   ├── 01_introduction.tex
│   ├── 02_background.tex
│   └── 06_research_agenda.tex
├── references.bib
└── docs/
    └── notes.md
```

## 4. Installation and Initialization

## 4.1 RTFM Installation and Embeddings Setup

RTFM must be installed and able to index the target project before the extension can return useful context packs.

Typical setup:

```bash
# 1. Install the RTFM CLI
uv tool install "rtfm-ai[embeddings]"

# 2. Go to your paper repository and initialize RTFM
cd my-paper
rtfm init

# 3. Synchronize files and generate local embeddings
rtfm sync
```

### Baseline Embeddings Setup

By default, RTFM uses a fast, multilingual local embedding model (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`). This model runs locally on the user's CPU/GPU and does not require external API keys. It is automatically downloaded from Hugging Face during the first sync or embedding step.

Users can check active embedding models and switch to higher-quality models using the `rtfm embed-models` and `rtfm embed` commands:

```bash
# List available models
rtfm embed-models

# Force regenerate embeddings with a balanced English-optimized model (BAAI/bge-base-en-v1.5)
rtfm embed --embed-model balanced
```

## 4.2 Extension Installation

During development:

```bash
git clone <repo-url> writing-context-rtfm
cd writing-context-rtfm
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

After packaging:

```bash
uv tool install writing-context-rtfm
```

## 4.3 Initialization Commands

The extension should provide:

```bash
writing-context-rtfm init
writing-context-rtfm init-db
writing-context-rtfm sync --path . --corpus manuscript
writing-context-rtfm pack --task "Write Section 6" --target section_6
writing-context-rtfm serve
```

Command behavior:

+ `init`: creates `.writing-context/config.yaml` and `.writing-context/cards.overrides.yaml.example` examples if they do not exist.
+ `init-db`: creates `.writing-context/context_cache.sqlite` and applies migrations.
+ `sync`: calls RTFM sync and invalidates context cache when configured.
+ `pack`: generates a context pack from the CLI for debugging.
+ `serve`: starts the MCP server.

## 5. Configuration Schema

## 5.1 File Location

```text
.writing-context/config.yaml
```

## 5.2 Recommended Schema

```yaml
version: 1

rtfm:
  corpus: manuscript
  project_root: .
  sync_before_pack: false  # Prefer explicit refresh_index calls.

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

providers:
  zotero:
    enabled: true
    mcp_server:
      command: node
      args: ["/path/to/zotero-mcp/dist/index.js"]
    extra:
      similarity_threshold: -0.4  # Swept threshold to retain related network papers while pruning noise
```

## 5.3 Python Schema

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class RTFMConfig:
    corpus: str = "manuscript"
    project_root: str = "."
    sync_before_pack: bool = False


@dataclass(frozen=True)
class ContextConfig:
    default_token_budget: int = 12000
    reserved_generation_margin: float = 0.10
    max_search_results_per_query: int = 10
    max_source_spans: int = 35
    include_source_excerpts: bool = False


@dataclass(frozen=True)
class CacheConfig:
    enabled: bool = True
    path: str = ".writing-context/context_cache.sqlite"
    invalidate_on_refresh: bool = True


@dataclass(frozen=True)
class SectionCardsConfig:
    path: str = ".writing-context/section_cards.yaml"
    required: bool = False


@dataclass(frozen=True)
class MCPServerConfig:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class ProviderConfig:
    enabled: bool = False
    mcp_server: MCPServerConfig | None = None
    sse_url: str | None = None
    headers: dict[str, str] | None = None
    extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class AppConfig:
    version: int
    rtfm: RTFMConfig
    context: ContextConfig
    cache: CacheConfig
    section_cards: SectionCardsConfig
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
```

## 6. Section Cards (Split Architecture)

## 6.1 File Locations

```text
.writing-context/cards.generated.yaml
.writing-context/cards.overrides.yaml
.writing-context/cards.lock.json
```

## 6.2 Rationale

Separating generated structure (`cards.generated.yaml`) from user overrides (`cards.overrides.yaml`) prevents automated scans from wiping out custom guidelines, terminology glossaries, or manually defined constraints. The lock file (`cards.lock.json`) ensures that user decisions (such as accepting or rejecting machine-extracted cards/fields) are saved persistently, avoiding repeated suggestions.

## 6.3 Recommended Schemas

### 1. `cards.generated.yaml` (Machine-Written)
Stores automatically extracted section cards, mapped rhetorical roles, and candidate constraints.

```yaml
version: 2
document:
  title: "Urban Emergency Routing"
  thesis: "Optimal routing reduces response times under resource-load conditions."
  writing_style:
    tone: "academic, formal, concise"
  terminology:
    territorial prioritization:
      definition: "Ranking geographic sectors based on risk profile."
      variants: ["sector prioritization", "zone ranking"]
      avoid: ["geosectoring"]
sections:
  section_1:
    identity:
      source: "sections/01_introduction.tex"
    structure:
      title: "Introduction"
    purpose:
      value: "Define the emergency routing problem."
      status: "verified"
    rhetorical_role:
      value: "problem definition"
    key_terms:
      - value: "emergency routing"
        status: "accepted"
      - value: "dynamic prioritisation"
        status: "generated"
    dependencies:
      - target: "section_2"
        status: "accepted"
    facts:
      - value: "Response delay grows exponentially with traffic congestion."
        status: "verified"
    constraints:
      - value: "Do not describe routing algorithms in detail."
        type: "scope_exclusion"
        status: "accepted"
```

### 2. `cards.overrides.yaml` (Author-Owned)
Author-owned file containing custom guidelines and overrides.

```yaml
version: 2
document:
  title: "Urban Emergency Routing & Territorial Prioritization"
  thesis: "Optimal routing reduces response times under resource-load conditions, supported by territorial prioritization."
  writing_style:
    tone: "academic, formal, concise"
    avoid:
      - "loose terminology"
  terminology:
    territorial prioritization:
      definition: "Ranking emergency regions based on vulnerability parameters."
sections:
  section_1:
    title: "Introduction & Context"
    purpose: "Establish emergency planning context and project scope."
    key_terms:
      - "territorial prioritization"
      - "urban emergency routing"
    depends_on: []
    must_preserve:
      - "Human operators make final dispatching decisions."
    avoid:
      - "deployment claims"
```

### 3. `cards.lock.json` (Lock State)
Stores status parameters and checksums of original files.

```json
{
  "generation_version": 2,
  "extractor_version": 1,
  "sections": {
    "section_1": {
      "content_hash": "a1b2c3d4e5f6g7h8",
      "decisions": {
        "key_terms": {
          "dynamic prioritisation": "rejected"
        }
      },
      "stale_fields": []
    }
  }
}
```

## 6.4 Python Schema

```python
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DocumentCard:
    title: str | None = None
    thesis: str | None = None
    writing_style: dict[str, object] | None = None
    terminology: dict[str, Any] | None = None


@dataclass(frozen=True)
class SectionCard:
    id: str
    title: str | None = None
    role: str | None = None
    path: str | None = None
    key_terms: list[str] | None = None
    depends_on: list[str] | None = None
    must_preserve: list[str] | None = None
    avoid: list[str] | None = None
    constraints: list[str] | None = None


@dataclass(frozen=True)
class SectionCards:
    version: int
    document: DocumentCard
    sections: dict[str, SectionCard]
```

## 7. Extension SQLite Schema

## 7.1 Database Location

```text
.writing-context/context_cache.sqlite
```

This database is generated and may be deleted and rebuilt safely.

## 7.2 Migration Strategy

Implement migrations in `storage.py`. For v0.1, one migration is sufficient.

```python
SCHEMA_VERSION = 1


class ExtensionStore:
    def init_db(self) -> None:
        self._connect()
        self._enable_foreign_keys()
        self._apply_migration_v1()
```

Always enable foreign keys:

```sql
PRAGMA foreign_keys = ON;
```

## 7.3 Tables

### `schema_version`

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### `context_pack_runs`

```sql
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

CREATE INDEX IF NOT EXISTS idx_context_pack_runs_task_hash
ON context_pack_runs(task_hash);
```

### `context_pack_sources`

```sql
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

CREATE INDEX IF NOT EXISTS idx_context_pack_sources_run_id
ON context_pack_sources(run_id);

CREATE INDEX IF NOT EXISTS idx_context_pack_sources_path
ON context_pack_sources(path);
```

### `context_pack_payloads`

```sql
CREATE TABLE IF NOT EXISTS context_pack_payloads (
    run_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    estimated_tokens INTEGER,
    source_count INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES context_pack_runs(run_id) ON DELETE CASCADE
);
```

### `retrieval_events`

```sql
CREATE TABLE IF NOT EXISTS retrieval_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    query TEXT NOT NULL,
    result_count INTEGER,
    elapsed_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES context_pack_runs(run_id) ON DELETE CASCADE
);
```

### `evaluation_records`

```sql
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

## 8. Hashing and Cache Invalidation

## 8.1 Stable Hash Function

```python
import hashlib


def stable_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()
```

## 8.2 Required Hashes

Compute:

```text
task_hash
config_hash
section_cards_hash
rtfm_index_fingerprint
extension_version
```

A cache entry is valid only if all hashes match.

## 8.3 Task Hash

```python
def compute_task_hash(task: str, target: str | None, token_budget: int) -> str:
    return stable_hash(task.strip(), target or "", str(token_budget))
```

## 8.4 Config and Section-Card Hashes

Hash the normalized YAML content, not just the file timestamp.

```python
def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
```

## 8.5 RTFM Index Fingerprint

For v0.1:

```text
rtfm_index_fingerprint = timestamp or UUID generated after the last refresh_index call
```

For v0.2, prefer an official RTFM index status command if available. Avoid direct SQL inspection of RTFM internals in v0.1.

## 8.6 Invalidation Rules

When `refresh_index` is called:

```text
1. Call RTFM sync.
2. Generate a new index fingerprint.
3. Delete cache rows associated with old fingerprints.
```

Because the schema uses `ON DELETE CASCADE`, removing stale rows from `context_pack_runs` removes related sources, payloads, and retrieval events.

## 9. RTFM Adapter Design

## 9.1 Adapter Interface

```python
@dataclass(frozen=True)
class RTFMResult:
    path: str
    line_start: int | None
    line_end: int | None
    snippet: str | None
    score: float | None
    metadata: dict[str, object]


class RTFMAdapter:
    def search(self, query: str, *, corpus: str, limit: int) -> list[RTFMResult]: ...
    def context(self, path: str, line_start: int, line_end: int) -> str: ...
    def expand(self, result_id: str) -> str: ...
    def sync(self, project_root: str, *, corpus: str) -> None: ...
```

## 9.2 Adapter Rules

+ Centralize all RTFM calls in this module.
+ Prefer CLI/API access.
+ Do not write to RTFM’s database.
+ Raise explicit adapter exceptions.
+ Log raw command failures in debug mode.

## 10. Context-Pack Schema

## 10.1 Source Span

```python
@dataclass(frozen=True)
class SourceSpan:
    path: str
    line_start: int | None
    line_end: int | None
    reason: str
    score: float
    query: str | None = None
    metadata: dict[str, object] | None = None
```

## 10.2 Context Pack

```python
@dataclass(frozen=True)
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

## 10.3 MCP Output Shape

The MCP response should be compact and structured:

```json
{
  "task": "Write Section 6",
  "target": "section_6",
  "document_thesis": "...",
  "constraints": ["Do not introduce unsupported claims."],
  "source_spans": [
    {
      "path": "sections/04_methodology.tex",
      "line_start": 31,
      "line_end": 84,
      "reason": "Defines the method that Section 6 depends on.",
      "score": 4.5
    }
  ],
  "estimated_tokens": 4200,
  "summary": "Context pack focused on methodological dependencies and Section 5 limitations."
}
```

## 11. Context-Pack Generation Algorithm

## 11.1 High-Level Algorithm

```text
1. Receive writing task, target, token budget, optional must-consider list.
2. Load config.
3. Load section cards.
4. Compute cache keys.
5. If cache is enabled, check context_cache.sqlite.
6. If valid cache hit exists, return cached payload.
7. Build retrieval queries.
8. Query RTFM.
9. Convert results to candidate spans.
10. Rank candidates.
11. Deduplicate overlapping spans.
12. Select spans within token budget.
13. Compose ContextPack.
14. Store run, retrieval events, sources, and payload.
15. Return ContextPack.
```

## 11.2 Query Builder

Inputs:

+ raw task;
+ target section ID;
+ target section title;
+ target section key terms;
+ dependency section titles;
+ explicit must-consider files or concepts.

Algorithm:

```python
def build_queries(task, target_card, dependency_cards, must_consider):
    queries = []
    queries.append(task)

    if target_card.title:
        queries.append(target_card.title)

    queries.extend(target_card.key_terms or [])

    for dep in dependency_cards:
        if dep.title:
            queries.append(dep.title)
        queries.extend(dep.key_terms or [])

    queries.extend(must_consider or [])

    return deduplicate_preserving_order(normalize_queries(queries))
```

## 11.3 Ranking Algorithm

Suggested scoring:

```text
score = 0
+ 2.0 if result path equals target section path
+ 1.5 if result path equals dependency section path
+ 1.0 for each key-term match
+ 1.0 if result path appears in must_consider
+ 0.5 if result is from outline/report/notes directory
+ 0.1 × RTFM score, if available
- 1.0 if result path matches ignored/generated patterns
```

## 11.4 Deduplication Algorithm

```text
1. Group candidates by path.
2. Sort by line_start.
3. Merge spans if they overlap or are separated by fewer than N lines.
4. Keep the highest score and concatenate reasons.
5. Sort final spans by score descending, then path, then line_start.
```

## 11.5 Token Budgeting

For v0.1, approximate:

```text
estimated_tokens = max(1, character_count // 4)
```

If the extension is configured to avoid source excerpts, estimate span cost from line range:

```text
estimated_tokens = number_of_lines × average_tokens_per_line
```

When a candidate exceeds the remaining budget:

+ skip it, or
+ truncate it if `allow_truncation` is enabled.

Default behavior: skip oversized low-priority spans.

## 12. MCP Server Design

## 12.1 Tools

### `get_writing_context_pack`

Input:

```json
{
  "task": "Write Section 6",
  "target": "section_6",
  "token_budget": 6000,
  "must_consider": ["current outline", "Sections 1-5"],
  "avoid": ["unsupported new claims"]
}
```

Output: `ContextPack` JSON.

### `refresh_index`

Input:

```json
{
  "project_root": ".",
  "corpus": "manuscript"
}
```

Output:

```json
{
  "status": "ok",
  "cache_invalidated": true,
  "new_fingerprint": "..."
}
```

### `get_section_context`

Input:

```json
{
  "section_id": "section_4",
  "token_budget": 3000
}
```

Output: `ContextPack` scoped to one section.

## 12.2 Agent Instruction

Install or document the following rule for agents:

```text
Before writing, rewriting, or expanding manuscript text, call get_writing_context_pack.
Do not read the whole repository unless the user explicitly asks for full-document analysis.
Use the returned source spans as the primary context.
If context is insufficient, request targeted expansion only for the missing span.
```

## 13. CLI Design

## 13.1 Commands

```bash
writing-context-rtfm init
writing-context-rtfm init-db
writing-context-rtfm sync --path . --corpus manuscript
writing-context-rtfm pack --task "Write Section 6" --target section_6
writing-context-rtfm serve
writing-context-rtfm cache clear
writing-context-rtfm cache stats
```

## 13.2 Command Behavior

`init`:

+ creates `.writing-context/`;
+ writes example config if missing;
+ writes example section cards if missing.

`init-db`:

+ creates `context_cache.sqlite`;
+ applies migrations;
+ is idempotent.

`sync`:

+ calls RTFM sync;
+ updates index fingerprint;
+ invalidates stale cache entries.

`pack`:

+ runs the context-pack generator;
+ prints JSON or YAML output.

`serve`:

+ starts the MCP server.

## 14. Test Plan

## 14.1 Unit Tests

```text
test_config_loads_defaults
test_config_yaml_overrides_defaults
test_section_cards_load_valid_yaml
test_section_cards_missing_file_is_allowed
test_section_cards_invalid_yaml_raises_clear_error
test_stable_hash_is_deterministic
test_task_hash_changes_when_budget_changes
test_storage_init_creates_tables
test_storage_init_is_idempotent
test_cache_miss_generates_pack
test_cache_hit_returns_payload
test_refresh_index_invalidates_cache
test_query_builder_uses_dependencies
test_ranking_prioritizes_target_section
test_dedup_merges_overlapping_spans
test_token_budget_limits_selected_spans
```

## 14.2 Integration Tests

```text
1. Create a mini manuscript fixture.
2. Run RTFM sync.
3. Run writing-context-rtfm init-db.
4. Generate a context pack for section_2.
5. Verify selected spans include expected files.
6. Generate the same context pack again.
7. Verify cache hit.
8. Edit cards.overrides.yaml.
9. Generate pack again.
10. Verify cache miss.
```

## 14.3 Manual Evaluation

Compare three workflows:

```text
A. Agent reads project freely.
B. Agent uses raw RTFM retrieval.
C. Agent uses writing-context-rtfm context pack.
```

Record:

+ total tokens consumed;
+ number of files read;
+ relevance of retrieved context;
+ writing quality;
+ unsupported claims;
+ time to first draft.

## 15. Implementation Tickets

## Ticket 1: Scaffold Project

Acceptance criteria:

+ package installs locally;
+ CLI entry point exists;
+ tests run.

## Ticket 2: Implement Config and Section Cards

Acceptance criteria:

+ config loads with defaults;
+ section cards load from YAML;
+ missing cards behave according to `required` flag.

## Ticket 3: Implement Extension SQLite Store

Acceptance criteria:

+ `init-db` creates tables;
+ migrations are idempotent;
+ cache read/write works.

## Ticket 4: Implement RTFM Adapter

Acceptance criteria:

+ search, context, expand, sync methods exist;
+ adapter is mockable;
+ integration test works with RTFM fixture.

## Ticket 5: Implement Context-Pack Generator

Acceptance criteria:

+ queries are generated from task and section cards;
+ candidates are ranked and deduplicated;
+ selected spans fit token budget;
+ results are cached.

## Ticket 6: Implement MCP Server

Acceptance criteria:

+ `get_writing_context_pack` works;
+ `refresh_index` works;
+ `get_section_context` works or is explicitly deferred.

## Ticket 7: Evaluate Token Reduction

Acceptance criteria:

+ evaluation fixture exists;
+ baseline comparison is documented;
+ token savings are reported.

## 16. Migration Path for Future Graph Features

The project may eventually add graph-like structures, but only after the context-pack workflow proves useful.

Recommended progression:

```text
v0.1: context packs + YAML section cards + SQLite cache
v0.2: better ranking + tokenizer integration + evaluation metrics
v0.3: lightweight section dependency map
v0.4: optional claim cards
v0.5: optional manuscript argument graph
```

Do not store section cards primarily in SQLite until at least two of the following become true:

+ more than 100 section cards;
+ multiple agents update cards concurrently;
+ cards include generated claim-level data;
+ cards need complex relational queries;
+ YAML maintenance becomes error-prone;
+ a UI editor is introduced.

## 17. Final Design Summary

The updated design is:

```text
RTFM remains the retrieval engine.
writing-context-rtfm remains the context-selection layer.
YAML stores human-authored guidance.
SQLite stores generated extension state.
MCP exposes compact context packs to writing agents.
```

This design keeps the project focused on its real objective: minimizing token usage while preserving enough prior context for high-quality writing.
