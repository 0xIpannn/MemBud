# MemBud

MemBud checks proposed memory-state transitions before they are committed. It provides a canonical transition model, deterministic checks for coverage, preservation, and faithfulness, and graph and text representations of the same input. Rule-based verification is the default; an optional LLM verifier supports controlled graph/text experiments through an OpenAI-compatible API. No API calls occur unless the LLM verifier is explicitly selected.

## Current status and roadmap

MemBud is an experimental local prototype, not a production memory service.

### Done

- Canonical transition/evidence/operation schemas, graph and text encodings, deterministic coverage/preservation/faithfulness checks, fail-safe HOLD behavior, guarded JSON storage, and append-only audit records.
- Paired three-condition runner (unguarded, graph, text), hash-pinned 12-case synthetic exploratory set, separate labels, metrics, and annotation/protocol documentation. The case set has **one reviewer**, not independent double annotation.
- Optional JSON-mode OpenAI-compatible LLM verifier. DeepSeek's official API was used for an exploratory 12-case run with model ID `deepseek-flash` (currently served as DeepSeek-V4.1-Flash-1017 per DeepSeek docs). Full artifacts: `data/results/deepseek-pilot-v1/pilot-summary.json` and `data/results/evaluation-v1-deepseek-metrics.json`.
- The DeepSeek run's model decisions on successful responses: graph 3 ACCEPT / 4 HOLD; text 4 ACCEPT / 4 HOLD. There were **5 graph and 4 text verifier errors**, which failed closed as HOLD and are separately excluded from decision-quality counts. Error-adjusted faulty-case detection was 4/7 in each condition; false accepts were 0/7. Valid-update acceptance was 3/5 graph and 4/5 text. This small exploratory run does **not** show graph superiority.
- Reported token usage was 32,126 in the full run plus 5,946 in its preliminary paired probe. The API did not return billed dollars; check the provider billing dashboard for actual spend. Earlier Command Code provider-API attempts returned HTTP 403 and were stopped.

### Still to do

1. Diagnose the 9 DeepSeek verifier errors (HTTP 500s and malformed completion envelopes), improve failure metadata/retries, then rerun only failed cases using the persistent response cache. Confirm actual spend in the provider dashboard before further paid calls; token usage alone is not a bill.
2. Independently review false positives/negatives and missing/error cases. Add a second reviewer/adjudication before describing any labels as benchmark ground truth; the present set is single-reviewed and synthetic.
3. Add checkpoint/resume, stronger cost accounting, and a broader held-out evaluation with a justified sample size. Measure downstream QA utility only where the dataset supports it.
4. Add a Pi Code integration only through a safe staged-write/pre-commit boundary. There is currently no Pi Code extension or automatic write interception; the provider API failure on the Command Code plan also remains documented.
5. Version the serialized transition schema and add migration policy before treating it as a stable interchange format. Revisit backend integrations only after a safe pre-commit hook is available.

See `data/pilot-report.md`, `data/experiment-protocol.md`, and `TODO.md` for run details, methods, limitations, and prioritized work.

## Run

Install the project in editable mode, then run the demo:

```bash
python -m pip install -e .
python -m membud.demo
```

This runs 11 hand-authored cases through both input paths and writes isolated per-case stores plus an append-only audit log under `membud-output/`. Per-case store files are reset on each run; the audit log remains append-only. It also exports Mermaid `.mmd` diagrams for the cases; `example-graph.md` and `example-graph.mmd` are documentation aliases of the valid case, not additional fixtures. Open any `.mmd` file with a Mermaid preview; raw diagram source starts with `flowchart LR`.

Run tests with `python -m pytest` after installing pytest (`python -m pip install pytest`).

Run the three-condition plumbing pilot on the hand-authored development fixtures:

```bash
python -m membud.experiment --output pilot-output --code-revision <git-revision>
```

This writes isolated stores, append-only condition audit logs, and `pilot-summary.json`; the CLI records a source-package working-tree SHA-256 unless `--code-revision` overrides it. The default input is the development fixture set; use `--manifest data/manifests/evaluation-v1.json` for the hash-pinned synthetic exploratory set. The manifest has one reviewer and is not a representative benchmark. The default `--verifier rules` path runs the same deterministic checks for graph and text, so it cannot establish representation superiority. To invoke the optional LLM verifier through DeepSeek's official API, make `DEEPSEEK_API_KEY` available to the process and run with `--verifier llm --provider deepseek`; `DEEPSEEK_MODEL` defaults to `deepseek-flash` and `DEEPSEEK_BASE_URL` to `https://api.deepseek.com`. The client uses OpenAI-compatible Chat Completions with JSON mode. The legacy Command Code route remains selectable with `--provider commandcode` and `CMD_API_KEY`. Never commit or paste API keys. The client caches validated responses in SQLite at `~/.cache/membud/deepseek-cache.sqlite3` for DeepSeek (or `~/.cache/membud/commandcode-cache.sqlite3` for Command Code); override with `DEEPSEEK_CACHE_PATH` or `CMD_CACHE_PATH` respectively. The cache and audit logs contain prompt inputs and model outputs, so keep them private and use only authorized data. Cache keys include model, request settings, and full prompt/input; cache hits make no API call and report zero new token usage. LLM results record requested/returned model, prompt version, non-secret request settings, raw structured response, token usage, retries, cache-hit status, and latency in the audit log. Completion usage responses provide token counts but not billed monetary cost; check the provider billing dashboard rather than inferring dollars from tokens. Pilot metrics and limitations are recorded in `data/pilot-report.md`.

