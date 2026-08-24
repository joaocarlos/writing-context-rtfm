# Model Context Protocol (MCP) Tools & Prompts Reference

`writing-context-rtfm` exposes a rich, 17-tool MCP interface designed to provide AI coding and writing agents (Claude Desktop, Cursor, Claude Code, Roo Code, Antigravity) with surgical context, section constraints, terminology auditing, and reference graph intelligence.

---

## Tool Summary Matrix

| Tool Name | Primary Purpose | Category |
| :--- | :--- | :--- |
| [`get_writing_context_pack`](#1-get_writing_context_pack) | Retrieve surgical context pack for drafting/rewriting sections. | Core Writing |
| [`get_proofreading_context_pack`](#2-get_proofreading_context_pack) | Retrieve context and immutability rules for proofreading/polishing text. | Proofreading |
| [`request_more_context`](#3-request_more_context) | Paginate and fetch additional background spans for a previous run. | Pagination |
| [`submit_generation_feedback`](#4-submit_generation_feedback) | Submit evaluation feedback on retrieved context to optimize cache. | Evaluation |
| [`audit_manuscript_terminology`](#5-audit_manuscript_terminology) | Detect undeclared term usage, drift, and glossary mismatches across drafts. | Terminology |
| [`get_term_context`](#6-get_term_context) | Look up term definitions, variants, and words to avoid. | Terminology |
| [`get_manuscript_reference_graph`](#7-get_manuscript_reference_graph) | Inspect `\label`, `\ref`, and `\cite` cross-reference dependency graph. | Graph & AST |
| [`inspect_target_section`](#8-inspect_target_section) | Inspect metadata, purpose, constraints, and dependencies for a section. | Cards & Structure |
| [`initialize_section_cards`](#9-initialize_section_cards) | Scan workspace and scaffold `cards.generated.yaml`. | Card Management |
| [`review_card_candidates`](#10-review_card_candidates) | List pending generated candidates for human/agent review. | Card Management |
| [`accept_card_candidate`](#11-accept_card_candidate) | Accept a candidate value into `cards.overrides.yaml`. | Card Management |
| [`reject_card_candidate`](#12-reject_card_candidate) | Reject a candidate value and record reason in `cards.lock.json`. | Card Management |
| [`edit_card_field`](#13-edit_card_field) | Manually edit or override a specific field in section cards. | Card Management |
| [`explain_card_candidate`](#14-explain_card_candidate) | Retrieve model rationale, confidence, and provenance for a candidate. | Card Management |
| [`get_card_field_diff`](#15-get_card_field_diff) | Inspect changes between generated and overridden section cards. | Card Management |
| [`get_section_card_history`](#16-get_section_card_history) | View modification history and candidate decisions for a section. | Card Management |
| [`get_target_feedback_summary`](#17-get_target_feedback_summary) | Retrieve aggregated feedback metrics for a target section for offline inspection. | Evaluation |
| [`refresh_index`](#18-refresh_index) | Re-sync RTFM retrieval index and invalidate stale cache entries. | Maintenance |


---

## 1. Core Context Retrieval Tools

### 1. `get_writing_context_pack`
Retrieves a compact, prioritized writing context pack containing unbroken target text, 1-hop reference graph definitions, local bibliography citations, and structural constraints.

#### Parameters
* **`task`** (*string*, required): Description of the writing or revision task.
* **`target`** (*string*, optional): Section identifier (e.g. `section_methodology`, `subsec:hardware_eval`) or file path.
* **`token_budget`** (*integer*, optional): Maximum token budget. Defaults to the configured context budget, typically `6000`. If non-negotiable target prose exceeds a soft budget, the engine auto-scales to preserve atomicity.
* **`must_consider`** (*array of strings*, optional): Required evidence atoms. Add each concrete concept, fact, protected literal, or citation key that the returned context must support. The pack reports coverage in `quality.atomic_coverage`.
* **`task_type`** (*string*, optional): One of `"write_new_section"`, `"revise_existing_section"`, `"proofread"`, `"expand"`, `"condense"`, `"align_with_previous_sections"`, or `"review"`.
* **`pack_mode`** (*string*, optional, default `"standard"`): Depth mode (`"minimal"`, `"standard"`, `"deep"`).
* **`role_budgets`** (*object*, optional): Source-role fractions for `target_text`, `local_context`, `dependency`, and `reference`; values must sum to `1.0`.
* **`strict_budget`** (*boolean*, optional, default `false`): When true, strictly caps tokens at the requested budget.

With the default elastic budget, the selector expands at most once—never through an open-ended retrieval loop—and never beyond `context.max_token_budget`. It first reserves evidence for required atoms and then fills remaining space by relevance. If the ceiling prevents full coverage, the result is degraded and names the uncovered atom IDs.

#### Output Example
```json
{
  "status": "complete",
  "task": "Draft subsection comparing latency on MCU platforms",
  "target": "section_methodology",
  "estimated_tokens": 3840,
  "quality": {
    "atomic_coverage": {
      "required": 2,
      "covered": 2,
      "ratio": 1.0,
      "uncovered": [],
      "requested_token_budget": 3000,
      "effective_token_budget": 3840,
      "expanded_for_coverage": true
    }
  },
  "document_thesis": "Parametric SLMs enable offline vehicle manual QA on MCUs.",
  "constraints": ["Write equations using LaTeX align environments"],
  "source_spans": [
    {
      "path": "access.tex",
      "line_start": 124,
      "line_end": 272,
      "priority": "essential",
      "source_role": "target_text",
      "score": 1.0
    },
    {
      "path": "references.bib",
      "line_start": 259,
      "line_end": 286,
      "priority": "supporting",
      "source_role": "reference",
      "score": 12.69
    }
  ],
  "formatted_prompt": "You are writing/editing a manuscript section...",
  "guidance": "Use the provided literature spans and citation keys..."
}
```

---

### 2. `get_proofreading_context_pack`
Retrieves a targeted proofreading context pack for polishing, grammar correction, and style consistency without context contamination.

#### Parameters
* **`target_file`** (*string*, required): Path to the file being proofread (e.g. `access.tex`, `chapter1.md`).
* **`line_start`** (*integer*, required): Starting line number (1-indexed).
* **`line_end`** (*integer*, required): Ending line number (1-indexed).
* **`mode`** (*string*, optional, default `"surface"`): One of `"surface"`, `"academic_clarity"`, `"consistency"`, or `"latex_safe"`.
* **`strictness`** (*string*, optional, default `"moderate"`): One of `"conservative"`, `"moderate"`, or `"assertive"`.
* **`max_tokens`** (*integer*, optional, default `4000`): Maximum token budget.

#### Safety Features
* Extracts and catalogs all `\cite{...}`, `\ref{...}`, `\label{...}`, and math environments as **immutable tokens**.
* Restricts retrieval exclusively to existing citations and terminology definitions to avoid injecting unprompted claims.

---

### 3. `request_more_context`
Fetches the next page of candidate source spans that were retrieved during the initial search but omitted due to token constraints.

#### Parameters
* **`run_id`** (*string*, required): The `run_id` UUID returned from a previous `get_writing_context_pack` call.
* **`limit`** (*integer*, optional, default `5`): Maximum number of additional spans to fetch.

---

### 4. `submit_generation_feedback`
Logs downstream agent evaluation metrics (e.g. helpfulness, constraint satisfaction, hallucinations) into the SQLite audit log.

#### Parameters
* **`run_id`** (*string*, required): The `run_id` UUID from the context pack.
* **`metric_name`** (*string*, required): Metric category (e.g. `"helpfulness"`, `"hallucination"`, `"constraint_violated"`).
* **`metric_value`** (*number*, required): Floating-point score (e.g. `1.0` for positive/passed, `0.0` for negative/failed).
* **`metric_text`** (*string*, optional): Qualitative explanation or failure note.

---

## 2. Terminology & Glossary Tools

### 5. `audit_manuscript_terminology`
Scans the entire manuscript index to detect terminology inconsistencies, undeclared acronym usage, and semantic drift.

#### Parameters
* **`project_root`** (*string*, optional): Root workspace path.

#### Output
Returns an audit report mapping each term to its declared sections, total occurrence count, line snippets, and warnings for undeclared section usage.

---

### 6. `get_term_context`
Looks up an individual technical term across `cards.overrides.yaml` and `cards.generated.yaml`.

#### Parameters
* **`term`** (*string*, required): The technical term or acronym to inspect (e.g. `"TinyGPT"`, `"MCU"`).
* **`project_root`** (*string*, optional): Root workspace path.

#### Output
Returns official definitions, permitted synonyms/variants, and words to avoid.

---

## 3. Structural & Graph Inspection Tools

### 7. `get_manuscript_reference_graph`
Extracts and builds the complete cross-reference dependency graph across all `.tex` and `.md` files.

#### Parameters
* **`project_root`** (*string*, optional): Root workspace path.

#### Output
Maps every declared `\label{...}` to its source file and line number, lists referenced labels, and extracts BibTeX citation keys.

---

### 8. `inspect_target_section`
Returns the effective merged section card, document title/thesis/style, and card-validation warnings. This is the read-before-write companion to the card mutation tools.

#### Parameters
* **`target`** (*string*, required): Section ID (e.g. `section_methodology`).
* **`project_root`** (*string*, optional): Root workspace path.

#### Output
Returns section title, path, purpose, dependencies, key terms, facts that must be preserved, prohibited wording, and constraints.

---

## 4. Section Card Scaffolding & Candidate Lifecycle Tools

### 9. `initialize_section_cards`
Scans workspace drafts and scaffolds `.writing-context/cards.generated.yaml`.

### 10. `review_card_candidates`
Lists pending candidate fields (purposes, facts, key terms, constraints) generated by deterministic scan or LLM inference.

### 11. `accept_card_candidate`
Accepts a candidate value into `.writing-context/cards.overrides.yaml` and appends an `accepted` event to the section history.

### 12. `reject_card_candidate`
Rejects a candidate, updates the decision snapshot, and appends a `rejected` event to `.writing-context/cards.lock.json`.

### 13. `edit_card_field`
Directly edits or deletes a field in `cards.overrides.yaml` and appends an `edited` or `deleted` history event.

### 14. `explain_card_candidate`
Returns the model rationale, extraction confidence rating, and source provenance for a specific candidate value.

### 15. `get_card_field_diff`
Compares generated, explicit override, and effective merged values for a section card.

#### Parameters
* **`section_id`** (*string*, required): Section ID to compare.
* **`project_root`** (*string*, optional): Root workspace path.

#### Output
Returns each field's `generated`, `override`, `effective`, `overridden`, and `changed` values plus `changed_fields`.

### 16. `get_section_card_history`
Returns append-only accept, reject, edit, and delete events plus the current decision snapshot.

#### Parameters
* **`section_id`** (*string*, required): Section ID whose history should be returned.
* **`limit`** (*integer*, optional, default `50`): Number of most-recent events, from `1` to `100`.
* **`project_root`** (*string*, optional): Root workspace path.

Older lock files remain valid and return an empty history until the first new mutation is recorded.
The deliberately destructive `cards rebuild` command clears the lock file and its recorded history.

---

### 17. `get_target_feedback_summary`
Retrieve aggregated feedback metrics for a target section for offline inspection and evaluation.

#### Parameters
* **`target`** (*string*, required): Target section ID (e.g. `section_methodology`).

---

## 5. Maintenance & Indexing Tools

### 18. `refresh_index`
Triggers an index synchronization and clears cached context packs.


---

## Pre-Packaged MCP Prompt Templates

Clients supporting MCP Prompts (`prompts/list`, `prompts/get`) can hydrate prompt templates directly:

1. **`write_section`**:
   - `task`: Task description.
   - `target`: Section ID or path.
   - `token_budget`: Token cap.
2. **`proofread_section`**:
   - `target_file`: File path.
   - `line_start`: Starting line.
   - `line_end`: Ending line.
   - `mode`: `"surface"`, `"academic_clarity"`, `"consistency"`, or `"latex_safe"`.

The server does not currently expose MCP resources. Section-card inspection is provided through `inspect_target_section` to avoid duplicating the same contract as both a tool and a resource.
