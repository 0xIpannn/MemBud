# MemBud paper — canonical evidence map (fact sheet)

**Owner:** factkeeper (shared task `task-1`).
**Snapshot:** 2026-11-07, from 21:31 WIB (Asia/Jakarta), workspace `/home/ifan/Projects/MemBud`.
**Basis:** `main` @ `1ce84ce600ff20c90dc55539c6fa59eda22d40d2` (short `1ce84ce`), 3 commits ahead of `origin/main` (`944b87bf0405a9b136e36d91a2e8399cd7de07a0`), no tracked modifications, `paper/` untracked.
**Purpose:** the single source of facts and numbers for the paper. Section writers (Bab 3, and Bab 4/5 where they reference instrument facts) may use **only** rows marked `established` or `derived`. A number not in this file, or marked `pending` / `do-not-use`, must not appear in the draft.

Every row was verified by reading the artifact, parsing it with `.venv/bin/python` where it is JSON, or re-running the stated command — never by copying prose. `derived` rows show the formula; `pending` rows have no repository artifact yet; `do-not-use` rows are forbidden or stale.

> **Post-snapshot note (2026-11-07, updated ~22:10 WIB).** While this map was being used, a **concurrent session** advanced `main` through several commits: `60976ed` (Mem0 staged-proposal bridge Phase 0), `2b4642b` (semantic verifier now consumes `evidence.contradicts` + cached held-out rerun), `d518346` (graph-LLM false-hold analysis), `bac5adb` (localhost /verify sidecar + opencode guard), `2d45642` (post-Milestone-2 follow-up docs), `62de491` (opt-in live model-originated denial test). New artifacts (`data/results/llm-heldout-v2-rerun/`, `data/results/graph-llm-false-hold-analysis/`, `docs/graph-llm-false-holds.md`, `data/mem0-bridge.md`, sidecar/opencode code) were **not** used for this paper, and `docs/llm-heldout.md`, `docs/technical-report-milestone-2.md`, and `data/backend-feasibility.md` gained post-pin appendices. The previously committed artifacts cited in this map (rules run, fairness audit, original held-out LLM run, manifest, cases, labels) were **not modified**, so all rows above remain valid for the pinned revision; use `git show 1ce84ce:<path>` for pinned docs. **Paper basis:** rules/fairness at `1ce84ce`; held-out LLM run at recorded code revision `4190526`. Bab 3 and Bab 4 describe only the pinned revision; later HEAD behavior is outside the evaluated scope. (This note is a provenance record, not a fact row.)

## Status legend

| Status | Meaning | What a writer may do |
|---|---|---|
| established | Directly present in a committed artifact, verified against the artifact itself (JSON pointer/line given). | May state, with the given source. |
| derived | Arithmetic from established rows, or a narrative rounding whose formula is shown. Not an independent artifact value. | May state only with the formula/qualifier given; never as a measured artifact value. |
| pending | No repository artifact supports it yet, or the artifact exists but the work is incomplete. | Must not be written as a result. May be written only as explicitly-pending work. |
| do-not-use | Forbidden claim, stale number, or unsupported phrasing. | Must not appear anywhere. |

## Quick index

| § | Content |
|---|---|
| 1 | Repository and toolchain state |
| 2 | Frozen v2 instrument: manifest and hash pins |
| 3 | Dataset: cases and labels |
| 4 | Conditions and run configuration |
| 5 | Rules-only v2 results |
| 6 | Fairness audit (v2) |
| 7 | Held-out LLM and semantic run (v2) |
| 8 | Architecture and behavioral facts for Bab 3 |
| 9 | Backend and adapter facts |
| 10 | Licensing and external data |
| 11 | Metric and denominator definitions (protocol facts) |
| 12 | DO NOT CLAIM (forbidden claims; all rows do-not-use) |
| 13 | Stale / superseded / discrepancy register |
| 14 | Pending and unsourced facts |
| 15 | Verification log (commands and cross-checks) |

---

## 1. Repository and toolchain state

| Fact | Value | Source | Status |
|---|---|---|---|
| Current branch | `main` | `.git/HEAD` → `ref: refs/heads/main` | established |
| HEAD commit | `1ce84ce600ff20c90dc55539c6fa59eda22d40d2` (short `1ce84ce`, subject "Make the blind review quiz explain its concepts") | `.git/refs/heads/main`; `git rev-parse HEAD` | established |
| `origin/main` commit | `944b87bf0405a9b136e36d91a2e8399cd7de07a0` | `.git/refs/remotes/origin/main` | established |
| Commits ahead of `origin/main` | 3 (`4190526`, `dad4759`, `1ce84ce`) | `git rev-list --count origin/main..HEAD` = `3`; `git log --oneline -3` | established |
| Working-tree state | No tracked modifications or staged changes; only untracked `paper/` | `git status --porcelain` → `?? paper/` | established |
| Test suite at HEAD | 243 passed in 0.48 s | `.venv/bin/python -m pytest -q` (run at HEAD `1ce84ce` in this workspace, 2026-11-07; no committed test-count artifact exists) | established (reproduced) |
| Test modules | 13 files under `tests/` | `git ls-files tests` lists `test_evaluation_manifest.py`, `test_evaluation_v2.py`, `test_fairness_audit.py`, `test_graph_verification.py`, `test_llm_client.py`, `test_llm_heldout.py`, `test_llm_verification.py`, `test_mvp.py`, `test_reference_backend.py`, `test_review_quiz.py`, `test_semantic.py`, `test_storage.py`, `test_store_safety.py` | established |
| Python requirement | `requires-python = ">=3.11"` | `pyproject.toml` L5 | established |
| Test count in milestone-2 report | 202 passing | `docs/technical-report-milestone-2.md` L19 and L135 | do-not-use (stale at HEAD; see §13 S1) |
| Test count in local pilot report | 112 tests "as of" the pilot changes | `data/pilot-report.md` L17 | do-not-use (historical; see §13 S11) |

Notes for writers:
- The paper may say "the suite at commit `1ce84ce` contains 243 passing tests (reproduced 2026-11-07)". Do **not** cite 202 or 112.
- The full 40-character HEAD hash is the safest identifier; short form `1ce84ce` is acceptable if the full hash is given at first use.
- `paper/` is deliberately untracked in this workspace; it is not part of the reproducibility basis.

---

## 2. Frozen v2 instrument: manifest and hash pins

Manifest file: `data/manifests/evaluation-v2.json` (274 lines; file SHA-256 `e1adf173a3f84d4f8d4ca910bdcabb2e6dd69a0f64be4509395090f5507d527a`, recomputed and matched).

| Fact | Value | Source | Status |
|---|---|---|---|
| Manifest id | `membud-evaluation-v2` | `data/manifests/evaluation-v2.json` → `$.manifest_id` (L2) | established |
| Manifest schema version | `2` | manifest → `$.schema_version` (L3) | established |
| Manifest status | `frozen_exploratory_single_reviewer_second_review_pending` | manifest → `$.status` (L4) | established |
| Manifest purpose (abridged) | "Expanded 36-case synthetic controlled evaluation set … Not a representative benchmark; labels are single-reviewer and second human review is pending." | manifest → `$.purpose` (L5) | established |
| Reviewer count | 1 | manifest → `$.review.reviewer_count` (L7) | established |
| Second independent reviewer present | `false` | manifest → `$.review.reviewer_count` / `$.review.second_independent_reviewer` (L7–L8) | established |
| Second-review status | `pending_human_review` | manifest → `$.review.second_review_status` (L9) | established |
| Review process pointer | `../review/README.md`; review-sheet tool `scripts/review_cases.py` | manifest → `$.review.process`, `$.review.review_sheet_tool` (L10–L11) | established (tool pointer superseded by blind-quiz tool; see §13 S10) |
| Review limitation (verbatim) | "Cases and proposed labels were authored and reviewed by one reviewer (project owner) during milestone development. No second independent human annotation has been collected … Treat all labels as exploratory single-reviewer annotations." | manifest → `$.review.limitation` (L12) | established |
| Cases file | `../cases/evaluation-v2.jsonl` (= `data/cases/evaluation-v2.jsonl`) | manifest → `$.cases_file` (L14) | established |
| Case-file SHA-256 | `40558746e915c6f5296235109900c050da61f87fe4f4fe320bf0290af9aa432d` | manifest → `$.cases_sha256` (L15); recomputed from the file: identical | established |
| Labels file | `../labels/evaluation-v2.jsonl` (= `data/labels/evaluation-v2.jsonl`) | manifest → `$.labels_file` (L16) | established |
| Labels-file SHA-256 | `9280d3e8e2aa7a61df31bed689647f768a98361bb7509e7f8d172bb376989a14` | manifest → `$.labels_sha256` (L17); recomputed from the file: identical | established |
| Manifest SHA-256 (self pin) | `e1adf173a3f84d4f8d4ca910bdcabb2e6dd69a0f64be4509395090f5507d527a` | `data/results/evaluation-v2/metrics.json` → `$.manifest_sha256` (L3); `data/results/evaluation-v2/run-config.json` L3; `data/results/fairness-audit-v2/audit.json` → `$.manifest.sha256` (L2739); `data/results/llm-heldout-v2/summary.json` L6; recomputed from the file: identical | established |
| Case count | 36 | manifest → `$.case_count` (L56); also `$.case_entries` length 36 | established |
| Split counts | `dev`: 18, `heldout`: 18 | manifest → `$.split_counts` (L239–L242) | established |
| Expected decision counts | `ACCEPT`: 14, `HOLD`: 12, `INVALID_INPUT`: 10 | manifest → `$.expected_decision_counts` (L243–L247); cross-checked against labels file (identical counts) | established |
| Expected decision counts by split | `dev`: 7/6/5; `heldout`: 7/6/5 (ACCEPT/HOLD/INVALID_INPUT) | manifest → `$.expected_decision_counts_by_split` (L248–L258) | established |
| Expected-invalid cases | 10 (DEV-14…DEV-18, HELD-14…HELD-18) | manifest → `$.case_entries[*].expected_invalid == true` | established |
| Conditions declared in manifest | `unguarded`, `graph`, `text` | manifest → `$.conditions` (L260–L264) | established |
| Input/label separation | `true` | manifest → `$.input_labels_separated` (L265) | established |
| Candidate generation | "hand-authored fixed candidates; reused unchanged across conditions" | manifest → `$.candidate_generation` (L266) | established |
| Synthetic, no licensed data | "Synthetic Indonesian-themed fictional cases; no HaluMem or other licensed data is included." | manifest → `$.notes[0]` (L268) | established |
| Authored/frozen together | "The 36 cases were authored together and frozen before the v2 rules-based run; dev/held-out results are reported separately and no tuning on held-out outcomes was performed." | manifest → `$.notes[4]` (L272–L274) | established |
| Hash-pin chain complete | Manifest pins cases + labels; metrics pins manifest + pilot summary; run-config pins manifest; fairness audit and LLM summary/metrics pin manifest, cases, labels | metrics → `$.manifest_sha256` (L3), `$.summary_sha256` (L4); run-config L3; audit.json → `$.manifest` (L2725–L2740); summary.json L6; metrics L4 (LLM); cross-checks in §15 | established |

