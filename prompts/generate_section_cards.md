# Prompt: Generate Section Overrides (`cards.overrides.yaml`)

Use this prompt with an AI agent or LLM working inside a `writing-context-rtfm` manuscript project to generate or curate `.writing-context/cards.overrides.yaml` (schema `version: 2`).

> [!NOTE]
> In `writing-context-rtfm`, deterministic structural metadata (section IDs, files, line ranges, and LaTeX `\ref` -> `\label` dependencies) is handled automatically by the AST parser into `.writing-context/cards.generated.yaml`.
> 
> The `.writing-context/cards.overrides.yaml` file is the **curated semantic layer**: it holds the manuscript thesis, writing style, glossary, and surgical section constraints (`purpose`, `key_terms`, `must_preserve`, `avoid`, `constraints`).

---

## Prompt Template

```markdown
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
  thesis: "<1-2 sentence central thesis or core argument of the manuscript>"
  writing_style:
    tone: "<e.g., Academic, formal, concise, third-person>"
    avoid_words: ["<cliché 1>", "<groundbreaking>", "<game-changing>"]
  terminology:
    "<Canonical Technical Term>":
      definition: "<Concise 1-sentence definition of the concept>"
      variants: ["<alternate phrase 1>", "<acronym>"]
      avoid: ["<deprecated or incorrect phrasing to avoid>"]

sections:
  <section_id>:                     # snake_case ID (e.g., section_methodology)
    purpose: "<Exactly 1 sentence: objective and contribution of this section>"
    key_terms:
      - "<highly specific domain term 1>"
      - "<highly specific domain term 2>"
      - "<highly specific domain term 3>"
      # STRICTLY 3–5 terms. Discriminative technical terms or parameters.
      # NEVER generic words ('machine learning', 'performance', 'data').
    must_preserve:
      - "<exact parameter, threshold, or equation, e.g., 'eta = 1e-4'>"
      # STRICTLY 0–2 items. Numeric constants or formal equations only.
      # NEVER prose sentences, arguments, or paragraph excerpts.
    avoid:
      - "<noisy phrase or out-of-scope topic to exclude from retrieval>"
      # STRICTLY 1–3 short phrases (2–4 words) to filter search results.
    constraints:
      - "<actionable rhetorical rule, e.g., 'No benchmarks in this section'>"
      # STRICTLY 1–2 rules maximum.
```

---

### Field Budgets & Extraction Constraints

Follow these strict bounds to prevent context bloat and query explosion:

| Scope | Field | Hard Limit | Rules & Negative Guidance |
| :--- | :--- | :--- | :--- |
| `document` | `thesis` | Max 2 sentences | State the central claim or hypothesis. Do not summarize the outline. |
| `document` | `terminology` | 5–10 terms max | Critical project concepts, novel metrics, or ambiguous acronyms. |
| `sections.<id>` | `purpose` | Exactly 1 sentence | Core objective and contribution. No generic filler. |
| `sections.<id>` | `key_terms` | **3 to 5 terms** | Specific technical entities or methods. **No generic words.** |
| `sections.<id>` | `must_preserve` | **0 to 2 items** | Exact equations, symbols, or parameters. **No prose sentences.** |
| `sections.<id>` | `depends_on` | 0 to 2 sections | Omit if linked by LaTeX `\ref`/`\label`. Use only for mathematical derivations. |
| `sections.<id>` | `avoid` | 1 to 3 phrases | Short negative filters (2–4 words) to drop false-positive search results. |
| `sections.<id>` | `constraints` | 1 to 2 rules | Actionable boundaries (e.g., "Confine dataset descriptions to Table 1"). |

---

### Execution Request

Analyze the project manuscript files (or target: `[Specify file path or leave blank for auto-discovery]`) and output the calibrated `.writing-context/cards.overrides.yaml` inside a single YAML code block.
```
