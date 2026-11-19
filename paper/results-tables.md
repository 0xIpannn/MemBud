# Bab 4 — exact results tables (pinned evaluated revision)

**Purpose.** This file is the authoritative table source for Chapter 4. Each table is numbered as it appears in the assembled chapter, and each cell is traceable to a committed artifact through the JSON pointer given under the table. The seven section drafts in `paper/sections/4-*.md` cite these table numbers and do not duplicate the tables.

**Pinned basis.** Rules-only run and fairness audit: the repository's evaluated state as recorded in the artifacts committed at `1ce84ce600ff20c90dc55539c6fa59eda22d40d2` (short `1ce84ce`). Held-out LLM/semantic run: the code revision recorded inside its own artifacts, `41905266e29a4ae699655d8b66e92474a4ee8921` (short `4190526`). The working tree was later advanced by a concurrent session; at the time of writing the working-tree copies of the artifacts below were byte-identical to `git show 1ce84ce:<path>` (SHA-256 compared), so the paths below point at the evaluated evidence. Artifacts produced after the pinned basis are outside the evaluated scope and are not used anywhere in this chapter.

| Artifact | SHA-256 |
|---|---|
| `data/manifests/evaluation-v2.json` | `e1adf173a3f84d4f8d4ca910bdcabb2e6dd69a0f64be4509395090f5507d527a` |
| `data/cases/evaluation-v2.jsonl` | `40558746e915c6f5296235109900c050da61f87fe4f4fe320bf0290af9aa432d` |
| `data/labels/evaluation-v2.jsonl` | `9280d3e8e2aa7a61df31bed689647f768a98361bb7509e7f8d172bb376989a14` |
| `data/results/evaluation-v2/metrics.json` | `1239e13deb499c9d3c536ed92e9e7175aaba5a63ccc11443ae22dbe7d5c3c680` |
| `data/results/evaluation-v2/run-config.json` | `c4c5aec820b63b2d04da0b8e80c9d6c68128eed924e1b8f44ca00f21d645d759` |
| `data/results/fairness-audit-v2/audit.json` | `a3b4b096275a901e22c265635ad9a8656ebd82a666ba4b3edc0ed432219d2515` |
| `data/results/llm-heldout-v2/summary.json` | `9a8ee5eaa7f25f5c042faa914d24ab57274f5c8401d75a36eb9a924331060f66` |
| `data/results/llm-heldout-v2/metrics.json` | `c4490aa24c936a3a754b9f3b47c71f38898692c92ce693092c7e993f23f5290a` |

**Conventions used in every table.**

1. *Case* is the unit of analysis: one transition case, paired across conditions. Claims and records are not independent samples.
2. `ACCEPT`, `HOLD`, `INVALID_INPUT`, `ERROR` are defined as in Table 3.6 and Section 3.2.6. `INVALID_INPUT` is a schema-construction failure recorded before verification; it is not a verifier decision and never writes memory. `ERROR` is an execution/verifier failure; it is non-accepting.
3. `APPLIED` is the unguarded reference condition's outcome: the schema-valid candidate is written with no verifier consultation. It is not a verifier decision. The fairness audit records the same behaviour as `ACCEPT` so that all four strategies share one vocabulary; Table 4.9 flags this.
4. **"Faulty" is always defined where it is used.** Three denominators exist and are never merged: the *HOLD-only* view (12 pooled expected-`HOLD` cases; 6 in the held-out LLM split), the *combined safe-rejection* view (22 pooled = 12 expected `HOLD` + 10 expected `INVALID_INPUT`), and *candidate-update errors* (claim-slot and record-slot instrument-validation counts, not cases decided by a verifier).
5. **As-completed** excludes error rows from numerator and denominator. **Fail-closed intention-to-treat (ITT)** retains every reference case and treats an error as a non-accepting outcome, with error-holds counted separately from explicit `HOLD` detections. The two forms are reported side by side; they are never merged.
6. "Labelled" means the single-reviewer exploratory label in `data/labels/evaluation-v2.jsonl`. The labels are **not** an adjudicated or independently reviewed gold standard: second human review is pending and no agreement statistic exists.
7. Rates are shown as fractions with their numerators and denominators; decimal values are the artifact's values rounded to four decimal places where a long float would not be readable, and the pointer gives the exact value. Values marked *derived* are arithmetic on established cells with the formula stated; they are not independent artifact fields.
8. Timing values are instrumentation on tiny fixtures and are timer-noise-dominated; they are never interpreted as a performance result (Section 4.5).

---

## 4.1 Instrument and candidate-update errors

*Table 4.1. Composition of the frozen evaluation instrument (`membud-evaluation-v2`) and its label census.*

| Property | Value |
|---|---|
| Manifest id / schema / status | `membud-evaluation-v2` / `2` / `frozen_exploratory_single_reviewer_second_review_pending` |
| Cases | 36 |
| Splits | `dev` 18 (`DEV-01`–`DEV-18`); `heldout` 18 (`HELD-01`–`HELD-18`) |
| Expected decisions (pooled) | `ACCEPT` 14; `HOLD` 12; `INVALID_INPUT` 10 |
| Expected decisions per split | each split: 7 `ACCEPT`, 6 `HOLD`, 5 `INVALID_INPUT` |
| Categories | 18 categories × 2 cases: 13 schema-valid-update/fault categories and 5 invalid-input families |
| Invalid-input family cases | `DEV-14`–`DEV-18`, `HELD-14`–`HELD-18` (10 cases) |
| Case schema version | `2` on 36/36 cases (the labels file carries no `schema_version` field) |
| Candidate generation | hand-authored fixed candidates, reused unchanged across conditions |
| Input/label separation | `true`: `required_claims` are verifier inputs; expected decisions, category and persistence labels live in the separate labels file |
| Labels | single reviewer (project owner), `reviewer_count` 1, `second_independent_reviewer` false, `second_review_status` `pending_human_review`; every label carries `label_status = single_reviewer_second_review_pending` |
| Authoring order | the 36 cases were authored together and frozen before the rules-only run; no tuning on held-out outcomes |
| Provenance | synthetic Indonesian-themed fictional cases; no HaluMem or other licensed data |

