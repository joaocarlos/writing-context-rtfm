# Benchmark v2 Specification & Pre-Pilot Diagnostic Protocol

**Status:** Pre-Pilot Diagnostic Sensitivity Specification  
**Depends on:** `PILOT_V1_DECISION.md`, `ANNOTATION_PROTOCOL.md`

## 1. Overview and Staged Strategy

Rather than immediately constructing an expansive 24–30 case research benchmark, Benchmark v2 is structured in two distinct phases:

```text
Phase 1: Pre-Pilot Diagnostic Sensitivity Test (9 Cases)
├── 7 Controlled Synthetic Adversarial Cases
└── 2 Curated Real-World Sanity Cases
         ↓
  Decision Gate: Correctness vs Quality Evaluation
         ↓
Phase 2: Full Benchmark v2 (24–30+ Cases) — Triggered ONLY if systematic failures are discovered
```

The objective of Phase 1 is **diagnostic sensitivity**: validating whether our failure taxonomy, Essential/Supporting evidence annotations, and candidate lifecycle metrics actually discriminate real-world pipeline problems.

---

## 2. Pre-Pilot Case Matrix (9 Cases)

### A. Controlled Synthetic Adversarial Cases (7 Cases)
Synthetic cases isolate exact pipeline mechanisms with zero confounding variables:

| Case ID | Category | Focus Mechanism | Description & Strain | Expected Failure Mode to Test |
| :--- | :--- | :--- | :--- | :--- |
| `ADV-BUDGET-01` | Budget Competition | Priority Ordering | Target text (1,000 tokens) + 2 essential dependencies (300 tokens each) under a 1,200 token budget. | Tests if `target_text` is prioritized over `dependency` without dropping entire section, and verifies `quality.status = "degraded"`. |
| `ADV-BUDGET-02` | Budget Competition | `must_consider` Atoms | 4 explicit evidence atoms in `must_consider` with a strict budget of 2,000 tokens. | Tests whether elastic composer reserves space for all 4 atoms or leaves uncovered items in `quality.atomic_coverage.uncovered`. |
| `ADV-BIB-01` | Bibliographic Density | Quota Allocation | Related Work section with 12 essential citations under standard `provider_reference_quota`. | Tests if reference quota rejects essential literature and measures `REJECT_PROVIDER_REFERENCE_QUOTA` frequency. |
| `ADV-BIB-02` | Disambiguation Handoff | Duplicate Keys & DOI | Multiple `.bib` files with conflicting citation keys (`vaswani2017` vs `vaswani_2017_attention`) sharing the same DOI. | Tests deduplication in `audit_passive_bibtex_ownership`, asserting `duplicate_identity_count == 0` and `replacement_found == True`. |
| `ADV-BOUND-01` | AST Boundaries | Monolithic Subsections | 2,500-line single-file manuscript with unnumbered environments (`\begin{definition}`, unnumbered subsubsections). | Tests virtual section resolution and AST snapping boundaries to prevent prose bleeding from adjacent sections. |
| `ADV-BOUND-02` | LaTeX Math Syntax | Complex Environments | 40-line `\begin{align*}` block with custom macros and nested `tikz` diagrams across line boundaries. | Tests syntax preservation: **zero math corruption**, zero delimiter stripping, 100% immutable token retention. |
| `ADV-CHAIN-01` | Multi-Hop Dependency | Transitive Evidence | Section 4 references Theorem 3.1 (Sec 3), which depends directly on Definition 1.2 (Sec 1). | Tests whether 1-hop reference graph miss is recovered via search/provider or causes dependency starvation. |

### B. Curated Real-World Sanity Cases (2 Cases)
Real-world cases test unscripted document complexities:
- `REAL-MANUSCRIPT-01`: Modular multi-file CS/Engineering paper with modular `\input{...}` chapters, algorithms, and real `.bib` library.
- `REAL-MANUSCRIPT-02`: Monolithic interdisciplinary paper with dense paragraph citations and extensive footnotes.

---

## 3. Retrieval Pathway Annotation Taxonomy

For all dependency and multi-hop cases (e.g. `ADV-CHAIN-01`), expected sources must include an explicit `expected_retrieval_path` field:

```yaml
expected_retrieval_path: semantic | lexical | reference_graph | provider | any
```

This annotation distinguishes:
1. **Benign Redundancy:** The evidence was omitted by the 1-hop reference graph but successfully captured by semantic/lexical search (no pipeline fix needed).
2. **True Dependency Starvation:** The evidence was not reachable by search and requires multi-hop AST traversal (justifying algorithmic refinement).

---

## 4. Decision Gate Criteria: Correctness vs Quality

Pre-pilot outcomes are evaluated under a strict dichotomy:

### 1. Correctness Failures (Hard Failures $\rightarrow$ Immediate Action)
A single reproducible instance triggers an immediate targeted fix:
- Truncation or syntax corruption of mathematical environments (`align*`, `equation`, `$$`);
- Broken citation keys or attribution of incorrect bibliographic identity;
- Violation of explicit `must_consider` preservation without degraded status signaling;
- Generation of structurally invalid or corrupted `SourceSpan` character/line coordinates.

### 2. Quality / Efficiency Failures (Soft Failures $\rightarrow$ Multi-Case Pattern Required)
Requires reproducible evidence across multiple cases before opening policy adjustments:
- Suboptimal ranking order within the composer;
- Marginal reference quota loss where secondary references are omitted;
- Candidate processing overhead under loose token budgets.

---

## 5. Diagnostic Reporting Schema

Every Pre-Pilot evaluation must produce a granular per-case diagnostic breakdown table:

| Case ID | Target Role | Essential Recall | Failure Stage | Hard Failure? | Degraded Correctly? | Follow-up Action |
| :--- | :--- | :---:| :--- | :---:| :---:| :--- |
| `ADV-BUDGET-01` | `target_text` | 100% | composer | No | Yes | none |
| `ADV-BIB-01` | `reference` | 60% | quota | No | Yes | evaluate quota elasticity |
| `ADV-BOUND-02` | `target_text` | 100% | none | No | Yes | none |
| `ADV-CHAIN-01` | `dependency` | 50% | retrieval | No | Yes | check `expected_retrieval_path` |
| `REAL-01` | `all` | 100% | none | No | Yes | none |

---

## 6. Strategic Posture

If the Pre-Pilot reveals:
- **No correctness failures and healthy degraded signaling:** Do not build an artificial 30-case benchmark. Deploy the extension for daily authoring and allow adversarial cases to emerge organically from real-world usage.
- **Clear, localized failure patterns (e.g. math truncation or reference quota strangulation):** Formulate a targeted, hypothesis-driven fix and validate it against the specific pre-pilot fixtures.
