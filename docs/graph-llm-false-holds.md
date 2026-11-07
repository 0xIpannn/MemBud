# Graph-LLM false holds on the held-out split: cache-only post-hoc analysis

**Date:** 2026-11-07
**Artifact:** [analysis.json](../data/results/graph-llm-false-hold-analysis/analysis.json)
**Script:** [scripts/analyze_llm_false_holds.py](../scripts/analyze_llm_false_holds.py)
**Tests:** [tests/test_llm_false_hold_analysis.py](../tests/test_llm_false_hold_analysis.py)
**Status:** descriptive post-hoc analysis of one cached run. Not a controlled experiment, no causal claim, no superiority claim. **Zero network or paid calls were made.** This document does not change [docs/llm-heldout.md](llm-heldout.md).

> **Follow-up (same day):** the semantic `HELD-13` false accept noted here was subsequently fixed (the semantic verifier now consumes `evidence.contradicts`) and the held-out conditions were replayed from cache with no provider calls; see the follow-up section in [docs/llm-heldout.md](llm-heldout.md) and `data/results/llm-heldout-v2-rerun/`. This analysis still describes the original committed `data/results/llm-heldout-v2/` run, and its four graph false holds are unchanged by that fix.

## Scope and method

The held-out v2 run held 4 of the 7 valid updates in the `graph` (LLM) condition
(`HELD-01`, `HELD-02`, `HELD-03`, `HELD-07`) while making 0 false accepts. This
analysis asks what the *cached model responses* said for those four cases, before
any representation comparison.

Inputs (all read-only):

- frozen manifest [data/manifests/evaluation-v2.json](../data/manifests/evaluation-v2.json) and labels
  [data/labels/evaluation-v2.jsonl](../data/labels/evaluation-v2.jsonl) (SHA-256 pinned by the manifest);
- per-case decisions in [data/results/llm-heldout-v2/summary.json](../data/results/llm-heldout-v2/summary.json);
- private completion cache `.cache/membud/deepseek-cache.sqlite3`, opened with
  `sqlite3.connect("file:...?mode=ro", uri=True)` + `PRAGMA query_only = ON`.

The cache payload does not store the prompt, so the script re-derives the exact
request messages and the client's SHA-256 cache key (same
`{base_url, model, temperature, response_format, zero_data_retention, messages}`
material as `OpenAICompatibleChatClient._cache_key`) for:

- 26 LLM verifier calls (13 `graph` + 13 `text`), mirroring `verify_with_llm`;
- 26 semantic comparator calls, mirroring the claim-pair enumeration of
  `membud.semantic._semantic_check`.

Coverage was complete and is asserted in the artifact:

| Check | Result |
|---|---|
| Re-derived unique cache keys | **52 / 52** (26 verifier + 26 semantic comparisons) |
| Cache rows matched to held-out calls | **52 / 52**, 0 missing, 0 unattributed |
| Graph/text summary rows reproduced from cached checks | **26 / 26** decisions, 26/26 check triples, 26/26 token blocks |
| Semantic rows whose cached comparison tokens aggregate to the summary usage | **13 / 13** |
| Verdict statuses that are `UNCERTAIN` | **0** (all cached verdicts are definite PASS/FAIL) |
| Cache file SHA-256 before/after analysis | unchanged (`186071e8...0d10cf4247`) |

Because the decisions, check statuses/reason codes, and token usage all
reproduce the committed summary exactly, the cache rows can be attributed 1:1 to
the run. No row had to be inferred or filled in. Item counts: 54 summary rows =
18 held-out cases x 3 conditions (13 completed + 5 `INVALID_INPUT` per
condition); only the 39 completed rows make model calls, and the semantic
condition makes 26 comparison calls in total.

## The 13 valid held-out cases

