# MemBud — Technical Report: Milestone 2

**Date:** 2026-11-07
**Scope:** architecture-agnostic memory-transition safety layer; strict validation, structural graph verification, semantic verification, storage reliability, backend interception, and expanded evaluation.
**Headline:** the implementation now has a real graph-structural verifier and can measure graph-vs-text differences on equivalent inputs, but the milestone does **not** establish graph superiority. The hypothesis remains open.

---

## 1. Summary of changes

| Area | Delivered | Key files |
|---|---|---|
| Transition schema | v2 strict operation-coverage invariants; structured DELETE authorization; `evidence[].contradicts`; versioned serialization; v1 archival compatibility | `src/membud/schemas.py`, `src/membud/serialization.py` |
| Graph verification | Decisions derived from graph adjacency (provenance, reachability, operation edges, contradiction edges, connectivity, structural consistency); text baseline retained | `src/membud/graph_verification.py`, `src/membud/verification.py`, `src/membud/graph.py` |
| Semantic verification | Separate comparator protocol + LLM comparator; fail-safe `UNCERTAIN`; optional fourth check at commit | `src/membud/semantic.py`, `src/membud/store.py` |
| Storage | Versioned store format + migration; optimistic `expected_version`; POSIX `flock`; atomic ACCEPT-only writes; stray-temp recovery; version/lock audit fields | `src/membud/store.py` |
| Backend | Reference in-memory/SQLite backend + `GuardedMemory` interception; Mem0 feasibility finding | `src/membud/adapters/reference.py`, `data/backend-feasibility.md` |
| Evaluation | 36-case hash-pinned v2 set (18 dev / 18 held-out); invalid-input handling; precision/recall; paired differences; per-split metrics; review process | `data/manifests/evaluation-v2.json`, `scripts/run_evaluation_v2.py`, `src/membud/experiment.py`, `src/membud/metrics.py` |
| Tests | Full suite grew from 113 to 202 passing tests | `tests/` |

No paid model calls were made in Milestone 2. The v2 evaluation is rules-only: 0 tokens, $0.00.

---

## 2. Strict transition validation

Schema version 2 enforces:

- Every added candidate record must be declared by `WRITE` or `SUPERSEDE`.
- Every changed prior record must be declared by `REVISE`, and a `REVISE` must actually change the record.
- Every removed prior record must be declared by `DELETE` or `SUPERSEDE`.
- Every declared operation must correspond to a real state change; a prior record can be targeted once and a candidate record proposed once.
- Operation references are validated against prior and candidate states.
- `DELETE` requires structured authorization — `{source, evidence_id, target_id, rationale}` — with `evidence_id` referencing existing transition evidence and `target_id` matching the operation target. A non-empty `reason` alone is no longer sufficient.

Version-less input is treated as frozen v1 archival data and keeps legacy semantics so previously committed evaluation artifacts remain reproducible; new/current transitions use v2 and are rejected when a state change is undeclared. This is a documented breaking change for callers that construct transitions directly.

**Test evidence:** `tests/test_mvp.py` covers unauthorized deletion (missing/mismatched/bad-evidence authorization), undeclared insertion/modification/removal, no-op `REVISE`, schema-version rejection, and legacy v1 behavior. The v2 dataset has 10 intentionally invalid cases, all rejected before verification.

---

## 3. Structural graph verification

`verify(transition, "graph")` now runs `verify_graph`, which consumes the canonical graph and makes decisions from adjacency — not from claim sets:

1. **Provenance paths** — candidate claims not present in prior memory need a `SUPPORTS` edge from an evidence node connected to the transition.
2. **Required-claim reachability** — every `required_claims` item must be attached via `REQUIRES` and be contained by a candidate record.
3. **Preservation/removal** — before-records must either remain claim-supersets or be reached by `REVISES`/`DELETES`/`SUPERSEDES` operation edges; changed records need evidence-backed additions reachable through provenance edges. Removals without an operation edge are flagged.
4. **Contradiction relationships** — `CONTRADICTS` edges from connected evidence to claims present in the candidate state cause a faithfulness failure.
5. **Connectivity/orphans** — claim nodes unreachable from the transition node are rejected.
6. **Structural consistency** — dangling edges, duplicate nodes, missing/extra nodes, unattached required claims, and operation/authorization edge mismatches are rejected with `GRAPH_STRUCTURE_INVALID`.