*Source:* `data/manifests/evaluation-v2.json` → `$.manifest_id`, `$.schema_version`, `$.status`, `$.case_count`, `$.split_counts`, `$.expected_decision_counts`, `$.expected_decision_counts_by_split`, `$.review.*`, `$.candidate_generation`, `$.input_labels_separated`, `$.notes[0]`, `$.notes[4]`; `data/cases/evaluation-v2.jsonl` (36 lines, `schema_version` = 2 on every line); `data/labels/evaluation-v2.jsonl` (36 lines, `label_status` identical on every line, expected-decision counts matching the manifest). Both case and label files match the SHA-256 pins recorded in the manifest.

*Table 4.2. Candidate-update error rates (instrument validation; pooled over all 36 cases).*

| Candidate-error family | Unit | Numerator | Denominator | Rate | Denominator meaning |
|---|---|---:|---:|---:|---|
| Required-claim omission | claim slot | 12 | 68 | 12/68 = 0.1765 | required supported claims expected in the candidate |
| Unsupported addition | claim slot | 8 | 64 | 8/64 = 0.1250 | material candidate claims (all candidate claims; the labels carry no separate materiality flag) |
| Prior-record corruption | record slot | 4 | 40 | 4/40 = 0.1000 | valid prior records annotated as expected to persist |
| Case-level error | case | 12 | 36 | 12/36 = 0.3333 | cases with at least one relevant candidate error |

*Source:* `data/results/evaluation-v2/metrics.json` → `$.candidate_error_rates.{omission,unsupported_addition,prior_record_corruption,case_error}` (each has `numerator`, `denominator`, `rate`). The denominators are the same set fixed in Table 3.7 and Section 3.2.6.

*Note.* These four rates quantify whether the frozen proposals encode the intended faults; they are computed once from the cases and labels, independently of any condition, and are therefore instrument-validation measures, not verifier scores. They are not a count of "faulty cases" in the sense of Table 4.4 or Table 4.6. The labels additionally record 28 fault-type assignments across the 22 expected-faulty cases (omission 8, undeclared state change 6, corruption 4, unauthorized deletion 4, unsupported addition 2, unsupported provenance 2, unresolved contradiction 2); the 14 expected-valid labels carry no fault type. The counts in this table are *errors in the candidate proposals*, whereas the fault-type assignments describe the designed fault that the case conveys; a case may carry more than one fault type.

---

## 4.2 RQ1 — guarded versus unguarded (rules-only run)

*Table 4.3. Rules-only decision counts by condition and split (36 cases).*

| Condition | Split | `ACCEPT` | `HOLD` | `INVALID_INPUT` | `APPLIED` | `ERROR` | Total |
|---|---|---:|---:|---:|---:|---:|---:|
| graph | pooled | 14 | 12 | 10 | — | 0 | 36 |
| graph | dev | 7 | 6 | 5 | — | 0 | 18 |
| graph | heldout | 7 | 6 | 5 | — | 0 | 18 |
| text | pooled | 16 | 10 | 10 | — | 0 | 36 |
| text | dev | 8 | 5 | 5 | — | 0 | 18 |
| text | heldout | 8 | 5 | 5 | — | 0 | 18 |
| unguarded | pooled | — | — | 10 | 26 | 0 | 36 |
| unguarded | dev | — | — | 5 | 13 | 0 | 18 |
| unguarded | heldout | — | — | 5 | 13 | 0 | 18 |

*Source:* `data/results/evaluation-v2/metrics.json` → `$.condition_metrics.graph.decision_counts`, `$.condition_metrics.text.decision_counts`, `$.condition_metrics.unguarded.decision_counts`, `$.condition_metrics.graph.splits.{dev,heldout}.decision_counts`, `$.condition_metrics.text.splits.{dev,heldout}.decision_counts`, `$.condition_metrics.unguarded.splits.{dev,heldout}.decision_counts`; `$.condition_metrics.*.verifier_errors` and `$.condition_metrics.unguarded.execution_errors` (all 0).

*Note.* The unguarded reference has no `ACCEPT`/`HOLD` outcomes: it applies every schema-valid candidate (`APPLIED`) and records schema-invalid rows as `INVALID_INPUT`. In the fairness audit the same 26 applications are recorded as `ACCEPT` so that all four strategies share one vocabulary (Table 4.9, footnote). Neither reading is a verifier decision.

*Table 4.4. HOLD-only verifier outcomes, as-completed form (expected-`HOLD` cases = 12 pooled, 6 per split; expected-valid cases = 14 pooled, 7 per split).*

| Condition | Split | Expected-HOLD cases | Faulty rows completed | Flagged `HOLD` | Detection (recall) | False accepts | False-accept rate | Precision* |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| graph | pooled | 12 | 12 | 12 | 12/12 = 1.0000 | 0 | 0/12 = 0.0000 | 12/12 = 1.0000 |
| graph | dev | 6 | 6 | 6 | 6/6 = 1.0000 | 0 | 0/6 = 0.0000 | 6/6 = 1.0000 |
| graph | heldout | 6 | 6 | 6 | 6/6 = 1.0000 | 0 | 0/6 = 0.0000 | 6/6 = 1.0000 |
| text | pooled | 12 | 12 | 10 | 10/12 = 0.8333 | 2 | 2/12 = 0.1667 | 10/10 = 1.0000 |
| text | dev | 6 | 6 | 5 | 5/6 = 0.8333 | 1 | 1/6 = 0.1667 | 5/5 = 1.0000 |
| text | heldout | 6 | 6 | 5 | 5/6 = 0.8333 | 1 | 1/6 = 0.1667 | 5/5 = 1.0000 |

| Condition | Split | Expected-valid cases | Valid rows completed | Accepted | Valid acceptance | False holds | False-hold rate |
|---|---|---:|---:|---:|---:|---:|---:|
| graph | pooled | 14 | 14 | 14 | 14/14 = 1.0000 | 0 | 0/14 = 0.0000 |
| graph | dev | 7 | 7 | 7 | 7/7 = 1.0000 | 0 | 0/7 = 0.0000 |
| graph | heldout | 7 | 7 | 7 | 7/7 = 1.0000 | 0 | 0/7 = 0.0000 |
| text | pooled | 14 | 14 | 14 | 14/14 = 1.0000 | 0 | 0/14 = 0.0000 |
| text | dev | 7 | 7 | 7 | 7/7 = 1.0000 | 0 | 0/7 = 0.0000 |
| text | heldout | 7 | 7 | 7 | 7/7 = 1.0000 | 0 | 0/7 = 0.0000 |