| Case | Label | Graph | Text | Failing graph checks (reason_code) | Graph chars | Text chars | Graph in/out tok | Retried |
|---|---|---|---|---|---:|---:|---:|---|
| HELD-01 | ACCEPT | HOLD | ACCEPT | coverage/REQUIRED_CLAIM_OMITTED; preservation/PRIOR_RECORD_UNAUTHORIZED_CHANGE | 1913 | 543 | 971/12174 | no |
| HELD-02 | ACCEPT | HOLD | ACCEPT | preservation/PRIOR_RECORD_UNAUTHORIZED_CHANGE | 1664 | 492 | 2039/3013 | yes |
| HELD-03 | ACCEPT | HOLD | HOLD | preservation/PRIOR_RECORD_UNAUTHORIZED_CHANGE | 2367 | 619 | 1125/2096 | no |
| HELD-04 | ACCEPT | ACCEPT | ACCEPT | — | 2700 | 830 | 1229/1085 | no |
| HELD-05 | ACCEPT | ACCEPT | ACCEPT | — | 1752 | 449 | 919/954 | no |
| HELD-06 | ACCEPT | ACCEPT | ACCEPT | — | 3868 | 1104 | 3584/7082 | yes |
| HELD-07 | ACCEPT | HOLD | ACCEPT | preservation/PRIOR_RECORD_UNAUTHORIZED_CHANGE | 1747 | 511 | 893/4366 | no |
| HELD-08 | HOLD | HOLD | HOLD | coverage/REQUIRED_CLAIM_OMITTED | 1567 | 502 | 832/3495 | no |
| HELD-09 | HOLD | HOLD | HOLD | coverage/REQUIRED_CLAIM_OMITTED; preservation/PRIOR_RECORD_UNAUTHORIZED_CHANGE; faithfulness/UNSUPPORTED_CLAIM | 2042 | 469 | 1004/2336 | no |
| HELD-10 | HOLD | HOLD | HOLD | coverage/REQUIRED_CLAIM_OMITTED; preservation/PRIOR_RECORD_UNAUTHORIZED_CHANGE; faithfulness/UNSUPPORTED_CLAIM | 2437 | 596 | 2675/2590 | yes |
| HELD-11 | HOLD | HOLD | HOLD | coverage/REQUIRED_CLAIM_OMITTED; faithfulness/UNSUPPORTED_CLAIM | 2644 | 641 | 1221/2309 | no |
| HELD-12 | HOLD | HOLD | HOLD | faithfulness/UNSUPPORTED_CLAIM | 2818 | 684 | 1300/1012 | no |
| HELD-13 | HOLD | HOLD | HOLD | faithfulness/CONTRADICTED_CLAIM | 2305 | 630 | 1101/1806 | no |

Condition totals reproduced: graph 3 ACCEPT / 10 HOLD, text 6 ACCEPT / 7 HOLD,
semantic 8 ACCEPT / 5 HOLD (5 expected-invalid rows per condition make no calls).
False accepts: graph 0, text 0, semantic 1 (`HELD-13`, out of scope here).
Graph-vs-text decisions are discordant on exactly `HELD-01`, `HELD-02`,
`HELD-07`; the other 10 valid cases agree.

## Attribution of the four false holds

All four fail **preservation**; `HELD-01` additionally fails **coverage**; none
fails **faithfulness**. The model's own reason text points in two directions.

| Case | Failed checks | Reason-code attribution | What the cached reason text says | Text condition |
|---|---|---|---|---|
| HELD-01 | coverage, preservation | operation scoping + null authorization | coverage: "The operation selects candidate record r2, which contains only 'bekerja di Gojek'. The required claim 'tinggal di Yogyakarta' is not covered by the **selected candidate**, even though candidate r1 contains it." preservation: "…the **selected candidate r2** omits it. The operation has **null authorization**…" | ACCEPT |
| HELD-02 | preservation | prior-change authorization requirement | "Prior record r1's claim … is changed … through a **REVISE** operation, but the operation **authorization is null**, so the prior-record change is unauthorized." | ACCEPT |
| HELD-03 | preservation | prior-change authorization requirement (shared with text) | "Operation:0:supersede changes prior record … but the operation has **authorization=null and no edge authorizes** this prior-record change." | **HOLD** |
| HELD-07 | preservation | prior-change authorization requirement | "Prior record r1 claim … is replaced …; the **REVISE operation has authorization null**, so the prior claim is not preserved with authorization." | ACCEPT |

