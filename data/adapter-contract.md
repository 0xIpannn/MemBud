# Memory adapter contract

Status: **reference integration demonstrated; no third-party backend compatibility claimed.**
`membud.adapters` now contains an executable reference host backend (in-memory and SQLite) plus a
`GuardedMemory` interception layer with end-to-end tests in `tests/test_reference_backend.py`. That
proves the contract is implementable; it is not evidence that Mem0, A-Mem, or any other host can be
guarded without changing their code (see `data/backend-feasibility.md`).

## 1. Structural protocol

`membud.adapters.MemoryAdapter` is a structural Python protocol (`runtime_checkable`). An adapter must:

1. Provide a stable `backend_name`.
2. Implement `snapshot(scope)` and convert the relevant host state into canonical
   `Record(id, claims)` values, preserving stable record IDs and claim boundaries where the host
   provides them. Scopes are plain `mapping[str, str]` values; adapters must not leak host-specific
   objects into verification or persistence.
3. Implement `replace_state(scope, records)` with atomic semantics for the supplied scope. It must
   replace the *entire* scoped state (no partial delta) and must be a no-op for records it cannot
   validate. If the backend cannot stage/rollback all effects, it is not safe to call this a guarded
   adapter.
4. Document source provenance, timestamps, relationships, metadata, and host-side effects the mapping
   loses or cannot represent.
5. Avoid calling host mutation methods until MemBud returns ACCEPT. An accepted update must also be
   checked against a fresh snapshot or protected by a backend transaction to avoid stale-state
   commits.

Evidence and proposed operations are part of the canonical `Transition`, not implicit adapter
metadata. Adapters must not invent evidence, authorization, or operation annotations.

Helper functions in `src/membud/adapters/base.py` implement the shared mechanics: `scope_key`,
`canonical_records`, `capture_prior_state`, `prior_state_matches`, `PriorState`, and
`AdapterContractError`. These use only canonical `Record` values and plain dicts.

## 2. Interception contract (normative)

A guarded write path is a function that accepts a proposed update for one scope and has these
properties, in this order:

1. **Explicit prior-state capture.** Snapshot the scoped state before doing anything else and keep
   the immutable snapshot for the decision record and for the stale-state check.
2. **Canonical transition construction.** Normalize the *complete proposed resulting state*, the
   evidence/provenance, the required claims, and the declared operations, then build a canonical
   schema-version-2 `Transition` (through `Transition.from_dict`). Every actual state change must be
   declared: insertion = `WRITE`/`SUPERSEDE`, modification = `REVISE`, removal = `DELETE`/`SUPERSEDE`.
   Undeclared insertion/modification/removal and a `DELETE` without structured
   `DeletionAuthorization` are invalid input and are rejected **before** any verification, audit-store
   write, or host write. Invalid input is a decision (`REJECTED`), not an exception escaping to the
   caller.
3. **Verification, then an ordered ACCEPT-only apply.** Submit the transition to MemBud verification
   on the graph path via `membud.store.run_case` with a verifier callback (see
   `GuardedMemory._verifying_apply`). The callback runs the deterministic checks first; *only* on
   ACCEPT does it re-snapshot the scope, compare it exactly with the captured prior state, and, if
   still current, call `replace_state(scope, transition.candidate)` exactly once and re-snapshot to
   require equality with the accepted candidate. A stale prior state, apply failure, or post-apply
   mismatch raises a fail-closed rejection. Because this happens inside `run_case`'s `flock`
   critical section *before* the shadow baseline is committed, a rejected apply can never advance
   the baseline. The three required checks are `coverage`, `preservation`, and `faithfulness`; any
   non-ACCEPT result means no host mutation.
4. **Baseline commit after the host apply.** The controlled JSON baseline and the run audit event are
   written by `run_case` only after the verifier callback returned ACCEPT (i.e. after the host apply
   succeeded).