*Source:* `data/results/evaluation-v2/metrics.json` → `$.condition_metrics.graph.as_completed` and `$.condition_metrics.text.as_completed` (pooled); the same block under `$.condition_metrics.{graph,text}.splits.dev` and `.splits.heldout`.

*\*Precision is derived:* TP ÷ (TP + FP) from the `flagged` (TP) and expected-valid `held` (FP) cells; the artifact does not record a separate HOLD-only precision field, so it is shown as arithmetic, not as a measured artifact value. FP = 0 in this run, so precision is 1.0000 by construction of the observed counts.

*Table 4.5. HOLD-only verifier outcomes, fail-closed ITT form (identical rows to Table 4.4 here because no error rows occurred).*

| Condition | Split | Expected-HOLD cases | Held with decision | Error holds | Accepted | Detection (artifact `detection_rate`) | False-accept rate | Not-accepted rate |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| graph | pooled | 12 | 12 | 0 | 0 | 12/12 = 1.0000 | 0/12 = 0.0000 | 12/12 = 1.0000 |
| graph | dev | 6 | 6 | 0 | 0 | 6/6 = 1.0000 | 0/6 = 0.0000 | 6/6 = 1.0000 |
| graph | heldout | 6 | 6 | 0 | 0 | 6/6 = 1.0000 | 0/6 = 0.0000 | 6/6 = 1.0000 |
| text | pooled | 12 | 10 | 0 | 2 | 10/12 = 0.8333 | 2/12 = 0.1667 | 10/12 = 0.8333 |
| text | dev | 6 | 5 | 0 | 1 | 5/6 = 0.8333 | 1/6 = 0.1667 | 5/6 = 0.8333 |
| text | heldout | 6 | 5 | 0 | 1 | 5/6 = 0.8333 | 1/6 = 0.1667 | 5/6 = 0.8333 |

| Condition | Split | Expected-valid cases | Accepted | Error holds | Not accepted | Valid acceptance | False-hold rate |
|---|---|---:|---:|---:|---:|---:|---:|
| graph | pooled | 14 | 14 | 0 | 0 | 14/14 = 1.0000 | 0/14 = 0.0000 |
| graph | dev | 7 | 7 | 0 | 0 | 7/7 = 1.0000 | 0/7 = 0.0000 |
| graph | heldout | 7 | 7 | 0 | 0 | 7/7 = 1.0000 | 0/7 = 0.0000 |
| text | pooled | 14 | 14 | 0 | 0 | 14/14 = 1.0000 | 0/14 = 0.0000 |
| text | dev | 7 | 7 | 0 | 0 | 7/7 = 1.0000 | 0/7 = 0.0000 |
| text | heldout | 7 | 7 | 0 | 0 | 7/7 = 1.0000 | 0/7 = 0.0000 |

*Source:* `data/results/evaluation-v2/metrics.json` → `$.condition_metrics.graph.fail_closed_itt` and `$.condition_metrics.text.fail_closed_itt` (pooled); the same block under each condition's `splits.dev` / `splits.heldout`. The artifact's `detection_rate` in this block counts `held_with_decision` in the numerator; error-holds are reported separately and are zero in this run.

*Note.* The as-completed and fail-closed ITT forms are reported separately for protocol completeness, but this run contains **zero** verifier errors and **zero** error-holds, so the two views coincide numerically. The ITT framing matters for the pipeline claim (an unverifiable update is not committed), not for the arithmetic of this particular run; it should not be described as though errors were observed.

*Table 4.6. Combined safe-rejection view (artifact `faulty_detection`; expected-faulty cases = 12 expected `HOLD` + 10 expected `INVALID_INPUT` = 22 pooled, 11 per split).*

| Condition | Split | Expected-faulty cases | True positives | False positives | False negatives | Precision | Recall | False-accept rate | Error rows |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| graph | pooled | 22 | 22 | 0 | 0 | 22/22 = 1.0000 | 22/22 = 1.0000 | 0/22 = 0.0000 | 0 |
| graph | dev | 11 | 11 | 0 | 0 | 11/11 = 1.0000 | 11/11 = 1.0000 | 0/11 = 0.0000 | 0 |
| graph | heldout | 11 | 11 | 0 | 0 | 11/11 = 1.0000 | 11/11 = 1.0000 | 0/11 = 0.0000 | 0 |
| text | pooled | 22 | 20 | 0 | 2 | 20/20 = 1.0000 | 20/22 = 0.9091 | 2/22 = 0.0909 | 0 |
| text | dev | 11 | 10 | 0 | 1 | 10/10 = 1.0000 | 10/11 = 0.9091 | 1/11 = 0.0909 | 0 |
| text | heldout | 11 | 10 | 0 | 1 | 10/10 = 1.0000 | 10/11 = 0.9091 | 1/11 = 0.0909 | 0 |

*Source:* `data/results/evaluation-v2/metrics.json` → `$.condition_metrics.{graph,text}.faulty_detection` and the same block under each condition's `splits.{dev,heldout}`. The false-negative cases are `DEV-13` and `HELD-13` (Section 4.3).

*Note.* This view is the artifact's `faulty_detection` block, whose note defines faulty as `expected_decision` equal to `HOLD` or `INVALID_INPUT`. It is a reporting convention for the combined decision stream: an expected-invalid case recorded as `INVALID_INPUT` counts as a correct safe rejection, but it is a schema-construction outcome, not a verifier detection. Section 3.2.6 therefore treats this view as a safe-rejection convention and never as verifier detection, and it is not interchangeable with the HOLD-only view in Table 4.4.

*Table 4.7. Invalid-input handling (separate block; excluded from precision/recall).*

| Condition | Expected-invalid cases | Classified `INVALID_INPUT` | Handled rate | Schema-accepted expected-invalid | Unexpected invalid rows on the 26 schema-valid cases | Unexpected-invalid rate | Rows with `INVALID_INPUT` status |
|---|---:|---:|---:|---:|---:|---:|---:|
| graph | 10 | 10 | 10/10 = 1.0000 | 0 | 0 | 0/26 = 0.0000 | 10 |
| text | 10 | 10 | 10/10 = 1.0000 | 0 | 0 | 0/26 = 0.0000 | 10 |
| unguarded | 10 | 10 | 10/10 = 1.0000 | not separately recorded | not separately recorded | not separately recorded | 10 |
| graph (dev / heldout) | 5 / 5 | 5 / 5 | 5/5 each | 0 / 0 | 0 / 0 | 0/13 each | 5 / 5 |
| text (dev / heldout) | 5 / 5 | 5 / 5 | 5/5 each | 0 / 0 | 0 / 0 | 0/13 each | 5 / 5 |

