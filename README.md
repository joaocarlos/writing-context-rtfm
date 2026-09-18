<!-- mcp-name: writing-context-rtfm -->
<div align="center">

***Surgical Context for Writing Agents***

Stop giving your AI agent the entire manuscript to write one section. Give it the exact paragraphs, constraints, and dependencies it needs to succeed. No token bloat. No hallucinations.

**`Lightweight · Task-Focused · Extension · MIT`**

<br>

[![PyPI Version](https://img.shields.io/pypi/v/writing-context-rtfm.svg)](https://pypi.org/project/writing-context-rtfm/) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) [![Python](https://img.shields.io/badge/Python-3.13+-blue.svg)](https://www.python.org/) [![MCP](https://img.shields.io/badge/MCP-2026-green.svg)](https://modelcontextprotocol.io/) [![Powered by RTFM](https://img.shields.io/badge/Powered%20by-RTFM-purple.svg)](https://github.com/roomi-fields/rtfm)

</div>

---

<!-- ─────────── TIER 1 — Pain & promise ─────────── -->

Your writing agent is drowning in tokens.

You ask Claude or Cursor to "Write the methodology section." To give it context, you feed it your 50-page manuscript, your related works, and your notes. The agent gets overwhelmed by the global narrative, loses track of the specific hyper-parameters you wanted to include, and writes a generic, repetitive summary that reads like a high-school essay. 

The bottleneck isn't the model's writing ability — it's the **noise**.

**`writing-context-rtfm` fixes the noise.** It is a lightweight MCP extension built on top of `rtfm-ai`. Instead of letting the agent grep freely, it acts as a gatekeeper. It takes the agent's task, queries the underlying RTFM index, aggressively filters out background noise, and packs only the *essential* and *supporting* source chunks into a tight, highly-focused prompt.

```bash
writing-context-rtfm pack \
  --task "Write the methodology section detailing dataset and quantization" \
  --target sections/methodology.tex \
  --budget 4000
```

3 seconds later, the agent receives a compact context pack containing exactly the paragraphs and key terms needed, alongside stylistic constraints for the target section. The agent writes perfectly.

> **Token budgets respected. Constraints enforced. Progressive disclosure over context dumps.**

---

## Installation & Zero-Friction Setup

`writing-context-rtfm` is designed for near-zero installation friction. It is published on PyPI, bundles `rtfm-ai[embeddings]` automatically, and can be used directly without manual virtual environment configuration.

### 1. Instant Execution with `uvx` (Zero Install)
You can run `writing-context-rtfm` directly without installing anything permanently:
```bash
# Direct MCP server invocation (used by Claude Desktop / Cursor)
uvx writing-context-rtfm

# Diagnostic check on any repository
uvx writing-context-rtfm doctor
```

### 2. Global CLI Installation
If you prefer having the CLI command permanently available in your shell:
```bash
# Using uv (recommended)
uv tool install writing-context-rtfm

# Using pipx
pipx install writing-context-rtfm
```
*(Note: `writing-context-rtfm` automatically pulls in `rtfm-ai[embeddings]>=0.46.1` as a core dependency. No separate `rtfm-ai` installation step is needed).*

---

## The Three Canonical Workflows

### Workflow 1: First Use (Primeiro Uso)
Set up a new or existing manuscript project in under 10 seconds:

1. **One-Command Quickstart**:
   ```bash
   cd your-manuscript-repo
   writing-context-rtfm init --quickstart
   ```
   This non-destructively:
   - Creates `.writing-context/config.yaml` and `.writing-context/cards.overrides.yaml.example`.
   - Adds `.writing-context/context_cache.sqlite` to your `.gitignore`.
   - Registers the MCP server automatically in `.mcp.json`.
   - Injects **Agent Rules of Thumb** blocks into `CLAUDE.md`, `AGENTS.md`, and `GEMINI.md`.
   - Automatically scans and scaffolds section cards (`cards.generated.yaml`).
   - Runs initial synchronization of the RTFM retrieval index (`library.db`).

   *(If you prefer step-by-step setup without automatic syncing, use `writing-context-rtfm init` followed by `writing-context-rtfm cards build` and `writing-context-rtfm sync`).*

2. **Verify System Health with Doctor**:
   ```bash
   writing-context-rtfm doctor
   ```
   Runs a comprehensive check across 6 categories: Python version (>=3.13), core/optional dependencies, SQLite index health, Zotero/BibTeX discovery, API keys, and local model hardware.

3. **Register MCP in Your Editor**:
   Add `writing-context-rtfm` to Claude Desktop, Cursor, or VS Code (see [MCP Server Integration](#mcp-server-integration)).

---

### Workflow 2: Daily Writing (Uso Diário)
Once installed, `writing-context-rtfm` operates silently in the background:

1. **Transparent Agent Retrieval**:
   When writing or revising, your AI agent automatically invokes the MCP tools:
   - `get_writing_context_pack(task="...", target="sections/methodology.tex", token_budget=4000)`: Extracts unbroken target text, snaps equation/table boundaries, traverses `\ref{}` links, pulls relevant local BibTeX/Zotero citations, and enforces glossary constraints.
   - `get_proofreading_context_pack(target_file="sections/abstract.tex", line_start=1, line_end=20)`: Delivers an isolated context slice without open-ended search noise.
2. **Writing & Revising**:
   Author drafts and revises text naturally. Agents follow the embedded immutability rules to avoid corrupting LaTeX environments, equations, and citations.
3. **Updating Context After Significant Changes**:
   When you add new files, write extensive paragraphs, or change literature references, update the retrieval index:
   ```bash
   # Synchronize the RTFM index
   writing-context-rtfm sync

   # Refresh section card metadata if LaTeX headers changed
   writing-context-rtfm cards update
   ```
   *(Or ask your AI agent to trigger the `refresh_index` MCP tool directly from chat).*
4. **Terminology Audits**:
   Inspect or audit project-wide terminology consistency before submitting:
   ```bash
   writing-context-rtfm get-term "Context Pack"
   ```

---

### Workflow 3: Troubleshooting (Resolução de Problemas)
When unexpected behavior occurs, use the built-in diagnostic and self-healing tools:

1. **Run Doctor for Immediate Diagnosis**:
   ```bash
   writing-context-rtfm doctor
   # Or for machine-readable JSON output:
   writing-context-rtfm doctor --json
   ```

2. **Diagnostic Matrix (Symptom -> Cause -> Safe Fix)**:
   - **`[!] Python: Python < 3.13`**:
     *Fix*: `uv python install 3.13` or run with `uvx --python 3.13 writing-context-rtfm`.
   - **`[!] Index: library.db missing or 0 chunks`**:
     *Fix*: Run `writing-context-rtfm sync` to build the full-text search index.
   - **`[!] Cards: Target file does not exist or stale references`**:
     *Fix*: Run `writing-context-rtfm cards validate` to find issues, or `writing-context-rtfm cards rebuild` to re-scan.
   - **`[!] Zotero: connection refused or collection not found`**:
     *Fix*: Ensure Zotero Desktop is running locally, or verify collection names in `.writing-context/config.yaml`. Local `.bib` files remain 100% active offline even if Zotero is unreachable.
   - **Cache Inconsistency or Orphaned Workers**:
     *Fix*: Run `writing-context-rtfm cache clear` to flush context cache, or `writing-context-rtfm cleanup` to terminate lingering worker processes.

---

### Literature Grounding (Offline BibTeX & Zotero)
`writing-context-rtfm` grounds your AI writing agent in your real bibliography and literature library, preventing citation key and claim hallucinations:

* **Native Offline BibTeX Provider (Built-in)**: Automatically discovers and parses local `.bib` files (extracting titles, authors, years, abstracts, DOIs, and venues). Works 100% offline out-of-the-box with zero configuration or external dependencies.
* **Zotero MCP (Optional Semantic Expansion)**: If you use Zotero Desktop, `writing-context-rtfm` connects via `zotero-mcp` to run dynamic semantic searches across your PDF library and notes:
  ```bash
  # Install or upgrade the current server with semantic dependencies
  uv tool install --upgrade "zotero-mcp-server[semantic]"

  # Configure local embeddings, then build the semantic index
  zotero-mcp setup --semantic-config-only
  zotero-mcp update-db --fulltext
  ```
  *(Ensure Zotero Desktop is running during writing sessions to allow local SQLite connections).*

  Scope searches by the names shown in Zotero, without looking up numeric IDs:

  ```yaml
  providers:
    zotero:
      enabled: true
      mcp_server:
        command: zotero-mcp
        args: ["serve"]
      extra:
        library_name: "My Library"  # Or the visible name of a shared group
        collections:
          - "Projects / Urban"      # Full path is safest for nested collections
          - "Methods"
        include_subcollections: true
  ```

  Collection entries form a union. A bare collection name is accepted only when it is unique in the selected library; otherwise use its full `Parent / Child` path. Citation-key lookups remain library-wide. Because Zotero's semantic result metadata does not expose collection membership, the provider performs a bounded semantic overfetch and strictly retains only item keys enumerated from the configured collections. Metadata searches are sent to each collection directly, and duplicate papers are removed by Zotero item key.

##### Baseline Model Embeddings
* **Default Local Model**: By default, RTFM automatically generates embeddings for all document chunks. It uses a fast, lightweight multilingual model (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`) which runs completely locally on your CPU/GPU and downloads automatically from Hugging Face on the first sync. No external API keys are required.
* **Customizing Models**: If you want to use a larger or different model, you can run the embedding step explicitly:
  ```bash
  # Options: fast (default), balanced (BAAI/bge-base-en-v1.5), quality (mixedbread-ai/mxbai-embed-large-v1)
  rtfm embed --embed-model balanced
  ```

##### OpenAI Semantic Search (Optional Extension)
If you prefer to leverage OpenAI embeddings for semantic expansion instead of running local transformer models, `writing-context-rtfm` includes a built-in provider that seamlessly overlays OpenAI vectors onto RTFM's index.
1. Securely save your API key to the local cache: `writing-context-rtfm auth openai_semantic "sk-..."`
2. Enable it in your `.writing-context/config.yaml`:
   ```yaml
   providers:
     openai_semantic:
       enabled: true
       model: "text-embedding-3-small"
       auto_sync: false # Set to true to embed all files automatically during `rtfm sync`
   ```
*Note: This architecture uses an ultra-fast `numpy` in-memory comparison, requiring zero C++ SQLite VSS extensions, ensuring maximum compatibility across all operating systems in your laboratory.*

##### Experimental Local Semantic Retrieval and Reranking

The extension can add an entirely local sentence-transformer candidate stream and an optional
cross-encoder reranker. These features require no API key, run in the foreground, and are disabled
by default because current personal-corpus checks show a quality/latency tradeoff rather than a
universal improvement.

```bash
pip install "writing-context-rtfm[local-models]"
```

```yaml
providers:
  local_embeddings:
    enabled: true
    model: sentence-transformers/all-MiniLM-L6-v2
    device: cpu
    batch_size: 16
    torch_threads: 4
    min_score: 0.5
    sync_on_query: true

  local_reranker:
    enabled: false
    model: Alibaba-NLP/gte-reranker-modernbert-base
    device: cpu
    batch_size: 4
    torch_threads: 4
    max_length: 512
    candidate_limit: 40
    blend_weight: 0.25
```

`all-MiniLM-L6-v2` is the practical CPU candidate. The larger
`mixedbread-ai/mxbai-embed-large-v1` is supported, including its required retrieval query prompt,
but was too slow to finish a one-project indexing canary under the tested four-thread CPU policy.
Test it only with an accelerated or optimized runtime. The first local query embeds changed chunks
and is therefore slower; subsequent queries reuse content-addressed vectors.

Proofreading does not depend on semantic retrieval. `proofread-pack` reads the exact requested line
range and adjacent paragraphs directly; semantic models must never replace the text being edited.
Optional prior-usage examples use only the in-process SQLite index. If that index is unavailable,
proofreading retains the target and glossary rules instead of launching an external RTFM process.

##### Card Scaffolding Generator Configuration (Optional)
To customize the model or API endpoint used during `cards build` / `cards infer` (for example, to use a local Ollama instance or the Hugging Face Inference API instead of OpenAI), configure the `generator` block in your `.writing-context/config.yaml`:
```yaml
generator:
  # The model to use (e.g., gpt-4o-mini, Qwen/Qwen2.5-Coder-7B-Instruct, phi3)
  model: "Qwen/Qwen2.5-Coder-7B-Instruct"
  # The API endpoint base URL (e.g. https://api-inference.huggingface.co/v1, http://localhost:11434/v1)
  api_base: "https://api-inference.huggingface.co/v1"
  # The API key/token (optional; falls back to environment variables or local auth cache)
  # api_key: "your-token"
```
*   **Hugging Face Inference API**: Query using a free Hugging Face token. You can save your token locally using: `writing-context-rtfm auth huggingface "hf_..."`.
*   **Local Ollama Server**: Start Ollama and run `ollama pull phi3` or `ollama pull qwen2.5-coder`. Point `api_base` to `http://localhost:11434/v1` and set `model` to your pulled model name. No API key is required.

---

## MCP Server Integration

### 1. Claude Desktop
Add this to your `claude_desktop_config.json` (on macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

**Option A: Zero-install with `uvx` (Recommended)**
```json
{
  "mcpServers": {
    "writing-context-rtfm": {
      "command": "uvx",
      "args": [
        "writing-context-rtfm"
      ]
    }
  }
}
```

**Option B: Using globally installed CLI**
```json
{
  "mcpServers": {
    "writing-context-rtfm": {
      "command": "writing-context-rtfm",
      "args": [
        "serve"
      ]
    }
  }
}
```

### 2. Cursor IDE
1. Open Cursor Settings (`Cmd + ,`).
2. Navigate to **Features** > **MCP** and click **+ Add New MCP Server**.
3. **Name:** `writing-context-rtfm`
4. **Type:** `command`
5. **Command:** `uvx writing-context-rtfm` (or `writing-context-rtfm serve`)

### 3. VS Code Extensions (Cline, Roo Code)
Update your MCP settings file (e.g., `cline_mcp_settings.json`):

```json
{
  "mcpServers": {
    "writing-context-rtfm": {
      "command": "uvx",
      "args": [
        "writing-context-rtfm"
      ]
    }
  }
}
```

### 4. Claude Code (Anthropic CLI Agent)
```bash
# Global configuration (using uvx)
claude mcp add --scope user --transport stdio writing-context-rtfm -- uvx writing-context-rtfm

# Local configuration (using installed CLI)
claude mcp add --scope local --transport stdio writing-context-rtfm -- writing-context-rtfm serve
```

---

<!-- ─────────── TIER 2 — Positioning & buzz ─────────── -->

## The Core Philosophy: RTFM Retrieves, We Pack

| **Tool** | **Role** | **Action** | **Output** |
|----------|----------|------------|------------|
| `rtfm-ai` | The Retrieval Layer | Indexes everything, runs FTS/Semantic search, returns raw hits. | 25 raw chunks |
| `writing-context-rtfm` | The Curation Layer | Filters noise, applies constraints, ranks by structural priority. | 4 essential chunks |

We do **not** replace or fork RTFM. We wrap it behind a clean, typed **Anti-Corruption Layer** (`RetrievalEngine`). RTFM is built to fetch raw memory; `writing-context-rtfm` is built to decide *what is enough memory to write a specific section*. 

For a complete breakdown of component ownership, the `RetrievalEngine` protocol, and our continuous upstream contract testing policy, see the [Architecture Boundaries Specification](docs/architecture_boundaries.md). 

---

## Features

### 1. Unbroken Target Atomicity & Elastic Auto-Scaling
When writing or revising a specific section, the extension extracts the contiguous, unbroken target text as highest-priority (`essential`) context. If the requested token budget is too small to fit the mandatory target text and constraints, the generator automatically scales the budget to fit the essential context and returns `"status": "complete"` with an informative notice, preventing severed prompts.

### 1a. Coverage-First Retrieval
Pass concrete required evidence through `must_consider`. Each item becomes an atomic obligation, and citation keys explicitly present in the task become citation obligations. The selector reserves the smallest high-ranked set of spans that covers those atoms before adding ordinary background. In elastic mode it may increase the budget once, up to `context.max_token_budget`; it never starts an unbounded retrieve-and-expand loop. Inspect `quality.atomic_coverage` before drafting. Missing atoms produce a degraded result and a direct-read/`request_more_context` warning.

### 2. AST-Aware Environment Snapping (LaTeX & Markdown)
Retrieved source slices are automatically checked against document ASTs. If a chunk boundary intersects an equation (`equation`, `align`, `$$...$$`), table (`tabular`, Markdown pipe table), code fence (` ``` ` / `~~~`), figure, or algorithm environment, the boundary automatically snaps outward to preserve the entire syntactic block.

### 3. 1-Hop Reference Graph Traversal
The extension automatically parses `\ref{...}` and label declarations in the target text, traversing the manuscript AST to inject defining snippets for referenced figures, tables, equations, and subsections directly into the context pack.

### 4. Native Offline BibTeX & Semantic Zotero Grounding
* **Built-in BibTeX Engine**: Directly parses and indexes local `.bib` files, extracting titles, authors, years, abstracts, DOIs, and citation keys offline.
* **Dynamic Zotero Search**: Connects to `zotero-mcp` for semantic literature search when enabled. Searches can be isolated to a named personal or group library and a union of named collections. Scoped semantic results are verified against collection item keys before they enter a context pack.
* **Proofread Protection**: In `proofread-pack` mode, open-ended search is disabled to avoid "context contamination", resolving only existing `\cite{}` keys.

### 5. Two-Tier Agent Protocol (Soft Gatekeeping)
Agents are instructed to retrieve curated context first via `get_writing_context_pack` or `get_proofreading_context_pack`. If an agent requires unbroken chapter-length prose synthesis, it is explicitly authorized to fall back autonomously to direct file reading.

### 6. LaTeX Safety & Immutability Rules
The extension catalogs all detected `\cite{...}`, `\ref{...}`, `\label{...}`, and math environments in the target text, issuing explicit immutability rules in the returned prompt guidance to prevent agents from corrupting manuscript formatting.

### 7. In-Process SQLite Caching & Fast-Path
Generated context packs are hashed and cached in `.writing-context/context_cache.sqlite`. Direct SQLite FTS5 querying with BM25 ranking provides sub-millisecond local search without subprocess overhead.

---

## The Split-Cards Pattern (Overrides & Generated)

To give writing agents context and rules, we define manuscript metadata. Rather than forcing you to maintain a single massive YAML configuration manually, `writing-context-rtfm` splits section cards into two layers:

1. **`cards.generated.yaml` (Machine-Written)**: Generated automatically by `writing-context-rtfm cards build` or `cards scan`. The tool scans your manuscript files, maps structural hierarchies, and extracts default purposes, key terms, facts, and constraints. **Do not modify this file.**
2. **`cards.overrides.yaml` (Human-Controlled)**: The user control panel. Create or edit this file to override generated settings or declare global document parameters (like style guidelines, project-wide glossary, or specific section rules).

At runtime, the extension automatically overlays `cards.overrides.yaml` on top of the generated metadata, compiling them into a single unified context card database.

### Override File Example (`cards.overrides.yaml`)
```yaml
version: 2

# Project-wide global context rules
document:
  title: "A New Approach to Manuscript Curation"
  thesis: "Surgical context selection using a gatekeeping protocol reduces LLM token overhead."
  writing_style:
    tone: "Academic, precise, third-person"
    avoid_words: ["groundbreaking", "revolutionary", "game-changing"]
  terminology:
    Context Pack:
      definition: "A compact JSON structure containing prioritized source spans and constraints."
      variants: ["writing context pack"]
      avoid: ["prompt dump"]

# Override specific sections generated by the tool
sections:
  section_methodology:
    title: "Proposed Methodology"
    purpose: "Detail the system architecture and context selection algorithms."
    depends_on:
      - section_introduction
    must_preserve:
      - "Token budget formula is B_usable = B_total * (1 - margin)"
    avoid: ["premature results discussion"]
    constraints:
      - "Write equations using LaTeX align environments"
```

Glossary entries are a project-wide terminology registry. Scalar definitions from older cards still
load, while the structured form lets the tool distinguish the canonical term, accepted variants,
and wording that should be replaced. Writing packs turn relevant entries into explicit constraints;
proofreading packs match them directly against the requested text and preserve the rules even when
the index is offline. Use `get-term` for one term and the MCP
`audit_manuscript_terminology` tool for a project-wide canonical/variant/forbidden-form audit.

### Single-File & Multi-File Manuscript Support
`writing-context-rtfm` natively supports both:
* **Multi-File Modular Projects**: Projects organized into sub-files (e.g. `sections/01_intro.tex`, `chapters/ch1.md`, `\input{...}`).
* **Single-File Monolithic Manuscripts**: Monolithic single-file papers (e.g. `main.tex`, `paper.md`). The AST parser uses virtual section nodes (`find_section_node`) to resolve section cards, calculate character boundaries, and isolate target subsections seamlessly.

---

## CLI Reference

```bash
# Initialize project config, gitignore, and editor rules
writing-context-rtfm init

# Build section cards (scan, infer, and update in sequence)
writing-context-rtfm cards build

# Deterministically scan the manuscript structure
writing-context-rtfm cards scan

# Interactively review generated card candidates
writing-context-rtfm cards review

# Initialize the local SQLite cache database (.writing-context/context_cache.sqlite)
writing-context-rtfm init-db

# Run diagnostics health checks on databases and configuration files
writing-context-rtfm doctor

# Sync the underlying RTFM index
writing-context-rtfm sync

# Generate a context pack directly in the terminal
writing-context-rtfm pack \
  --task "Update the introduction" \
  --target sections/introduction.tex \
  --budget 4000

# Explain candidate lifecycle, funnel, and provider ownership exclusions
writing-context-rtfm explain-pack \
  --task "Update the introduction" \
  --target sections/introduction.tex \
  --budget 4000

# Generate a proofreading context pack
writing-context-rtfm proofread-pack sections/abstract.tex --line-start 1 --line-end 10 --max-tokens 3000

# Inspect configured rules and details for a specific section card
writing-context-rtfm inspect-target --target section_abstract

# Look up a term in the document glossary config
writing-context-rtfm get-term "Context Pack"

# Show the LaTeX reference graph and section dependencies
writing-context-rtfm show-graph

# Clear the cached context packs
writing-context-rtfm cache clear

# Validate section cards for stale references or missing targets
writing-context-rtfm cards validate

# Rebuild section cards (clears generated cards and re-scans fresh)
writing-context-rtfm cards rebuild

# Authenticate API keys/tokens (e.g. openai_semantic, huggingface) securely into SQLite cache
writing-context-rtfm auth huggingface "hf_..."

# Remove active background worker PID registrations
writing-context-rtfm cleanup

# Start MCP Server
writing-context-rtfm serve
```

Automatic sync before every pack is disabled by default in 0.11.0. Prefer an explicit
`writing-context-rtfm sync` or MCP `refresh_index` call after manuscript changes. Set
`rtfm.sync_before_pack: true` only after confirming that the installed RTFM worker lifecycle is
stable on your machine.

---

## Where this fits

```
┌─────────────────────────────────┐
│       AI Agent / LLM Client     │  ← Execution (Cursor, Claude)
├─────────────────────────────────┤
│     writing-context-rtfm        │  ← Curation (Packs, Filters, Rules)
├─────────────────────────────────┤
│           rtfm-ai               │  ← Retrieval (Index, FTS, Semantic)
└─────────────────────────────────┘
```

Without the context packer, your agent retrieves 50 documents and hopes for the best. With it, the agent receives a surgically precise, prioritized briefing.

## License
[MIT License](LICENSE) — use it, fork it, extend it.
