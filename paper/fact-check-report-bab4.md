# Fact-check report — Bab 4 (Results), assembled draft v1

- **Task:** `task-7` — independent audit of the assembled Bab 4 (`paper/chapter-4-results.md`, 4.1–4.7 + Tables 4.1–4.19) against the pinned artifacts.
- **Auditor:** fact-checker-bab4 (independent of the writer).
- **Date:** 2026-11-07 (Asia/Jakarta).
- **Verdict:** **NOT READY** — every numeric table cell reproduces, but one reported metric is defined wrongly in prose/table meaning (blocker), one table column has a non-additive total (major), and several precision/consistency defects remain.
- **Severity summary:** **1 blocker · 1 major · 10 minor** (12 findings).
- **Pinned basis used:** rules-only run and fairness audit artifacts **and** docs read with `git show 1ce84ce:<path>`; held-out LLM/semantic artifacts recording code revision `4190526`. The moving HEAD (`62de491` at audit start) was never used. Post-evaluation artifacts (`llm-heldout-v2-rerun/`, `graph-llm-false-hold-analysis/`, `mem0-bridge.md`, sidecar/opencode code, `service.py`) are not cited as evidence in the chapter or its tables and were not used in this audit.
- **Method:** every numeric table cell was recomputed from the pinned JSON with `.venv/bin/python` (486 automated cell comparisons: 275 for Tables 4.3–4.7; 211 for Tables 4.1, 4.2, 4.8–4.16 and 4.19). Aggregates were independently re-derived from the raw per-case records (`data/results/evaluation-v2/{graph,text,unguarded}/audit.jsonl`, 36 rows each; `fairness-audit-v2/audit.json → $.cases`, 36; `llm-heldout-v2/summary.json → $.results`, 54) rather than trusted from the aggregate blocks. Tables 4.17–4.18 are non-numeric and were checked line-by-line against pinned docs and tests. Result: **0 numeric mismatches**; the two string-level differences initially flagged by the comparator were rate-suffix/prose formatting only, not data errors.

---

## Findings