Breakdown:

- **Failing check clustering:** preservation 4/4, coverage 1/4, faithfulness 0/4
  (graph also fails preservation on the two true HOLDs `HELD-09/10`; preservation
  is not a false-hold-only failure mode, but it is the shared one).
- **Reason-code clustering:** `PRIOR_RECORD_UNAUTHORIZED_CHANGE` in 4/4 false
  holds; `REQUIRED_CLAIM_OMITTED` additionally in `HELD-01`.
- **Attribution clustering:** 3/4 cite a null/absent authorization for an
  explicit `REVISE`/`SUPERSEDE` prior-record change; 1/4 (`HELD-01`) shows the
  model treating the operation-linked candidate record as the whole resulting
  state.
- **Representation-specific or not:** 3/4 are graph-only in this cache
  (`HELD-01/02/07` were accepted by the text LLM condition); `HELD-03` was also
  held by the text condition, with the same reason code. So the graph condition
  is where the pattern is *most frequent* (4 vs 1), but at least one instance
  (`HELD-03`) is shared prompt/policy ambiguity, not a graph-only effect.
- For contrast, text accepted `HELD-02` because "the change is an explicit
  REVISE operation and is supported by correction evidence e1", and accepted
  `HELD-07` because "the evidence directly contradicts the old claim and supports
  the replacement". The same transitions are in both conditions; only the
  serialization differs. The deterministic text baseline accepts all four.

This is a difference in how the model *read* the transition, not a difference in
what the transition contained: the graph and text serializations are both
lossless views of the same manifest case.

## Serialization length / token asymmetry

Paired over the 13 valid cases (same transition, two renderings):

| Measure | Graph | Text | Graph/Text |
|---|---:|---:|---:|
| Total serialized input chars | 29,824 | 8,070 | **3.70x** |
| Total input tokens | 18,893 | 9,430 | **2.00x** |
| Total output tokens (summed over attempts) | 44,318 | 19,057 | **2.33x** |
| Total accounted tokens | 63,211 | 28,487 | **2.22x** |

Per-case char ratios range 3.12x–4.35x (mean 3.71x); input-token ratios range
0.89x–5.37x (mean 2.16x). The 2.22x total-token figure matches the descriptive
number in [docs/llm-heldout.md](llm-heldout.md).

**This asymmetry does not explain the four false holds.** Within the graph
condition the false-hold cases are *shorter* than average:

| Graph outcome group | n | Mean graph chars | Mean graph input tokens | Mean char ratio |
|---|---:|---:|---:|---:|
| False holds (01/02/03/07) | 4 | 1,922.8 | 1,257.0 | 3.54x |
| Correct accepts (04/05/06) | 3 | 2,773.3 | 1,910.7 | 3.55x |
| True HOLDs (08–13) | 6 | 2,302.2 | 1,355.5 | 3.91x |

Descriptive Pearson values (n=13, reported as exact numbers only; no power or
significance claim): graph chars vs graph HOLD indicator r = **−0.432**, graph
chars vs false-hold indicator r = **−0.408**, graph input tokens vs graph HOLD
indicator r = **−0.316**. If anything the direction is inverse, and the largest
graph input of all (`HELD-06`, 3,868 chars) was accepted.

Caveat on the output-token ratio: the client sums token usage across JSON-repair
attempts, and cached visible content is often far shorter than the reported
completion tokens. 9/26 verifier calls have fewer than 0.35 visible content
chars per completion token (`HELD-01` graph: 1,078 visible chars vs 12,174
completion tokens). That strongly suggests hidden reasoning tokens (or provider
accounting we cannot see); the cache stores only visible `content`, so the
output-token ratio is **not** a clean measure of visible output.

