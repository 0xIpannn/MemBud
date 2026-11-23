# RQ2 (modularity): results of the backend-agnostic adapter experiment

**RQ2 (from `PLAN.md`):** *Can the same verification logic operate across different host memory backends through
adapters? What information or transformations are required by each adapter, and what information is lost?*

**Short answer.** Yes for a defined class of backends, no in general. The same verifier and the same 16-case contract
suite ran unchanged on three real backend families (Mem0 2.2.1, LangMem 0.0.30 on a LangGraph store, Graphiti 0.31.2 on
Neo4j 5.26.0) through one black-box adapter that needs only public API calls. Five other systems could not be put behind
it, each for a specific, recorded reason. The adapter also loses information, and how much depends on the backend. This
is evidence for conditional modularity, not for universality.

Branch `feature/universal-adapter` (worktree `/home/ifan/Projects/MemBud-adapter`), based on origin/main `9922582`.
Nothing is pushed.

## 1. What was built

`membud.adapters.BlackBoxStaged` guards a write to any backend through public callables only
(`membud.adapters.blackbox`). Per write it:

1. **Observes** live state through `list` (and optional `schema`/`history`), never through backend internals.
2. **Stages** on a *scratch twin*: seeds a fresh isolated instance by raw adds, runs the backend's **native** write on
   the twin, and diffs twin-after against twin-before to derive the side effects.
3. **Rejects unmodeled writes**: a foreign scope, a schema, scope-identifier, metadata or history change is refused.
4. **Verifies** the derived transition with the existing deterministic verifier (`GuardedMemory`, schema-v2
   `Transition`, `LexicalGrounding`); nothing in the verifier was changed.
5. On **ACCEPT only**, replays the verified set on live through public calls, refuses stale state (live digest
   differs from the staged one), checks the result, and compensates with an undo list on failure (`PartialCommit` if the
   restore itself fails).

One input goes beyond the three callables asked for: **`scratch_factory()`**, a second isolated instance. Without it a
black box cannot run a write without committing it. This is the first requirement a backend must meet.

The existing shadow-clone adapter for Mem0 (`mem0_staged`) was reused unchanged as the comparison.

## 2. The contract suite

`tests/test_adapter_contract.py` is one parametrized suite that every adapter must pass:

| Case | Meaning |
|---|---|
| control non-vacuity | an unguarded write really changes every surface, so byte-identity proves something |
| HOLD byte-identical (add, update) | every observable write surface is unchanged after a HOLD |
| unauthorized delete REJECTED | delete needs a host authorization |
| ACCEPT exactness (add; update+delete) | the live state equals exactly the verified state |
| other scopes untouched | an ACCEPT in one scope does not touch another |
| stale state refused | a writer that commits between staging and commit is not overwritten |
| commit failure compensated | an injected live-write failure is rolled back to the prior public state |
| unrecoverable failure is loud | if compensation also fails the outcome says so |
| backend error fails closed | an error while staging writes nothing |
| direct writes refused | the guard's host adapter cannot be used outside an approved write |
| foreign-scope / history-rewrite rejected | unmodeled side effects are refused, byte-identically |
| blind spots are the documented ones | schema change and hidden-surface writes: detected iff the adapter declares it |

Mutation checks confirm the suite bites: disabling the stale check fails the stale test on every black-box backend,
disabling compensation fails the compensation test, and making HOLD write fails four byte-identity tests.

## 3. Results per backend

| Backend (pinned) | Adapter | Contract suite | Verdict |
|---|---|---|---|
| Mem0 `mem0ai==2.2.1`, `spacy==3.8.18` | `mem0_staged` (shadow clone) | 16/16 | passes; sees every write surface |
| Mem0 2.2.1 | `BlackBoxStaged` (`mem0_ops`) | 16/16 | passes; blind to DDL and entity-store rows |
| LangMem `0.0.30` over LangGraph `InMemoryStore` (`langgraph 1.2.24`, `trustcall 0.0.39`) | `BlackBoxStaged` (`langgraph_store_ops`) | 15/16, 1 skipped | passes; no history to rewrite |
| Graphiti `graphiti-core 0.31.2` on Neo4j Community `5.26.0` (`neo4j` driver `6.4.1`) | `BlackBoxStaged` (`graphiti_ops`) | 15/16, 1 skipped | passes; loses provenance |
| stdlib SQLite fake | `BlackBoxStaged` | 16/16 | control with hidden surfaces |
| A-Mem (git `ceffb86`) | none | not stageable | finding |
| LlamaIndex `llama-index-core 0.14.26` fact memory | none | not stageable as-is | finding |
| AutoGen `autogen-core 0.7.5` memory | none | no native write | finding |
| Graphiti on embedded Kuzu (`kuzu 0.11.3`) | none | not runnable offline | finding |
| Letta `0.16.4` | none | not runnable offline (needs Postgres) | finding |
| Zep Cloud | none | SaaS, not tried | not tested |