| ID | Severity | Location (section + quoted cell/phrase) | Problem | Evidence | Suggested fix |
|---|---|---|---|---|---|
| **F-01** | **Blocker** | Table 4.2, row `\| Case-level error \| case \| 12 \| 36 \| 12/36 = 0.3333 \| cases with at least one relevant candidate error \|` (L49); §4.1 L16 *"At the case level, 12 of 36 cases contained at least one relevant candidate error"*; Table 4.2 *Note* L53 *"They are not a count of 'faulty cases' in the sense of Table 4.4 or Table 4.6"*; Table 4.2 *Source* L51 *"The denominators are the same set fixed in Table 3.7 and Section 3.2.6"* | The **number** matches the artifact, but the **meaning attributed to it is wrong**. The artifact's `candidate_error_rates.case_error` is *not* computed from candidate slots: `src/membud/metrics.py` L485–L488 counts every label with `expected_failed_checks` truthy, **else every label with `expected_decision == "HOLD"`**; the labels file has no `expected_failed_checks` key at all, so the value is exactly the 12 expected-`HOLD` labels. Recomputing a genuine case-level candidate-error count with the chapter's own claim/record-slot procedure gives **14 of 36** cases (the 10 expected-`HOLD` cases `DEV-08`–`DEV-12` and `HELD-08`–`HELD-12`, plus the 4 invalid cases `DEV-14`, `DEV-18`, `HELD-14`, `HELD-18`), not 12; and only **10 of the 12** expected-`HOLD` cases carry such an error (`DEV-13`/`HELD-13` carry none). The note is therefore also false: 12 **is** the HOLD-only faulty count of Table 4.4/4.6. Finally the row contradicts the Chapter 3 definition it claims to share (`Table 3.7`: *"cases with at least one relevant error / cases with an applicable denominator"*). | Pinned `metrics.json → $.candidate_error_rates.case_error = {numerator: 12, denominator: 36, rate: 0.3333}`; pinned `src/membud/metrics.py` L472–L494 (identical at `4190526` and `1ce84ce`); label keys contain no `expected_failed_checks`; recomputation listed below. | Treat the row as a label census, not an error rate: either (a) relabel it — *"labels with `expected_decision = HOLD` (the artifact's `case_error` implementation; `expected_failed_checks` is absent from the labels)"* — delete the "at least one relevant candidate error" wording from the prose and note, and drop the "same denominators as Table 3.7" claim; or (b) remove the row from Table 4.2 and, if a real case-level candidate-error count is wanted, report the derived **14/36** (any omission/unsupported-addition/prior-record-corruption error) with the formula and a *derived* label, plus note that 10 of the 26 schema-valid cases are affected. Apply the same change in `paper/results-tables.md`; consider adding a source note that the artifact's `case_error` implementation deviates from the protocol definition. |
| **F-02** | **Major** | Table 4.13, column *"Rows with reported usage"* (L266–L269): per-condition `13`, `13`, `13`, total row `54`; Table 4.13 *Note* L273 | The column total is not the sum of its parts: 13 + 13 + 13 = **39**, yet the table prints **54** with no explanation. Both numbers are genuine artifact fields but under different definitions: per condition, `condition_metrics.*.usage.reported.rows_with_reported_usage` counts the 13 attempted/executed rows; the run-level `$.run.usage.reported.rows_with_reported_usage` counts all 54 rows because each of the 15 `INVALID_INPUT` rows also carries an all-zero `usage` record with `reported: true`. A reader cannot reconcile the total from the table. | Pinned `llm-heldout-v2/metrics.json` per-condition values `{graph: 13, text: 13, semantic: 13}`, run value `54`; `summary.json → $.usage.reported.rows_with_reported_usage = 54`; all 15 invalid rows have `usage.reported = true` and zero tokens. | Add the denominator distinction to the note: *"Per-condition cells count rows with reported usage among the 13 executed rows per condition; the total is the run-level field over all 54 rows, because the 15 `INVALID_INPUT` rows also carry a zero-token usage record."* Alternatively use 18/18/18/54 consistently. Apply in `paper/results-tables.md` too. |
| **F-03** | Minor | §4.5 L307: *"that figure appears in no artifact"* | Self-contradictory wording: the same sentence says a narrative report mentions the ≈$0.40 figure, and it does appear in pinned committed docs (`docs/llm-heldout.md` L14: *"≈$0.40 at DeepSeek's top published per-token rate"*; `docs/technical-report-milestone-2.md` L164). Table 4.16's note already has the precise wording ("no JSON artifact"). | Pinned `docs/llm-heldout.md` L14; pinned `docs/technical-report-milestone-2.md` L164; chapter L342. | *"…that figure appears in no JSON artifact and in no billing record."* |
| **F-04** | Minor | Table 4.16, `rules graph`/`rules text` rows, column *"Cache hits"* = `0` (L335–L336) | Not an artifact field for the rules run: `condition_metrics.*.llm_usage` has no cache-hit key (fields are `input_tokens`, `output_tokens`, `total_tokens`, `api_attempts`, `json_retries`, `response_count`, `usage_reported_count`). Zero is true by construction (no provider calls), but the cell is presented as a recorded artifact value without a derivation. | Pinned rules `metrics.json → $.condition_metrics.graph.llm_usage` keys; `$.condition_metrics.*.estimated_cost.basis = rules_only_no_paid_calls`. | Mark as derived/expected: *"0 (no provider calls)"* or `—`; or drop the column for rules rows. |
| **F-05** | Minor | Table 4.11, `CONTRADICTED_CLAIM` / text FAIL = `0` (L223) | The artifact's text `reason_code_counts.fail` *omits* the key (absence, not a recorded zero). Reporting 0 is defensible as derived from absence but is not an artifact value; the table's source note does not mark it. | Pinned `metrics.json → $.condition_metrics.text.reason_code_counts.fail` = `{PRIOR_RECORD_UNAUTHORIZED_CHANGE: 4, REQUIRED_CLAIM_OMITTED: 8, UNSUPPORTED_CLAIM: 8}` (no `CONTRADICTED_CLAIM`); graph has it = 2. | Add "*text: key absent in the artifact, shown as 0*" (or mark the cell derived). |
| **F-06** | Minor | Table 4.2, row *Required-claim omission*, denominator-meaning cell (L46): *"required supported claims expected in the candidate"* | Mismatches the artifact and Chapter 3: the denominator is **all** required claims (68), not only "supported" ones — `metrics.py` uses `len(required)`; Chapter 3 Table 3.4 says *"required claims"* and Table 3.7 denominator *"all required claims"*. (The evidence map's *"required supported claims"* denominator is itself imprecise.) | Pinned `metrics.py` L479–L480, L491; artifact `omission = 12/68`; Ch3 Table 3.4/3.7. | *"all required claims (68)"*. |
| **F-07** | Minor | Chapter header L3: *"Pinned evaluated revision: rules-only run and fairness audit at commit `1ce84ce`"* vs §4.1 L20: *"the rules run is not claimed to be pinned to that commit"* | Internal pin-attribution inconsistency: the rules-run artifact records `code_revision = working-tree-sha256:044a09c9…`, not a git commit; the pin applies to the committed artifact set, not the run's code revision (as §4.1 correctly says). | Pinned `run-config.json`/`metrics.json → $.code_revision`; `paper/results-tables.md` L5/L388. | Reword L3: *"rules-only run and fairness audit **artifacts** pinned at `1ce84ce`; the rules run itself records a working-tree digest (see §4.1)"*. Keep §4.1 unchanged. |
| **F-08** | Minor | §4.1 L10 duplicates the chapter roadmap already given at L5 | Two near-identical introductory roadmaps on the same page (both list 4.2→4.7 and the pinned artifact sets). Assembly-duplication, not a factual error. | Chapter L5 vs L10. | Merge into one opening paragraph; keep the §4.1 opening focused on the instrument. |
| **F-09** | Minor | §4.1 L20: *"Every table cell in the appendix is traceable to a committed JSON pointer"* | Overstated: Tables 4.17–4.18 cells trace to pinned Markdown docs (`data/adapter-contract.md`, `data/backend-feasibility.md`) and to repository tests (`tests/test_reference_backend.py`), not to JSON pointers; a few cells (F-04, F-05) are derivations. | Table 4.17/4.18 *Source* lines; `paper/results-tables.md` §4.6. | *"Every numeric cell traces to a committed artifact (JSON, pinned doc, or repository test), with derived cells marked."* |
| **F-10** | Minor | §4.4 L242: *"no false-accept cost in either LLM condition"* | Ambiguous and in tension with the next paragraph: the semantic condition is also a model-based condition and has **1** false accept (`HELD-13`). The phrase intends only the graph-LLM and text-LLM verifier conditions. | Chapter L242 vs L244 (semantic `1/6` false accepts); Table 4.12. | *"…no false-accept cost in either the graph-LLM or text-LLM condition (the semantic condition's single false accept is reported below)."* |
| **F-11** | Minor | §4.6 L356 *"the two reference backends"* (also §4.7 L396, Table 4.19); vs Chapter 3 §3.1 L41 *"one reference backend"* | Cross-chapter terminology inconsistency: Chapter 4/Table 4.17/evidence-map describe **two** reference backends (in-memory, SQLite); Chapter 3 §3.1 says **one** reference backend (while §3.2.5 says "in memory and in SQLite"). Not a factual error in Chapter 4, but the chapters should agree. | Ch3 L41 vs Ch3 §3.2.5; Ch4 L348/L356/Table 4.17; `evidence-map.md` §12. | Align wording cross-chapter: *"one reference backend adapter with in-memory and SQLite implementations"* (Chapter 3 fix) or *"two reference backend implementations"* consistently. |
| **F-12** | Minor | Table 4.17 row *"Third-party backend integration or compatibility (Mem0, A-Mem, Zep/Graphiti)"* (L369) | Zep/Graphiti is named but is not covered by the cited pinned sources (`data/backend-feasibility.md` L3, L7–L9, L78–L80 and `data/adapter-contract.md` L1–L7, L127–L129 discuss only Mem0/A-Mem). The non-claim is true (no integration exists), but the citation does not support the third name. | Pinned `data/backend-feasibility.md`; `paper/evidence-map.md` §12 (Zep/Graphiti = `TODO.md`/`PLAN.md`). | Drop "Zep/Graphiti" from the row, or cite `TODO.md`/`PLAN.md` as the source of the non-claim. |

---

## Blocker detail — Table 4.2 `case_error` is a label census, not a case-level error rate

Recomputation from the frozen files (pinned `git show`), using the chapter's own claim/record-slot definitions:

```
$ .venv/bin/python  # (git show 1ce84ce:<path> for every file)
artifact candidate_error_rates.case_error = {'numerator': 12, 'denominator': 36, 'rate': 0.3333333333333333}
labels count = 36   expected-HOLD labels = 12
cases with >=1 omission/unsupported/corruption error = 14
   ['DEV-08','DEV-09','DEV-10','DEV-11','DEV-12','DEV-14','DEV-18',
    'HELD-08','HELD-09','HELD-10','HELD-11','HELD-12','HELD-14','HELD-18']
   of which expected-HOLD: DEV-08..DEV-12, HELD-08..HELD-12  (10)
   expected-HOLD cases WITHOUT such an error: DEV-13, HELD-13
labels keys: case_id, category, corrupted_prior_record_ids, expected_decision,
             expected_invalid, fault_types, label_status, notes, split,
             valid_prior_records_expected_to_persist
expected_failed_checks present in any label: False
```

Why the artifact says 12: `src/membud/metrics.py` (pinned; identical at `4190526` and `1ce84ce`):

```python
if label.get("expected_failed_checks"):
    case_error_count += 1
elif label["expected_decision"] == "HOLD":
    case_error_count += 1
...
"case_error": _rate(case_error_count, len(cases)),
```

With no `expected_failed_checks` field, the function counts the 12 expected-`HOLD` labels. The same count is the HOLD-only faulty denominator in Table 4.4/4.6, so the note *"They are not a count of 'faulty cases' in the sense of Table 4.4 or Table 4.6"* is not just imprecise — it is false. The cell's value is right; its definition and the prose around it are wrong, which is why this is classed a blocker rather than a numeric mismatch.

---

## Verified clean (checked, no finding)

### Pinned-source discipline
- All eight table-bearing artifacts are byte-identical to `git show 1ce84ce:<path>` (SHA-256 compared): manifest `e1adf173…527a`, cases `40558746…432d`, labels `9280d3e8…9a14`, rules `metrics.json` `1239e13d…c680`, `run-config.json` `c4c5aec8…d759`, fairness `audit.json` `a3b4b096…2515`, held-out `summary.json` `9a8ee5ea…f66`, held-out `metrics.json` `c4490aa2…290a`.
- Rules-run provenance stated correctly in the chapter body: `code_revision = working-tree-sha256:044a09c92325d90499c00616222144c5d0ea81d59b2ef5c769c71fbdebd92bfc` is a working-tree digest, not a commit (§4.1 L20), matching `run-config.json`/`metrics.json`. (Header wording is F-07.)
- Held-out run attributed to `41905266e29a4ae699655d8b66e92474a4ee8921` (`4190526`) in `4.4`, Table 4.14 and the header, matching `summary.json`/`metrics.json → $.code_revision`.
- Semantic annotation gap pinned correctly: `src/membud/semantic.py` at `4190526` builds `evidence_claims` from `evidence.supports` only (`_semantic_check`), so it does not consume `evidence.contradicts`; the artifact records `HELD-13` semantic `ACCEPT` with `SEMANTIC_OK`. The chapter says exactly this and confines later revisions to outside the evaluated scope.
- No post-evaluation artifact (`llm-heldout-v2-rerun`, `graph-llm-false-hold-analysis`, `mem0-bridge.md`, sidecar/opencode code, `service.py`) appears in the chapter or tables.

### Table 4.1 — instrument census (all cells checked against pinned manifest/cases/labels)
- `membud-evaluation-v2` / schema 2 / `frozen_exploratory_single_reviewer_second_review_pending`; 36 cases; 18 dev (`DEV-01`–`DEV-18`) / 18 held-out (`HELD-01`–`HELD-18`); pooled 14 `ACCEPT` / 12 `HOLD` / 10 `INVALID_INPUT`; 7/6/5 per split; 18 categories × 2 cases (7 valid + 6 faulty + 5 invalid families); invalid cases `DEV-14`–`DEV-18`, `HELD-14`–`HELD-18`; `schema_version` 2 on 36/36 cases and absent from the labels file; hand-authored fixed candidates; `input_labels_separated: true`; `reviewer_count 1`, `second_independent_reviewer false`, `second_review_status pending_human_review`, all 36 `label_status = single_reviewer_second_review_pending`; notes[0] synthetic/no-HaluMem and notes[4] authored-together/frozen-before-rules-run; both file digests match the manifest pins.

### Table 4.2 — candidate errors (except the F-01/F-06 defects)
- Omission `12/68 = 0.1765`, unsupported addition `8/64 = 0.1250`, prior-record corruption `4/40 = 0.1000`, case-level `12/36 = 0.3333` all equal the artifact; the first three also reproduce from the frozen cases/labels (12/68, 8/64, 4/40) with the Chapter 3 method.
- Fault-type sentence verified: 28 assignments across the 22 labels that carry fault types (12 `HOLD` + 10 `INVALID_INPUT`); breakdown omission 8, undeclared state change 6, corruption 4, unauthorized deletion 4, unsupported addition 2, unsupported provenance 2, unresolved contradiction 2; the 14 `ACCEPT` labels carry none.

### Tables 4.3–4.7 — rules-only decision stream (275 cell checks, 0 mismatches; independently recomputed from 36 raw audit rows per condition)
- Table 4.3: graph 14/12/10 (7/6/5 per split), text 16/10/10 (8/5/5 per split), unguarded APPLIED 26 / INVALID_INPUT 10 (13/5 per split), all `ERROR` 0, totals 36/18.
- Table 4.4 (as-completed, HOLD-only): graph 12/12 detection, 0/12 false accepts, 14/14 valid acceptance, 0/14 false holds; text 10/12 = 0.8333 detection, 2/12 = 0.1667 false accepts, 14/14 valid, 0/14 false holds; per split 6/6 graph, 5/6 text with one false accept per half. Derived precision cells (12/12, 10/10, 5/5) correctly marked as arithmetic.
- Table 4.5 (fail-closed ITT): rows coincide with Table 4.4 because error rows and error-holds are 0; `held_with_decision` 12 graph / 10 text, accepted 0 / 2, not-accepted 12/12 / 10/12; correct note that the artifact's ITT `detection_rate` numerator is `held_with_decision`.
- Table 4.6 (combined `faulty_detection`): 22 pooled / 11 per split; graph TP 22, FP 0, FN 0, precision 22/22, recall 22/22, FA 0/22; text TP 20, FP 0, FN 2, precision 20/20, recall 20/22 = 0.9091, FA 2/22 = 0.0909; per split 11/11 graph and 10/11 text; error rows 0. FN ids `DEV-13`, `HELD-13` confirmed.
- Table 4.7: expected-invalid 10/10 classified in both guarded conditions and unguarded; schema-accepted expected-invalid 0; unexpected-invalid rows 0/26 (0/13 per split); 10 `INVALID_INPUT` rows; unguarded "not separately recorded" is correct (the artifact publishes only `construction_failures_by_condition.unguarded = 10`).
- Aggregate blocks re-derived from the raw `audit.jsonl` rows: decision counts, split counts, HOLD-only detection/false accepts/valid acceptance, combined TP, invalid rows, and reason codes (`checks_observed` 78; FAIL counts 2/4/8/8 graph, 4/8/8 text; `uncertain` empty).

### Tables 4.8–4.11 — paired differences and fairness audit (cross-checked from `audit.json → $.cases`, 36 rows)
- Table 4.8: `graph_vs_text` differing cases 2/36 (`DEV-13`, `HELD-13`), graph `HOLD` vs text `ACCEPT`, expected `HOLD`, category `contradiction_unresolved`; 5.5556% derived; the quoted artifact note is verbatim.
- Table 4.9: all four strategies reproduce exactly — unguarded `ACCEPT 26 / HOLD 0 / INVALID_INPUT 10`, FA 12/12, FH 0/14, valid accepted 14/14 (raw application), label agreement 24/36 = 0.6667; text 16/10/10, FA 2/12 (`DEV-13`,`HELD-13`), 34/36 = 0.9444; `text_fair` and graph 14/12/10, FA 0/12, FH 0/14, 14/14, 36/36; `reads_contradicts` flags correct; the unguarded `ACCEPT` reading is correctly flagged as raw application, and the `docs/fairness-audit.md` "—" rendering is correctly explained.
- Table 4.10: `graph_vs_text` 2/36, `text_vs_text_fair` 2/36 (`text_fair` adds FAIL `CONTRADICTED_CLAIM` with `COVERAGE_OK`/`PRESERVATION_OK`), `text_fair_vs_graph` 0/36 (`[]`); graph records `CONTRADICTED_CLAIM` on both differing cases, text records none. The "all 36 / also all 26 non-invalid" distinction is stated correctly.
- The two graph-vs-text differences are consistently accompanied by the fairness-audit explanation wherever they are used as a difference (4.3, 4.7, Table 4.19).

### Tables 4.12–4.14 — held-out LLM/semantic run (cross-checked from `summary.json → $.results`, 54 rows + metrics)
- Table 4.12: graph 3/10/5, text 6/7/5, semantic 8/5/5; false accounts 0/6, 0/6, 1/6 (`HELD-13`); false holds 4/7 (`HELD-01/02/03/07`), 1/7 (`HELD-03`), 0/7; valid accepted 3/7, 6/7, 7/7; HOLD-only detection 6/6, 6/6, 5/6; combined 11/11, 11/11, 10/11; case coverage 13/13 each; expected-invalid 5/5 each.
- Per-case check outcomes verified: all four graph false holds fail `preservation` with `PRIOR_RECORD_UNAUTHORIZED_CHANGE` and `HELD-01` also fails `coverage` with `REQUIRED_CLAIM_OMITTED`; text false hold only `HELD-03`; semantic `HELD-13` checks `coverage/preservation/faithfulness/semantic` all PASS with `SEMANTIC_OK`.
- Table 4.13: tokens 18,893/44,318/63,211 (graph), 9,430/19,057/28,487 (text), 6,182/3,351/9,533 (semantic), total 34,505/66,726/101,231; attempts 16/17/26 (59); mean latencies 20432.41176923077 / 8533.353076923077 / 2973.7456923076925 ms (recomputed as means over the 13 executed rows each); cost ranges 0.006321–0.126422 / 0.002849–0.056974 / 0.000953–0.019066, total 0.010123–0.202462 USD; `billing_data_available` false. Ratios 2.2189 (tokens) and 2.3944 (latency) correct. (The "Rows with reported usage" column is F-02.)
- Table 4.14: run id, revision, split 18 = 13+5, 54 total rows / 39 executed / 15 `INVALID_INPUT`, planned/executed 39/39, 101,231 reported = accounted tokens (0 fallback rows), cap 250,000 / not aborted / overshoot 0, 59 attempts, 0 cache hits, 0 verifier errors, 0 semantic-uncertain rows (abstention 0/13), model/provider/prompt versions — all match `summary.json`/`metrics.json`.

### Tables 4.15–4.16 — rules latency/graph size and cost fields
- Table 4.15: all pooled and per-split means reproduce exactly (end-to-end 0.24980555555555556 / 0.29330555555555554 / 0.1308888888888889 ms; graph prep 0.04246153846153847, verification 0.11123076923076923, nodes 9.615384615384615, edges 13.961538461538462; text prep 0.05746153846153846, verification 0.10026923076923076; dev/heldout values likewise); token blocks 0 and estimated cost 0.0 on `rules_only_no_paid_calls`; unguarded correctly "no token/cost block". Timer-noise framing is consistent with the protocol and pinned reports, and no performance claim is made.
- Table 4.16: rules graph/text 0 paid calls, 0 tokens, 0.0 USD on `rules_only_no_paid_calls`; unguarded no block (expected, not missing); held-out 101,231 tokens (34,505 in / 66,726 out), range 0.010123–0.202462 USD with `estimated: true`, `billing_data_available: false`, 59 attempts, 0 cache hits. Billed cost is consistently reported as unavailable, and ≈$0.40 is never presented as a bill or JSON-artifact value.

### Tables 4.17–4.18 — RQ2 evidence (non-numeric; verified against pinned docs/tests)
- Adapter contract statements match pinned `data/adapter-contract.md` §1–§2 and §4 (structural protocol; `snapshot` / atomic `replace_state`; interception order; ACCEPT-only persistence; re-snapshot/staleness check; decision record; known limits — no distributed transaction, crash window, atomicity requirement).
- All named tests exist in pinned `tests/test_reference_backend.py`: `test_accept_applies_exactly_the_candidate`, `test_hold_leaves_sqlite_backend_bytes_identical`, `test_hold_leaves_in_memory_backend_records_identical`, `test_invalid_candidate_is_rejected_and_leaves_backend_identical`, `test_undeclared_state_changes_are_rejected_before_any_write`, `test_unauthorized_delete_is_rejected_before_any_write`, `test_delete_authorization_must_reference_transition_evidence`, `test_decision_log_appends_every_outcome_in_order` (plus `test_stale_prior_state_is_refused_before_persisting`).
- Mem0 facts match pinned `data/backend-feasibility.md` L23–L50 (release `mem0ai==2.2.1`, wheel SHA-256 `fe91bb91…eef4d`, `pip download --no-deps`, unpacked only, no install/runtime call, Apache-2.0, sequential multi-store writes, no public pre-commit hook, decision). A-Mem facts match L52–L69 (commit `ceffb860f0712bbae97b184d440df62bc910ca8d`, shallow HEAD, MIT, `add_note()`→`process_memory()` neighbour mutation, public update delete/re-add, staging wrapper/fork required, explicit mapping/loss note). Line-range citations L3, L7–L9, L11–L21, L13–L21, L71–L80, L78–L80 resolve against the pinned 88-line document; the working-tree copy's post-pin appendix starts at L89, so the cited ranges are unaffected.
- RQ2 scope is stated correctly everywhere: two reference backends + contract + source-inspection feasibility; no third-party integration/compatibility, no three-backend evaluation, no production claim. (F-12 flags only the Zep/Graphiti citation.)

### Table 4.19 — summary
- Every numeric claim reproduces: 12/12 graph and 10/12 text HOLD-only detection; unguarded 12/12 raw false-accept-equivalent; 2/36 paired differences with `DEV-13`/`HELD-13` and 0/36 `text_fair` vs graph; graph-LLM 4/7 false holds and 0/6 false accepts, text-LLM 1/7, semantic 7/7 valid accepted and `HELD-13` false accept; estimates/rules-latency/billed-cost caveats consistent with the tables.

### Statistics / inference guardrails
- No superiority, significance, power, confidence-interval, multiplicity, causal, population, production-readiness, third-party-compatibility, equivalence or downstream-QA claim; the only occurrences of those words are explicit negations or scope limits.
- "not detectable at this sample size / n" is used consistently for the zero `text_fair`-vs-graph difference; never "equivalent".
- Every graph-vs-text difference is paired with the fairness-audit explanation (annotation access, not structure; `text_fair` vs graph 0/36); the graph verifier is never said to catch contradictions a fair text verifier cannot, and the structural-checks capability is described as unmeasured.
- LLM conditions are declared non-equal-budget in `4.4`, `4.5`, `4.7` and Table 4.19.
- Labels are consistently single-reviewer exploratory with second review pending; "gold" never appears; cases-authored-alongside-verifiers circularity is stated.
- Rules latency is consistently timer-noise/instrumentation-only; billed cost unavailable; token counts are telemetry; ≈$0.40 is never a bill.
- RQ3 downstream QA is explicitly not measured and not inferred; RQ2 is scoped to contract + reference backend + feasibility.

### Denominator discipline (other than F-01)
- HOLD-only denominators (12 pooled, 6 held-out, 7 valid), combined (22 pooled / 11 per split, 11 held-out), and claim/record-slot candidate-error denominators are defined wherever used and never silently merged.
- As-completed and fail-closed ITT are reported separately (Tables 4.4/4.5) and their coincidence here is explicitly attributed to zero error rows; `INVALID_INPUT` is kept as a separate block (Table 4.7) and never credited as detection.

### Chapter 3 consistency
- Metric vocabulary matches Table 3.6 (`ACCEPT`, `HOLD`, `INVALID_INPUT`, `ERROR`, `APPLIED`) and §3.2.6; 26 schema-valid = 36 − 10, 10 invalid, 14/12/10 label census and 7/6/5 per split all agree. The only §3.2.6/Table 3.7 contradiction found is the case-level error definition (F-01) and the omission denominator wording (F-06).

### Structure
- H1 chapter heading; H2 for 4.1–4.7, sequential; Tables 4.1–4.19 each defined exactly once, sequential, and each referenced from the text (Table 4.3 via the range "Tables 4.3–4.7"); all Section/Table cross-references resolve; the 19 chapter tables match the authoritative `paper/results-tables.md` table-for-table (including the split 4.4/4.5 blocks). Markdown column counts are consistent in every table.

### Writer's known source-level discrepancies (confirmed handled correctly)
1. `docs/technical-report-milestone-2.md` §7 "both conditions detect 12/12, 0 false accepts" — the chapter follows the artifact: text is 10/12 with 2/12 false accepts in Table 4.4, §4.7 and Table 4.19; the 12/12 claim is not repeated.
2. `docs/fairness-audit.md` prints unguarded "Valid accepted" as "—" — Table 4.9 uses the audit's 14/14 with the explicit "raw applied; not verifier acceptance" qualifier and the note explains the "—" rendering; both are consistent.
3. Rules-run `code_revision` is a working-tree digest — stated correctly in §4.1 L20 and in the `paper/results-tables.md` source notes; the chapter does not claim the rules run is pinned to a commit (header wording is F-07).

---

## Reproduction commands used

Artifacts were read from the pinned commit, never from the moving HEAD:

```bash
cd /home/ifan/Projects/MemBud
git show 1ce84ce:data/results/evaluation-v2/metrics.json
git show 1ce84ce:data/results/fairness-audit-v2/audit.json
git show 1ce84ce:data/results/llm-heldout-v2/{metrics,summary}.json
git show 1ce84ce:data/manifests/evaluation-v2.json
git show 1ce84ce:data/cases/evaluation-v2.jsonl
git show 1ce84ce:data/labels/evaluation-v2.jsonl
git show 1ce84ce:data/results/evaluation-v2/{graph,text,unguarded}/audit.jsonl
git show 1ce84ce:data/results/evaluation-v2/run-config.json
```

Cell checks (illustrative; the full script ran all 486 comparisons, and aggregates were re-derived from the raw rows):

```bash
.venv/bin/python - <<'PY'
import subprocess, json, collections
def pj(p): return json.loads(subprocess.check_output(['git','show','1ce84ce:'+p], text=True))
def rows(p):
    t = subprocess.check_output(['git','show','1ce84ce:'+p], text=True)
    return [json.loads(x) for x in t.splitlines() if x.strip()]
m = pj('data/results/evaluation-v2/metrics.json')
a = pj('data/results/fairness-audit-v2/audit.json')
l = pj('data/results/llm-heldout-v2/metrics.json')

# Table 4.3 / 4.4 / 4.5 / 4.6 / 4.7 aggregates
print(m['condition_metrics']['graph']['decision_counts'])
print(m['condition_metrics']['text']['as_completed']['faulty'])
print(m['condition_metrics']['text']['fail_closed_itt']['faulty'])
print(m['condition_metrics']['text']['faulty_detection'])
print(m['condition_metrics']['graph']['invalid_input'])

# Recompute decision counts and HOLD-only detection from the raw 36-row audit logs
labels = {x['case_id']: x for x in rows('data/labels/evaluation-v2.jsonl')}
for cond in ('graph', 'text'):
    rs = rows(f'data/results/evaluation-v2/{cond}/audit.jsonl')
    print(cond, collections.Counter(r['decision'] for r in rs),
          'detected', sum(1 for r in rs if labels[r['input']['case_id']]['expected_decision'] == 'HOLD'
                          and r['decision'] == 'HOLD'), '/',
          sum(1 for x in labels.values() if x['expected_decision'] == 'HOLD'))

# Tables 4.9 / 4.10 fairness
print(a['per_strategy']['text_fair']['false_accepts'], a['paired_differences']['text_fair_vs_graph'])

# Tables 4.12 / 4.13 / 4.14 held-out
print({c: l['condition_metrics'][c]['faulty_detection_hold_only'] for c in ('graph','text','semantic')})
print(l['run']['estimated_cost_range_usd'], l['run']['usage']['reported'], l['run']['budget'])
# summary-level totals and F-02 counter-example
s = pj('data/results/llm-heldout-v2/summary.json')
print(s['attempts'], s['usage']['estimated'], s['usage']['reported']['rows_with_reported_usage'])
print({c: l['condition_metrics'][c]['usage']['reported']['rows_with_reported_usage']
       for c in ('graph','text','semantic')})

# Tables 4.15 timing/graph size
print({c: m['condition_metrics'][c]['timing_ms'] for c in ('graph','text','unguarded')})
PY
```

The F-01 recomputation and F-02 counter-example shown above are exact outputs of these pinned reads.

---

## Verdict rationale

The chapter's numbers are exceptionally well traced: **no numeric table cell mismatched the pinned artifacts**, and all 19 tables match the authoritative `paper/results-tables.md` table-for-table. However, Table 4.2's fourth row turns the artifact's degenerate label-count field (`case_error` = number of expected-`HOLD` labels) into a claimed "cases with at least one relevant candidate error" rate, repeats that in prose, and states the opposite of the truth about its relation to the HOLD-only denominator; that is a substantive factual error in a results table and must be fixed before the chapter can be signed off. With that fixed and the major/minor items addressed, the chapter would be ready.
