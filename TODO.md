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
- [x] Phase 0 opencode productization spike: stdlib localhost `/verify` sidecar (`membud.service`) plus a no-build `tool.execute.before` guard plugin against a mock memory tool (`integrations/opencode/`); 39 sidecar tests, node/bun harness, real opencode 1.19.5 module-load smoke under an offline network namespace, and measured loopback latency. No live model-originated denial test, no MCP/memory-backend integration.
- [x] Mem0 host-owned staged-proposal bridge (`membud.adapters.mem0_bridge`) with deterministic mapping and byte-identical HOLD tests; no Mem0 import and no compatibility claim (`data/mem0-bridge.md`).
- [x] Live model-originated opencode denial test: a real `deepseek/deepseek-flash` session in opencode 1.19.5 called the plugin-registered `memory_write` tool; HOLD was denied before execution with byte-identical state and surfaced to the model, ACCEPT executed and persisted exactly the candidate. Reproducible via `scripts/run_live_opencode_denial.py --confirm-paid-call` (≈$0.0005 for the run); evidence in `integrations/opencode/live/EVIDENCE.md`.
- [ ] If productizing: decide on sidecar authentication/TLS/rate limiting/concurrency-version arbitration; extend the live test to unscripted agent behavior, other providers/models, and a real backend/MCP tool through a host-owned staged write.
- [ ] Treat Zep/Graphiti as a stretch goal after the core evaluation is reproducible.

## Run and report the evaluation

- [x] Run an 11-case local development smoke pilot through unguarded, graph, and text paths; outcomes/limitations recorded in `data/pilot-report.md` (not an evaluation run).
- [x] Freeze deterministic pilot configuration/manifest and run all three local conditions on identical cases; source revision was an uncommitted working-tree label, so repeat with a pinned commit for any reported final run.
- [x] Compute exploratory omission, corruption, unsupported-addition, detection, false-hold, and valid-update acceptance rates with explicit denominators (`data/results/evaluation-v1-metrics.json`).
- [x] Measure latency and reported token usage on the DeepSeek exploratory pilot; failures and cache hits are reported separately.
- [ ] Measure billed cost using provider billing data and downstream QA utility where supported.
- [ ] Review false positives and false negatives independently; report missing runs and limitations.
- [x] Update the README and pilot report with what has actually been tested; make no graph-superiority claim without comparative results.


## Milestone 2 — architecture-agnostic safety layer (complete)

- [x] Strict operation-coverage invariants: undeclared insertions, modifications, and removals are rejected; every operation must map to a real state change; prior/candidate references are validated.
- [x] Structured DELETE authorization (`source`, `evidence_id`, `target_id`, `rationale`); DELETE without it is rejected, and the evidence reference/target are validated.
- [x] Structural graph verifier over actual adjacency (provenance paths, required-claim reachability, operation edges, contradiction edges, orphan claims, structural consistency); retained independent claim-set text baseline.
- [x] Separate semantic verification (`membud.semantic`) with fail-safe `UNCERTAIN`; deterministic baseline unchanged.
- [x] Versioned store (`schema_version`/`version`), legacy migration, optimistic `expected_version`, POSIX flock, atomic ACCEPT-only writes, stray-temp recovery, optional semantic check at commit.
- [x] Reference-backend write interception demonstrated with byte-identical HOLD behavior; Mem0 source inspection documented (no safe public pre-commit hook; no compatibility claimed).
- [x] 36-case hash-pinned v2 evaluation set with 18 dev / 18 held-out cases, 14 ACCEPT / 12 HOLD / 10 INVALID_INPUT; rules-only three-condition run and metrics (precision/recall, false accepts/holds, valid acceptance, latency overhead, invalid-input handling, paired differences, per-split breakdown).
- [x] Second-review process prepared (review sheet generator + adjudication template); no independence fabricated.
- [x] Updated README/PLAN/TODO and added `docs/technical-report-milestone-2.md`.

### Remaining after Milestone 2

- [x] Fairness/equivalence audit (plain text vs fair text with equal contradiction access vs structural graph): fair text matches graph on all 26 non-invalid cases; the earlier 2/36 graph advantage is explained by annotation access, not structural inference (`docs/fairness-audit.md`).
- [ ] Define and evaluate a graph hypothesis where relational structure supplies detection capability a fair text verifier cannot reconstruct from the same facts (multi-hop provenance/derivation, entity-relation contradictions across records, temporal ordering), and compare against a fair-text control with identical information.
- [ ] Collect the pending independent second human review and adjudicate v2 labels.
- [x] Run the optional semantic/LLM conditions on the v2 held-out split under the approved $2 cap (101,231 reported tokens, ~$0.40 upper-bound estimate, no abort): graph LLM 3/10/5, text LLM 6/7/5, semantic 8/5/5; no superiority claim. See `docs/llm-heldout.md`.
- [x] Make the semantic verifier consume `evidence.contradicts` (it inherited plain text's blind spot and accepted HELD-13), then rerun the cached held-out conditions: cache-only rerun with 0 provider attempts / 0 new tokens / 51 cache hits / 0 misses; graph and text unchanged, semantic HELD-13 ACCEPT→HOLD (`CONTRADICTED_CLAIM`), semantic false accepts 1/6→0/6, faulty detection 5/6→6/6 (`data/results/llm-heldout-v2-rerun/`).
- [x] Investigate the graph-LLM false holds (4/7 valid held; prompt/serialization effects, retries) from the response cache only: all 52 completions attributed 1:1; all four fail preservation with reasons citing null/absent authorization on evidence-backed `REVISE`/`SUPERSEDE`; length/token asymmetry not supported; 7/52 calls retried once. See `docs/graph-llm-false-holds.md`. No paid calls.
- [x] Run the controlled G0/G1/G2 graph false-hold ablation under the approved spend: G0 (cache control) reproduced the 4 false holds; G1 (omit null authorization) converted 2/4; G2 (one-sentence policy clarification) converted 4/4 and on the 13 valid held-out cases accepted 7/7 valid with 0/6 false accepts and 0 false holds. 20 paid calls, 76,486 accounted tokens (≈$0.008–$0.15 placeholder estimate). Development evidence only: the split was used to select the variants. See `docs/graph-llm-false-holds.md`.
- [x] Replicate the G2 policy-clarification prompt variant on the unused dev split: 7 ACCEPT / 6 HOLD, 7/7 valid accepted, 0/6 false accepts, 0 false holds (13/13 label agreement; 13 paid calls, 40,906 accounted tokens). Artifact `data/results/graph-false-hold-ablation-dev/`.
- [ ] Use the blind quiz (`data/review/quiz-v2.html`) to collect the independent second review of the v2 labels.
- [ ] Add checkpoint/resume, provider-billing cost accounting, and a powered evaluation with a justified sample size.
- [ ] Implement a real third-party backend integration through a host-owned staged-write/fork after a safe pre-commit boundary is demonstrated.
- [ ] Stabilize transition/store formats as a versioned interchange protocol with migration tests across releases.
