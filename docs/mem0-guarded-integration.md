# Guarded integration with a real Mem0 (`mem0ai==2.2.1`)

Status (2026-11-19): **implemented and tested end to end against the real, unmodified Mem0 library**, for one
pinned stack. This is a compatibility claim with a precise scope (section 6), not a general one.

| | |
| --- | --- |
| Backend | `mem0ai==2.2.1` (wheel SHA-256 `fe91bb91…ef4d`, three write-path source files pinned by hash) |
| Stack | sync `mem0.Memory`; embedded on-disk Qdrant (memory **and** entity collections); Mem0's SQLite history/messages DB; real spaCy entity extraction |
| Model endpoints | **offline deterministic fakes** (scripted LLM, hash embedder). No network, no paid call. |
| Code | `src/membud/adapters/mem0_staged.py` (wrapper), `mem0_offline.py` (offline harness) |
| Tests | `tests/test_mem0_staged.py` (38 tests; needs the pinned env, otherwise the module skips) |
| Evidence | `scripts/run_mem0_guarded_evidence.py` → `data/results/mem0-guarded-v1/evidence.json` (byte-stable) |

Reproduce:

```bash
uv venv --python 3.12 .venv-mem0
VIRTUAL_ENV=$PWD/.venv-mem0 uv pip install -r requirements-mem0.txt
PYTHONPATH=src .venv-mem0/bin/python -m pytest tests/test_mem0_staged.py
PYTHONPATH=src .venv-mem0/bin/python scripts/run_mem0_guarded_evidence.py
```

## 1. Why a wrapper, and why not a hook

`data/backend-feasibility.md` found that Mem0 2.2.1 exposes no public pre-commit hook: `Memory.add()` extracts,
embeds and then writes four stores sequentially (vector store, history, entity store, messages), and `update()` /
`delete()` write directly. Wrapping the call after the fact is too late; patching private methods is unstable and
partial.

The wrapper therefore does not hook Mem0 at all. It **runs the real Mem0 method on a private shadow copy of the
host state, derives the complete write set from a state diff, verifies it, and only then replays exactly that write
set onto the live host.**

## 2. Mechanism

For each guarded `add` / `update` / `delete`:

1. **Snapshot** the live host: every Qdrant collection (ids, vectors, payloads, collection params) and every
   SQLite table (`rowid`-keyed rows) plus the schema DDL.
2. **Stage.** Build a shadow `Memory` (`copy.copy` of the live object, so the same LLM/embedder and the *same
   unmodified Mem0 code*) whose vector store, entity store and SQLite DB are private in-memory clones loaded from
   the snapshot. Call the real `Memory.add/update/delete` on it. Read-your-writes, hash-dedup, entity
   link/unlink, history rows and message eviction are therefore Mem0's own behaviour, not a re-implementation.
3. **Diff** shadow-after vs snapshot. The diff is the complete set of pending host write side effects, derived from
   state rather than a list of write calls, so a write the wrapper has never heard of still appears (or fails the
   next step).