---

## 3. Dataset: cases and labels

| Fact | Value | Source | Status |
|---|---|---|---|
| Case file | `data/cases/evaluation-v2.jsonl`, 36 JSONL lines | file line count = 36; manifest `$.cases_file` | established |
| Case identifiers | `DEV-01`…`DEV-18`, `HELD-01`…`HELD-18` | manifest → `$.case_ids` (L18–L55) | established |
| Case schema version | every case carries `schema_version: 2` (36/36) | parsed `data/cases/evaluation-v2.jsonl`; manifest → `$.schema_version` | established |
| Case fields | `case_id`, `schema_version`, `before`, `candidate`, `evidence`, `required_claims`, `operations` | parsed case objects (keys verified) | established |
| Evidence fields | `id`, `supports`, `source`, `text`, `contradicts` (optional, defaults empty) | `src/membud/schemas.py` L44–L49 | established |
| Label file | `data/labels/evaluation-v2.jsonl`, 36 JSONL lines | file line count = 36; manifest `$.labels_file` | established |
| Label fields | `case_id`, `split`, `category`, `expected_decision`, `expected_invalid`, `fault_types`, `valid_prior_records_expected_to_persist`, `corrupted_prior_record_ids`, `label_status`, `notes` | parsed label objects (keys verified) | established |
| Label status value | `single_reviewer_second_review_pending` on all 36 labels | `data/labels/evaluation-v2.jsonl` → per-row `label_status`; all 36 identical | established |
| Label expected decisions | ACCEPT 14, HOLD 12, INVALID_INPUT 10 | parsed labels; matches manifest `$.expected_decision_counts` | established |
| Split assignment of labels | dev 18, heldout 18 | parsed labels; matches manifest `$.split_counts` | established |
| Category coverage | 18 categories × 2 cases = 36 | `data/results/evaluation-v2/metrics.json` → `$.case_category_counts` (L43–L62); parsed labels also give each category count = 2 | established |
| Non-invalid categories (13) | `valid_write`, `valid_revise`, `valid_supersede`, `valid_delete_authorized`, `valid_noop`, `valid_multi_op`, `valid_contradiction_correction`, `omission`, `corruption_no_evidence`, `corruption_insufficient_evidence`, `unsupported_addition`, `unsupported_provenance`, `contradiction_unresolved` | metrics `$.case_category_counts`; labels `category` | established |
| Invalid-input families (5) | `invalid_unauthorized_delete`, `invalid_delete_authorization_target`, `invalid_undeclared_insertion`, `invalid_undeclared_modification`, `invalid_undeclared_removal` (2 cases each) | metrics `$.case_category_counts`; labels `category` | established |
| Invalid-input mechanism | Schema construction rejects before verification; recorded as `INVALID_INPUT`; never writes memory | metrics `$.invalid_input_summary.note` (L63–L83); `data/experiment-protocol.md` L26 | established |
| Fault-type distribution (labels) | `omission` 8, `undeclared_state_change` 6, `corruption` 4, `unauthorized_deletion` 4, `unsupported_addition` 2, `unsupported_provenance` 2, `unresolved_contradiction` 2 | parsed `fault_types` across 36 labels | established |
| Dataset provenance | Synthetic Indonesian-themed, repository-authored; no licensed data committed | manifest `$.notes[0]`; `data/README.md` L6 | established |
| Unit of analysis | one transition case, paired across conditions; claims/records are not independent samples | `data/experiment-protocol.md` L32; metrics `$.interpretation` (L1537) | established |
| Archival v1 set | 12-case synthetic set, single reviewer, archived (`data/manifests/evaluation-v1.json`, etc.) | `data/README.md` L5 | established (archival; do not use for v2 claims — §13 S16) |
| v2 labels carry a `schema_version` field | **No** — labels JSONL has no `schema_version` field; only the cases (and manifest) do | parsed label keys (absent); parsed case keys (present) | established (precision note: say "cases carry schema_version 2", not "the labels file is schema_version 2") |

---

## 4. Conditions and run configuration

### 4.1 Rule-based (rules-only) v2 run

| Fact | Value | Source | Status |
|---|---|---|---|
| Run id | `evaluation-v2-rules` | `data/results/evaluation-v2/run-config.json` L5; `data/results/evaluation-v2/metrics.json` → `$.run_id` (L6) | established |
| Verifier | `rules` | run-config L7; metrics → `$.verifier` | established |
| Conditions run | `unguarded`, `graph`, `text` | run-config L8–L12; metrics → `$.condition_metrics` keys (L84) | established |
| Paid API calls | `0` | run-config L13 | established |
| Tokens / cost | 0 tokens (all three conditions; `llm_usage` and `token_usage` blocks all zero); estimated cost `0.0` USD, `billing_data_available: false`, basis `rules_only_no_paid_calls` | metrics → `$.condition_metrics.{graph,text,unguarded}.llm_usage`, `.estimated_cost` (e.g. L116–L142) | established |
| Rules code revision recorded as | `working-tree-sha256:044a09c92325d90499c00616222144c5d0ea81d59b2ef5c769c71fbdebd92bfc` (a working-tree digest, not a git SHA) | run-config L6; metrics → `$.code_revision` (L7) | established (reproducibility caveat §13 S9) |
| Pilot-summary pin | metrics records `summary_sha256 = fbc31a73caa86c1a6ca3379aac52d3f0b1965c4c9d75434dbfeca5545d305584`; recomputed SHA-256 of `data/results/evaluation-v2/pilot-summary.json` is identical | metrics → `$.summary_sha256` (L4) | established |
| Manifest pin | `e1adf173…` (see §2) | run-config L3; metrics → `$.manifest_sha256` (L3) | established |
| Raw per-condition artifacts | `data/results/evaluation-v2/{graph,text,unguarded}/` contain `audit.jsonl` plus per-case JSON stores | directory listing; `data/pilot-report.md` L47 | established |

### 4.2 Held-out LLM/semantic run

| Fact | Value | Source | Status |
|---|---|---|---|
| Run id | `llm-heldout-v2-deepseek-flash` | `data/results/llm-heldout-v2/summary.json` → `$.run_id` (L2); metrics → `$.run_id` (L2) | established |
| Generated at | `2026-11-07T13:03:13.469188+00:00` | summary → `$.generated_at` (L3); metrics → `$.generated_at` (L12) | established |
| Split | `heldout` only (18 cases: 13 schema-valid, 5 expected-invalid) | summary → `$.split` (L10), `$.case_count` (L16), `$.valid_case_count` (L17), `$.invalid_case_count` (L18) | established |
| Conditions | `graph`, `text`, `semantic` | summary → `$.conditions` (L11–L15); metrics → `$.condition_metrics` (L13) | established |
| Model id | `deepseek-flash` | summary → `$.model` (L19); `src/membud/llm/client.py` L54 (`DEEPSEEK_MODEL`) | established |
| Provider | `deepseek-openai-compatible` | summary → `$.provider` (L20) | established |
| Prompt versions | LLM: `membud-transition-checks-v1`; semantic: `membud-semantic-compare-v1` | summary → `$.prompt_versions` (L24–L27); code constants `src/membud/verification.py` L10, `src/membud/semantic.py` L25 | established |
| Temperature | `0` (client default `0.0`; run script uses `from_deepseek_env`, which does not override it) | `src/membud/llm/client.py` L66 (`temperature: float = 0.0`), L128–L141 (`from_deepseek_env`); documented `docs/llm-heldout.md` L11 and `data/experiment-protocol.md` L13 | established (via code path + docs; not embedded in run summary JSON) |
| JSON mode | `response_format: json_object` on every completion request | `src/membud/llm/client.py` L105 (and L226, L322); `docs/llm-heldout.md` L11 | established |
| Max provider attempts per request | 3 | summary → `$.max_attempts` (L22); `src/membud/llm/client.py` L63 | established |
| Estimated-token fallback | 2,500 tokens/attempt when usage is absent | summary → `$.estimated_tokens_per_attempt` (L23); metrics → `$.cost_assumption.fallback_tokens_per_attempt` (L421) | established |
| Token cap | 250,000 (hard cap; checked after each row, before the next row starts) | summary → `$.budget.max_total_tokens` (L1962); metrics → `$.run.budget.max_total_tokens` (L389) | established |
| Cache | 0 cache hits | summary → `$.cache_hits` (L1991); metrics per-condition `cache_hit_rate.cache_hits` = 0 | established |
| Code revision recorded as | `41905266e29a4ae699655d8b66e92474a4ee8921` (commit `4190526`), **not** HEAD `1ce84ce` | summary → `$.code_revision` (L4); metrics → `$.code_revision` (L5) | established (provenance note §13 S8) |

---

## 5. Rules-only v2 results

All values below are from `data/results/evaluation-v2/metrics.json` (1,538 lines) unless stated. This file is the machine artifact; `docs/technical-report-milestone-2.md` §7 and `data/pilot-report.md` restate it.

### 5.1 Decision counts

