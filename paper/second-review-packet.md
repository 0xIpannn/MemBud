# Second-reviewer packet (independent human review of the v2 labels)

**Status:** ready to send, but **no reviewer has been recruited and no answers exist yet**. Until this process completes, every report must say: *single-reviewer exploratory labels; second independent review pending; no inter-annotator agreement statistic exists.*
**Artifacts:** quiz `data/review/quiz-v2.html` (input-only, no labels, hash-ordered); frozen manifest `data/manifests/evaluation-v2.json`; process documentation `data/review/README.md`; adjudication template `data/review/adjudication-template.md`; annotation rules `data/annotation-guidelines.md`.

## For the coordinator (you)

**What to send the reviewer:** only
1. `data/review/quiz-v2.html` (open it offline in a browser; it is self-contained, makes no network calls, and contains no labels);
2. `data/annotation-guidelines.md` (the decision rules);
3. the short brief below.

**Do not send:** `data/labels/`, `data/review/review-sheet-v2.md` (it shows proposed labels), the paper drafts, the case categories, or the expected decision distribution.

**Eligibility:** the reviewer must **not** be an author of the cases or the paper and must not have seen the proposed labels. A third person (not either reviewer) is needed later for adjudication if there are disagreements.

**Time estimate:** 36 cases; 60–90 minutes. The quiz autosaves in the browser and shows progress; answers can be exported at any point and resumed later.

**Expected outputs from the reviewer:** the downloaded answers JSON `membud-evaluation-v2-answers-<slug>.json`, sent to the coordinator **unedited** (no screenshots, no manual JSON edits).

## Reviewer brief (send verbatim)

> You are independently reviewing 36 proposed memory updates for a research study. Each case gives you the prior memory state, the evidence available for the update, the required claims, the proposed new state, and the declared operations. Your job is to decide, for each case, whether the proposed update is safe to commit.
>
> For each case choose one of:
> - **ACCEPT** — the proposed state is supported by the evidence, preserves prior memory that should persist, and every change is properly declared;
> - **HOLD** — something is wrong or cannot be verified: a required claim is dropped, a prior record is changed or removed without proper operation/authorization, a candidate claim is unsupported by the evidence or contradicted, an operation is wrong, or the evidence is insufficient;
> - **INVALID_INPUT** — the transition itself is malformed under the strict schema (for example, a state change is not declared by any operation, or a deletion lacks structured authorization).
>
> Apply the written rules in the annotation guidelines you were given. Do not use outside world knowledge; judge only against the supplied evidence and prior state. Add a short note when you are unsure, and mark your decision anyway — do not skip cases. Do not look for the authors' expected answers, and do not discuss cases with the authors before submitting.
>
> The quiz contains no labels and saves your answers locally. When done, download the answers JSON and send it back unchanged.

## Grading and adjudication (after answers arrive)

```bash
# 1. Grade the answers against the frozen labels (this is the only step that reads labels)
PYTHONPATH=src .venv/bin/python scripts/review_quiz.py \
  --grade ~/Downloads/membud-evaluation-v2-answers-<slug>.json \
  --reviewer "<Reviewer name>" \
  --output data/review/agreement-<slug>.json
```

The grader validates the answers, rejects a mismatched manifest identity/hash or reviewer name, allows blank decisions, and reports coverage plus overall and per-split raw agreement; it lists every disagreement with the full transition summary. Verified CLI: `--build`, `--grade ANSWERS_JSON`, `--reviewer`, `--output`, `--manifest`.

Then:
1. **Adjudicate every disagreement** using `data/review/adjudication-template.md`; the adjudicator must be a third person and must cite the annotation rule applied.
2. **Re-freeze** the adjudicated decisions as a **new** labels file + manifest version (e.g. `evaluation-v2-reviewed.json`); never overwrite the single-reviewer labels.
3. **Re-run** the deterministic evaluation on the new manifest (rules-only, 0 paid calls) and update the paper's numbers only if the manifest changes.
4. **Report** reviewer roles (anonymised), cases reviewed, raw agreement, Cohen's kappa over the three-way decision labels (computed separately from the report's per-case rows), adjudicated case list, and any guideline revisions.

## What may and may not be claimed

| May claim | May NOT claim |
|---|---|
| "single-reviewer exploratory labels with second review pending" (current state) | "independently reviewed", "double-annotated", "adjudicated gold data" (until step 2 completes) |
| descriptive paired counts on the frozen instrument | inter-annotator agreement of any kind (none exists yet) |
| "the process is prepared and the blind quiz is built" | representative-benchmark validity (the set is synthetic, n=36) |

## Checklist to mark this item done

- [ ] Reviewer recruited: non-author, has not seen the labels.
- [ ] Brief + quiz + annotation guidelines sent (nothing else).
- [ ] Answers JSON received and graded; coverage reported.
- [ ] Disagreements adjudicated by a third person; rules cited.
- [ ] New labels + manifest frozen and hash-pinned.
- [ ] Deterministic evaluation re-run on the new manifest (0 paid calls).
- [ ] Agreement (raw + kappa) and adjudication list reported in the paper.
- [ ] Bab 3/4 wording updated from "pending" to "completed" only after all of the above.
