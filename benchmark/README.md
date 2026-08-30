# Private context-quality benchmark

Pilot v1 retrieval, candidate exposure, and BibTeX handoff diagnostics are formally closed. See
[`PILOT_V1_DECISION.md`](PILOT_V1_DECISION.md) for the evidence, negative production-change
decision, retained diagnostics, and criteria for reopening bibliographic prioritization.

This directory contains the committed, corpus-free parts of the writing-context benchmark.
Real manuscript archives, case rubrics, model configuration, masked workspaces, gold sections,
prompts, generations, judgments, and private reports are ignored by Git.

Copy `cases.example.yaml` to `cases.local.yaml` and `models.example.yaml` to
`models.local.yaml`, then populate only the local copies. The supplied model example uses
authenticated Antigravity and Codex CLI sessions and does not require API keys. Each CLI runs from
an isolated temporary directory with tools disabled or a read-only sandbox. Pin the exact model
identifier; do not bind the experiment to an installed CLI version. The harness never
substitutes a model.

The CLIs do not expose a common enforceable temperature or maximum-token interface. The harness
records the requested `0.2` temperature and output ceiling and places them in the benchmark
instruction, but reports must describe sampling controls as CLI-managed. If strict API-level
sampling equivalence is required, use the legacy `gemini`/`openai` HTTP providers with API keys.

## Manual run

Run every command from the repository root. Use the virtual-environment interpreter explicitly;
this machine does not provide a bare `python` command.

1. Create the ignored local configuration files if they do not already exist:

```text
cp benchmark/cases.example.yaml benchmark/cases.local.yaml
cp benchmark/models.example.yaml benchmark/models.local.yaml
```

Keep the private archives under these anonymized names:

```text
benchmark/P1-intelligent-cities.zip
benchmark/P2-smartbreathe.zip
benchmark/P3-tinymoe.zip
benchmark/P4-verus.zip
```

Fill in the local manifests, verify the exact Antigravity model ID with `agy models`, and confirm
that `codex` is available. Fix model IDs in the manifest, but do not bind the experiment to
installed CLI versions. Do not put credentials in either YAML file.

2. Run preflight. It verifies archive hashes, ZIP safety, privacy rules, disk space, dependencies,
CLI availability, and live model availability where the transport supports discovery:

```text
.venv/bin/python scripts/benchmark_context_quality.py preflight
```

When a coding-agent sandbox blocks Agy's log directory or localhost language-server port, run
this command in a normal terminal or approve the command's filesystem/network escalation. That is
not an API-key requirement.

3. Prepare masked, hash-addressed workspaces:

```text
.venv/bin/python scripts/benchmark_context_quality.py prepare
```

Preparation indexes only the explicitly allowlisted manuscript and bibliography files. It calls
RTFM's sync library in-process and does not start the machine-wide RTFM worker daemon.

4. Run the required two-case, retrieval-only canary. This makes no model calls:

```text
.venv/bin/python scripts/benchmark_context_quality.py retrieve --stage pilot --limit-cases 2
```

Inspect the private prepared metadata, masks, cards, source paths, leakage reports, and context
token counts. Every capped condition must be at most 6,000 tokens and have nonempty evidence.

5. Run the deterministic bibliography/annotation audit, then the full pilot retrieval:

```text
.venv/bin/python scripts/audit_benchmark_cases.py
.venv/bin/python scripts/benchmark_context_quality.py retrieve --stage pilot
.venv/bin/python scripts/benchmark_context_quality.py retrieval-report --stage pilot --anonymized
```

Unresolved annotation disagreements must remain flagged and be excluded from relevance
aggregates. Do not invent missing bibliography entries or repeatedly regenerate annotations until
a model approves them.

The audit summary separates `corpus_warning_case_ids` from
`unresolved_annotation_case_ids`. A corpus warning records an objective manuscript defect and
remains visible in mechanical audit results, but does not automatically invalidate an otherwise
approved relevance rubric. Semantic objections such as source sufficiency, comparative advantage,
implicit limitations, or method rationale require a curator correction and an independent auditor
approval; never clear them from exact-word matches alone. When a curator changes ideas, anchors,
citations, or expected source spans, rerun `prepare` for those cases and then rerun `retrieve`.
Annotation status or corpus-warning changes alone do not change the case hash.

Use `--project` to reaudit only projects whose annotations changed:

```text
.venv/bin/python scripts/review_benchmark_annotations.py --role auditor --project P1 --dry-run
.venv/bin/python scripts/review_benchmark_annotations.py --role auditor --project P1 --confirm-paid-run
```

Do not reduce `max_search_results_per_query` merely to make packs look cleaner: required evidence
can occur below the first few results. The primary ranking repair keeps the default result depth and
span cap, scopes target boosts to explicit target ranges in single-file manuscripts, preserves raw
retrieval rank for ties, restores ranked order after role-quota selection, and filters weak
keyword-only BibTeX overlap. The general score defaults remain unchanged because raising them would
risk recall on lower-scored corpora. Keep RRF disabled unless confirmation evidence satisfies the
predeclared promotion rule.

### Candidate diagnostics