| Fact | Value | Source | Status |
|---|---|---|---|
| Graph decision counts (pooled, 36 cases) | ACCEPT 14, HOLD 12, INVALID_INPUT 10, ERROR 0 | metrics → `$.condition_metrics.graph.decision_counts` (L282–L287) | established |
| Text decision counts (pooled, 36 cases) | ACCEPT 16, HOLD 10, INVALID_INPUT 10, ERROR 0 | metrics → `$.condition_metrics.text.decision_counts` (L925–L930) | established |
| Unguarded counts (pooled) | APPLIED 26, INVALID_INPUT 10 (no ACCEPT/HOLD verifier decisions) | metrics → `$.condition_metrics.unguarded.decision_counts` (L1410–L1413) | established |
| Unguarded applied rows | 26 | metrics → `$.condition_metrics.unguarded.applied_count` | established |
| Unguarded raw effect | gold-faulty (HOLD) cases 12, faulty updates applied 12, false-accept-equivalent rate 12/12 = 1.0 | metrics → `$.condition_metrics.unguarded.raw_effect` | established |
| Graph split counts | dev 7/6/5; heldout 7/6/5 (ACCEPT/HOLD/INVALID_INPUT) | metrics → `$.condition_metrics.graph.splits.dev.decision_counts` (L494–L499), `.heldout.decision_counts` (L706–L711) | established |
| Text split counts | dev 8/5/5; heldout 8/5/5 | metrics → `$.condition_metrics.text.splits.dev.decision_counts` (L1136–L1141), `.heldout.decision_counts` (L1347–L1352) | established |
| Unguarded split counts | dev APPLIED 13 / INVALID_INPUT 5; heldout APPLIED 13 / INVALID_INPUT 5 | metrics → `$.condition_metrics.unguarded.splits.dev.decision_counts` (L1458), `.heldout.decision_counts` (L1506) | established |
| Verifier/execution errors | graph 0/0, text 0/0, unguarded 0 | metrics → `$.condition_metrics.{graph,text}.verifier_errors`, `.execution_errors` (e.g. L299–L300); unguarded `$.condition_metrics.unguarded.execution_errors` | established |

### 5.2 Faulty detection, precision/recall, false accepts/holds

| Fact | Value | Source | Status |
|---|---|---|---|
| Gold faulty cases (combined denominator) | 22 = 12 HOLD + 10 expected INVALID_INPUT (`gold_faulty_cases`) | metrics → `$.condition_metrics.graph.faulty_detection.gold_faulty_cases` (L214–L272); note defines "Faulty means expected_decision is HOLD or INVALID_INPUT" | established |
| Gold HOLD-only cases | 12 | metrics → `$.condition_metrics.graph.as_completed.faulty.completed` (L143–L174) | established |
| Graph precision | 22/22 = 1.0 (TP 22, FP 0, FN 0) | metrics → `$.condition_metrics.graph.faulty_detection.precision` (L220–L240) | established |
| Graph recall | 22/22 = 1.0 | metrics → `$.condition_metrics.graph.faulty_detection.recall` | established |
| Graph false-accept rate (combined) | 0/22 = 0.0 | metrics → `$.condition_metrics.graph.faulty_detection.false_accept_rate` | established |
| Text precision | 20/20 = 1.0 (TP 20, FP 0) | metrics → `$.condition_metrics.text.faulty_detection.precision` | established |
| Text recall | 20/22 = 0.9090909090909091 (FN 2) | metrics → `$.condition_metrics.text.faulty_detection.recall` | established |
| Text false-accept rate (combined) | 2/22 = 0.09090909090909091 | metrics → `$.condition_metrics.text.faulty_detection.false_accept_rate` | established |
| As-completed view (graph) | faulty completed 12, flagged 12, detection 12/12 = 1.0, false accepts 0/12; valid completed 14, accepted 14, false holds 0/14 | metrics → `$.condition_metrics.graph.as_completed` (L143–L174) | established |
| As-completed view (text) | faulty completed 12, flagged 10, detection 10/12 = 0.8333333333333334, false accepts 2/12 = 0.16666666666666666; valid completed 14, accepted 14, false holds 0/14 | metrics → `$.condition_metrics.text.as_completed` (L786–L817) | established |
| Fail-closed ITT view | identical to as-completed here because there are **zero** error rows: graph faulty cases 12, held with decision 12, error holds 0; text faulty cases 12, held with decision 10, error holds 0 | metrics → `$.condition_metrics.{graph,text}.fail_closed_itt` (L175–L213, L818–L856) | established (ITT is vacuous in this run; say so if cited) |
| Valid-update acceptance | graph 14/14 = 1.0; text 14/14 = 1.0; unguarded valid_acceptance 14/14 = 1.0 (unguarded applies; not verifier acceptance) | metrics → `$.condition_metrics.{graph,text}.valid_update_acceptance`; `data/results/fairness-audit-v2/audit.json` → `$.per_strategy.unguarded.valid_acceptance` | established (fairness doc prints "—" for unguarded; see §13 S4) |
| INVALID_INPUT handling | 10 expected, 10 handled in every condition; 0 schema-accepted expected-invalid; 0 unexpected invalid rows; `unexpected_invalid_rate` denominator 26 | metrics → `$.condition_metrics.{graph,text}.invalid_input`; `$.invalid_input_summary` (L63–L83) | established |

### 5.3 Paired differences

| Fact | Value | Source | Status |
|---|---|---|---|
| Graph-vs-text differing cases | 2 of 36: `DEV-13`, `HELD-13` | metrics → `$.paired_differences.graph_vs_text.differing_case_count` (L1515–L1536) | established |
| Decisions on differing cases | graph `HOLD`, text `ACCEPT`, expected `HOLD` (both cases) | metrics → `$.paired_differences.graph_vs_text.differing_cases` (L1522–L1535) | established |
| Paired-difference caveat (verbatim) | "Counts and lists only; with 36 single-reviewed synthetic cases no statistical superiority claim is made. Differences indicate verifier-construction differences on identical inputs." | metrics → `$.paired_differences.graph_vs_text.note` | established |
| Different-case rate | 2/36 = 0.05555555555555555 (5.56%) | derived from the two established counts above | derived (formula 2 ÷ 36; no CI computed) |

### 5.4 Candidate-generation error rates (instrument validation)

| Fact | Value | Source | Status |
|---|---|---|---|
| Required-claim omission | 12/68 = 0.17647058823529413 | metrics → `$.candidate_error_rates.omission` (L21–L42) | established |
| Unsupported addition | 8/64 = 0.125 | metrics → `$.candidate_error_rates.unsupported_addition` | established |
| Prior-record corruption | 4/40 = 0.1 | metrics → `$.candidate_error_rates.prior_record_corruption` | established |
| Case-level error rate | 12/36 = 0.3333333333333333 | metrics → `$.candidate_error_rates.case_error` | established |

### 5.5 Cost, tokens, latency, graph size

| Fact | Value | Source | Status |
|---|---|---|---|
| Rules tokens | 0 input / 0 output / 0 total for graph, text, unguarded | metrics → `$.condition_metrics.{graph,text,unguarded}.llm_usage` and `.token_usage` | established |
| Rules estimated cost | `0.0` USD, `billing_data_available: false`, basis `rules_only_no_paid_calls` | metrics → `$.condition_metrics.{graph,text,unguarded}.estimated_cost` | established |
| Graph end-to-end latency mean | 0.24980555555555556 ms (n=36) | metrics → `$.condition_metrics.graph.timing_ms.latency_ms` (L87–L92) | established |
| Graph preparation mean | 0.04246153846153847 ms (n=26) | metrics → `$.condition_metrics.graph.timing_ms.preparation_ms` (L93–L97) | established |
| Graph verification mean | 0.11123076923076923 ms (n=26) | metrics → `$.condition_metrics.graph.timing_ms.verification_ms` (L98–L99) | established |
| Text end-to-end latency mean | 0.29330555555555554 ms (n=36) | metrics → `$.condition_metrics.text.timing_ms.latency_ms` (L729–L736) | established |
| Text preparation mean | 0.05746153846153846 ms (n=26) | metrics → `$.condition_metrics.text.timing_ms.preparation_ms` | established |
| Text verification mean | 0.10026923076923076 ms (n=26) | metrics → `$.condition_metrics.text.timing_ms.verification_ms` | established |
| Unguarded end-to-end latency mean | 0.1308888888888889 ms (n=36) | metrics → `$.condition_metrics.unguarded.timing_ms.latency_ms` | established |
| Graph node count mean | 9.615384615384615 (n=26) | metrics → `$.condition_metrics.graph.timing_ms.graph_node_count` (L100–L103) | established |
| Graph edge count mean | 13.961538461538462 (n=26) | metrics → `$.condition_metrics.graph.timing_ms.graph_edge_count` (L104–L105) | established |
| Latency interpretation | "sub-millisecond and noise-dominated … not a meaningful performance measurement" | `docs/technical-report-milestone-2.md` L109, L122; `data/pilot-report.md` L63; metrics `$.condition_metrics.*.timing_breakdown.note` | established (must be reported as instrumentation caveat, never as a performance finding) |
| Narrative roundings (for cross-checking, not for citation) | graph preparation ≈0.043 ms, verification ≈0.111 ms, end-to-end ≈0.250 ms; text ≈0.058/0.100/0.293 ms | `docs/technical-report-milestone-2.md` L109; `data/pilot-report.md` L63 | derived (roundings of the exact values above; prefer exact JSON values) |

### 5.6 Reason codes and interpretation

| Fact | Value | Source | Status |
|---|---|---|---|
| Graph FAIL reason-code counts | `CONTRADICTED_CLAIM` 2, `PRIOR_RECORD_UNAUTHORIZED_CHANGE` 4, `REQUIRED_CLAIM_OMITTED` 8, `UNSUPPORTED_CLAIM` 8; `checks_observed` 78 | metrics → `$.condition_metrics.graph.reason_code_counts` (L288–L298) | established |
| Text FAIL reason-code counts | `PRIOR_RECORD_UNAUTHORIZED_CHANGE` 4, `REQUIRED_CLAIM_OMITTED` 8, `UNSUPPORTED_CLAIM` 8 (no `CONTRADICTED_CLAIM`); `checks_observed` 78 | metrics → `$.condition_metrics.text.reason_code_counts` | established |
| Metrics interpretation (verbatim, key sentence) | "Exploratory case-level rates on a small synthetic single-reviewer set; not population estimates. … No statistical superiority claim is supported by this set." | metrics → `$.interpretation` (L1537) | established |

---

## 6. Fairness audit (v2)

Artifact: `data/results/fairness-audit-v2/audit.json` (3,109 lines; deterministic, rules-only, 0 paid API calls). Narrative: `docs/fairness-audit.md` (54 lines). All per-strategy values below were parsed from the JSON.

### 6.1 Design

