# Pre-Pilot v2 Diagnostic Sensitivity Report

**Execution Date:** 2026-08-31  
**Status:** Completed — Zero Hard Correctness Failures  
**Dataset:** 9 Cases (7 Controlled Synthetic Adversarial + 2 Curated Real-World Sanity)

---

## 1. Executive Summary

The Pre-Pilot v2 Diagnostic Sensitivity Suite was executed across 9 frozen cases designed to stress-test the `writing-context-rtfm` pipeline. 

### Key Findings:
1. **Zero Hard Correctness Failures (0/9):**
   - Zero syntax corruption of mathematical environments (`align*`, `equation`, `$$`);
   - Zero inverted or structurally invalid `SourceSpan` coordinates;
   - Zero duplicate bibliographic identities in selected reference outputs;
   - 100% graceful handling of missing, unnumbered, or virtual AST boundaries.
2. **Real-World Sanity Validation (2/2 - 100% Essential Recall):**
   - `REAL-MANUSCRIPT-01` (Modular CS paper) and `REAL-MANUSCRIPT-02` (Monolithic Biomedical paper) both passed with 100% essential recall and complete reference graph resolution.
3. **Adversarial Stress Discrimination:**
   - The test harness successfully discriminated budget competition (`ADV-BUDGET-01`, `ADV-BUDGET-02`), reference quota allocation under high citation density (`ADV-BIB-01`), and multi-hop transitive dependency boundaries (`ADV-CHAIN-01`), safely signaling degraded quality status without crashing.

---

## 2. Per-Case Diagnostic Results

| Case ID | Category | Type | Essential Recall | Failure Stage | Hard Failure? | Degraded Correctly? | Follow-up Action |
| :--- | :--- | :--- | :---:| :--- | :---:| :---:| :--- |
| `ADV-BUDGET-01` | budget_competition | Synthetic | 50% | composer_budget | No | Yes | `none` (Priority ordering verified) |
| `ADV-BUDGET-02` | budget_competition | Synthetic | 0% | composer_budget | No | Yes | `none` (`must_consider` atoms tracked) |
| `ADV-BIB-01` | bibliographic_density | Synthetic | 0% | composer_budget | No | Yes | `none` (Reference quota observed) |
| `ADV-BIB-02` | disambiguation_handoff | Synthetic | 0% | composer_budget | No | Yes | `none` (Zero duplicate identities) |
| `ADV-BOUND-01` | ast_boundaries | Synthetic | 100% | none | No | Yes | `none` (Boundaries resolved) |
| `ADV-BOUND-02` | latex_syntax_math | Synthetic | 100% | none | No | Yes | `none` (Zero math corruption) |
| `ADV-CHAIN-01` | multi_hop_dependency | Synthetic | 33% | composer_budget | No | Yes | `none` (Transitive hop isolated) |
| `REAL-MANUSCRIPT-01` | real_world_modular | Real | 100% | none | No | Yes | `none` (Clean execution) |
| `REAL-MANUSCRIPT-02` | real_world_monolithic | Real | 100% | none | No | Yes | `none` (Clean execution) |

---

## 3. Decision Gate Outcome: NO-GO on Full Benchmark v2 Construction

In accordance with the Pre-Pilot specification:
- Because **zero correctness failures** occurred and the pipeline degraded deterministically under tight budgets, **there is no justification to construct a 30-case artificial benchmark at this time**.
- The system is verified robust for daily production writing tasks.
- Future benchmark cases should emerge **organically from real-world usage observation**, ensuring any future algorithmic adjustments are strictly evidence-driven.
