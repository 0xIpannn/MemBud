# MemBud MVP

A minimal local prototype for checking proposed memory-state transitions. It uses a JSON store, deterministic annotation-based checks, a toy graph representation, and a lossless textual representation. It makes no LLM calls and does not establish that graphs outperform text.

## Run

Install the project in editable mode, then run the demo:

```bash
python -m pip install -e .
python -m membud.demo
```

This runs 10 hand-authored cases through both input paths and writes isolated per-case stores plus an append-only audit log under `mvp-output/`. It also exports one raw Mermaid `.mmd` diagram per case in `mvp-output/` (for example, `omission.mmd`, `corruption.mmd`, and `unsupported.mmd`), plus `example-graph.md` and `example-graph.mmd` for the valid case. Open any `.mmd` file with a Mermaid preview; raw diagram source starts with `flowchart LR`.

Run tests with `python -m pytest` after installing pytest (`python -m pip install pytest`).

## Input semantics

A transition contains the full prior state (`before`), the full proposed resulting state (`candidate`), explicit source-supported claims (`evidence[].supports`), and claims required to survive (`required_claims`). Claims are exact strings; semantic equivalence is intentionally not inferred. Faithfulness allows claims already present in prior memory or explicitly supported by evidence. Preservation requires every prior claim to remain on the same record unless that record ID is explicitly listed in `supersedes`. A supersession is explicit metadata, not proof that the change is justified; this toy MVP does not judge supersession correctness. Candidate state replaces the store only on `ACCEPT`.

The graph and text paths serialize the same information, then run the same deterministic checks. The graph is currently a representation/demo artifact, not a distinct semantic verifier. The fixtures function as their own annotation oracle and are not benchmark evaluation data.