The exact-claim-set verifier remains `verify(transition, "text")`, unchanged and independent. Adversarial tests inject malformed graphs to prove the structural checks fire; a contradiction fixture shows graph HOLD where the text baseline ACCEPTs, and a disconnect fixture shows graph HOLD where the intact graph ACCEPTs.

**Caveat:** the graph verifier sees `evidence.contradicts` metadata that the text baseline intentionally ignores. This makes the two verifiers non-equivalent by design; it is the mechanism under test, not a free advantage.

---

## 4. Semantic verification

`membud.semantic` is a separate, opt-in mechanism:

- `SemanticRelation`: `EQUIVALENT` / `CONTRADICTORY` / `UNRELATED` / `UNCERTAIN`.
- `SemanticComparator` protocol; `LLMSemanticComparator` over the existing OpenAI-compatible client with strict JSON validation.
- Any transport, malformed-response, or validator failure becomes `UNCERTAIN`; `compare` never raises.
- `verify_with_semantics` keeps the deterministic text result verbatim and appends one `semantic` check. ACCEPT requires all checks PASS, so `UNCERTAIN` yields HOLD.
- `store.commit` accepts the optional `semantic` check only when the three deterministic checks are present and all pass.

Deterministic verification is unchanged when semantics are not requested.

---

## 5. Storage and reliability

- Store format: `{"schema_version": 2, "version": N, "records": [...]}`; legacy bare-list stores load as v1 and migrate on the next ACCEPT.
- `version` increments exactly once per ACCEPT; HOLD never creates, rewrites, or reformats the store.
- Optional `expected_version` gives optimistic concurrency control; conflicts fail closed with an audit error and leave state untouched.
- A POSIX `flock` on `<store>.lock` serializes load → verify → write → audit; lock acquisition degrades to a no-op where unavailable and is recorded in audit events.
- Writes remain atomic (temp file + fsync + rename); stray atomic-write temp files are removed and never read as state.
- Audit events record store schema/version before/after, lock status, and expected version.
- Existing guarantees — store/`before` reconciliation, duplicate-ID rejection, audit rollback, HOLD byte-identity — are preserved and tested.

---

## 6. Backend integration

A reference host backend (in-memory and SQLite) plus `GuardedMemory` demonstrates actual interception: prior snapshot → canonical transition construction (invalid updates rejected before any write) → graph verification with audit → persist exactly the candidate only on ACCEPT, with staleness re-check and rollback on apply failure. HOLD/invalid input leaves backend records and SQLite bytes identical; every outcome emits a decision record.

This is a **reference backend**, not a third-party integration. Source inspection of `mem0ai 2.2.1` (wheel downloaded, not installed) found sequential writes to multiple stores with no public pre-commit hook; a safe integration would require a host-owned staging/fork. No compatibility is claimed.

---

## 7. Evaluation

**Dataset:** 36 synthetic hash-pinned cases, 18 dev / 18 held-out, balanced across categories: valid writes/revises/supersedes/NOOPs/authorized deletions/multi-op updates, omission, corruption with absent and insufficient evidence, unsupported additions, unsupported provenance, unresolved contradictions, and 10 expected-invalid transitions. Labels are single-reviewed; `data/review/` contains a blank second-review sheet and adjudication template. No independence is fabricated.

**Run:** `scripts/run_evaluation_v2.py`, rules-only, reproduced independently by the Lead.

| Condition | ACCEPT | HOLD | INVALID_INPUT | Errors |
|---|---:|---:|---:|---:|
| graph | 14 | 12 | 10 | 0 |
| text | 16 | 10 | 10 | 0 |
| unguarded | 26 APPLIED | — | 10 | 0 |