*Source:* `data/results/evaluation-v2/metrics.json` → `$.condition_metrics.{graph,text}.invalid_input` (pooled and per split), `$.condition_metrics.unguarded.decision_counts` and `$.invalid_input_summary.construction_failures_by_condition.unguarded`; `$.invalid_input_summary.case_ids` lists the ten expected-invalid cases. The artifact's `invalid_input` block is recorded only for the two verifier conditions; for the unguarded reference the ten construction failures are recorded in `$.invalid_input_summary`, while the schema-accepted and unexpected-invalid fields are not separately reported and are marked "not separately recorded" rather than zero.

*Note.* `INVALID_INPUT` is produced before verification, so this block is a construction-conformance measure, not a detection result: none of these ten rows is credited as a true positive in Table 4.4, and none may write memory. The denominator 26 for unexpected-invalid rows is the number of schema-valid cases.

---

## 4.3 RQ1 — graph versus text and the information-parity audit

*Table 4.8. Paired differences, `graph_vs_text` (rules-only run, identical frozen inputs).*

| Quantity | Value |
|---|---|
| Differing cases | 2 of 36 (5.5556%, derived: 2 ÷ 36) |
| Differing case ids | `DEV-13` (dev), `HELD-13` (heldout) |
| Decisions on both differing cases | graph `HOLD`; text `ACCEPT`; expected `HOLD` |
| Category of both differing cases | `contradiction_unresolved` |
| Artifact note (verbatim) | "Counts and lists only; with 36 single-reviewed synthetic cases no statistical superiority claim is made. Differences indicate verifier-construction differences on identical inputs." |

*Source:* `data/results/evaluation-v2/metrics.json` → `$.paired_differences.graph_vs_text` (`differing_case_count`, `differing_case_ids`, `differing_cases`, `note`). The categories are in `data/labels/evaluation-v2.jsonl` (`category` = `contradiction_unresolved` for both cases).

*Table 4.9. Fairness audit: four strategies on the same 36 frozen cases (rules-only, 0 paid API calls).*

| Strategy | Reads `evidence.contradicts` | `ACCEPT` | `HOLD` | `INVALID_INPUT` | False accepts | False holds | Valid accepted | Label agreement |
|---|---|---:|---:|---:|---|---|---|---|
| unguarded | no | 26 | 0 | 10 | 12/12 (all expected-HOLD cases applied) | 0/14 | 14/14 (raw applied; not verifier acceptance) | 24/36 = 0.6667 |
| text | no | 16 | 10 | 10 | 2/12 (`DEV-13`, `HELD-13`) | 0/14 | 14/14 | 34/36 = 0.9444 |
| text_fair | yes | 14 | 12 | 10 | 0/12 | 0/14 | 14/14 | 36/36 = 1.0000 |
| graph | yes | 14 | 12 | 10 | 0/12 | 0/14 | 14/14 | 36/36 = 1.0000 |

*Source:* `data/results/fairness-audit-v2/audit.json` → `$.strategies.*.reads_contradicts` and `$.per_strategy.{unguarded,text,text_fair,graph}` (`decision_counts`, `false_accepts`, `false_holds`, `valid_acceptance`, `label_agreement`). Per-split false accepts: dev `DEV-13` and heldout `HELD-13` for `text`; six per split for `unguarded` (`DEV-08`–`DEV-13`, `HELD-08`–`HELD-13`); none for `text_fair` or `graph` (`$.breakdown.by_split.*.strategies.*.false_accept_case_ids`).

*Note.* The audit records the unguarded reference's 26 applications as `ACCEPT` (its own semantics string says this is "equivalent to applying the candidate"); `data/results/evaluation-v2/metrics.json` records the same 26 rows as `APPLIED`. The unguarded valid-accepted cell is therefore raw application, not verifier acceptance; `docs/fairness-audit.md` prints it as "—", and both readings are consistent. `text` is the historical exact-claim-set baseline used in the rules run; `text_fair` is a control constructed for the audit (claim-set checks plus contradiction access), not a production dispatch path.

*Table 4.10. Pre-specified information-parity contrasts with case ids.*

| Contrast | Question isolated | Differing cases | Differing case ids | Decision change | Expected | Reason-code change | Source |
|---|---|---|---:|---|---|---|---|
| `graph_vs_text` | verifier construction, **not** information-matched | 2/36 | `DEV-13`, `HELD-13` | graph `HOLD` → text `ACCEPT` | `HOLD` | graph records `CONTRADICTED_CLAIM` on both; text records none | `metrics.json` → `$.paired_differences.graph_vs_text` |
| `text_vs_text_fair` | value of `evidence.contradicts` information access | 2/36 | `DEV-13`, `HELD-13` | text `ACCEPT` → `text_fair` `HOLD` | `HOLD` | `text_fair` adds FAIL `CONTRADICTED_CLAIM` (with `COVERAGE_OK`, `PRESERVATION_OK`) | `audit.json` → `$.paired_differences.text_vs_text_fair` |
| `text_fair_vs_graph` | verifier construction with information held equal | 0/36 | — | — | — | — | `audit.json` → `$.paired_differences.text_fair_vs_graph` = `[]` |

*Source:* `data/results/fairness-audit-v2/audit.json` → `$.paired_differences.*`; `data/results/evaluation-v2/metrics.json` → `$.paired_differences.graph_vs_text`.

*Table 4.11. Failure reason codes and observed checks, pooled (rules-only run).*

| Reason code | graph FAIL | text FAIL |
|---|---:|---:|
| `CONTRADICTED_CLAIM` | 2 | 0 |
| `PRIOR_RECORD_UNAUTHORIZED_CHANGE` | 4 | 4 |
| `REQUIRED_CLAIM_OMITTED` | 8 | 8 |
| `UNSUPPORTED_CLAIM` | 8 | 8 |
| `checks_observed` | 78 | 78 |
| `invalid_input_rows` | 10 | 10 |

*Source:* `data/results/evaluation-v2/metrics.json` → `$.condition_metrics.graph.reason_code_counts`, `$.condition_metrics.text.reason_code_counts`. `uncertain` counts are empty for both conditions.