## Retries, UNCERTAIN, and fail-closed paths

- **JSON-repair retries: 7/52 cached calls (13.5%)**, each exactly one retry
  (`json_retries=1`, `request_attempts=2`): graph 3 (`HELD-02/06/10`), text 4
  (`HELD-02/09/10/13`), semantic 0. This matches the run's 59 provider request
  attempts for 52 calls. **The expectation of 0 retries is not met**; the
  "0 retries" wording in the task framing should be corrected.
- Retries cluster on neither false holds nor false accepts: among the four graph
  false holds only `HELD-02` retried, and graph `HELD-06` retried yet was
  accepted. Retries do inflate reported output tokens (tokens are summed across
  attempts), so they matter for accounting, not for the hold reasons.
- **UNCERTAIN rate is 0.** All 78 graph/text check verdicts (26 calls x 3) are
  PASS/FAIL: graph 23 PASS / 16 FAIL / 0 UNCERTAIN; text 27 PASS / 12 FAIL / 0
  UNCERTAIN. All 26 semantic comparator verdicts are definite relations
  (25 UNRELATED, 1 CONTRADICTORY), 0 UNCERTAIN. There are no
  `INVALID_VERIFIER_OUTPUT` rows, so transport/JSON-repair fail-closed paths did
  not create the false holds: they are model judgment FAILs.

## Ranked candidate causes

Ranked by directness of cached evidence; all are descriptive.

1. **Graph authorization semantics for prior-record changes (best supported).**
   The graph renderer emits an explicit `authorization: null` field for every
   operation and only emits `AUTHORIZED_BY` edges when structured authorization
   exists. The model reads a null authorization / missing authorizing edge on an
   evidence-backed `REVISE`/`SUPERSEDE` as an unauthorized prior-record change.
   Direct evidence: 3/4 false holds, sole preservation failure, reason text
   explicitly cites null authorization; two of these (`HELD-02`, `HELD-07`) were
   accepted by the text condition with the same evidence.
2. **Graph operation scoping of the result state.** `HAS_OPERATION`/`PROPOSES`
   edges attach the operation node only to the new/changed candidate record;
   `HELD-01`'s coverage reason treats that record as the whole result and misses
   the untouched candidate record `r1`, producing coverage + preservation FAILs.
   Text accepted citing both candidate records. Direct evidence: 1/4.
3. **Shared prompt/policy ambiguity about authorizing `REVISE`/`SUPERSEDE`.**
   The shared system prompt says an explicit operation alone does not authorize
   a change and does not state that an evidence-backed `REVISE`/`SUPERSEDE` is
   itself an explicit authorized change (structured authorization is required by
   the deterministic rules only for `DELETE`). `HELD-03` is held by both graph
   and text for this reason, so part of the pattern is representation-independent.
4. **Serialization length/token asymmetry (not supported).** False-hold cases are
   shorter than average; correlations are inverse and descriptive only.
5. **Retry/transport/fail-closed artifacts (not supported).** 0 UNCERTAIN, 0
   verifier errors; only 1 of 4 false holds retried; retries are an accounting
   confounder, not a hold cause.
6. **Hidden reasoning completion tokens (unresolvable from the cache).** The
   cache does not store `reasoning_content`; a hidden reasoning chain could in
   principle explain over-conservative verdicts, but it cannot be inspected here.
7. **Case-specific model variance (unresolvable from the cache).** Temperature is
   0 but provider-side determinism is unproven; repeated sampling would be
   needed to separate variance from representation effects.

## Smallest next experiment (requires explicit user spend approval)

**No such calls were made now; this is a proposal only.** The experiment is a
cache-backed ablation with two new graph renderings. New prompts mean cache
misses, so these are new paid calls against the same frozen held-out transitions,
same model (`deepseek-flash`), temperature 0, JSON mode and validator:

