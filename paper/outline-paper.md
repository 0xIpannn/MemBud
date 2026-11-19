# MemBud paper — master outline and evidence plan

**Status:** working outline, 2026-11-07. Bab 3 complete (draft v2, fact-checked). Bab 4 draft assembled (11,135 words incl. 19 tables) and under independent audit.
**Pinned basis:** rules-only run and fairness audit at commit `1ce84ce`; held-out LLM/semantic run at code revision `4190526`. Current `main` is later (concurrent session) and is **not** part of the evaluated evidence.
**Framing:** methods/systems contribution + rigorous negative result. No graph-superiority claim, no acceptance promise.

## Working title

*MemBud: Verifying Memory-State Transitions Before Commit — A Controlled Comparison of Graph and Text Verification* (alternatives: "…and the Limits of Representation"; final title decided with venue).

## Chapter map

| Chapter | Working title | Status | Evidence base |
|---|---|---|---|
| Abstract | — | pending (after Bab 5) | Bab 4/5 |
| 1 | Introduction | user draft exists; corrections required (`paper/bab1-corrections.md`) | `PLAN.md` §3, user Introduction, verified bib |
| 2 | Related Work / Literature Review | user draft exists; needs citation fixes + FadeMem verification | `paper/references.bib`, user LitReview |
| 3 | Implementation Method | **done** (`paper/chapter-3-implementation-method.md`, 11,782 words, fact-checked) | `paper/evidence-map.md` |
| 4 | Results | **draft assembled** (`paper/chapter-4-results.md`; tables in `paper/results-tables.md`) — audit pending | rules/fairness/LLM artifacts (pinned) |
| 5 | Discussion | pending | Bab 4 + threats already framed in 3.2.6 |
| 6 | Conclusion and Future Work | pending | Bab 3–5 |
| — | References | 8 verified entries (`paper/references.bib`); add only verified sources | `paper/references-verification.md` |
| — | Artifact/reproducibility statement | pending (venue-dependent) | repository + pinned artifacts |

Note: the user supplied explicit headings only for Bab 3. Bab 4–6 headings below are a proposal for review, not a user template.

## Chapter 4 structure (current draft plan)

- 4.1 Instrument and candidate-update errors (denominators, fault encoding).
- 4.2 RQ1: guarded vs unguarded (rules-only decision counts, HOLD-only detection, false accepts/holds, valid acceptance, invalid-input handling).
- 4.3 RQ1: graph vs non-graph and the information-parity audit (two paired differences; text→text_fair vs text_fair→graph contrasts; answer: annotation access, not structure).
- 4.4 RQ1: held-out model-based conditions (graph-LLM over-conservative; text-LLM; semantic HELD-13 under revision 4190526; budgets differ → descriptive).
- 4.5 RQ3: cost, tokens, latency, acceptance trade-offs (rules timer noise; paid run tokens/attempts; billed cost unavailable; QA not measured).
- 4.6 RQ2: modularity evidence (adapter contract + reference backend interception + byte-identical HOLD; Mem0/A-Mem feasibility inspection; no third-party compatibility; 3-backend evaluation = future work).
- 4.7 Summary of findings (RQ → answer → evidence → not-supported table).

## Chapter 5 structure (proposed)

- 5.1 The negative result: what the fairness audit establishes and what it does not (no detectable representation-level advantage at n=36; not equivalence).
- 5.2 Why the plain-text comparison was misleading (annotation access; evaluation-design lesson).
- 5.3 Trade-offs: false holds, over-conservatism, token/latency costs, fail-closed behaviour.
- 5.4 RQ2: modularity and the pre-commit boundary problem (why third-party integration needs host-owned staging).
- 5.5 Threats to validity and ethics (carry forward 3.2.6; add reviewer/annotation limits).
- 5.6 Implications for the graph-memory hypothesis and for evaluation practice.

## Chapter 6 structure (proposed)

Conclusions by RQ; contributions (artefact, information-parity protocol, negative result); future work (independent second review, powered held-out set, contradiction-aware semantic verifier, real backend staged integration, downstream QA with licence review, re-pinning to post-evaluation improvements); closing statement (no superiority claim, no production claim).

## RQ evidence matrix (master)

| RQ | Question | Where answered | Key artifacts | Claim strength |
|---|---|---|---|---|
| RQ1a | Does guarded verification reduce omission/corruption/unsupported additions vs unguarded? | 4.2, 5.1 | `data/results/evaluation-v2/`, fairness audit | measured on synthetic single-reviewer instrument; descriptive |
| RQ1b | Does graph verification catch more errors than non-graph under the same criteria/budget? | 4.3, 4.4, 5.1–5.2 | paired differences; fairness audit; held-out LLM run | **negative on this instrument**: no detectable representation advantage; visible difference = annotation access |
| RQ2 | Can the layer run across backends via adapters; what information is required/lost? | 4.6, 5.4 | `src/membud/adapters/`, `data/adapter-contract.md`, `data/backend-feasibility.md` | partially answered: contract + reference backend; third-party integration and 3-backend evaluation = future work |
| RQ3 | Trade-offs: acceptance, downstream QA, latency, tokens, cost | 4.5, 5.3 | metrics JSON, LLM summary | partially answered: acceptance/false holds/tokens/latency; billed cost unavailable; QA not measured |

## Claim discipline (binding on every chapter)

1. No invented numbers/citations; `paper/evidence-map.md` + `paper/references.bib` are the only sources.
2. Labels are single-reviewer exploratory; second review pending; no agreement statistic. Never "gold".
3. No superiority/significance/causal/production/third-party/QA claims.
4. Define "faulty" denominators explicitly (HOLD-only vs combined vs LLM split).
5. A zero paired difference = "no detectable difference at this n", never equivalence.
6. LLM conditions are not equal-budget; report descriptively.
7. Billed cost unavailable; token counts are telemetry; rules latency is timer noise.

## Open decisions (user)

1. Venue + cycle/deadline → final formatting, length, reference style, artifact statement.
2. Re-pin to post-evaluation HEAD? (semantic fix + cached rerun + Mem0 bridge exist; would require regenerating Bab 3 wording, evidence map, and Bab 4.) Default: stay pinned.
3. Commit `paper/` to git or keep untracked while the concurrent session works.
4. Bab 4–6 headings: approve the proposed structure or supply a template (as was done for Bab 3).
5. Second human reviewer recruitment (critical path for an international venue).

## Milestones

| Date | Milestone | Status |
|---|---|---|
| 2026-11-09 | Bab 3 assembled + fact-checked | **done (2026-11-07)** |
| 2026-11-30 | Bab 4 draft + tables | **drafted 2026-11-07, audit in progress** |
| 2026-12-07 | Bab 5 + Bab 6 + Abstract | pending |
| 2026-12-15 | Full draft v1 | pending |
| 2026-12-22 | Internal review round 2 + formatting | pending |
| 2027-01-07 | Submission-ready (venue deadline to match) | pending |