*Note.* The only difference in failure attribution is the two `CONTRADICTED_CLAIM` failures, both on the cases in Table 4.8. The remaining reason-code counts are identical because the two verifiers examine the same canonical transition and the same 78 checks were observed; the audit attributes the difference to information access, not to a different number of checks.

---

## 4.4 RQ1 — held-out model-based conditions

*Table 4.12. Held-out LLM and semantic conditions: decisions and case-level outcomes (held-out split only; 18 cases = 13 schema-valid + 5 expected-invalid).*

| Condition | `ACCEPT` | `HOLD` | `INVALID_INPUT` | False accepts | False holds | Valid accepted | HOLD-only detection | Combined safe rejection | Case coverage |
|---|---:|---:|---:|---|---|---|---|---|---|
| graph (LLM) | 3 | 10 | 5 | 0/6 | 4/7 = 0.5714 | 3/7 = 0.4286 | 6/6 = 1.0000 | 11/11 = 1.0000 | 13/13 = 1.0000 |
| text (LLM) | 6 | 7 | 5 | 0/6 | 1/7 = 0.1429 | 6/7 = 0.8571 | 6/6 = 1.0000 | 11/11 = 1.0000 | 13/13 = 1.0000 |
| semantic (text + semantic check) | 8 | 5 | 5 | 1/6 = 0.1667 (`HELD-13`) | 0/7 | 7/7 = 1.0000 | 5/6 = 0.8333 | 10/11 = 0.9091 | 13/13 = 1.0000 |

*Source:* `data/results/llm-heldout-v2/metrics.json` → `$.condition_metrics.{graph,text,semantic}` (`decision_counts`, `false_accepts`, `false_holds`, `valid_acceptance`, `faulty_detection_hold_only`, `faulty_detection_combined`, `case_coverage`). False-hold ids: graph `HELD-01`, `HELD-02`, `HELD-03`, `HELD-07`; text `HELD-03`; semantic none. Semantic false accept id: `HELD-13`. Per-case check outcomes are in `data/results/llm-heldout-v2/summary.json` → `$.results`.

*Note.* "Faulty" here means the **HOLD-only** held-out denominator: 6 expected-`HOLD` cases (`HELD-08`–`HELD-13`). The combined safe-rejection column uses the same convention as Table 4.6 (6 expected `HOLD` + 5 expected `INVALID_INPUT` = 11). The run recorded no verifier errors (0/13 per condition), so the as-completed and fail-closed ITT forms coincide; the held-out artifact does not publish a separate ITT block, and none is inferred here. Expected-invalid handling was 5/5 in every condition (`row_count` 18, `invalid_input_rows` 5, `executed_rows` 13 per condition).

*Table 4.13. Held-out run: token, attempt, latency, and estimated-cost fields per condition.*

| Condition | Input tokens | Output tokens | Total tokens | Rows with reported usage | Provider attempts | Mean latency (ms) | Estimated cost low (USD) | Estimated cost high (USD) | Billing data available |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| graph | 18,893 | 44,318 | 63,211 | 13 | 16 | 20432.41176923077 | 0.006321 | 0.126422 | false |
| text | 9,430 | 19,057 | 28,487 | 13 | 17 | 8533.353076923077 | 0.002849 | 0.056974 | false |
| semantic | 6,182 | 3,351 | 9,533 | 13 | 26 | 2973.7456923076925 | 0.000953 | 0.019066 | false |
| **total** | **34,505** | **66,726** | **101,231** | **54** | **59** | — | **0.010123** | **0.202462** | **false** |

*Source:* `data/results/llm-heldout-v2/metrics.json` → `$.condition_metrics.*.usage.reported`, `.attempts`, `.mean_latency_ms`, `.estimated_cost_range_usd`; totals from `$.run.usage.reported`, `$.run.estimated_cost_range_usd`, `$.run.budget`. The mean latencies are the exact artifact values in milliseconds (approximately 20.4 s, 8.5 s and 3.0 s) and are means over the 13 executed (schema-valid) rows per condition; the five `INVALID_INPUT` rows per condition were rejected by schema construction with no provider call.

*Note.* Estimated cost is **not billed cost**: the provider did not report monetary cost, the artifact records `billing_data_available: false`, and the range is a mechanical application of an assumed 0.10–2.00 USD per 1,000,000 accounted tokens to the token telemetry (`$.cost_assumption`). Token counts are usage telemetry, not a billing record. Derived descriptive ratios from these cells: graph ÷ text tokens = 63,211 ÷ 28,487 = 2.2189; graph ÷ text mean latency = 20,432.41176923077 ÷ 8,533.353076923077 = 2.3944. These ratios are descriptive only, at n = 13 executed rows per condition.

*Table 4.14. Held-out run accounting and budget facts.*

| Field | Value |
|---|---|
| Run id | `llm-heldout-v2-deepseek-flash` |
| Code revision recorded in artifacts | `41905266e29a4ae699655d8b66e92474a4ee8921` (`4190526`) |
| Split / case count | `heldout` / 18 cases (13 schema-valid, 5 expected-invalid) |
| Rows | 54 total (18 × 3 conditions); 39 executed; 15 `INVALID_INPUT` |
| Planned / executed non-invalid rows | 39 / 39 |
| Reported tokens | 101,231 (34,505 input, 66,726 output); 54 rows with reported usage |
| Estimated tokens charged | 0 accounted tokens from the 2,500-token fallback; 0 rows |
| Accounted tokens | 101,231 |
| Budget cap / aborted / overshoot | 250,000 / false / 0 |
| Provider attempts | 59 |
| Cache hits | 0 |
| Verifier errors | 0 rows |
| Semantic uncertain rows | 0 (abstention 0/13) |
| Model / provider | `deepseek-flash` / `deepseek-openai-compatible` |
| Prompt versions | `membud-transition-checks-v1` (LLM); `membud-semantic-compare-v1` (semantic) |

*Source:* `data/results/llm-heldout-v2/summary.json` → `$.run_id`, `$.code_revision`, `$.split`, `$.case_count`, `$.valid_case_count`, `$.invalid_case_count`, `$.budget.*`, `$.usage.*`, `$.reported_tokens_used`, `$.estimated_tokens_used`, `$.accounted_tokens_used`, `$.attempts`, `$.cache_hits`, `$.llm_verifier_errors`, `$.semantic_uncertain_rows`, `$.model`, `$.provider`, `$.prompt_versions`; `data/results/llm-heldout-v2/metrics.json` → `$.run.*`, `$.condition_metrics.semantic.semantic_abstention_rate`.

