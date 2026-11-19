# Bab 3 — Implementation Method: outline, mapping, and guardrails (LOCKED v2)

**Date:** 2026-11-07. **Deadline:** Bab 3 assembled 2026-11-09; full paper submission-ready target 2027-01-07 (venue deadline still to be matched; no acceptance promise).
**Basis:** `main` @ `1ce84ce`, clean tree, 243 tests passing.
**Decisions locked with the user:**
- Language: **English** (Indonesian terms only where the user's template requires them).
- Template: the user's exact heading list below (Chapter 3 Implementation Method, 3.1 + 3.2 with 3.2.1–3.2.6).
- RQs: the three RQs from the user's Introduction draft (reliability, modularity, trade-offs).
- Venue: international conference/workshop (ACL/EMNLP/NeurIPS D&B/COLM class) — final venue/cycle to be chosen; do not promise acceptance.
- Framing: **methods/systems contribution + rigorous negative result**; graph superiority is not claimed.
- Claims reconciliation: the Introduction's "adapter evaluated across 3 backends" and "question answering" are **design goals/future work only**; Bab 3 describes only what was implemented.
- Instrument: frozen 36-case v2 set, single-reviewer exploratory labels; second independent review pending.

## Exact heading list (user template)

```
CHAPTER 3 IMPLEMENTATION METHOD
3.1 Thinking Framework
3.2 Research Methods
3.2.1 Data Collection
3.2.2 Exploratory Data Analysis
3.2.3 Data Preprocessing
3.2.4 Data Splitting
3.2.5 Modelling
3.2.6 Model Evaluation
```

Validity/ethics is folded into the end of 3.2.6 because the template has no separate validity heading. If the user's template later adds 3.2.7, lift that paragraph out unchanged.

## Section mapping

| Section | Must establish | Primary artifacts | Owner |
|---|---|---|---|
| 3.1 Thinking Framework | problem → gap → framework → hypothesis under information parity → RQ mapping → scope/future work | `PLAN.md` §3–4; user Introduction; `docs/fairness-audit.md` | Lead (done) |
| 3.2 Research Methods | research design, unit of analysis, procedure, conditions, reproducibility, claims boundary | `PLAN.md` §8–9; `data/experiment-protocol.md`; `docs/technical-report-milestone-2.md` | Lead (done) |
| 3.2.1 Data Collection | how the 36 cases/labels were authored, annotated, frozen, reviewed | `data/annotation-guidelines.md`, `data/cases|labels/evaluation-v2.jsonl`, `data/manifests/evaluation-v2.json`, `data/review/README.md` | eval-writer |
| 3.2.2 Exploratory Data Analysis | descriptive characterisation of the instrument (computed, traceable) | cases/labels/manifest; computed stats | eval-writer |
| 3.2.3 Data Preprocessing | canonicalisation, schema v2 invariants, serialisation, label/input separation, hashing, invalid input | `src/membud/schemas.py`, `serialization.py`, `graph.py`; docs §2 | methods-writer |
| 3.2.4 Data Splitting | 18/18 frozen-before-run split, per-split balance, no tuning on held-out | manifest; protocol §v2; results per-split fields | eval-writer |
| 3.2.5 Modelling | conditions/verifiers/decision policy/LLM settings/adapter/store; information-parity logic | `src/membud/verification.py`, `graph_verification.py`, `fairness.py`, `semantic.py`, `llm/client.py`, `adapters/reference.py`, `store.py`; docs §3–6, §11–12 | methods-writer |
| 3.2.6 Model Evaluation | metric definitions and denominators, paired analysis, inference policy, cost/latency, validity/ethics | `src/membud/metrics.py`; results JSON field definitions; protocol §Metrics | eval-writer |

## Claims corrections (apply to Bab 1 separately; Bab 3 must reflect the corrected scope)

| Existing draft claim | Status | Bab 3 treatment |
|---|---|---|
| "adapter interface evaluated across 3 backends" | not supported | implemented: adapter contract + one reference backend (in-memory/SQLite); Mem0/A-Mem source-inspection only, no third-party integration/compatibility; 3-backend evaluation = future work (Bab 1) |
| "contribution includes downstream question answering" | not measured | QA utility explicitly not measured; listed in 3.2.6/future work; do not infer QA from acceptance |
| "accept, revise/retry, or reject/hold decision" | not implemented | implemented policy: `ACCEPT`, `HOLD`, `INVALID_INPUT` (plus execution `ERROR` in metrics); bounded retries are execution events, not decisions |
| "graph-based verification catches contradictions text cannot" | contradicted by fairness audit | fair text control with the same `evidence.contradicts` matches graph on all 26 non-invalid v2 cases; visible difference = annotation access, not structural inference |
| "graph superiority" | unsupported | never claim; negative result is acceptable and expected framing |

## Numbers allowed in Bab 3 (instrument facts only)

| Fact | Value | Source |
|---|---|---|
| Cases / split | 36; 18 dev / 18 held-out | `data/manifests/evaluation-v2.json` → `case_count`, `split_counts` |
| Gold decision distribution (labels, not results) | 14 ACCEPT / 12 HOLD / 10 INVALID_INPUT; 7/6/5 per split | manifest → `expected_decision_counts[_by_split]` |
| Categories | 18 categories × 2 cases | `data/results/evaluation-v2/metrics.json` → `case_category_counts` |
| Hash pins | cases `40558746…`, labels `9280d3e8…`, manifest `e1adf173…`, labels SHA-256 in manifest | manifest; metrics → `manifest_sha256` |
| Tests at HEAD | 243 passing | local run at `1ce84ce` (milestone-2 report's 202 is stale) |
| LLM run instrument | deepseek-flash, temp 0, JSON mode, `membud-transition-checks-v1`, `membud-semantic-compare-v1`, 250,000-token cap, 101,231 reported tokens, 59 attempts, 0 verifier errors; billed cost unavailable (the narrative "≈$0.40" figure is not an artifact value — the JSON records only a per-token estimate range) | `docs/llm-heldout.md`; `data/results/llm-heldout-v2/` |
| Licenses (no licensed data used) | HaluMem CC BY-NC-ND 4.0; LoCoMo CC BY-NC-SA | user-established; `data/README.md` documents HaluMem |

**Observed run outcomes are results, not instrument facts.** Decision counts of the graph/text/LLM conditions, fairness-audit outcomes, paired differences, false holds/accepts, and latency means belong to Bab 4. Bab 3 may describe the metrics and the run configuration, not the findings.

## RQ definitions (user Introduction, verbatim intent)

- **RQ1 (reliability):** does MemBud reduce omissions, corruption of valid prior records, and unsupported additions relative to an unguarded pipeline, and does graph-based verification catch more errors than a non-graph verifier using the same criteria and budget?
- **RQ2 (modularity):** can the same verification logic run across backends through adapters, and what backend-specific information is required?
- **RQ3 (trade-offs):** how does the safety layer affect valid-update acceptance, downstream question answering, and computational cost, including graph-construction overhead?

Coverage in the implemented scope: RQ1 addressed for the frozen synthetic instrument; RQ2 addressed only at the adapter-contract + reference-backend + feasibility-inspection level; RQ3 addressed for acceptance/false-hold/token/latency instrumentation, **not** for downstream QA. State this honestly in 3.1/3.2 and carry it to the limitations.

## Guardrails for all writers

1. No invented numbers; no invented citations; no rounding without a source.
2. No superiority phrasing ("outperforms", "catches what text cannot", "state of the art", "novel graph reasoning").
3. Single-reviewer status stated at first mention of labels; second review pending; no agreement statistic exists.
4. No licensed data quoted or committed.
5. The fairness control is the methodological spine: `text → text_fair` isolates annotation access; `text_fair → graph` isolates representation. A zero difference is reported as "no representation-level advantage detectable at this n," not as proof of equivalence.
6. 243 tests at `1ce84ce`; do not change code/data/tests while drafting.

## Assembly plan

1. Lead: 3.1, 3.2 (done), assemble after writers report.
2. factkeeper: `paper/evidence-map.md` (facts). 3. lit-verifier: `paper/references.bib` + verification table.
3. methods-writer: 3.2.3, 3.2.5. eval-writer: 3.2.1, 3.2.2, 3.2.4, 3.2.6.
4. Lead resolves TODO markers (citations/numbers), writes `paper/chapter-3-implementation-method.md`.
5. fact-checker audit (independent) → fix → deliver 2026-11-09.
