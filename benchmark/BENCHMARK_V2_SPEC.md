# Benchmark v2 Specification: Adversarial Phenomena and Robustness Gates

**Status:** Specification Draft for Future Corpus Expansion  
**Depends on:** `PILOT_V1_DECISION.md`, `ANNOTATION_PROTOCOL.md`

## 1. Overview and Objectives

Benchmark v2 expands the empirical evaluation framework beyond Pilot v1's 8 clean manuscript cases to include **adversarial phenomena**, stress-testing the pipeline across edge cases in retrieval, candidate exposure, bibliographic ownership, and budget quota enforcement without relaxing production safety guarantees.

As established in `PILOT_V1_DECISION.md`, no algorithmic changes to retrieval fusion, composer selection, or provider quotas may be promoted without reproducible evidence of repeated patterns across multiple cases. Benchmark v2 provides the structured protocol to evaluate candidate improvements under stress.

---

## 2. Adversarial Phenomena Taxonomy

Benchmark v2 introduces 6 primary categories of adversarial phenomena designed to probe potential pipeline failure modes:

### Category A: Competing and Distracting Terminology (ADV-TERM)
- **Description:** Target sections and dependencies contain overlapping terminology with divergent semantic meanings in different chapters (polysemy, homonymy, domain collision).
- **Target Failure Mode:** Keyword-expansion pollution and false-positive retrieval crowding out essential evidence.
- **Evaluation Criteria:** Precision of essential vs supporting spans; proportion of irrelevant spans selected under strict token limits.

### Category B: Bibliographic Ownership & Disambiguation Clashes (ADV-BIB)
- **Description:** LaTeX manuscripts with multiple `.bib` files containing:
  - Disjoint citation keys pointing to the same paper (duplicate entries with different aliases);
  - Shared citation keys with conflicting DOI/title metadata;
  - Passive `.bib` references with no matching direct citation key in target text.
- **Target Failure Mode:** Incomplete structured replacement detection, erroneous exclusion without replacement, duplicate identity leakage.
- **Evaluation Criteria:** Zero duplicate identity count, 100% accurate passive ownership audit provenance tracking, strict adherence to `provider_reference_quota`.

### Category C: Ambiguous and Multi-Target Section Boundaries (ADV-BOUND)
- **Description:** Large single-file manuscripts with deep sub-subsections, unnumbered environments, appendices, and ambiguous structural scopes.
- **Target Failure Mode:** AST snapping over-expansion or failure to isolate local vs dependency context.
- **Evaluation Criteria:** AST snap boundaries match semantic scope; target text token budgets do not bleed into supporting roles.

### Category D: Dense LaTeX Syntax and Math Environments (ADV-LATEX)
- **Description:** Heavy mathematical blocks (`align`, `equation*`, `tikz`, custom macros, nested tables) spanning across line boundaries.
- **Target Failure Mode:** Truncation of mathematical expressions, broken delimiters, syntax corruption.
- **Evaluation Criteria:** Zero LaTeX syntax corruption; 100% preservation of math environments and label/ref anchors.

### Category E: Sparse Long-Range Evidence Chains (ADV-CHAIN)
- **Description:** Tasks requiring 2-hop structural dependencies (e.g. section 4 relies on a theorem in section 2 that relies on definitions in section 1).
- **Target Failure Mode:** Dependency starvation where 1-hop reference graph misses transitive foundations.
- **Evaluation Criteria:** Atomic coverage score on multi-atom tasks; explicit tracking of unverified dependencies in diagnostic telemetry.

### Category F: Severe Budget Constriction (ADV-BUDGET)
- **Description:** Token budget allocated is tight relative to baseline target text + essential dependencies (e.g. 500 tokens for a complex 3-dependency section).
- **Target Failure Mode:** Crash on strict baseline overflow, non-deterministic span dropping, role quota starvation.
- **Evaluation Criteria:** Deterministic priority selection: `target_text > local_context > dependency > reference`; graceful degraded status signaling.

---

## 3. Benchmark Protocol and Gating Criteria

Any future pull request proposing changes to retrieval streams, RRF fusion, candidate exposure, or composer budgets must run through Benchmark v2 and satisfy all of the following non-negotiable gates:

1. **Non-Regression on Frozen Pilot v1:**
   - Evaluated using `scripts/benchmark_regression.py`.
   - Raw query recall, post-exclusion recall, and selection regret must not degrade.
2. **Deterministic Funnel Cardinality:**
   - Every candidate must trace monotonically: `retrieved >= normalized >= deduplicated >= exposed >= eligible >= selected`.
   - Total rejections must equal `exposed - eligible + (eligible - selected)`.
3. **Bibliographic Ownership Integrity:**
   - `duplicate_identity_count == 0`
   - `hard_constraint_violations == 0`
   - Every excluded `.bib` candidate must have an explicit `OwnershipAuditRecord` with verified replacement provenance.
4. **Cost-to-Gain Bound:**
   - Candidate processing token increase ratio must not exceed +10% unless statistically significant selection recall gain ($\Delta > 0$) is proven across $\ge 3$ distinct projects.
   - P95 total latency increase must not exceed +20%.

---

## 4. Anonymized Aggregate Artifacts

All benchmark v2 runs must produce anonymized aggregate artifacts committed under `benchmark/anonymized_aggregates/` with:
- SHA-256 freeze fingerprint of input cases;
- Anonymized token counts, timing percentiles (p50, p95), and funnel metrics;
- Zero inclusion of raw proprietary prose or full manuscript text.
