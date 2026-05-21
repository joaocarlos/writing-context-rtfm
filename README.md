<!-- mcp-name: writing-context-rtfm -->
<div align="center">

***Surgical Context for Writing Agents***

Stop giving your AI agent the entire manuscript to write one section. Give it the exact paragraphs, constraints, and dependencies it needs to succeed. No token bloat. No hallucinations.

**`Lightweight · Task-Focused · Extension · MIT`**

<br>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) [![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/) [![MCP](https://img.shields.io/badge/MCP-2026-green.svg)](https://modelcontextprotocol.io/) [![Powered by RTFM](https://img.shields.io/badge/Powered%20by-RTFM-purple.svg)](https://github.com/roomi-fields/rtfm)

</div>

---

<!-- ─────────── TIER 1 — Pain & promise ─────────── -->

Your writing agent is drowning in tokens.

You ask Claude or Cursor to "Write the methodology section." To give it context, you feed it your 50-page manuscript, your related works, and your notes. The agent gets overwhelmed by the global narrative, loses track of the specific hyper-parameters you wanted to include, and writes a generic, repetitive summary that reads like a high-school essay. 

The bottleneck isn't the model's writing ability — it's the **noise**.

**`writing-context-rtfm` fixes the noise.** It is a lightweight MCP extension built on top of `rtfm-ai`. Instead of letting the agent grep freely, it acts as a gatekeeper. It takes the agent's task, queries the underlying RTFM index, aggressively filters out background noise, and packs only the *essential* and *supporting* source chunks into a tight, highly-focused prompt.

```bash
python3 src/writing_context_rtfm/cli.py pack \
  --task "Write the methodology section detailing dataset and quantization" \
  --target section_approach
```

3 seconds later, the agent receives an 800-token context pack containing exactly the 3 paragraphs about the dataset, the 1 paragraph about quantization, and the stylistic constraints for the section. The agent writes perfectly.

> **Token budgets respected. Constraints enforced. Progressive disclosure over context dumps.**

---

## Installation & MCP Integration

`writing-context-rtfm` functions as an MCP (Model Context Protocol) server. You can run it dynamically using `uvx` (recommended) or install it globally on your system.

### Option A: Dynamic Run (Recommended)
You do not need to install the package manually. You can prefix commands with `uvx` (or `npx` equivalent) to run the server on demand.

### Option B: Global Installation
If you prefer a static global installation:

```bash
# Using uv
uv tool install writing-context-rtfm

# Using pipx
pipx install writing-context-rtfm
```

Once installed, use `writing-context-rtfm serve` as the server command instead of `uvx writing-context-rtfm serve`.

---

### 1. Claude Desktop
Add this to your `claude_desktop_config.json` (on macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "writing-context-rtfm": {
      "command": "uvx",
      "args": [
        "writing-context-rtfm",
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
5. **Command:** `uvx writing-context-rtfm serve`

### 3. VS Code Extensions (Cline, Roo Code)
Update your MCP settings file (e.g., `cline_mcp_settings.json`):

```json
{
  "mcpServers": {
    "writing-context-rtfm": {
      "command": "uvx",
      "args": [
        "writing-context-rtfm",
        "serve"
      ]
    }
  }
}
```

### 4. Claude Code (Anthropic CLI Agent)
You can configure the server globally for Claude Code using:

```bash
claude mcp add --scope user --transport stdio writing-context-rtfm -- uvx writing-context-rtfm serve
```

Or for a specific project/repository:

```bash
claude mcp add --scope local --transport stdio writing-context-rtfm -- uvx writing-context-rtfm serve
```

### 5. Codex (CLI & Desktop)
To add the server using the Codex CLI:

```bash
codex mcp add writing-context-rtfm -- uvx writing-context-rtfm serve
```

Alternatively, you can manually append it to your Codex configuration file (`~/.codex/config.toml`):

```toml
[mcp_servers.writing-context-rtfm]
command = "uvx"
args = ["writing-context-rtfm", "serve"]
enabled = true
```

*(If testing locally before publishing, you can use `uvx --from /absolute/path/to/writing-context-rtfm writing-context-rtfm serve` or `uv run python -m writing_context_rtfm.cli serve` instead).*

---

<!-- ─────────── TIER 2 — Positioning & buzz ─────────── -->

## The Core Philosophy: RTFM Retrieves, We Pack

| **Tool** | **Role** | **Action** | **Output** |
|----------|----------|------------|------------|
| `rtfm-ai` | The Retrieval Layer | Indexes everything, runs FTS/Semantic search, returns raw hits. | 25 raw chunks |
| `writing-context-rtfm` | The Curation Layer | Filters noise, applies constraints, ranks by structural priority. | 4 essential chunks |

We do **not** replace or fork RTFM. We wrap it. RTFM is built to fetch memory. `writing-context-rtfm` is built to decide *what is enough memory to write a specific section*. 

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

## What we measured: A/B Testing

We tested a Gemini 2.5 agent trying to write a methodology section using two context strategies:

| Strategy | Context Size | Result Quality |
|----------|--------------|----------------|
| **Agent A (Full Repo Context)** | 3,000+ tokens | Wrote a generic paper summary. Hallucinated an introduction. Skipped hyperparameters entirely because it lost focus. |
| **Agent B (MCP Context Pack)** | **~600 tokens** | Jumped straight to the methodology. Correctly documented the CNN architecture, Adam optimizer, and learning rates. Followed all constraints. |

When you restrict an agent's vision to exactly what matters, its writing becomes sharper, more structured, and deeply accurate.

---

<!-- ─────────── TIER 3 — Technical depth ─────────── -->

### Intelligent Scoring & Priority
Raw search scores aren't enough for structural writing. We combine semantic relevance with manuscript structure:
- **RTFM Score**: Baseline semantic/FTS relevance.
- **Target Boost**: Chunks coming from the file the agent is supposed to be writing (`03_approach.tex`) get an automatic +0.8 boost.
- **Key-Term Scoping**: If a chunk matches a key term (e.g., "CNN") but comes from the *Conclusion* file, it receives a severe 75% score penalty to prevent background noise from polluting the context.

Chunks are finally classified as `[Priority: essential]`, `supporting`, or `background` so the LLM knows what to focus on.

### CLI Reference
```bash
# Initialize project config and SQLite cache
writing-context-rtfm init
writing-context-rtfm init-db

# Sync the underlying RTFM index
writing-context-rtfm sync --corpus manuscript

# Generate a context pack directly in the terminal
writing-context-rtfm pack \
  --task "Update the introduction" \
  --target section_intro \
  --budget 4000

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