Retrieval policy v3 stores candidate diagnostics in the private retrieval artifacts. For
`rtfm_topk`, `candidate_spans` is the ranked top-100 result pool before the context-budget cut. For
pack conditions, it is the unified, score-sorted pool after AST snapping and deduplication; raw
per-query and provider streams remain available separately in `candidate_streams`. The generation
prompt continues to use only `spans`.

The anonymized report includes candidate recall at 1, 3, 5, 10, 25, 50, and 100; the median first
candidate rank; the candidate-to-selected recall delta; stage recall; and aggregate loss counts.
Private `expected_source_outcomes` retain the source index and first loss transition for case-level
inspection. The stable pack stages are:

```text
retrieved -> deduplicated -> score_filtered -> diversified -> budget_candidates -> selected
```

Interpret `never_retrieved` as a candidate-generation failure. Interpret `retrieved_not_selected`
using the first loss stage: deduplication, score/avoid filtering, diversity ordering, atomic
coverage ordering, or final role/cap/token selection. Final selection losses are classified as
`token_budget`, `max_source_spans`, or `provider_reference_quota`. Candidate diagnostics require fresh v3
retrieval artifacts; reanalysis of pre-v3 artifacts cannot reconstruct candidates or final rejection
reasons that were not saved. The retrieval-only report defaults to
`benchmark/anonymized_aggregates/pilot-retrieval.json` and makes no model calls.

### Candidate exposure benchmark

After all eight Pilot v1 annotations are resolved, freeze the prepared cases, cards, RTFM index,
queries, normalization, deduplication, downstream composer, and hard limits before comparing
exposure policies:

```text
.venv/bin/python scripts/benchmark_candidate_exposure.py freeze
.venv/bin/python scripts/benchmark_candidate_exposure.py run
.venv/bin/python scripts/benchmark_candidate_exposure.py report
```

The freeze and detailed results remain under `benchmark/private.local`. The committed anonymized
report contains aggregates only. A run compares the unchanged top-10 policy with deep task-stream
retrieval, a globally capped pool, score-tail adaptation, observable progressive coverage, and an
offline oracle trigger. The oracle may use expected-source labels only to estimate an upper bound;
it is never eligible for production promotion.

Coverage is measured at three distinct boundaries: raw query streams, query streams after the
known `.bib` exclusion used when the structured bibliography provider is active, and the effective
pool exposed by the production pack. Final selection remains fixed. This prevents a provider handoff
or exclusion from being misreported as insufficient retrieval depth or composer regret.

Each case-policy cost measurement runs five times. Policy order rotates deterministically by case
and repetition so the baseline does not receive every cold-cache run. Coverage is counted once per
case; costs use all repetitions and report retrieved candidates, unique candidates, candidate spans,
candidate tokens processed, retrieval/fusion/composer latency, and total latency. Promotion requires
an exposure gain in at least two cases, no final-recall or hard-constraint regression, at most a
100% candidate-processing increase, and at most a 100% p95 latency increase. These thresholds were
declared before the authoritative run. A Pilot-only result remains exploratory even when it passes.

This milestone does not change the retrieval backend, composer, RRF setting, hard constraints, or
production defaults. Its purpose is to locate exposure losses and measure whether bounded expansion
can recover them at acceptable processing cost.

### BibTeX provider handoff benchmark

When candidate exposure shows that an RTFM `.bib` result is removed by structured-provider
ownership without an equivalent provider result, run the narrower handoff experiment:

```text
.venv/bin/python scripts/benchmark_bibtex_handoff.py freeze
.venv/bin/python scripts/benchmark_bibtex_handoff.py run
.venv/bin/python scripts/benchmark_bibtex_handoff.py report
```

The experiment fixes retrieval at the current top-10 policy and leaves fusion, RRF, hard limits,
and the production composer unchanged. It compares three behaviors: the current exclusion,
retaining the original RTFM chunk only when no structured replacement exists, and reconstructing
only missing provider-owned BibTeX entries.

Replacement equivalence is deterministic and checked in this order: citation key, normalized DOI,
then normalized title. Text similarity and expected-source labels are not runtime triggers. Private
telemetry records excluded and replacement candidate IDs, the ownership reason, replacement
provider, matching identities, duplication, added tokens, and final selection. The anonymized
report publishes only aggregates, including:

```text
bibliographic handoff recall =
  relevant excluded BibTeX entries with an equivalent replacement
  / relevant excluded BibTeX entries
```

Costs run five times with deterministic variant-order rotation. A variant may be reported as a
`correctness_fix_candidate` only when it improves final selection, introduces no duplicate
bibliographic identity or hard-constraint violation, increases candidate processing by at most 10%,
and increases local p95 latency by at most 20%. Production promotion remains false for Pilot v1:
the current benchmark contains only one observed relevant BibTeX handoff failure.

The completed Pilot v1 run did not promote either repair. Fallback restored exposure with
duplication and a 70.64% candidate-processing increase. Reconstruction restored exposure without
duplicate identities but increased candidate processing by 117.43%. Neither improved final
selection because the restored reference lost the provider-reference quota contest. Do not change
the quota or add another repair policy until the reopening criteria in `PILOT_V1_DECISION.md` are
met.

