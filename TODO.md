# MemBud TODO

Prioritized next steps from the current implementation and `PLAN.md`.

## Immediate: make the prototype robust

- [x] Review the canonical schema and public API; document schema/versioning and compatibility expectations.
- [x] Add focused tests for graph construction and Mermaid rendering, including node/edge provenance and disconnected or malformed inputs.
- [x] Add end-to-end store tests for accepted, held, and invalid transitions; verify held updates never alter persisted state.
- [x] Test audit-log behavior across restarts, failed writes, and repeated case IDs.
- [x] Add validation for edge cases in operation references, duplicate IDs, and candidate/prior record consistency.
- [x] Document known limitations and expected behavior for each bundled demo case.

## Evaluation design

- [x] Inspect HaluMem's current data format, labels, license, and usage constraints; record whether it supports each planned metric (`data/README.md`).
- [x] Define annotation rules for required claims, material claims, valid prior records, and expected persistence (`data/annotation-guidelines.md`).
- [x] Review/finalize the synthetic cases under the one-reviewer exploratory protocol; project owner approved the case set. Do not describe labels as double-annotated ground truth.
- [x] Freeze a hash-pinned exploratory case manifest and keep development fixtures separate (`data/manifests/evaluation-v1.json`).
- [x] Specify unguarded, graph, and text conditions and information-equivalence requirements in `data/experiment-protocol.md`.
- [x] Define draft metrics, denominators, failure handling, and pilot-vs-final sample-size/precision expectations in `data/experiment-protocol.md`.

## Verification and experiment infrastructure

- [x] Implement a common verifier interface for graph and text inputs; keep the current deterministic checks as a baseline (`verify(transition, representation=...)`).
- [x] Add check statuses/reason codes and fail-safe commit validation; uncertain or invalid results cannot authorize commits.
- [x] Reconcile the persisted store with `transition.before` before verification/commit; write accepted state atomically only on `ACCEPT`; leave held stores untouched.
- [x] Freeze verification results and require consistent `status`/`passed` flags at commit.
- [x] Add bounded transient-failure and malformed-JSON/schema retries; retries never independently authorize commits.
- [x] Add a configurable OpenAI-compatible LLM client and verifier for DeepSeek's official API plus an optional Command Code route; pin model/prompt/settings in audit output and keep credentials out of the repository.
- [x] Add persistent SQLite response caching keyed by provider/model, prompt/input, and request settings.
- [x] Add append-only run logging for inputs, outputs, decisions, resulting state, run ID, optional code revision, timing, provider token usage, retry counts, and failure events. Provider responses do not report billed monetary cost; pin a source revision/hash for reproducible model runs.
- [x] Add a local 3-condition development pilot runner (`python -m membud.experiment`) with an explicitly selected optional LLM verifier.
- [x] Harden the DeepSeek client: retry bounded malformed completion envelopes, cap provider retry delays, and record attempt counts on failed verifier rows.
- [x] Rerun the 9 failed graph/text rows (5 graph, 4 text); all completed (24,407 reported tokens, 0 cache hits, no residual errors) and the combined decisions match the labels in both conditions.
- [ ] Add checkpoint/resume support before external/model calls.
- [x] Record/report graph preparation time and verifier time separately, plus graph node/edge counts. Successful LLM responses include token usage and retries; failed response attempts still need more complete provider-attempt metadata. The provider completion response does not report billed cost.

## Backend integrations

- [x] Define the structural adapter contract and atomicity/provenance requirements (`src/membud/adapters/`, `data/adapter-contract.md`).
- [x] Inspect Mem0's proposed-update/write path; no supported safe pre-commit hook was found, so defer adapter/fork (`data/backend-feasibility.md`).
- [x] Inspect A-Mem's note/evolution write path; safe guarding requires staging/forking, so defer integration (`data/backend-feasibility.md`).
- [ ] Treat Zep/Graphiti as a stretch goal after the core evaluation is reproducible.

## Run and report the evaluation

- [x] Run an 11-case local development smoke pilot through unguarded, graph, and text paths; outcomes/limitations recorded in `data/pilot-report.md` (not an evaluation run).
- [x] Freeze deterministic pilot configuration/manifest and run all three local conditions on identical cases; source revision was an uncommitted working-tree label, so repeat with a pinned commit for any reported final run.
- [x] Compute exploratory omission, corruption, unsupported-addition, detection, false-hold, and valid-update acceptance rates with explicit denominators (`data/results/evaluation-v1-metrics.json`).
- [x] Measure latency and reported token usage on the DeepSeek exploratory pilot; failures and cache hits are reported separately.
- [ ] Measure billed cost using provider billing data and downstream QA utility where supported.
- [ ] Review false positives and false negatives independently; report missing runs and limitations.
- [x] Update the README and pilot report with what has actually been tested; make no graph-superiority claim without comparative results.