---

## 4.5 RQ3 — cost, tokens, latency, and acceptance trade-offs

*Table 4.15. Rules-only instrumentation: latency and graph size (timer-noise-dominated; instrumentation only, not a performance result).*

| Condition | Split | End-to-end mean ms (n) | Preparation mean ms (n) | Verification mean ms (n) | Graph nodes mean (n) | Graph edges mean (n) | Total tokens | Estimated cost (USD) |
|---|---|---|---:|---:|---:|---:|---:|---:|
| graph | pooled | 0.24980555555555556 (36) | 0.04246153846153847 (26) | 0.11123076923076923 (26) | 9.615384615384615 (26) | 13.961538461538462 (26) | 0 | 0.0 (`rules_only_no_paid_calls`) |
| graph | dev | 0.25027777777777777 (18) | 0.047 (13) | 0.12146153846153845 (13) | 9.461538461538462 (13) | 13.692307692307692 (13) | 0 | 0.0 |
| graph | heldout | 0.24933333333333335 (18) | 0.03792307692307692 (13) | 0.10099999999999999 (13) | 9.76923076923077 (13) | 14.23076923076923 (13) | 0 | 0.0 |
| text | pooled | 0.29330555555555554 (36) | 0.05746153846153846 (26) | 0.10026923076923076 (26) | — | — | 0 | 0.0 (`rules_only_no_paid_calls`) |
| text | dev | 0.2687222222222222 (18) | 0.057923076923076924 (13) | 0.10307692307692308 (13) | — | — | 0 | 0.0 |
| text | heldout | 0.3178888888888889 (18) | 0.057 (13) | 0.09746153846153846 (13) | — | — | 0 | 0.0 |
| unguarded | pooled | 0.1308888888888889 (36) | — | — | — | — | no token block | no cost block |
| unguarded | dev | 0.13877777777777778 (18) | — | — | — | — | no token block | no cost block |
| unguarded | heldout | 0.123 (18) | — | — | — | — | no token block | no cost block |

*Source:* `data/results/evaluation-v2/metrics.json` → `$.condition_metrics.{graph,text,unguarded}.timing_ms`, `.timing_breakdown`, `.llm_usage`, `.token_usage`, `.estimated_cost`, and the same blocks under `splits.{dev,heldout}`. `preparation_ms` and `verification_ms` are means over the 26 schema-valid rows where instrumentation was recorded; `latency_ms` is end-to-end over all 36 rows. The unguarded condition performs no verifier call and therefore records no token or cost block, which the protocol expects rather than treats as missing data.

*Note.* All rules-run times are sub-millisecond on 36 tiny fixtures and are dominated by timer noise (the protocol and narrative reports state this explicitly). They are reported as instrumentation for reproducibility, not as a performance finding; no claim is made that any condition is faster or cheaper on this basis. Graph size is descriptive: the structural representation averaged 9.62 nodes and 13.96 edges over the 26 schema-valid cases.

*Table 4.16. Cost and token fields recorded by run.*

| Run | Paid calls | Tokens recorded | Token fields | Estimated-cost field | Billing data available | Attempts | Cache hits |
|---|---|---:|---|---|---|---:|---:|
| rules graph | 0 | 0 / 0 / 0 | `$.condition_metrics.graph.llm_usage` and `.token_usage` (all zero) | 0.0 USD, basis `rules_only_no_paid_calls` | false | 0 | 0 |
| rules text | 0 | 0 / 0 / 0 | `$.condition_metrics.text.llm_usage` and `.token_usage` (all zero) | 0.0 USD, basis `rules_only_no_paid_calls` | false | 0 | 0 |
| rules unguarded | 0 | no token block | no `llm_usage` / `token_usage` block | no cost block | — | — | — |
| held-out LLM (all conditions) | not recorded (59 provider attempts) | 101,231 total (34,505 in / 66,726 out) | `$.run.usage.reported`; per-condition `$.condition_metrics.*.usage` | range 0.010123–0.202462 USD, `estimated: true` | false | 59 | 0 |

*Source:* `data/results/evaluation-v2/metrics.json` → `$.condition_metrics.*.llm_usage`, `.token_usage`, `.estimated_cost`; `data/results/llm-heldout-v2/metrics.json` → `$.run.usage`, `$.run.estimated_cost_range_usd`, `$.cost_assumption`, `$.condition_metrics.*.usage`, `.attempts`, `.cache_hit_rate`.

*Note.* No billed monetary amount is recorded for any run. For the held-out LLM run, a narrative report (not the JSON artifact) mentions an approximate 0.40 USD upper bound at a top published per-token rate; that figure appears in no JSON artifact, the artifact's stated envelope at 2.00 USD per million tokens is 0.202462 USD, and neither figure is a bill. This chapter therefore reports only the artifact range and states that billing data are unavailable.

---

## 4.6 RQ2 — modularity evidence

*Table 4.17. RQ2 evidence: demonstrated capabilities and non-claims.*

| Capability | Status on the pinned revision | Evidence |
|---|---|---|
| Structural `MemoryAdapter` protocol (`backend_name`, `snapshot(scope)`, atomic `replace_state(scope, records)`) | implemented | `src/membud/adapters/base.py`; `data/adapter-contract.md` §1 |
| Reference in-memory backend | demonstrated | `src/membud/adapters/reference.py`; `data/backend-feasibility.md` L11–L21 |
| Reference SQLite backend (single `BEGIN IMMEDIATE` scope replacement) | demonstrated | `src/membud/adapters/reference.py`; `data/backend-feasibility.md` L11–L21 |
| `GuardedMemory` interception order: prior-state capture → canonical transition (invalid input rejected before any write) → verification with audit → ACCEPT-only persistence → decision record | demonstrated | `src/membud/adapters/reference.py`; `data/adapter-contract.md` §2; `data/backend-feasibility.md` L11–L21 |
| HOLD/invalid input leaves backend records and SQLite file bytes identical | demonstrated by automated tests comparing backend state and SQLite SHA-256 before/after HOLD | `tests/test_reference_backend.py` (e.g. `test_hold_leaves_sqlite_backend_bytes_identical`, `test_hold_leaves_in_memory_backend_records_identical`, `test_invalid_candidate_is_rejected_and_leaves_backend_identical`); `data/backend-feasibility.md` L13–L21 |
| Undeclared/unauthorized changes rejected before any write | demonstrated by automated tests | `tests/test_reference_backend.py` (`test_undeclared_state_changes_are_rejected_before_any_write`, `test_unauthorized_delete_is_rejected_before_any_write`, `test_delete_authorization_must_reference_transition_evidence`); `data/adapter-contract.md` §2 |
| Every outcome emits a decision record; ACCEPT applies exactly the candidate | demonstrated by automated tests | `tests/test_reference_backend.py` (`test_accept_applies_exactly_the_candidate`, `test_decision_log_appends_every_outcome_in_order`) |
| Third-party backend integration or compatibility (Mem0, A-Mem, Zep/Graphiti) | **not claimed** — no third-party adapter is implemented or exercised | `data/backend-feasibility.md` L3, L7–L9, L78–L80; `data/adapter-contract.md` L1–L7, L127–L129 |
| "Adapter interface evaluated across three backends" | **not supported** — two *reference* backends plus the interception layer are demonstrated; third-party backends were source-inspected only | `data/backend-feasibility.md` L71–L80 |