4. **Classify.** Memory-point changes become Mem0-style `ADD`/`UPDATE`/`DELETE` proposals for the existing
   `Mem0ProposalBridge`. The remaining changes must be *consistent consequences* of those proposals, or the
   operation is rejected as an `UnmodeledSideEffect`:
   - history rows are append-only and 1:1 (event, old text, new text) with the memory events;
   - entity points may only reference memories that exist after the commit and stay inside the operation scope;
   - messages are insert-only (plus Mem0's own per-session eviction of old rows);
   - no write to an unknown collection/table, no DDL, no collection-parameter change, no memory point outside the
     operation's scope, no change of scope identifiers.
5. **Verify.** The transition goes through the existing `GuardedMemory` / graph verifier. Evidence comes from the
   host (default: `LexicalGrounding` of the trusted `user` messages; or explicit evidence). DELETE needs
   host-supplied structured authorization; a model-originated delete is never self-authorizing.
6. **Commit (ACCEPT only).** `Mem0HostAdapter.replace_state` is the *only* code that writes the live host and
   `GuardedMemory` calls it only after ACCEPT. It additionally requires (a) an armed staged change set, (b) the
   records handed in equal the verified candidate, (c) the live state still equals the snapshot (stale writes are
   refused), then applies the change set (SQLite in one `BEGIN IMMEDIATE` transaction, Qdrant point upserts/deletes)
   and checks live == shadow-after. Any failure applies the inverse change set and re-checks the pre-state.
7. **HOLD / REJECTED / error.** The shadow is dropped. The live stores were never written to (they were only read
   to take the snapshot).

Every outcome (`StagedOutcome`) is appended to `staged-outcomes.jsonl`, with the pending side effects *enumerated
before they were suppressed*, plus MemBud's usual audit and decision logs.

## 3. What is demonstrated

`data/results/mem0-guarded-v1/evidence.json` (and the tests that assert the same things):

**Refused operations leave every host write surface byte-identical.** For each of 9 refused operations the raw
SHA-256 of *every file* under the Qdrant directory (memory and entity collections), the SQLite history DB and
Mem0's config directory, the logical dump of every collection/table, and a per-surface digest
(memory points / entity points / history / messages) are equal before and after:

| Scenario | Outcome | Notes |
| --- | --- | --- |
| add with hallucinated claims | HOLD (`UNSUPPORTED_CLAIM`) | unguarded control changes **all four surfaces**; the guard had enumerated memory-insert, entity-insert, history-insert and messages-insert before suppressing them |
| update to an unsupported text | HOLD (`UNSUPPORTED_CLAIM`, `PRIOR_RECORD_UNAUTHORIZED_CHANGE`) | control changes memory, entity store (unlink + relink), history |
| update contradicted by host evidence | HOLD (`CONTRADICTED_CLAIM`) | |
| delete without host authorization | REJECTED | control deletes the memory, its history row and entity links |
| delete citing non-existent evidence | REJECTED | |
| error inside real Mem0 (LLM failure) | exception propagates | live untouched |
| well-supported add + unmodeled write to a **foreign scope** | REJECTED (`UnmodeledSideEffect`) | |
| …+ **DDL** (new table) | REJECTED | |
| …+ **history rewrite** | REJECTED | |

(Also tested, not in the JSON: a new Qdrant collection, an extra history row, a dangling entity link, a stale scoped
prior, a stale foreign-scope write, direct `replace_state` calls.)

The control matters: it shows each byte-equality is not vacuous, i.e. unmodified Mem0 given the same operation
really does write to every one of those surfaces.

**ACCEPT persists exactly the verified state, with unmodified-Mem0 semantics.** After guarded `add`, `update` and
`delete`, live state digest-equals the verified shadow state, and an id/timestamp-free view of memories, entity
links, history and messages equals what the same operations produce on an identical *unguarded* twin host. The
accepted state survives closing and reopening Mem0 from disk, and unmodified Mem0 `get_all` / `search` / `history`
work on it.

**Failure injection.** A failed entity-store write after the memory write is compensated (logical state restored).
If the rollback itself fails, the outcome says `could not restore` loudly instead of reporting success.

**Mutation check.** Making the wrapper apply the change set on HOLD fails 9 of the tests, so the HOLD tests have
teeth.

Two bugs found by the tests while building this, both pinned by regression tests: (1) a failed NOOP commit could be silently re-applied, because `GuardedMemory` calls `replace_state(prior)` after a failed apply and, for a NOOP, the prior equals the candidate; armed change sets are now single-use (`test_failed_noop_commit_is_not_silently_retried_by_the_guard`). (2) Mem0 skips entity cleanup if its entity store was never
initialized in the process, so a shadow whose entity store is lazy behaves differently from a live Mem0 that has
initialized it. The shadow now initializes it eagerly; `test_accept_matches_what_unguarded_mem0_does` pins this.

## 4. Verification semantics the wrapper adds

* A claim is verified against host evidence, not model assertions. `LexicalGrounding` is a deliberately simple
  host policy (every content token of the claim, lightly stemmed, appears in one trusted user message); any callable
  with the same signature can replace it. It is a stand-in for a real grounding policy, not a claim about
  extraction quality.
* On HOLD, raw messages are *not* saved either (Mem0 would have saved them). That is a semantic consequence: the
  next `add` for that session will not see them as `last_messages` context. Hosts that need messages retained
  independent of memory acceptance must store them themselves.
* `StagedOutcome.mem0_result` is `None` unless applied; `staged_result` is what Mem0 *would* have returned and
  contains ids that do not exist if it was held.

## 5. Residual risks and non-guarantees

* **Vectors are compared with a tolerance (1e-5), not bitwise, for ACCEPT.** Qdrant (cosine) re-normalizes on every
  upsert and that is not bit-idempotent (a float32 ulp), so a vector copied through the API cannot be bit-equal.
  IDs, payloads, rows and schema are exact. HOLD does not rely on this: it is proven on raw file bytes.
* **Qdrant + SQLite are not one transaction.** Commit is "SQLite in one transaction, Qdrant sequentially, inverse
  on failure". The inverse restores logical state (tested) but not bytes (internal storage files differ), and a
  process crash mid-commit can leave a partial write. `PartialCommitError`/`could not restore` is the loud signal
  when compensation fails. This is best-effort atomicity, not durability.
* **Concurrent writers are detected, not serialized.** A change to any host state between snapshot and commit
  makes the commit fail (`StalePriorState`); the guarded call is then REJECTED and must be retried. There is no
  cross-process lock and the process-local lock does not stop an unguarded writer.
* **Bypass.** Anything holding the raw `Memory` object can still call `memory.add()`. The wrapper cannot enforce
  that the host routes writes through it (same limit as every guard in this repo).
* **Cost is O(store size) per write** (snapshot + clone + diff). Measured median guarded-add latency on this
  machine, offline fakes, `data/results/mem0-guarded-v1/timing.json`: ≈15–20 ms at 0 memories, ≈45–60 ms at 100,
  ≈150–225 ms at 500 (HOLD / ACCEPT). It does not scale to large stores without incremental snapshots.
* **Pinned and fail-closed.** Another Mem0 version, changed source hashes, enabled telemetry, a non-Qdrant vector
  store, `AsyncMemory` or a `Memory` subclass all raise `StagedWriteError` at construction. Telemetry is an outward
  network write that cannot be staged, so it must be off.
* **Setup side effect.** The entity collection is created at wrapper construction (the live Mem0 would create it
  lazily on first write). Everything after construction is covered by the byte-identity claim.
* **Not tested:** a real LLM or embedder, a Qdrant server (only embedded local mode), other vector stores, graph
  memory, multimodal messages, `delete_all`/`reset`, `AsyncMemory`, large stores, real multi-process contention, real
  multi-session dialogue. Extraction *quality* is not judged; only what MemBud models (claims, provenance, DELETE
  authorization) is verified.

## 6. Claim, precisely

> With `mem0ai==2.2.1` (hash-pinned write path), embedded Qdrant + SQLite history, telemetry off, and the write
> operations routed through `StagedMem0`, a HOLD, REJECTED or erroring `add`/`update`/`delete` leaves the Qdrant
> memory and entity collections, the SQLite history and messages tables, and every file Mem0 owns byte-identical,
> and an ACCEPT persists exactly the verified staged state with the same observable semantics as unmodified Mem0.
> Tested with offline fakes for the model endpoints only.

It is **not** a claim that Mem0 can be guarded when called directly, that other Mem0 versions/stores work, that a
real LLM's behaviour was evaluated, or that the guard improves downstream answer quality on real dialogue (that
remains open: `TODO.md`).

