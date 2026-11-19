# CHAPTER 4 RESULTS

*Draft v1 — 2026-11-07. Pinned evaluated revision: rules-only run and fairness audit at commit `1ce84ce`; held-out LLM/semantic run at the code revision recorded in its artifacts, `4190526`. Current `main` is later (a concurrent session advanced it); no post-evaluation artifact is cited in this chapter. Labels are single-reviewer exploratory annotations; a second independent review is pending. All tables are reproduced from the committed artifacts; their source pointers and reproduction commands are collected in `paper/results-tables.md` and `paper/appendix-bab4-notes.md`.*

This chapter reports the observed outcomes of the study on the frozen instrument described in Chapter 3. Section 4.1 characterises the instrument and its candidate-update errors; Sections 4.2–4.4 answer RQ1 for the deterministic comparison, the information-parity audit, and the held-out model-based conditions; Section 4.5 answers RQ3 for acceptance, tokens, latency and unbilled cost; Section 4.6 answers RQ2 at the level of the adapter contract, the reference backend and the feasibility inspection; Section 4.7 summarises the findings by research question. The results are descriptive: the instrument has 36 single-reviewer synthetic cases, no significance, power or causal claim is made, and a zero difference is read as "not detectable at this sample size", never as equivalence.


## 4.1 Instrument and Candidate-Update Errors

This chapter reports the observed outcomes of the evaluation defined in Section 3.2.6, organised by research question: Section 4.2 covers the guarded-versus-unguarded comparison, Section 4.3 the graph-versus-text comparison and its information-parity audit, Section 4.4 the held-out model-based conditions, Section 4.5 the cost, token and latency evidence, Section 4.6 the modularity evidence, and Section 4.7 the summary. The numbers come from two pinned artifact sets: the rules-only run and fairness audit committed at revision `1ce84ce`, and the held-out LLM/semantic run whose artifacts record code revision `4190526`. The exact tables, with JSON pointers, are in the results-table appendix (`paper/results-tables.md`); this chapter cites them by number.

The frozen instrument is `membud-evaluation-v2`: 36 synthetic Indonesian-themed transition cases, split into 18 development cases (`DEV-01`–`DEV-18`) and 18 held-out cases (`HELD-01`–`HELD-18`). The labels assign 14 cases to `ACCEPT`, 12 to `HOLD` and 10 to `INVALID_INPUT`, and each split has the same 7/6/5 composition. The 36 cases cover 18 categories with exactly two cases each: 13 categories of schema-valid updates and designed faults, and five invalid-input families covering unauthorized delete, authorization-target mismatch, and undeclared insertion, modification and removal. Every case carries `schema_version` 2, the labels are separated from verifier inputs, and the candidates were hand-authored once and reused unchanged across conditions, so condition differences cannot come from different candidate material (Table 4.1).

The labels are single-reviewer exploratory annotations. One reviewer authored the cases and proposed labels, no second independent annotation has been collected, and no agreement statistic exists; the manifest records this and marks every label second-review pending. The cases were authored together and frozen before the rules-only run, and no threshold, prompt, case or metric definition was tuned on held-out outcomes, which removes one class of post hoc selection but does not replace independent annotation. Because the cases were authored alongside the verifiers, circularity cannot be ruled out, and the results are descriptive counts on this instrument rather than population estimates.

Table 4.2 reports the candidate-update error rates, which are instrument-validation measures computed once from the cases and labels, independently of any condition. Required-claim omission was 12/68: twelve required supported claims absent from the candidate, out of 68 required claims. Unsupported addition was 8/64: eight unsupported candidate claims, out of 64 candidate claims; every candidate claim was treated as material because the labels carry no separate materiality flag. Prior-record corruption was 4/40: four valid prior records annotated as expected to persist were improperly changed or removed, out of 40 such records. At the case level, 12 of 36 cases contained at least one relevant candidate error. These rates describe whether the frozen proposals encoded the intended faults; they are not verifier scores and not a count of faulty cases as used for detection. The labels also record 28 fault-type assignments across the 22 expected-faulty cases, while the 14 expected-valid labels carry none; a case may carry more than one type. Cases with no evidence or an empty candidate are reported as not applicable rather than as zero, because absence of an opportunity to err is not the same as making no error.

