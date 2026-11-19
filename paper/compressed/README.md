# Compressed Chapter 3 for the 6-page paper

**Deliverable:** `chapter-3-compressed-double-column.docx`
**Source:** the user's Chapter 3 DOCX (`paper/source/chapter-3-implementation-method.docx`, 5,520 words).
**Compression:** ~1,000 words (from 5,520), one compact table, one figure → **estimated ~1.2–1.4 double-column pages**, leaving roughly 4.6–4.8 of the 6 pages for the other sections.

## Formatting

- US Letter, 0.75 in margins, **two columns** (0.2 in gutter), Times New Roman 10 pt, single spacing, justified.
- Title `3. Implementation Method` spans the page width (single-column continuous section); body flows in two columns.
- One table (`Table 1. Rule-based experimental arms`) and one figure (`Figure 1`, architecture, column width 3.2 in).
- Markdown source kept at `chapter-3-compressed.md` for editing.

## Content kept (and what was cut)

Kept: the thinking framework and RQ mapping; the 36-case instrument (18/18 split, category balance, single-reviewer labels, hash pins, no licensed data); preprocessing and schema-v2 invariants; the four arms incl. the text-fair information-parity control; decision policy; LLM/semantic options; adapter contract and reference-backend/feasibility scope (no third-party claim); evaluation metrics, as-completed vs fail-closed ITT, paired contrasts, descriptive-only inference, planned fresh set; threats.

Cut to fit: the 9-figure guide (only the architecture figure is kept), the long EDA table and its discussion, the full node/edge legend, Gate A/Gate B research-flow detail, the step-by-step Algorithm 3.1 listing, and most of the per-arm prose (replaced by Table 1).

## Regenerate

```sh
.venv/bin/python paper/tools/make_compressed_bab3_docx.py
```

## Notes / limits

- There is no Office renderer in this environment, so the **page count is an estimate**, not a verified count. Open the DOCX in Word and check; then tell me the observed pages and I will tune (drop the figure → ~1 page; add a second figure → ~1.5 pages; A4 vs Letter; IEEE/ACM/Springer template).
- The DOCX was validated structurally with `python-docx` (opens, 2 sections, 1 table, 1 image).
- If the paper is not the thesis chapter but a venue submission, say which template (IEEE two-column, ACM sigconf, etc.) and I will match its fonts, headings and reference style.