| Fact | Value | Source | Status |
|---|---|---|---|
| Audit id | `fairness-audit-v2` | audit.json → `$.audit_id` (L3) | established |
| Case count | 36 | audit.json → `$.case_count` | established |
| Verifier | `rules` | audit.json → `$.verifier` | established |
| Paid API calls | 0 | audit.json → `$.paid_api_calls` (L2742) | established |
| Strategies (4) | `unguarded`, `text`, `text_fair`, `graph` | audit.json → `$.strategies` (L3086–L3108); `docs/fairness-audit.md` L15–L20 | established |
| `text` reads `evidence.contradicts` | `false` | audit.json → `$.strategies.text.reads_contradicts` | established |
| `text_fair` reads `evidence.contradicts` | `true` — claim-set baseline plus contradiction access; contradicted candidate claim ⇒ faithfulness FAIL `CONTRADICTED_CLAIM` | audit.json → `$.strategies.text_fair` (semantics string); `src/membud/fairness.py` L44–L60 | established |
| `graph` reads `evidence.contradicts` | `true` — decisions derived from graph adjacency and contradiction edges | audit.json → `$.strategies.graph` | established |
| `unguarded` semantics | applies schema-valid candidate unconditionally; no verifier | audit.json → `$.strategies.unguarded` | established |
| Information-parity logic (verbatim, abridged) | `text` → `text_fair` isolates contradiction information access; `text_fair` → `graph` isolates verifier construction with information held equal | audit.json → `$.analysis_note` (L2); `docs/fairness-audit.md` L22 | established |

### 6.2 Results

| Fact | Value | Source | Status |
|---|---|---|---|
| Graph (fairness) decision counts | ACCEPT 14, HOLD 12, INVALID_INPUT 10; false accepts 0/12; false holds 0/14; valid accepted 14/14; label agreement 36/36 = 1.0 | audit.json → `$.per_strategy.graph` (L2792–L2843) | established |
| `text_fair` decision counts | ACCEPT 14, HOLD 12, INVALID_INPUT 10; false accepts 0/12; false holds 0/14; valid accepted 14/14; label agreement 36/36 = 1.0 | audit.json → `$.per_strategy.text_fair` | established |
| `text` decision counts | ACCEPT 16, HOLD 10, INVALID_INPUT 10; false accepts 2/12 (`DEV-13`, `HELD-13`); false holds 0/14; valid accepted 14/14; label agreement 34/36 = 0.9444444444444444 | audit.json → `$.per_strategy.text` | established |
| `unguarded` counts | ACCEPT 26, HOLD 0, INVALID_INPUT 10; false accepts 12/12 (case ids on the record); false holds 0/14; label agreement 24/36 = 0.6666666666666666 | audit.json → `$.per_strategy.unguarded` | established |
| `text_fair` vs `graph` paired differences | zero (`[]`) across the audit — no decision differs between fair text and graph | audit.json → `$.paired_differences.text_fair_vs_graph` (L2744) | established |
| `text` vs `text_fair` paired differences | exactly 2 cases: `DEV-13`, `HELD-13`; `text: ACCEPT` → `text_fair: HOLD`; expected `HOLD`; reason codes `text_fair` include `CONTRADICTED_CLAIM` | audit.json → `$.paired_differences.text_vs_text_fair` (L2745–L2791) | established |
| Invalid-input handling under all strategies | classified 10/10 expected | audit.json → `$.per_strategy.*.invalid_input_handling` | established |
| Audit conclusion (verbatim, key clause) | "on this manifest, once contradiction information access is equalized, structural graph verification adds no decision-level detection power beyond the fair text control … narrow evidence from a small, single-reviewer, synthetic set and does not establish equivalence in general" | audit.json → `$.analysis_note` (L2); `docs/fairness-audit.md` L39–L41 | established |
| "text_fair matches graph on all 26 non-invalid cases" | established: audit shows zero `text_fair_vs_graph` differences; 36 − 10 invalid = 26 non-invalid | audit.json `$.paired_differences.text_fair_vs_graph`; case count arithmetic from `$.manifest.expected_invalid_case_count` = 10 | established (both this and the stronger "all 36 decisions agree" are true; state precisely) |
| Documented per-strategy table | audit matches `docs/fairness-audit.md` L26–L35 except unguarded valid-accepted shown as "—" | `docs/fairness-audit.md` L26–L35 vs `$.per_strategy.unguarded.valid_acceptance` (14/14) | established (discrepancy note §13 S4) |

### 6.3 Interpretation wording that is allowed

| Fact | Value | Source | Status |
|---|---|---|---|
| Established defensible sentence | "a fair text control that consumes the same `evidence.contradicts` matches graph on all 26 non-invalid v2 cases; the visible difference is annotation access, not structural inference." | `paper/README.md` L12; consistent with `docs/fairness-audit.md` L33–L41 and `audit.json` `$.analysis_note` | established (use this, or a close paraphrase with the same qualifiers) |

---

## 7. Held-out LLM and semantic run (v2)

Artifact: `data/results/llm-heldout-v2/{summary.json,metrics.json}`. Narrative: `docs/llm-heldout.md` (40 lines), also `docs/technical-report-milestone-2.md` §12 (L154–L164). Split: held-out only, 18 cases (13 schema-valid, 5 expected-invalid); 39 non-invalid rows executed (13 × 3 conditions); 54 total rows (18 × 3).

### 7.1 Spend, budget, attempts

| Fact | Value | Source | Status |
|---|---|---|---|
| Reported tokens (total) | 101,231 (input 34,505 / output 66,726; 54 rows with reported usage) | summary → `$.usage.reported` (L1974–L1980), `$.reported_tokens_used` (L1987); metrics → `$.run.usage.reported` (L370–L383) | established |
| Accounted tokens | 101,231 (0 estimated); no rows charged the 2,500-token fallback | summary → `$.accounted_tokens_used` (L1989), `$.usage.estimated` (L1981–L1985) | established |
| Provider request attempts | 59 (per condition: graph 16, text 17, semantic 26) | summary → `$.attempts` (L1990); metrics → `$.condition_metrics.*.attempts` (L102, L219, L340) | established |
| Token cap | 250,000; not aborted; overshoot 0 | summary → `$.budget` (L1961–L1971) | established |
| Planned vs executed non-invalid rows | 39 / 39 | summary → `$.budget.planned_non_invalid_rows`, `.executed_non_invalid_rows` | established |
| Verifier errors | 0 rows (no case ids) | summary → `$.llm_verifier_errors` (L1992–L1995); metrics per-condition `verifier_error_rate` 0/13 | established |
| Semantic uncertain rows | 0 (semantic abstention rate 0/13) | summary → `$.semantic_uncertain_rows` (L1996); metrics → `$.condition_metrics.semantic.semantic_abstention_rate` | established |
| Cache hits | 0 | summary → `$.cache_hits` (L1991); metrics per-condition `cache_hit_rate` 0/13 | established |
| Billing data | unavailable (`billing_data_available: false`) — provider did not report monetary cost | metrics → `$.cost_assumption` (L416–L423), `$.run.estimated_cost_range_usd.billing_data_available` (L404); `docs/llm-heldout.md` L14 | established |
| Estimated cost range (artifact) | total: low `0.010123` USD, high `0.202462` USD, at assumed $0.10–$2.00 per 1,000,000 accounted tokens; `estimated: true` | metrics → `$.run.estimated_cost_range_usd` (L400–L412), `$.cost_assumption` (L416–L423) | established |
| Estimated cost, narrative upper bound | ≈$0.40 "at DeepSeek's top published per-token rate"; ≈$0.20 "under the harness's $2/M ceiling" | `docs/llm-heldout.md` L14; `docs/technical-report-milestone-2.md` L164; `README.md` L29; `TODO.md` L74 | derived / narrative — **not in any JSON artifact**. The artifact ceiling at $2/M is `0.202462`; ≈$0.40 implies a ≈$4.00/M rate assumption not recorded in the repo (101,231 × 4 ÷ 1,000,000 = 0.404924). See §13 S2 before citing. |
| Cost range per condition (artifact) | graph `0.006321`–`0.126422`; text `0.002849`–`0.056974`; semantic `0.000953`–`0.019066` USD | metrics → `$.condition_metrics.*.estimated_cost_range_usd` (L118, L235, L356) | established |

### 7.2 Per-condition decisions and outcomes