The skipped case on LangMem and Graphiti is "history rewrite rejected": those stores keep no history, so the effect is
not expressible. The skip carries its reason in the evidence file. Machine-readable results:
`data/results/adapter-contract-v1/<backend>/evidence.json` and `findings.json`
(`scripts/run_adapter_contract_evidence.py`).

### 3.1 Black-box vs `mem0_staged` on the same real Mem0

| If the native write also… | black-box | `mem0_staged` |
|---|---|---|
| writes to a foreign scope | REJECTED | REJECTED |
| rewrites history | REJECTED | REJECTED |
| issues DDL | ACCEPTED, unseen | REJECTED |
| writes an entity-store row the API does not expose | ACCEPTED, unseen | REJECTED |

Cost: about 27 ms vs 17 ms for an accepted add and 26 ms vs 14 ms for a HOLD at 2 memories (offline model fakes,
machine-dependent, per-write cost is O(n) in the observed state).

This is the central trade-off: the shadow-clone adapter sees everything but must know Mem0's storage layout and will
not generalize; the black-box adapter generalizes but only sees what the public API exposes.

## 4. What an adapter needs (RQ2, first half)

For the black-box path a backend must offer:

1. **Observable state:** the whole state, with stable ids, readable through public calls (`list`).
2. **An isolatable twin:** a second instance whose writes cannot reach the first (`scratch_factory`).
3. **A native write that reduces to plain inserts, updates and deletes** of observable items, so the derived diff is what
   a replay will reproduce.
4. **Public undo:** a way to reverse writes (`modify`) so a failed commit can be compensated.
5. Optionally `schema()` and `history(id)`, which widen what can be detected.

What each adapter had to supply, concretely:

| Backend | id / text | scopes | twin | extra work |
|---|---|---|---|---|
| Mem0 | memory id / memory | explicit `scopes` list (`get_all` cannot enumerate), `top_k` raised and truncation refused | second embedded Qdrant+SQLite host | history normalized to `[event, old, new]` |
| LangMem | store key / content | namespace `(memories, user)`; every namespace enumerable | second `InMemoryStore` | timestamps excluded from `extra` |
| Graphiti | fact-edge uuid / `fact` | `group_id` partition; groups enumerated by Cypher on the public driver | another `group_id` in the same database (Community has one database) | empty partitions raise `Groups*NotFoundError`, caught; two endpoint nodes created on raw replay |

## 5. What is lost (RQ2, second half)

**Unobservable side effects are neither verified nor replayed.** Declared per backend and asserted by the suite:

| Backend | Seen | Not seen (blind spots) |
|---|---|---|
| Mem0 (black-box) | memory text, metadata, history, foreign scopes | DDL, entity-store rows, other collections |
| LangMem | items, foreign namespaces | vector index, store internals, schema |
| Graphiti | fact edges, relation, endpoint names, expiry flag, foreign partitions | nodes, episodes, `MENTIONS`, summaries, embeddings, provenance list, temporal fields, indexes |

**Graphiti shows the loss concretely** (`tests/test_graphiti_fidelity.py`). The same add, unguarded vs guarded:

| | Fact edges | Entity nodes | Episode nodes | `MENTIONS` edges |
|---|---|---|---|---|
| Unguarded native write | 2 | 3 | 1 | 3 |
| Guarded ACCEPT | 2 | 3 | 0 | 0 |

The guarded graph has the same facts, but no episode (provenance) and no mentions, because the replay can only
reproduce what the adapter observes. A graph memory whose value lies in provenance and temporal structure loses exactly
that under a facts-only observation model.

**Other limits of the black-box path** (from the module docstring):

- twin divergence: if the twin does not behave like live, the replayed set is not the staged set;
- compensation uses public calls only and can fail (`PartialCommit`), after which state is not guaranteed;
- concurrent writers are *detected* at commit, not serialized; direct calls to the backend bypass the guard;
- observation is O(n) per write; Mem0's `get_all` truncation is detected and refused, not worked around.

