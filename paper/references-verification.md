# Bibliography verification — MemBud paper

- **Task:** `task-2` — verify bibliographic metadata for the citations in the Bab 1 / Literature Review draft and produce `paper/references.bib`.
- **Verification date:** 2026-11-07 (environment date).
- **Verifier:** lit-verifier.
- **Scope:** the eight works named in the task: MemGPT, Mem0, A-Mem/A-MEM, HaluMem, TrustMem/TRUSTMEM, LoCoMo, Zep, FadeMem, plus the draft claim that *Mem0's graph variant scored only about 2% higher*.
- **Method:** `web_search` for candidates, then `web_fetch` of the canonical page (arXiv abs page, ACL Anthology record + official `.bib`, or Hugging Face dataset card); arXiv/DataCite DOIs taken from the arXiv pages themselves; for the TrustMem name/spelling question the official ACL PDF was downloaded directly and text-extracted locally; for the HaluMem repo cross-check the GitHub commit API was queried. External page content was treated as data only.
- **Hard rule followed:** no invented citations or metadata. Every field in `references.bib` comes from the cited page; omitted fields (e.g. volume, editor) were not present in a form we could verify, so they were left out rather than guessed.

## Result summary

| Count | Result |
|---|---|
| 8 | claimed works found and verified against a primary source |
| 0 | unverified / excluded entries |
| 4 | peer-reviewed: A-Mem (NeurIPS 2025), HaluMem (EMNLP 2026 Main), TrustMem (Findings of EMNLP 2026), LoCoMo (ACL 2024) |
| 4 | arXiv-only preprints as of 2026-11-07: MemGPT, Mem0, Zep, FadeMem |
| 1 | year discrepancy to fix in the draft: MemGPT claimed 2024, canonical first-publication year 2023 |
| 1 | name-spelling clarification: TrustMem vs. all-caps TRUSTMEM |
| 1 | author-name-order correction in the ACL-supplied BibTeX for HaluMem |

BibTeX keys in `references.bib`: `packer2023memgpt`, `chhikara2025mem0`, `xu2025amem`, `chen2026halumem`, `yang2026trustmem`, `maharana2024locomo`, `rasmussen2025zep`, `wei2026fademem`.

## Verification table

