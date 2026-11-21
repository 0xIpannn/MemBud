# MemBud TODO

Prioritized next steps from the current implementation and `PLAN.md`.

Reconciled with `PLAN.md` §19 on 2026-11-08: the actionable list is at the top;
the sections below are a historical work log (mostly complete) and the
post-Milestone-2 log.

## Next up (2026-11-08, updated after the format/infra round)

1. [ ] Collect the independent second human review and adjudicate the v2 labels (`data/review/quiz-v2.html`).
2. [ ] Run a genuinely independent, better-powered evaluation on fresh cases with tuning/final separation; the paired-CI/sample-size machinery is delivered (`scripts/analyze_paired_metrics.py`, `docs/statistical-analysis.md`), but fresh cases, second-review labels, and spend approval remain.
3. [ ] Confirm provider-billed cost. Checkpoint/resume and token-class estimate accounting are delivered, but the provider returns no bill and only a placeholder/explicit-rate estimate exists.
4. [x] Real backend end to end: Mem0 `2.2.1` (embedded Qdrant + SQLite history, real entity linking) behind the host-owned staged write wrapper `membud.adapters.mem0_staged`; HOLD/REJECTED/error leave every host file and write surface byte-identical (`docs/mem0-guarded-integration.md`, `data/results/mem0-guarded-v1/evidence.json`). Scope is the pinned stack with offline model fakes.
4b. [ ] Follow-ups to that integration: run it with a real LLM/embedder (needs spend approval), cover other Mem0 versions/vector stores and `AsyncMemory`, make commit transactional or crash-safe, add incremental snapshots (cost is O(store) per write), and implement A-Mem.
5. [ ] If productizing: add concurrency/version arbitration to the opencode guard (TOCTOU between snapshot and write) and decide on sidecar auth/TLS/rate limits; extend live tests to unscripted agent behavior, other providers, and MCP/real backends.

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
- [x] Add checkpoint/resume support before external/model calls (`membud.checkpoint`, `--resume`; config-fingerprint validation, zero-call row replay, crash-tolerant JSONL, budget totals preserved; see also token-class cost accounting in `membud.cost`).
- [x] Record/report graph preparation time and verifier time separately, plus graph node/edge counts. Successful LLM responses include token usage and retries; failed response attempts still need more complete provider-attempt metadata. The provider completion response does not report billed cost.

## Backend integrations

- [x] Define the structural adapter contract and atomicity/provenance requirements (`src/membud/adapters/`, `data/adapter-contract.md`).
- [x] Inspect Mem0's proposed-update/write path; no supported safe pre-commit hook was found, so defer adapter/fork (`data/backend-feasibility.md`).
- [x] Inspect A-Mem's note/evolution write path; safe guarding requires staging/forking, so defer integration (`data/backend-feasibility.md`).
- [x] Phase 0 opencode productization spike: stdlib localhost `/verify` sidecar (`membud.service`) plus a no-build `tool.execute.before` guard plugin against a mock memory tool (`integrations/opencode/`); 39 sidecar tests, node/bun harness, real opencode 1.19.5 module-load smoke under an offline network namespace, and measured loopback latency. The live model-originated denial test is recorded below; no MCP/memory-backend integration.
- [x] Mem0 host-owned staged-proposal bridge (`membud.adapters.mem0_bridge`) with deterministic mapping and byte-identical HOLD tests; the bridge itself imports no Mem0 (`data/mem0-bridge.md`).
- [x] Real Mem0 `2.2.1` guarded end to end via shadow-clone staging and state-diff side-effect derivation (`membud.adapters.mem0_staged`, 38 tests, `scripts/run_mem0_guarded_evidence.py`): byte-identical HOLD across memory points, entity points, history, messages and Mem0's config dir; ACCEPT persists exactly the verified state; unmodeled writes (foreign scope, DDL, history rewrite) rejected; stale-state refusal; injected-failure compensation. Pinned stack, offline model fakes, no A-Mem (`docs/mem0-guarded-integration.md`).
- [x] Live model-originated opencode denial test: a real `deepseek/deepseek-flash` session in opencode 1.19.5 called the plugin-registered `memory_write` tool; HOLD was denied before execution with byte-identical state and surfaced to the model, ACCEPT executed and persisted exactly the candidate. Reproducible via `scripts/run_live_opencode_denial.py --confirm-paid-call` (≈$0.0005 for the run); evidence in `integrations/opencode/live/EVIDENCE.md`.
- [ ] If productizing: decide on sidecar authentication/TLS/rate limiting/concurrency-version arbitration; extend the live test to unscripted agent behavior, other providers/models, and a real backend/MCP tool through a host-owned staged write.
- [ ] Treat Zep/Graphiti as a stretch goal after the core evaluation is reproducible.

