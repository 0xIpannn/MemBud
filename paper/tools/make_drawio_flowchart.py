#!/usr/bin/env python3
"""Build the Chapter 3 runtime flowchart as an editable draw.io file.

Source of truth: Chapter 3 (DOCX, 2026-11-08), §3.2.5 "Modelling: The MemBud
Method" and Algorithm 3.1 "Verify-and-commit". This replaces the earlier
runtime sketch that still contained a REVISE loop (not implemented in the
prototype) and used Allow/Block labels instead of ACCEPT/HOLD/INVALID_INPUT.

Outputs (only these):
  paper/diagrams/methodology-flowchart-chapter3.drawio   editable draw.io (XML)
  paper/diagrams/methodology-flowchart-chapter3-preview.svg/.png  approximate preview
"""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "paper" / "diagrams"
OUT.mkdir(parents=True, exist_ok=True)

# ---- palette (same semantic colours as the Chapter 3 figures) -------------
INK = "#2b3a4a"
NEUTRAL_FILL = "#f5f7fa"
RED_FILL, RED_STROKE, RED_TEXT = "#fbeaea", "#b23b3b", "#7a2222"
GREEN_FILL, GREEN_STROKE, GREEN_TEXT = "#e8f2e8", "#2e7d32", "#1e5422"
BLUE_FILL, BLUE_STROKE = "#e8eff6", "#2f6f9f"
GREY = "#8a8a8a"

# kind: group | terminator | process | decision | io | note
NODES = [
    ("g_host", "group", "Host memory system", 30, 60, 790, 150),
    ("g_membud", "group", "MemBud pre-commit safety layer (gate-only mode)", 30, 225, 790, 650),
    ("g_store", "group", "Guarded store and append-only audit log", 30, 865, 790, 150),

    ("start", "terminator", "Start", 330, 78, 120, 42),
    ("host", "io", "Host proposes a memory update<br>(prior state + evidence + operations)", 250, 138, 280, 52),
    ("adapter", "process", "Adapter snapshot(scope)<br>returns canonical prior records", 270, 250, 240, 52),
    ("parse", "process", "Parse and validate transition<br>(schema version 2 invariants)", 270, 318, 240, 52),
    ("valid", "decision", "Schema<br>valid?", 310, 386, 160, 74),
    ("invalid", "error", "INVALID_INPUT<br>record for every arm<br>no verification, no write", 20, 392, 230, 66),
    ("reconcile", "decision", "Stored state<br>= T.before?", 310, 480, 160, 74),
    ("auditerr", "error", "Fail closed; log audit error<br>(no verification, no write)", 30, 486, 230, 66),
    ("nowrite", "stop", "End (no write)", 20, 585, 200, 44),
    ("build", "process", "Build canonical transition T = (B, C, E, R, O)<br>and two renderings of the same facts: graph G, text Z", 250, 578, 280, 62),
    ("verify", "process", "Verifier arm (graph or text; rules or LLM)<br>coverage, preservation, faithfulness (+ semantic)", 250, 656, 280, 62),
    ("pass", "decision", "All required<br>checks PASS?", 310, 740, 160, 74),
    ("accept", "accept", "ACCEPT<br>append audit entry;<br>atomic write (temp, fsync, rename), version + 1", 100, 900, 260, 76),
    ("hold", "hold", "HOLD<br>append audit entry; no write; store unchanged", 500, 900, 260, 76),
    ("end", "terminator", "End", 330, 1060, 120, 42),
    ("note", "note", "REVISE / retry is not implemented in this prototype; execution retries never authorise a commit.", 60, 1110, 730, 32),
]

# (source, target, label, exitX, exitY, entryX, entryY)
EDGES = [
    ("start", "host", "", 0.5, 1, 0.5, 0),
    ("host", "adapter", "", 0.5, 1, 0.5, 0),
    ("adapter", "parse", "", 0.5, 1, 0.5, 0),
    ("parse", "valid", "", 0.5, 1, 0.5, 0),
    ("valid", "invalid", "invalid", 0, 0.5, 1, 0.5),
    ("valid", "reconcile", "valid", 0.5, 1, 0.5, 0),
    ("reconcile", "auditerr", "mismatch", 0, 0.5, 1, 0.5),
    ("reconcile", "build", "match", 0.5, 1, 0.5, 0),
    ("build", "verify", "", 0.5, 1, 0.5, 0),
    ("verify", "pass", "", 0.5, 1, 0.5, 0),
    ("pass", "accept", "yes", 0, 0.5, 0.5, 0),
    ("pass", "hold", "no / UNCERTAIN", 1, 0.5, 0.5, 0),
    ("accept", "end", "", 0.5, 1, 0.25, 0),
    ("hold", "end", "", 0.5, 1, 0.75, 0),
    ("invalid", "nowrite", "", 0, 0.5, 0, 0.5),
    ("auditerr", "nowrite", "", 0.5, 1, 0.5, 0),
]


