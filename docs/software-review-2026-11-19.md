# MemBud software review — 2026-11-19

**Review target:** repository `/home/ifan/Projects/MemBud`, branch `main`, HEAD `5023c41`
("Document the format/runner/statistics infrastructure round"), working tree clean except the
pre-existing untracked `paper/` directory. Python 3.14 (`.venv`), stdlib-only runtime.

> **Status (2026-11-19):** the P0/P1/P2 remediation from §9 was applied after this review; see
> §10 for what changed and how it was verified. Sections 1–9 are the reviewed snapshot.

**Artifact sizes:** 7,145 lines in `src/membud/` (23 modules), 10,207 lines of scripts and
integration code, 10,552 lines of tests (22 files).

**Scope:** the existing software only — library, store, adapters, sidecar, evaluation/statistics
tooling, tests, and the documentation claims about them. Not reviewed: the `paper/` drafts, the
external literature folder, and any third-party backend (none is integrated).

**Out of scope / not done:** no network access, no paid model calls, no source edits, no
third-party security audit, no licensed-data handling beyond a secret/PII check.

---

## 1. Method

- Four adversarial reviewers worked in parallel with disjoint scopes and write scopes:
  core verification semantics (schemas/graph/verifiers/semantic/migrations); persistence,
  adapters and `/verify` sidecar; evaluation/metrics/statistics/cost/LLM client; and
  tests/docs-claim consistency plus the opencode integration.
- Each reviewer wrote a raw report (`.tmp-review/part-a/b/c/d.md`, gitignored) and reported
  findings with severity, `file:line`, a code path or reproduction, impact and recommendation.
- The Lead independently **reproduced every Blocker/Major finding** (no Blockers were found),
  re-ran the full suite, and ran cross-cutting checks: demo, experiment runner, the `/verify`
  sidecar over real HTTP, deterministic regeneration of committed artifacts, manifest/hash pins,
  token arithmetic, and dependency/secret hygiene.
- The four raw reports are not part of this deliverable; every Major below was re-verified by the
  Lead. Minor findings without a "Lead-reproduced" note are reviewer-reproduced and code-reviewed
  by the Lead but not necessarily re-run end to end.

**Baseline (re-verified by the Lead):**

| Check | Result |
|---|---|
| Full test suite at HEAD | **542 passed in ~7 s**, 0 skipped |
| `python -m membud.demo` | 11 cases through graph and text paths; audit written |
| `python -m membud.experiment` (dev fixtures) | 33 condition runs, 0 execution errors |
| `scripts/run_evaluation_v2.py` regenerated into scratch | 108/108 decision rows identical to committed artifact; only latency/hash fields differ; graph 14/12/10, text 16/10/10, two diffs DEV-13/HELD-13 |
| Manifest/case/label SHA-256 pins | all match (`evaluation-v1` and `evaluation-v2`) |
| HOLD byte-identical probe (ACCEPT then HOLD) | store bytes identical, version stays 1, one audit line per run, no temp files |
| `/verify` sidecar over loopback | ACCEPT and HOLD decisions correct; malformed JSON → 400 HOLD; oversized body → 413; GET `/verify` → 405 |
| Dependency surface | stdlib only; no third-party runtime dependency |
| Secrets / licensed data | no credentials in tracked files or history; `.env` gitignored; no licensed benchmark data found |
| Committed artifact reproducibility | rules run decisions identical; fairness audit byte-identical (reviewer); statistical report byte-identical via `--check` (reviewer); LLM token/attempt/cache counts match README |

## 2. Verdict

**No Blocker. The core safety path is sound; the defects cluster in error/durability paths,
one public-API trust boundary, and documentation/provenance claims.**

What genuinely holds: strict schema-v2 operation invariants; the commit gate (ACCEPT requires
three passing, consistent checks); HOLD never writes; atomic ACCEPT-only store writes with
`flock` serialization; append-only audit; byte-identical HOLD for store and reference adapters;
loopback-only sidecar with size/depth caps that fails closed; hash-pinned data and artifacts; and
an evaluation/statistics chain whose headline numbers reproduce.

The 8 Major findings are: three durability/recovery defects (reference adapter shadow store,
checkpoint/resume crash window, store-write/audit ordering); one schema trust-boundary defect
(legacy v1 default + `schema_version` type confusion); one latent public-API trust hazard
(`prepared_input` graph); and three documentation/provenance defects (plain-text detection number,
dev-split artifact label, and a documented analysis command that no longer reproduces). The
reviewer-reported failure-path accounting/provenance defects (phantom token charge, audit "after"
versions, rules-only cost basis on a failed LLM run) are ranked Minor: they do not affect completed
committed runs, and the phantom charge is bound to the M2 crash path and is fixed with it. **No
fabricated headline result was found**; the committed evaluation chain reproduces. **M1–M3 and M8
should be resolved before the next paid model run or before the docs are used as a source of
claims.**

---

## 3. Major findings

