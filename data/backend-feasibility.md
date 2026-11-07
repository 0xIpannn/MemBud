# Candidate backend feasibility notes

Status: **the reference backend is demonstrated; third-party backend compatibility is NOT claimed.**
`src/membud/adapters/reference.py` implements a stdlib-only in-memory/SQLite host backend plus a
`GuardedMemory` interception layer, and `tests/test_reference_backend.py` proves ACCEPT-only
persistence (including byte-identical HOLD, pre-write rejection of undeclared/unauthorized changes,
evidence propagation, and audit records). The Mem0/A-Mem findings below are source-inspection
findings only; neither was installed, configured, or exercised at runtime, and no adapter for either
is implemented.

## Reference backend (demonstrated)

- In-memory and SQLite backends implement `MemoryAdapter` (`snapshot` / atomic `replace_state`) using
  only `membud.schemas.Record` and plain scopes. The SQLite backend replaces a scope inside one
  `BEGIN IMMEDIATE` transaction; tests compare SHA-256 of the database file before/after HOLD.
- `GuardedMemory.propose_update` captures prior state, builds a canonical schema-v2 `Transition`,
  runs the graph verifier through `store.run_case` (audited), re-checks prior state, and calls
  `replace_state` only on ACCEPT. HOLD, invalid input, stale prior state, and apply failures never
  persist the candidate. The interception contract is documented in `data/adapter-contract.md`.
- Evidence/provenance (`source`, `text`, `supports`, structured DELETE authorization) is carried into
  the canonical transition, the `run_case` audit input, and the guarded decision log.

## Mem0 (source inspection of PyPI release 2.2.1)

- Package: `mem0ai==2.2.1` (wheel `mem0ai-2.2.1-py3-none-any.whl`), downloaded with
  `pip download --no-deps` and unpacked for inspection; wheel SHA-256
  `fe91bb91ac8926231993a4aa58df00a60c6c74c709e6a338fe399500776eef4d`. No install, no runtime call,
  and no network/model call. Earlier shallow-HEAD GitHub review of `mem0ai/mem0` reached the same
  conclusion; this inspection pins a concrete release.
- License metadata in the wheel: Apache-2.0.
- Public `Memory.add()` (`mem0/memory/main.py`, ~line 760) validates input and calls
  `_add_to_vector_store()` (~line 881). With `infer=True` (the default) that method calls the LLM to
  extract memories, embeds them, and then persists: `self.vector_store.insert(...)` (~line 1057, with a
  per-record fallback ~line 1067), `self.db.batch_add_history(...)` (~line 1091), entity-store
  inserts/updates (~lines 1196+), and `self.db.save_messages(...)` (~lines 990/1044/1073/1207).
  `update()` (~line 1829) and `delete()` (~line 1883) call `_update_memory()` (~line 2052) /
  `_delete_memory()` (~line 2114), which mutate the vector store, then write history and entity-store
  links directly.
- **Hook analysis:** no public callback, transaction, or pre-commit hook exposing the complete proposed
  transition was found. `_add_to_vector_store` computes the extracted candidate set internally, then
  writes it to several stores sequentially. `VectorStoreBase` (`mem0/vector_stores/base.py`) is a
  swappable low-level interface, but it receives individual `insert`/`update`/`delete` calls rather than
  a canonical candidate, and history/message/entity-store side effects do not route through it; many
  fallbacks also swallow per-record write errors. Wrapping `Memory.add()` after the fact is too late,
  and intercepting private persistence methods depends on unstable internals and partial side effects.
- **Decision:** no safe pre-commit hook exists in this release. A guarded integration would require a
  fork/subclass or a host-owned proposal pipeline that replicates extraction, constructs and verifies
  the candidate, and only then invokes the write path — plus a pinned release and a proof that HOLD
  mutates nothing (including history, messages, and entity links). That is not implemented or claimed
  here.

## A-Mem (source inspection)

- Repository: [agiresearch/A-mem](https://github.com/agiresearch/A-mem), inspected commit
  `ceffb860f0712bbae97b184d440df62bc910ca8d` (shallow `HEAD`; no release/version pin established).
- License in the inspected checkout: MIT.
- In `agentic_memory/memory_system.py`, `add_note()` calls `process_memory()` first. `process_memory()`
  can mutate neighbor notes in `self.memories` during an evolution action; `add_note()` then stores the
  new note and writes it to the retriever. Public `update()` also directly changes a note and updates
  ChromaDB by delete/re-add. These operations do not expose an isolated candidate
  transition/transaction callback.
- A pre-commit guard could be introduced by forking or subclassing around `process_memory()` and
  staging neighbor changes, but the existing method mutates shared note objects before the retriever
  write. Safe rollback/atomicity and graph-link preservation would need explicit implementation and
  tests.
- **Decision:** feasible only with a wrapper/fork that stages all new and neighboring note changes
  before persistence; not a drop-in adapter. Map content, timestamps, context, tags, links, and
  evolution history explicitly, and document losses (e.g., no source-span provenance unless the host
  supplies it).

## Demonstrated vs claimed

| Claim | Status |
| --- | --- |
| Reference backend can implement `MemoryAdapter` with atomic scoped replacement | **Demonstrated** by `tests/test_reference_backend.py` |
| Guarded layer persists only on ACCEPT and leaves state untouched on HOLD/invalid input | **Demonstrated** (records and SQLite file bytes compared) |
| Evidence/provenance and structured DELETE authorization reach the transition/audit | **Demonstrated** |
| Mem0 can be used behind MemBud without modifying Mem0 | **Not claimed** — no safe pre-commit hook in 2.2.1; fork/staging pipeline required |
| A-Mem can be used behind MemBud without modifying A-Mem | **Not claimed** — neighbor mutations occur before persistence; staging wrapper/fork required |

## Recommendation

Continue with the controlled JSON store for the first reproducible comparison. The reference guarded
backend is the executable proof of the adapter contract and the baseline for any future host
integration. Integrate a third-party host only after pinning an exact release/commit, recording it in
the experiment configuration, and demonstrating with a test that every relevant write side effect
(primary store, history, messages, entity links) is absent on HOLD. Do not integrate against moving
`HEAD`.