- **G0** — exact current graph; served from the existing cache, must reproduce
  the current graph decisions (free control).
- **G1** — identical graph, but operation nodes omit the `authorization` key when
  it is null (tests whether the explicit null field is the trigger for cause 1).
- **G2** — exact graph plus a one-sentence policy clarification that an
  evidence-backed `REVISE`/`SUPERSEDE` is an explicit authorized change and
  structured authorization is required only for `DELETE` (tests cause 3).

| Variant | Cases | Variants | Planned calls | Estimated tokens | Estimated cost range* |
|---|---|---:|---:|---:|---:|
| Smallest directional probe | the 4 false holds | G1, G2 | 8 | ≈38,899 | $0.004 – $0.078 |
| Recommended paired | all 13 valid cases | G1, G2 | 26 | ≈126,422 | $0.013 – $0.253 |

\* Planning envelope only: harness placeholder $0.10–$2.00 per 1M accounted
tokens applied to the observed graph-condition tokens/call (63,211 / 13 ≈ 4,862
tokens). The provider does not return billed cost, so this is not a quote.
Budget cap recommendation: set `--max-total-tokens` to the recommended variant's
estimated tokens plus one row of headroom.

Readout: per-case decisions per variant, preservation FAIL counts, and
authorization-language mentions. Support for causes 1/3 would mean G1 and/or G2
convert some graph false holds to ACCEPT without converting true HOLDs to
ACCEPT; G0 must reproduce the current cache decisions exactly.

## Risks and limitations

- **Post-hoc, single run, n=13 (4 false holds).** No randomization, no
  replication, no statistical power; the correlations above must not be read as
  effect estimates. Conditions also differ in verifier construction and token
  budget, so nothing here is a representation comparison or a superiority claim.
- **Single-reviewer labels.** `HELD-01/02/03/07` labels are single-reviewer and
  the manifest states second human review is pending. If any label is wrong, the
  "false hold" framing changes.
- **Reason text is the model's stated rationale**, not necessarily its internal
  cause; hidden reasoning tokens are not in the cache.
- **Cache-key mapping risk is mitigated, not zero.** The script's key
  derivation and prompt/pair-sequence mirrors are cross-checked against the real
  `_cache_key`, `verify_with_llm`, and `_semantic_check` in
  [tests/test_llm_false_hold_analysis.py](../tests/test_llm_false_hold_analysis.py);
  full 52/52 coverage plus exact token/check/decision agreement is the end-to-end
  evidence that the mapping is correct. If the private cache is ever evicted the
  analysis must be re-run (the committed artifact is the record).
- **Token accounting confounders.** Output tokens sum across JSON-repair attempts
  and may include hidden reasoning; input-token and serialized-char ratios are
  the interpretable asymmetry numbers.
- **No cache/result mutation.** The analysis opens the cache read-only
  (before/after SHA-256 identical) and does not write
  `data/results/llm-heldout-v2/**`. No git add/commit is performed by this task;
  integration is the Lead's.

## Follow-ups

1. Obtain explicit user spend approval, then run the G1/G2 ablation above; keep
   the G0 cache-hit control.
2. If cause 3 is confirmed, decide whether the shared verifier prompt should
   state the `REVISE`/`SUPERSEDE` authorization policy explicitly (a policy
   change, separate from any representation claim).
3. Do not compare graph vs text representations on the current run; retest with
   the larger, better-powered held-out set and second human review already listed
   as follow-ups in [docs/llm-heldout.md](llm-heldout.md).

---

## Controlled ablation results (G0/G1/G2, 2026-11-07)

The ranked causes above were tested with the smallest controlled probe
(`scripts/run_graph_false_hold_ablation.py`). Variants use the same frozen
held-out transitions, model (`deepseek-flash`), temperature 0, and JSON-mode
validator; only the graph serialization or the shared system prompt changes:

