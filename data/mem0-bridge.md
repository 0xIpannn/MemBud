# Mem0 staged-proposal bridge (Phase 0)

Status: **the host-owned staging pipeline is demonstrated against the reference backend; Mem0
compatibility is NOT claimed and no Mem0 code was installed, imported, or run.**

`src/membud/adapters/mem0_bridge.py` implements a stdlib-only bridge that maps a *host-normalized,
Mem0-style extracted proposal set* plus an explicit prior canonical snapshot into a schema-version-2
`membud.schemas.Transition`, then routes it through the existing reference
`membud.adapters.reference.GuardedMemory` path (ACCEPT-only persistence). It is a host-owned pipeline,
not an interception hook: see `data/backend-feasibility.md` for the source-inspection finding that
`mem0ai==2.2.1` has no safe public pre-commit hook.

## 1. Pinned provenance

| Item | Value |
| --- | --- |
| Package | `mem0ai==2.2.1` |
| Wheel | `mem0ai-2.2.1-py3-none-any.whl` |
| Wheel SHA-256 | `fe91bb91ac8926231993a4aa58df00a60c6c74c709e6a338fe399500776eef4d` |
| Evidence | `data/backend-feasibility.md` (downloaded with `pip download --no-deps`, unpacked; no install, no runtime call) |
| License (wheel metadata) | Apache-2.0 |

This pin is the provenance of a **source inspection**, not a compatibility claim. The inspected
release exposes `Memory.add()` -> `_add_to_vector_store()`, which extracts memories, embeds them, and
then writes the vector store, history, messages, and entity links sequentially; no public callback,
transaction, or pre-commit hook exposes the complete proposed transition first.

The *proposal shape* consumed by the bridge is a host-normalized contract modeled on Mem0's public
record/metadata/scoping shapes and its inferred event vocabulary (`ADD`/`UPDATE`/`DELETE`/`NONE`). It
is **unverified against a running Mem0**: the exact internal payload of `_add_to_vector_store` was not
runtime-tested, and this document must not be read as a claim about internal data structures of that
release. `MEM0_SHAPE_CONTRACT` in the module records this status machine-readably.

## 2. Proposal contract

`Mem0ProposalBridge.prepare_proposals(scope=..., prior=..., proposals=..., case_id=...)` accepts plain
dicts only (never host objects). Unknown keys are rejected. Allowed keys:

| Key | Meaning | Rules |
| --- | --- | --- |
| `event` | Extracted operation | `ADD`, `UPDATE`, `DELETE`, or `NONE` (case-insensitive; normalized) |
| `id` | Host record ID | Optional for `ADD` (a deterministic ID is derived), required for `UPDATE`/`DELETE` |
| `memory` | Extracted memory text | Required for `ADD`/`UPDATE`; one claim per memory text; no leading/trailing whitespace |
| `metadata` | Host metadata | Optional JSON-serializable mapping; retained in staging provenance only |
| `user_id` / `agent_id` / `run_id` | Scope identifiers | Optional; if present must match the bridge scope, otherwise the proposal is rejected |

Event mapping (deterministic):

| Mem0-style event | Canonical operation | Candidate effect |
| --- | --- | --- |
| `ADD` | `WRITE` | New record; explicit ID or `mem0-<sha256(scope, memory)[:16]>`; ID collisions with prior state or other proposals rejected |
| `UPDATE` | `REVISE` | Existing prior record ID required; the record's claims become the proposal's memory text |
| `DELETE` | `DELETE` | Existing prior record ID required; removal needs host-supplied structured authorization (below) |
| `NONE` | no operation | Explicit no-op; if nothing changes, the transition declares a single `NOOP` |

Mapping decisions:

- **Claims** are the memory text verbatim (one claim per proposal). No NLP splitting or normalizing is
  invented by the bridge; records from the prior snapshot keep their claim boundaries.
- **Stable IDs** are deterministic: derived IDs hash the canonical scope plus the memory text, so the
  same proposal set maps to the same transition dict/digest. Candidate order is also deterministic
  (prior order for retained/revised records, new records sorted by ID), independent of proposal order.
- **Duplicate IDs are rejected**: two `ADD`s with the same explicit ID, two derived IDs from the same
  memory text, and two events targeting the same prior record all raise before any write.
- **Evidence/provenance passes through**: the host supplies `evidence` (claim-supporting/contradicting
  text and source); the bridge normalizes and forwards it unchanged into the canonical transition.
  Model output cannot add evidence. Host `metadata` is retained on the staging object but is not
  invented into evidence.
- **Structured DELETE authorization** is supplied by the trusted host as
  `delete_authorizations={record_id: {source, evidence_id, rationale}}`, never read from the model's
  metadata. The authorizing `evidence_id` must reference evidence in the same transition, and the
  operation records `source`, `evidence_id`, `target_id`, and `rationale`.