def node_style(kind: str) -> str:
    base = f"html=1;whiteSpace=wrap;strokeColor={INK};fontColor={INK};"
    if kind == "group":
        return (
            "rounded=1;dashed=1;dashPattern=8 8;fillColor=none;"
            f"strokeColor={GREY};align=left;verticalAlign=top;spacingLeft=10;"
            "spacingTop=6;fontStyle=1;fontColor=#555555;html=1;whiteSpace=wrap;"
        )
    if kind == "terminator":
        return "shape=mxgraph.flowchart.terminator;strokeWidth=2;" + base
    if kind == "stop":
        return (
            "shape=mxgraph.flowchart.terminator;strokeWidth=2;"
            f"fillColor={RED_FILL};strokeColor={RED_STROKE};fontColor={RED_TEXT};" + base
        )
    if kind == "io":
        return (
            "shape=parallelogram;perimeter=parallelogramPerimeter;fixedSize=1;size=0.15;"
            "strokeWidth=2;" + base
        )
    if kind == "decision":
        return "rhombus;strokeWidth=2;" + base + f"fillColor={NEUTRAL_FILL};"
    if kind == "note":
        return (
            "rounded=1;dashed=1;fillColor=#fffbe6;strokeColor=#b8a24a;"
            "align=center;fontSize=11;fontColor=#6b5a1a;whiteSpace=wrap;html=1;"
        )
    fill, stroke, text = {
        "error": (RED_FILL, RED_STROKE, RED_TEXT),
        "accept": (GREEN_FILL, GREEN_STROKE, GREEN_TEXT),
        "hold": (RED_FILL, RED_STROKE, RED_TEXT),
    }.get(kind, (NEUTRAL_FILL, INK, INK))
    return (
        f"rounded=1;absoluteArcSize=1;arcSize=14;strokeWidth=2;fillColor={fill};"
        f"strokeColor={stroke};fontColor={text};" + base
    )


def cell_vertex(cid, kind, value, x, y, w, h) -> str:
    return (
        f'<mxCell id={quoteattr(cid)} value={quoteattr(value)} style={quoteattr(node_style(kind))} '
        f'vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" /></mxCell>'
    )


def edge_style(ex, ey, nx, ny) -> str:
    return (
        "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
        "endArrow=block;endFill=1;strokeWidth=1.5;labelBackgroundColor=#ffffff;fontSize=10;"
        f"exitX={ex};exitY={ey};exitDx=0;exitDy=0;entryX={nx};entryY={ny};entryDx=0;entryDy=0;"
    )


