# Bab 3 review packet — what to read, what was verified, what I need from you

**Prepared:** 2026-11-07. **Purpose:** everything needed to review Bab 3 in ~15 minutes, plus the decisions that unblock the rest of the paper.
**Scope note:** only Bab 3 was requested. Bab 4/5 drafts exist but are **parked** (see §6); no repo code/data/tests were touched by the paper team.

## 1. The deliverable

- **File:** `paper/chapter-3-implementation-method.md` (11,463 words; SHA-256 `633a33b534d395488fa8a6452b122e0f388198eb56ef0e7eae9ec69ee76d9491`; current draft v3, 2026-11-08).
- **Revision log:** v2 was fixed after the independent audit; v3 (2026-11-08, per user feedback) replaced the opening of 3.1 that restated Chapters 1–2 and trimmed the duplicated RQ wording. The change is prose-only — no number was added or changed — so the audit's verified-clean list still applies to the unchanged sections.
- **Language:** English. **Template:** your exact headings — `CHAPTER 3 IMPLEMENTATION METHOD`; `3.1 Thinking Framework`; `3.2 Research Methods`; `3.2.1 Data Collection`; `3.2.2 Exploratory Data Analysis`; `3.2.3 Data Preprocessing`; `3.2.4 Data Splitting`; `3.2.5 Modelling`; `3.2.6 Model Evaluation`. (Validity/ethics sits at the end of 3.2.6 because the template has no separate heading; it can be lifted into 3.2.7 if your template adds one.)
- **Structure:** 8 sections, 7 tables (3.1–3.7), 1 figure (Figure 3.1, inline Mermaid source). The figure is also rendered for the Word template: `paper/figures/figure-3-1-framework.png` (SVG source alongside).
- **What it is:** the method chapter for the pinned evaluated revision — no results leak into it; observed outcomes are deferred to the results chapter.
- **Claim framing:** methods/systems contribution + rigorous negative result. No graph-superiority claim anywhere.

## 2. What was verified (evidence)

| Check | Result |
|---|---|
| Tests at evaluated revision `1ce84ce` | **243 passed** (isolated `git archive`, re-run 2026-11-07) |
| Pinned artifacts unchanged since `1ce84ce` | verified (`git diff 1ce84ce..HEAD -- data/cases data/labels data/manifests data/results/evaluation-v2 data/results/fairness-audit-v2 data/results/llm-heldout-v2` is empty) |
| Every number in Bab 3 | traced to `paper/evidence-map.md` (364 sourced rows) or to the EDA command printed in §3.2.2 |
| Independent fact-check | `paper/fact-check-report.md`: **READY WITH FIXES — 0 blockers, 2 major + 14 minor**, all applied (results-leakage wording, "gold" label vocabulary, cost/telemetry wording, pin wording, heading hierarchy, citation attribution) |
| Citations | 8/8 verified against primary sources (`paper/references.bib` + `paper/references-verification.md`); no invented entries |
| Paid API calls / licensed data | none / none |

**Pin:** rules-only run and fairness audit at commit `1ce84ce`; held-out LLM/semantic run at the code revision recorded in its artifacts, `4190526`. Current `main` is later because a **different session** is committing code work; that later work is deliberately outside the evaluated scope (see `paper/README.md` workspace note). `docs/llm-heldout.md`, `docs/technical-report-milestone-2.md`, and `data/backend-feasibility.md` have post-pin appendices — pinned claims were checked against `git show 1ce84ce:`.

## 3. Decisions already applied (from your answers)

- English primary.
- Your 6-subsection Implementation Method template (corrected "IMPLEMENTATION" spelling; heading hierarchy fixed).
- Your three RQs from the Introduction draft.
- International conference/workshop class, framing = methods/systems + negative result.
- Your instruction to keep the "3-backend adapter" and "downstream QA" ambitions as **future work**: Bab 3 describes only the reference backend + feasibility inspection, and states QA was not measured.

## 4. Things to review in Bab 3 (the judgement calls)

