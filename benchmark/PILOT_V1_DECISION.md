# Pilot v1 Retrieval and Composition Decision

**Status:** Closed as a negative production-change decision

**Scope:** Eight resolved Pilot v1 cases and 16 annotated expected sources

**Decision date:** 2026-08-30

## Decision

Keep the production retrieval, provider ownership rule, reference quota, fusion policy, and
composer unchanged. Do not promote deeper candidate exposure, broad BibTeX fallback, broad BibTeX
reconstruction, or a budget-aware composer from the current evidence.

The observed bibliographic loss was traced to an interaction between structured-provider ownership
and `provider_reference_quota`. Broad fallback and reconstruction restored candidate exposure but
did not improve final selection and increased processing cost. Pilot v1 contains one relevant
instance of this failure, which is insufficient to redesign reference allocation.

## Evidence Chain

The three frozen diagnostic stages support the following decomposition:

```text
raw retrieval                 16 / 16
candidate exposure            15 / 16
feasible exposed selection    15 / 15
```

The Candidate Exposure Benchmark found:

- Current raw query recall was 16/16; recall after the `.bib` ownership exclusion was 15/16.
- Deep task retrieval, global cap, progressive coverage, and the offline oracle did not improve
  effective exposure or final selection.
- The score-tail policy reduced final selection to 14/16 while increasing candidate processing by
  136.57% and local p95 latency by 133.59%.
- Selection regret for the current policy was zero: every annotated source exposed to the composer
  was selected.

The BibTeX Handoff Benchmark found:

| Variant | Exposed | Selected | Handoff recall | Candidate increase | Duplicate identities |
|---|---:|---:|---:|---:|---:|
| Current | 15/16 | 15/16 | 0% | baseline | 0 |
| Fallback | 16/16 | 15/16 | 100% | 70.64% | 28 |
| Reconstruction | 16/16 | 15/16 | 100% | 117.43% | 0 |

Both repair variants restored the missing bibliographic candidate, but it was rejected by the
existing provider-reference quota. Neither variant improved final recall. These results do not show
that the quota is wrong; they show only that one relevant reference lost a quota allocation contest.

## Hypotheses Closed for Pilot v1

- **Insufficient backend retrieval:** not supported; the annotated sources were present in raw
  retrieval.
- **Insufficient task-stream depth:** refuted for the observed loss; deeper retrieval could not
  reverse a later ownership exclusion.
- **RRF as the primary cause:** not supported by the frozen pilot.
- **Composer drops exposed annotated sources:** not observed under the current policy.
- **Broad `.bib` fallback:** rejected because it adds duplication and processing without final gain.
- **Broad `.bib` reconstruction:** rejected because it expands the provider candidate pool without
  final gain.
- **Reference-quota adjustment:** not tested and not justified by one relevant failure.

These are Pilot v1 conclusions, not universal claims about other manuscripts or task distributions.

## Assets Retained

The following remain supported diagnostic infrastructure:

- provider-ownership audit hooks;
- BibTeX citation-key, DOI, normalized-title, source-path, and line provenance;
- candidate-stage and handoff telemetry;
- deterministic freezes and anonymized aggregate reports;
- regression tests for Current, Fallback, and Reconstruction behavior.

Fallback and Reconstruction remain benchmark variants only. They are not production defaults.

## Reopening Criteria

Do not reopen bibliographic prioritization or quota allocation because another isolated case is
found. Reopen only after a frozen benchmark demonstrates a repeated pattern across multiple cases
or projects. At minimum, the evidence must report:

```text
bibliographic_raw_recall
bibliographic_exposed_recall
bibliographic_selected_recall
bibliographic_handoff_recall
quota_rejection_rate
relevant_reference_quota_loss
tokens_per_selected_reference
```

The report must distinguish:

```text
reference absent from retrieval
reference removed by provider ownership
reference restored but rejected by quota
reference exposed and selected
```

Any future policy must improve bibliographic selected recall in more than one case, preserve overall
final recall and hard constraints, and satisfy cost thresholds declared before its evaluation.

## Benchmark v2 Scope

Benchmark v2 should expand evidence collection without changing the production pipeline. It should
include adversarial but realistic writing tasks with:

- related-work sections containing many competing references;
- comparisons requiring two to five specific papers;
- claims dependent on one identifiable reference;
- semantically similar BibTeX entries;
- citations located outside the target section;
- relevant references at varied retrieval ranks;
- competition between references and methodology/results context;
- small and large context budgets.

Only if v2 demonstrates repeated relevant-reference quota loss should the project evaluate
bibliographic quota allocation. That later experiment should compare fixed-count quota with a
bounded token allocation using observable features such as task relevance, explicit citation keys,
claim linkage, section role, uniqueness, and token cost. It is not part of Pilot v1 closure.

## Frozen Artifacts

- `benchmark/anonymized_aggregates/pilot-retrieval.json`
- `benchmark/anonymized_aggregates/pilot-v1-exposure.json`
- `benchmark/anonymized_aggregates/pilot-v1-bibtex-handoff.json`

Private freezes and case-level results remain under `benchmark/private.local` and must not be
committed.
