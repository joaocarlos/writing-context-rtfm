# Prompt: Generate Section Overrides (`cards.overrides.yaml`)

Use this prompt with an AI agent or LLM working inside a `writing-context-rtfm` manuscript project to generate or curate `.writing-context/cards.overrides.yaml` (schema `version: 2`).

> [!NOTE]
> In `writing-context-rtfm`, deterministic structural metadata (section IDs, files, line ranges, and LaTeX `\ref` -> `\label` dependencies) is handled automatically by the AST parser into `.writing-context/cards.generated.yaml`.
> 
> The `.writing-context/cards.overrides.yaml` file is the **curated semantic layer**: it holds the manuscript thesis, writing style, glossary, and surgical section constraints (`purpose`, `key_terms`, `must_preserve`, `avoid`, `constraints`).

---

## Prompt Template

You are a writing-context analyst. Your task is to analyze the manuscript and produce a curated, surgical `.writing-context/cards.overrides.yaml` file (schema `version: 2`) for `writing-context-rtfm`.

### Core Principle: Retrieval-Calibrated Context
Every field in this file directly controls how the RTFM retrieval engine and token budgeter operate downstream:
- **`key_terms`**: Each term triggers an individual semantic search query in RTFM. Over-specifying terms or choosing generic words (e.g., "algorithm", "results", "evaluation") floods the context with irrelevant chunks and exhausts token budgets.
- **`must_preserve`**: Injected verbatim into the prompt's immutable constraints. Copying full narrative sentences or paragraphs consumes budget tokens needed for actual context retrieval.
- **`depends_on`**: Imports dependency chunks and their key terms. Rely on the AST reference graph (`cards.generated.yaml`) for structural cross-references (`\cite`, `\ref`); only add explicit semantic dependencies when a section directly builds upon mathematical formulations or outputs from another section.
- **`avoid`**: Hard negative filter phrases used to discard noisy or tangential candidate spans.

---

### MCP Pre-flight Workflow (Recommended)
If working as an MCP agent:
1. **Initialize AST Scaffolding First**: Ensure `.writing-context/cards.generated.yaml` exists by calling `manage_section_cards(action="init")` or inspecting existing sections with `manage_section_cards(action="inspect", target="<section_id>")`.
2. **Reuse Canonical Section IDs**: Match the exact `section_id` keys discovered by the parser (e.g., `section_introduction`, `section_methodology`).
3. **Section-by-Section Inspection**: Rather than dumping entire files into context, inspect target sections individually or read their specific files.

---

### Schema (`cards.overrides.yaml`, Version 2)

```yaml
version: 2

document:
  title: "<Manuscript Title>"
  thesis: "<1-2 sentence core thesis>"
  writing_style:
    tone: "Academic, formal, concise"
    avoid_words: ["<cliché>", "<groundbreaking>"]
  terminology:
    "<Canonical Term>":
      definition: "<1-sentence definition>"
      variants: ["<alternate phrase>", "<acronym>"]
      avoid: ["<deprecated phrasing>"]

sections:
  <section_id>:
    # Exactly 1 sentence: core objective
    purpose: "<1 sentence objective>"

    # STRICTLY 3–5 specific domain terms (no generic words)
    key_terms:
      - "<domain term 1>"
      - "<domain term 2>"
      - "<domain term 3>"

    # STRICTLY 0–2 constants/equations only (NO prose sentences)
    must_preserve:
      - "<exact parameter/equation, e.g. 'eta = 1e-4'>"

    # STRICTLY 1–3 negative filter phrases
    avoid:
      - "<noisy out-of-scope phrase>"

    # STRICTLY 1–2 rhetorical rules
    constraints:
      - "<actionable boundary rule>"
```

---

### Field Budgets & Extraction Constraints

Follow these strict bounds to prevent context bloat and query explosion:

#### Document-Level Fields (`document`)

| Field | Hard Limit | Rules & Negative Guidance |
| :--- | :--- | :--- |
| `thesis` | Max 2 sentences | Central claim or hypothesis. Do not summarize outline. |
| `writing_style` | Tone + avoid words | Academic tone and banned cliché words. |
| `terminology` | 5–10 terms max | Critical project concepts, novel metrics, or ambiguous acronyms. |

#### Section-Level Fields (`sections.<section_id>`)

| Field | Hard Limit | Rules & Negative Guidance |
| :--- | :--- | :--- |
| `purpose` | Exactly 1 sentence | Core objective and contribution. No generic filler. |
| `key_terms` | **3 to 5 terms** | Specific technical entities or methods. **No generic words.** |
| `must_preserve` | **0 to 2 items** | Exact equations, symbols, or parameters. **No prose sentences.** |
| `depends_on` | 0 to 2 sections | Omit if linked by LaTeX `\ref`/`\label`. Use only for mathematical derivations. |
| `avoid` | 1 to 3 phrases | Short negative filters (2–4 words) to drop false-positive search results. |
| `constraints` | 1 to 2 rules | Actionable boundaries (e.g., "Confine dataset descriptions to Table 1"). |

---

### Execution Request

Analyze the project manuscript files (or target: `[Specify file path or leave blank for auto-discovery]`) and output the calibrated `.writing-context/cards.overrides.yaml` inside a single YAML code block.
