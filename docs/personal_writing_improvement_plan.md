# Personal Writing-Context Improvement Plan

## Goal and priority

Make AI-assisted writing more reliable by supplying the evidence needed for the task with less irrelevant context. Retrieval quality is the primary objective. Token reduction is valuable only when it does not remove required evidence.

This is a product-improvement plan for a personal writing workflow, not a claim of general research validity.

## Immediate improvement: atomic coverage

1. Treat every explicit `must_consider` item as one required evidence atom.
2. Treat citation keys explicitly requested in the task as citation atoms.
3. Count evidence already guaranteed in the packet—target text, constraints, `must_preserve` values, terminology, and verified prior claims—before asking retrieval to cover an atom.
4. Prefer the smallest high-ranked set of retrieved spans that covers the remaining atoms, then fill the rest of the pack by ordinary relevance ranking.
5. In elastic mode, calculate a single larger effective budget when required spans do not fit the requested budget. Never perform an open-ended retrieve-and-expand loop. Respect `context.max_token_budget` as the hard safety ceiling.
6. In strict mode, keep the caller's hard cap and report atoms that could not fit.
7. Report covered and uncovered atoms, coverage ratio, requested/effective budgets, and whether atomic coverage caused expansion.
8. When an atom has no supporting candidate, return a prominent warning that tells the agent to use `request_more_context` or direct-read the named dependencies. Do not pretend the pack is complete.

## Workflow defaults

- Keep RRF disabled until it consistently improves useful evidence coverage.
- Use elastic budgets for normal drafting and revision. Use strict budgets only when an external context limit is genuinely hard.
- Put the concrete facts, concepts, and citation keys that must appear in `must_consider`; keep broad stylistic instructions in the task or card constraints.
- Inspect `quality.atomic_coverage` before drafting. If coverage is incomplete, retrieve or read the missing evidence before generating prose.
- Preserve target LaTeX commands, labels, references, equations, citations, and protected literals.
- Keep canonical terms, accepted variants, and forbidden forms in the document glossary. Run the
  terminology audit before a final consistency pass; proofreading consumes matching glossary rules
  directly from the exact target text.
- Keep automatic RTFM sync disabled. Refresh explicitly after edits so retrieval never hides a
  long-running worker lifecycle inside a pack request.

## Validation for personal use

Use 8–12 representative writing tasks and compare the coverage-aware pack with the normal manual-context workflow. Record:

- whether every required idea and citation had supporting evidence;
- which context spans were irrelevant;
- how much manual context had to be added;
- editing time and number of factual/citation corrections;
- which draft was preferred;
- context size, latency, and process safety.

Promote a change into the default workflow when it reduces manual repair without causing missed evidence or protected-content regressions. Token savings are a secondary tie-breaker.

## Later improvements

1. Let agents turn task checklists into explicit `must_consider` atoms automatically, with user-visible confirmation.
2. Add a bounded direct-read fallback for known dependency files when retrieval has no candidate for an atom.
3. Learn from `submit_generation_feedback` which sources repeatedly help or distract for a target.
4. Revisit RRF only after atomic coverage and source-noise diagnostics are stable.
5. Keep proofreading target-first: direct-read the requested line range and adjacent paragraphs.
   Evaluate any semantic enhancement only for terminology consistency; never use retrieval to
   rediscover or replace the text being edited.
6. Keep local MiniLM and ModernBERT retrieval experimental. Current engineering canaries do not
   justify always-on dense retrieval or reranking. Prefer a future conditional fallback for explicit
   uncovered `must_consider` atoms.
