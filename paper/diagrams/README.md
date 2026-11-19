# Chapter 3 diagrams (draw.io)

## Files

| File | Purpose |
|---|---|
| `Methodology Flowchart.drawio` | **Original upload, unchanged** (runtime sketch: Start → host input → generate update → analyze graphs → "Update Safe?" → Revise/Allow/Block → End). Kept for reference. |
| `methodology-flowchart-chapter3.drawio` | **Updated, editable flow** aligned with Chapter 3 §3.2.5 "Modelling: The MemBud Method" and Algorithm 3.1 "Verify-and-commit". Open this in draw.io / diagrams.net. |
| `methodology-flowchart-chapter3-preview.png` / `.svg` | Approximate schematic preview generated from the same geometry. The exact render is what draw.io shows; there is no draw.io CLI in this environment. |

Regenerate the preview and the updated drawio with:

```sh
.venv/bin/python paper/tools/make_drawio_flowchart.py
```

## What changed, and why

The original flowchart was a runtime sketch that no longer matched Chapter 3:

| Original | Updated (Chapter 3) | Why |
|---|---|---|
| Start → User Input → Input Processing → Generate Proposed Memory Update | Start → Host proposes update → Adapter snapshot → Parse & validate transition (schema v2) | Chapter 3 defines verification over a canonical transition package built by the adapter (§3.2.5, Algorithm 3.1). |
| "Analyze Proposed Memory Graph, Compare With Existing Memory Graph" | Build canonical transition `T=(B,C,E,R,O)`; build graph `G` and text `Z` from the same facts | Verification is coverage/preservation/faithfulness over the transition, not a comparison of two host graphs; graph and text are two renderings of identical facts. |
| "Update Safe?" | "Schema valid?" → "Stored state = T.before?" → "All required checks PASS?" | Chapter 3 has three ordered gates: schema validation (`INVALID_INPUT`), store/prior-state reconciliation (fail closed + audit error), and the check decision. |
| "Revise Proposed Update" loop ("No, but reviseable") | Removed; note states "REVISE / retry is not implemented in this prototype; execution retries never authorise a commit." | §3.2.5 says the REVISE decision is not implemented and is not part of the primary comparison. |
| Allow Update / Block Update | `ACCEPT` / `HOLD`; invalid input reported separately as `INVALID_INPUT` | Chapter 3's decision vocabulary. |
| (no store step) | ACCEPT: append audit entry, atomic write (temp file, fsync, rename), version + 1. HOLD: append audit entry, no write, store unchanged. | Chapter 3 commit protocol (§3.2.5, commit protocol paragraph; Algorithm 3.1 lines 7–8). |
| "Memory Architecture" / "Our Proposed Safety Layer" groups | "Host memory system" / "MemBud pre-commit safety layer (gate-only mode)" / "Guarded store and append-only audit log" | Chapter 3 terminology and the three boundaries the chapter distinguishes. |
| default white boxes | Palette from the Chapter 3 figures: neutral process, red `HOLD`/`INVALID_INPUT`, green `ACCEPT` | Consistency with figures 3.1–3.9. |

The updated file uses plain draw.io shapes (terminator, parallelogram, process, rhombus) and orthogonal edges with exit/entry anchors, so every node and label is editable in draw.io. The approximate preview is not part of the thesis; use the `.drawio` in draw.io and export SVG/PDF from there for the final document.

## If this is the wrong flowchart

This file is the **runtime verify-and-commit flow** (like Algorithm 3.1 / Figure 3.5). If by "methodology flowchart" you meant the **research-phases flow** in Figure 3.2 (seven phases, Gate A/B, development loop), that is already generated as `paper/figures/fig-3-2-research-flow.*`; tell me and I can convert that one into an editable `.drawio` instead.