Three senses of "faulty" recur and are kept apart. The HOLD-only view covers the 12 cases labelled `HOLD` (6 in the held-out model split); it asks whether a verifier flagged an update that should not have been committed and never counts schema-invalid input as a detection. The combined safe-rejection view covers 22 pooled cases (12 expected `HOLD` plus 10 expected `INVALID_INPUT`) and credits a schema-construction rejection as a correct safe rejection; per Section 3.2.6 it is a reporting convention for the decision stream and is never described as verifier detection. Candidate-update errors are a third, instrument-level notion measured over claim slots and record slots. Two further conventions carry over: outcomes are reported in an as-completed form, which excludes error rows from numerator and denominator, and a fail-closed intention-to-treat form, which retains every reference case and treats an execution or verifier error as a non-accepting outcome with error-holds counted separately; and `INVALID_INPUT` is reported as a separate block because it is a construction failure recorded before verification, not a verifier decision and never a memory write.

Finally, provenance differs between runs and is stated where numbers are used. The rules-only run records a working-tree digest rather than a git commit; the paper's basis is the committed artifact set at `1ce84ce`, and the rules run is not claimed to be pinned to that commit. The held-out artifacts record `4190526` and are attributed to that revision. The fairness audit is deterministic and records zero paid API calls. Every table cell in the appendix is traceable to a committed JSON pointer, and derived cells are labelled as arithmetic on established cells.

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

## 4.2 RQ1 — Guarded versus Unguarded

RQ1 asks whether guarded verification reduces omissions, corruption of valid prior memory and unsupported additions relative to an unguarded update pipeline. The rules-only run addresses the guarded-versus-unguarded part directly: graph and text are guarded verifiers, unguarded applies every constructible candidate without verification, and all three receive the same frozen packages. The run made no paid API calls and recorded zero verifier and execution errors, so the decision stream is complete for all 36 cases. Tables 4.3–4.7 report the decision counts, detection, false-accept, false-hold, valid-acceptance and invalid-input views. The two guarded conditions differ on two cases, analysed in Section 4.3; this section reports the guarded-versus-unguarded contrast and each guarded condition's ordinary detection behaviour.

Pooled over the 36 cases, graph returned 14 `ACCEPT`, 12 `HOLD` and 10 `INVALID_INPUT`; text returned 16 `ACCEPT`, 10 `HOLD` and 10 `INVALID_INPUT`; neither recorded an `ERROR` row. Both splits had the same structure: graph 7/6/5 in each half, text 8/5/5 in each half. The unguarded reference has no verifier decisions: it recorded 26 `APPLIED` and 10 `INVALID_INPUT`, distributed as 13 applied and 5 invalid per half. The fairness audit records these 26 applications as `ACCEPT` for a shared vocabulary, denoting raw application rather than verifier acceptance. The unguarded raw effect on the labelled-`HOLD` candidates was 12/12: every such candidate was applied when no verifier was consulted. (The artifact calls this a false-accept-equivalent rate, a raw-effect reference only.)

The HOLD-only view covers the 12 cases labelled `HOLD` and the 14 labelled `ACCEPT`, using the 26 schema-valid cases and excluding the 10 expected-invalid rows. In the as-completed form (Table 4.4), graph flagged all 12 expected-`HOLD` cases (12/12), accepted none of them, accepted all 14 expected-valid updates (14/14) and recorded no false hold. Text flagged 10 of 12 (10/12 = 0.8333), accepting `DEV-13` and `HELD-13`, so its false-accept rate on this denominator was 2/12 = 0.1667; it also accepted all 14 expected-valid updates with no false hold. The split view repeats the pattern: graph 6/6 detected in each half, text 5/6 in each half with one false accept per half. Because no expected-valid case was held, the implied HOLD-only precision is 12/12 for graph and 10/10 for text; the artifact has no separate HOLD-only precision field, so the appendix marks those cells as arithmetic.