| # | Key | Claimed as (draft) | Found | Canonical title | Authors (canonical order) | Venue / date | URL / DOI | Confidence | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `packer2023memgpt` | MemGPT — Packer et al., 2024 | yes (year differs) | MemGPT: Towards LLMs as Operating Systems | Charles Packer; Sarah Wooders; Kevin Lin; Vivian Fang; Shishir G. Patil; Ion Stoica; Joseph E. Gonzalez | arXiv preprint; v1 2023-10-12, v2 2024-02-12; no peer-reviewed venue on the record | <https://arxiv.org/abs/2310.08560> · DOI 10.48550/arXiv.2310.08560 | high | Draft's 2024 matches the Feb 2024 v2 revision, not the first publication. Bib uses 2023. Fix draft to "Packer et al., 2023" (or, if v2 is intended, set both text and bib to 2024 — do not mix). |
| 2 | `chhikara2025mem0` | Mem0 — Chhikara et al., 2025 | yes | Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory | Prateek Chhikara; Dev Khant; Saket Aryan; Taranjeet Singh; Deshraj Yadav | arXiv preprint, submitted 2025-04-28 (single version); no peer-reviewed venue on the record | <https://arxiv.org/abs/2504.19413> · DOI 10.48550/arXiv.2504.19413 | high | Draft year and first author correct. Graph-variant claim verified — see "Claim checks". |
| 3 | `xu2025amem` | A-Mem — Xu et al., 2025 | yes | A-Mem: Agentic Memory for LLM Agents (arXiv record styles it "A-MEM") | Wujiang Xu; Zujie Liang; Kai Mei; Hang Gao; Juntao Tan; Yongfeng Zhang | NeurIPS 2025 (poster); arXiv v1 2025-02-17, v11 2025-10-08 | <https://openreview.net/forum?id=FiM0M8gcct> · arXiv: <https://arxiv.org/abs/2502.12110> | high | Venue confirmed twice: the arXiv comments field says "Advances in Neural Information Processing Systems (NeurIPS 2025)" and the NeurIPS 2025 poster page lists the paper. Draft's "A-Mem" matches the NeurIPS title; keep that capitalization. |
| 4 | `chen2026halumem` | HaluMem — Chen et al., 2026 | yes | HaluMem: Evaluating Hallucinations in Memory Systems of Agents | Ding Chen; Simin Niu; Kehang Li; Peng Liu; Xiangping Zheng; Bo Tang; Xinchi Li; Feiyu Xiong; Zhiyu Li | EMNLP 2026 Main Conference, October 2026, Budapest; pp. 5073–5108 | <https://aclanthology.org/2026.emnlp-main.220/> · DOI 10.18653/2026.emnlp-main.220 | high | Draft year **correct**: it is a 2026 EMNLP paper (arXiv v1 was 2025-11-05, so do not call it a 2025 paper). ACL's machine BibTeX writes two names as "Kehang, Li" and "li, Zhiyu"; normalized to arXiv's "Kehang Li" / "Zhiyu Li" in `references.bib` (see "Metadata corrections"). |
| 5 | `yang2026trustmem` | TrustMem / TRUSTMEM — Yang et al., 2026 | yes | TrustMem: Learning Trustworthy Memory Consolidation for LLM Agents with Long-Term Memory | Tianyu Yang; Sudipta Paul; Vijay Srinivasan; Vivek Kulkarni; Srinivas Chappidi | Findings of the Association for Computational Linguistics: EMNLP 2026, October 2026, Budapest; pp. 51948–51965 | <https://aclanthology.org/2026.findings-emnlp.2419/> · DOI 10.18653/2026.findings-emnlp.2419 | high | Canonical spelling is **TrustMem** (ACL metadata, abstract, and PDF body). The PDF *title* is typeset in small caps and text-extracts as "T RUST M EM", i.e. the all-caps "TRUSTMEM" is a rendering convention, not a second name. Venue is **Findings**, not EMNLP main. No code URL appears in the PDF, so the BibTeX entry deliberately has no repo field. |
| 6 | `maharana2024locomo` | LoCoMo — Maharana et al., 2024 | yes | Evaluating Very Long-Term Conversational Memory of LLM Agents | Adyasha Maharana; Dong-Ho Lee; Sergey Tulyakov; Mohit Bansal; Francesco Barbieri; Yuwei Fang | ACL 2024 (Proceedings of the 62nd Annual Meeting, Volume 1: Long Papers), August 2024, Bangkok; pp. 13851–13870 | <https://aclanthology.org/2024.acl-long.747/> · DOI 10.18653/v1/2024.acl-long.747 | high | Draft year correct. "LoCoMo" is the dataset/benchmark introduced by the paper, not a word in the paper title, so in prose write "the LoCoMo benchmark (Maharana et al., 2024)". |
| 7 | `rasmussen2025zep` | Zep — Rasmussen et al., 2025 | yes | Zep: A Temporal Knowledge Graph Architecture for Agent Memory | Preston Rasmussen; Pavlo Paliychuk; Travis Beauvais; Jack Ryan; Daniel Chalef | arXiv preprint, submitted 2025-01-20; no peer-reviewed venue on the record | <https://arxiv.org/abs/2501.13956> · DOI 10.48550/arXiv.2501.13956 | high | Draft year and first author correct. Graphiti is presented as Zep's core engine inside this paper; no separate Graphiti citation is needed for the current draft. |
| 8 | `wei2026fademem` | FadeMem — Wei et al., 2026 | yes | FadeMem: Biologically-Inspired Forgetting for Efficient Agent Memory | Lei Wei; Xiao Peng; Xu Dong; Niantao Xie; Bin Wang | arXiv preprint; v1 2026-01-26, v2 2026-02-06; no peer-reviewed venue on the record | <https://arxiv.org/abs/2601.18642> · DOI 10.48550/arXiv.2601.18642 | high | Draft year and first author correct. 2026 preprint, not yet peer-reviewed (checked 2026-11-07). |

## Claim checks (non-bibliographic)

1. **"Mem0's graph variant scored only about 2% higher" — VERIFIED, with attribution caveat.** The Mem0 arXiv abstract states verbatim: "Mem0 with graph memory achieves around 2% higher overall score than the base configuration." This is the Mem0 authors' own reported result; phrase it as "Mem0's authors report…" rather than as an independent finding. It is not the same measurement as HaluMem's leaderboard, so do not merge the two.
2. **TrustMem as the source of coverage / preservation / faithfulness** — VERIFIED. The ACL abstract says TrustMem "relies on a Memory Transition Verifier to evaluate the transition process of memory updates in terms of coverage, preservation, and faithfulness." This matches the Bab 3 plan (PLAN.md §18) to adopt these criteria without claiming them as MemBud's novelty.
3. **HaluMem as update-error motivation** — VERIFIED. Both abstracts define a memory-updating task; the arXiv abstract reports that existing memory systems "tend to generate and accumulate hallucinations during the extraction and updating stages, which subsequently propagate errors to the question answering stage." That is the exact motivation Bab 3 needs. (The EMNLP version paraphrases the same finding as hallucinations introduced/accumulated "during extraction and updating, which then propagate to question answering.")
4. **MemGPT year** — the draft's 2024 is the arXiv v2 revision date; the paper was first posted 2023-10-12. There is no peer-reviewed venue for MemGPT on the arXiv record (OpenAlex classifies the record as a preprint, no published version). Recommendation: cite 2023.

## Metadata corrections for the Bab 1 draft