## Run and report the evaluation

- [x] Run an 11-case local development smoke pilot through unguarded, graph, and text paths; outcomes/limitations recorded in `data/pilot-report.md` (not an evaluation run).
- [x] Freeze deterministic pilot configuration/manifest and run all three local conditions on identical cases; source revision was an uncommitted working-tree label, so repeat with a pinned commit for any reported final run.
- [x] Compute exploratory omission, corruption, unsupported-addition, detection, false-hold, and valid-update acceptance rates with explicit denominators (`data/results/evaluation-v1-metrics.json`).
- [x] Measure latency and reported token usage on the DeepSeek exploratory pilot; failures and cache hits are reported separately.
- [ ] Measure billed cost using provider billing data. Downstream QA utility is measured on a deterministic conversation simulator (`docs/qa-utility.md`); real-dialogue QA is still open.
- [ ] Review false positives and false negatives independently; report missing runs and limitations.
- [x] Update the README and pilot report with what has actually been tested; make no graph-superiority claim without comparative results.


## Milestone 2 — architecture-agnostic safety layer (complete)

- [x] Strict operation-coverage invariants: undeclared insertions, modifications, and removals are rejected; every operation must map to a real state change; prior/candidate references are validated.
- [x] Structured DELETE authorization (`source`, `evidence_id`, `target_id`, `rationale`); DELETE without it is rejected, and the evidence reference/target are validated.
- [x] Structural graph verifier over actual adjacency (provenance paths, required-claim reachability, operation edges, contradiction edges, orphan claims, structural consistency); retained independent claim-set text baseline.
- [x] Separate semantic verification (`membud.semantic`) with fail-safe `UNCERTAIN`; deterministic baseline unchanged.
- [x] Versioned store (`schema_version`/`version`), legacy migration, optimistic `expected_version`, POSIX flock, atomic ACCEPT-only writes, stray-temp recovery, optional semantic check at commit.
- [x] Reference-backend write interception demonstrated with byte-identical HOLD behavior; Mem0 source inspection documented (no safe public pre-commit hook; superseded for the pinned stack by the real-Mem0 wrapper, 2026-11-19).
- [x] 36-case hash-pinned v2 evaluation set with 18 dev / 18 held-out cases, 14 ACCEPT / 12 HOLD / 10 INVALID_INPUT; rules-only three-condition run and metrics (precision/recall, false accepts/holds, valid acceptance, latency overhead, invalid-input handling, paired differences, per-split breakdown).
- [x] Second-review process prepared (review sheet generator + adjudication template); no independence fabricated.
- [x] Updated README/PLAN/TODO and added `docs/technical-report-milestone-2.md`.

### Post-Milestone-2 work log (2026-11-07/08)