Table 4.5 reports the fail-closed intention-to-treat form, which retains every reference case and treats an error as a non-accepting outcome, with error-holds counted separately from detections. Here the two forms coincide numerically: there were zero error rows and zero error-holds in both guarded conditions, so ITT detection is 12/12 for graph and 10/12 for text, false accepts are 0/12 and 2/12, and valid acceptance is 14/14 in both. The distinction remains part of the protocol and is reported for completeness, but it is not evidence that errors occurred: no such rows existed in this run.

The combined safe-rejection view (Table 4.6) uses an explicitly larger denominator: the 22 cases expected-faulty under the combined definition, namely the 12 expected-`HOLD` plus the 10 expected-`INVALID_INPUT` cases, against the 14 expected-valid cases. Graph recorded 22 true positives, no false positives and no false negatives, for precision 22/22, recall 22/22 and false-accept rate 0/22. Text recorded 20 true positives and 2 false negatives (`DEV-13`, `HELD-13`), for precision 20/20, recall 20/22 = 0.9091 and false-accept rate 2/22 = 0.0909. Each split reproduces the structure at half the counts (graph 11/11, text 10/11). This view is a reporting convention because it credits a schema-construction rejection as a correct safe rejection; as Section 3.2.6 states, it is never verifier detection and is not interchangeable with the HOLD-only view.

Invalid input is reported separately in Table 4.7 and excluded from the precision and recall figures above. The ten expected-invalid cases were classified `INVALID_INPUT` 10/10 in both guarded conditions and in the unguarded reference; no expected-invalid case was accepted by schema construction, and no schema-valid case was unexpectedly classified invalid (0/26 in both guarded conditions). For the unguarded reference the artifact records the ten construction failures but does not separately publish the schema-accepted and unexpected-invalid fields; those cells are marked not separately recorded rather than zero. These rows never reach a verifier, are never counted as true positives and never write memory.

Taken together, the decision stream is the pattern the design intends. Both guarded conditions accepted all 14 labelled-valid updates, neither produced a false hold, and both classified every expected-invalid transition before verification. Graph did not commit a candidate for any expected-`HOLD` case; text committed candidates for two of the twelve, and those are the annotated-contradiction cases analysed next. The unguarded reference applied every constructible candidate, including all twelve expected-`HOLD` candidates. These are exact counts on 36 single-reviewer synthetic cases: no significance test, power analysis or confidence interval is computed, no condition is claimed superior, and the counts are not population estimates. The difference between guarded conditions is reported descriptively in Section 4.3 as information access, not verifier quality.

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

## 4.3 RQ1 — Graph versus Text and the Information-Parity Audit

The second half of RQ1 asks whether the graph verifier detects more transition errors than a non-graph verifier applying the same criteria. The rules-only run answers this for the deterministic comparison: graph and text receive the identical frozen transition and differ only in verification mechanism, with the qualification that the plain text baseline does not consume the `evidence.contradicts` annotations while the graph verifier does. On the 36 paired cases the two conditions disagreed on exactly two, `DEV-13` and `HELD-13`. Both are `contradiction_unresolved` cases, both expected `HOLD`; graph returned `HOLD` and text returned `ACCEPT` on both, so the differing-case count was 2/36, or 5.5556% as a derived proportion (Table 4.8). The artifact's note states that these are counts and lists only, that no statistical superiority claim is made on 36 single-reviewed synthetic cases, and that the differences indicate verifier-construction differences on identical inputs.

The reason-code record shows what separated the conditions and what did not. Graph recorded two `CONTRADICTED_CLAIM` failures and text recorded none; the counts of `PRIOR_RECORD_UNAUTHORIZED_CHANGE` (4), `REQUIRED_CLAIM_OMITTED` (8) and `UNSUPPORTED_CLAIM` (8) were identical, both conditions observed 78 checks, and both recorded the ten invalid-input rows separately (Table 4.11). The verifiers therefore examined the same facts and failed on the same cases except the two contradiction cases, where graph had a failure signal the text baseline never evaluated. That motivated the information-parity audit, because a difference produced by one condition reading an annotation the other does not read is not yet evidence about representation.

