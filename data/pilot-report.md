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

The output directory contains isolated condition stores, JSONL audit events, and a summary JSON. Tests verify that guarded stores retain prior state on HOLD and unguarded stores apply the same candidates. The full test suite passed (66 tests) after the pilot-runner changes.

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

Successful model decisions were graph: 3 ACCEPT / 4 HOLD, text: 4 ACCEPT / 4 HOLD. There were also 5 graph and 4 text verifier errors (6 HTTP 500 failures and 3 malformed completion envelopes), which safely produced HOLD and are excluded from decision-quality numerators; execution errors are reported separately. On all gold faulty cases, effective detection was 4/7 for each condition; false accepts were 0/7. Valid-update acceptance was 3/5 graph and 4/5 text; no completed valid case was falsely held. These are exploratory results only and do not establish representation superiority. The run reported 32,126 tokens across successful uncached completions; a preliminary one-case paired probe added 5,946 reported tokens and was reused from cache. The API did not report billed cost, so actual spend is not measured. One reviewer, small synthetic cases, and nine verifier errors limit interpretation.

**Limitations:** All runs remain exploratory and do not demonstrate production reliability or graph superiority. The evaluation set has one reviewer and is synthetic, small, and not representative. Downstream QA utility and billed cost remain unmeasured. The earlier deterministic pilot used the label `local-working-tree`; the DeepSeek run records a source-package working-tree SHA-256. Preserve its summary, metrics, audit logs, and exact configuration for any reproduction.
