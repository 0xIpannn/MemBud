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
3. **Verification with audit.** Submit the transition to MemBud verification on the graph path via
   `membud.store.run_case` (which writes the run audit event and conditionally the controlled JSON
   baseline). The three required checks are `coverage`, `preservation`, and `faithfulness`; any
   non-ACCEPT result means no host mutation.
4. **ACCEPT-only persistence.** Only when the result is ACCEPT: re-snapshot the scope and compare it
   exactly with the captured prior state. If it differs, fail closed and report `REJECTED`
   (stale-prior-state); do not persist. Otherwise call `replace_state(scope, transition.candidate)`
   exactly once, then re-snapshot and require equality with the accepted candidate. On apply failure,
   attempt to restore the captured prior state and report `REJECTED` with `applied=False`.
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

`GuardedMemory.capture_prior(scope)` exposes the explicit prior-state snapshot. `InMemoryBackend` and
`SQLiteBackend` are reference implementations only: they exist so the interception contract can be
tested deterministically (stdlib only, no network).

## 4. Known limits of this reference integration

- The controlled JSON baseline (`run_case` store) and the host backend are separate writes; there is no
  distributed transaction spanning them. The re-snapshot before apply and the post-apply equality
  check close the common stale-write window, but a crash between the shadow-store write and the host
  `replace_state` can leave the shadow baseline ahead of the host. The host backend is authoritative;
  the shadow store is an audit/verification baseline.
- The decision record is appended after the backend apply, so a crash in between can leave an applied
  write without its decision record (the `run_case` audit event is already durable).
- Only adapters whose `replace_state` is genuinely atomic (or whose non-atomic path is restored on
  failure) can be labelled guarded. The reference backends satisfy this; third-party backends are
  unproven.

Current source-inspection feasibility findings for Mem0/A-Mem are in `data/backend-feasibility.md`.
Neither inspected host exposes a safe public pre-commit transition hook, so no third-party adapter is
implemented or claimed.