## 7. A-Mem

Not implemented. `data/backend-feasibility.md` records why it needs a different mechanism (neighbour notes are
mutated in shared in-memory objects *before* the retriever write, so a state-diff shadow would need a deep clone of
the note graph plus ChromaDB). The shadow-and-diff approach here is the template for it.

## 8. Backend-agnostic path (black-box adapter)

`StagedMem0` above depends on Mem0's storage layout. `membud.adapters.BlackBoxStaged` guards a backend through
public callables only (see `data/adapter-contract.md` section 5). On real Mem0 2.2.1 both adapters pass the same
16-case contract suite (`tests/test_adapter_contract.py`); they differ in what they can see: when a native write
also issues DDL or writes an entity-store row not exposed by the API, `StagedMem0` REJECTS it and the black-box
adapter ACCEPTS it unseen. Foreign-scope writes and history rewrites are rejected by both.

Real-model check (`scripts/run_blackbox_realmodel.py`, free `nvidia/nemotron-3.5-lightning:free`, local hash
embedder, 5 synthetic sessions, $0): extraction was sensible but all five proposals were HELD by the lexical
grounding policy (paraphrase false holds); live state stayed unchanged. This shows fail-closed behaviour only,
not an ACCEPT with a real model. A first attempt on `google/gemma-4-31b-it:free` hit upstream 429s.

Findings for other backends (`data/results/adapter-contract-v1/findings.json`): A-Mem (commit `ceffb86`) cannot be
staged through its public API (a second in-process instance destroys the first's chroma store; `add_note` always
rewrites neighbours); Graphiti 0.31.2/Kuzu and Letta 0.16.4 do not run offline; Zep is SaaS.

LangMem (0.0.30, `langgraph 1.2.24`, `trustcall 0.0.39`) was added as the second real backend through the same
adapter (`langgraph_store_ops` over a LangGraph `BaseStore`): 15/16 contract cases pass; the skipped case is a
history rewrite, which this store cannot express because it keeps no history. Its native write is one scripted LLM
call plus plain puts (no cascades), which is why it stages cleanly where A-Mem does not. Blind spots: index/vector
writes and any other store internals. Tested only with `InMemoryStore` and a scripted chat model.