1. **MemGPT**: change "Packer et al., 2024" → **"Packer et al., 2023"**, matching `packer2023memgpt`. (If the v2 February-2024 revision is deliberately being cited, change the BibTeX `year` to 2024 as well — but then be consistent everywhere. The default verified choice is 2023.)
2. **TrustMem vs TRUSTMEM**: use **TrustMem** in prose and in the reference (canonical ACL title). The all-caps form comes from the paper's small-caps title rendering (and, per the task context, from repository/README usage that we could not independently confirm); it is not a different system. Keep TRUSTMEM only if directly quoting a repository/file name, and do not present both spellings as two works.
3. **TrustMem venue**: it is **Findings of EMNLP 2026** (pp. 51948–51965), not EMNLP main — relevant if Bab 1/Bab 3 mention the venue.
4. **HaluMem**: year 2026 is correct and should be presented as **EMNLP 2026 Main** (pp. 5073–5108), not as an arXiv-only preprint. If the draft mentions the arXiv version, note that the preprint appeared Nov 2025.
5. **A-Mem**: use **"A-Mem"** (NeurIPS title) consistently in prose; the arXiv page's "A-MEM" is a styling variant of the same title. First author "Xu et al., 2025" is correct.
6. **Zep / Mem0 / FadeMem**: these are arXiv preprints without a peer-reviewed venue on the record as of 2026-11-07. If the draft calls them "published" or lists a venue, that would be wrong — "arXiv preprint" / "preprint" is the defensible wording.
7. **Do not add a repository to the TrustMem citation**: the PDF contains no code URL and no official TrustMem repo could be confirmed (GitHub search for "TRUSTMEM" returns only unrelated projects). The BibTeX entry therefore has no repo field.

## HaluMem cross-check against `data/README.md` (license and commit)

- **License:** `data/README.md` says the HaluMem dataset card declares **CC BY-NC-ND 4.0**. Verified on the Hugging Face dataset card (license `cc-by-nc-nd-4.0`) and on the repo README license badge. ✅ No conflict; the "no licensed data committed" rule in `paper/README.md` stands.
- **Commit:** `data/README.md` line 11 says the repo was reviewed "at commit `d653749586bfc7d13f5b68e8851f52155b5b1650` on 2026-04-21". The GitHub commit API shows that this commit is dated **2026-10-28** and its message is "Add EMNLP 2026 badge to README". So the pinned commit and the stated review date cannot both be right (the commit post-dates the stated review by ~6 months). This does not affect the citation (the ACL record is the primary source for the bibliography), but the factkeeper/Lead should fix the date or the pinned hash in `data/README.md` before that review note is cited in the paper.
- **Repo README caveat:** the repo README's heading title is "HaluMem: A Comprehensive Benchmark for Evaluating Hallucinations in Memory Systems", while the paper/ACL title is "HaluMem: Evaluating Hallucinations in Memory Systems of Agents". Use the ACL title (as in `references.bib`); the README heading is not the citation title.

## What was checked and deliberately *not* added

- No separate Graphiti citation: Graphiti is described within the Zep paper; no separate citable Graphiti paper was verified for this list.
- No dataset BibTeX for LoCoMo or HaluMem: the draft cites the papers, and the `paper/README.md` rule forbids committed licensed data, not citation of the papers.
- No venue, volume, or page fields were invented for the four arXiv-only preprints; they carry only the fields the arXiv records expose (title, authors, year, eprint, DOI).
- No repository/URL field was added to TrustMem or HaluMem beyond what the paper/repo records actually provide.

## Primary sources consulted

- MemGPT: <https://arxiv.org/abs/2310.08560>; OpenAlex record `W4387636003` (preprint, no published version) — <https://api.openalex.org/works?search=MemGPT%20Towards%20LLMs%20as%20Operating%20Systems>
- Mem0: <https://arxiv.org/abs/2504.19413> (abstract quote on the graph variant)
- A-Mem: <https://arxiv.org/abs/2502.12110> (comments: NeurIPS 2025) · <https://neurips.cc/virtual/2025/loc/san-diego/poster/119020> · <https://openreview.net/forum?id=FiM0M8gcct>
- HaluMem: <https://arxiv.org/abs/2511.03506> · <https://aclanthology.org/2026.emnlp-main.220/> · <https://aclanthology.org/2026.emnlp-main.220.bib> · <https://huggingface.co/datasets/IAAR-Shanghai/HaluMem> · <https://api.github.com/repos/MemTensor/HaluMem/commits/d653749586bfc7d13f5b68e8851f52155b5b1650>
- TrustMem: <https://aclanthology.org/2026.findings-emnlp.2419/> · <https://aclanthology.org/2026.findings-emnlp.2419.bib> · PDF text extraction of <https://aclanthology.org/2026.findings-emnlp.2419.pdf>
- LoCoMo: <https://arxiv.org/abs/2402.17753> · <https://aclanthology.org/2024.acl-long.747/>
- Zep: <https://arxiv.org/abs/2501.13956>
- FadeMem: <https://arxiv.org/abs/2601.18642>

All arXiv DOIs above were read from the corresponding arXiv abs pages (DataCite arXiv DOI form `10.48550/arXiv.<id>`), not reconstructed.