| Fact | Value | Source | Status |
|---|---|---|---|
| Graph LLM decision counts | ACCEPT 3, HOLD 10, INVALID_INPUT 5 | metrics → `$.condition_metrics.graph.decision_counts` (L25–L30) | established |
| Text LLM decision counts | ACCEPT 6, HOLD 7, INVALID_INPUT 5 | metrics → `$.condition_metrics.text.decision_counts` (L142–L147) | established |
| Semantic decision counts | ACCEPT 8, HOLD 5, INVALID_INPUT 5 | metrics → `$.condition_metrics.semantic.decision_counts` (L259–L264) | established |
| Graph false accepts | 0/6 (no case ids) | metrics → `$.condition_metrics.graph.false_accepts` (L62–L67) | established |
| Graph false holds | 4/7 = 0.5714285714285714 (`HELD-01`, `HELD-02`, `HELD-03`, `HELD-07`) | metrics → `$.condition_metrics.graph.false_holds` (L68–L78) | established |
| Graph valid accepted | 3/7 = 0.42857142857142855 (`HELD-04`, `HELD-05`, `HELD-06`) | metrics → `$.condition_metrics.graph.valid_acceptance` (L79–L101) | established |
| Text false accepts | 0/6 | metrics → `$.condition_metrics.text.false_accepts` (L179–L184) | established |
| Text false holds | 1/7 = 0.14285714285714285 (`HELD-03`) | metrics → `$.condition_metrics.text.false_holds` (L185–L192) | established |
| Text valid accepted | 6/7 = 0.8571428571428571 (`HELD-01`, `HELD-02`, `HELD-04`, `HELD-05`, `HELD-06`, `HELD-07`) | metrics → `$.condition_metrics.text.valid_acceptance` (L193–L218) | established |
| Semantic false accepts | 1/6 = 0.16666666666666666 (`HELD-13`) | metrics → `$.condition_metrics.semantic.false_accepts` (L294–L301) | established |
| Semantic false holds | 0/7 | metrics → `$.condition_metrics.semantic.false_holds` (L302–L307) | established |
| Semantic valid accepted | 7/7 = 1.0 | metrics → `$.condition_metrics.semantic.valid_acceptance` (L308–L339) | established |
| Faulty detection, HOLD-only | graph 6/6 = 1.0; text 6/6 = 1.0; semantic 5/6 = 0.8333333333333334 | metrics → `$.condition_metrics.*.faulty_detection_hold_only` | established |
| Faulty detection, combined safe rejection (HOLD or INVALID_INPUT) | graph 11/11 = 1.0; text 11/11 = 1.0; semantic 10/11 = 0.9090909090909091 | metrics → `$.condition_metrics.*.faulty_detection_combined` | established |
| Case coverage | 13/13 = 1.0 in every condition | metrics → `$.condition_metrics.*.case_coverage` | established |
| Per-condition tokens | graph 63,211 (18,893 in / 44,318 out); text 28,487 (9,430 / 19,057); semantic 9,533 (6,182 / 3,351) | metrics → `$.condition_metrics.*.usage.reported` (L108, L225, L346) | established |
| Per-condition mean latency | graph 20,432.41176923077 ms; text 8,533.353076923077 ms; semantic 2,973.7456923076925 ms | metrics → `$.condition_metrics.*.mean_latency_ms` (L103, L220, L341) | established |
| Graph/text token ratio | 63,211 ÷ 28,487 = 2.2189419735317863 (doc reports ≈2.2×) | derived from established per-condition tokens; `docs/llm-heldout.md` L32 | derived (descriptive only; n small) |
| Graph/text latency ratio | 20,432.41176923077 ÷ 8,533.353076923077 = 2.394417714237861 (doc reports ≈2.4×) | derived from established means; `docs/llm-heldout.md` L32 | derived (descriptive only; n small) |
| Held-out `faulty` denominator | 6 gold HOLD cases (7 valid + 6 HOLD + 5 invalid = 18) | `docs/llm-heldout.md` L16; metrics false_accept/false_hold denominators 6 and 7 | established (terminology differs from rules metrics "faulty = 22"; see §13 S3) |
| Run caveats (verbatim, key clauses) | "Descriptive held-out evaluation only. … this run does not establish or claim superiority of any condition." / "Labels are single-reviewer exploratory annotations; a second independent human review is still pending." | metrics → `$.comparison_policy` (L413), `$.labels_caveat` (L415) | established |
| Semantic blind spot | the semantic module does **not** consume `evidence.contradicts`; it accepted `HELD-13` | `docs/llm-heldout.md` L31; `README.md` L40; `TODO.md` L75 | established |
| Model served revision | `deepseek-flash` is a catalog alias; docs say DeepSeek currently maps it to DeepSeek-V4.1-Flash-1017, but an alias may not identify an immutable backend revision | `README.md` L17; `data/experiment-protocol.md` L13 | pending (external, docs-only — do not state the served revision in the paper without primary-source verification) |

---

## 8. Architecture and behavioral facts for Bab 3

These rows support descriptive methodology claims (not results). Sources are the implementation and the repository's own documentation.

| Fact | Value | Source | Status |
|---|---|---|---|
| Positioning | MemBud checks proposed memory-state transitions before they are committed; verification layer between host memory and persistent write. Experimental local prototype, not a production memory service. | `README.md` L3, L7 | established |
| Canonical transition fields (v2) | `case_id`, `before`, `candidate`, `evidence`, `required_claims`, `operations`, `schema_version` | `src/membud/schemas.py` L111–L118 | established |
| Record model | `id`, atomic `claims` | `src/membud/schemas.py` L34–L36 | established |
| Evidence model | `id`, `supports`, `source`, `text`, `contradicts` | `src/membud/schemas.py` L44–L49 | established |
| DELETE authorization (structured) | `source`, `evidence_id`, `target_id`, `rationale`; all non-empty | `src/membud/schemas.py` L62–L80 | established |
| Operation model | `type`, `record_id`, `target_id`, `reason`, `authorization` | `src/membud/schemas.py` L84–L89 | established |
| Operation types | `WRITE`, `REVISE`, `DELETE`, `SUPERSEDE`, `NOOP` (strict v2 mapping of state changes to operations) | `README.md` L96–L104; `data/annotation-guidelines.md` L37–L39 | established |
| Schema versions | current `SCHEMA_VERSION = 2`; supported `{1, 2}`; version-less input loads as v1 legacy archival data | `src/membud/schemas.py` L7–L8, L124–L129 | established |
| Strict v2 invariants | every added candidate record needs `WRITE`/`SUPERSEDE`; changed prior record needs a real `REVISE`; removed prior record needs `DELETE`/`SUPERSEDE`; undeclared state change raises `ValueError` before verification | `README.md` L104; `docs/technical-report-milestone-2.md` L27–L38 | established |
| Decision policy | commit only if coverage, preservation, faithfulness all PASS; any FAIL/UNCERTAIN → HOLD; schema-invalid input → INVALID_INPUT before verification; fail-closed | `data/experiment-protocol.md` L15; `src/membud/store.py` L45–L47, L238–L263 | established |
| Text verifier | exact-claim-set baseline (coverage/preservation/faithfulness over claim sets); deliberately does not read `evidence.contradicts`; independent of graph verifier | `src/membud/verification.py` L21–L88; `README.md` L94, L108 | established |
| Graph verifier | structural verification from graph adjacency: provenance paths, required-claim reachability, preservation/removal via operation edges, contradiction edges, connectivity/orphans, structural consistency (`GRAPH_STRUCTURE_INVALID`) | `src/membud/graph_verification.py` L55+; `docs/technical-report-milestone-2.md` L42–L56 | established |
| Canonical graph elements (exact) | nodes: `transition`, `record`, `claim`, `evidence`, `operation`; edges: `BEFORE`, `PROPOSES`, `CONTAINS`, `HAS_EVIDENCE`, `SUPPORTS`, `REQUIRES`, `HAS_OPERATION`, `REVISES`, `DELETES`, `SUPERSEDES`, `CONTRADICTS`, `AUTHORIZED_BY`; built from the canonical transition only (no extra inputs) | `src/membud/graph.py` L7–L96 | established (do not reduce to only the six relationship names used in the outline) |
| `text_fair` control | exact claim-set baseline plus `evidence.contradicts` access; contradicted candidate claim ⇒ faithfulness FAIL `CONTRADICTED_CLAIM`; otherwise baseline result returned unchanged; independent of graph verifier; used only by the fairness audit, not the production dispatch | `src/membud/fairness.py` L1–L60; `data/results/fairness-audit-v2/audit.json` → `$.strategies.text_fair` | established |
| Semantic verifier (optional, separate) | `SemanticRelation` = EQUIVALENT / CONTRADICTORY / UNRELATED / UNCERTAIN; any transport/malformed/validator failure → UNCERTAIN; `verify_with_semantics` keeps deterministic result and appends one `semantic` check; ACCEPT requires all checks PASS | `docs/technical-report-milestone-2.md` L59–L67; `src/membud/semantic.py` L320+ | established |
| Semantic verifier does not read `contradicts` | confirmed by its acceptance of `HELD-13` in the held-out run | `docs/llm-heldout.md` L31; `TODO.md` L75 | established |
| Store format | `{"schema_version": 2, "version": N, "records": [...]}`; legacy bare-list stores load as v1 and migrate on the next ACCEPT | `README.md` L78; `docs/technical-report-milestone-2.md` L73–L81 (store format at L75) | established |
| Store guarantees | version increments exactly once per ACCEPT; HOLD never creates/rewrites/reformats the store; atomic temp-file + fsync + rename write; stray-temp recovery | `src/membud/store.py` L238–L300; `README.md` L78; `docs/technical-report-milestone-2.md` L73–L81 | established |
| Concurrency/locking | optional `expected_version` optimistic concurrency; POSIX `flock` around load → verify → write → audit; lock status recorded; degrades to no-op where unavailable | `src/membud/store.py` L196–L235, L264–L290; `README.md` L78 | established |
| Audit record fields | run id, UTC start, elapsed, store schema/version before/after, lock status, expected version, optional caller-supplied code revision; append-only JSONL | `README.md` L78; `src/membud/store.py` L264–L300 | established |
| Adapter protocol | structural `MemoryAdapter` protocol: `backend_name`, `snapshot(scope)`, atomic `replace_state(scope, records)`; no host mutation before ACCEPT; fresh-snapshot/stale check before apply | `src/membud/adapters/base.py` L24–L45; `data/adapter-contract.md` L9–L33 | established |
| Guarded interception order | capture prior state → build canonical v2 transition (invalid input rejected before any write) → verify with audit → ACCEPT-only persistence (`replace_state` once, re-snapshot equality) → decision/audit record | `data/adapter-contract.md` L37–L70 | established |
| Known adapter limits | shadow JSON baseline and host backend are separate writes (no distributed transaction); crash windows documented; only genuinely atomic adapters can be called guarded | `data/adapter-contract.md` L114–L129 | established |
| LLM client | JSON-mode OpenAI-compatible chat client; bounded retries; SQLite response cache; usage/latency capture; credentials from environment, never committed | `README.md` L67, L78; `src/membud/llm/client.py` L42–L110 | established |
| Run modes used in the paper | rules-only v2 run (deterministic, 0 tokens, $0.00); one-shot held-out LLM+semantic run under a hard 250,000-token cap | §4.1, §4.2; `docs/llm-heldout.md` L8–L14 | established |
| No human subjects / no production traffic | all cases are synthetic and repository-authored; no user data or licensed data committed | manifest `$.notes[0]`; `data/README.md` L6; `data/review/README.md` L130 | established (ethics wording beyond this, e.g. "no consent needed", is out of scope — §14) |
| No model training | no training/fine-tuning of any model is performed; the optional LLM is used only as a verifier | `PLAN.md` L27; `README.md` L3 | established |

---

## 9. Backend and adapter facts

