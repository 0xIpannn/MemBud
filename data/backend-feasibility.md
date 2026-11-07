# Candidate backend feasibility notes

Source-code feasibility review only; no backend was installed, configured, or exercised at runtime. Findings are scoped to the inspected repository revisions and should be reconfirmed against a pinned release before implementation.

## Mem0

- Repository: [mem0ai/mem0](https://github.com/mem0ai/mem0), inspected commit `f1791daa86d469249c9b002f62644d58a1eb96a0` (shallow `HEAD`; no release/version pin established).
- License in the inspected checkout: Apache-2.0.
- In `mem0/memory/main.py`, public `Memory.add()` normalizes inputs and calls `_add_to_vector_store()`. The inferred path performs retrieval/extraction, then embeds and persists records using vector-store inserts, then writes history/entity links and messages. Existing update/delete methods also mutate the stores through internal calls.
- No supported public pre-commit callback exposing a complete proposed canonical transition was identified in the inspected path. Wrapping `Memory.add()` is too late; intercepting private persistence methods risks partial side effects and depends on internal implementation details. A safe integration likely requires a documented fork/subclass or a host-owned proposal pipeline that constructs and verifies the candidate before calling write methods.
- **Decision:** do not describe current Mem0 as plug-and-play. A controlled wrapper/fork is possible but needs a pinned release, targeted hook design, and integration test proving no mutation occurs on HOLD.

## A-Mem

- Repository: [agiresearch/A-mem](https://github.com/agiresearch/A-mem), inspected commit `ceffb860f0712bbae97b184d440df62bc910ca8d` (shallow `HEAD`; no release/version pin established).
- License in the inspected checkout: MIT.
- In `agentic_memory/memory_system.py`, `add_note()` calls `process_memory()` first. `process_memory()` can mutate neighbor notes in `self.memories` during an evolution action; `add_note()` then stores the new note and writes it to the retriever. Public `update()` also directly changes a note and updates ChromaDB by delete/re-add. These operations do not expose an isolated candidate transition/transaction callback.
- A pre-commit guard could be introduced by forking or subclassing around `process_memory()` and staging neighbor changes, but the existing method mutates shared note objects before the retriever write. Safe rollback/atomicity and graph-link preservation would need explicit implementation and tests.
- **Decision:** feasible only with a wrapper/fork that stages all new and neighboring note changes before persistence; not a drop-in adapter. Map content, timestamps, context, tags, links, and evolution history explicitly, and document losses (e.g., no source-span provenance unless the host supplies it).

## Recommendation

Continue with the controlled JSON store for the first reproducible comparison. Consider one backend only after the common adapter contract is defined and a proof-of-interception test demonstrates that the guarded path can prevent every relevant write side effect. Do not integrate against moving `HEAD` without pinning an exact release/commit and recording it in the experiment configuration.
