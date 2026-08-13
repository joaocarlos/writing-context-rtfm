# Prompt: Generate Main-Section Cards (`version: 2`)

Use this prompt with an AI agent or LLM working inside a `writing-context-rtfm` manuscript project to generate or update `.writing-context/section_cards.yaml` (or `.writing-context/cards.overrides.yaml`).

---

## Prompt Template

```markdown
You are a writing-context analyst. Your task is to inspect the project workspace, analyze the manuscript files, and generate a valid `.writing-context/section_cards.yaml` file (schema `version: 2`) for `writing-context-rtfm`.

### Instructions for Manuscript Discovery
1. **Search Project Files**: Discover the main entry files (`.tex` or `.md` files) in the workspace. If specific files are named (e.g., `main.tex`, `manuscript.md`), analyze those target files first.
2. **Main-Section Organization**: Organize cards exclusively around top-level main sections (`\section` in papers/articles, `\chapter` in books/theses, or H1 `#` in Markdown). Do not create separate cards for subsections; encapsulate their content under the parent main section.
3. **Extract Metadata**: Derive the document title, central thesis statement, writing style, key technical terminology, and main section attributes directly from manuscript prose.

---

### Schema (Version 2)

Produce a YAML structure adhering to `version: 2`:

```yaml
version: 2

document:
  title: "<Manuscript Title>"
  thesis: "<1-2 sentence central thesis or core argument of the manuscript>"
  writing_style:
    tone: "<e.g., Academic, formal, concise, third-person>"
    avoid_words: ["<cliché>", "<groundbreaking>", "<game-changing>"]
  terminology:
    "<Canonical Term>":
      definition: "<Concise definition of the technical concept>"
      variants: ["<alternate phrase 1>", "<alternate phrase 2>"]
      avoid: ["<deprecated or incorrect phrasing to avoid>"]

sections:
  <section_id>:                     # snake_case identifier (e.g., section_introduction, section_methodology)
    title: "<Human-readable Section Title>"
    purpose: "<1 sentence: main objective and contribution of this section>"
    path: "<relative path to the section file, e.g., sections/01_introduction.tex>"
    key_terms:
      - "<domain-specific technical term central to this section>"
      # 3–8 specific terms recommended
    depends_on:
      - <other_section_id>          # section_ids whose outputs/claims this section references
    must_preserve:
      - "<exact sentence, formula, or caveat that must survive rewrites intact>"
    avoid:
      - "<phrase or topic to filter out from context packs for this section>"
    constraints:
      - "<free-form writing or structural constraint, e.g., 'List main contributions in a bulleted list'>"
```

---

### Guidelines

1. **`version`**: Must be set to `2`.
2. **`section_id`**: Use valid `snake_case` (e.g. `section_introduction`). This key is used with `--target` / `target` in context pack requests.
3. **`path`**: Provide exact relative paths from project root.
4. **`key_terms`**: Use exact technical terms used in the manuscript (e.g. `"quantization-aware training"` rather than generic `"machine learning"`).
5. **`must_preserve`**: Short verbatim claims, equations, or fixed statements (not instructions).
6. **`avoid`**: Short phrases (2-5 words) to filter out noisy or irrelevant search results.
7. **`terminology`**: Standardize critical project terms with canonical definitions, allowed variants, and forbidden phrases.

---

### Execution Request

Analyze the project manuscript files (or target: `[Specify file path or leave blank for auto-discovery]`) and output the complete `section_cards.yaml` inside a single YAML block.
```