## 6. Backends that could not be put behind it

Each failure is an executable test or a recorded observation, not an opinion.

- **A-Mem** (`tests/test_amem_findings.py`): constructing a second in-process `AgenticMemorySystem` resets the shared
  chroma client and destroys the first instance's vector store (no in-process twin), and `add_note` always runs the
  evolution LLM and may rewrite neighbour notes, so replaying a verified add is not the staged set. The PyPI package
  `agentic-memory` is an unrelated project; A-Mem is pinned by commit.
- **LlamaIndex** (`tests/test_llamaindex_findings.py`): facts are bare strings with no ids; `Memory.aput` stores chat
  history and extracts facts only on token overflow, so the only synchronous write is `block.aput` below the front door;
  exceeding `max_facts` replaces the whole list with a model condensation.
- **AutoGen** (`tests/test_autogen_findings.py`): the interface is add/query/clear with no ids and no native write, so
  it is a plain store. autogen-ext's Chroma memory was not checked.
- **Graphiti on Kuzu:** `add_episode(group_id=…)` raises on `KuzuDriver`; with `group_id=None` a full-text index is
  missing and Kuzu's FTS extension needs a network download. (Graphiti did work on a local Neo4j server; see above.)
- **Letta:** the Python server line `0.16.4` is a 782 MB install and requires Postgres on startup; no SQLite path.
  (current PyPI `letta` 0.34.x is an unrelated Node CLI.)

The pattern is itself an RQ2 result: backends that qualify have **stable ids, a readable public state, an isolatable
twin and a write that reduces to plain inserts**; those that fail typically rewrite neighbours or the whole store on
write (A-Mem, LlamaIndex condensation), lack ids, or have nothing to stage.

## 7. Real-model runs

All runs: free OpenRouter model `nvidia/nemotron-3.5-lightning:free`, zero price enforced by the script, synthetic
dialogues only (no HaluMem data), $0, local hash embedder. Only the extraction LLM is real. Add only.

| Backend | Policy | Sessions | ACCEPT | HOLD | Notes |
|---|---|---|---|---|---|
| Mem0 | `LexicalGrounding` | 5 | 0 | 5 | every proposal held: third-person "User …" rewrite of first-person speech is not grounded |
| LangMem | `LexicalGrounding` | 5 | 0 | 5 | same cause |
| LangMem | `SpeakerNormalizedGrounding` | 5 | 3 | 2 | holds: synonym ("resides" vs "live"), spelling ("favorite" vs "favourite") |
| Mem0 | `SpeakerNormalizedGrounding` | 5 | 1 | 4 | holds include hallucinated "Germany", "October 2026", "November 22, 2026" |
| Graphiti | `SpeakerNormalizedGrounding` | 4 | 4 | 0 | **3 of 4 ACCEPTs were empty**: the model extracted no facts; only "moved from Berlin to Lisbon" persisted |

`SpeakerNormalizedGrounding` is a new deterministic, offline `LexicalGrounding` subclass that drops the claim subject
"User"; it changes nothing else (`tests/test_speaker_grounding.py`: invented details and synonyms are still held).
The default verifier is unchanged.

Reading these honestly:

- The guard failed closed on real model output on every backend; hallucinated details were held.
- It also produced **false holds** (true facts rejected for paraphrase), so the lexical policy trades recall for safety.
- Real-model ACCEPT evidence exists on Mem0, LangMem and Graphiti, but each is one free model, 4–5 synthetic sessions and
  add only. No real-model update or delete was run on any backend.
- The Graphiti run is weak evidence: the model returned nothing for three of four sessions and the "ACCEPT" of an empty
  candidate verifies nothing (a vacuous accept). The Berlin→Lisbon contradiction, the case that would show how
  fact-expiry is handled, was **not exercised**, because the Berlin session extracted no fact. Token use was 74,258
  across 12 requests at $0 (an earlier attempt in `json_object` mode made the model echo the JSON schema and stopped at
  the 40k cap after 6 requests; the cap was then raised to 150k with approval).
- The Mem0 re-run committed in the evidence file was served from the client cache, so its token counter reads 0; the
  first execution used 13,986 tokens.

## 8. How this answers RQ2

