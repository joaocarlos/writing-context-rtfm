<!-- writing-context-rtfm MCP tools -->
## MCP Tools: writing-context-rtfm

**IMPORTANT: This project uses `writing-context-rtfm` for surgical context retrieval.
ALWAYS call `get_writing_context_pack` BEFORE writing, rewriting, or expanding any
section of this manuscript.** The context pack is cheaper (fewer tokens), scoped to
your task, and enforces the constraints the author has defined. Reading the whole
manuscript freely is wasteful and will cause you to miss critical constraints.

**EXCEPTIONS**: Do NOT call `get_writing_context_pack` when:
- The user is asking a factual or structural question about the project (answer directly).
- The task is purely administrative (renaming files, formatting YAML, git operations).
- The user explicitly says "no context needed" or "just answer".

---

### When to use `get_writing_context_pack` FIRST

| Task | Action |
|------|--------|
| Writing a new section or subsection | `get_writing_context_pack` with `task` + `target` |
| Rewriting or expanding existing text | `get_writing_context_pack` with the rewrite task |
| Checking what constraints apply to a section | `get_writing_context_pack` — read `constraints` in the pack |
| Verifying what prior sections said | `get_writing_context_pack` with `depends_on` section as `target` |
| Refreshing context after edits to the manuscript | `refresh_index` first, then `get_writing_context_pack` |

Fall back to direct file reads **only** when the pack's `source_spans` are insufficient
and the pack's `status` is `"degraded"` or `warnings` is non-empty.

---

### Tools

| Tool | When to use |
|------|-------------|
| `get_writing_context_pack` | Before any writing task — returns prioritized source spans, constraints, and the document thesis |
| `refresh_index` | After significant edits to the manuscript files, to re-sync the RTFM index and invalidate the cache |

---

### How to read a context pack

The pack returned by `get_writing_context_pack` has this structure:

```
task              — the writing task you submitted
target            — the section you are writing
document_thesis   — the manuscript's overarching argument (always respect this)
constraints       — hard rules injected from section cards (must_preserve + constraints fields)
source_spans      — the retrieved context chunks, each tagged with:
  path            — which file the chunk came from
  priority        — "essential" | "supporting" | "background"
  reason          — why this chunk was included
  score           — relevance score
status            — "complete" | "degraded" (degraded = missing section cards or failed queries)
warnings          — list of issues found during retrieval
```

**Priority rules:**
- `essential` — chunks from the target section file with high relevance. Use as primary context.
- `supporting` — chunks from dependency sections or high-relevance background. Use for coherence.
- `background` — low-relevance but retrieved. Use only if `essential` and `supporting` are insufficient.

---

### Workflow

```
1. User asks you to write/rewrite/expand section X
   └─► Call get_writing_context_pack(task=<task>, target=<section_id>, token_budget=<N>)

2. Read the pack:
   a. Respect ALL strings in `constraints` — these are the author's hard rules.
   b. Use `document_thesis` to stay aligned with the manuscript's central argument.
   c. Read `essential` spans first. Read `supporting` spans for coherence checks.
   d. Treat `background` spans as supplementary only.

3. Write the section using ONLY the retrieved context as your source of truth.
   - Do not introduce facts, claims, or data not present in the source spans.
   - Do not contradict any `must_preserve` constraint.

4. If the pack status is "degraded" or source_spans is empty:
   - Report the warnings to the user before writing.
   - Ask whether to proceed with limited context or to run refresh_index first.

5. After writing, do NOT re-read the entire manuscript to "check consistency".
   Instead, call get_writing_context_pack for the adjacent section if needed.
```

---

### Section IDs

Section IDs (used as `target`) are defined in `.writing-context/section_cards.yaml`.
List them before writing if unsure which ID to use. Common pattern: `section_intro`,
`section_methodology`, `section_results`, `section_conclusion`.