- **Scope** is required and non-empty; proposals declaring a scope identifier the bridge scope does not
  define, or a different value, are rejected. Unscoped memory writes are refused.
- **Fail-closed preparation**: missing/unknown fields, ambiguous or duplicate targets, non-canonical
  values (e.g. whitespace-padded memory), stale DELETE memory text, non-canonical prior snapshots, and
  any resulting schema invalidation raise `Mem0BridgeError` *before* verification, audit, or backend
  writes. An empty proposal set is rejected too (the host decides how to represent "nothing
  extracted"). Preparation itself performs no writes.

## 3. What IS demonstrated

The staged pipeline is exercised end to end by `tests/test_mem0_bridge.py` with a fake Mem0-like
extractor (a plain callable returning plain dicts); no Mem0 package is imported and no network/model
call occurs.

| Demonstrated | Where |
| --- | --- |
| Host-normalized proposals map to a schema-v2 `Transition` with explicit staging object | `test_add_proposal_maps_to_schema_v2_write_transition`, `test_prepare_is_pure_and_writes_nothing` |
| ACCEPT persists *exactly* the staged candidate (reference in-memory and SQLite backends) | `test_accept_persists_exactly_the_staged_candidate` |
| HOLD on unsupported claim leaves backend records and SQLite file bytes identical | `test_hold_unsupported_claim_leaves_records_and_sqlite_bytes_identical` |
| HOLD on contradicted evidence (faithfulness `CONTRADICTED_CLAIM`) leaves bytes identical | `test_hold_contradicted_evidence_leaves_records_and_sqlite_bytes_identical` |
| Invalid proposals rejected before any verification/audit/backend write | `test_invalid_proposal_is_rejected_before_any_write` (13 cases) |
| Duplicate IDs rejected | `test_duplicate_proposal_ids_are_rejected` |
| Deterministic mapping, digest, and proposal-order independence | `test_determinism_and_proposal_order_independence`, `test_staged_digest_matches_canonical_transition_payload` |
| Scope/evidence mapping and scope-dependent stable IDs | `test_scope_and_evidence_mapping_are_validated_and_passed_through`, `test_stable_ids_are_scope_dependent` |
| Structured DELETE authorization mapping and ACCEPT | `test_delete_requires_and_records_structured_authorization` |
| Stale staged prior is refused and the raced state is preserved | `test_stale_staged_prior_is_refused` |
| `NONE` maps to a `NOOP` transition | `test_none_event_produces_noop_transition` |

All persistence in these tests goes to the reference `InMemoryBackend`/`SQLiteBackend`; the bridge
composes `GuardedMemory` without modifying `reference.py`.

## 4. What is NOT demonstrated or claimed

- **No interception inside unmodified Mem0.** The bridge never imports, wraps, monkeypatches, or calls
  Mem0. `Memory.add()`/`update()`/`delete()` still write their stores immediately; staging afterwards
  is too late.
- **No fork/subclass.** `_add_to_vector_store`, `_update_memory`, and `_delete_memory` are untouched.
- **No runtime-tested Mem0.** No install, no configuration, no extraction call, no embedding/model
  call, no vector store run. The event vocabulary and field names are a modeled contract.
- **No claim about Mem0's write side effects** (vector store, history, messages, entity links),
  concurrency, failure/rollback behavior, or crash atomicity.
- **No extraction-quality claim.** The bridge maps whatever the host extractor produced; it does not
  judge whether extraction found the right memories.

## 5. Threat model

Trust boundaries: the extractor/model output is untrusted; the host application, the prior canonical
snapshot, evidence, and DELETE authorizations are trusted inputs. Nothing in an extraction can widen
its own authority.

| Threat | Mitigation in this bridge | Residual risk |
| --- | --- | --- |
| Model-extracted `DELETE` used to erase memory | DELETE requires host-supplied structured authorization whose `evidence_id` must reference transition evidence; authorization never comes from model metadata | A compromised/incorrect host can still authorize a deletion; the bridge cannot judge intent |
| Evidence spoofing ("the chat said X") | Evidence is host-supplied and passed through; the verifier requires a provenance path and rejects contradicted claims | The bridge does not verify that evidence text is genuine; the host must source evidence from real transcripts |
| Cross-user scope confusion | Scope required; proposal scope identifiers must match the bridge scope; derived IDs are scope-dependent | A host that calls the bridge with the wrong `scope` can still target that scope; scope is the host's responsibility |
| Duplicate/stale IDs causing partial or cloned state | Duplicate target/record IDs rejected; `UPDATE`/`DELETE` must reference the prior snapshot; stale DELETE memory rejected; `GuardedMemory` re-checks prior state before persisting | A non-atomic host backend could still partially write (see `data/adapter-contract.md`) |
| Undeclared mutation hidden in a proposal | Every state change must be declared by an operation; `Transition.from_dict` rejects undeclared insertion/modification/removal; unknown proposal keys rejected | — |
| Writing before verification | Preparation is pure; only `apply_staged`/`StagedProposal.apply` persists, through `GuardedMemory`, and only on ACCEPT | A host could bypass the bridge and call the host's own write path; the contract cannot enforce that |
| Silent degradation | All contract violations raise `Mem0BridgeError`; no "best effort" mapping | — |
| Partial multi-store writes in a real host | Out of scope: MemBud persists the complete scoped candidate in one `replace_state`; Mem0's sequential stores and per-record error fallbacks are not guarded | Must be addressed before any Mem0 claim (section 6) |
| Metadata/PII handling | `metadata` is retained only in the staging provenance object and is not written into the canonical transition/audit by the bridge | Host-side logging of the staging object is the host's responsibility |

## 6. Required before any Mem0 compatibility claim

A compatibility claim requires demonstrating, against a pinned release, that HOLD mutations are absent
from **every** write side effect and that ACCEPT persists exactly the verified candidate. Source
inspection of `mem0ai==2.2.1` identified the following write paths (line numbers in
`data/backend-feasibility.md`); all are **NOT VERIFIED**:

| Write side effect | Why it matters | Required proof | Status |
| --- | --- | --- | --- |
| `vector_store.insert(...)` (and per-record fallback) | Primary memory store; fallback can partially succeed | HOLD => no insert/update/delete; ACCEPT => exactly the candidate vectors/IDs | NOT VERIFIED |
| `vector_store.update(...)` / `vector_store.delete(...)` in `_update_memory` / `_delete_memory` | Update/delete paths bypass add-time staging | Same HOLD/ACCEPT proof for update and delete flows | NOT VERIFIED |
| `db.batch_add_history(...)` | History rows are durable side effects even when the vector write is later rejected | HOLD => no history row; ACCEPT => documented history row | NOT VERIFIED |
| `db.save_messages(...)` | Raw message persistence happens alongside extraction | HOLD => no message row; ACCEPT => documented message row and retention behavior | NOT VERIFIED |
| Entity-store inserts/updates and entity links (~lines 1196+) | Entity links are separate stores and can drift from memory state | HOLD => no entity mutation; ACCEPT => documented entity updates | NOT VERIFIED |
| Crash/failure atomicity across stores | Sequential writes are not transactional; a crash can leave partial state | Interrupt-injection test proving no partial state or a documented rollback | NOT VERIFIED |
| Concurrency / stale prior state | Two writers can interleave between snapshot and write | Prior-state re-check against a fresh host snapshot or host transaction | NOT VERIFIED |
| Extraction determinism and prompt/version pin | Proposals are model output; behavior changes across releases/prompts | Pin release, prompt, model, and record them in the experiment config | NOT VERIFIED |

Checklist to execute before claiming (ordered):

1. Pin the exact Mem0 release (wheel sha256), Python version, extractor prompt/model, and vector store
   backend in the experiment configuration.
2. Build a staging wrapper/fork that produces the complete candidate **and** the complete set of pending
   side effects *before* any write.
3. Run MemBud verification on the canonical transition inside that wrapper; call the host write path
   only on ACCEPT.
4. Prove with tests that HOLD/invalid input leaves vector store rows, history, messages, entity links,
   and file bytes unchanged; prove ACCEPT writes exactly the candidate plus the documented side effects.
5. Prove failure/rollback behavior under injected errors and concurrent writers.
6. Only then document the integration as guarded, with the residual losses (e.g. Mem0 history/entity
   semantics that MemBud's canonical model does not represent).

## 7. Path to a real integration

- **A. Host-owned extraction + host-owned storage (what this bridge models).** The host extracts
  proposals, MemBud verifies, and the host persists the complete candidate with an atomic
  `replace_state` into its own store (or the reference SQLite backend). This is safe *only if* the host
  store is authoritative; mirroring to Mem0 afterwards reintroduces unguarded Mem0 writes and must be
  documented as a cache, not a guarded path.
- **B. Fork/subclass the Mem0 write path.** Stage the extracted candidate and all intended side effects
  before `_add_to_vector_store` writes anything; call MemBud verification inside the staged path and
  invoke the original writer only on ACCEPT. This is the most direct route to a real guarded Mem0
  integration, but it depends on private internals (`_add_to_vector_store`, `_update_memory`,
  `_delete_memory`), requires maintaining a pinned fork, and needs the full section 6 checklist.
- **C. Upstream hook.** Contribute a public pre-commit candidate callback/transaction to Mem0 and claim
  unmodified-host compatibility only once such a release exists and is pinned.

Until A/B/C is demonstrated, the honest status is: **Mem0 is a candidate host; only the host-owned
staging mechanics are demonstrated, against the reference backend.**

## 8. Reproduce

```bash
TMPDIR=$PWD/.tmp-mem0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_mem0_bridge.py tests/test_reference_backend.py
```

No network install, no Mem0 import, no paid/model calls.