| Fact | Value | Source | Status |
|---|---|---|---|
| Reference backend | stdlib-only in-memory and SQLite host backends + `GuardedMemory` interception layer | `src/membud/adapters/reference.py` L64 (`InMemoryBackend`), L102 (`SQLiteBackend`), L310 (`GuardedMemory`), L364 (`propose_update`); `data/backend-feasibility.md` L11–L21 | established |
| Reference backend status | demonstrated by `tests/test_reference_backend.py` (ACCEPT-only persistence; byte-identical HOLD; pre-write rejection of undeclared/unauthorized changes; audit records) | `data/backend-feasibility.md` L3–L9, L13–L21, L71–L80 | established |
| Third-party compatibility | **not claimed**: no third-party adapter is implemented or exercised at runtime | `data/backend-feasibility.md` L3, L7–L9, L78–L80; `data/adapter-contract.md` L3–L7, L127–L129; `README.md` L14, L38 | established |
| Mem0 inspected version | `mem0ai==2.2.1`, wheel sha256 `fe91bb91ac8926231993a4aa58df00a60c6c74c709e6a338fe399500776eef4d`; downloaded with `pip download --no-deps`, unpacked only — not installed, no runtime call | `data/backend-feasibility.md` L23–L30 | established |
| Mem0 license metadata | Apache-2.0 | `data/backend-feasibility.md` L30 | established |
| Mem0 hook finding | no public callback/transaction/pre-commit hook exposing the complete proposed transition; writes go to several stores sequentially; decision: no safe pre-commit hook in this release; guarded integration would need fork/host-owned staging | `data/backend-feasibility.md` L39–L50 | established |
| A-Mem inspection | repository `agiresearch/A-mem` at commit `ceffb860f0712bbae97b184d440df62bc910ca8d` (shallow HEAD); license MIT; neighbor notes can mutate before persistence; guarded integration needs staging/fork | `data/backend-feasibility.md` L52–L69 | established |
| "Three backends" | **not supported**: the repository demonstrates two *reference* backends (in-memory, SQLite) plus an interception layer; Mem0 and A-Mem were source-inspected, not integrated | §9 rows above; `data/backend-feasibility.md` L71–L80 | do-not-use (see §12) |
| Zep/Graphiti | stretch-goal only; not inspected/integrated in the evaluated artifacts | `TODO.md` L44; `PLAN.md` L390 | pending |

---

## 10. Licensing and external data

| Fact | Value | Source | Status |
|---|---|---|---|
| HaluMem license | CC BY-NC-ND 4.0 (dataset card) | `data/README.md` L15–L17 (review metadata: commit `d653749586bfc7d13f5b68e8851f52155b5b1650`, date 2026-04-21 — date/hash inconsistency pending, see §13 S19) | established (license documented in repo; review metadata flagged) |
| HaluMem use decision | assessed, not used; does not provide MemBud's per-claim transition labels; no HaluMem-derived records committed | `data/README.md` L21–L27; manifest `$.notes[0]` | established |
| LoCoMo license | CC BY-NC-SA **(4.0)** — user-established, **not documented in the repository** | no repository artifact; `paper/README.md` L14 mentions "LoCoMo (CC BY-NC-SA)" without version; `PLAN.md` L388 mentions LoCoMo as a possible downstream-QA source only | pending (verify against primary source before citing; do not attribute the license claim to the repo) |
| Licensed data committed | none; evaluation set is synthetic Indonesian-themed and repository-authored | `data/README.md` L6; manifest `$.notes[0]` | established |
| Repository's own license | no top-level `LICENSE` file and no `license` field in `pyproject.toml` | workspace file listing; `pyproject.toml` L1–L20 | established (do not describe the repository license in the paper without a source) |
| External citations | out of scope for this map; `paper/references.bib` is the only citation source and only verified entries may appear | `paper/README.md` L11 | established (process rule) |

---

## 11. Metric and denominator definitions (protocol facts)

These definitions must accompany any number in Bab 3/4.

| Fact | Value | Source | Status |
|---|---|---|---|
| Unit of analysis | one transition case, paired across conditions; claims/records are not independent samples | `data/experiment-protocol.md` L32, L54; metrics `$.interpretation` | established |
| Decision vocabulary | `ACCEPT`, `HOLD`, `INVALID_INPUT`; execution errors/missing results reported separately | audit.json `$.decision_vocabulary` (L2720–L2724); `data/experiment-protocol.md` L45 | established |
| `ACCEPT` | all required checks PASS; candidate committed | `data/adapter-contract.md` L65–L70; `data/experiment-protocol.md` L15 | established |
| `HOLD` | verification did not accept; host state untouched | `data/adapter-contract.md` L65–L70 | established |
| `INVALID_INPUT` | schema construction failure before verification; not a verifier decision; never writes memory | metrics `$.invalid_input_summary.note`; `data/experiment-protocol.md` L26 | established |
| As-completed denominator | execution/verifier errors excluded from numerator and denominator | `data/experiment-protocol.md` L41–L43; metrics `$.interpretation` | established |
| Fail-closed ITT denominator | all cases retained; error holds are non-accepting outcomes, not credited as detections | `data/experiment-protocol.md` L41–L43; metrics `$.interpretation` | established |
| Precision/recall over available decisions | true positives = gold faulty flagged HOLD/INVALID_INPUT; false positives = gold valid flagged; false negatives = gold faulty accepted; error rows excluded and reported separately | metrics `$.condition_metrics.graph.faulty_detection.note` (L214–L272) | established |
| False-accept rate | gold-faulty accepted ÷ gold faulty | `data/experiment-protocol.md` L47 | established |
| False-hold rate | gold-valid not accepted ÷ gold valid | `data/experiment-protocol.md` L47 | established |
| N/A ≠ 0 | do not count an absent denominator as zero | `data/experiment-protocol.md` L36, L54 | established |
| Candidate error rates | omission = required supported claims absent ÷ required supported claims; unsupported-addition = unsupported material candidate claims ÷ material candidate claims; prior-record corruption = improperly changed/removed valid prior records ÷ valid prior records expected to persist | `data/experiment-protocol.md` L38–L40; metrics `$.candidate_error_rates` | established |
| Latency separation | `preparation_ms` (graph build / text serialization) separate from `verification_ms`; `latency_ms` end-to-end; means only over instrumented rows | `data/experiment-protocol.md` L52; metrics `$.condition_metrics.graph.timing_breakdown` (L106–L115) | established |
| Latency status | sub-millisecond on tiny fixtures, timer-noise-dominated; not a meaningful performance measurement | `docs/technical-report-milestone-2.md` L109, L122; `data/pilot-report.md` L63 | established |
| Cost policy | estimated cost is reported as `estimated_cost_usd`; rules-only = 0.0; paid provider = not billed, provider billing unavailable; token counts are not billed cost | `data/experiment-protocol.md` L51, L58; metrics `$.cost_assumption` | established |
| Inference policy | no significance/power claims at n=36 single-reviewer synthetic cases; paired descriptive effect sizes only; zero-difference findings are "not detectable at this n", not equivalence | `data/experiment-protocol.md` L62; `docs/fairness-audit.md` L41; metrics `$.interpretation` | established |
| Bootstrap CIs | planned for a larger set; **not computed** for the current 36 cases | `data/experiment-protocol.md` L62 | pending |
| No tuning on held-out | 18/18 split authored together and frozen before the v2 rules run; no tuning on held-out outcomes | manifest `$.notes[4]`; `data/experiment-protocol.md` L28 | established |
| Second reviewer / agreement statistic | not collected; no kappa/agreement exists | manifest `$.review`; `data/review/README.md` L3–L9, L74–L84 | pending |
| "faulty" has two denominators | combined = HOLD + INVALID_INPUT (rules metrics: 22 pooled); HOLD-only = 12 pooled, 6 per held-out LLM split | metrics `$.condition_metrics.graph.faulty_detection.gold_faulty_cases` = 22 with note; `$.condition_metrics.graph.as_completed.faulty.completed` = 12; `docs/llm-heldout.md` L16 = 6 | established (define the term explicitly whenever used — §13 S3) |

---

## 12. DO NOT CLAIM (forbidden claims; all rows `do-not-use`)

Every row here is forbidden. The "why" cell states the refuting evidence and, where useful, a safe replacement sentence (prefix `Use instead:`). Even the replacement must keep its qualifiers and sources.