## Code layout

- `membud.schemas`: canonical records, evidence, operations, transitions, checks, and validation.
- `membud.graph`: canonical graph construction and Mermaid rendering.
- `membud.serialization`: lossless dictionary and deterministic text representations.
- `membud.verification`: deterministic and LLM-assisted coverage, preservation, faithfulness, and decision policy. Checks return `PASS`, `FAIL`, or `UNCERTAIN` statuses with machine-readable reason codes and affected IDs.
- `membud.llm`: bounded-retry, JSON-mode OpenAI-compatible chat client with usage/latency capture.
- `membud.store`: guarded commits, JSON persistence, and append-only audit logging. A transition is verified only when the persisted store's records and claims match the transition's `before` state; otherwise the run fails closed and records an audit error. Commits reject `ACCEPT` results unless exactly the required checks (coverage, preservation, faithfulness) are present, pass, and carry a consistent `passed` flag. Accepted state is written atomically (same-directory temp file, fsync, rename) and only on `ACCEPT`; held transitions never create or rewrite the store file. Audit events are appended after the guarded write, and the write is rolled back if the append fails. Callers must pass the result from the selected verifier; the store validates result structure but does not independently rerun an external LLM verifier. Audit entries include run ID, UTC start time, elapsed time, and optional caller-supplied code revision.
- `membud.demo`: bundled fixtures and the local demonstration runner.
- `membud.experiment`: paired unguarded/graph/text development pilot runner.
- `membud.metrics`: explicit-denominator metrics for a hash-pinned manifest run.
- `membud.adapters`: structural adapter contract for host memory backends.

`membud.core` is a compatibility facade that re-exports the public API. New code should import from the focused modules directly.

## Schema and compatibility

The canonical transition is represented by `Transition`, `Record`, `Evidence`, and `Operation` in `membud.schemas`. `transition_dict()` emits the normalized JSON form: `case_id`, `before`, `candidate`, `evidence`, `required_claims`, and `operations`. Legacy input using `supersedes` is accepted and normalized to explicit operations; serialized output uses `operations` only.

The serialized transition format is not currently versioned. Treat it as an evolving local format, not a stable interchange protocol. Before persisting data across releases or integrating external systems, add an explicit schema version and migration policy. The `membud.core` facade is retained for compatibility, while focused module imports are preferred; changes to canonical fields or validation semantics may require callers to update.

## Input semantics

A transition contains the full prior state (`before`), the full proposed resulting state (`candidate`), explicit source-supported claims (`evidence[].supports`), claims required to survive (`required_claims`), and proposed `operations`. Evidence can also retain a `source` reference and original `text` excerpt. Claims are exact strings; semantic equivalence is intentionally not inferred.

Operations use explicit types and record references:

- `WRITE` proposes a new candidate record.
- `REVISE` changes an existing record while retaining its ID.
- `DELETE` removes a prior record and requires a non-empty `reason` as the toy authorization annotation.
- `SUPERSEDE` replaces a prior record with a distinct candidate record.
- `NOOP` must be the only operation, and its candidate state must equal the prior state.

Legacy input with `supersedes` and exactly one candidate record is normalized to these operations; serialized transitions use only `operations`.

Faithfulness allows claims already present in prior memory or explicitly supported by evidence. Preservation requires prior claims to remain on the same record unless an explicit `REVISE` or `SUPERSEDE` supplies evidence for every added replacement claim, or an explicit reasoned `DELETE` removes the record. An operation alone does not make an unsupported revision safe. Omitting an operation does not authorize removal, which lets the fixtures represent accidental omissions as well as intentional changes. Candidate state replaces the store only on `ACCEPT`, and only when the persisted store matches the transition's prior state; held transitions never rewrite the store file.

The default rule-based graph and text paths serialize the same information, then run the same deterministic checks. The optional LLM path sends graph or text serialization to the same model and prompt policy. Graph edges connect the transition to prior/candidate records, evidence, required claims, and operations; the renderer does not invent missing relationships. The deterministic verifier emits pass/fail results; the LLM verifier can also return `UNCERTAIN`. Any failed or uncertain check prevents commit. The graph is currently a representation/demo artifact, not a distinct semantic verifier. The fixtures function as their own annotation oracle and are not benchmark evaluation data.

## Demo scenarios and limitations

The bundled cases exercise valid updates (`valid`, `valid-noop`, `preserve-added-claim`, `supported-revision`, `explicit-supersession`, `remove-record`) and unsafe updates (`omission`, `corruption`, `unsupported`, `unsupported-plus-valid`, `implicit-removal`). The expected decisions and failing checks for each case are covered by the test suite. These are hand-authored examples, not a representative benchmark. Claims are compared as exact strings; the checks do not infer semantic equivalence, resolve factual conflicts, or establish whether a deletion reason is actually authorized. A successful demo run is not evidence of production readiness or graph-verification effectiveness.