1. **§3.1 Thinking Framework** — is the information-parity argument (text → text_fair isolates annotation access; text_fair → graph isolates representation) acceptable as the chapter's conceptual spine?
2. **§3.2 Research Methods** — the claims boundary (implemented vs future work) and the conditions table.
3. **§3.2.2 EDA** — includes the exact reproduction command; the derived counts are instrument characterisation, not findings.
4. **§3.2.5 Modelling** — the semantic-verifier sentence is pinned to revision `4190526` ("at the revision evaluated in this study…"). This is true of the pin; current `main` has since changed that behaviour.
5. **§3.2.6 Model Evaluation** — metric definitions, HOLD-only vs combined denominators, as-completed vs ITT, and the threats-to-validity paragraph.
6. **Labels** — stated throughout as single-reviewer exploratory, second review pending, no agreement statistic; never "gold".

## 5. Decisions I need from you (blocking the rest of the paper)

| # | Decision | Options / recommendation |
|---|---|---|
| D1 | **Venue + cycle + region + deadline** | International conference/workshop class chosen; need the actual CFP/cycle to set format, length, reference style, artifact statement. Recommendation: name one target; I'll verify the CFP and build the plan backwards. |
| D2 | **Re-pin or stay pinned** | Stay pinned to the evaluated revision (default; Bab 3/4 already consistent) **or** re-pin to the concurrent session's post-evaluation improvements (semantic consumes `contradicts`, cached rerun, Mem0 bridge). Re-pinning means regenerating evidence map, Bab 3 wording, and Bab 4 numbers. |
| D3 | **Bab 4–6 headings** | Your template covered only Bab 3. Approve the proposed Bab 4 (Results 4.1–4.7), Bab 5 (Discussion 5.1–5.7), Bab 6 (Conclusions) structure in `paper/outline-paper.md`, or paste your template. |
| D4 | **Commit `paper/`?** | Currently untracked, deliberately: the concurrent session is active on `main`. Options: keep untracked, commit on a paper branch, or commit to `main` once the concurrent session stops. |
| D5 | **Second human reviewer** | Critical path for an international venue. The blind quiz is ready (`data/review/quiz-v2.html`); a non-author reviewer + third-person adjudication are needed. Do you have someone, or should I prepare a reviewer instructions packet? |

## 6. Parked drafts (not final, not audited/fixed)

- `paper/chapter-4-results.md` (11,135 words incl. Tables 4.1–4.19) + `paper/results-tables.md` + `paper/appendix-bab4-notes.md`.
  Independent audit: **NOT READY — 1 blocker, 1 major, 10 minor**. The blocker is a mislabeled metric: the artifact's `case_error` value 12/36 is the expected-`HOLD` label count, not "cases with ≥1 candidate error" (honest derived figure: 14/36). Not fixed, per the pause.
- `paper/chapter-5-discussion.md` (3,397 words) — prose-only interpretation, never independently audited.
- Both are additive and deletable; nothing references them from Bab 3.

## 7. Suggested next actions

1. You review Bab 3 (§4 above) and answer D1/D2/D3.
2. I apply any Bab 3 edits, then either finalize Bab 4 (fix the blocker, re-audit) or discard the parked Bab 4/5, per D2/D3.
3. Baseline for any next step stays: no invented numbers/citations; no paid calls; no licensed data; pinned evidence.

## 8. File index

| File | Purpose |
|---|---|
| `paper/chapter-3-implementation-method.md` | **Bab 3 deliverable (review this)** |
| `paper/outline-bab3.md` | heading list, artifact mapping, guardrails |
| `paper/evidence-map.md` | canonical facts with sources |
| `paper/fact-check-report.md` | Bab 3 audit + applied fixes |
| `paper/bab1-corrections.md` | required Bab 1/2 corrections |
| `paper/references.bib` / `references-verification.md` | verified citations + audit |
| `paper/outline-paper.md` | full-paper chapter map (D3) |
| `paper/plan-paper-2bulan.md` | milestones, critical path, risks |
| `paper/README.md` | workspace status + pinned-revision + concurrent-session note |