The fairness audit tested this on the same 36 frozen cases, deterministically and with zero paid API calls. It compared four strategies: `unguarded`, the no-verifier reference; `text`, the historical exact-claim-set baseline, which does not read `evidence.contradicts`; `text_fair`, a control that applies the same claim-set checks plus the same contradiction access the graph verifier has, so that a candidate claim appearing in an evidence contradiction fails faithfulness with `CONTRADICTED_CLAIM`; and `graph`, the structural verifier. The audit separates two effects: `text` to `text_fair` isolates the value of contradiction information access, and `text_fair` to `graph` isolates verifier construction with information held equal.

Table 4.9 reports the results. The unguarded strategy applied all 26 schema-valid candidates, recorded no holds, produced 12 false accepts on the 12 expected-`HOLD` cases, applied the 14 expected-valid updates (raw application, not verifier acceptance), and agreed with the labels on 24/36 cases. Text returned 16 `ACCEPT`, 10 `HOLD` and 10 `INVALID_INPUT`, with 2/12 false accepts (`DEV-13`, `HELD-13`), 0/14 false holds, 14/14 valid acceptance and 34/36 label agreement. Text_fair and graph both returned 14 `ACCEPT`, 12 `HOLD` and 10 `INVALID_INPUT`, with 0/12 false accepts, 0/14 false holds, 14/14 valid acceptance and 36/36 label agreement. The per-split audit record repeats the pattern, with one false accept per half for text (the development case in the development half, the held-out case in the held-out half) and none for text_fair or graph.

The paired contrasts make the explanation explicit (Table 4.10). Adding contradiction access changed exactly two decisions: text moved from `ACCEPT` to `HOLD` on `DEV-13` and `HELD-13` under text_fair, adding the failure code `CONTRADICTED_CLAIM` while coverage and preservation stayed successful. Holding that access equal and comparing verifier construction, text_fair and graph differed on zero of the 36 cases; the audit's paired-difference list is empty. Because the zero-difference list spans all 36 cases it also spans all 26 non-invalid cases, and both descriptions are true when the denominator is stated. The audit concludes that on this manifest, once contradiction access is equalized, structural graph verification added no decision-level detection power beyond the fair text control, and the graph-versus-text gap is explained by information access rather than representation.

This result must be read precisely. The two rules-only differences are real decisions on identical inputs, but they are differences between a verifier that reads the contradiction annotations and one that does not; they are not evidence that graph structure caught a contradiction a fair text verifier could not. The defensible statement is that the gate behaviour differs because annotation access differs, and that with equal access the fair text control and graph agreed on every case in this instrument. The audit does not show graph structure is useless: its structural checks defend against malformed graph inputs and attribute failures, a capability the audit did not measure. It also does not establish equivalence between representations. Zero paired differences at n = 36 means no decision-level difference was detectable at this size; with single-reviewer synthetic cases and a minimal fair control, equivalence is not established, and the evidence is narrow. The plain text baseline is the historical control, and text_fair is an audit control, not a production verifier. Section 4.4 reports the model-based conditions, where the same annotation-access issue reappears; Section 4.7 states the resulting answer to RQ1 without upgrading it beyond these counts.

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

## 4.4 RQ1 — Held-Out Model-Based Conditions

Sections 4.2 and 4.3 isolate verifier construction with deterministic verifiers. The held-out model-based run addresses the same graph-versus-non-graph question when the decision is produced by a language model rather than by fixed rules. The run used only the held-out split: 18 cases, 13 schema-valid (7 labelled `ACCEPT`, 6 labelled `HOLD`) and 5 expected-invalid. Three conditions were executed: graph and text each submitted the serialized transition to an LLM verifier, differing in whether the serialization was the structural graph or the text form; semantic used the deterministic text baseline plus a separate LLM semantic check, so commit required all checks to pass. The artifacts record model id `deepseek-flash`, provider `deepseek-openai-compatible`, prompt versions `membud-transition-checks-v1` and `membud-semantic-compare-v1`, and code revision `4190526`; the run used temperature 0 and JSON mode under a hard 250,000-token cap. All 39 planned non-invalid rows executed, the five expected-invalid rows were recorded in every condition without a provider call, and the run recorded zero verifier errors, zero cache hits, no budget abort and zero overshoot. The model-based results are attributed to revision `4190526`.