| Forbidden claim / phrasing (do not use) | Why it is unsupported or contradicted; safe replacement | Source | Status |
|---|---|---|---|
| "Graph verification is superior to text verification", "graph outperforms text", "graph advantage demonstrated" | The fair control eliminates the only observed difference: `text_fair` vs `graph` has zero paired differences, and the repository explicitly makes no superiority claim. Use instead: "On this 36-case single-reviewer synthetic set, no decision-level graph advantage was detectable once information access was equalized." | audit.json `$.paired_differences.text_fair_vs_graph` = `[]`; `docs/fairness-audit.md` L39–L41; `README.md` L24–L25, L44; `docs/technical-report-milestone-2.md` L5, L111 | do-not-use |
| "Graph catches contradictions text cannot", "graph detects contradictions that text misses" | The two differing cases are explained by annotation access; the plain text baseline never read `evidence.contradicts`, and a fair text control with the same access matches graph. Use instead: "The graph verifier consumes `evidence.contradicts` annotations the plain-text baseline did not consume; with equal access, the two agree." | audit.json `$.analysis_note` (L2), `$.paired_differences.text_vs_text_fair`; `docs/technical-report-milestone-2.md` L149 | do-not-use |
| "Graph verification is statistically significant / powered / proven better" | No significance test, no power analysis, no effect-size inference; n=36 single-reviewer synthetic cases. Use instead: "Descriptive paired counts only; the design can detect only large paired effects." | `data/experiment-protocol.md` L62; metrics `$.interpretation`; `docs/fairness-audit.md` L51–L52 | do-not-use |
| "Adapter interface was evaluated across 3 backends", "evaluated on multiple host backends" | Only two *reference* backends (in-memory, SQLite) plus GuardedMemory are demonstrated; Mem0/A-Mem were source-inspected and not installed/exercised; no third-party adapter exists. Use instead: "(2) an adapter contract and a reference backend that demonstrate write interception, together with a documented feasibility assessment of integrating the layer with two existing memory systems." Put the 3-backend evaluation in Future Work. | `data/backend-feasibility.md` L3–L9, L23–L50, L71–L80; `data/adapter-contract.md` L3–L7; `README.md` L14; `paper/bab1-corrections.md` B1 | do-not-use |
| "Mem0 / A-Mem / Zep / Graphiti integration", "supports third-party backends" | No integration exists; Mem0 2.2.1 has no safe public pre-commit hook; A-Mem would need a staging wrapper/fork; Zep/Graphiti is a stretch goal. Use instead: "Third-party backend integration is future work; source inspection identified no safe pre-commit boundary in the inspected releases." | `data/backend-feasibility.md` L39–L50, L61–L69, L78–L80; `TODO.md` L79; `PLAN.md` L390 | do-not-use |
| "Improves downstream question answering", "QA utility measured", "question-answering contribution" | Downstream QA was not measured; no QA dataset/metric artifact exists in the repo. Use instead: "(3) a controlled comparison of graph and non-graph verification on transition errors, update acceptance, latency and token cost, under an information-matched control." Put downstream QA in Future Work and state that it is out of scope for this study. | `TODO.md` L52; `README.md` L37; `docs/technical-report-milestone-2.md` L119; `PLAN.md` L39 (RQ only); `paper/bab1-corrections.md` B2 | do-not-use |
| "Returns an ACCEPT / REVISE / RETRY / REJECT decision" (user draft), or any retry-as-decision claim | No REVISE/retry decision exists. Implemented outcomes are `ACCEPT`, `HOLD`, `INVALID_INPUT`, plus execution `ERROR` recorded in metrics; retries are execution events that never authorise a commit. Use instead: "returns an ACCEPT or HOLD decision, with schema-invalid transitions reported separately as invalid input." | audit.json `$.decision_vocabulary` (L2720–L2724); `data/experiment-protocol.md` L15; metrics `$.interpretation`; `paper/bab1-corrections.md` B3 | do-not-use |
| "Production-ready", "production memory service", "deployable" | Repository describes itself as an experimental local prototype, not a production memory service. Use instead: "Research prototype; no production-readiness claim." | `README.md` L7, L112; `docs/technical-report-milestone-2.md` L9 | do-not-use |
| "Labels are double-annotated / independently reviewed / adjudicated gold data", any inter-annotator agreement figure | Single reviewer; second review pending; no agreement statistic; case set authored alongside verifiers. Use instead: "Single-reviewer exploratory labels; second independent review pending (blind quiz built, answers not yet collected); no agreement statistic exists." | manifest `$.review`; `data/review/README.md` L3–L9, L123–L130; `data/annotation-guidelines.md` L43–L45; `TODO.md` L18 | do-not-use |
| "The 36-case set is a representative benchmark", "population estimate" | Manifest purpose says it is not a representative benchmark; one reviewer; synthetic. Use instead: "A small synthetic, single-reviewer controlled set; results are exploratory, not population estimates." | manifest `$.purpose`; metrics `$.interpretation`; `data/review/README.md` L129–L130 | do-not-use |
| "Graph and text are statistically equivalent", "equivalent representations" | Zero difference at this n is "not detectable", not equivalence. Use instead: "No decision-level difference was detectable at this n; equivalence is not established." | `docs/fairness-audit.md` L39–L41; metrics `$.interpretation` | do-not-use |
| "Latency proves graph is faster / cheaper", latency as a performance finding | Timer noise at fixture scale; explicitly declared not meaningful. Use instead: "Timings are reported as instrumentation only; the fixtures are too small for performance inference." | `docs/technical-report-milestone-2.md` L109, L122; `data/pilot-report.md` L63; `data/experiment-protocol.md` L52 | do-not-use |
| "The run cost $0.40", any billed-cost claim | Provider did not report billed cost; billing data unavailable; token counts are telemetry, not a bill. Use instead: "Estimated, not billed: ≈$0.40 upper bound in the narrative docs; artifact-envelope estimate at $2/M is $0.202462; billed amount unavailable." | metrics `$.cost_assumption`; `docs/llm-heldout.md` L14; `README.md` L19, L29 | do-not-use |
| "All three LLM conditions were compared under equal token/latency budgets" | The graph LLM condition used ≈2.2× tokens and ≈2.4× latency of text, and conditions differ in verifier construction; comparison policy says no superiority claim. Use instead: "Descriptive comparison under different verifier constructions and budgets; not an equal-budget comparison." | metrics `$.comparison_policy` (L413); `docs/llm-heldout.md` L32–L33 | do-not-use |
| "The semantic verifier resolves contradictions", "semantic module handles contradiction evidence" | The semantic module does not consume `evidence.contradicts`; it accepted `HELD-13`. Use instead: "The semantic comparator does not read contradiction annotations; this is a documented integration gap." | `docs/llm-heldout.md` L31; `TODO.md` L75 | do-not-use |
| "Results at HEAD 1ce84ce" for the LLM run | LLM artifacts record code revision `4190526`, not HEAD; the rules run records a working-tree digest. Use instead: "The LLM run was produced at revision 4190526; the rules run records a working-tree digest. Cite the artifact's own revision when describing a computation." | summary/metrics `$.code_revision`; run-config L6 | do-not-use |
| "`deepseek-flash` = DeepSeek-V4.1-Flash-1017" as a settled model identity | The alias may not identify an immutable backend revision; the mapping is a docs-only external statement. Use instead: "Model ID `deepseek-flash` (provider catalog alias), temperature 0, JSON mode." | `data/experiment-protocol.md` L13; `README.md` L17 | do-not-use |
| "The 243-test suite proves correctness / performance" | Test count is a repository-state fact, not evidence of correctness or of any research claim. Use instead: "At commit 1ce84ce the suite reports 243 passing tests." | `README.md` L112 (demo not evidence) | do-not-use |
| Any "state of the art", "novel graph reasoning", "first to …" claim | Not supported by any artifact; novelty requires literature verification and the repository claims no graph superiority. Use instead: "A methods/systems contribution plus a rigorous negative result on a controlled synthetic set." | `PLAN.md` L390–L392; `paper/README.md` L12 | do-not-use |
| Any number not present in this file, or present only as `pending` / `do-not-use` | Violates the fact-discipline rule. Use instead: omit the number, or add a verified row to this map first. | `paper/README.md` L10 | do-not-use |

---

## 13. Stale / superseded / discrepancy register

| Statement / discrepancy (fact) | What the artifacts actually say (value) | Source | Status |
|---|---|---|---|
| S1 — Tech report says "202 passing tests" | 202 was the milestone-2 count at its own commit; current HEAD suite is **243 passed** (reproduced 2026-11-07). Use 243 for current state. | `docs/technical-report-milestone-2.md` L19, L135 vs test run at HEAD (`.venv/bin/python -m pytest -q`) | do-not-use for 202 at HEAD (stale); use 243 (§1) |
| S2 — LLM spend narrative: "≈$0.40 upper bound at DeepSeek's top published rate" | Artifact JSON cost block is **0.010123–0.202462 USD** (assumed $0.10–$2.00/M). `0.202462` is the $2/M ceiling; ≈$0.40 implies ≈$4/M (101,231 × 4 ÷ 1,000,000 = 0.404924) and appears in no JSON artifact. Provider billing unavailable either way. | `data/results/llm-heldout-v2/metrics.json` → `$.run.estimated_cost_range_usd` (L400–L412), `$.cost_assumption` (L416–L423) vs `docs/llm-heldout.md` L14, `docs/technical-report-milestone-2.md` L164, `README.md` L29, `TODO.md` L74 | do-not-use as a billed-cost claim; cite ≈$0.40 only as a narrative estimate with an explicit "not billed, not in the JSON" qualifier |
| S3 — "Gold faulty" is used with different denominators | Rules metrics `gold_faulty_cases = 22` (HOLD + INVALID_INPUT); tech report distinguishes a "Gold HOLD-only view" (12); LLM doc says "7 valid, 6 gold faulty, 5 invalid" (faulty = HOLD only). All internally consistent; the term is defined differently. Use: define "faulty" explicitly in the paper. | metrics `$.condition_metrics.graph.faulty_detection` note; `docs/technical-report-milestone-2.md` L105–L106; `docs/llm-heldout.md` L16; `data/experiment-protocol.md` L28 | established (all consistent; define the term explicitly) |
| S4 — `docs/fairness-audit.md` prints unguarded "Valid accepted —" | `audit.json` records `per_strategy.unguarded.valid_acceptance` = 14/14 rate 1.0 (unguarded applies every candidate; this is not verifier acceptance). | `docs/fairness-audit.md` L28 vs `audit.json` → `$.per_strategy.unguarded.valid_acceptance` | established (both true; use with the qualifier "raw applied, not verifier acceptance") |
| S5 — Tech report §7 (L93–L111) frames the graph-vs-text difference before the fairness audit | §11 (L143–L149) and §12 (L154–L164) are the correcting addenda: the difference is annotation access, not structure; §11 itself says the earlier "catches contradictions" framing must be read as annotation consumption. | `docs/technical-report-milestone-2.md` L143–L149; audit.json `$.analysis_note` | do-not-use (superseded framing) |
| S6 — Tech report §3 says "a contradiction fixture shows graph HOLD where the text baseline ACCEPTs" | This is a unit-test fixture, not the 36-case evaluation; do not present it as evaluation evidence of detection gain. | `docs/technical-report-milestone-2.md` L53; `tests/test_graph_verification.py` | do-not-use as evaluation evidence |
| S7 — `data/README.md` calls the v2 set "`schema_version: 2`" | Cases carry `schema_version: 2` (36/36) and the manifest is schema version 2; the **labels JSONL has no schema_version field**. Say "the cases and manifest are schema_version 2". | `data/README.md` L6; parsed cases/labels | established (precision note) |
| S8 — LLM run code revision vs HEAD | LLM summary/metrics record `4190526…` (the pre-`dad4759`/`1ce84ce` commit), not HEAD `1ce84ce`; results were committed later. Not an error. | summary L4; metrics L5; `.git/refs/heads/main` | established (provenance note: cite the artifact revision for the LLM computation) |
| S9 — Rules metrics `code_revision` is `working-tree-sha256:044a…`, not a git commit | The rules run's source identity is a working-tree digest; the mapping to a commit is not recorded in the artifact. Do not claim the rules run is pinned to commit `1ce84ce`. | run-config L6; metrics L7 | established (reproducibility caveat) |
| S10 — Manifest `review.review_sheet_tool` names `scripts/review_cases.py`, but the current blind quiz workflow uses `scripts/review_quiz.py` | `data/review/README.md` explains the manifest still names the older sheet tool and that changing the manifest while review is pending would require a new version. | manifest L11; `data/review/README.md` L147–L150 | established (internal documentation note) |
| S11 — `data/pilot-report.md` says "112 tests" | Historical pilot-time count, superseded by 243 at HEAD. | `data/pilot-report.md` L17 | do-not-use (historical) |
| S12 — "26 non-invalid cases" vs "all 36 cases" | Both true: the audit's zero `text_fair_vs_graph` differences span all 36 cases; 36 − 10 invalid = 26 non-invalid. Prefer "zero differences on all 36 cases (hence also on all 26 non-invalid cases)". | audit.json `$.paired_differences.text_fair_vs_graph`, `$.manifest.expected_invalid_case_count`; `README.md` L25 | established (precision note) |
| S13 — `docs/llm-heldout.md` L14 says "The 250,000-token cap was not approached" | 101,231 of 250,000 = 40.49% of cap; no abort, overshoot 0. Accurate as a run fact; do not turn it into a "under budget by X" performance claim. | summary `$.budget` (L1961–L1971) | established |
| S14 — `data/experiment-protocol.md` says "7 valid, 6 faulty, 5 invalid per split" | Consistent with labels (ACCEPT 7 / HOLD 6 / INVALID_INPUT 5 per split); "faulty" here means gold HOLD only, unlike the combined 22 definition. | `data/experiment-protocol.md` L28; parsed labels; metrics `$.condition_metrics.graph.faulty_detection.gold_faulty_cases` | established (see S3; define terms) |
| S15 — `data/pilot-report.md` L63: "text false accepts are 1/11 per split" | Denominator 11 per split = 6 gold-HOLD + 5 invalid; pooled text false accepts are 2/22. Consistent with metrics when the denominator is stated. | `data/pilot-report.md` L63; metrics `$.condition_metrics.text.faulty_detection` | established (precision note: always state the denominator) |
| S16 — v1 12-case DeepSeek pilot numbers (9 original verifier errors, rerun to 24/24, 32,126 + 24,407 + 5,946 tokens, "roughly $0.10") | Real archival artifacts for the v1 pilot, but superseded for v2 claims; mixing them with v2 numbers invites confusion. Cite only with explicit pilot framing and their own artifact paths. | `data/pilot-report.md` L33–L43; `README.md` L18–L19; `data/results/evaluation-v1-*` | do-not-use as main results (archival pilot only) |
| S17 — `README.md` says model "deepseek-flash (currently served as DeepSeek-V4.1-Flash-1017 per DeepSeek docs)" | Alias-to-revision mapping is external and not in the run artifacts; the protocol warns an alias may not identify an immutable revision. | `README.md` L17; `data/experiment-protocol.md` L13 | pending (external fact; see §14) |
| S18 — Task brief said "clean tree" | `git status --porcelain` shows only `?? paper/`; no tracked modifications. The paper workspace is intentionally untracked. | git status at snapshot | established (precision note, not a discrepancy) |
| S19 — HaluMem review metadata: `data/README.md` L11 says the repository was reviewed at commit `d653749586bfc7d13f5b68e8851f52155b5b1650` on 2026-04-21 | lit-verifier found that commit dated 2026-10-28, so the hash and date cannot both be right; fix the date or the hash before citing this review note. The CC BY-NC-ND 4.0 license finding is unaffected. | `data/README.md` L11; `paper/bab1-corrections.md` §C2 | pending (doc fix) |