*Source:* `data/adapter-contract.md`; `data/backend-feasibility.md`; `tests/test_reference_backend.py`. The tests are automated repository tests, not an evaluation run; they establish that the contract is implementable and fail-safe on the reference backends, not that a third-party host can be guarded.

*Table 4.18. Third-party backend source-inspection findings (no install, no runtime call, no integration).*

| Host inspected | Version / commit inspected | Licence metadata | Pre-commit boundary finding | Integration verdict |
|---|---|---|---|---|
| Mem0 | `mem0ai==2.2.1` (wheel SHA-256 `fe91bb91ac8926231993a4aa58df00a60c6c74c709e6a338fe399500776eef4d`; downloaded with `pip download --no-deps`, unpacked only) | Apache-2.0 | no public callback, transaction, or pre-commit hook exposing the complete proposed transition; writes go to several stores sequentially (`vector_store.insert`, history, entity store, messages) | not integrated; a guarded integration would need a fork/subclass or host-owned staging pipeline; no compatibility claimed |
| A-Mem | repository `agiresearch/A-mem` at commit `ceffb860f0712bbae97b184d440df62bc910ca8d` (shallow HEAD) | MIT | `add_note()` → `process_memory()` can mutate neighbour notes before the new note is persisted; public `update()` writes ChromaDB by delete/re-add | not integrated; staging wrapper/fork required to hold all new and neighbour changes before persistence; no compatibility claimed |
| Reference backend | in-tree `InMemoryBackend`, `SQLiteBackend`, `GuardedMemory` | in-tree reference code | interception order implemented and tested | demonstrated |

*Source:* `data/backend-feasibility.md` (pinned at `1ce84ce`) → Mem0 section, A-Mem section, "Demonstrated vs claimed" table; `data/adapter-contract.md` §4 (known limits).

*Note.* The adapter's information requirements are part of the contract: an adapter must preserve stable record ids and claim boundaries, expose scopes as plain mappings, and document source provenance, timestamps, relationships, metadata, and host-side effects that the mapping loses. Evidence and proposed operations travel in the canonical transition, not in adapter metadata; adapters must not invent evidence, authorization, or operation annotations. The reference integration also records known limits: the controlled JSON baseline and the host backend are separate writes with no distributed transaction, a crash between them can leave the baseline ahead of the host, and only adapters whose `replace_state` is genuinely atomic (or restored on failure) can be labelled guarded.

---

## 4.7 Summary of findings

*Table 4.19. Summary by research question: short answer, key evidence, and what is not supported.*

| RQ | Short answer on this instrument | Key evidence | What is not supported |
|---|---|---|---|
| RQ1a — guarded vs unguarded | The guarded conditions committed no candidate on the 10 expected-invalid cases and held 12/12 (graph) and 10/12 (text) expected-`HOLD` cases; the unguarded reference applied all 12 expected-`HOLD` candidates (raw false-accept-equivalent 12/12 on that denominator). | Tables 4.3, 4.4, 4.5, 4.7 | That guarded verification "reduces errors" in general, that the difference is causal, or that these counts estimate a population. |
| RQ1b — graph vs non-graph (rules) | Plain text differed from graph on exactly 2 of 36 cases (`DEV-13`, `HELD-13`); with information access equalized, fair text and graph agreed on all 36 cases (zero paired differences). The visible difference is annotation access, not structural inference. | Tables 4.8, 4.9, 4.10, 4.11 | A graph advantage, a "graph catches contradictions text cannot" reading, statistical significance, or equivalence between representations. |
| RQ1b — held-out model conditions | The graph-LLM condition was conservative in the false-hold sense (4/7 valid updates held, 0/6 false accepts); text-LLM held 1/7; the semantic condition accepted all 7 valid updates and one annotated-contradiction case (`HELD-13`), because at the evaluated revision it does not read `evidence.contradicts`. Conditions differed in token and latency budgets, so the comparison is descriptive. | Tables 4.12, 4.13, 4.14 | Superiority of any condition, an equal-budget comparison, or a causal account of the graph false holds (the pinned artifacts record the decisions and failed checks, not the cause). |
| RQ2 — modularity | A structural adapter contract and `GuardedMemory` interception are implemented and tested on two reference backends (in-memory, SQLite), including byte-identical HOLD and pre-write rejection of invalid updates. Mem0 2.2.1 and A-Mem were source-inspected and found to lack a safe public pre-commit boundary; no third-party integration exists. | Tables 4.17, 4.18 | Third-party compatibility or integration, an evaluation across three backends, or production readiness. |
| RQ3 — trade-offs | Acceptance, false holds, tokens, attempts, latency and an estimated (not billed) cost range are recorded for the guarded rules conditions and the held-out LLM conditions. Rules latency is timer noise; billed cost is unavailable; downstream question answering was not measured. | Tables 4.13, 4.14, 4.15, 4.16 | A billed cost, a performance claim from rules latency, a downstream QA or utility claim, or an equal-budget token/latency comparison. |

*Source:* tables above; every cell traces to the artifacts listed under each table. RQ labels follow the study's three research questions (RQ1 reliability, RQ2 modularity, RQ3 trade-offs; RQ1 is split into the guarded-versus-unguarded and graph-versus-non-graph contrasts, with the held-out model conditions reported separately).