Table 4.12 reports decisions and outcomes with two explicit denominators: the HOLD-only denominator of 6 held-out cases labelled `HOLD`, and the combined denominator of 11 adding the 5 expected-invalid cases under the same safe-rejection convention as Table 4.6. On the 18 held-out cases graph returned 3 `ACCEPT`, 10 `HOLD` and 5 `INVALID_INPUT`; text returned 6 `ACCEPT`, 7 `HOLD` and 5 `INVALID_INPUT`; semantic returned 8 `ACCEPT`, 5 `HOLD` and 5 `INVALID_INPUT`. Expected-invalid rows were handled 5/5 in every condition and case coverage was 13/13 executed rows in every condition.

The case-level outcomes show a clear false-hold cost in the graph condition and no false-accept cost in either LLM condition. Graph produced 0/6 false accepts and 4/7 false holds: it held the valid updates `HELD-01`, `HELD-02`, `HELD-03` and `HELD-07`, so valid acceptance was 3/7 and HOLD-only detection 6/6. Text produced 0/6 false accepts and 1/7 false holds (`HELD-03`), with valid acceptance 6/7 and HOLD-only detection 6/6. Both LLM conditions therefore rejected all six expected-`HOLD` cases, and the difference between them is how many valid updates they were willing to commit. The artifact's per-case checks show that all four graph false holds failed preservation with `PRIOR_RECORD_UNAUTHORIZED_CHANGE`, with `HELD-01` also failing coverage with `REQUIRED_CLAIM_OMITTED`; the single text false hold failed preservation in the same way. The pinned artifacts record these outcomes but do not establish their cause, and this chapter does not attribute the four graph false holds to serialization length, token budget or any other mechanism.

The semantic condition behaved in the opposite direction on false holds and exposed an annotation gap. It accepted all seven labelled-valid updates (7/7), recorded no false holds, and reached HOLD-only detection 5/6. Its single false accept was `HELD-13`, the held-out annotated-contradiction case that the plain text baseline also accepted; the artifact records that case's semantic check as `PASS` with `SEMANTIC_OK`. At the evaluated revision `4190526` the semantic module does not consume the `evidence.contradicts` field, so the semantic check added no contradiction signal beyond the deterministic text checks that already accepted the case. This is a documented integration gap in the evaluated revision, not a finding about semantic-model quality; it is the same annotation-access issue Section 4.3 identified in the rules-only comparison. Later revisions are outside the evaluated scope and are not reported here.

The conditions also differed in resource budgets, which matters for any comparison. Graph consumed 63,211 tokens with a mean 20,432.41 ms; text consumed 28,487 tokens with a mean 8,533.35 ms; semantic consumed 9,533 tokens with a mean 2,973.75 ms. On these cells the graph-to-text ratio is approximately 2.22 for tokens and 2.39 for mean latency, computed as descriptive arithmetic (Table 4.13). The conditions differ both in verifier construction and in token and latency budget, so the comparison is descriptive only and not an equal-budget comparison; no superiority claim is made and no statistical inference is justified at 13 executed rows per condition. With zero error rows, the as-completed and fail-closed ITT forms coincide; the held-out artifact does not publish a separate ITT block, and none is inferred here.

What this run establishes is narrow: the harness executed the three held-out conditions under a hard spend cap and produced complete decisions, and on this split the graph-LLM condition was conservative in the false-hold sense while the text-LLM condition was less so, with neither making a false accept. It does not establish that one condition is better; the labels are single-reviewer exploratory annotations, the split contains only 7 valid and 6 expected-`HOLD` cases, the two LLM conditions differ in budget, and the semantic condition contains a documented annotation gap. These outcomes are why RQ3 is treated descriptively in Section 4.5: acceptance, false holds, tokens and latency were measured, billed cost was unavailable, and downstream question answering was not measured.

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

## 4.5 RQ3 — Cost, Tokens, Latency and Acceptance Trade-Offs

RQ3 asks how verification affects valid-update acceptance, downstream question answering, latency, token usage and API cost, including graph-construction overhead. This chapter can answer the acceptance, false-hold, token, latency and estimated-cost parts descriptively; it cannot answer the question-answering part because no question-answering condition was run, and it cannot report a billed cost because the provider did not return one. Table 4.16 records which cost and token fields exist for each run, Table 4.15 reports the rules-only instrumentation, and Tables 4.13 and 4.14 report the held-out resource fields.

