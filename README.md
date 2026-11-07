# MemBud

MemBud checks proposed memory-state transitions before they are committed. It provides a canonical transition model, deterministic checks for coverage, preservation, and faithfulness, and graph and text representations of the same input. Rule-based verification is the default; an optional LLM verifier supports controlled graph/text experiments through an OpenAI-compatible API. No API calls occur unless the LLM verifier is explicitly selected.

## Current status and roadmap

MemBud is an experimental local prototype, not a production memory service.

### Done

- Canonical transition/evidence/operation schemas, graph and text encodings, deterministic coverage/preservation/faithfulness checks, fail-safe HOLD behavior, guarded JSON storage, and append-only audit records.
- Paired three-condition runner (unguarded, graph, text), hash-pinned 12-case synthetic exploratory set, separate labels, metrics, and annotation/protocol documentation. The case set has **one reviewer**, not independent double annotation.
- Optional JSON-mode OpenAI-compatible LLM verifier. DeepSeek's official API was used for an exploratory 12-case run with model ID `deepseek-flash` (currently served as DeepSeek-V4.1-Flash-1017 per DeepSeek docs). Full artifacts: `data/results/deepseek-pilot-v1/pilot-summary.json`, `data/results/evaluation-v1-deepseek-metrics.json`, the deterministic rules run at `data/results/evaluation-v1-rules/pilot-summary.json`, and the 9-row rerun at `data/results/deepseek-rerun-v1/merged-pilot-summary.json` with `data/results/evaluation-v1-deepseek-rerun-metrics.json`.
- The original DeepSeek run produced 15 completed decisions and **9 verifier errors** (5 graph, 4 text) that failed closed as HOLD; on completed responses only, faulty-case detection was 4/4 in each condition. All 9 failed rows were then rerun with the same model/settings on the committed manifest: all completed (24,407 reported tokens, 11 request attempts, 0 cache hits), leaving no residual errors. Using the rerun rows in place of the failures, both conditions return the expected **5 ACCEPT / 7 HOLD** across the 12 cases, detect **7/7** faulty transitions, accept **5/5** valid updates, and produce **0** false accepts and **0** false holds on the single-reviewed labels. This small synthetic pilot still does **not** show graph superiority: the conditions are tied.
- Reported token usage: 32,126 from the original run (13 of 24 completions reported usage; 9 failed rows and 2 cache hits reported none) plus 24,407 from the 9-row rerun, for 56,533 combined across 22 of 24 completions; a preliminary paired probe added 5,946. Even at the top of DeepSeek's published per-token range the rerun bounds at roughly $0.10, but the API does not return billed dollars — check the provider billing dashboard for actual spend. Earlier Command Code provider-API attempts returned HTTP 403 and were stopped.

### Still to do

1. Confirm actual spend in the provider dashboard for the original run, the 9-row rerun, and the preliminary probe (reported tokens: 32,126 + 24,407 + 5,946; token counts are not a bill). The rerun resolved all 9 original verifier errors, so no error-diagnosis work remains.
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

Install the test extra and run the suite: `python -m pip install -e ".[test]"` then `python -m pytest`.

Run the three-condition plumbing pilot on the hand-authored development fixtures:

```bash
python -m membud.experiment --output pilot-output --code-revision <git-revision>
```