*Overall result.* On this 36-case, single-reviewer, synthetic instrument, the pipeline behaved as designed: both guarded rules conditions accepted all 14 labelled-valid updates and never wrote memory for a schema-invalid transition, both rejected every expected-invalid case, the graph condition rejected all 12 expected-`HOLD` cases while the plain text condition rejected 10 of 12 (its two commits were the annotated-contradiction cases discussed in Section 4.3), and the unguarded reference applied every constructible candidate. The visible graph-versus-text difference was fully explained by annotation access — a fair text control consuming the same `evidence.contradicts` field matched the graph verifier on all 36 cases — so the evidence does not attribute the difference to graph structure. No representation-level advantage was detectable at this sample size; that is not evidence of equivalence, and it is not evidence of superiority. The held-out model conditions were descriptive and, at the evaluated revision, included an over-conservative graph condition and a semantic condition with a documented annotation gap. RQ2 is answered as a contract-plus-reference-backend-plus-feasibility result rather than a multi-backend evaluation, and RQ3 is answered for acceptance, false holds, tokens, latency and an estimated (unbilled) cost range, with downstream question answering out of scope.

---

## Reproduction checks

The commands below were executed with `.venv/bin/python` from the workspace root while preparing this file; each printed exactly the values used above. At the pinned basis the working-tree artifacts were byte-identical to `git show 1ce84ce:<path>` (SHA-256 compared), so reading the working-tree path is equivalent to reading the committed artifact.

1. Decision counts and split counts (Table 4.3):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['condition_metrics']['graph']['decision_counts'],m['condition_metrics']['text']['decision_counts'],m['condition_metrics']['unguarded']['decision_counts'])"`
2. HOLD-only as-completed / ITT blocks (Tables 4.4, 4.5):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['condition_metrics']['text']['as_completed'],m['condition_metrics']['text']['fail_closed_itt'])"`
3. Combined view and its per-split blocks (Table 4.6):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['condition_metrics']['graph']['faulty_detection'],m['condition_metrics']['text']['faulty_detection'])"`
4. Invalid-input blocks (Table 4.7):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['condition_metrics']['graph']['invalid_input'],m['invalid_input_summary'])"`
5. Candidate-error rates (Table 4.2):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['candidate_error_rates'])"`
6. Paired graph-vs-text differences (Table 4.8):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['paired_differences'])"`
7. Fairness-audit strategies and contrasts (Tables 4.9, 4.10):
   `.venv/bin/python -c "import json;a=json.load(open('data/results/fairness-audit-v2/audit.json'));print(a['per_strategy']['text_fair']);print(a['paired_differences'])"`
8. Reason codes (Table 4.11):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['condition_metrics']['graph']['reason_code_counts'],m['condition_metrics']['text']['reason_code_counts'])"`
9. Held-out decisions and outcomes (Table 4.12):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/llm-heldout-v2/metrics.json'));print({c:{k:m['condition_metrics'][c][k] for k in ('decision_counts','false_accepts','false_holds','valid_acceptance','faulty_detection_hold_only','faulty_detection_combined','case_coverage')} for c in ('graph','text','semantic')})"`
10. Held-out tokens, attempts, latency, cost (Tables 4.13, 4.14):
    `.venv/bin/python -c "import json;m=json.load(open('data/results/llm-heldout-v2/metrics.json'));print(m['run']['usage']['reported'],m['run']['estimated_cost_range_usd'],m['run']['budget']);print({c:(m['condition_metrics'][c]['attempts'],m['condition_metrics'][c]['mean_latency_ms'],m['condition_metrics'][c]['usage']['reported'],m['condition_metrics'][c]['estimated_cost_range_usd']) for c in ('graph','text','semantic')})"`
11. Rules timing and graph size (Table 4.15):
    `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print({c:m['condition_metrics'][c]['timing_ms'] for c in ('graph','text','unguarded')})"`
12. Derived ratios (Table 4.13 notes):
    `.venv/bin/python -c "print(63211/28487, 20432.41176923077/8533.353076923077, 2/36)"`

## Source notes and discrepancies

1. **Rules-run code revision.** The rules run records `code_revision = working-tree-sha256:044a09c92325d90499c00616222144c5d0ea81d59b2ef5c769c71fbdebd92bfc`, a working-tree digest rather than a git commit; the mapping to a commit is not recorded in the artifact. The paper's evaluation basis is the committed artifact set at `1ce84ce` (hash pins in the table above), and this chapter does not claim that the rules run is pinned to that commit. The held-out LLM artifacts record `41905266e29a4ae699655d8b66e92474a4ee8921`, and this chapter cites that revision for the model-based computation.
2. **Unguarded valid-acceptance rendering.** `docs/fairness-audit.md` prints the unguarded "Valid accepted" cell as "—"; `audit.json` records `14/14` with rate 1.0, which is raw application, not verifier acceptance. Table 4.9 shows the value with that qualifier; both sources are consistent once the semantics are stated.
3. **Narrative 12/12 claim in the milestone report.** `docs/technical-report-milestone-2.md` §7 contains a bullet stating that both conditions detect 12/12 HOLD cases with 0 false accepts; the artifact (`metrics.json` → `$.condition_metrics.text.as_completed`) records text detection `10/12` and false accepts `2/12`. The artifact is authoritative, and Table 4.4 follows it. The report's later §11 addendum and `docs/fairness-audit.md` agree with the artifact.
4. **Narrative cost figure.** The pinned `docs/llm-heldout.md` mentions an approximate 0.40 USD upper bound "at DeepSeek's top published per-token rate". That figure is not in any JSON artifact; the artifact envelope at 2.00 USD per million accounted tokens is 0.202462 USD. Neither is a billed amount; the provider reported no billing data. Table 4.13 reports the artifact range only.
5. **Held-out false-hold cause.** The pinned artifacts record the decisions and the per-case check outcomes for the four graph false holds (all four fail `preservation` with `PRIOR_RECORD_UNAUTHORIZED_CHANGE`; `HELD-01` also fails `coverage` with `REQUIRED_CLAIM_OMITTED`), but they do not establish a cause. This chapter does not attribute the false holds to serialization length, token budget, or any other mechanism.
6. **Semantic annotation gap.** At the evaluated revision `4190526`, the semantic module does not consume `evidence.contradicts`; the held-out artifact records `HELD-13` as semantic `ACCEPT` with the `semantic` check `PASS` (`SEMANTIC_OK`). Later revisions are outside the evaluated scope and are not reported in this chapter.
