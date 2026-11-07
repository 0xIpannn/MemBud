# Local development pilot report

A plumbing smoke run executed the 11 hand-authored cases from `membud.demo.cases()` through all three conditions using `membud.experiment.run_pilot`:

```bash
python -m membud.experiment --output /tmp/membud-pilot-check --code-revision local-working-tree
```

The run produced 33 case-condition rows and zero runner errors. Results were:

| Condition | ACCEPT | HOLD | APPLIED | Errors |
|---|---:|---:|---:|---:|
| Unguarded | 0 | 0 | 11 | 0 |
| Graph guarded | 6 | 5 | 0 | 0 |
| Text guarded | 6 | 5 | 0 | 0 |

The output directory contains isolated condition stores, JSONL audit events, and a summary JSON. Tests verify that guarded stores retain prior state on HOLD and unguarded stores apply the same candidates. The full test suite passed (112 tests as of the guarded-store, client, and runner-isolation hardening changes) after the pilot-runner changes.

## Frozen exploratory set run

After the project owner reviewed and approved the case policy, a 12-case, hash-pinned synthetic manifest was created at `data/manifests/evaluation-v1.json`, with transition inputs and gold labels stored separately. The manifest was run through the same three conditions:

```bash
python -m membud.experiment --manifest data/manifests/evaluation-v1.json --output /tmp/membud-eval-v1 --code-revision local-working-tree
```

The run produced 36 case-condition rows with zero runner errors. Unguarded applied all 12 candidates. Graph and text each returned 5 ACCEPT and 7 HOLD, matching all expected decisions, failing checks, and affected IDs (covered by `tests/test_evaluation_manifest.py`). The computed denominators and exploratory rates are saved in `data/results/evaluation-v1-metrics.json`: proposal omissions 7/23 required claims; unsupported additions 4/20 material candidate claims; prior-record corruption 4/13 valid prior records expected to persist. Both guarded conditions detected 7/7 labeled faulty cases, held 0/5 labeled valid cases, and accepted 5/5 valid updates. These metrics are mechanically consistent with the explicit rule annotations and tiny synthetic set; they are not independent benchmark estimates. Timing instrumentation recorded mean preparation/verifier/end-to-end time separately: graph `0.018/0.055/0.117 ms` (mean graph size 8.33 nodes / 11.83 edges), text `0.021/0.052/0.117 ms`, and unguarded end-to-end `0.051 ms`. These sub-millisecond figures on 12 tiny local fixtures are timer/runtime noise, not meaningful performance measurements or evidence of a graph cost advantage. No model tokens, API calls, or cost were incurred.

## Command Code connectivity probe

After configuring `CMD_API_KEY` locally, one synthetic case was sent through graph and text conditions using the default `deepseek/deepseek-v4.1-flash` model ID. Both chat-completion requests were rejected with HTTP 403 (`error code: 1010`); no completion IDs, token usage, or valid model outputs were returned. The authenticated model-catalog request was also rejected with HTTP 403. After the user reported an API update, the same one-case graph/text probe was repeated; both requests again received HTTP 403 (`error code: 1010`). These were connectivity probes, not evaluation runs; no completion usage was returned and billing status for rejected requests is not reported. Temporary audit files are in `/tmp/membud-llm-probe` and `/tmp/membud-llm-probe-updated-api`.

## DeepSeek API exploratory pilot

After the Command Code provider route was unavailable on the active plan, the 12-case frozen manifest was run through DeepSeek's official OpenAI-compatible Chat Completions API using `deepseek-flash` (the API returned that model ID; DeepSeek's docs currently map it to DeepSeek-V4.1-Flash-1017). The paired run used the same prompt policy, temperature 0, JSON mode, and candidate transitions for graph and text. Outputs and hash/config metadata are in `data/results/deepseek-pilot-v1/pilot-summary.json`; denominators and metrics are in `data/results/evaluation-v1-deepseek-metrics.json`.

Successful model decisions were graph: 3 ACCEPT / 4 HOLD, text: 4 ACCEPT / 4 HOLD. There were also 5 graph and 4 text verifier errors (6 HTTP 500 failures and 3 malformed completion envelopes), which safely produced HOLD. On completed responses only, both conditions detected 4/4 gold faulty cases and accepted every completed gold valid case. Counting fail-closed error holds as non-accepting (intention-to-treat), detection was 4/7 in each condition, false accepts were 0/7, valid-update acceptance was 3/5 graph and 4/5 text, and valid cases left unaccepted were 2/5 graph and 1/5 text. No completed valid case was falsely held. The conditions flagged different faulty cases (graph DRAFT-03/04/11/16; text DRAFT-03/04/11/14). These are exploratory results only and do not establish representation superiority. The run reported 32,126 tokens across the 13 of 24 required completions that reported usage (9 failed rows and 2 cache hits reported none); a preliminary one-case paired probe added 5,946 reported tokens and was reused from cache. The API did not report billed cost, so actual spend is not measured. One reviewer and the small synthetic case set limit interpretation; the original nine verifier errors were rerun to completion (below).