The rules-only conditions made no paid API calls. Both graph and text recorded zero input, output and total tokens and an estimated cost of 0.0 USD on the basis `rules_only_no_paid_calls`; unguarded recorded no token or cost block at all, which is expected because it performs no verifier call and must not be read as missing data (Table 4.16). The rules-run latency figures are instrumentation only and timer-noise dominated. End-to-end means over all 36 cases were 0.2498 ms for graph, 0.2933 ms for text and 0.1309 ms for unguarded; among the 26 schema-valid instrumented rows, graph preparation averaged 0.0425 ms and verification 0.1112 ms, while text preparation averaged 0.0575 ms and verification 0.1003 ms (Table 4.15). The split-level values move in both directions, consistent with the timer-noise caveat rather than a stable difference. Graph construction produced a mean of 9.62 nodes and 13.96 edges over the 26 schema-valid rows, a descriptive fact rather than a cost estimate. None of these timings is a performance finding: the fixtures are tiny, the protocol declares the measurement noise-dominated, and no claim that a condition is faster or cheaper is made on this basis.

The held-out model-based run produced the only meaningful token and latency telemetry (Table 4.13). It recorded 101,231 total tokens, of which 34,505 were input and 66,726 were output, across 54 rows with reported usage and 59 provider attempts; zero rows were charged the 2,500-token fallback, so the accounted total equals the reported total. Per condition, graph used 63,211 tokens over 13 executed rows and 16 attempts with a mean latency of 20,432.41 ms; text used 28,487 tokens over 13 rows and 17 attempts with a mean latency of 8,533.35 ms; semantic used 9,533 tokens over 13 rows and 26 attempts with a mean latency of 2,973.75 ms. The graph-to-text ratios from these cells are approximately 2.22 for tokens and 2.39 for mean latency, descriptive arithmetic rather than a tested difference. The run stayed inside its 250,000-token cap: it did not abort, recorded zero overshoot, and executed all 39 planned non-invalid rows (Table 4.14); it recorded zero cache hits and zero verifier errors. The semantic condition recorded 26 attempts; the artifact records attempts without a per-call decomposition, and this chapter does not infer one.

Monetary cost could not be established. The provider did not report billing data, the artifact records `billing_data_available: false`, and the only recorded monetary field is a mechanical estimate applying an assumed 0.10–2.00 USD per 1,000,000 accounted tokens to the token telemetry: 0.010123–0.202462 USD for the whole run, and 0.006321–0.126422 USD for graph, 0.002849–0.056974 USD for text and 0.000953–0.019066 USD for semantic (Table 4.13). Token counts are usage telemetry, not a billing record, and the estimate is a range, not a bill. A narrative report outside the JSON artifact mentions an approximate 0.40 USD upper bound at a top published per-token rate; that figure appears in no artifact, the artifact envelope at 2.00 USD per million tokens is 0.202462 USD, and neither figure is billed. The chapter reports the artifact range and records billing data as unavailable.

The acceptance trade-off is visible in the case-level outcomes of Section 4.4. In the rules-only run both guarded conditions accepted 14/14 labelled-valid updates with no false holds, while the unguarded reference applied all 26 schema-valid candidates. In the held-out run graph-LLM accepted 3/7 labelled-valid updates and held 4/7 with no false accept; text-LLM accepted 6/7 and held 1/7; semantic accepted 7/7 with one false accept on the annotated-contradiction case. Those counts describe a trade-off between committing valid updates and rejecting unverifiable ones, on 13 executed rows with single-reviewer labels. They do not show which operating point is preferable: graph was conservative in the false-hold sense, semantic permissive in the false-accept sense, budgets differed, and the labels are exploratory. No equal-budget comparison or efficiency ranking is claimed.

Downstream question answering was not measured, and nothing here infers it. The evaluation contains no question-answering dataset, retrieval pipeline or answer-quality scoring; acceptance is a safety decision about state faithfulness and prior-memory preservation, and it says nothing about whether a later answer built on that memory would be correct. RQ3 is therefore answered for acceptance, false holds, tokens, attempts, latency and an estimated unbilled cost range, with question-answering utility left as explicitly future work rather than estimated from acceptance.

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

