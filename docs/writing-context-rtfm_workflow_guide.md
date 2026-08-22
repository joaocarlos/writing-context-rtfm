# writing-context-rtfm Workflow Guide & Reference

This guide outlines the standard workflow, commands, and Agent Rules of Engagement for the `writing-context-rtfm` MCP extension. It is designed to be read by both developers (to setup and configure projects) and AI agents (to guide text generation and self-correction).

---

## 1. Conceptual Overview

`writing-context-rtfm` is a lightweight Model Context Protocol (MCP) server that acts as a context decision layer on top of **RTFM** (which manages raw indexing and semantic retrieval).

* **RTFM** indexes and retrieves.
* **writing-context-rtfm** decides *what is enough context to write*, formats structured packs, checks LaTeX constraints, and enforces token budgets.

```mermaid
graph TD
    A[Manuscript Workspace] -->|cards build| B[cards.generated.yaml]
    C[cards.overrides.yaml] -->|User Overrides| D[Runtime Merge Engine]
    B -->|Generated Cards| D
    D -->|Merged Cards| E[Writing Context MCP Server]
    F[RTFM library.db] -->|Semantic Spans| E
    E -->|Filter & Token Cap| G[Compact Writing Context Pack]
    G -->|Write / Edit| H[AI Agent / Client]
```

---

## 2. Setup and Project Onboarding

To integrate the extension into a manuscript project, follow this setup sequence:

### Step 1: Install the Package
Ensure the package is installed in the target Python environment:
```bash
uv pip install writing-context-rtfm
```

### Step 2: Initialize Configuration and Rules
Run the initialization command at the root of your project:
```bash
writing-context-rtfm init
```
This command non-destructively initializes:
1. **`.writing-context/config.yaml`**: The main configuration pointing to your RTFM corpus.
2. **`.writing-context/cards.overrides.yaml.example`**: A sample template containing overrides formatting (document title, global thesis, terminology glossaries, style constraints, and section overrides).
3. **`.gitignore`**: Appends cache database files to keep run history untracked.
4. **`.mcp.json`**: Editor server definition.
5. **`CLAUDE.md` / `AGENTS.md` / `GEMINI.md`**: Appends agent guidelines.

### Step 3: Scaffold Section Cards
Scan your project to compile the section metadata:
```bash
writing-context-rtfm cards build
```
Under the hood, this scans your LaTeX files for hierarchy and cross-references, uses model-assisted inference to extract purposes and constraints, and saves them to `.writing-context/cards.generated.yaml`.

* **Model Inference Fallback Chain**: If no OpenAI API key is configured, `cards build` automatically attempts to resolve card extraction using a model fallback chain (OpenAI API -> Hugging Face serverless API using `HF_TOKEN` -> local Ollama server running at `http://localhost:11434` -> deterministic offline scan of LaTeX cross-references).
* **Refining Card Properties**: Copy `.writing-context/cards.overrides.yaml.example` to `.writing-context/cards.overrides.yaml` and add your custom guidelines or section tweaks. The server merges overrides on top of the generated file at runtime.

### Step 4: Install the RTFM CLI (Retrieval Engine)
Since this extension uses RTFM to search and index files, ensure you install `rtfm-ai` globally or in your virtual environment:
```bash
# Global installation (recommended)
uv tool install "rtfm-ai[embeddings]"

# Or in a local project environment
uv pip install "rtfm-ai[embeddings]"
```

