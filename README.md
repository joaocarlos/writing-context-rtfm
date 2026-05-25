<!-- mcp-name: writing-context-rtfm -->
<div align="center">

***Surgical Context for Writing Agents***

Stop giving your AI agent the entire manuscript to write one section. Give it the exact paragraphs, constraints, and dependencies it needs to succeed. No token bloat. No hallucinations.

**`Lightweight · Task-Focused · Extension · MIT`**

<br>

[![PyPI Version](https://img.shields.io/pypi/v/writing-context-rtfm.svg)](https://pypi.org/project/writing-context-rtfm/) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) [![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/) [![MCP](https://img.shields.io/badge/MCP-2026-green.svg)](https://modelcontextprotocol.io/) [![Powered by RTFM](https://img.shields.io/badge/Powered%20by-RTFM-purple.svg)](https://github.com/roomi-fields/rtfm)

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

## Installation & Onboarding

`writing-context-rtfm` is published on PyPI and runs as a Model Context Protocol (MCP) server.

### 1. Install the CLI & Server
You can install the package globally or in your virtual environment:

```bash
# Using uv (recommended)
uv tool install writing-context-rtfm

# Using pipx
pipx install writing-context-rtfm
```

---

### 2. Quick Project Onboarding
To integrate the server into your manuscript repository, run the following two commands:

#### Step A: Initialize configuration and editor rules
```bash
writing-context-rtfm init
```
This command non-destructively:
* Creates a self-documenting `.writing-context/config.yaml` file template showing how to tune token budgets and role weights.
* Appends the cache database path to your `.gitignore`.
* Updates your local `.mcp.json` to register the MCP server automatically.
* Adds **Agent Rules of Thumb** blocks into `CLAUDE.md`, `AGENTS.md`, and `GEMINI.md` to guide AI agents on retrieving context first and respecting LaTeX boundaries.

#### Step B: Auto-scaffold your section cards
```bash
writing-context-rtfm init-cards
```
This scans your workspace for LaTeX files, parses `\input` structures and references, and scaffolds your manuscript sections and dependency mappings in `.writing-context/section_cards.yaml`.

---

## MCP Server Integration

### 1. Claude Desktop
Add this to your `claude_desktop_config.json` (on macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

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
5. **Command:** `writing-context-rtfm serve`

### 3. VS Code Extensions (Cline, Roo Code)
Update your MCP settings file (e.g., `cline_mcp_settings.json`):

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

### 4. Claude Code (Anthropic CLI Agent)
```bash
# Global configuration
claude mcp add --scope user --transport stdio writing-context-rtfm -- writing-context-rtfm serve

# Repository-local configuration
claude mcp add --scope local --transport stdio writing-context-rtfm -- writing-context-rtfm serve
```

---

<!-- ─────────── TIER 2 — Positioning & buzz ─────────── -->

## The Core Philosophy: RTFM Retrieves, We Pack

| **Tool** | **Role** | **Action** | **Output** |
|----------|----------|------------|------------|
| `rtfm-ai` | The Retrieval Layer | Indexes everything, runs FTS/Semantic search, returns raw hits. | 25 raw chunks |
| `writing-context-rtfm` | The Curation Layer | Filters noise, applies constraints, ranks by structural priority. | 4 essential chunks |

We do **not** replace or fork RTFM. We wrap it. RTFM is built to fetch memory. `writing-context-rtfm` is built to decide *what is enough memory to write a specific section*. 

---

## Features

### 1. Agent Self-Correction Loop
If the requested token budget is too small to fit the necessary target context, the packer returns a `"status": "degraded"` and appends a warning to the `warnings` list recommending a specific minimum budget:
* **Writing Packs (`pack`)**: `To resolve this, call the tool with a larger token_budget of at least X.`
* **Proofreading Packs (`proofread-pack`)**: `To resolve this, call the tool with a larger max_tokens value of at least X.`

AI client agents are instructed via the auto-injected guidelines to parse this warning, extract the recommended value `X`, and automatically retry the call with the new budget to get the missing context.

### 2. LaTeX Safety Checks
The extension automatically parses the target text and detects LaTeX math environments, macro calls, and cross-references (e.g., `\ref{...}`, `\begin{equation} ... \end{equation}`). It issues safety warnings to the AI writing agent containing the exact code patterns that must not be deleted or broken during edits, maintaining compilation safety.

### 3. SQLite Local Caching
To optimize token and response latency, generated context packs are hashed and cached locally in `.writing-context/context_cache.sqlite`. Cache keys are dynamically invalidated whenever the project configuration, section cards, or underlying RTFM indexes are updated.

---

## The Section Cards Pattern
Writing agents need rules. We enforce them using a `.writing-context/section_cards.yaml` file. 

```yaml
version: 1
document:
  title: "TinyML Fault Detection"
sections:
  section_approach:
    title: "Proposed approach"
    path: "sections/03_approach.tex"
    key_terms: ["CNN", "quantization"]
    must_preserve:
      - "The train-test split is fixed and reproducible."
    avoid:
      - "real-time deployment claims"
```

When the agent asks for context to write `section_approach`, we:
1. **Expand the query**: We don't just search the agent's prompt. We search the section title and key terms.
2. **Post-filter Avoids**: If an RTFM result matches an `avoid` term, it is instantly discarded.
3. **Inject Constraints**: The returned context pack explicitly feeds the `must_preserve` rules directly to the LLM.

> [!IMPORTANT]
> **Modular documents are required.** This extension's noise-reduction algorithms heavily rely on the `path` defined in your section cards to perform "Target Boosts" and semantic scoping. If your entire manuscript is just a single monolithic `main.tex` or `main.md` file, the packer won't be able to distinguish the target section from background noise. Keep your writing modular (e.g., `sections/01_intro.tex`, `sections/02_methodology.tex`) for optimal results.

---

## CLI Reference

```bash
# Initialize project config, gitignore, and editor rules
writing-context-rtfm init

# Scan workspace to scaffold section cards from LaTeX inputs and labels
writing-context-rtfm init-cards

# Run diagnostics health checks on databases and configuration files
writing-context-rtfm doctor

# Sync the underlying RTFM index
writing-context-rtfm sync

# Generate a context pack directly in the terminal
writing-context-rtfm pack \
  --task "Update the introduction" \
  --target sections/introduction.tex \
  --budget 4000

# Generate a proofreading context pack
writing-context-rtfm proofread-pack sections/abstract.tex --line-start 1 --line-end 10 --max-tokens 3000

# Start MCP Server
writing-context-rtfm serve
```

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