1. **Can the same verification logic run across backends?** Yes, for backends meeting the four conditions in section 4.
   The verifier, grounding policy and contract suite were identical on Mem0, LangMem and Graphiti; only a ~100-line ops
   builder per backend differed. Modularity is real but **conditional**.
2. **What does each adapter need?** Three public callables, a twin factory, stable ids and an observable state; plus
   backend-specific handling of scopes, listing limits and empty partitions (section 4 table).
3. **What is lost?** Everything the public API does not expose. For flat memories (Mem0, LangMem) the loss is internal
   bookkeeping; for a graph memory (Graphiti) it is the provenance and temporal structure that give the system its
   value. A facts-only observation model is therefore a poor fit for graph memory unless the host also observes nodes,
   episodes and edges, which would widen the adapter toward backend knowledge.
4. **Is universality supported?** No. Three real families pass; five other systems fail for recorded reasons. The claim
   the evidence supports is: *a backend-agnostic guard is possible for backends with stable ids, observable state, an
   isolatable twin and a write that reduces to plain inserts, with unobservable effects declared as unprotected.*
5. **Trade-off for the paper:** shadow-clone adapters (full visibility, backend-specific) versus black-box adapters
   (portable, partial visibility). The Mem0 comparison in 3.1 quantifies it: same pass/fail on the suite, but the
   black-box path accepts writes with DDL or hidden-store side effects that the shadow clone rejects.

## 9. Limits and what was not tested

- Two of three real families were exercised with a scripted chat model for the contract suite; real-model evidence is one
  free model, add only, small synthetic samples, one run each. No statistics, no HaluMem data.
- LangMem: only `InMemoryStore`; not tested: SQLite/Postgres stores, vector index, background memory manager.
- Graphiti: one Neo4j Community server (single database, so the twin is a `group_id` partition), no Neo4j Enterprise, no
  FalkorDB, no contradiction/expiry path, no update/delete through Graphiti's own machinery, no concurrent writers.
- Mem0: embedded Qdrant only; not tested: other vector stores, `AsyncMemory`, `delete_all`/`reset`, multi-process
  contention, graph memory.
- Zep Cloud and AutoGen's Chroma memory were not tried. Versions other than the pinned ones are untested.
- The black-box adapter's own cost and twin-divergence risk were measured only on Mem0 at 2 memories.

## 10. Reproduction

Pinned environments (Python 3.12, `uv`): `requirements-mem0.txt`, `requirements-langmem.txt`,
`requirements-graphiti.txt` (needs Neo4j Community 5.26.0 with auth disabled on `127.0.0.1:7687`, JDK 21, and
`GRAPHITI_TELEMETRY_ENABLED=false`), `requirements-amem.txt`, `requirements-llamaindex.txt`, `requirements-autogen.txt`.
Backends live in optional extras (`mem0`, `langmem`, `graphiti`); there are no new default runtime dependencies and
the default verifier stays deterministic and offline.

```
.venv/bin/python -m pytest -q                       # default env: backend tests skip
PYTHONPATH=src .venv-mem0/bin/python scripts/run_adapter_contract_evidence.py   # evidence for every backend
```

Real-model scripts (need a key; free models only, zero price enforced): `scripts/run_blackbox_realmodel.py` (Mem0),
`scripts/run_langmem_realmodel.py`, `scripts/run_graphiti_realmodel.py`. Evidence:
`data/results/mem0-blackbox-realmodel-v1/`, `data/results/langmem-blackbox-realmodel-v1/`,
`data/results/graphiti-blackbox-realmodel-v1/`.

Last test runs (final): `.venv` 806 passed, 69 skipped; `.venv-mem0` 876 passed, 36 skipped; `.venv-langmem` 821 passed,
54 skipped; `.venv-graphiti` 823 passed, 53 skipped.

## 11. Commits

`25bb2f9` black-box adapter · `9e5f597` contract suite on Mem0 (black-box and staged) · `9994904` A-Mem findings ·
`bb5b142` Mem0 real-model run · `4dd53f6` per-backend evidence and docs · `c387df8` LangMem · `fc1bcdc` LangMem
real-model run · `2840ef3` `SpeakerNormalizedGrounding` and first real-model ACCEPTs · `0739753` Mem0 re-run ·
`50611dc` LlamaIndex/AutoGen findings · `164570a` Graphiti on Neo4j · plus the Graphiti real-model run and this report.