- [x] Fairness/equivalence audit (plain text vs fair text with equal contradiction access vs structural graph): fair text matches graph on all 26 non-invalid cases; the earlier 2/36 graph advantage is explained by annotation access, not structural inference (`docs/fairness-audit.md`).
- [x] Define and evaluate a graph hypothesis where relational structure supplies detection capability a fair text verifier cannot reconstruct from the same facts (multi-hop provenance/derivation, entity-relation contradictions across records, temporal ordering), and compare against a fair-text control with identical information (2026-11-19). The new `membud-relational` format, a 480-case hash-pinned minimal-pair suite (`data/manifests/relational-v1.json`), and an independent graph verifier vs information-matched text controls with 1-hop, 2-hop and closure reach. Bounded text fails exactly beyond its reach (1-hop: 144/240 false accepts if optimistic, or 144/240 false holds if pessimistic). Closure text = graph on 480/480 decisions and fail codes. The advantage is reasoning reach, not representation. See `docs/relational-hypothesis.md`.
- [x] Downstream conversational QA utility benchmark over guarded vs unguarded stores (simulated multi-session conversations, noisy extractor with five fault types, fixed reader; 200 conversations × 3 fault rates; paired bootstrap CIs). At fault rate 0.35, graph/closure gating improves overall EM from 0.852 to 0.942 (+0.090 [+0.079, +0.101]) and removes wrong user answers, but turns some into stale ones (+0.072) by holding malformed real updates. 1-hop pessimistic gating (0.250) is far worse than no gate. See `docs/qa-utility.md`.
- [x] Switch primary LLM provider configuration and runners from DeepSeek to Anthropic Claude (`claude-haiku-4-5-20251001` default, `claude-sonnet-4-5-20250929` option via `CLAUDE_MODEL`) and Google Gemini (`gemini-2.5-flash` default, `gemini-2.5-pro` option via `GEMINI_MODEL`), keeping legacy DeepSeek/Command Code routes only for historical artifact replay (`src/membud/llm/client.py`, `src/membud/cost.py`, runners, `.env.example`, `README.md`).
- [ ] Run the relational suite through the LLM verifier (graph JSON vs flat text, stratified by `required_hops`) to test whether *representation* matters when reach is not a parameter. Needs spend approval.
- [ ] Validate the QA utility findings on real multi-session dialogue with a model extractor (e.g. LoCoMo-style), and add a HOLD → clarification/re-extraction repair path to recover the stale answers that gating alone causes.
- [ ] Collect the pending independent second human review and adjudicate v2 labels.
- [x] Run the optional semantic/LLM conditions on the v2 held-out split under the approved $2 cap (101,231 reported tokens, ~$0.40 upper-bound estimate, no abort): graph LLM 3/10/5, text LLM 6/7/5, semantic 8/5/5; no superiority claim. See `docs/llm-heldout.md`.
- [x] Make the semantic verifier consume `evidence.contradicts` (it inherited plain text's blind spot and accepted HELD-13), then rerun the cached held-out conditions: cache-only rerun with 0 provider attempts / 0 new tokens / 51 cache hits / 0 misses; graph and text unchanged, semantic HELD-13 ACCEPT→HOLD (`CONTRADICTED_CLAIM`), semantic false accepts 1/6→0/6, faulty detection 5/6→6/6 (`data/results/llm-heldout-v2-rerun/`).
- [x] Investigate the graph-LLM false holds (4/7 valid held; prompt/serialization effects, retries) from the response cache only: all 52 completions attributed 1:1; all four fail preservation with reasons citing null/absent authorization on evidence-backed `REVISE`/`SUPERSEDE`; length/token asymmetry not supported; 7/52 calls retried once. See `docs/graph-llm-false-holds.md`. No paid calls.
- [x] Run the controlled G0/G1/G2 graph false-hold ablation under the approved spend: G0 (cache control) reproduced the 4 false holds; G1 (omit null authorization) converted 2/4; G2 (one-sentence policy clarification) converted 4/4 and on the 13 valid held-out cases accepted 7/7 valid with 0/6 false accepts and 0 false holds. 27 provider attempts across the probe and paired runs (25 called rows; see the counting convention in `docs/graph-llm-false-holds.md`), 76,486 accounted tokens (≈$0.008–$0.15 placeholder estimate). Development evidence only: the split was used to select the variants. See `docs/graph-llm-false-holds.md`.
- [x] Replicate the G2 policy-clarification prompt variant on the unused dev split: 7 ACCEPT / 6 HOLD, 7/7 valid accepted, 0/6 false accepts, 0 false holds (13/13 label agreement; 13 called rows / 16 provider attempts, 40,906 accounted tokens). Artifact `data/results/graph-false-hold-ablation-dev/`.
- [x] Promote the clarified prompt to the default `membud-transition-checks-v2` (legacy v1 selectable) and re-run the held-out evaluation: graph/text/semantic each 7 ACCEPT / 6 HOLD / 5 INVALID, 0 false accepts, 0 false holds (24,265 new tokens, no abort; artifact `data/results/llm-heldout-v2-prompt-v2/`). Held-out was used for prompt selection, so this remains development evidence.
- [x] Stabilize transition/store formats as a versioned interchange protocol: `membud.migrations` + `docs/format-policy.md` (registry, compatibility matrix, explicit v1->v2 transition migration, store migration on ACCEPT only, unknown-version rejection, release checklist) and hand-written cross-version tests in `tests/test_format_migration.py` (13 originally; 19 after the 2026-11-19 remediation).
- [x] Add checkpoint/resume for model runs: append-only JSONL checkpoint with a canonical config fingerprint, zero-call row replay (including `--require-cache`), torn-tail tolerance, and preserved budget/attempt/cache totals (`src/membud/checkpoint.py`, `scripts/run_llm_heldout.py --resume`).
- [x] Add token-class cost accounting with mandatory pricing provenance and hard `verified_billed: false` (`src/membud/cost.py`; both runners accept `--price-*` flags; every figure labelled an estimate, never a bill).
- [x] Deliver paired statistical-analysis infrastructure from committed artifacts: percentile bootstrap CIs over cases, exact McNemar, Wilson intervals, and a sample-size/precision rationale (`scripts/analyze_paired_metrics.py`, `data/results/statistical-analysis/report.json`, `docs/statistical-analysis.md`). Results remain exploratory; no superiority or equivalence claim.
- [ ] Run a genuinely independent, better-powered evaluation on fresh cases before treating the v2 prompt as validated; collect the pending second human review.
- [ ] Use the blind quiz (`data/review/quiz-v2.html`) to collect the independent second review of the v2 labels.
- [ ] Confirm provider-billed cost and run a powered evaluation with a justified sample size (checkpoint/resume and analysis machinery are delivered).
- [ ] Implement a real third-party backend integration through a host-owned staged-write/fork after a safe pre-commit boundary is demonstrated.

### Software review and remediation (2026-11-19)

- [x] Independent four-reviewer software review plus Lead validation of every Major finding; report: `docs/software-review-2026-11-19.md` (8 Major / 24 Minor / 9 Nit, no Blocker; committed evaluation numbers reproduce).
- [x] Apply the ordered remediation: checkpoint/resume crash-window recovery with zero-charge pre-request failures; host-apply-before-shadow-commit and `resync_shadow_store` recovery in the reference adapter; honest `store_written`/`rolled_back` audit fields and documented write/audit ordering; enforced schema-v2 commit gate with strict `schema_version` typing (archival v1 replay via explicit `allow_legacy=True`); `prepared_input` graph validation; corrected 12/12 claim, dev-split artifact metadata, false-hold analysis reproducibility, latency/paid-call/live-cost figures; owner-only PII logs; JSON protocol-error envelopes in the sidecar; repo-relative artifact paths; failure-injection/regression tests and CI (pytest + opencode guard harness).
- [x] Full suite after remediation: 573 passing, 0 skipped (also verified on Python 3.12 and 3.14).

### Actual conversation pipeline (2026-11-19)

- [x] Multi-format conversation ingestion (`membud`, `openai`, `opencode`, `halumem`, plain text/Markdown) and canonical `ConversationTurn`/`ConversationTranscript` schemas (`src/membud/conversation.py`).
- [x] Transcript-grounded evidence verification (`verify_transcript_grounding`, `verify_with_transcript`) integrated into `membud.store`, `/verify` sidecar (`membud.service`), and `integrations/opencode/plugin/membud-verify-guard.js` (`harness.mjs`).
- [x] Conversation extractors (`RuleBasedConversationExtractor`, `LLMConversationExtractor`) and multi-turn session runner (`run_conversation_session`, `python -m membud.conversation`).
- [x] Hash-pinned multi-turn conversation evaluation (`data/manifests/conversations-v1.json`, `scripts/run_conversation_evaluation.py`, `data/results/conversations-v1/`): 8 conversations / 28 steps (`graph` and `text_fair` 17/17 valid acceptance, 10/10 faulty detection, 0 false accepts, 8/8 safe final states vs 4/8 unguarded) plus 8/8 raw-transcript extraction convergence (`20/20` `ACCEPT`). See `docs/conversation-pipeline.md`.
