# Features Guide: writing-context-rtfm

This guide details the design, usage, and schema of the five new feature modules implemented to improve the extension.

---

## 1. Split-Card Scaffolding & Overrides Framework

### Objective

Allows users to manage manuscript metadata without manual file maintenance. The system automatically builds cards from the LaTeX source code tree, while preserving human-authored thesis, style, glossary, and section overrides in a separate file.

### CLI Subcommand Suite: `cards`

- **`cards scan`**: Recursively scans `.tex` and `.md` drafts, parses inputs/cross-references, maps section hierarchies, and writes deterministic structures to `.writing-context/cards.generated.yaml`.
- **`cards infer`**: Runs generative LLM semantic extraction on new/modified sections to identify their purpose, facts, and constraints (requires an OpenAI API key, automatically skipped offline).
- **`cards review`**: Interactively reviews, accepts, or rejects model-generated section candidates.
- **`cards update`**: Re-evaluates manuscript files, marking modified sections as stale to trigger targeted re-inference.
- **`cards validate`**: Verifies configuration files for stale fields, missing LaTeX paths, and cross-reference dependencies.
- **`cards build`**: Combined pipeline command that executes scan, infer, and update in sequence.

### Tool: `initialize_section_cards`
- **Behavior**: Scans workspace files, maps dependencies, and initializes configuration skeletons inside `.writing-context/cards.generated.yaml`.
- **Arguments**: `project_root` (Optional).
- **Output JSON**:
    ```json
    {
        "status": "success",
        "sc_path": ".writing-context/cards.generated.yaml",
        "added": [{ "id": "section_methodology", "path": "sections/methodology.tex" }],
        "preserved_count": 4,
        "total_sections": 5
    }
    ```

---

## 2. Configurable Zotero Grounding

### Objective

Allows writing agents to fetch related literature context directly from your local Zotero library, while preventing out-of-domain search results from polluting the context token budget.

### Configuration: `similarity_threshold`

- **Behavior**: When Zotero semantic search is queried, the provider filters out paper snippets that do not match the target query's context. It parses the returned similarity score and drops any results below the threshold.
- **Tuning**: Configurable under `providers.zotero.extra` in `.writing-context/config.yaml`:
  * **`similarity_threshold` (Default: `-0.4`)**: Swept and optimized to preserve relevant priority scheduling and queueing papers (scores `-0.19` to `-0.3`) while successfully discarding unrelated topics.
  * **`include_abstract` (Default: `false`)**: Configures whether full document abstracts are packed into retrieved citation spans.

---

## 3. Context Pack Pagination ("Progressive Expansion")

### Objective

Allows agents to fetch context in multiple stages or "tiers" when the initial token budget restricts the retrieved source spans.

### Tool: `request_more_context`

- **Behavior**: Retrieves additional background or supporting context spans that were initially found during search but not included in the main pack due to token constraints (marked with `selected = 0` in the database). Marks retrieved items as selected so subsequent requests get fresh pagination pages.
- **Arguments**:
    - `run_id`: The unique UUID from a previous context generation run.
    - `limit`: (Default: 5) Maximum number of additional context spans to return.
- **Output JSON**:
    ```json
    {
        "run_id": "test-run-123",
        "source_spans": [
            {
                "path": "sections/background.md",
                "line_start": 40,
                "line_end": 50,
                "score": 0.85,
                "reason": "Retrieved supporting context span",
                "priority": "supporting"
            }
        ],
        "count": 1
    }
    ```

---

## 4. Generation Feedback Loop & Cache Optimization

### Objective

Allows client agents to submit automated evaluations of context packs (e.g. helpfulness, hallucinations, constraint violations) to audit generation runs and build a dataset for downstream optimization.

### Tool: `submit_generation_feedback`

- **Behavior**: Records evaluation scores and comments associated with a `run_id` into the `evaluation_records` table in SQLite.
- **Arguments**:
    - `run_id`: The context pack's unique UUID.
    - `metric_name`: Metric category (e.g., `helpfulness`, `hallucinations`, `constraint_violated`).
    - `metric_value`: 1.0 (True/Positive) or 0.0 (False/Negative).
    - `metric_text`: (Optional) Text explanation.
- **Output JSON**:
    ```json
    {
        "status": "feedback_saved",
        "run_id": "test-run-123"
    }
    ```

---

## 5. Semantic Drift & Terminology Auditing

### Objective

Ensures consistent technical terms are used across sections and detects semantic drift or term mismatches before files are finalized.

### Tool: `audit_manuscript_terminology`

- **Behavior**: Collects all key terms defined in the document's section cards. For each term, executes queries against the RTFM index. Analyzes occurrences to detect:
    1. **Undeclared usage**: Occurrences in files whose sections do not define the term and do not declare dependencies on sections that do.
    2. **Unused terms**: Terms defined in cards but never found in the manuscript index.
- **Arguments**:
    - `project_root`: (Optional) Custom workspace path.
- **Output JSON**:
    ```json
    {
        "status": "success",
        "audited_terms_count": 12,
        "report": {
            "quantization": {
                "declared_in_sections": ["section_intro"],
                "occurrence_count": 3,
                "warnings": [
                    "Term 'quantization' is used in 'sections/results.md' (Section 'section_results'), but 'section_results' neither declares it nor depends on sections that do (['section_intro'])."
                ],
                "occurrences": [
                    {
                        "path": "sections/results.md",
                        "line_start": 10,
                        "line_end": 12,
                        "snippet": "We benchmark the quantization metrics."
                    }
                ]
            }
        }
    }
    ```

---

## 6. Native MCP Prompts Integration

### Objective

Exposes pre-defined prompt templates to the MCP client that automate the retrieval-pack generation and package it directly into user messages.

### Prompts

1. **`write_section`**: Hydrates a prompt for drafting or editing a section with context spans, document thesis, and constraint files.
    - **Arguments**: `task` (required), `target` (required), `token_budget` (optional).
2. **`proofread_section`**: Hydrates a prompt for grammar correction, styling consistency, and terminology check.
    - **Arguments**: `target_file` (required), `line_start` (required), `line_end` (required), `mode` (optional), `strictness` (optional).

- **Execution Flow**: When requested, the server runs the context pack generator in the background, extracts the payload, formats a detailed markdown instructions set, and returns it to the client as standard prompt messages.
