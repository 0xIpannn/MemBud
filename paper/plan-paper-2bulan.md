# Two-month paper plan (2026-11-07 → 2027-01-07)

**Goal:** Bab 3 in 2 days (2026-11-09); full paper submission-ready by 2027-01-07, targeted at an international conference/workshop (ACL/EMNLP/NeurIPS D&B/COLM class). **No acceptance promise** — venue fit and the second human review are the gating uncertainties.

**Hard constraints carried through:** no invented numbers/citations; no licensed data committed; no paid LLM calls without explicit spend approval; `main` stays clean and tests pass; graph-superiority is not claimed.

## Critical path

Two items dominate the schedule; both are outside pure writing:

1. **Independent second human review of the v2 labels.** The blind quiz (`data/review/quiz-v2.html`) exists and contains no labels; a non-author reviewer must fill it, and a third person must adjudicate disagreements. Until then every result is "single-reviewer exploratory". For an international venue, this is the most likely reviewer objection. Start recruiting now.
2. **Venue cycle.** ACL/EMNLP/NeurIPS D&B/COLM cycles have fixed, sometimes already-closed deadlines. The plan must match a real CFP before promising a date. If the next suitable deadline is < 8 weeks away, submit the current evidence with explicit limitations; if later, use the extra time for a powered evaluation.

## Milestones

| Date | Milestone | Owner | Exit criterion |
|---|---|---|---|
| 2026-11-09 | **Bab 3 assembled + fact-checked** | Lead + fact-checker | Chapter 3 built from `paper/sections/`, every number traced to `paper/evidence-map.md`, zero TODO citations, no superiority claim |
| 2026-11-10 | Bab 3 user review; Bab 1 corrections applied | User + Lead | User signs off headings/content; `paper/bab1-corrections.md` edits merged into Bab 1/2 |
| 2026-11-13 | Second reviewer recruited; blind quiz sent | User | Reviewer is not an author and has not seen labels |
| 2026-11-20 | Second review returned; agreement computed; adjudication done | User + third adjudicator | `data/review/agreement-*.json` exists; disagreements adjudicated; new hash-pinned labels/manifest |
| 2026-11-24 | Rerun deterministic evaluation on adjudicated labels (rules-only, free) | Lead | New results artifacts; discrepancy note vs current labels |
| 2026-11-30 | Bab 4 (Results) draft + figures/tables | Lead + writers | All RQ-relevant numbers from artifacts; per-split and pooled; no causal language |
| 2026-12-07 | Bab 5 (Discussion) + Bab 6 (Conclusion) + Abstract | Lead | Negative result framed correctly; threats to validity carried forward; contributions match evidence |
| 2026-12-15 | Full draft v1 (all chapters, references, format) | Lead | Compiles; citations resolve; consistency pass |
| 2026-12-22 | Internal review round 2: fact-check, language edit, venue formatting | Lead + independent checker | No unsupported number; venue template applied; page limit met |
| 2027-01-07 | **Submission-ready package** | Lead | Final PDF/LaTeX, artifact statement, cover letter, checklist; actual venue deadline confirmed by user |

## What can and cannot be added in 2 months

**Feasible without new spend:**
- Second human review + agreement/adjudication (time cost only).
- Rerun deterministic evaluation on adjudicated labels.
- Figures (framework, pipeline, confusion-style tables), artifact documentation, reproducibility statement.
- Bab 1–2 cleanup and verified references.
- Possibly a small deterministic ablation set (new hand-authored cases, no API cost) — but any new cases must be frozen and reported as exploratory, not held-out.

**Not feasible without explicit approval / scope change:**
- A powered held-out evaluation (needs justified sample size, probably 100+ cases and/or multiple reviewers).
- Real third-party backend integrations (host-owned staging work; Mem0/A-Mem patching risk).
- Downstream QA utility study (needs a QA dataset and licensed-data review; LoCoMo is CC BY-NC-SA and cannot be committed).
- New paid LLM conditions beyond the existing held-out run (101,231 reported tokens; billed cost unavailable); any new call needs spend approval and a cache-aware protocol.

## Venue strategy (to finalize with the user)

- **Preferred shape:** workshop or Datasets & Benchmarks track where rigorous negative results and analysis are valued; a full main-conference empirical paper is a stretch given single-reviewer labels and n=36.
- **Check before committing:** next submission cycle, page limit, artifact/evaluation policy, anonymity requirements, and whether negative results are explicitly in scope.
- **Fallback that does not block progress:** arXiv preprint after Bab 3–5 stabilize (no acceptance dependency), then submit to a venue when the second review is in.
- **Do not** promise acceptance; do not name a venue until its CFP is checked and the user approves.

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Second reviewer unavailable | Medium | Keep the blind quiz ready; if unavailable by 2026-11-20, submit as "single-reviewer exploratory" and state it plainly; venue choice may move to workshop/D&B |
| Venue deadline incompatible with evidence quality | Medium | Pick a later cycle; the 2-month goal is submission-ready, not arbitrary-cycle submission |
| Reviewers read the negative result as "no contribution" | Medium | Lead with artefact + information-parity protocol; make the negative result rigorous and transferable; include the fairness audit as a methodological contribution |
| Overclaim slips back into Bab 1/Abstract | Medium | `paper/evidence-map.md` do-not-claim list; independent fact-check before submission |
| Paid-run cost or billing uncertainty | Low | No new paid calls without approval; existing run capped and documented as estimate, not bill |
| Scope creep (3 backends, QA) | High | Explicitly future work; do not reopen unless the user approves a scope/timeline change |
| Main branch churn during writing | Low | Paper files are additive; no code/data/test edits without separate tested commits |

## Deliverables

1. `paper/chapter-3-implementation-method.md` (final Bab 3, English, user template) — **2026-11-09**.
2. `paper/evidence-map.md` (fact sheet), `paper/references.bib` (verified), `paper/bab1-corrections.md`, this plan.
3. Full paper draft (chapters 1–6 + abstract + references) — 2026-12-15.
4. Submission package — 2027-01-07 (venue template, artifact statement, cover letter).