- Gold HOLD-only view: both conditions detect 12/12, 0 false accepts, 14/14 valid updates accepted, 0 false holds.
- Combined faulty view (invalid transitions counted as correct rejection): graph precision/recall 22/22, text precision 20/20 and recall 20/22.
- **Paired differences:** only 2/36 decisions differ — DEV-13 and HELD-13, both designed unresolved-contradiction cases where graph returns HOLD and text returns ACCEPT (the text baseline does not consume `evidence.contradicts`).
- Dev and held-out results are identical in structure (graph 7/6/5, text 8/5/5 per split).
- Latency on 36 tiny fixtures is sub-millisecond and noise-dominated: graph preparation ≈0.043 ms, verification ≈0.111 ms, end-to-end ≈0.250 ms; text ≈0.058/0.100/0.293 ms. This is not a meaningful performance measurement.

**Interpretation.** The harness can measure graph-vs-text differences on equivalent inputs. The measured difference is exactly the designed feature difference on two constructed cases. It is not evidence that graph verification is generally superior: the dataset is synthetic and single-reviewed, the cases were authored alongside the verifier, the text baseline was intentionally not extended to consume contradiction metadata, and n=36 has no statistical power for a superiority claim.

---

## 8. Limitations

- Single-reviewer synthetic labels; second human review pending (process prepared, not collected).
- Graph verifier advantage is demonstrated only on constructed contradiction cases where the text baseline lacks the corresponding mechanism; a fair comparison would also give text access to contradiction annotations.
- No powered evaluation, no confidence intervals, no downstream QA utility, and no billed-cost measurement.
- Semantic verification is implemented and unit-tested with fakes; it has not been evaluated with a live model in this milestone (no spend approval requested).
- Reference backend is not a third-party integration; Mem0/A-Mem still require host-owned staging.
- Latency/overhead measurements are timer noise at fixture scale.

## 9. Remaining work

1. Independent second human review + adjudication of v2 labels; then a larger powered held-out evaluation.
2. Optional semantic/LLM conditions with bounded spend, calibration/abstention, and billed-cost accounting.
3. Checkpoint/resume and longer-running experiment support.
4. Real backend integration through a host-owned staged-write/fork.
5. Stable versioned interchange format with cross-release migration tests.
6. Extend the text baseline with contradiction metadata for a controlled information-parity comparison of the two verifiers.

## 10. Verification

- Full test suite: 202 passing (`TMPDIR=$PWD/.tmp-... PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider`).
- v2 evaluation reproduced independently by the Lead with identical decision counts and paired differences.
- `python -m membud.demo` runs the 11-case demo through both guarded paths.
- No paid API calls were made in this milestone.


---

## 11. Fairness follow-up addendum (2026-11-07)

A post-milestone fairness audit tested whether the two graph-vs-text decision differences were structural or informational. A `text_fair` control was added that consumes the same `evidence.contradicts` annotations as the graph verifier, holding information constant.

Results on the 36 frozen v2 cases (no paid calls): plain text 16 ACCEPT / 10 HOLD / 10 INVALID with 2 false accepts (DEV-13, HELD-13); fair text and graph both 14 / 12 / 10 with 0 false accepts and 36/36 label agreement. `text_fair_vs_graph` has zero paired differences; `text_vs_text_fair` has exactly the two contradiction cases.

**Interpretation:** on this manifest, the visible graph advantage is fully explained by annotation access, not by structural verification. The milestone's earlier framing that graph "catches contradictions text cannot" must be read as "graph consumes contradiction annotations text did not consume". Future graph-vs-text claims require relational structure a fair text verifier cannot reconstruct from the same facts, tested against an information-matched control. Full write-up: `docs/fairness-audit.md`; artifact: `data/results/fairness-audit-v2/audit.json`; implementation: `src/membud/fairness.py`, `scripts/run_fairness_audit.py`.


---

## 12. Held-out LLM and semantic addendum (paid, capped)