def main() -> None:
    cells = ['<mxCell id="0" />', '<mxCell id="1" parent="0" />']
    for cid, kind, value, x, y, w, h in NODES:
        cells.append(cell_vertex(cid, kind, value, x, y, w, h))
    for i, (src, tgt, label, ex, ey, nx, ny) in enumerate(EDGES):
        eid = f"e{i}"
        cells.append(
            f'<mxCell id={quoteattr(eid)} value={quoteattr(label)} style={quoteattr(edge_style(ex, ey, nx, ny))} '
            f'edge="1" parent="1" source={quoteattr(src)} target={quoteattr(tgt)}>'
            '<mxGeometry relative="1" as="geometry" /></mxCell>'
        )
    model = (
        '<mxGraphModel grid="1" page="1" gridSize="10" guides="1" tooltips="1" connect="1" '
        'arrows="1" fold="1" pageScale="1" pageWidth="850" pageHeight="1170" math="0" shadow="0">'
        "<root>" + "".join(cells) + "</root></mxGraphModel>"
    )
    doc = (
        '<mxfile host="app.diagrams.net" type="device">'
        '<diagram name="Chapter 3 - verify and commit" id="membud-ch3-flow">'
        + model
        + "</diagram></mxfile>"
    )
    out = OUT / "methodology-flowchart-chapter3.drawio"
    out.write_text(doc + "\n", encoding="utf-8")
    ET.parse(out)  # validity gate
    print("wrote", out, "cells:", len(NODES) + len(EDGES) + 2)

    # ---- approximate SVG preview (draw.io itself is the exact renderer) ----
    def by_id(cid):
        return next(n for n in NODES if n[0] == cid)

    def cx(n):
        return n[3] + n[5] / 2

    def cy(n):
        return n[4] + n[6] / 2

    def anchor(n, ex, ey):
        x, y, w, h = n[3], n[4], n[5], n[6]
        if ex == 0:
            return x, y + ey * h
        if ex == 1:
            return x + w, y + ey * h
        return x + ex * w, y + ey * h

    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="850" height="1170" viewBox="0 0 850 1170" font-family="Helvetica, Arial, sans-serif">']
    svg.append('<defs><marker id="a" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto"><path d="M0,0 L10,4 L0,8 z" fill="#2b3a4a"/></marker></defs>')
    for cid, kind, value, x, y, w, h in NODES:
        if kind == "group":
            svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="none" stroke="#8a8a8a" stroke-dasharray="8 8"/>')
            svg.append(f'<text x="{x+10}" y="{y+18}" font-size="12" font-weight="600" fill="#555">{escape(value.split("<br>")[0])}</text>')
            continue
        if kind == "note":
            svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="#fffbe6" stroke="#b8a24a" stroke-dasharray="4 3"/>')
        elif kind == "decision":
            pts = f"{cx((cid,kind,value,x,y,w,h))},{y} {x+w},{cy((cid,kind,value,x,y,w,h))} {cx((cid,kind,value,x,y,w,h))},{y+h} {x},{cy((cid,kind,value,x,y,w,h))}"
            svg.append(f'<polygon points="{pts}" fill="{NEUTRAL_FILL}" stroke="{INK}" stroke-width="2"/>')
        elif kind == "io":
            off = 0.15 * w
            pts = f"{x+off},{y} {x+w},{y} {x+w-off},{y+h} {x},{y+h}"
            svg.append(f'<polygon points="{pts}" fill="{NEUTRAL_FILL}" stroke="{INK}" stroke-width="2"/>')
        elif kind in ("terminator", "stop"):
            fill, stroke = (RED_FILL, RED_STROKE) if kind == "stop" else (NEUTRAL_FILL, INK)
            svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="21" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        else:
            fill, stroke, text = {
                "error": (RED_FILL, RED_STROKE, RED_TEXT),
                "accept": (GREEN_FILL, GREEN_STROKE, GREEN_TEXT),
                "hold": (RED_FILL, RED_STROKE, RED_TEXT),
            }.get(kind, (NEUTRAL_FILL, INK, INK))
            svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        lines = value.replace("<br>", "\n").split("\n")
        fs = 11 if kind == "note" else 12
        for j, line in enumerate(lines):
            ty = y + h / 2 + (j - (len(lines) - 1) / 2) * (fs + 3) + fs / 3
            svg.append(f'<text x="{cx((cid,kind,value,x,y,w,h))}" y="{ty:.1f}" font-size="{fs}" text-anchor="middle" fill="{INK}">{escape(line)}</text>')
    for i, (src, tgt, label, ex, ey, nx, ny) in enumerate(EDGES):
        a, b = by_id(src), by_id(tgt)
        sx, sy = anchor(a, ex, ey)
        tx, ty = anchor(b, nx, ny)
        if abs(sx - tx) < 1:
            pts = [(sx, sy), (tx, ty)]
        elif ey in (0.5,) and ex in (1,):
            pts = [(sx, sy), (tx, sy), (tx, ty)]
        elif ex in (0, 1) and ey == 0.5:
            pts = [(sx, sy), (tx, sy), (tx, ty)]
        else:
            mid = (sy + ty) / 2
            pts = [(sx, sy), (sx, mid), (tx, mid), (tx, ty)]
        d = " L ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        svg.append(f'<path d="M {d}" fill="none" stroke="{INK}" stroke-width="1.5" marker-end="url(#a)"/>')
        if label:
            if len(pts) == 2:
                lx, ly = (pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2
            elif len(pts) == 3:
                lx, ly = (pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2
            else:
                lx, ly = (pts[1][0] + pts[2][0]) / 2, (pts[1][1] + pts[2][1]) / 2
            svg.append(f'<rect x="{lx-26}" y="{ly-9}" width="52" height="16" fill="white" opacity="0.95"/>')
            svg.append(f'<text x="{lx}" y="{ly+3.5}" font-size="10" text-anchor="middle" fill="{INK}">{escape(label)}</text>')
    svg.append("</svg>")
    svg_path = OUT / "methodology-flowchart-chapter3-preview.svg"
    svg_path.write_text("\n".join(svg) + "\n", encoding="utf-8")
    subprocess.run(
        ["rsvg-convert", "-w", "1700", "-o", str(OUT / "methodology-flowchart-chapter3-preview.png"), str(svg_path)],
        check=True,
    )
    print("wrote preview svg/png")


if __name__ == "__main__":
    main()
