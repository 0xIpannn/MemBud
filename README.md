# MemBud MVP

A minimal local prototype for checking proposed memory-state transitions. It uses a JSON store, deterministic annotation-based checks, a toy graph representation, and a lossless textual representation. It makes no LLM calls and does not establish that graphs outperform text.

## Run

Install the project in editable mode, then run the demo:

```bash
python -m pip install -e .
python -m membud.demo
```

This runs 11 hand-authored cases through both input paths and writes isolated per-case stores plus an append-only audit log under `mvp-output/`. It also exports one raw Mermaid `.mmd` diagram per case in `mvp-output/` (for example, `omission.mmd`, `corruption.mmd`, and `unsupported.mmd`), plus `example-graph.md` and `example-graph.mmd` as documentation aliases of the valid case, not additional fixtures. Open any `.mmd` file with a Mermaid preview; raw diagram source starts with `flowchart LR`.

Run tests with `python -m pytest` after installing pytest (`python -m pip install pytest`).

## Input semantics

A transition contains the full prior state (`before`), the full proposed resulting state (`candidate`), explicit source-supported claims (`evidence[].supports`), claims required to survive (`required_claims`), and proposed `operations`. Evidence can also retain a `source` reference and original `text` excerpt. Claims are exact strings; semantic equivalence is intentionally not inferred.

Operations use explicit types and record references:

- `WRITE` proposes a new candidate record.
- `REVISE` changes an existing record while retaining its ID.
- `DELETE` removes a prior record and requires a non-empty `reason` as the toy authorization annotation.
- `SUPERSEDE` replaces a prior record with a distinct candidate record.
- `NOOP` must be the only operation, and its candidate state must equal the prior state.

Legacy input with `supersedes` and exactly one candidate record is normalized to these operations; serialized transitions use only `operations`.

Faithfulness allows claims already present in prior memory or explicitly supported by evidence. Preservation requires prior claims to remain on the same record unless an explicit `REVISE` or `SUPERSEDE` supplies evidence for every added replacement claim, or an explicit reasoned `DELETE` removes the record. An operation alone does not make an unsupported revision safe. Omitting an operation does not authorize removal, which lets the fixtures represent accidental omissions as well as intentional changes. Candidate state replaces the store only on `ACCEPT`.

The graph and text paths serialize the same information, then run the same deterministic checks. Graph edges connect the transition to prior/candidate records, evidence, required claims, and operations; the renderer does not invent missing relationships. The graph is currently a representation/demo artifact, not a distinct semantic verifier. The fixtures function as their own annotation oracle and are not benchmark evaluation data.