This writes isolated stores, append-only condition audit logs, and `pilot-summary.json`; use a fresh `--output` directory for each run, because the runner refuses to reuse one; the CLI records a source-package working-tree SHA-256 unless `--code-revision` overrides it. The default input is the development fixture set; use `--manifest data/manifests/evaluation-v1.json` for the hash-pinned synthetic exploratory set. The manifest has one reviewer and is not a representative benchmark. The default `--verifier rules` path runs the same deterministic checks for graph and text, so it cannot establish representation superiority. To invoke the optional LLM verifier through DeepSeek's official API, make `DEEPSEEK_API_KEY` available to the process and run with `--verifier llm --provider deepseek`; `DEEPSEEK_MODEL` defaults to `deepseek-flash` and `DEEPSEEK_BASE_URL` to `https://api.deepseek.com`. The client uses OpenAI-compatible Chat Completions with JSON mode. The legacy Command Code route remains selectable with `--provider commandcode` and `CMD_API_KEY`. Never commit or paste API keys. MemBud reads credentials from the process environment; it does not load `.env` automatically (see `.env.example`). The client caches validated responses in SQLite at `~/.cache/membud/deepseek-cache.sqlite3` for DeepSeek (or `~/.cache/membud/commandcode-cache.sqlite3` for Command Code); override with `DEEPSEEK_CACHE_PATH` or `CMD_CACHE_PATH` respectively. The cache and audit logs contain prompt inputs and model outputs, so keep them private and use only authorized data. Cache keys include model, request settings, and full prompt/input; cache hits make no API call and report zero new token usage. LLM results record requested/returned model, prompt version, non-secret request settings, raw structured response, token usage, retries, cache-hit status, and latency in the audit log. Completion usage responses provide token counts but not billed monetary cost; check the provider billing dashboard rather than inferring dollars from tokens. Pilot metrics and limitations are recorded in `data/pilot-report.md`.

## Code layout

- `membud.schemas`: canonical records, evidence, operations, transitions, checks, and validation.
- `membud.graph`: canonical graph construction and Mermaid rendering.
- `membud.serialization`: lossless dictionary and deterministic text representations.
- `membud.verification`: dispatches structural graph verification or the exact-claim-set text baseline, plus the optional LLM-assisted path. Checks return `PASS`, `FAIL`, or `UNCERTAIN` statuses with machine-readable reason codes and affected IDs.
- `membud.graph_verification`: structural verifier that derives coverage/preservation/faithfulness from graph adjacency (evidence-to-claim provenance paths, required-claim reachability, `REVISES`/`DELETES`/`SUPERSEDES` operation edges, `CONTRADICTS` edges, orphan/disconnected claim nodes, and operation/authorization edge consistency).
- `membud.semantic`: optional, separate semantic comparison mechanism (paraphrase equivalence and contradictions) with fail-safe `UNCERTAIN`; it never changes the deterministic baseline and is only used when a caller explicitly requests `verify_with_semantics`.
- `membud.llm`: bounded-retry, JSON-mode OpenAI-compatible chat client with usage/latency capture.
- `membud.store`: guarded, versioned JSON persistence and append-only audit logging. Accepted state is stored as `{"schema_version": 2, "version": <int>, "records": [...]}`; legacy bare-list stores load as v1 and migrate on the next `ACCEPT`. Each `ACCEPT` increments the version exactly once, while `HOLD` never creates or rewrites the store. A transition is verified only when the persisted store's records and claims match the transition's `before` state; otherwise the run fails closed and records an audit error. Commits reject `ACCEPT` results unless the three required checks (coverage, preservation, faithfulness) are present, pass, and carry a consistent `passed` flag; an optional fourth `semantic` check is accepted and must also pass. Writes are atomic (same-directory temp file, fsync, rename) and only happen after a successful audit append; stray temp files are cleaned and never read as state. An optional `expected_version` gives optimistic concurrency control, and a POSIX `flock` on `<store>.lock` serializes the guarded load/verify/write/audit critical section. Callers must pass the result from the selected verifier; the store validates result structure but does not independently rerun an external LLM verifier. Audit entries include run ID, UTC start time, elapsed time, store schema/version before and after, lock status, expected version, and optional caller-supplied code revision.
- `membud.demo`: bundled fixtures and the local demonstration runner.
- `membud.experiment`: paired unguarded/graph/text development pilot runner.
- `membud.metrics`: explicit-denominator metrics for a hash-pinned manifest run.
- `membud.adapters`: structural adapter contract for host memory backends.

`membud.core` is a compatibility facade that re-exports the public API. New code should import from the focused modules directly.

## Schema and compatibility

The canonical transition is represented by `Transition`, `Record`, `Evidence`, `DeletionAuthorization`, and `Operation` in `membud.schemas`. `transition_dict()` emits the normalized JSON form: `schema_version`, `case_id`, `before`, `candidate`, `evidence`, `required_claims`, and `operations`. Legacy input using `supersedes` is accepted and normalized to explicit operations; serialized output uses `operations` only.