5. **Audit/decision record.** Append one JSONL record per proposal (ACCEPT, HOLD, and REJECTED
   including invalid input) containing: run ID, case ID, decision, `applied`, reason, prior state,
   the canonical transition (including evidence source/text and DELETE authorization), the
   verification result, and the `run_case` audit event. HOLD/invalid input must leave host backend
   bytes/records identical to the captured prior state.

Outcome semantics:

- `ACCEPT` — all checks passed, the exact candidate was persisted, post-state verified.
- `HOLD` — verification did not accept; host state untouched; run audit records the result.
- `REJECTED` — invalid input, a failed verification path, a stale prior state, or a failed apply;
  host state untouched (or restored when possible).

## 3. Reference API

```python
from membud.adapters import SQLiteBackend, GuardedMemory

backend = SQLiteBackend(tmp / "memory.sqlite", [{"id": "r1", "claims": ["tinggal di Bandung"]}],
                        scope={"user_id": "u1"})
guard = GuardedMemory(backend,
                      store_path=tmp / "state.json",
                      audit_log_path=tmp / "audit.jsonl")
decision = guard.propose_update(
    {"user_id": "u1"},
    case_id="valid-revise",
    candidate=[{"id": "r1", "claims": ["tinggal di Bandung", "bekerja di Tokopedia"]}],
    operations=[{"type": "REVISE", "record_id": "r1", "target_id": "r1"}],
    evidence=[{"id": "e1", "supports": ["bekerja di Tokopedia"],
               "source": "chat:42", "text": "Ia bekerja di Tokopedia."}],
    required_claims=["tinggal di Bandung"],
)
# decision.decision in {"ACCEPT", "HOLD", "REJECTED"}; decision.applied, decision.reason,
# decision.transition, decision.result, decision.audit_event, decision.prior, decision.run_id
print(backend.snapshot({"user_id": "u1"}))  # updated only on ACCEPT
```

`DELETE` operations accept either a nested `authorization` object or flat convenience keys:

```python
operations=[{
    "type": "DELETE", "target_id": "r1",
    "reason": "user asked to forget",
    "source": "chat:42", "evidence_id": "e1",
    "rationale": "user explicitly requested deletion",
}]
```

The authorization `evidence_id` must reference evidence in the same transition. Both styles compile to
the canonical `Operation.authorization` (`source`, `evidence_id`, `target_id`, `rationale`).

`GuardedMemory.capture_prior(scope)` exposes the explicit prior-state snapshot.
`GuardedMemory.resync_shadow_store(scope)` is the explicit recovery hook for the crash window
described in §4: it rewrites the baseline to the live host snapshot (at most one version bump) and
records a `RESYNC` audit event; it is never called automatically.

`InMemoryBackend` and `SQLiteBackend` are reference implementations only: they exist so the
interception contract can be tested deterministically (stdlib only, no network). A `GuardedMemory`
instance owns one shadow store; use one instance per scope (or one store keyed per scope), because
the store is reconciled against the whole scoped prior state.

## 4. Known limits of this reference integration

- The controlled JSON baseline (`run_case` store) and the host backend are separate writes; there is
  no distributed transaction spanning them. The ordering (host apply inside the verifier callback,
  baseline commit afterwards) prevents an ordinary stale/apply rejection from advancing the
  baseline. The remaining crash window is the opposite direction: process death after the host
  `replace_state` succeeds and before `run_case` commits the baseline leaves the baseline *behind*
  the host, so the next proposal fails prior-state reconciliation. The host is authoritative; call
  `GuardedMemory.resync_shadow_store(scope)` after establishing that the live host state is correct.
- The post-verification stale re-check and `replace_state` are serialized by the store `flock`, but a
  non-guarded host writer can still interleave between them. The rollback helper only restores the
  prior state when the host still holds our candidate, so it never clobbers an unrelated newer write;
  full version arbitration against non-guarded writers remains future work (`TODO.md`).
- The decision record is appended after the backend apply; a crash in between can leave an applied
  write without a decision record (the `run_case` audit event is already durable by then).
- Only adapters whose `replace_state` is genuinely atomic (or whose non-atomic path is restored on
  failure) can be labelled guarded. The reference backends satisfy this; third-party backends are
  unproven.

