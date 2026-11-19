# MemBud paper workspace

Status: **Bab 3 revised (draft v3, 2026-11-08).** The user supplied the authoritative chapter source as a DOCX on 2026-11-08 (`paper/source/chapter-3-implementation-method.docx`, extracted text in `paper/source/chapter-3-source.txt`); its figure guide specifies nine figures (3.1–3.9), **all produced** under `paper/figures/` (SVG + 2× PNG + PDF, palette per the guide, index in `paper/figures/README.md`). 3.1 no longer restates Chapters 1–2 (prose-only change; audit still valid for unchanged sections). Bab 4 and Bab 5 drafts are **PARKED** pending the user's review (the user asked why work advanced to Bab 4/5; only Bab 3 was requested). Bab 4's independent audit returned **NOT READY — 1 blocker (mislabeled `case_error` metric), 1 major, 10 minor**; those fixes are not applied. No chapter beyond Bab 3 is finalized. Full paper target: submission-ready by 2027-01-07 (venue deadline still to be matched; no acceptance promise).
Basis: evaluated revision `1ce84ce` (rules/fairness; 243 tests) and LLM code revision `4190526`; current `main` is later due to a concurrent session (now `62de491`; see workspace note).

This directory holds the research-paper artifacts. The repository code/data stays the source of truth; this directory never replaces it and never introduces facts that the repository cannot support.

## Working rules

