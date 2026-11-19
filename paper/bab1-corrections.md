# Bab 1 / Literature Review corrections required before submission

**Purpose:** align the user's Introduction + Literature Review draft with (a) verified bibliographic metadata and (b) what the repository actually supports. These are edits to Bab 1/2, not Bab 3; Bab 3 already follows the corrected scope. Do not paste the old claims into the final paper.

## A. Citation corrections (verified 2026-11-07; see `paper/references-verification.md`)

| # | Draft says | Correct | Action |
|---|---|---|---|
| 1 | MemGPT (Packer et al., 2024) | Packer et al., **2023** (arXiv v1 2023-10-12; v2 Feb 2024, no peer-reviewed venue) | Change year to 2023, or keep 2024 consistently in text **and** bib if deliberately citing v2. Do not mix. |
| 2 | TrustMem… "TrustTMem" typo; venue unspecified | Canonical name **TrustMem**; **Findings of EMNLP 2026**, pp. 51948–51965, DOI 10.18653/2026.findings-emnlp.2419 | Fix spelling; if a venue is named, say Findings (not main). "TRUSTMEM" in repo docs is a small-caps rendering artifact, not a second system. |
| 3 | HaluMem (Chen et al., 2026) | Correct: **EMNLP 2026 Main**, pp. 5073–5108, DOI 10.18653/2026.emnlp-main.220 | Keep year 2026; use the ACL title. |
| 4 | A-Mem (Xu et al., 2025) | Correct; **NeurIPS 2025** poster. Use "A-Mem" (arXiv styles "A-MEM") | Keep. |
| 5 | LoCoMo (Maharana et al., 2024) | Correct; **ACL 2024**, pp. 13851–13870 | Keep. |
| 6 | Mem0 (Chhikara et al., 2025) | Correct; **arXiv preprint** (2504.19413) | Do not call it peer-reviewed/published; "2025" fine. |
| 7 | Zep (Rasmussen et al., 2025) | Correct; **arXiv preprint** (2501.13956). Authors: Rasmussen, Paliychuk, Beauvais, Ryan, Chalef | Fix if any author spelling is wrong (not "Paliwal"). |
| 8 | FadeMem (Wei et al., 2026) | **arXiv preprint** (2026) | Do not call it published. |
| 9 | "Mem0's graph variant scored only about 2% higher" | Verified verbatim in Mem0's abstract: "graph memory achieves around 2% higher overall score than the base configuration" | Attribute as **the Mem0 authors' self-reported result**, not an independent finding. |

## B. Claim corrections (evidence boundary)

| # | Draft claim | Problem | Suggested replacement |
|---|---|---|---|
| B1 | Contribution (2): "an adapter interface **evaluated across 3 backends**" | Only a reference backend (in-memory/SQLite) was implemented and tested; Mem0/A-Mem were source-inspected only; no third-party integration or compatibility exists. | "**(2) an adapter contract and a reference backend that demonstrate write interception, together with a documented feasibility assessment of integrating the layer with two existing memory systems.**" Put the 3-backend evaluation in Future Work. |
| B2 | Contribution (3) / RQ3: "controlled comparison … **question answering**, and cost" | No downstream-QA utility was evaluated; the harness has no QA condition. | "**(3) a controlled comparison of graph and non-graph verification on transition errors, update acceptance, latency and token cost, under an information-matched control.**" Put QA in Future Work; RQ3 should say QA is out of scope for this study. |
| B3 | "returns an **accept, revise/retry, or reject/hold** decision" | No REVISE/retry decision exists. Implemented: `ACCEPT`, `HOLD`, `INVALID_INPUT` (plus execution `ERROR` in metrics); retries are execution events that never authorise a commit. | "returns an **accept or hold** decision, with schema-invalid transitions reported separately as invalid input." |
| B4 | Any implication that "graph-based verification catches contradictions a text verifier cannot" | Contradicted by the fairness audit: the fair text control reads the same `evidence.contradicts` and matches graph on all 26 non-invalid v2 cases. The visible difference is **annotation access**, not structural inference. | Keep the hypothesis open; remove any capability claim from Introduction/Abstract. In Bab 1, say the study tests whether structure adds anything beyond information access — and that the answer turned out to be negative on this instrument. |
| B5 | "graph superiority" framing anywhere | Not supported. | Methods/systems contribution + rigorous negative result. |

## C. Items that must be checked by the user before reuse

1. **FadeMem numeric claims** in the Literature Review (e.g. "reduces storage by 45%", "retains 82.1%") — these were not verifiable from the citation check in this session; verify against the FadeMem paper before submission, or drop the numbers and describe the mechanism qualitatively.
2. **HaluMem repository review note** — `data/README.md` line 11 says the repo was reviewed at commit `d653749…` on 2026-04-21, but lit-verifier found that commit dated 2026-10-28. The hash and the date cannot both be right. Fix the date or the hash before citing the review note. (Small doc fix; not blocking Bab 3.)
3. **"Workplace assistant" example** (project requirements across sessions) — fine as motivation; no citation needed.
4. **"MemGPT … manage, consolidate, and link stored information"** — MemGPT is about virtual context management, not exactly consolidation/linking; consider softening to "manage context and long-term storage". Verify against the paper.
5. The user's draft RQs are otherwise compatible with the implemented study: RQ1 is answerable at the synthetic-instrument level; RQ2 is answerable only as a contract/reference-backend/feasibility question (say so); RQ3 is answerable for acceptance/false-holds/cost but not QA.

## D. Language / consistency notes

- Keep one system name per citation: TrustMem (not TrustTMem/TRUSTMEM), A-Mem, Mem0, MemGPT, HaluMem, LoCoMo, Zep, FadeMem.
- Citation style: the draft is author–year; `paper/references.bib` is the only permitted source. Markdown drafts use `[@key]`; the venue's LaTeX style can be applied later.
- Single-reviewer wording must appear wherever labels/ground truth are first mentioned: "single-reviewer exploratory labels; a second independent review is pending (blind quiz built, not yet collected); no inter-annotator agreement exists."