## 5. Black-box adapter (backend-agnostic path)

`membud.adapters.BlackBoxStaged` guards any backend through three public callables (`BlackBoxOps`: `add`,
`list`, `modify`; optional `schema`, `history`) plus a `scratch_factory()` returning a second, isolated
instance. Without that fourth input a black box cannot run a write without committing it. Per write it
observes live state, seeds a twin by raw adds, runs the native write on the twin, diffs twin-after vs
twin-before, rejects unmodeled changes (foreign scope, schema, scope identifiers, metadata, history),
verifies the diff, and on ACCEPT only replays the verified set on live, refusing stale state and
compensating on failure. `GuardedMemory` is the only live writer.

The shared contract suite is `tests/test_adapter_contract.py`: control non-vacuity, HOLD byte-identical,
unauthorized delete REJECTED, ACCEPT exactness, other scopes untouched, stale refused, injected commit
failure compensated, unrecoverable failure loud, backend error fails closed, direct writes refused,
foreign-scope/history rewrites rejected. Every adapter must pass all of it.

| Backend | Adapter | Contract suite | Sees DDL / hidden-table writes |
|---|---|---|---|
| Mem0 2.2.1 (pinned) | `StagedMem0` (shadow clone; needs storage layout) | 16/16 | yes (rejected) |
| Mem0 2.2.1 (pinned) | `BlackBoxStaged` | 16/16 | **no** (accepted, unseen) |
| stdlib SQLite fake | `BlackBoxStaged` | 16/16 | schema yes, hidden table no |
| LangMem 0.0.30 over LangGraph `InMemoryStore` (pinned in `requirements-langmem.txt`) | `BlackBoxStaged` via `langgraph_store_ops` | 15/16, 1 skipped (no history surface to rewrite) | **no** schema/index/vector writes (accepted, unseen); foreign namespaces yes |
| A-Mem (git `ceffb86`) | none | not stageable (finding) | n/a |
| Graphiti 0.31.2 / Letta 0.16.4 / Zep | none | not runnable offline (finding) | n/a |

Evidence: `data/results/adapter-contract-v1/<backend>/evidence.json` and `findings.json`
(`scripts/run_adapter_contract_evidence.py`); real-model run: `data/results/mem0-blackbox-realmodel-v1/`.

**What the black-box adapter cannot protect against** when a side effect is invisible through the public
API: writes to hidden tables or indexes, DDL, entity stores or caches that `list`/`schema`/`history` do not
expose; twin divergence (the twin must behave like live, otherwise the replayed set is not the staged
set); compensation that is itself limited to public calls (failed restore raises `PartialCommit`);
concurrent non-guarded writers between the stale check and the replay; bypass by calling the backend
directly; O(n) cost per write (about 27 ms vs 17 ms for `mem0_staged` at 2 memories). Mem0's `get_all`
cannot enumerate scopes (hence an explicit `scopes` list) and silently truncates, so a result that fills
the limit is refused.

**Universality is not claimed.** Two real backend families pass the black-box path (Mem0 2.2.1, LangMem 0.0.30 on
an in-memory LangGraph store) plus one stdlib fake; a third real backend could not be staged safely or offline
(A-Mem, Graphiti, Letta; see findings). LangMem was exercised only on `InMemoryStore` with a scripted chat model
(real `langmem` and `trustcall` code, fake LLM); its SQLite/Postgres stores, vector index, background manager
and real models are untested. Untested: Zep/Graphiti, Letta, any
backend other than Mem0 and LangMem through the black-box path, `AsyncMemory`, `delete_all`/`reset`, multi-process
contention, and every Mem0 write path except add/update/delete. The default verifier is unchanged
(deterministic, offline); no runtime dependency was added.

Current source-inspection feasibility findings for Mem0/A-Mem are in `data/backend-feasibility.md`.
Neither inspected host exposes a safe public pre-commit transition hook. Section 5 documents the black-box
route and why A-Mem still fails it.