### Step 5: Initialize and Sync the RTFM Index
To enable semantic and keyword retrieval, initialize the RTFM configuration and perform the initial file synchronization:
```bash
# 1. Initialize the RTFM directory (.rtfm/)
rtfm init

# 2. Sync the project files to generate the local chunk database and embeddings
rtfm sync
```
* **Embedding Model**: By default, RTFM automatically generates embeddings for all document chunks. It uses a fast, lightweight multilingual model (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`) which runs completely locally on your CPU/GPU and downloads automatically from Hugging Face on the first sync. No external API keys are required.
* **Customizing Models**: You can customize the model by running the embedding step explicitly with a model alias (e.g., `fast`, `balanced`, or `quality`) or any Hugging Face model path:
  ```bash
  # Generate embeddings using the balanced model (BAAI/bge-base-en-v1.5)
  rtfm embed --embed-model balanced
  ```

### 2.1 Empty Repositories and Overleaf Workflows

Because `writing-context-rtfm` analyzes the LaTeX file structure of your project, onboarding requires files to exist locally:

#### A. Starting a New Project (Empty Repository)
If your repository is empty:
1. `writing-context-rtfm init` will run successfully, but `cards build` won't find any LaTeX files to parse.
2. Create your root LaTeX file (e.g., `main.tex`) and any modular sections (e.g., `sections/01_introduction.tex`).
3. Run `writing-context-rtfm cards build` to automatically build your `.writing-context/cards.generated.yaml`.

#### B. Working with Overleaf Manuscripts
If your manuscript is hosted on Overleaf, you must bridge it to your local environment for the local MCP server:
1. **Clone the Overleaf Project**:
   - *Direct Git Integration (Premium)*: Run `git clone https://git.overleaf.com/your-project-id`
   - *GitHub Sync (Free)*: Enable GitHub Sync inside Overleaf and clone the target GitHub repository locally.
   - *Manual Download*: Download the project ZIP from Overleaf, extract it, and run `git init` locally.
2. **Setup the extension**:
   - Run the onboarding sequence (`writing-context-rtfm init`, `cards build`, `rtfm init`, and `rtfm sync`) inside the local folder.
3. **Synchronize Changes**:
   - Let your AI agent write files locally. Commit and push the changes back to Overleaf or GitHub to automatically sync your Overleaf project.

---

## 3. Command Reference

| Command | Purpose | Key Arguments |
| :--- | :--- | :--- |
| `init` | Initial setup of configuration, gitignore, and agent guidelines. | None |
| `cards build` | Discovers files, infers metadata, and compiles section card structures. | None |
| `sync` | Manually updates the underlying RTFM index. | None |
| `pack` | Generates a context pack for draft/revise tasks. | `--task`, `--target`, `--budget`, `--pack-mode` |
| `proofread-pack` | Generates a context pack optimized for grammar/style edits. | `target_file`, `--line-start`, `--line-end`, `--max-tokens` |
| `doctor` | Runs diagnostic health checks on databases and configuration files. | None |

---

## 4. Agent Rules of Engagement

AI agents working in a repository equipped with `writing-context-rtfm` adhere to a two-tier protocol:

### A. Two-Tier Retrieval Protocol (Soft Gatekeeping)
* **Tier 1 (Curated Context First)**: Always call `get_writing_context_pack` or `get_proofreading_context_pack` before drafting, rewriting, expanding, or proofreading text to obtain section constraints, thesis, terminology definitions, and 1-hop reference graph snippets.
* **Tier 2 (Autonomous Direct-Read Fallback)**: If you need continuous prose flow, full-chapter narrative context, or the returned context pack is truncated, you are fully authorized to read the target and dependency files directly after inspecting the pack.

### B. Handle LaTeX Safety Triggers
* **Rule**: Inspect the returned `warnings` array in the context pack.
* **Rule**: If a warning begins with `LaTeX Safety:`, pay extreme attention to the listed environments and labels (e.g. `\begin{equation} ... \end{equation}`, `\ref{...}`, `\cite{...}`). You **must not** delete or break these LaTeX markers during edits.

### C. Self-Correct Token Budgets & Elastic Scaling
* **Rule**: The generator automatically scales undersized budgets to fit mandatory unbroken target text and local constraints.
* **Rule**: If the status is `"degraded"` and a warning recommends a larger budget, retry the call with the recommended value (`token_budget` or `max_tokens` set to `X` or higher) to retrieve deeper background spans.

---

## 5. Simulation & Verification

To verify that the MCP server operates correctly in your workspace:

1. Generate a standard writing context pack:
   ```bash
   writing-context-rtfm pack --task "write introduction" --target sections/introduction.tex --budget 2000
   ```
2. Run again to confirm SQLite caching speeds up subsequent retrievals:
   ```json
   "cache": {
     "enabled": true,
     "hit": true
   }
   ```
3. Run with a highly restricted budget to verify the self-correction warning triggers:
   ```bash
   writing-context-rtfm pack --task "write introduction" --target sections/introduction.tex --budget 150
   ```
   *Expected output:* `"status": "degraded"` with warning containing `To resolve this, call the tool with a larger token_budget of at least 1150.`
