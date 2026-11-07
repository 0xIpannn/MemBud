# Held-out LLM and semantic evaluation (v2)

**Date:** 2026-11-07
**Artifact:** `data/results/llm-heldout-v2/` (`summary.json`, `metrics.json`, audit logs)
**Reproduce:** see `scripts/run_llm_heldout.py`; the run is cache-backed and budget-capped.
**Status:** descriptive held-out evaluation, single reviewer labels, no superiority claim.

## Setup

- Manifest: frozen `membud-evaluation-v2` (SHA-256 pin), split = held-out (18 cases: 13 schema-valid, 5 expected-invalid).
- Model: DeepSeek `deepseek-flash` official OpenAI-compatible API, temperature 0, JSON mode, prompt `membud-transition-checks-v1`; semantic comparator prompt `membud-semantic-compare-v1`.
- Conditions: `graph` and `text` use the optional LLM verifier on the graph/text serialization; `semantic` uses the deterministic **text** baseline plus the separate LLM semantic check (4-check result, commit requires all PASS).
- 39 non-invalid rows executed, 0 verifier errors, 0 cache hits, no budget abort.
- **Spend:** 101,231 reported tokens (34,505 input / 66,726 output; 59 provider request attempts). Estimated upper bound **≈$0.40** at DeepSeek's top published per-token rate and **≈$0.20** under the harness's $2/M ceiling; the API does not return billed cost, so the provider dashboard remains authoritative. The 250,000-token cap was not approached.

## Results (held-out, n = 13 non-invalid; 7 valid, 6 gold faulty; 5 invalid)

| Condition | ACCEPT | HOLD | INVALID_INPUT | False accepts | False holds | Valid accepted | Tokens | Mean latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| graph (LLM) | 3 | 10 | 5 | 0/6 | 4/7 | 3/7 | 63,211 | 20.4 s |
| text (LLM) | 6 | 7 | 5 | 0/6 | 1/7 | 6/7 | 28,487 | 8.5 s |
| semantic (text + semantic check) | 8 | 5 | 5 | 1/6 | 0/7 | 7/7 | 9,533 | 3.0 s |

- Expected-invalid handling: 5/5 in every condition, no provider calls for those rows.
- Semantic abstentions: 0/13 (the comparator returned a definite verdict for every comparison it made).
- Faulty detection (HOLD-only view, ignoring errors): graph 6/6, text 6/6, semantic 5/6. All false accepts for graph/text are zero; semantic's single false accept is HELD-13.

## Interpretation

1. **The LLM graph condition was markedly over-conservative on this run**: it held 4 of 7 valid updates (HELD-01/02/03/07) while making no false accepts. The text LLM condition held 1 of 7 (HELD-03). This is a false-hold cost trade-off, not a detection gain: both caught all 6 gold faulty cases.
2. **The semantic condition accepted all 7 valid updates** but also accepted HELD-13 — the annotated-contradiction case that plain text also accepts. The semantic module compares claims for paraphrase/contradiction but **does not consume `evidence.contradicts`**, so it inherits exactly the blind spot identified in the fairness audit. This is an integration gap to fix, not a semantic-model failure per se.
3. **Cost/latency:** on this run, graph serialization used ≈2.2× the tokens and ≈2.4× the latency of text for the LLM verifier. With n=13 valid + 6 faulty cases, these differences are descriptive only; no statistical inference is justified.
4. **No superiority claim.** Conditions differ both in verifier construction and in token/latency budgets, labels are single-reviewed, and the held-out split is small. The run demonstrates that the harness can execute equivalent-information LLM/semantic comparisons under a hard spend cap, not that any condition is better.

## Follow-ups identified

- Make `verify_with_semantics` consume `evidence.contradicts` (or explicitly document that a contradiction-aware comparator is required) and re-run with the existing cache.
- Investigate the graph-LLM false-holds (prompt/graph-serialization length effects, retries) before comparing representations.
- Collect the independent second human review of the held-out labels via the blind quiz (`data/review/quiz-v2.html`).
- Repeat with a larger, better-powered held-out set before drawing any conclusion about representation.

---

## Follow-up: cache-only rerun after the semantic contradiction fix (2026-11-07)

The semantic module now consumes `evidence[].contradicts` deterministically: if a claim in the candidate state appears in any evidence contradiction annotation, the appended `semantic` check fails with `CONTRADICTED_CLAIM` before and independent of comparator calls. The comparator prompt/version is unchanged, so every request keeps its cache key. All three held-out conditions were rerun from the private response cache with `--require-cache`, which aborts before any provider I/O on a cache miss.

| Condition | ACCEPT | HOLD | INVALID_INPUT | False accepts | False holds | Valid accepted |
|---|---:|---:|---:|---:|---:|---:|
| graph (LLM) | 3 | 10 | 5 | 0/6 | 4/7 | 3/7 |
| text (LLM) | 6 | 7 | 5 | 0/6 | 1/7 | 6/7 |
| semantic (text + semantic check) | 7 | 6 | 5 | **0/6** | 0/7 | 7/7 |

- **0 provider attempts, 0 new tokens, 51 cache hits, 0 misses, no budget abort**, and the private cache file was not modified.
- Graph/text decisions and checks are unchanged. The only delta is semantic `HELD-13`: now HOLD with `semantic` FAIL `CONTRADICTED_CLAIM` on `["bekerja di Unilever"]` and **zero comparator calls**. Semantic false accepts move 1/6 -> 0/6 and faulty detection 5/6 -> 6/6.
- Artifact: `data/results/llm-heldout-v2-rerun/`. This is a cache replay of the same one-shot run plus one deterministic fix, not a new paid evaluation, and it still supports no superiority claim.
- The four graph-LLM false holds and the retry/accounting detail are analyzed separately in `docs/graph-llm-false-holds.md`; a follow-up controlled G0/G1/G2 ablation there (2026-11-07) found the prompt-policy clarification variant accepts 7/7 valid and 0/6 faulty cases on the 13 valid held-out cases — development evidence only, because the split was used to select the variant, so this is **not** a clean held-out result and no superiority claim is implied.


---

## Follow-up: default prompt v2 after dev-split replication (2026-11-07)

The one-sentence authorization-policy clarification from the graph false-hold
ablation was replicated on the 13 schema-valid dev cases (7 ACCEPT / 6 HOLD,
7/7 valid accepted, 0/6 false accepts) and promoted to the default LLM prompt
`membud-transition-checks-v2`. The legacy v1 prompt remains selectable
(`policy_clarification=False`) so the original cached run and its analysis stay
reproducible.

The held-out evaluation was then re-run (`data/results/llm-heldout-v2-prompt-v2/`):
graph rows replayed the cached G2 completions, text rows were new paid calls
under the clarified prompt, and semantic is unchanged.

| Condition | ACCEPT | HOLD | INVALID_INPUT | False accepts | False holds | Valid accepted |
|---|---:|---:|---:|---:|---:|---:|
| graph (LLM) | 7 | 6 | 5 | 0/6 | 0/7 | 7/7 |
| text (LLM) | 7 | 6 | 5 | 0/6 | 0/7 | 7/7 |
| semantic | 7 | 6 | 5 | 0/6 | 0/7 | 7/7 |

24,265 reported tokens (text only), 38 cache hits, no budget abort; estimated
$0.0024-$0.0485 at the harness placeholder. The text condition's previous
`HELD-03` false hold is fixed by the same clarification.

**Not a clean held-out result:** the held-out split was used to select the
prompt, so these numbers are development evidence; the dev replication is the
out-of-sample check. Labels remain single-reviewer and this is one model and one
run. No representation-superiority claim: all three conditions tie on this
split.