---

## 14. Pending and unsourced facts

These must not be written as results. They may appear only as explicitly pending work or with the stated limitation.

| Fact | What is missing / why it is pending (value) | Source | Status |
|---|---|---|---|
| Independent second human review of v2 labels | not recruited/collected; no agreement statistic or kappa; process documented | manifest `$.review`; `data/review/README.md` L3–L9, L74–L84; `TODO.md` L77 | pending |
| Inter-annotator agreement (raw or kappa) | only a *planned* grader exists; no answers file exists | `data/review/README.md` L84, L104–L110 | pending |
| Adjudicated labels / new frozen manifest version | process documented, not executed | `data/review/README.md` L115–L121 | pending |
| Billed monetary cost of any run | provider billing unavailable; provider dashboard authoritative | metrics `$.cost_assumption`; `docs/llm-heldout.md` L14; `README.md` L19 | pending |
| Downstream QA utility / question-answering evaluation | not measured; no artifact | `TODO.md` L52; `README.md` L37; `docs/technical-report-milestone-2.md` L119 | pending (not measured) |
| Third-party backend integration with any host (Mem0, A-Mem, Zep/Graphiti) | not implemented or exercised; feasibility notes only | `data/backend-feasibility.md` L3–L9, L78–L80; `TODO.md` L79 | pending |
| "Adapter interface evaluated across 3 backends" (user Introduction draft) | unsupported: repository demonstrates 2 reference backends + interception layer; the user draft is not a repository artifact | `data/backend-feasibility.md` L71–L80; `paper/bab1-corrections.md` B1; user's Introduction draft (external, not in repo) | do-not-use (unsupported claim) |
| "Question answering" contribution (user Introduction draft) | unsupported: no QA measurement exists | §14 row above; `PLAN.md` L39 | do-not-use (unsupported claim) |
| "Mem0's graph variant scored only about 2% higher" (user draft claim) | external literature claim; not verifiable from this repo; bibliography scope is `lit-verifier`/`task-2` | user's draft (external); `paper/README.md` L11; `PLAN.md` L392 | pending (external; bibliography task) |
| Bootstrap confidence intervals for paired effects | planned for a larger set; not computed for the current 36 cases | `data/experiment-protocol.md` L62 | pending |
| Model served revision for `deepseek-flash` | external, provider-docs-only; alias may not identify immutable revision | `data/experiment-protocol.md` L13; `README.md` L17 | pending |
| LoCoMo license (CC BY-NC-SA 4.0) | user-established; no repository artifact states it; verify primary source before citing | §10; `PLAN.md` L388 | pending |
| HaluMem bibliographic/primary-source citation details | outside this map; must be verified by the bibliography task, not copied from repository prose | `data/README.md` L11–L27; `paper/README.md` L11 | pending |
| Repository license / artifact-availability statement | no `LICENSE` file and no `license` field in `pyproject.toml`; do not describe licensing terms without a source | workspace file listing; `pyproject.toml` L1–L20 | established (absence verified; no licensing claim allowed) |
| Ethics approval / consent statement | no artifact states IRB/consent status; only "synthetic data, no human subjects" is supportable from repository content | manifest `$.notes[0]`; `data/README.md` L6 | pending |

---

## 15. Verification log (commands and cross-checks)

Everything in this section was executed while building this map (2026-11-07, Asia/Jakarta). It exists so the fact-checker can reproduce the checks.

1. **Repository state**
   - `git rev-parse HEAD` → `1ce84ce600ff20c90dc55539c6fa59eda22d40d2`
   - `git branch -vv` → `main 1ce84ce [origin/main: ahead 3]`
   - `git status --porcelain` → `?? paper/`
   - `git rev-list --count origin/main..HEAD` → `3`
2. **Tests**
   - `.venv/bin/python -m pytest -q` → `243 passed in 0.48s`
3. **Hashes (all matched)**
   - SHA-256 of `data/cases/evaluation-v2.jsonl` = manifest `$.cases_sha256` = `40558746…432d`
   - SHA-256 of `data/labels/evaluation-v2.jsonl` = manifest `$.labels_sha256` = `9280d3e8…9a14`
   - SHA-256 of `data/manifests/evaluation-v2.json` = `e1adf173…527a` = metrics `$.manifest_sha256` = run-config `$.manifest_sha256` = fairness `$.manifest.sha256` = LLM summary/metrics `$.manifest_sha256`
   - SHA-256 of `data/results/evaluation-v2/pilot-summary.json` = metrics `$.summary_sha256` = `fbc31a73…5584`
4. **JSON parsing/validation**
   - Manifest: 36 `case_entries`; `split_counts` 18/18; `expected_decision_counts` 14/12/10; `expected_decision_counts_by_split` 7/6/5 both splits; review `reviewer_count` 1, `second_review_status` `pending_human_review`.
   - Cases: 36 lines, all `schema_version` = 2, IDs `DEV-01…18` + `HELD-01…18`, label IDs match exactly.
   - Labels: 36 lines; expected decisions 14/12/10; splits 18/18; `label_status` all `single_reviewer_second_review_pending`; 10 `expected_invalid` true.
   - Rules metrics: decision counts graph 14/12/10, text 16/10/10, unguarded APPLIED 26 / INVALID 10; precision/recall graph 22/22, text 20/20 and 20/22; paired differences 2 (`DEV-13`, `HELD-13`); per-split counts; latency and graph node/edge means; all timings/tokens/cost fields read from JSON.
   - Fairness audit: 4 strategies; per-strategy counts; `text_fair_vs_graph` = `[]`; `text_vs_text_fair` length 2 (`DEV-13`, `HELD-13`); unguarded valid acceptance 14/14; 0 paid API calls.
   - LLM run: 18 held-out cases; conditions graph/text/semantic; decisions 3/10/5, 6/7/5, 8/5/5; false accepts 0/6, 0/6, 1/6; false holds 4/7, 1/7, 0/7; valid accepted 3/7, 6/7, 7/7; 101,231 reported tokens (34,505/66,726); 59 attempts; 250,000 cap; 0 verifier errors; 0 cache hits; 0 semantic uncertain; cost range 0.010123–0.202462 USD; billing unavailable.
   - Cross-check: `26/36` non-invalid; `2/36 = 0.05555555555555555`; token ratio `63211/28487 = 2.2189419735317863`; latency ratio `20432.41176923077/8533.353076923077 = 2.394417714237861`; `101231 × 2 / 1,000,000 = 0.202462`; `101231 × 4 / 1,000,000 = 0.404924`.
5. **Documentation cross-check**
   - `docs/technical-report-milestone-2.md`, `docs/fairness-audit.md`, `docs/llm-heldout.md`, `README.md`, `TODO.md`, `PLAN.md`, `data/experiment-protocol.md`, `data/annotation-guidelines.md`, `data/README.md`, `data/review/README.md`, `data/backend-feasibility.md`, `data/adapter-contract.md`, `data/pilot-report.md` were read and their numeric claims compared with the machine artifacts. Disagreements are registered in §13; unsupported claims in §12 and §14.
6. **Known limitation of this map**
   - The test count (243) is a runtime observation, not a committed artifact; it will change if code changes. Re-run the command if the paper states it after any further commits.
   - The map intentionally does not cover Bab 1–2 literature citations; that is the bibliography task (`paper/references.bib`).

---

*End of canonical evidence map. If a number is not here, it is not allowed in the draft. If an artifact changes, rebuild this file — do not patch rows from prose.*