## 4.6 RQ2 — Modularity Evidence

RQ2 asks whether the same verification logic can operate across different host memory backends through adapters, and what information or transformations each adapter requires and loses. The evaluated revision answers this only partially: it demonstrates an executable adapter contract and a guarded interception path on two reference backends, and it documents a source-inspection feasibility assessment for two third-party memory systems. Table 4.17 records each capability as demonstrated or not claimed, and Table 4.18 summarises the two inspections.

The adapter contract is a structural protocol with a stable `backend_name`, `snapshot(scope)` to convert host state into canonical `Record(id, claims)` values, and an atomic `replace_state(scope, records)` that replaces the entire scoped state and is a no-op for records it cannot validate. Scopes are plain string mappings, and adapters must not leak host-specific objects into verification or persistence. The contract requires adapters to preserve stable record ids and claim boundaries where the host provides them, to document provenance, timestamps, relationships, metadata and lost host-side effects, and not to call host mutation methods until verification returns `ACCEPT`. Evidence and proposed operations travel in the canonical transition, and adapters must not invent evidence, authorization or operation annotations. A guarded write path then follows a fixed order: capture the prior state, construct the canonical schema-version-2 transition with invalid input rejected before any write, verify through the audited run path, persist only on `ACCEPT` after re-snapshotting and comparing against the captured prior state, and append one decision record per proposal. `HOLD`, invalid input, stale prior state and a failed apply all leave host state untouched, or restore it when possible. In this sense the layer is modular: the verifier is not coupled to the reference backend, and the contract states the obligations a host must satisfy.

The reference backend demonstrates the contract end to end. `InMemoryBackend` and `SQLiteBackend` implement the protocol, with the SQLite backend replacing a scope inside one `BEGIN IMMEDIATE` transaction, and `GuardedMemory` implements the interception order over them. The reference-backend test module exercises the claimed behaviours: `ACCEPT` applies exactly the candidate; `HOLD` leaves in-memory records and the SQLite file bytes identical (SHA-256 compared before and after); undeclared changes and unauthorized deletes are rejected before any write; invalid candidates leave the backend identical; stale prior state is refused; and every outcome appends a decision record in order (Table 4.17). These are automated repository tests, not an evaluation run; they show the contract is implementable and fail-safe on the reference backends, not that a third-party host can be guarded unchanged. The reference integration also documents limits: the JSON baseline and host backend are separate writes with no distributed transaction, a crash between them can leave the baseline ahead of the host, and only adapters whose replacement is atomic or restorable can be labelled guarded.

The third-party assessment is source inspection, not integration. For Mem0, the inspected release was `mem0ai==2.2.1`, downloaded with `pip download --no-deps` and unpacked only; it was not installed, configured or exercised, no network or model call was made, and its metadata records Apache-2.0. The inspection found no public callback, transaction or pre-commit hook exposing the complete proposed transition: memory addition computes an extracted candidate set internally and then writes several stores sequentially, and update and delete mutate the vector store before writing history and entity-store links. The recorded decision is that no safe pre-commit hook exists in this release; a guarded integration would require a fork or host-owned staging pipeline that constructs and verifies the candidate before the write path, plus a pinned release and proof that a hold mutates nothing. For A-Mem, the inspected checkout was `agiresearch/A-mem` at commit `ceffb860f0712bbae97b184d440df62bc910ca8d` (a shallow commit, not a release pin), with MIT licence metadata. Its `add_note()` path calls `process_memory()` first, which can mutate neighbour notes before the new note is persisted, and public update writes the retriever by delete and re-add; the recorded decision is that integration is feasible only with a wrapper or fork that stages all new and neighbouring changes.

Two consequences follow for RQ2. First, the information an adapter must supply and what it may lose are explicit contract items: the adapter maps host state to canonical records and must document what it cannot represent, while evidence and operation declarations travel in the transition and must not be fabricated. The A-Mem assessment notes that content, timestamps, context, tags, links and evolution history would need explicit mapping, with losses such as absent source-span provenance unless the host supplies it. Second, this revision's evidence covers a contract plus a reference backend plus a feasibility assessment; the two reference backends implement the same contract rather than being independent third-party systems. It does not show that MemBud runs across three backends, that any third-party host is compatible, that no information is lost in a real mapping, or that the layer is production-ready; the three-backend evaluation remains future work, and none of those claims is made.

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