## DeepSeek rerun of failed rows

The 9 verifier-error rows (5 graph, 4 text) were rerun with `scripts/rerun_failed.py` under the same model, prompt policy, and committed manifest. All 9 completed on rerun (24,407 reported tokens; 11 request attempts; 0 cache hits, since failed rows had no cache entries). Replacing the original failed rows with the rerun rows yields 24/24 completed decisions: both conditions match all gold decisions (5 ACCEPT / 7 HOLD), detect 7/7 faulty transitions, accept 5/5 valid updates, and produce 0 false accepts and 0 false holds. Artifacts: `data/results/deepseek-rerun-v1/rerun-summary.json`, `data/results/deepseek-rerun-v1/merged-pilot-summary.json`, and `data/results/evaluation-v1-deepseek-rerun-metrics.json`. This remains a 12-case single-reviewer synthetic pilot and shows no graph-vs-text difference.

**Limitations:** All runs remain exploratory and do not demonstrate production reliability or graph superiority. The evaluation set has one reviewer and is synthetic, small, and not representative. Downstream QA utility and billed cost remain unmeasured. The earlier deterministic pilot used the label `local-working-tree`; the DeepSeek run records a source-package working-tree SHA-256. Preserve its summary, metrics, audit logs, and exact configuration for any reproduction.

## Expanded v2 rules-only comparison (single reviewer; second review pending)

The frozen 36-case v2 set (`data/manifests/evaluation-v2.json`, 18 dev / 18 held-out, SHA-256 pinned) was run through all three conditions with the **rules verifier only** — no paid API calls, zero tokens, zero cost. Graph now dispatches to the structural graph verifier; text remains the claim-set baseline. The runner records schema-invalid transitions (`expected_invalid`) as `INVALID_INPUT` without aborting and without writing memory. Artifacts: `data/results/evaluation-v2/pilot-summary.json`, `metrics.json`, `run-config.json`, and per-condition audit logs/stores. Reproduce with:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_evaluation_v2.py
```

Exact pooled results (36 cases: 14 gold ACCEPT, 12 gold HOLD, 10 expected INVALID_INPUT):

| Condition | ACCEPT | HOLD | INVALID_INPUT | Errors |
|---|---:|---:|---:|---:|
| Graph (structural) | 14 | 12 | 10 | 0 |
| Text (claim-set baseline) | 16 | 10 | 10 | 0 |
| Unguarded (raw apply) | 26 applied | — | 10 | 0 |

Faulty detection including expected-invalid cases as correct rejections: graph precision 22/22, recall 22/22, false accepts 0/22; text precision 20/20, recall 20/22, false accepts 2/22 (0.0909). Restricted to gold-HOLD cases only (invalid inputs excluded): both conditions detect 12/12, false accepts 0/12, and accept all 14/14 valid updates with false holds 0/14; there were zero verifier/execution errors. Unguarded applied all 12/12 constructible gold-HOLD candidates — a raw false-accept-equivalent rate of 12/12. The expected-invalid set (10 cases: unauthorized DELETE, bad DELETE authorization target/evidence, undeclared insertion/modification/removal) was handled 10/10 in every condition with zero unexpected invalid rows.

Dev and held-out splits are identical by construction: graph 7 ACCEPT / 6 HOLD / 5 INVALID_INPUT per split, text 8 ACCEPT / 5 HOLD / 5 INVALID_INPUT per split; text false accepts are 1/11 per split, graph 0/11. Mean rules-only timings are sub-millisecond and dominated by process/timer noise: graph preparation 0.043 ms, verification 0.111 ms, end-to-end 0.250 ms over 36 end-to-end and 26 constructible cases (invalid-input rows have no preparation/verification timing); text preparation 0.058 ms, verification 0.100 ms, end-to-end 0.293 ms. Estimated monetary cost is 0.00 USD (rules-only; no billing was involved). Graph and text made different decisions on exactly 2 of 36 cases, both the deliberately authored unresolved-contradiction cases (`DEV-13`, `HELD-13`): graph HOLD, text ACCEPT. This difference is attributable to verifier construction — the text claim-set baseline is deliberately not aware of the new `evidence.contradicts` annotation, while the graph verifier reads the contradiction edge. It is **not** evidence of general graph superiority: 34/36 decisions were identical, the set is synthetic and single-reviewed, and the two differing cases were designed to exercise the graph-only contradiction field.

**Limitations for the v2 run:** all labels are single-reviewer annotations; a second independent human review is pending (`data/review/README.md`, blank sheet `data/review/review-sheet-v2.md`, template `data/review/adjudication-template.md`), so no inter-annotator agreement exists. The 36 cases were authored together and frozen before the run; the dev/held-out split is descriptive, not a power analysis. Rules-only timing numbers are not meaningful performance measurements. No graph-superiority or production-reliability claim is supported.