The serialized transition format is now versioned (`schema_version: 2`). Version 1 input without an explicit `schema_version` remains loadable for frozen archival data and keeps the legacy, weaker operation semantics; version-2 input enforces the strict operation invariants described below. This is an intentional breaking change for callers that construct transitions directly: newly written transitions must declare or default to version 2, and undeclared state changes are rejected. A version 1 → 2 migration is implicit on load (the serialized form is normalized to the current version only when callers rewrite it); the `membud.core` facade is retained for compatibility, while focused module imports are preferred.

## Input semantics

A transition contains the full prior state (`before`), the full proposed resulting state (`candidate`), explicit source-supported claims (`evidence[].supports`), claims required to survive (`required_claims`), and proposed `operations`. Evidence can also retain a `source` reference and original `text` excerpt. Claims are compared as exact strings by the deterministic baseline; semantic equivalence is not inferred there. Optional `evidence[].contradicts` declarations add explicit contradiction relationships consumed by graph verification, and the separate `membud.semantic` module can be requested explicitly when semantic comparison is wanted.

Operations use explicit types and record references:

- `WRITE` proposes a new candidate record.
- `REVISE` changes an existing record while retaining its ID.
- `DELETE` removes a prior record and requires both a human-readable `reason` and structured authorization metadata: `authorization: {source, evidence_id, target_id, rationale}`. `evidence_id` must reference transition evidence, `target_id` must match the operation target, and a missing or mismatched authorization is rejected.
- `SUPERSEDE` replaces a prior record with a distinct candidate record.
- `NOOP` must be the only operation, and its candidate state must equal the prior state.

Strict operation invariants (schema version 2): every added candidate record needs a `WRITE` or `SUPERSEDE`; every changed prior record needs a `REVISE` and a `REVISE` must actually change the record; every removed prior record needs a `DELETE` or `SUPERSEDE`; every operation must correspond to a real state change; a prior record can only be targeted once; and a candidate record can only be proposed once. Undeclared insertions, modifications, or removals raise `ValueError` instead of producing a decision. Legacy input with `supersedes` and exactly one candidate record is normalized to these operations; serialized transitions use only `operations`.

Faithfulness allows claims already present in prior memory or explicitly supported by evidence. Preservation requires prior claims to remain on the same record unless an explicit `REVISE` or `SUPERSEDE` supplies evidence for every added replacement claim, or an explicit reasoned `DELETE` removes the record. An operation alone does not make an unsupported revision safe. Omitting an operation does not authorize removal, which lets the fixtures represent accidental omissions as well as intentional changes. Candidate state replaces the store only on `ACCEPT`, and only when the persisted store matches the transition's prior state; held transitions never rewrite the store file.

The rule-based `graph` and `text` paths are now distinct verification strategies over the same transition facts. `text` is the original exact-claim-set baseline; `graph` builds the canonical graph and makes its decisions from graph structure (provenance paths, required-claim reachability, operation edges for changes/removals, `CONTRADICTS` relationships, connectivity, and structural consistency), so it can fail a transition the claim-set baseline accepts (for example, a candidate claim explicitly contradicted by connected evidence). The optional LLM path sends graph or text serialization to the same model and prompt policy. The deterministic verifiers emit pass/fail results; the LLM verifier can also return `UNCERTAIN`. Any failed or uncertain check prevents commit. The fixtures function as their own annotation oracle and are not benchmark evaluation data.

## Demo scenarios and limitations

The bundled cases exercise valid updates (`valid`, `valid-noop`, `preserve-added-claim`, `supported-revision`, `explicit-supersession`, `remove-record`) and unsafe updates (`omission`, `corruption`, `unsupported`, `unsupported-plus-valid`, `required-claim-deletion`). `implicit-removal` is no longer a runnable fixture: undeclared removals are now rejected by schema validation and are covered by unit tests instead. The expected decisions and failing checks for each case are covered by the test suite. These are hand-authored examples, not a representative benchmark. Claims are compared as exact strings; the checks do not infer semantic equivalence, resolve factual conflicts, or establish whether a deletion reason is actually authorized. A successful demo run is not evidence of production readiness or graph-verification effectiveness.
