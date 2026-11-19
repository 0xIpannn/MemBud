# Venue options and decision matrix (international conference/workshop class)

**Status:** prepared for your decision; **no venue is chosen and no deadline is asserted here**. You selected "international conference/workshop (ACL/EMNLP/NeurIPS D&B/COLM class)". Before committing to any target, the actual CFP must be checked on the official page — cycles move and this file intentionally contains no dates from memory.

## What the evidence can and cannot support today

| Strength | Current evidence | Gap for a competitive submission |
|---|---|---|
| Artefact + design | canonical schema v2, structural graph verifier, fair text control, versioned store, reference backend, 243 tests at pinned commit | third-party integration absent (source inspection only) |
| Rigorous negative result | information-parity audit: fair text matches graph on all 36 cases (0 paired differences); two differing cases explained by annotation access | single-reviewer labels; second independent review pending |
| Controlled LLM comparison | one capped held-out run at revision `4190526`; descriptive only | small held-out split (7 valid / 6 HOLD), non-equal budgets, no independent annotation |
| Cost/latency instrumentation | tokens, attempts, estimate range, timer-noise caveat | billed cost unavailable; no downstream QA |

## Option classes (fit vs cost)

| Option class | Fit for this paper | Requirements to verify in the CFP | Evidence gaps before submitting | Effort to adapt |
|---|---|---|---|---|
| **Workshop at a major venue** (e.g. an ACL/EMNLP/NeurIPS workshop on agents/memory/evaluation) | **Strongest fit:** rigorous negative results and evaluation methodology are in scope; shorter format | page limit, archival vs non-archival, anonymity, artifact policy, whether negative results are explicitly welcome | second review strongly preferred but a workshop may accept "exploratory" with explicit limitation | low–medium (8-page format) |
| **NeurIPS Datasets & Benchmarks track** | Good if framed as a benchmark/evaluation-protocol contribution (the information-parity audit is reusable) | D&B track scope, artifact/eval checklist, compute/data statements, anonymity | licensed datasets are a problem: no HaluMem/LoCoMo data may be committed; synthetic set is small | medium (needs dataset/benchmark framing, not just system) |
| **COLM** (conference on language modeling) | Plausible for agent-memory evaluation; values analysis | scope, page limit, submission cycle, archival policy | second review + stronger held-out evaluation likely required | medium |
| **ACL/EMNLP main or Findings** (via ARR) | Possible for a methods + negative-result paper, but competitive | ARR cycle dates, responsible-NLP checklist, limitations section, artifact policy | single-reviewer labels and n=36 are likely blockers; second review + powered set needed | high (full 8-page + appendix + thorough related work) |
| **International journal** (e.g. TACL, JAIR, ACM TIST, Elsevier) | TACL fits methods/evaluation; review cycle long | journal-specific formatting, data availability, ethics | same as ARR plus longer cycle; not aligned with a ~2-month goal | high |
| **arXiv preprint alone** | Not a peer-reviewed venue, but unblocks dissemination | n/a (moderation) | none for posting; must keep pin + limitations | low |
| **Indonesian national journal/conference (SINTA)** | Fallback if the international route slips; English article acceptable | SINTA rank, template, review time | similar evidence gaps but typically more tolerant of exploratory n | low–medium |

## Recommendation (for your decision)

1. **Primary:** a workshop or D&B-style venue where evaluation methodology and negative results are first-class. That maximizes acceptance odds for the *current* evidence and fits the 2-month goal.
2. **Secondary:** COLM/ACL-Findings if the second human review is collected and a larger held-out set is added (not feasible in 2 months without new spend/annotation).
3. **Do not** target a main-conference empirical track on the current evidence; reviewers will (correctly) challenge single-reviewer labels, n=36, and the semantic-verifier gap at current HEAD unless the paper is re-pinned.
4. **Always:** arXiv preprint as a no-risk milestone once Bab 3–5 are stable; it does not substitute for peer review.

## What I need from you (D1)

- Target venue name (or a shortlist to verify), **region** (EU/US/Asia), and the **deadline** you want to aim at.
- Once named, I will: fetch the official CFP page, extract limit/format/anonymity/artifact requirements, check the next cycle deadline, and update `paper/plan-paper-2bulan.md` backwards from it.
- I will not assert a deadline or acceptance probability until the CFP is checked.

## CFP verification checklist (to run when the venue is named)

- [ ] Official CFP URL and cycle dates (submission, notification, camera-ready).
- [ ] Page limit and template; appendix/references policy.
- [ ] Archival vs non-archival; dual-submission rules.
- [ ] Anonymity requirements (and whether the artifact/repo must be anonymised).
- [ ] Artifact/evaluation policy; reproducibility statement requirements.
- [ ] Ethics/limitations statement requirements; data-availability policy.
- [ ] Whether negative results / evaluation papers are explicitly in scope.
- [ ] Whether a workshop paper can later be extended to a main-conference submission.
