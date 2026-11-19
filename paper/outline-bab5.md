# Bab 5 — Discussion: outline and argument plan

**Status:** proposed structure (2026-11-07). Not yet drafted. Bab 4 audit pending; numbers in Bab 5 will reference Chapter 4 tables only after that audit passes.
**Purpose:** interpret the pinned-revision results, state the negative result precisely, and connect it to the graph-memory hypothesis and to evaluation practice.
**Claim discipline:** no superiority/significance/causal/population/production claims; zero difference = "not detectable at this n", not equivalence; "faulty" denominators explicit; labels single-reviewer exploratory.

## Proposed structure

### 5.1 From measurement to interpretation
- Restate RQ1's two halves and what Chapter 4 established: the guarded pipeline behaves as designed on the synthetic instrument; both guarded conditions accept all labelled-valid updates with no false holds in the rules-only run; graph rejects all 12 expected-`HOLD` cases while plain text rejects 10/12.
- State the negative result precisely: **after information access is equalized, no decision-level representation advantage was detectable at n=36.** This is not equivalence and not graph inferiority — it is a bounded, instrument-specific finding.
- Name the two distinct effects the design separates (annotation access vs verifier construction) and which one the evidence supports.

### 5.2 Why the plain-text comparison was misleading — and what that teaches
- The only two differences (`DEV-13`, `HELD-13`) both come from `evidence.contradicts`, which the plain baseline does not read; the fair text control reads it and matches graph on all 36 cases (0 paired differences).
- Methodological lesson: a representation comparison is only interpretable when the information sets are equal; report what each verifier can see; do not attribute to "structure" what is explained by field access.
- This is the paper's transferable methodological contribution, independent of the negative result.
- Caveat: the fair control is minimal and constructed for the audit, not a production verifier; the audit covers only contradiction annotations, not other relational mechanisms.

### 5.3 The cost of conservatism
- Rules-only run: zero false holds on the 14 labelled-valid updates in both guarded conditions; the fail-closed policy cost nothing observable on this instrument.
- Held-out LLM run: graph-LLM was markedly over-conservative (4/7 valid held) versus text-LLM (1/7); semantic held 0/7 but false-accepted `HELD-13` at the evaluated revision because it did not read `evidence.contradicts`.
- Interpretation: the trade-off is real but this instrument cannot price it — no downstream QA, small split, single reviewer. State this as a limitation, not a result.
- Fail-closed behaviour (invalid input never writes; HOLD leaves bytes identical) is a safety property worth separating from accuracy.

### 5.4 Modularity and the pre-commit boundary (RQ2)
- The adapter contract and reference backend show the verification layer can be interposed and fail safely on two reference storage modes, with byte-identical HOLD.
- The more important finding is negative/structural: the inspected third-party memory systems expose no safe public pre-commit boundary; guarding them requires host-owned staging or a fork. This is a deployment-obstacle finding, not an integration claim.
- What an adapter must supply/lose (record identity, claims, evidence spans, timestamps, relations) is the modularity cost; three-backend evaluation remains future work.

### 5.5 What would count as evidence for a graph advantage?
- Conditions the design identifies for a future test: (i) relational structure the graph exposes but a fair text verifier cannot reconstruct from the same canonical facts (multi-hop provenance/derivation, entity–relation contradictions across records, temporal ordering); (ii) information-matched fair controls; (iii) a powered, independently reviewed held-out set; (iv) pre-specified contrasts, stratified by error type; (v) a cost model that prices false holds (downstream QA) and graph overhead.
- Explicitly: a better prompt or a longer graph serialisation is not evidence of representation advantage; neither is comparison against a verifier that ignores a field.
- Note the concrete next experiment implied by this study (contradiction-aware semantic verifier; fair-text baseline extended with the same relations; larger set).

### 5.6 Threats to validity (carry forward, do not restate at length)
- Point back to Section 3.2.6; add only what Bab 4 made concrete: circularity (cases authored alongside verifiers), single reviewer (no agreement statistic), small n (no power), synthetic instrument (external validity), LLM conditions non-deterministic and non-equal-budget, billed cost unavailable, no downstream QA.
- Note the pinned-revision caveat: the evaluated revision predates later repository work; this is a reproducibility decision, not a result.

### 5.7 Implications
- **For the graph-memory hypothesis:** the hypothesis is not refuted; it is narrowed. The v2 evidence does not support a representation-level advantage, and the fairness audit shows why earlier apparent advantages must be re-tested under information parity.
- **For evaluation practice:** report information access, use fair controls, pre-specify contrasts, and keep negative results; the fairness audit is reusable.
- **For builders:** verification-before-commit is implementable and fail-safe on a reference backend; third-party integration is blocked by missing pre-commit boundaries, so host-owned staging is the practical path.

### 5.8 Conclusion (short handoff to Bab 6)
- One paragraph: what the study contributes (artefact, information-parity protocol, rigorous negative result), what it does not claim, and what remains.

## Mapping claims → evidence (after Bab 4 audit passes)

| Claim in Bab 5 | Evidence | Risk |
|---|---|---|
| guarded pipeline behaved as designed | Ch. 4 §4.2 tables 4.3–4.7 | low |
| visible graph advantage = annotation access | Ch. 4 §4.3 tables 4.8–4.11 | low |
| no representation-level advantage detectable at n=36 | Ch. 4 §4.3, §4.7 | must keep "detectable at this n" qualifier |
| LLM graph over-conservative | Ch. 4 §4.4 table 4.12 | descriptive only |
| third-party memory systems lack safe pre-commit boundary | Ch. 4 §4.6 tables 4.17–4.18; `data/backend-feasibility.md` (pinned) | no integration claim |
| QA not measured | Ch. 4 §4.5 | no inference |
| labels single-reviewer | throughout | never "gold" |

## Guardrails for the drafter

1. No new numbers: cite Chapter 4 tables; do not recompute in Bab 5.
2. Every comparative statement gets its denominator and the "descriptive / not detectable at this n" qualifier where relevant.
3. Do not upgrade the negative result into "graph does not help" or "representations are equivalent".
4. Keep the pinned revision caveat out of the main argument; put it in limitations.
5. Cite only `paper/references.bib` keys; no new literature without verification.
6. Length target: ~2,500–3,500 words (thesis chapter scale), organised in prose; no bullet-only sections.