Real-model LangMem run (`scripts/run_langmem_realmodel.py`, free `nvidia/nemotron-3.5-lightning:free`, $0, 5 requests,
5,300 tokens, synthetic sessions): the real manager extracted facts, but all five proposals were HELD by the default
`LexicalGrounding` and live state stayed empty. Even "User adopted a dog named Biscuit." is not grounded by
"I adopted a dog named Biscuit." (checked offline), i.e. third-person rewriting of first-person speech defeats the
lexical policy. Some extractions also carried model-added commentary ("p ~ 0.99"), which a grounded check should
hold. This shows fail-closed behaviour and the paraphrase false-hold problem, not an ACCEPT with a real model.

Second LangMem run with `SpeakerNormalizedGrounding` (a `LexicalGrounding` subclass that drops the claim subject
"User"/"User's"; deterministic, offline; `tests/test_speaker_grounding.py`): 3 of 5 sessions were ACCEPTed and
persisted exactly the verified memory (engineer at Acme Corp; allergic to peanuts; adopted a dog named Biscuit), 2
were HELD ("resides in Berlin" is a synonym of "live"; "favorite" vs the user's "favourite" is a spelling mismatch).
Both holds are the intended conservative behaviour, and both are false holds (the facts are true), so this policy
trades recall for safety. 5 requests, 11,762 tokens, $0, free `nvidia/nemotron-3.5-lightning:free`. First real-model
ACCEPTs on any backend; one model, 5 synthetic sessions, add only (no real-model update/delete), Mem0 not re-run
with this policy. Evidence: `data/results/langmem-blackbox-realmodel-v1/evidence.json` (overwritten by this run; the
all-HOLD run is in the commit history).

Mem0 re-run with `SpeakerNormalizedGrounding` (`scripts/run_blackbox_realmodel.py`, same free model, 5 synthetic
sessions, first execution 13,986 tokens, $0; the committed evidence file is the byte-identical-input re-run served
from the client cache, so its token counter reads 0): 1 ACCEPT ("User adopted a dog named Biscuit"), 4 HOLD. The
holds are mostly correct: the model added "Germany", "October 2026" and "November 22, 2026", none of which the user
said; one write was held as a whole because its second claim ("favorite" vs "favourite") was unsupported, which also
blocked the supported claim (all-or-nothing per write). Real-model ACCEPT evidence now exists on two backends
(Mem0 2.2.1: 1 accept; LangMem 0.0.30: 3 accepts), each with one free model, add only.

Third-backend probes (executable findings, `tests/test_llamaindex_findings.py`, `tests/test_autogen_findings.py`):
LlamaIndex `FactExtractionMemoryBlock` (llama-index-core 0.14.26) keeps facts as bare strings without ids, extracts
them only when the token budget overflows (not on `Memory.aput`), and rewrites the whole list when `max_facts` is
exceeded; an id-based black-box diff cannot track updates without an adapter-side id registry, which was not built.
AutoGen's `Memory` (autogen-core 0.7.5) has `add`/`query`/`clear` only and no native write, so it is a plain store.
autogen-ext's Chroma-backed memory was not checked. Net: still two real backend families through the black-box path.

Graphiti 0.31.2 on Neo4j (third real backend; `graphiti_ops`, `tests/adapter_harness_graphiti.py`): 15/16 contract
cases pass, 1 skipped (no history surface). Setup, all inside the worktree: Neo4j Community 5.26.0 tarball in the
git-ignored `.neo4j/` with `dbms.security.auth_enabled=false`, bound to 127.0.0.1, run with the local JDK 21
(`.neo4j/neo4j-community-5.26.0/bin/neo4j console`); `.venv-graphiti` from `requirements-graphiti.txt`;
`GRAPHITI_TELEMETRY_ENABLED=false`. LLM and embedder are offline fakes; only the database is real. Tests skip when no
Neo4j listens on 7687. The accepted write keeps the facts but drops the episode node and MENTIONS edges (see
`data/adapter-contract.md`); a real-model Graphiti run was not done.

Real-model Graphiti run (`scripts/run_graphiti_realmodel.py`, free `nvidia/nemotron-3.5-lightning:free`, `json_schema`
mode, 12 requests, 74,258 tokens, $0): 4 sessions, 4 ACCEPT, but 3 of the 4 were empty (the model extracted no facts),
so they verify nothing; "moved from Berlin to Lisbon" was persisted. The Berlin to Lisbon contradiction was not
exercised. An earlier `json_object` attempt made the model echo the JSON schema. See `docs/rq2-modularity-results.md`.