A one-shot DeepSeek `deepseek-flash` run on the 18-case held-out split (13 valid/invalid-testable + 5 expected-invalid) compared three conditions: LLM verification on graph serialization, LLM verification on text serialization, and the deterministic text baseline plus the separate LLM semantic check.

| Condition | ACCEPT | HOLD | INVALID | False accepts | False holds | Valid accepted |
|---|---:|---:|---:|---:|---:|---:|
| graph (LLM) | 3 | 10 | 5 | 0/6 | 4/7 | 3/7 |
| text (LLM) | 6 | 7 | 5 | 0/6 | 1/7 | 6/7 |
| semantic (text + semantic check) | 8 | 5 | 5 | 1/6 (HELD-13) | 0/7 | 7/7 |

101,231 reported tokens, 59 attempts, 0 verifier errors, no budget abort; estimated upper bound ≈$0.40 at the top published per-token rate (billing unavailable). The LLM graph condition was markedly over-conservative; the semantic condition accepted HELD-13 because `verify_with_semantics` does not consume `evidence.contradicts` — the same annotation-access gap documented in §11. The held-out set is too small (7 valid, 6 faulty) and single-reviewed for any superiority claim; this run establishes that the harness can execute equivalent-information LLM/semantic comparisons under a hard spend cap. Details: `docs/llm-heldout.md`.


---

## 13. Post-milestone software follow-up addendum (2026-11-07)

- **Semantic annotation gap closed.** `membud.semantic` now consumes `evidence.contradicts` deterministically: a candidate-state claim listed in an evidence contradiction makes the appended `semantic` check FAIL with `CONTRADICTED_CLAIM`, before and independent of comparator calls; the LLM comparator prompt/version is unchanged so response-cache keys stay valid.
- **Cache-only held-out replay.** `data/results/llm-heldout-v2-rerun/` replays all three held-out conditions with `--require-cache`: 0 provider attempts, 0 new tokens, 51 cache hits, 0 misses. Graph/text decisions are unchanged; semantic `HELD-13` flips ACCEPT -> HOLD, so semantic false accepts go 1/6 -> 0/6 and faulty detection 5/6 -> 6/6. This is a replay plus a deterministic fix, not a new paid run.
- **Graph-LLM false-hold attribution.** A cache-only analysis attributes all 52 cached completions 1:1 to the run; the four graph false holds fail preservation with model reasons citing null authorization / missing authorizing edges on evidence-backed `REVISE`/`SUPERSEDE` (3/4 graph-only, 1/4 shared with text). Serialization length/token asymmetry is not supported as a cause, and 7/52 calls retried once. See `docs/graph-llm-false-holds.md`; the proposed G1/G2 ablation needs explicit spend approval.
- **Phase 0 productization.** A stdlib localhost `/verify` sidecar (`membud.service`) and a no-build opencode `tool.execute.before` guard plugin (`integrations/opencode/`) demonstrate fail-closed tool-call denial against a mock memory tool, with 39 sidecar tests, a node/bun harness, a real opencode 1.19.5 module-load smoke under an offline network namespace, and measured loopback latency (hook p50 ≈2-3 ms, p95 ≈3.7-5.4 ms on tiny fixtures). A follow-up opt-in live test with a real model (`deepseek/deepseek-flash`) confirmed the full path: a HOLD was denied before tool execution with byte-identical host state and surfaced to the model, while an ACCEPT executed once and persisted exactly the candidate (≈$0.0005 for the run; `integrations/opencode/live/EVIDENCE.md`). Not production software; one model/provider/opencode version and two scripted prompts only.
- **Mem0 groundwork.** `membud.adapters.mem0_bridge` implements a host-owned staged-proposal path with ACCEPT-only reference-backend persistence and byte-identical HOLD tests; `mem0ai==2.2.1` still has no safe public pre-commit hook, so no compatibility is claimed (`data/mem0-bridge.md`).
- **Verification.** Full test suite: 385 passing. The opt-in live opencode test made the only paid provider calls in these follow-ups (one real model; ≈$0.0005 for the packaged run and under $0.01 including development iterations); no other paid calls were made.