### M1. Reference adapter advances the shadow baseline before the host apply; a stale/apply rejection permanently wedges the guarded path
*Lead-reproduced. Files: `src/membud/adapters/reference.py:435-444`, `:476-489`, `:491-524`;
`src/membud/store.py:308-320`.*

**What happens.** `GuardedMemory.propose_update` calls `run_case`, which verifies **and persists the
shadow baseline** and appends an audit `ACCEPT`. Only afterwards does it re-check the host prior
state (`:476`) and apply the candidate (`:492`). A stale host therefore produces
`decision=REJECTED, applied=False` in the decision log, while the shadow store has already
advanced to the candidate and the run audit records `ACCEPT`.

**Lead reproduction** (with the project's own `RacingBackend` pattern): first proposal →
`REJECTED StalePriorState`, host unchanged at the raced state, shadow store `version: 1` =
candidate, run audit last event `ACCEPT`, decision log last event `REJECTED`; a second proposal
from the live host then always fails with
`ValueError: persisted store does not match transition.before` — there is no resync API, so the
guarded path is wedged until manual file deletion.

**Impact.** The reference integration is not crash/stale-recoverable, and its two logs contradict
each other. `data/adapter-contract.md:114-122` discloses only the crash variant and says the
baseline does "not persist" on rejection. The normal path (ACCEPT/HOLD, byte-identical HOLD) is
unaffected.

**Fix.** Verify first and persist the baseline only after `replace_state` plus post-apply
verification succeed, or roll the shadow store back on any post-verification `REJECTED`; add a
`resync` helper; add a test asserting the shadow store is not advanced on `StalePriorState`.

### M2. Checkpoint/resume crash window silently converts a completed ACCEPT row into `ERROR`, with a phantom token charge
*Reviewer-reproduced with the tests' `FakeClient`; mechanism confirmed by the Lead in code.
Files: `scripts/run_llm_heldout.py:455-466`, `:477-491`, `:960-990`; `src/membud/store.py:308-343,374-382`.*

**What happens.** `store.run_case` writes the per-row store and audit **before** the runner appends
the checkpoint row. If the process dies in that window, `--resume` sees no checkpoint row and
re-runs the row with `expected_version=0`; `run_case` then fails closed with
`state version conflict: expected 0, found on-disk version 1`. The runner catches that as
`status="error", decision="ERROR"`, appends the error row to the checkpoint and marks the run
complete — the paid ACCEPT decision is lost permanently.

The failure path feeds `usage={}` into `account_usage`, which falls back to
`2500 tokens × max_attempts` even when no provider call was made: the repro records
`accounted_tokens=7500`, `attempts=3`, `usage_estimated=True`, `checkpoint_is_complete=True`,
`budget.aborted=False`, with `client.calls == 0`.

**Impact.** Exactly the interruption mode checkpoint/resume claims to handle can corrupt a paid
row and the budget ledger; phantom charges can also abort later legitimate rows.

**Fix.** On resume, reconcile an existing store/audit for the condition+case before re-running
(recover the decision from the last audit event; treat the version conflict as already-applied),
or durably record intent before the store commit. Charge the fallback only when at least one
provider attempt actually occurred; record `attempts=0, accounted_tokens=0` for pre-request
failures. Add crash-window tests.

### M3. Store writes the state before appending the audit event; the README claims the opposite, and a crash in the gap leaves an unaudited ACCEPT
*Lead-confirmed in code and docs; reviewer reproduced the crash with a subprocess.
Files: `src/membud/store.py:374-380` (write then `_append_event`), `:288-290` (docstring states the
real order), `README.md:92`.*

**What happens.** On ACCEPT the store is atomically written and then the audit event is appended;
a caught append failure triggers a best-effort rollback (`_restore_store`). A process death between
`os.replace` and the append is not caught, so accepted state can be durable with no audit record.
`README.md:92` states writes "only happen after a successful audit append" — the inverse of what
the code does. `docs/technical-report-milestone-2.md:81` also says the audit rollback is "tested";
no such test exists.

**Impact.** A safety-relevant claim is wrong and the audit log is not write-ahead; forensics cannot
assume every persisted ACCEPT has an audit entry.

**Fix.** Either correct the README/technical report and explicitly document the crash window, or
implement a durable intent event before the write (with load-time reconciliation), and add a
failure-injection test for append failure and process death.

### M4. Legacy v1 input and `schema_version` type confusion bypass the strict v2 invariants and can be committed
*Lead-reproduced. Files: `src/membud/schemas.py:127-129`, `:164-166`, `:254-255`;
`src/membud/verification.py:33-55`; `src/membud/store.py:238-261`; `README.md:109`;
`docs/format-policy.md:28-31`.*

**What happens.** `Transition.from_dict` defaults a missing `schema_version` to **1**, and
`validate()`/`verify()`/`commit()`/`run_case()` never require v2. A version-less transition with an
evidence-supported undeclared insertion is ACCEPTed and written:
`verify(t,"graph") → ACCEPT`, `run_case → ACCEPT`, store contains the fabricated record; the same
payload with `schema_version: 2` raises `undeclared insertion of candidate record r2`.

Type confusion compounds it: `schema_version: true` and `1.0` satisfy `value not in {1,2}`
(`True == 1`, `1.0 == 1`) and load as legacy v1 (verify → ACCEPT); `2.0` loads as v2 and carries a
float. This violates `docs/format-policy.md:31` ("any other version → rejected") and
`README.md:109` says newly written transitions "must declare or default to version 2", which the
code contradicts.

**Impact.** Library/CLI/experiment callers (adapters and the HTTP sidecar hard-code v2 and are
safe) can commit undeclared state changes under the weaker legacy semantics. The strict
operation-coverage milestone is opt-in at the library boundary.

**Fix.** Enforce the current transition version on the commit path
(`require_current_schema=True` or require explicit `migrate_transition_to_v2()` before
verify/commit), or make omission default to v2 and require an explicit archival flag for v1; type-check
`type(v) is int`; add `true`/`1.0`/`2.0` and v1-commit tests.

### M5. `verify(..., prepared_input=<graph>)` trusts caller-supplied relationship edges; forged graphs turn HOLD into ACCEPT
*Lead-reproduced. Files: `src/membud/verification.py:47-51`; `src/membud/graph_verification.py:55-58`, `:74-98`, `:140-160`, `:223-224`.*

**What happens.** The deterministic graph verifier consumes a supplied graph as its facts and only
partially validates edges. Reproductions:
- canonical contradiction case → `HOLD` (`CONTRADICTED_CLAIM`); delete only the `CONTRADICTS` edge
  from the supplied graph → **ACCEPT**;
- coverage case with an omitted required claim → `HOLD` (`REQUIRED_CLAIM_OMITTED`); add one
  fabricated `CONTAINS` edge `record:candidate:r2 → claim:Z` → **ACCEPT**.

`store.run_case` builds the canonical graph itself, so the commit path is not affected; but
`verify()` is exported through `membud.core`, and `prepared_input` is undocumented. This is a
latent soundness hole for any caller that forwards or caches a graph.

**Fix.** Make the override private/test-only, or assert the supplied graph equals
`build_graph(t)` (and validate all canonical edge sets); add tests that tamper with
`CONTRADICTS`/`CONTAINS`/`SUPPORTS`/`HAS_EVIDENCE` and expect HOLD.

### M6. Documentation claims plain text detects 12/12 gold-HOLD cases with 0 false accepts; the artifact says 10/12 with 2 false accepts
*Lead-verified against `data/results/evaluation-v2/metrics.json` and
`data/results/fairness-audit-v2/audit.json`. Files: `README.md:23`;
`docs/technical-report-milestone-2.md:105`; `data/pilot-report.md:61`.*

`metrics.json` → text `as_completed.faulty`: completed 12, flagged 10, `false_accepts: 2`
(DEV-13, HELD-13); graph is the only 12/12/0 condition, and fair text is also 12/12/0. The same
documents contradict the sentence two lines later (`README.md:25`,
`technical-report:147`) and `docs/statistical-analysis.md:45` and `docs/fairness-audit.md` are
correct. `paper/results-tables.md:390` already records this exact discrepancy (the paper's
fact-check found it), but the repository docs are unfixed.

**Impact.** A quoted headline number overstates the plain-text baseline and understates its false
accepts. **Fix:** write "graph and fair text 12/12 with 0 false accepts; plain text 10/12 with 2
false accepts (DEV-13, HELD-13)" in all three files; add a test asserting README prose numbers
against the committed metrics JSON.

### M7. The dev-replication artifact is mislabeled `split: "heldout"`
*Lead-verified. File: `data/results/graph-false-hold-ablation-dev/summary.json:8,:27`;
root cause `scripts/run_graph_false_hold_ablation.py:117-121`, `:479`.*

The summary says `"split": "heldout"` and the comparison policy says "four … held-out cases",
while its 13 cases are `DEV-01`…`DEV-13`. `split` and the policy string are hard-coded in the
script regardless of `--cases`. This artifact is the "out-of-sample dev replication" cited in
`docs/graph-llm-false-holds.md:359-370` as the check that justified promoting
`membud-transition-checks-v2`.

**Impact.** The data are genuinely dev cases, so the underlying claim still holds via the explicit
case list, but the artifact metadata is wrong: any consumer filtering `split == "heldout"` would
ingest dev cases into a held-out analysis (double-dipping). **Fix:** derive the split from the
cases/manifest (or an explicit `--split`), regenerate the summary, and note the correction in the
doc.

### M8. The documented "cache-only" analysis no longer reproduces on a normal workspace
*Lead-reproduced. File: `scripts/analyze_llm_false_holds.py` (docstring run command);
committed artifact `data/results/graph-llm-false-hold-analysis/analysis.json`; doc
`docs/graph-llm-false-holds.md`.*

Running the documented command at HEAD exits **3**:
`no_unattributed_cache_rows: 51 cache rows not attributable to held-out calls`
(`loaded=103 expected_keys=52 matched_verifier=26/26 matched_semantic=26/26 full_coverage=False`).
The committed artifact records `coverage.all_assertions_passed: true`. The cause is cache growth:
later prompt-v2 runs append rows to the same
`.cache/membud/deepseek-cache.sqlite3`, so the "52 cache keys" coverage assertion no longer holds
on a used workspace. Historical conclusions are unaffected.

**Impact.** A documentation claim of cache-only reproducibility fails for anyone running the
command as written. **Fix:** pin/archive the exact cache snapshot for the analysis (or filter to the
run's expected keys and report extra rows as informational), and update the doc caveat.

---

## 4. Minor findings

*Reviewer-reproduced unless marked Lead. No decision-level impact found.*

| # | Finding | Location |
|---|---|---|
| m1 | `metrics.py` reports `estimated_cost_usd: 0.0` / `basis: rules_only_no_paid_calls` whenever a condition has zero completed responses, even for an LLM verifier with all rows errored; contradicts the protocol's "paid runs report null + unknown spend" rule (latent; requires committing a failed-run summary). | `src/membud/metrics.py:61-94` |
| m2 | LLM harness false-hold / valid-acceptance rates are neither as-completed nor fail-closed ITT for hard error rows (no error-hold count; rates do not complement). | `scripts/run_llm_heldout.py:163`, `:582-624`, `:694-697`; `data/experiment-protocol.md:42-54` |
| m3 | Cache payload that is valid JSON but missing completion keys raises `KeyError`, bypassing `CacheMissError` and the `--require-cache` loud-abort path; becomes a silent verifier_error/HOLD. | `src/membud/llm/client.py:186-206` |
| m4 | JSON-repair exhaustion discards accumulated real usage/attempts, so the runner substitutes a `2500 × max_attempts` estimate. | `src/membud/llm/client.py:219-237`; `scripts/run_llm_heldout.py:368-373` |
| m5 | `code_revision` fingerprint hashes only `src/membud/**/*.py` + `pyproject.toml`; edits to `scripts/run_llm_heldout.py` (accounting/budget/metrics) do not invalidate `--resume`. | `src/membud/experiment.py:22-30`; `scripts/run_llm_heldout.py:881,891-907` |
| m6 | `validate()` is incomplete for directly constructed transitions: empty `DeletionAuthorization.source`/`rationale` passes and verifies ACCEPT, while `from_dict` rejects it; non-string claims raise `AttributeError`. **Lead-reproduced.** | `src/membud/schemas.py:164-283` |
| m7 | Nested unknown/misspelled keys are silently ignored (`evidence.contradict`, `required_claim`); a typo drops a contradiction and the transition ACCEPTs. HTTP allow-list is top-level only. **Lead-reproduced.** | `src/membud/schemas.py:38-40,51-59,91-107,120-162`; `src/membud/service.py:258-282` |
| m8 | Evidence is caller-self-asserted; no authenticity/provenance binding. The opencode guard copies `evidence` straight from model tool args, so ACCEPT means "consistent with the caller's evidence"; the trust boundary is undocumented. | `src/membud/schemas.py:44-59`; `integrations/opencode/plugin/membud-verify-guard.js:216-227` |
| m9 | Audit error events report fabricated `store_version_after`/`schema_version_after` for writes that failed or were rolled back; no `store_written`/`rolled_back` flag. **Lead-reproduced** (injected ENOSPC and append failure → error event claims version 1, store absent). | `src/membud/store.py:338-343`, `:384-409` |
| m10 | `apply_staged` divergence check runs after the host was mutated, and transition equality is order-sensitive while validation is set-based → content-preserving reorder yields `Mem0BridgeError` after apply + ACCEPT record. | `src/membud/adapters/mem0_bridge.py:749-762`; `src/membud/adapters/base.py:130-133` |
| m11 | Check-then-act TOCTOU between the post-verification re-snapshot and `replace_state`; two concurrent guards can both report `ACCEPT/applied=True` and one accepted write is silently lost; unconditional restore can clobber a newer write. Documented as future work in `TODO.md`, but the adapter-contract doc says the window is "closed". | `src/membud/adapters/reference.py:475-492`, `:540-546`; `data/adapter-contract.md:116-120` |
| m12 | Post-`run_case` exceptions escape `propose_update` (e.g. snapshot `OSError` after verification, decision-log append failure after ACCEPT): host may be mutated and no decision record written; contradicts the technical report's "every outcome emits a decision record" (the adapter-contract doc discloses only the crash variant, not the exception variant). | `src/membud/adapters/reference.py:476-489`, `:509`, `:526-588`; `docs/technical-report-milestone-2.md:87`; `data/adapter-contract.md:120-122` |
| m13 | One `GuardedMemory` store is global, not per-scope; a multi-scope host can only ever persist the first scope, and later scopes fail closed. Undocumented. | `src/membud/adapters/reference.py:435-444`; `src/membud/store.py:320` |
| m14 | Unsupported HTTP methods and protocol-layer errors bypass the documented JSON HOLD envelope (`PUT /verify` → 501 HTML; bad request line → 400 HTML; `HTTP/9.9` → 505; oversized headers → 431). Fail-closed, but the docstring's "every malformed/unsupported request produces JSON HOLD" is false. | `src/membud/service.py:15-16`, `:469-473` |
| m15 | Audit/decision logs (and the reference SQLite DB) are created `0644` while the store is `0600`; memory text in PII-bearing logs is world-readable under umask 022. | `src/membud/store.py:188-193`; `src/membud/adapters/reference.py:139-140,582-588` |
| m16 | Lock degradation is fail-open (two writers without `fcntl` can both ACCEPT and overwrite); the README omits the caveat. The `flock` is also held across a caller-supplied verifier (possibly an LLM call) with no timeout/stale-lock handling. | `src/membud/store.py:196-219`; `README.md:92`; `src/membud/experiment.py:311` |
| m17 | "Rejects non-loopback peers" is untested (only bind-address rejection is tested). | `integrations/opencode/README.md:335-339`; `src/membud/service.py:480`; `tests/test_service.py:597` |
| m18 | The opencode module-load smoke has no retained artifact; `probe.log` is absent, and the wrapper claim relies on string inspection of the installed 1.19.5 binary. Wrapper order itself was independently confirmed. | `integrations/opencode/README.md`; untracked `.tmp-plugin` scratch |
| m19 | Committed artifacts embed absolute `/home/ifan/...` paths (`run-config.json`, LLM `summary.json`), and the placeholder price date defaults to the run day, so cost blocks are not byte-reproducible without `--price-date`. | `data/results/evaluation-v2/run-config.json`; `src/membud/cost.py:150-151,282` |
| m20 | Paid-call totals in the docs do not reconcile with artifacts: README says 46 paid calls and the false-hold doc says 20 (8 probe + 12 paired), while artifacts record 58 provider attempts and 51 called rows (8 + 19 + 16 + 15; 8 + 17 + 13 + 13). The 141,657-token total does reconcile exactly (24,829 + 51,657 + 40,906 + 24,265). Define the counting rule. | `README.md:36`; `docs/graph-llm-false-holds.md:344` |
| m21 | README/technical-report latency claim "p50 ≈2–3 ms / p95 ≈3.7–5.4 ms" has no committed measurement; the committed table is Node p50 2.025 / p95 3.669 and bun p50 1.433 / p95 2.006, and 5.4 ms appears nowhere. A reviewer re-run measured p50 3.97 / p95 7.01 ms. | `README.md:37`; `docs/technical-report-milestone-2.md:174`; `integrations/opencode/README.md:229-244` |
| m22 | The live-opencode script's docstring still says ≈8.6–9k input tokens / $0.0017 while the committed evidence records 1,042 input / 435 output / $0.00053286; both evidence state SHA-256s were re-verified, so only the prose is stale. | `scripts/run_live_opencode_denial.py:23-24,551`; `integrations/opencode/live/EVIDENCE.md` |
| m23 | `tests/test_semantic.py:700-707` wraps the documented 4-check `store.commit` passthrough in `try/except → pytest.skip`; currently dead, but a regression in that guarantee would show as a skip rather than a failure. | `tests/test_semantic.py:700-707` |
| m24 | `mem0-bridge.md` says "13 cases" for the invalid-proposal rejection test; the test is parameterized over 15. | `data/mem0-bridge.md:93`; `tests/test_mem0_bridge.py` |

## 5. Nits

- `verify()`'s text path silently ignores a supplied `prepared_input` (`_ = textual_representation(t)`
  dead code) while the graph path consumes it (`src/membud/verification.py:49-55`).
- The transition legacy version is derived from the store legacy constant
  (`src/membud/migrations.py:29`); the two formats evolve independently.
- Duplicated contradiction helper in `semantic.py:67-83` and `fairness.py:32-41`, plus the graph
  derivation — three implementations must stay in sync.
- `to_mermaid` escapes only `"`, `\r`, `\n`; a claim containing `]`/`|` can break the diagram
  (`src/membud/graph.py:136-142`).
- `store_path == log_path` corrupts the store; unused `MAX_STRING_LENGTH`; `/healthz` limits are
  incomplete (`src/membud/store.py:295-298`; `src/membud/service.py`).
- `cost.py` can emit placeholder-range provenance for two explicit tables when rates differ
  (`src/membud/cost.py:391-405`, `:435-457`).
- Checkpoint torn-tail repair re-joins with `"\n"` after `splitlines()`, assuming LF-only input
  (`src/membud/checkpoint.py:331-335`).
- `experiment._run_guarded` can attach the previous case's audit event to an error row, and re-reads
  the whole log per row (O(n²) I/O) (`src/membud/experiment.py:338-355`, `:457-458`).
- `pytest>=8` is unpinned and there is no CI configuration; the JS plugin/harness is never run by
  `pytest`; `tests/test_service.py:619` writes into the repo's `.tmp-plugin`.

---

## 6. Claims vs reality (documentation spot-check)

| Claim | Source | Reality |
|---|---|---|
| "writes … only happen after a successful audit append" | `README.md:92` | **False**: store is written first, audit appended after, best-effort rollback on caught failure; crash window leaves an unaudited ACCEPT (M3). |
| "newly written transitions must declare or default to version 2" | `README.md:109` | **False**: `from_dict` defaults a missing version to 1 (M4). |
| "Both conditions detect 12/12 gold faulty (HOLD) cases with 0 false accepts" | `README.md:23`, `docs/technical-report-milestone-2.md:105`, `data/pilot-report.md:61` | **False for plain text**: text 10/12 with 2 false accepts; graph and fair text 12/12/0 (M6). |
| Dev replication was out-of-sample on the dev split | `data/results/graph-false-hold-ablation-dev/summary.json` | Data are dev cases, but `split`/policy metadata says "heldout" (M7). |
| False-hold analysis is reproducible cache-only from the committed command | `scripts/analyze_llm_false_holds.py` docstring; `docs/graph-llm-false-holds.md` | **Fails at HEAD** (exit 3, 103 cache rows, 51 unattributable) (M8). |
| Measured hook latency p50 ≈2–3 ms / p95 ≈3.7–5.4 ms | `README.md:37`, `docs/technical-report-milestone-2.md:174` | Committed table: Node p50 2.025 / p95 3.669; bun p50 1.433 / p95 2.006; 5.4 ms not found (m21). |
| 46 paid calls (README) / 20 paid calls (false-hold doc) | `README.md:36`, `docs/graph-llm-false-holds.md:344` | Artifacts: 58 provider attempts / 51 called rows; 141,657-token total reconciles (m20). |
| "audit rollback … tested" | `docs/technical-report-milestone-2.md:81` | No such test exists; failure-injection tests are absent (M3). |
| "every outcome emits a decision record" | `docs/technical-report-milestone-2.md:87` | Post-`run_case` exceptions can escape without a decision record (m12). |
| "rejects non-loopback peers" | `integrations/opencode/README.md:335-339` | Untested; only the bind-address check is covered (m17). |
| Test counts: 542 passing; 39 sidecar tests; 13 cross-version tests | `README.md:42`; docs | **Verified** exactly. |

---

## 7. Verified good (evidence-backed)

- **Schema-v2 operation invariants:** sound on the `from_dict` path — 5,000-transition fuzz found
  0 accepted transitions with undeclared insertion/modification/removal or non-real operations
  (core reviewer); targeted rejection tests exist.
- **Verifier behavior:** on canonical inputs the graph verifier equals the fair-text control on
  8,000 random transitions (0 differences), consistent with the committed fairness audit; the plain
  text advantage on DEV-13/HELD-13 is explained by `evidence.contradicts` access.
- **Commit gate:** rejects ACCEPT results with missing/duplicate/unknown checks, non-PASS or
  UNCERTAIN checks, inconsistent `passed` flags, and stale prior state; HOLD never writes (Lead
  byte-identity probe: identical store bytes, version unchanged).
- **Persistence primitives:** same-directory temp file + `fsync` + `os.replace`; stray-temp recovery;
  `flock` critical section serializes load → verify → write → audit; legacy store migrates only on
  ACCEPT; unknown versions rejected; `expected_version` conflict leaves the store untouched.
- **Reference adapters:** HOLD leaves host backend bytes identical; ACCEPT persists exactly the
  candidate; SQLite backend not mutated before ACCEPT; Mem0 staged preparation is pure/deterministic.
- **Sidecar:** loopback binding, one request per connection, body/size/claim/depth caps, strict
  envelope, no persistence; every tested bad request returns a fail-closed HOLD.
- **PP/LLM verification:** fail-closed on malformed payloads and provider/transport errors; the
  semantic layer consumes `evidence.contradicts` before any comparator call, so a model verdict
  cannot erase an annotated contradiction.
- **Serialization/migrations:** 3,000-transition round-trip lossless and deterministic; migration
  APIs fail closed on undeclared v1 and unknown integer versions.
- **Checkpoint core:** config-fingerprint refusal before client calls, torn-tail repair, dedupe,
  totals preservation, zero-call replay including `--require-cache`.
- **Cost module:** token-class extraction, provenance enforced, `verified_billed: false` hard-coded,
  double-billing avoided, placeholder arithmetic; no committed artifact misuses it.
- **LLM client:** cache key covers model/base URL/temperature/response format/ZDR/messages (no
  collision path found); bounded retries/backoff; API key never written to configuration/logs; cache
  dir 0700/file 0600; corrupt-JSON cache degrades to a miss.
- **Reproducibility of committed artifacts:** rules decisions reproduce 108/108; fairness audit
  byte-identical; statistical report+docs byte-identical via `--check`; manifest/case/label/state
  hashes verified; LLM token/attempt/cache counts and 141,657-token total reconcile.
- **Hygiene:** stdlib-only runtime, no secrets in history, no licensed benchmark data, `.env`
  gitignored.

## 8. Coverage gaps / not verified

- No failure-injection or crash-point tests for the store write/audit window, checkpoint/resume
  window, or audit rollback (M2/M3 are only reproducible with ad-hoc probes).
- No committed `checkpoint.jsonl` or end-to-end `membud.cost` artifact; those modules are
  unit-tested only.
- No doc-vs-artifact consistency test, so M6/m20/m21-type drift is invisible to the 542-test suite.
- The original DeepSeek pilot (9 verifier errors) cannot be analyzed by
  `scripts/analyze_paired_metrics.py`, which refuses datasets containing execution/verifier errors —
  so the promised fail-closed ITT paired analysis is not available for that artifact.
- The JS plugin/harness is not exercised by pytest, there is no CI workflow, and the module-load
  smoke has no retained log.
- Non-loopback peer rejection is untested; the lock-degradation fail-open path is untested.
- This review is not a security audit: no fuzzing of the HTTP parser, no TLS/auth review beyond
  confirming the documented gaps, no third-party dependency review (there are none).

## 9. Prioritized remediation

**P0 — before the next paid model run (data integrity / accounting):**

1. **M2** reconcile store/audit on `--resume` and charge the token fallback only when at least one
   provider attempt occurred; add crash-window tests.
2. **M1** do not persist the shadow baseline before the host apply succeeds (or roll it back on any
   post-verification `REJECTED`); add a resync path and a stale-race regression test.
3. **M3** correct `README.md:92` and the technical report, and either implement a durable intent
   event or document the unaudited-ACCEPT crash window; add a failure-injection test.

**P1 — before the paper/docs are used as a claims source:**

4. **M6** fix the "12/12" sentences in README, technical report and pilot report (graph 12/12,
   plain text 10/12 with 2 false accepts, fair text 12/12). The paper workspace already records this
   discrepancy in `paper/results-tables.md:390`.
5. **M7** regenerate the dev-ablation summary with the correct split metadata and note the fix.
6. **M8** pin/archive the cache snapshot for the false-hold analysis (or filter expected keys) and
   update the doc; also fix the stale latency and paid-call figures (m20/m21/m22).
7. **M4** enforce schema-v2 on the commit path and strictly type-check `schema_version`; add tests.
8. **M5** lock down `prepared_input` (private/test-only or validate against `build_graph(t)`).

**P2 — hardening/hygiene (no result impact):**

9. Report `null` cost + unknown spend for a zero-response LLM condition (**m1**); set audit "after"
   fields only after a successful write and add write-status fields (**m9**); file modes for PII
   logs (**m15**); sidecar JSON envelope for unsupported methods (**m14**); per-scope store or
   explicit single-scope documentation (**m13**); TOCTOU/resync wording (**m11**); post-verify
   exception wrapping (**m12**); repo-relative artifact paths (**m19**); remove the conditional skip
   (**m23**); add CI + JS harness execution; pin the cache/price provenance and paid-call counting
   rule.

---

## 10. Remediation applied (2026-11-19)

**Status:** the ordered P0 → P1 → P2 plan in §9 was applied to the working tree after this review.
The findings in §3–§6 remain the reviewed snapshot; this section records what changed and how it
was verified. **Full suite after remediation: 573 passing, 0 skipped** (was 542; also verified on Python 3.12); demo, experiment
runner, evaluation-v2 reproduction (all decision rows identical), the documented false-hold analysis
command, and the no-model opencode guard harness all pass.

| Plan item | Change | Verification |
|---|---|---|
| P0-1 (M2) | Resume now recovers a durable store+audit decision when its checkpoint row was lost to a crash (no re-run, no provider call, original usage preserved); a pre-request failure charges `0` tokens/attempts instead of the `2500 × max_attempts` fallback (explicit `api_attempts: 0`). | New tests: `test_resume_recovers_accept_whose_checkpoint_row_was_lost`, `test_pre_request_failure_is_charged_zero_tokens`, `test_account_usage_zero_attempts_charges_nothing`. |
| P0-2 (M1) | The verified host apply now runs inside `run_case`'s flock critical section *before* the shadow baseline commit (verifier callback); stale/apply/post-apply rejection can no longer advance the baseline. Explicit `resync_shadow_store()` recovery for the opposite crash window; rollback never clobbers a foreign write. | New tests: stale race leaves no store and the next proposal succeeds; apply failure/post-apply mismatch leave no store and restore the host; resync recovers a host-ahead baseline; contract docs updated. |
| P0-3 (M3/m9) | Store docs now state the real order (state write → audit append, rollback on caught failure, documented unaudited crash window); events carry `store_written`/`rolled_back`; error events no longer claim an advanced version/schema. Audit-rollback claim is now actually tested. | New failure-injection tests: append failure rolls back (store absent / previous bytes), write ENOSPC reports no write, HOLD reports not-written. |
| P1-4 (M6) | Corrected the "both conditions detect 12/12" sentences in README, technical report and pilot report to graph/fair-text 12/12 and plain text 10/12 with 2 false accepts. | New doc-vs-artifact consistency test (`test_plain_text_detection_prose_matches_committed_metrics`). |
| P1-5 (M7) | Ablation script derives `split`/policy from the manifest cases (no hard-coded "heldout"); committed dev summary corrected to `split: "dev"` with a `metadata_correction` note (measurements untouched). | New split-derivation test; committed metadata verified. |
| P1-6 (M8/m20/m21/m22) | False-hold analysis tolerates extra cache rows from later runs (all 52 expected rows still required; extras informational) so the documented command exits 0; latency, paid-call counts, live-run cost prose and price provenance corrected (58 provider attempts / 51 called rows / 141,657 tokens; counts rule documented). | `scripts/analyze_llm_false_holds.py` exits 0 on the used workspace; docs/artifact arithmetic reconcile; tests added. |
| P1-7 (M4) | `store.run_case`/`store.commit` require current-format transitions unless explicit `allow_legacy=True`; `schema_version` must be a JSON integer (bool/float/string rejected); migrations helpers type-check; README/format-policy updated; v1 archival replay moved to the explicit flag. | New gate/typing tests plus updated archival-replay tests; v1 manifest replay verified with `--allow-legacy`. |
| P1-8 (M5) | `verify(prepared_input=...)` validates the supplied graph against `build_graph(t)` and raises on any mismatch/malformed shape (graph-path only; text path documented as recomputing). | New tampered/malformed graph tests. |
| P2-9 | Metrics report `null`/unknown spend for a declared model verifier with zero completed responses; PII-bearing audit/decision logs, lock file and reference SQLite DB are created `0600`; sidecar returns the structured JSON HOLD envelope for unsupported methods and protocol parse errors; post-verify exception paths and decision-log append failures no longer escape silently (`host_applied` flag); artifact writers use repo-relative paths; the conditional `pytest.skip` was removed; CI runs pytest + the no-model opencode harness; cache/price/counting provenance documented. | New tests for cost basis, file modes, sidecar 405/400/505 JSON, host-applied reporting, decision-log failure; workflow file added; harness validated locally. |

**Independent verification round.** A separate verifier reviewed the remediation diff against HEAD
with real CPython 3.11/3.12/3.13 interpreters and found two CI-blocking compatibility defects plus
one incomplete fix; all three were then fixed and re-verified:
- `store.py` used `Iterable` in a runtime-evaluated annotation without importing it (Python ≤ 3.13
  raised `NameError` on import; 3.14's lazy annotations masked it locally). Fixed by importing it;
  the full suite now passes on Python 3.12 and 3.14.
- `service.py`'s protocol-error JSON path did not emit an HTTP status line on Python ≤ 3.12
  (`request_version` remained `HTTP/0.9`). Fixed by forcing a real response version before sending;
  the raw 400/505 responses are now proper JSON envelopes on 3.12.
- The resume fix recovered only ACCEPT rows; a crash after a durable HOLD audit event re-executed
  the row (a paid call and a duplicated audit event). Recovery now rebuilds non-ACCEPT rows from the
  durable audit event too, and `recovered_without_audit` rows are counted in the attempted/metrics
  views instead of disappearing.

**Acknowledged/not changed:** committed historical artifacts (e.g. `data/results/evaluation-v2/run-config.json`,
older LLM summaries) still contain the absolute paths recorded when they were produced; writers now
emit repo-relative paths for future runs. The committed false-hold `analysis.json` remains the
historical 52-row snapshot; the script now tolerates extra rows from later shared-cache runs without
failing, so the documented command exits 0. The newly added CI workflow was validated by running its
pytest command on Python 3.12/3.14 locally and its no-model harness command on Node 26 locally; the
GitHub-hosted run itself has not been observed.

**Deferred (not part of the ordered plan; still open Minor findings):** LLM-harness as-completed vs
ITT error-row metrics (`m2`), shape-invalid cache payloads (`m3`), JSON-repair usage propagation
(`m4`), runner/analysis files in the code fingerprint (`m5`), `validate()` field-level completeness
for direct construction (`m6`), nested unknown-key rejection (`m7`), explicit evidence trust-boundary
wording (`m8`), mem0 bridge order-sensitive equality (`m10`), lock-degradation README caveat
(`m16`), a non-loopback peer test (`m17`), the missing module-load smoke artifact (`m18`), and the
"13 vs 15 cases" doc count (`m24`). None affects a committed result; they remain candidates for the
next hardening round.

---

*Original review conducted read-only; no source, test, data, or documentation file was modified by
the review itself. Raw per-part reviewer reports are in `.tmp-review/` (gitignored) and are not part
of the repository deliverable. The remediation above was applied afterwards, as a separate change.*