## 4.7 Summary of Findings

This chapter reported the outcomes of the pinned evaluated revision: the rules-only guarded-versus-unguarded and graph-versus-text comparisons with the information-parity audit, the held-out model-based conditions, and the cost, token, latency and modularity evidence relevant to RQ3 and RQ2. Table 4.19 collects the findings by research question with the claims that are not supported; this section states them in prose.

For RQ1a, the rules-only decision stream behaved as the design intends. Both guarded conditions accepted all 14 labelled-valid updates, recorded no false holds on those cases, classified every expected-invalid transition as `INVALID_INPUT` before verification (10/10 in each), and differed in how many expected-`HOLD` cases they committed: graph held all 12, while text held 10 and committed the two annotated-contradiction cases. The unguarded reference applied all 26 schema-valid candidates, including all 12 expected-`HOLD` candidates. These are exact counts on 36 single-reviewer synthetic cases; they do not estimate a population, establish causation, or support a superiority claim among verifiers.

For RQ1b, the paired rules-only comparison found two differing cases out of 36, `DEV-13` and `HELD-13`, both annotated unresolved contradictions where graph returned `HOLD` and plain text returned `ACCEPT`. The information-parity audit showed why: the plain text baseline does not read the `evidence.contradicts` annotations, and when a fair text control was given the same access it changed exactly those two decisions and agreed with the graph verifier on all 36 cases, with zero paired differences. The visible graph advantage is therefore attributable to annotation access rather than to structural inference, and it must not be described as graph catching contradictions that a text verifier could not. The zero difference is not equivalence: no decision-level representation advantage was detectable at this sample size on this instrument. In the held-out model-based run the picture was descriptive: graph-LLM was conservative in the false-hold sense (4/7 valid updates held, 0/6 false accepts, all 6 expected-`HOLD` cases detected), text-LLM held 1/7, and the semantic condition accepted all 7 valid updates and one annotated-contradiction case because the evaluated revision does not read `evidence.contradicts`. Conditions also differed in token and latency budgets, so no ranking is claimed.

For RQ2, the evaluated revision demonstrates a structural adapter contract and a guarded interception path on two reference backends, with automated tests covering `ACCEPT`-only persistence, byte-identical hold, pre-write rejection of invalid updates and decision records. It does not demonstrate a third-party integration: Mem0 2.2.1 and A-Mem were source-inspected and each was found to lack a safe public pre-commit boundary, so guarded integration would require host-owned staging or a fork. RQ2 is therefore answered as a contract-plus-reference-backend-plus-feasibility result, and the three-backend evaluation, third-party compatibility and production operation remain out of scope. For RQ3, acceptance, false holds, tokens, attempts, latency and an estimated unbilled cost range were recorded; the rules-run latency is timer noise and not a performance finding, the billed cost is unavailable and the artifact range is an estimate rather than a bill, and downstream question answering was not measured and is not inferred from acceptance.

The overall result is therefore descriptive. On a 36-case single-reviewer synthetic instrument the pipeline behaved as designed: both guarded conditions accepted the labelled-valid updates without false holds and rejected every expected-invalid transition before verification, and graph rejected all expected-`HOLD` cases while plain text rejected ten of twelve. The visible graph advantage over plain text was explained by annotation access, because a fair text control with the same access matched graph on every case; no representation-level advantage was detectable at this sample size. That is neither equivalence nor superiority. The chapter does not establish statistical significance or power, population estimates, causal effects, production readiness, third-party backend compatibility, billed monetary cost, or downstream question-answering utility. The labels are single-reviewer exploratory annotations with second review pending, and the cases were authored alongside the verifiers, so circularity cannot be ruled out. Chapter 5 takes up what the negative result means for the graph-memory hypothesis, why the plain-text comparison was misleading, and what evidence would be needed to move beyond this instrument.

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

*Table 4.19 lives in Section 4.7; Appendix notes for this chapter are in `paper/appendix-bab4-notes.md`.*