| Variant | Change | New calls? |
| --- | --- | --- |
| G0 `graph_cache` | none — current graph + current prompt, replayed from the response cache | no (cache only) |
| G1 `graph_no_null_auth` | operation nodes omit `"authorization": null` | yes |
| G2 `graph_policy_clarified` | prompt adds: an evidence-backed `REVISE`/`SUPERSEDE` is itself an explicit authorized change; structured authorization is required only for `DELETE` | yes |

### Directional probe (the four original false holds)

Artifact: `data/results/graph-false-hold-ablation/`.

| Variant | Decisions | Converted to ACCEPT | Preservation failures | Authorization mentions |
| --- | --- | ---: | ---: | ---: |
| G0 control | 0 ACCEPT / 4 HOLD | 0/4 | 4/4 | 4 |
| G1 no-null-auth | 2 ACCEPT / 2 HOLD | 2/4 (HELD-03, HELD-07) | 2/4 | 2 |
| G2 policy-clarified | 4 ACCEPT / 0 HOLD | **4/4** | **0/4** | **0** |

### Paired safety check (all 13 schema-valid held-out cases)

Because a prompt that removes false holds could also remove true holds, the
paired run checks the 7 valid and 6 faulty cases together.
Artifact: `data/results/graph-false-hold-ablation-paired/`.

| Variant | ACCEPT / HOLD | Valid accepted | False holds | False accepts | Label agreement |
| --- | ---: | ---: | ---: | ---: | ---: |
| G0 control | 3 / 10 | 3/7 | 4/7 | 0/6 | 29/36 (matches the original run) |
| G1 no-null-auth | 5 / 8 | 5/7 | 2/7 | **0/6** | 31/36 |
| G2 policy-clarified | **7 / 6** | **7/7** | **0/7** | **0/6** | **36/36** |

(G0's "label agreement" is over all 36 cases, including 5 expected-invalid per
condition; the other rows are computed on the 13 valid cases. G2's two
remaining preservation failures are true HOLDs HELD-09/10, not false holds.)

### Interpretation

- The explicit null-authorization field is **part** of the cause: removing it
  (G1) fixes 2 of 4 false holds with no false accepts.
- The shared prompt's ambiguity is the **larger** cause: one sentence stating
  the deterministic policy (G2) fixes all 4 false holds and keeps every true
  HOLD held.
- G2 is consistent with the deterministic verifiers, where structured
  authorization is required for `DELETE` and an evidence-backed
  `REVISE`/`SUPERSEDE` is an authorized change. It is a legitimate policy
  clarification, not a representation change.

### Limitations (important)

- **Development evidence, not a clean held-out result.** The held-out split was
  used to diagnose the false holds *and* to select these variants. A prompt
  tuned on that split must not be reported as generalizing to it; an independent
  split or fresh cases is required before promoting G2 to the default prompt.
- Single model, single run, single-reviewer labels (second review pending);
  temperature-0 provider determinism is unproven.
- G2 remains an **opt-in prompt variant** (`verify_with_llm(policy_clarification=True)`,
  prompt version `membud-transition-checks-v1-policy-clarified`); the default
  prompt and all deterministic verifiers are unchanged.
- This still does not establish graph-vs-text superiority: it removes a
  false-hold artifact inside the LLM graph condition.

### Cost

Both runs made **20 paid calls in total** (8 probe + 12 new paired; the rest
were cache hits), accounted at **76,486 tokens** by the runner's conservative
accounting. At the harness placeholder range that is **$0.0076-$0.153**; the
provider's billing dashboard remains authoritative. The G0 controls used zero
tokens (cache replay).

### Recommended follow-up

Replicate G2 on the dev split (or a fresh set) before changing the default
prompt; if it holds, promote the clarification as a new default prompt version
and re-run the held-out evaluation with a fresh cache. The G1 serialization
change alone is not sufficient and should not replace the prompt fix.