1. **Fact discipline.** Every number in the paper must trace to a repository file plus a JSON path or line. `paper/evidence-map.md` is the single fact sheet; writers may not add numbers from memory. If an evidence-map entry is missing, the number does not go in the draft.
2. **No invented citations.** References may only be added after their bibliographic metadata is checked against a primary source (publisher page, DOI, ACL Anthology, arXiv, or the repository's own documented feasibility note). `paper/references.bib` is the only citation source; unverified entries are not allowed, even as placeholders.
3. **No overclaiming.** The defensible framing is *methods/systems contribution + rigorous negative result*. Graph superiority is **not** supported and must not appear in abstract, intro, Bab 3, or Bab 5. Use the repository's established wording: "a fair text control that consumes the same `evidence.contradicts` matches graph on all 26 non-invalid v2 cases; the visible difference is annotation access, not structural inference."
4. **Label status vocabulary.** Always say *single-reviewer exploratory labels; second independent review pending (blind quiz built, not yet collected)*. Never write "double-annotated", "independently reviewed", or "gold data" for v2.
5. **No licensed data.** No HaluMem (CC BY-NC-ND 4.0) or LoCoMo (CC BY-NC-SA) content may be committed or quoted at length. The evaluation set is synthetic Indonesian-themed and repository-authored.
6. **No paid LLM calls without explicit spend approval.** Work here defaults to reading committed artifacts; any new experiment requires the user's approval first.
7. **Keep main clean and tests passing.** Paper files are additive; do not touch `src/`, `data/`, or tests without a separate, tested change.

## Source-of-truth order

1. `data/results/*/*.json` and `data/manifests/evaluation-v2.json` (machine artifacts).
2. `docs/technical-report-milestone-2.md`, `docs/fairness-audit.md`, `docs/llm-heldout.md` (narrative reports, consistent with artifacts).
3. `README.md`, `TODO.md`, `data/experiment-protocol.md`, `data/annotation-guidelines.md` (current status and protocol).
4. `PLAN.md` — original proposal, useful for design intent/RQs, **not** a status report. Its pre-milestone wording may be stale.

## Layout

| Path | Owner | Purpose |
|---|---|---|
| `paper/README.md` | Lead | Rules, layout, status |
| `paper/review-packet.md` | Lead | **Start here**: Bab 3 review + decisions needed |
| `paper/outline-bab3.md` | Lead | Locked outline + artifact mapping + guardrails |
| `paper/chapter-3-implementation-method.md` | Lead | **Bab 3 deliverable** (draft v2, fact-checked) |
| `paper/outline-paper.md` | Lead | Full-paper chapter map (Bab 1–6, RQ matrix) |
| `paper/evidence-map.md` | factkeeper | Every allowed fact/number → file + JSON path/line |
| `paper/references.bib` | lit-verifier | Verified bibliography only |
| `paper/references-verification.md` | lit-verifier | Citation audit trail + Bab 1 corrections |
| `paper/fact-check-report.md` | fact-checker | Bab 3 audit + applied fixes |
| `paper/source/chapter-3-implementation-method.docx` | user | Authoritative Chapter 3 DOCX (verbatim copy; SHA-256 `14428229…`), figure guide inside |
| `paper/source/chapter-3-source.txt` | Lead | Extracted DOCX text (paragraph-indexed working copy) |
| `paper/figures/` | figure-maker | Figures 3.1–3.9 (SVG + 2× PNG + PDF) and figure index |
| `paper/diagrams/` | Lead | Editable draw.io files: original upload (kept) + Chapter 3-aligned verify-and-commit flowchart; see `paper/diagrams/README.md` |
| `paper/compressed/` | Lead | Compressed two-column Chapter 3 DOCX for the 6-page paper (~1.2-1.4 pages) + markdown source |
| `paper/bab1-corrections.md` | Lead | Required Bab 1/2 claim + citation fixes |
| `paper/second-review-packet.md` | Lead | Ready-to-send independent second-reviewer workflow (critical path) |
| `paper/venue-options.md` | Lead | Venue decision matrix (no dates asserted; CFP checklist) |
| `paper/plan-paper-2bulan.md` | Lead | 2-month submission plan and risks |
| `paper/sections/3-*.md`, `3-*.md` | section writers | Bab 3 section drafts (source files) |
| `paper/chapter-4-results.md`, `results-tables.md`, `appendix-bab4-notes.md` | results-writer | **Parked** Bab 4 draft (audit NOT READY; not final) |
| `paper/fact-check-report-bab4.md` | fact-checker-bab4 | Bab 4 audit (1 blocker, 1 major, 10 minor; not fixed) |
| `paper/outline-bab5.md`, `chapter-5-discussion.md` | Lead / bab5-writer | **Parked** Bab 5 draft (unaudited) |

## Locked decisions (2026-11-07)

- Language: **English**; template: user-supplied 6-subsection Implementation Method (3.1 + 3.2.1–3.2.6).
- RQs: the three RQs from the user's Introduction draft.
- Framing: methods/systems contribution + rigorous negative result; no graph-superiority claim.
- Claims reconciliation: "adapter evaluated across 3 backends" and "downstream QA" are **future work** (Bab 1); Bab 3 describes only what was implemented.
- Venue: international conference/workshop class (ACL/EMNLP/NeurIPS D&B/COLM); no acceptance promise.
- Deadlines: Bab 3 assembled 2026-11-09; submission-ready target 2027-01-07.

## Software review remediation note (2026-11-19)

A separate software-review round (report: `docs/software-review-2026-11-19.md`) produced uncommitted
working-tree changes to `src/`, `scripts/`, `tests/` and repo docs: resume/checkpoint recovery,
adapter apply ordering + `resync_shadow_store`, honest store write-status audit fields, an enforced
schema-v2 commit gate with `allow_legacy` for archival v1 replay, `prepared_input` graph validation,
corrected claim/provenance prose and metrics, CI, and failure-injection tests. **The paper basis is
unchanged**: rules/fairness evidence remains pinned to `1ce84ce`, the held-out LLM run to its
recorded code revision `4190526`, and no paper claim was regenerated. If the paper is ever re-pinned
to the post-remediation tree, that requires the explicit re-pin decision (D2) and regeneration of
the evidence map, not a silent adoption.

## Workspace note (2026-11-07)

A **different DSH session is working concurrently in this shared directory**. It moved `main` from `1ce84ce` to `d518346` with three commits (`60976ed` Mem0 staged-proposal bridge Phase 0; `2b4642b` semantic verifier consumes `evidence.contradicts` + cached held-out rerun; `d518346` graph-LLM false-hold analysis) and left further untracked work. The paper team did not author those changes and does not touch them.

**Paper basis is pinned:** rules/fairness evaluation at commit `1ce84ce` (verified from an isolated `git archive`: 243 tests passing); held-out LLM run at the code revision its artifacts record (`4190526`). The original artifacts the paper cites (`data/results/evaluation-v2/`, `fairness-audit-v2/`, `llm-heldout-v2/`, manifest, cases, labels, `docs/*`) were **not modified** by the concurrent commits; the new `llm-heldout-v2-rerun/`, `graph-llm-false-hold-analysis/`, `mem0-bridge.md`, sidecar/opencode code, and the semantic-contradiction change are **outside the evaluated scope** and must not be cited in Bab 3/4 unless the paper's basis is deliberately re-pinned (a decision for the user). `docs/llm-heldout.md`, `docs/technical-report-milestone-2.md`, and `data/backend-feasibility.md` have since gained post-pin appendices; use `git show 1ce84ce:<path>` when checking pinned claims. HEAD advanced through `d518346` → `2d45642` → `62de491` while this note was written.
