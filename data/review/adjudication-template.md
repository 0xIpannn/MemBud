# Adjudication record template (PENDING — do not prefill)

Use this template only after a real second reviewer has independently annotated
the frozen case package. It records how each disagreement was resolved. It is
intentionally blank: filling it with invented reviewer names, dates, or decisions
would fabricate independent review.

## Review round metadata

| Field | Value |
|---|---|
| Manifest reviewed | `data/manifests/evaluation-v2.json` |
| Manifest SHA-256 | _(fill from manifest `cases_sha256`/`labels_sha256` + manifest hash)_ |
| Review round / date | _(pending)_ |
| Reviewer A (author) ID | _(pending)_ |
| Reviewer B (independent) ID | _(pending — must be a different person)_ |
| Adjudicator ID | _(pending — different from A and B)_ |
| Cases reviewed | _0 / 36 (pending)_ |
| Raw agreement | _not computed — no second-review data_ |
| Cohen's kappa (3-way decision) | _not computed — no second-review data_ |
| Unresolved cases | _none recorded_ |

## Disagreement log

Copy one row per case where Reviewer B's decision differs from the frozen
single-reviewer label. Do not add rows until a real disagreement has been
recorded.

| Case ID | Frozen label | Reviewer B decision | Reviewer B basis | Adjudicated decision | Adjudicator basis (guideline rule) | Date |
|---|---|---|---|---|---|---|
| _(none yet — pending independent review)_ | | | | | | |

## Claim/finding-level disagreements (optional)

| Case ID | Field | Reviewer A annotation | Reviewer B annotation | Resolved value | Rule applied |
|---|---|---|---|---|---|
| _(none yet)_ | | | | | |

## Guideline issues discovered

Record any case where `data/annotation-guidelines.md` was ambiguous or silent. If
the guideline changes, create a new manifest version and restart review.

| Case ID | Ambiguity | Proposed guideline change | Accepted? |
|---|---|---|---|
| _(none yet)_ | | | |

## Post-adjudication actions

- [ ] Adjudicated labels written to a **new** file (never overwrite
      `data/labels/evaluation-v2.jsonl`).
- [ ] New manifest version created with new SHA-256 pins and
      `reviewer_count = 2`, `second_independent_reviewer = true`,
      `second_review_status = "completed"`.
- [ ] Evaluation rerun and metrics regenerated from the adjudicated manifest.
- [ ] Final report states reviewer count, agreement statistics, adjudicated
      case list, and remaining limitations.