6. Run a one-case, one-repetition smoke test first. The harness prints the exact request count before
making model calls:

```text
.venv/bin/python scripts/benchmark_context_quality.py generate --stage pilot --limit-cases 1 --limit-repetitions 1 --confirm-paid-run
.venv/bin/python scripts/benchmark_context_quality.py judge --stage pilot --limit-cases 1 --limit-repetitions 1 --confirm-paid-run
.venv/bin/python scripts/benchmark_context_quality.py report --stage pilot --limit-cases 1 --limit-repetitions 1 --anonymized --output benchmark/anonymized_aggregates/pilot-smoke.json
```

This is exactly 4 generations followed by 8 blinded judgments. Inspect prompt privacy, structural
metrics, malformed judge responses, and the process table before expanding the run.

7. If the smoke run is acceptable, run the complete first repetition:

```text
.venv/bin/python scripts/benchmark_context_quality.py generate --stage pilot --limit-repetitions 1 --confirm-paid-run
.venv/bin/python scripts/benchmark_context_quality.py judge --stage pilot --limit-repetitions 1 --confirm-paid-run
.venv/bin/python scripts/benchmark_context_quality.py report --stage pilot --limit-repetitions 1 --anonymized
```

For the eight-case pilot, this is 32 generations followed by 64 blinded judgments. On the current
machine and models, the observed runs took roughly 8–12 minutes and 10–15 minutes respectively;
CLI and model load can change those times substantially. Inspect
`benchmark/anonymized_aggregates/pilot.json`. If the first repetition is acceptable, finish the
remaining two repetitions by omitting both limit options; exact-key artifacts resume automatically:

```text
.venv/bin/python scripts/benchmark_context_quality.py generate --stage pilot --confirm-paid-run
.venv/bin/python scripts/benchmark_context_quality.py judge --stage pilot --confirm-paid-run
.venv/bin/python scripts/benchmark_context_quality.py report --stage pilot --anonymized
```

The full pilot totals 96 generations and 192 judgments. Do not run two copies of a paid stage at
the same time. If interrupted, press Ctrl-C once, wait for the command to exit, and rerun the exact
same command; completed atomic artifacts resume. Each model invocation runs sequentially in its
own process group, which the harness reaps on success, timeout, error, or interruption.

### Smoke-test stop conditions

Do not expand a one-case smoke run when any of the following occurs:

- a condition's evidence packet omits a required idea, required citation, or protected literal;
- a CLI output exceeds the case-specific output ceiling;
- either configured CLI reports a different model identifier;
- the unresolved dual-judge rate is high enough to make the comparison ambiguous;
- any `python -m rtfm.cli worker restart-all` process appears.

The CLI adapters remove PATH entries that expose `rtfm`, remove `VIRTUAL_ENV`, isolate subprocess
groups, and make Codex ignore inherited user configuration and rules. This prevents a coding-agent
generator from calling the retrieval tool under evaluation and avoids the recursive lazy-worker
restart defect present in the locally inspected RTFM releases. Do not invoke the standalone `rtfm`
CLI during a benchmark run; preparation and retrieval use the RTFM Python library and direct SQLite
paths instead.

Before the full pilot, audit atomic evidence availability separately from graded-source recall. A
graded source span can be retrieved while the exact required anchor, citation, or protected literal
is absent from the selected excerpt. Treat such a candidate as an infeasible condition, not as a
pure generation-quality failure.

8. To reanalyze already completed artifacts after changing only report or metric code, use the
exact `source_code_revision` recorded by the original artifacts:

```text
.venv/bin/python scripts/benchmark_context_quality.py report --stage pilot --limit-repetitions 1 --anonymized --source-code-revision '<original-revision>' --output benchmark/anonymized_aggregates/pilot-reanalyzed.json
```

Keep `--limit-repetitions` identical to the completed run. This command loads retrieval,
generation, and judgment artifacts from that historical content-addressed revision, recomputes
metrics with the current analysis code, and records both revisions in the report. It makes no
generator or judge calls. Do not use this option to combine artifacts whose corpus, prompts,
models, cards, or retrieval policy changed; those conditions require fresh content-addressed
stages.

Use `agy models` to obtain an exact available model ID. The primary Gemini-family transport is
`agy_cli`; preflight checks the configured model against the live model list. CLI versions are not
part of the experiment contract.
Private prompts are sent as NDJSON over stdin using Antigravity's `stream-json` input/output mode;
they are never placed in the process argument list.

`claude_cli` is implemented for optional sensitivity runs. It does not satisfy an OpenAI-family
slot in the predeclared primary design; changing a primary generator or judge to Claude creates a
different study condition that must be reported explicitly.

The pilot is eight cases, four strategies, three repetitions, and one generator (96
generations and 192 dual-model judgments). Confirmation is twelve cases, four strategies,
three repetitions, and two generators (288 generations and 576 judgments). Artifacts are
reused only when their complete content-addressed key matches.

Deterministic scores are proxies and model judgments are not human ground truth. Reports must
limit conclusions to the configured corpus and model identifiers.
