# Private annotation protocol

Run these roles as independent coding-agent passes. Each pass records its status in the private
case manifest. Do not paste manuscript prose into commits, issues, chat transcripts, or public
reports.

## 1. Curator — complete project

The curator may see the immutable archive and complete target. They choose the target, write the
task, hash the target, and draft atomic required ideas, anchor aliases, terminology, prohibited
claims, protected literals, citation sets, and graded relevant-source spans. Every required idea
must be independently scorable. The curator sets `annotations.curator: complete` but does not
author section cards.

## 2. Card author — masked project and task only

Preparation first replaces the target body with the benchmark marker. The card author receives
only that masked workspace and the task—never the private gold file or gold-derived rubric. They
review the generated title/task-only cards, add only facts independently supported by visible
sources, and set `annotations.card_author: complete`. Cards are then hashed and frozen. If gold
knowledge was used, discard and rebuild the cards from a fresh masked workspace.

## 3. Auditor — complete project plus both handoffs

The auditor checks archive/target/gold hashes, rubric atomicity, source-span relevance grades,
citation-key validity, explicit index files, frozen-card provenance, and the over-50-token leakage
report. They may compare gold and visible sources but must not copy gold facts into cards. Record
each unresolved issue in `annotations.disagreements`; otherwise set `annotations.auditor:
complete`.

Use stable issue codes in the form `code:private-detail`. Keep objective corpus defects, such as a
manuscript citation whose BibTeX entry is genuinely absent, in `annotations.corpus_warnings`
instead of rubric disagreements. Corpus warnings remain mechanical audit failures and must remain
visible in reports, but they do not by themselves invalidate source-relevance annotations.

The private independent review at `private.local/annotation-reviews/auditor-PN.json` is canonical.
The audit command verifies that its decision and issue list match the manifest. Never clear a
semantic disagreement merely because an exact word appears in gold: source sufficiency, causal
claims, comparative advantages, implicit limitations, and method rationale require an independent
curator correction followed by auditor approval. Unresolved cases remain excluded from aggregate
relevance metrics.

Unresolved cases may still exercise generation, but their relevance metrics are excluded from
aggregates. Do not resolve disagreements with an automatic tie-breaker.

## Before paid calls

Run `retrieve --stage pilot --limit-cases 2` and inspect private prepared metadata and retrieval
artifacts for exact source paths, masks, allowlists, card hashes, token counts, and leakage flags.
Resolve unexpected leakage or indexing before proceeding. Then run `generate --stage pilot
--limit-repetitions 1 --confirm-paid-run` as the paid smoke test. The full pilot is authorized only
after reviewing all four smoke-test conditions.

Proofreading cases belong to a later, separate track after the writing-pack harness is stable.
