# Memory adapter contract

`membud.adapters.MemoryAdapter` is a structural Python protocol for a host that can expose and safely update memory state. An adapter must:

1. Provide a stable `backend_name`.
2. Implement `snapshot(scope)` and convert the relevant host state into canonical `Record` objects, preserving stable record IDs and claim boundaries where the host provides them.
3. Implement `replace_state(scope, records)` with atomic semantics for the scoped state. If the backend cannot stage/rollback all effects, it is not safe to call this a guarded adapter.
4. Document source provenance, timestamps, relationships, metadata, and host-side effects the mapping loses or cannot represent.
5. Avoid calling host mutation methods until MemBud returns ACCEPT. An accepted update must also be checked against a fresh snapshot or protected by a backend transaction to avoid stale-state commits.

Evidence and proposed operations are part of the canonical `Transition`, not implicit adapter metadata. Adapters must not invent evidence, authorization, or operation annotations. The JSON store is the controlled baseline; the protocol does not yet include a concrete adapter implementation or a distributed transaction mechanism.

Current source-inspection feasibility findings are in `data/backend-feasibility.md`. The inspected Mem0/A-Mem code paths do not expose a safe public pre-commit transition hook, so no backend adapter is implemented yet.
