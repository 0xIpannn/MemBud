#!/usr/bin/env python3
"""Generate the nine Chapter 3 figures from the DOCX figure guide.

Outputs (all under ``paper/figures/``):
  fig-3-N-<slug>.svg  hand-authored vector source
  fig-3-N-<slug>.png  raster preview, 2x the SVG pixel size
  fig-3-N-<slug>.pdf  vector PDF for the thesis
  README.md           index of figures, captions and files

Palette (one colour per concept, shared by every figure):
  graph #2f6f9f | text #c07020 | unguarded #7a7a7a
  HOLD / INVALID_INPUT #b23b3b | ACCEPT #2e7d32
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "paper" / "figures"
RENDER = OUT  # renders live next to the SVG sources

# --------------------------------------------------------------------------
# palette and typography
# --------------------------------------------------------------------------
GRAPH = "#2f6f9f"
TEXT = "#c07020"
UNGUARDED = "#7a7a7a"
RED = "#b23b3b"
GREEN = "#2e7d32"

INK = "#1d2b3a"
STROKE = "#2b3a4a"
MUTED = "#55657a"
FILL = "#f5f7fa"
WHITE = "#ffffff"
GRID = "#c9d2dc"

BLUE_T = "#e8f0f7"
ORANGE_T = "#f8efe3"
GREEN_T = "#e7f2e8"
RED_T = "#f7e8e8"
GREY_T = "#eef0f3"
IMPOSSIBLE_T = "#e6e9ee"

FONT = "Helvetica, Arial, 'DejaVu Sans', sans-serif"

MARKER_COLORS = {
    STROKE: "arr-neutral",
    GRAPH: "arr-graph",
    TEXT: "arr-text",
    RED: "arr-red",
    GREEN: "arr-green",
    UNGUARDED: "arr-grey",
}


def esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class Fig:
    """Tiny SVG authoring helper."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.parts: list[str] = []

    # -- primitives ------------------------------------------------------
    def raw(self, s: str) -> None:
        self.parts.append(s)

    def rect(self, x, y, w, h, *, fill=FILL, stroke=STROKE, lw=2.0,
             rx=10, dashed=False, opacity=None):
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        op = f' opacity="{opacity}"' if opacity is not None else ""
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{lw}"{dash}{op}/>'
        )

    def polygon(self, pts, *, fill=FILL, stroke=STROKE, lw=2.0, dashed=False):
        d = " ".join(f"{x},{y}" for x, y in pts)
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        self.parts.append(
            f'<polygon points="{d}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{lw}" stroke-linejoin="round"{dash}/>'
        )

    def text(self, x, y, s, *, size=12, color=INK, anchor="middle",
             weight="normal", rotate=None, italic=False):
        tr = f' transform="rotate({rotate} {x} {y})"' if rotate else ""
        it = ' font-style="italic"' if italic else ""
        self.parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'text-anchor="{anchor}" font-weight="{weight}"{tr}{it}>{esc(s)}</text>'
        )

    def text_block(self, cx, cy, lines, *, default_size=12,
                   default_color=INK, anchor="middle"):
        """lines: list of dicts {t, size?, color?, weight?, italic?} or strings."""
        norm = []
        for ln in lines:
            if isinstance(ln, str):
                norm.append({"t": ln, "size": default_size,
                             "color": default_color, "weight": "normal"})
            else:
                norm.append({
                    "t": ln["t"],
                    "size": ln.get("size", default_size),
                    "color": ln.get("color", default_color),
                    "weight": ln.get("weight", "normal"),
                    "italic": ln.get("italic", False),
                })
        total = sum(l["size"] * 1.35 for l in norm)
        y = cy - total / 2
        for l in norm:
            y += l["size"] * 1.05
            self.text(cx, y, l["t"], size=l["size"], color=l["color"],
                      anchor=anchor, weight=l["weight"], italic=l["italic"])
            y += l["size"] * 0.30

    def box(self, x, y, w, h, title, subs=(), *, stroke=STROKE, fill=FILL,
            title_color=INK, sub_color=MUTED, size=13.5, sub_size=11.5,
            rx=10, lw=2.0, dashed=False):
        self.rect(x, y, w, h, fill=fill, stroke=stroke, lw=lw, rx=rx,
                  dashed=dashed)
        lines = [{"t": title, "size": size, "color": title_color,
                  "weight": "600"}]
        for s in subs:
            lines.append({"t": s, "size": sub_size, "color": sub_color})
        self.text_block(x + w / 2, y + h / 2, lines)

    def diamond(self, cx, cy, w, h, title, subs=(), *, stroke=STROKE,
                fill=FILL, title_color=INK, sub_color=MUTED,
                size=13.5, sub_size=10.5, lw=2.0):
        pts = [(cx, cy - h / 2), (cx + w / 2, cy),
               (cx, cy + h / 2), (cx - w / 2, cy)]
        self.polygon(pts, fill=fill, stroke=stroke, lw=lw)
        lines = [{"t": title, "size": size, "color": title_color,
                  "weight": "600"}]
        for s in subs:
            lines.append({"t": s, "size": sub_size, "color": sub_color})
        self.text_block(cx, cy, lines)

    def ellipse(self, cx, cy, rx, ry, title, subs=(), *, stroke=STROKE,
                fill=FILL, color=INK, size=12, sub_size=10, lw=2.0,
                dashed=False):
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        self.parts.append(
            f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{lw}"{dash}/>'
        )
        lines = [{"t": title, "size": size, "color": color,
                  "weight": "600"}]
        for s in subs:
            lines.append({"t": s, "size": sub_size, "color": color})
        self.text_block(cx, cy, lines)

    def parallelogram(self, cx, cy, w, h, title, *, stroke=STROKE, fill=FILL,
                      color=INK, size=11.5, lw=2.0, dashed=False):
        x, y = cx - w / 2, cy - h / 2
        sk = 16
        pts = [(x + sk, y), (x + w, y), (x + w - sk, y + h), (x, y + h)]
        self.polygon(pts, fill=fill, stroke=stroke, lw=lw, dashed=dashed)
        self.text_block(cx, cy, [{"t": title, "size": size, "color": color,
                                  "weight": "600"}])

    def hexagon(self, cx, cy, w, h, title, subs=(), *, stroke=STROKE,
                fill=FILL, color=INK, size=11.5, sub_size=9.5, lw=2.0,
                dashed=False):
        x, y = cx - w / 2, cy - h / 2
        sk = 16
        pts = [(x + sk, y), (x + w - sk, y), (x + w, cy),
               (x + w - sk, y + h), (x + sk, y + h), (x, cy)]
        self.polygon(pts, fill=fill, stroke=stroke, lw=lw, dashed=dashed)
        lines = [{"t": title, "size": size, "color": color,
                  "weight": "600"}]
        for s in subs:
            lines.append({"t": s, "size": sub_size, "color": color})
        self.text_block(cx, cy, lines)

    # -- links -----------------------------------------------------------
    def line(self, pts, *, color=STROKE, lw=1.9, dashed=False):
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        d = "M " + " L ".join(f"{x} {y}" for x, y in pts)
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{lw}"'
            f'{dash} stroke-linejoin="round" stroke-linecap="round"/>'
        )

    def path(self, d, *, color=STROKE, lw=1.9, dashed=False, arrow=True):
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        marker = f' marker-end="url(#{MARKER_COLORS[color]})"' if arrow else ""
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{lw}"'
            f'{dash} stroke-linejoin="round" stroke-linecap="round"{marker}/>'
        )

    def arrow(self, pts, *, color=STROKE, lw=1.9, dashed=False):
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        d = "M " + " L ".join(f"{x} {y}" for x, y in pts)
        marker = f' marker-end="url(#{MARKER_COLORS[color]})"'
        self.parts.append(
            f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{lw}"'
            f'{dash} stroke-linejoin="round" stroke-linecap="round"{marker}/>'
        )

    # -- braces ----------------------------------------------------------
    def hbrace(self, x1, x2, y, *, color=STROKE, tick=10, label=None,
               label_dy=22, size=11, label_color=None):
        d = (f"M {x1} {y - tick} L {x1} {y} L {x2} {y} L {x2} {y - tick}")
        self.line([(x1, y - tick), (x1, y), (x2, y), (x2, y - tick)],
                  color=color, lw=1.6)
        cx = (x1 + x2) / 2
        self.line([(cx, y), (cx, y + 6)], color=color, lw=1.6)
        if label:
            self.text(cx, y + label_dy, label, size=size,
                      color=label_color or color)

    def vbrace(self, y1, y2, x, *, color=STROKE, tick=10, label=None,
               label_dx=16, size=11, label_color=None):
        self.line([(x + tick, y1), (x, y1), (x, y2), (x + tick, y2)],
                  color=color, lw=1.6)
        cy = (y1 + y2) / 2
        self.line([(x, cy), (x - 6, cy)], color=color, lw=1.6)
        if label:
            self.text(x - label_dx, cy, label, size=size,
                      color=label_color or color, rotate=-90)

    # -- output ----------------------------------------------------------
    def svg(self) -> str:
        head = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" '
            f'height="{self.h}" viewBox="0 0 {self.w} {self.h}" '
            f'font-family="{FONT}">',
            "<defs>",
        ]
        for color, mid in MARKER_COLORS.items():
            head.append(
                f'<marker id="{mid}" markerWidth="9" markerHeight="7" '
                f'refX="8" refY="3.5" orient="auto" markerUnits="strokeWidth">'
                f'<path d="M0,0 L9,3.5 L0,7 z" fill="{color}"/></marker>'
            )
        head.append("</defs>")
        head.append(f'<rect x="0" y="0" width="{self.w}" height="{self.h}" '
                    f'fill="{WHITE}"/>')
        return "\n".join(head + self.parts + ["</svg>", ""])


# ==========================================================================
# 3.1  Thinking framework
# ==========================================================================
def fig_3_1() -> Fig:
    f = Fig(1100, 300)
    xs = [25, 240, 455, 670, 885]
    w, h, y = 190, 112, 42
    titles = ["Problem", "Gap", "Intervention (MemBud)",
              "Controlled test", "Evidence"]
    subs = [
        ["Memory updates can", "persist errors"],
        ["No pre-commit check", "of transitions"],
        ["Guarded", "verify-before-commit"],
        ["Paired arms on", "frozen packages"],
        ["Descriptive counts,", "costs and limits"],
    ]
    for i, x in enumerate(xs):
        f.box(x, y, w, h, titles[i], subs[i], size=13.5, sub_size=11.5)
        if i < 4:
            f.arrow([(x + w + 3, y + h / 2), (xs[i + 1] - 4, y + h / 2)])

    # RQ mapping brackets beneath the boxes they belong to.
    for cx, label in [(xs[2] + w / 2, "RQ2: modularity"),
                      (xs[3] + w / 2, "RQ1: reliability"),
                      (xs[4] + w / 2, "RQ3: trade-offs")]:
        f.hbrace(cx - 62, cx + 62, 200, label=label, label_dy=24, size=11.5)
    return f


# ==========================================================================
# 3.2  Research flow with gates
# ==========================================================================
def fig_3_2() -> Fig:
    f = Fig(1100, 600)
    y1, h1 = 50, 80
    xs = [30, 270, 510, 750]
    titles = [["1. Literature", "and RQs"],
              ["2. Case construction", "and annotation"],
              ["3. Prototype"], ["4. Development runs"]]
    for x, t in zip(xs, titles):
        f.box(x, y1, 200, h1, t[0], t[1:], size=13)
        if x != xs[-1]:
            f.arrow([(x + 203, y1 + h1 / 2), (x + 237, y1 + h1 / 2)])

    # development loop: phase 4 back to phase 3 (over the top)
    f.path(f"M {xs[3] + 100} {y1} C {xs[3] + 100} 10, "
           f"{xs[2] + 100} 10, {xs[2] + 100} {y1 - 4}")
    f.text(730, 20, "iterate on the development split", size=11,
           color=MUTED)

    # Gate A after the development runs
    f.arrow([(xs[3] + 100, y1 + h1), (xs[3] + 100, 205),
             (330, 205), (330, 228)])
    f.diamond(330, 285, 260, 132, "Gate A",
              ["freeze manifest,", "prompts, policy"], size=14,
              sub_size=9.5)

    # Gate B before the final run
    f.arrow([(460, 285), (637, 285)])
    f.diamond(770, 285, 260, 132, "Gate B",
              ["no tuning after", "freeze"], size=14, sub_size=9.5)

    # final run, analysis, reporting
    xs3 = [190, 430, 670]
    f.arrow([(770, 341), (770, 380), (xs3[0] + 100, 380),
             (xs3[0] + 100, 413)])
    t2 = [["5. Held-out / fresh run"], ["6. Analysis"], ["7. Reporting"]]
    for x, t in zip(xs3, t2):
        f.box(x, 415, 200, 80, t[0], size=13)
        if x != xs3[-1]:
            f.arrow([(x + 203, 455), (x + 237, 455)])
    return f


# ==========================================================================
# 3.3  Preprocessing pipeline (vertical) with INVALID_INPUT exit
# ==========================================================================
def fig_3_3() -> Fig:
    f = Fig(1000, 780)
    cx = 380
    steps = [
        (40, "Raw case"),
        (122, "Parse"),
        (204, "Normalise"),
        (286, "Validate (schema v2)"),
    ]
    for i, (y, label) in enumerate(steps):
        f.box(cx - 160, y, 320, 58, label, size=13.5)
        if i:
            f.arrow([(cx, y - 24), (cx, y - 4)])

    # validation diamond
    f.arrow([(cx, 344), (cx, 358)])
    f.diamond(cx, 410, 260, 104, "Schema valid?", size=13.5)

    # invalid branch -> INVALID_INPUT
    f.arrow([(cx + 132, 410), (716, 410)], color=RED, lw=2.1)
    f.text(560, 401, "invalid", size=11.5, color=RED, weight="600")
    f.box(722, 380, 230, 60, "INVALID_INPUT", stroke=RED, fill=RED_T,
          title_color=RED, size=13.5)
    f.text(837, 464, "no verification, no write", size=11, color=RED)

    # valid branch -> prior-state check
    f.text(397, 474, "valid", size=11.5, color=GREEN, weight="600")
    f.arrow([(cx, 462), (cx, 496)])
    f.box(cx - 190, 500, 380, 66, "Check store against prior state",
          ["stored state = transition prior state"], size=12.5)

    # split into the two renderings
    f.line([(cx, 566), (cx, 606)])
    f.arrow([(cx, 606), (300, 646)], color=GRAPH, lw=2.1)
    f.arrow([(cx, 606), (660, 646)], color=TEXT, lw=2.1)
    f.box(150, 650, 300, 70, "Graph rendering", ["structural graph"],
          stroke=GRAPH, fill=BLUE_T, title_color=GRAPH, size=13)
    f.box(510, 650, 300, 70, "Text rendering", ["claim-set text"],
          stroke=TEXT, fill=ORANGE_T, title_color=TEXT, size=13)
    f.text(480, 752, "identical content, two renderings", size=11.5,
           color=MUTED)
    return f


# ==========================================================================
# 3.4  Data splits over time (swimlanes + Gate A)
# ==========================================================================
def fig_3_4() -> Fig:
    f = Fig(1100, 500)
    lanes = [
        (80, "Development", "tuning allowed"),
        (180, "Held-out v2", "used for prompt selection"),
        (280, "Fresh set", "single run after Gate A"),
    ]
    for ly, name, _note in lanes:
        f.rect(200, ly, 860, 70, fill=WHITE, stroke=GRID, lw=1.4, rx=8)
        f.rect(30, ly, 155, 70, fill=FILL, stroke=STROKE, lw=1.8)
        f.text(107, ly + 36, name, size=13, weight="600")
        f.text(107, ly + 54, "split", size=10, color=MUTED)

    # Development: tuning allowed
    f.rect(220, 95, 340, 40, fill=FILL, stroke=STROKE, lw=1.8, rx=6)
    f.text(390, 121, "tuning allowed (development only)", size=11.5,
           color=INK)

    # Held-out v2: prompt selection, flag attached to the box
    f.rect(600, 196, 196, 46, fill=ORANGE_T, stroke=TEXT, lw=1.8, rx=6)
    f.text(698, 216, "used for prompt selection", size=11, color=TEXT)
    f.text(698, 233, "flagged", size=10, color=RED, weight="600")
    f.line([(604, 198), (604, 182)], color=RED, lw=1.8)
    f.polygon([(604, 182), (626, 188), (604, 194)], fill=RED, stroke=RED,
              lw=1)

    # Gate A line
    f.line([(800, 66), (800, 402)], color=STROKE, lw=2.0, dashed=True)
    f.text(800, 52, "Gate A: freeze manifest, prompts, policy", size=12,
           weight="600")
    f.line([(800, 66), (800, 76)], color=STROKE, lw=1.6)

    # Fresh set: frozen at Gate A, one run
    f.rect(820, 288, 160, 40, fill=FILL, stroke=STROKE, lw=1.8, rx=6)
    f.text(900, 314, "single run", size=11.5, color=INK)
    f.text(900, 342, "after Gate A; never tuned", size=9.5, color=MUTED)

    # time axis
    f.arrow([(200, 420), (1060, 420)], color=STROKE)
    f.text(1060, 440, "time", size=11.5, color=MUTED, anchor="end")
    return f


# ==========================================================================
# 3.5  MemBud architecture
# ==========================================================================
def fig_3_5() -> Fig:
    f = Fig(1100, 700)
    # top row: host -> adapter -> transition package
    top_y, h = 40, 70
    f.box(30, top_y, 200, h, "Host memory system", size=12.5)
    f.box(270, top_y, 190, h, "Proposed operations", size=12)
    f.box(500, top_y, 190, h, "Backend adapter", size=12.5)
    f.box(730, top_y, 300, h, "Transition package",
          ["prior state, candidate, evidence,", "claims, operations"],
          size=12.5, sub_size=10.5)
    f.arrow([(233, 75), (267, 75)])
    f.arrow([(463, 75), (497, 75)])
    f.arrow([(693, 75), (727, 75)])

    # parallel renderings and verifiers
    f.arrow([(820, 110), (680, 170)])
    f.arrow([(940, 110), (950, 170)])
    f.box(560, 172, 200, 62, "Graph builder", stroke=GRAPH, fill=BLUE_T,
          title_color=GRAPH, size=12.5)
    f.box(850, 172, 200, 62, "Text renderer", stroke=TEXT, fill=ORANGE_T,
          title_color=TEXT, size=12.5)
    f.arrow([(660, 234), (660, 268)], color=GRAPH)
    f.arrow([(950, 234), (950, 268)], color=TEXT)
    f.box(560, 270, 200, 62, "Graph verifier", stroke=GRAPH, fill=BLUE_T,
          title_color=GRAPH, size=12.5)
    f.box(850, 270, 200, 62, "Text verifier", stroke=TEXT, fill=ORANGE_T,
          title_color=TEXT, size=12.5)

    # decision policy
    f.arrow([(660, 332), (610, 378)], color=STROKE)
    f.arrow([(950, 332), (720, 378)], color=STROKE)
    f.box(430, 380, 300, 70, "Decision policy",
          ["commit only if every check passes"], size=13, sub_size=11)

    # ACCEPT -> store (green)
    f.arrow([(430, 415), (300, 415), (300, 496)], color=GREEN, lw=2.2)
    f.text(352, 405, "ACCEPT", size=12, color=GREEN, weight="600")
    f.box(170, 500, 230, 70, "Store", ["versioned JSON, version + 1"],
          stroke=GREEN, fill=GREEN_T, title_color=GREEN, size=13)

    # dashed "no write before decision" boundary above the store
    f.line([(140, 478), (460, 478)], color=MUTED, lw=1.6, dashed=True)
    f.text(318, 470, "no store write before the decision", size=10,
           color=MUTED, anchor="start")

    # HOLD -> audit only (red)
    f.arrow([(650, 450), (650, 598)], color=RED, lw=2.2)
    f.text(662, 520, "HOLD: no write", size=12, color=RED, weight="600",
           anchor="start")

    # audit log spans the bottom
    f.box(30, 600, 1040, 66, "Audit log (append-only)",
          ["every proposal, decision and write is recorded"], size=13,
          sub_size=11, stroke=STROKE, fill=FILL)
    f.arrow([(300, 570), (300, 598)], color=STROKE, lw=1.7)
    f.text(330, 590, "audit entry", size=10, color=MUTED, anchor="start")
    return f


# ==========================================================================
# 3.6  Canonical graph schema + valid / corrupted examples
# ==========================================================================
def fig_3_6() -> Fig:
    f = Fig(1180, 820)
    # ---------------- left: node shapes + edge types -------------------
    f.text(30, 32, "Node shapes (5)", size=14.5, weight="600", anchor="start")
    shapes = [
        ("rounded rect", "transition (root)"),
        ("rect", "record (prior / candidate)"),
        ("ellipse", "claim (atomic, shared)"),
        ("parallelogram", "evidence (source excerpt)"),
        ("hexagon", "operation (WRITE / REVISE / DELETE / SUPERSEDE)"),
    ]
    ys = [78, 136, 194, 252, 312]
    for (kind, label), y in zip(shapes, ys):
        if kind == "rect":
            f.rect(40, y - 20, 120, 40, rx=4)
        elif kind == "ellipse":
            f.ellipse(100, y, 60, 20, "")
        elif kind == "parallelogram":
            f.parallelogram(100, y, 120, 38, "")
        elif kind == "hexagon":
            f.hexagon(100, y, 120, 38, "")
        else:
            f.rect(40, y - 20, 120, 40, rx=16, fill=BLUE_T, stroke=GRAPH,
                   lw=2.2)
        f.text(180, y + 5, label, size=11.5, anchor="start")

    f.text(30, 366, "Edge types (12)  —  from → to", size=14.5, weight="600",
           anchor="start")
    edges = [
        ("BEFORE", "transition → prior record", STROKE),
        ("PROPOSES", "transition → candidate; operation → candidate", STROKE),
        ("CONTAINS", "record → claim", STROKE),
        ("HAS_EVIDENCE", "transition → evidence", STROKE),
        ("SUPPORTS", "evidence → claim", STROKE),
        ("CONTRADICTS", "evidence → claim (flag)", RED),
        ("REQUIRES", "transition → required claim", STROKE),
        ("HAS_OPERATION", "transition → operation", STROKE),
        ("REVISES", "operation → prior record", STROKE),
        ("DELETES", "operation → prior record", STROKE),
        ("SUPERSEDES", "operation → prior record", STROKE),
        ("AUTHORIZED_BY", "operation → evidence", STROKE),
    ]
    for i, (name, desc, col) in enumerate(edges):
        y = 400 + i * 34
        f.arrow([(35, y), (88, y)], color=col, lw=1.5)
        f.text(100, y + 4, f"{name}  —  {desc}", size=10.5, anchor="start",
               color=col if col == RED else INK)

    # ---------------- right: valid example ------------------------------
    f.text(500, 32, "Valid update  →  ACCEPT", size=14.5, weight="600",
           anchor="start", color=GREEN)
    f.rect(1050, 16, 110, 30, fill=GREEN_T, stroke=GREEN, lw=2, rx=15)
    f.text(1105, 36, "ACCEPT", size=12, color=GREEN, weight="600")

    f.rect(760, 70, 160, 42, rx=18, fill=BLUE_T, stroke=GRAPH, lw=2.2)
    f.text(840, 96, "T (transition)", size=11.5, color=GRAPH)
    f.rect(555, 150, 130, 52, rx=6)
    f.text(620, 172, "R1 prior", size=11.5)
    f.text(620, 189, "record", size=10, color=MUTED)
    f.rect(985, 150, 130, 52, rx=6)
    f.text(1050, 172, "R2 candidate", size=11.5)
    f.text(1050, 189, "record", size=10, color=MUTED)
    f.ellipse(700, 258, 56, 25, "C1", size=11.5)
    f.ellipse(1050, 258, 56, 25, "C2 (new)", size=11)
    f.parallelogram(920, 344, 140, 38, "E1 evidence", size=11)
    f.hexagon(620, 344, 150, 42, "OP1 (WRITE)", size=11)

    f.arrow([(775, 108), (665, 146)], color=STROKE)
    f.text(700, 122, "BEFORE", size=9.5, color=MUTED)
    f.arrow([(905, 108), (1000, 146)])
    f.text(970, 122, "PROPOSES", size=9.5, color=MUTED)
    f.arrow([(620, 202), (620, 232)])
    f.text(648, 224, "CONTAINS", size=9.5, color=MUTED, anchor="start")
    f.arrow([(985, 202), (742, 240)])
    f.text(845, 215, "CONTAINS", size=9.5, color=MUTED)
    f.arrow([(1050, 202), (1050, 232)])
    f.text(1062, 222, "CONTAINS", size=9.5, color=MUTED, anchor="start")
    f.arrow([(870, 110), (905, 322)], color=STROKE)
    f.text(908, 200, "HAS_EVIDENCE", size=9.5, color=MUTED, anchor="start")
    f.arrow([(975, 322), (1032, 284)])
    f.text(950, 302, "SUPPORTS", size=9.5, color=MUTED, anchor="end")
    f.arrow([(780, 112), (650, 322)])
    f.text(655, 295, "HAS_OPERATION", size=9.5, color=MUTED, anchor="end")
    f.arrow([(695, 332), (990, 202)])
    f.text(840, 258, "PROPOSES", size=9.5, color=MUTED)

    # ---------------- right: corrupted example --------------------------
    f.text(500, 430, "Corrupted update  →  HOLD", size=14.5, weight="600",
           anchor="start", color=RED)
    f.rect(1053, 414, 105, 30, fill=RED_T, stroke=RED, lw=2, rx=15)
    f.text(1105, 434, "HOLD", size=12.5, color=RED, weight="600")

    f.rect(760, 462, 160, 42, rx=18, fill=BLUE_T, stroke=GRAPH, lw=2.2)
    f.text(840, 488, "T (transition)", size=11.5, color=GRAPH)
    f.rect(555, 540, 130, 52, rx=6)
    f.text(620, 562, "R1 prior", size=11.5)
    f.text(620, 579, "record", size=10, color=MUTED)
    f.rect(985, 540, 130, 52, rx=6)
    f.text(1050, 562, "R2 candidate", size=11.5)
    f.text(1050, 579, "record", size=10, color=MUTED)
    f.ellipse(585, 648, 50, 24, "C1", size=11.5)
    f.ellipse(720, 648, 54, 24, "C2", size=11.5, dashed=True, stroke=RED,
              color=RED)
    f.ellipse(1050, 648, 50, 24, "C1", size=11.5)

    f.arrow([(775, 500), (665, 538)])
    f.text(700, 514, "BEFORE", size=9.5, color=MUTED)
    f.arrow([(905, 500), (1000, 538)])
    f.text(970, 514, "PROPOSES", size=9.5, color=MUTED)
    f.arrow([(605, 592), (595, 622)])
    f.text(560, 612, "CONTAINS", size=9.5, color=MUTED, anchor="end")
    f.arrow([(645, 592), (705, 626)])
    f.text(700, 608, "CONTAINS", size=9.5, color=MUTED, anchor="start")
    f.arrow([(1035, 592), (1035, 624)])
    f.text(1060, 612, "CONTAINS", size=9.5, color=MUTED, anchor="start")

    # dropped claim: red X and explanation, no operation node
    f.hexagon(620, 730, 150, 40, "no operation", size=10.5, dashed=True,
              stroke=RED, fill=WHITE, color=RED)
    f.line([(596, 706), (644, 754)], color=RED, lw=2.6)
    f.line([(644, 706), (596, 754)], color=RED, lw=2.6)
    f.text(700, 716, "prior claim C2 dropped with no operation", size=11,
           color=RED, anchor="start")
    f.text(700, 748, "→ preservation fails  →  HOLD", size=11, color=RED,
           anchor="start")
    return f


# ==========================================================================
# 3.7  Commit sequence (lifelines + ACCEPT/HOLD alternatives)
# ==========================================================================
def fig_3_7() -> Fig:
    f = Fig(1100, 700)
    lifelines = [(100, "Host"), (300, "Adapter"), (500, "MemBud verifier"),
                 (750, "Store"), (950, "Audit log")]
    for x, name in lifelines:
        f.box(x - 72, 34, 144, 48, name, size=12, rx=8)
        f.line([(x, 82), (x, 646)], color=MUTED, lw=1.3, dashed=True)

    def msg(y, x1, x2, label, *, color=STROKE, dashed=False, size=11,
            dy=-6):
        f.arrow([(x1, y), (x2, y)], color=color, dashed=dashed)
        f.text((x1 + x2) / 2, y + dy, label, size=size, color=color)

    msg(120, 100, 300, "propose update")
    msg(165, 300, 750, "snapshot(scope)")
    msg(195, 750, 300, "prior records", dashed=True)
    msg(240, 300, 500, "build transition package")
    # verify self-message
    f.path("M 500 270 L 560 270 L 560 290 L 508 290")
    f.text(568, 284, "verify: coverage, preservation, faithfulness",
           size=10.5, color=STROKE, anchor="start")
    msg(330, 500, 300, "decision: ACCEPT or HOLD", size=11)

    # alt frame
    f.rect(46, 352, 1004, 296, fill=WHITE, stroke=MUTED, lw=1.6, rx=6,
           dashed=True)
    f.text(60, 370, "alt", size=12, weight="600", color=INK, anchor="start")

    # ACCEPT compartment
    f.text(68, 398, "[ACCEPT]", size=11.5, weight="600", color=GREEN,
           anchor="start")
    msg(420, 300, 950, "append audit entry")
    msg(470, 300, 750, "atomic write: temp file, fsync, rename", color=GREEN)
    f.path("M 750 492 L 810 492 L 810 512 L 758 512", color=GREEN)
    f.text(818, 506, "version + 1", size=10.5, color=GREEN, anchor="start")
    f.line([(46, 530), (1050, 530)], color=MUTED, lw=1.2, dashed=True)

    # HOLD compartment
    f.text(68, 552, "[HOLD]", size=11.5, weight="600", color=RED,
           anchor="start")
    msg(578, 300, 950, "append audit entry")
    msg(616, 300, 750, "no write; store unchanged", color=RED, dashed=True)
    f.line([(735, 601), (765, 631)], color=RED, lw=2.4)
    f.line([(765, 601), (735, 631)], color=RED, lw=2.4)
    return f


# ==========================================================================
# 3.8  Experimental arms (fan-out with "same information" bracket)
# ==========================================================================
def fig_3_8() -> Fig:
    f = Fig(1180, 620)
    f.box(30, 240, 200, 100, "Frozen transition package",
          ["prior state, candidate,", "evidence, claims, operations"],
          size=12, sub_size=9.5)

    lanes = [
        (62, "Unguarded", ["applies candidate", "without checks"],
         UNGUARDED, GREY_T),
        (152, "Text", ["claim sets; no", "contradiction access"],
         TEXT, ORANGE_T),
        (262, "Text-fair", ["claim sets +", "contradiction access"],
         TEXT, ORANGE_T),
        (352, "Graph", ["adjacency +", "contradiction access"],
         GRAPH, BLUE_T),
    ]
    for y, title, subs, col, fill in lanes:
        f.box(380, y, 320, 72, title, subs, stroke=col, fill=fill,
              title_color=col, size=12.5, sub_size=10.5)
        f.arrow([(233, 290), (377, y + 36)], color=col, lw=1.7)

    # "same information" bracket on the right edge of Text-fair + Graph
    f.vbrace(250, 432, 782, color=STROKE, tick=10, label=None)
    f.text(800, 341, "same information", size=11.5, weight="600", rotate=-90)

    # all guarded lanes meet at one decision policy (below the lanes)
    f.box(380, 470, 260, 82, "Decision policy",
          ["ACCEPT only if every check passes"], size=12.5, sub_size=10.5)
    f.arrow([(700, 188), (748, 188), (748, 512), (642, 512)], color=TEXT,
            lw=1.8)
    f.arrow([(700, 298), (730, 298), (730, 492), (642, 492)], color=TEXT,
            lw=1.8)
    f.arrow([(700, 412), (712, 412), (712, 524), (642, 524)], color=GRAPH,
            lw=1.8)
    f.text(800, 560, "all guarded arms", size=10, color=MUTED, anchor="start")

    # unguarded bypass: no verifier, no decision policy
    f.box(800, 80, 250, 86, "No verification",
          ["candidate applied directly"], stroke=UNGUARDED, fill=GREY_T,
          title_color=UNGUARDED, size=12.5, sub_size=10.5)
    f.arrow([(700, 106), (798, 120)], color=UNGUARDED, lw=1.8)
    return f



# ==========================================================================
# 3.9  Decision matrix
# ==========================================================================
def fig_3_9() -> Fig:
    f = Fig(1100, 640)
    col_x = [240, 480, 720]
    col_w = 240
    row_y = [140, 270, 400]
    row_h = 130

    f.text(140, 108, "gold class ↓", size=12, weight="600")
    f.text(140, 126, "decision →", size=12, weight="600")
    heads = [("ACCEPT", GREEN), ("HOLD", RED), ("INVALID_INPUT", RED)]
    for (label, col), x in zip(heads, col_x):
        f.rect(x, 90, col_w, 50, fill=FILL, stroke=STROKE, lw=1.8, rx=6)
        f.text(x + col_w / 2, 121, label, size=13, color=col, weight="600")

    rows = [("Valid", "labelled ACCEPT"), ("Faulty", "labelled HOLD"),
            ("Invalid input", "construction failure")]
    for (name, sub), y in zip(rows, row_y):
        f.rect(40, y, 200, row_h, fill=FILL, stroke=STROKE, lw=1.8, rx=6)
        f.text(140, y + row_h / 2 - 6, name, size=13.5, weight="600")
        f.text(140, y + row_h / 2 + 16, sub, size=10.5, color=MUTED)

    def cell(x, y, lines, *, fill, stroke, color, size=12, sub_size=10.5):
        f.rect(x + 4, y + 4, col_w - 8, row_h - 8, fill=fill, stroke=stroke,
               lw=1.8, rx=6)
        text_lines = [{"t": lines[0], "size": size, "color": color,
                       "weight": "600"}]
        for s in lines[1:]:
            text_lines.append({"t": s, "size": sub_size, "color": color})
        f.text_block(x + col_w / 2, y + row_h / 2, text_lines)

    # Valid row
    cell(col_x[0], row_y[0], ["valid acceptance", "correct commit"],
         fill=GREEN_T, stroke=GREEN, color=GREEN)
    cell(col_x[1], row_y[0], ["false hold", "error: valid update held"],
         fill=RED_T, stroke=RED, color=RED)
    cell(col_x[2], row_y[0], ["impossible", "schema-valid case"],
         fill=IMPOSSIBLE_T, stroke=GRID, color=MUTED)
    # Faulty row
    cell(col_x[1], row_y[1], ["detection", "true positive"],
         fill=GREEN_T, stroke=GREEN, color=GREEN)
    cell(col_x[0], row_y[1], ["false accept", "error: faulty update committed"],
         fill=RED_T, stroke=RED, color=RED)
    cell(col_x[2], row_y[1], ["impossible", "not a construction failure"],
         fill=IMPOSSIBLE_T, stroke=GRID, color=MUTED)
    # Invalid row
    cell(col_x[2], row_y[2], ["correct invalid handling",
                              "separate block; not verifier detection"],
         fill=BLUE_T, stroke=GRAPH, color=GRAPH, size=11.5, sub_size=9.5)
    cell(col_x[0], row_y[2], ["impossible", "never reaches a verifier"],
         fill=IMPOSSIBLE_T, stroke=GRID, color=MUTED)
    cell(col_x[1], row_y[2], ["impossible", "never reaches a verifier"],
         fill=IMPOSSIBLE_T, stroke=GRID, color=MUTED)

    # separator: invalid input is reported as a separate block
    f.line([(40, 396), (960, 396)], color=STROKE, lw=3)

    # legend + footnote
    legend = [(GREEN_T, GREEN, "correct outcome"), (RED_T, RED, "error"),
              (BLUE_T, GRAPH, "construction outcome"),
              (IMPOSSIBLE_T, GRID, "impossible")]
    lx = 40
    for fill, stroke, label in legend:
        f.rect(lx, 556, 16, 16, fill=fill, stroke=stroke, lw=1.4, rx=3)
        f.text(lx + 24, 570, label, size=10.5, color=INK, anchor="start")
        lx += 200
    f.text(40, 612, "Faulty = cases labelled HOLD (HOLD-only view); combined "
           "safe-rejection denominators are defined in Section 3.2.6.",
           size=10.5, color=MUTED, anchor="start")
    return f


# --------------------------------------------------------------------------
# figure registry
# --------------------------------------------------------------------------
FIGURES = [
    dict(n="3-1", slug="thinking-framework", section="3.1",
         caption="Thinking framework: problem, gap, intervention, "
                 "controlled test, evidence.",
         desc="Left-to-right five-step framework with RQ2/RQ1/RQ3 mapping "
              "brackets beneath the intervention, controlled test and "
              "evidence boxes.",
         make=fig_3_1),
    dict(n="3-2", slug="research-flow", section="3.1",
         caption="Research flow with the freeze gate (Gate A) and the "
                 "no-tuning gate (Gate B).",
         desc="Seven-phase flowchart with the development loop and the two "
              "gate diamonds (Gate A after development runs, Gate B before "
              "the final run).",
         make=fig_3_2),
    dict(n="3-3", slug="preprocessing-pipeline", section="3.2.3",
         caption="Preprocessing pipeline from raw case to validated "
                 "transition package, graph rendering, and text rendering, "
                 "with the INVALID_INPUT exit.",
         desc="Vertical pipeline from raw case through parse, normalise and "
              "schema-v2 validation to a validity diamond; the red invalid "
              "branch exits to INVALID_INPUT, the valid branch checks prior "
              "state and splits into graph and text renderings.",
         make=fig_3_3),
    dict(n="3-4", slug="data-splits", section="3.2.4",
         caption="Data splits over time: development (tuning allowed), "
                 "held-out v2 (consumed for prompt selection), and the fresh "
                 "set (frozen at Gate A, never used for tuning).",
         desc="Three-lane swimlane timeline with the tuning bar on "
              "development, the flagged prompt-selection use of held-out v2, "
              "the Gate A vertical line and a single run on the fresh set.",
         make=fig_3_4),
    dict(n="3-5", slug="membud-architecture", section="3.2.5",
         caption="MemBud architecture: host proposes, adapter builds the "
                 "transition package, graph and text renderings feed their "
                 "verifiers, the decision policy returns ACCEPT or HOLD, "
                 "only ACCEPT reaches the store, and every step is audited.",
         desc="Block diagram from host and proposed operations through the "
              "adapter and transition package, parallel graph/text "
              "renderings and verifiers into the decision policy, with "
              "ACCEPT to the store, HOLD to the audit log only, and a dashed "
              "no-write-before-decision boundary.",
         make=fig_3_5),
    dict(n="3-6", slug="canonical-graph-schema", section="3.2.5",
         caption="Canonical graph schema, with a worked example of one valid "
                 "update and one corrupted update.",
         desc="Left: the five node shapes and twelve labelled edge types. "
              "Right: a valid update (prior and new records, a shared claim, "
              "evidence and a WRITE operation, ending in ACCEPT) and a "
              "corrupted update (a prior claim dropped with no operation, "
              "ending in HOLD).",
         make=fig_3_6),
    dict(n="3-7", slug="commit-sequence", section="3.2.5",
         caption="Sequence of a guarded update: host proposal, adapter "
                 "snapshot, verification, audit append, and a store write "
                 "that occurs only on ACCEPT.",
         desc="Five-lifeline sequence diagram (Host, Adapter, verifier, "
              "Store, Audit log) with the propose/ snapshot/build/verify/"
              "decision messages and an alt frame for ACCEPT (atomic write, "
              "version + 1) versus HOLD (no write, store unchanged).",
         make=fig_3_7),
    dict(n="3-8", slug="experimental-arms", section="3.2.6",
         caption="One transition package fans out to the four arms; the "
                 "text-fair and graph arms receive identical information, "
                 "and every guarded arm passes through the same decision "
                 "policy.",
         desc="Fan-out from the frozen transition package to unguarded, text, "
              "text-fair and graph arms, with a 'same information' bracket "
              "enclosing the text-fair and graph arms and every guarded arm "
              "passing through one decision policy.",
         make=fig_3_8),
    dict(n="3-9", slug="decision-matrix", section="3.2.7",
         caption="Decision matrix: gold class (valid, faulty, invalid input) "
                 "against decision (ACCEPT, HOLD, INVALID_INPUT), with the "
                 "cells that define each metric marked.",
         desc="Three-by-three matrix of gold class against decision with the "
              "metric-defining cells labelled (valid acceptance, false hold, "
              "detection, false accept, correct invalid handling), impossible "
              "cells greyed and the invalid-input row shown as a separate "
              "reporting block.",
         make=fig_3_9),
]


def make_readme() -> str:
    lines = [
        "# Chapter 3 figures",
        "",
        "Vector figures for Chapter 3, generated by "
        "`paper/tools/make_figures.py`. Each figure is SVG (source), PNG "
        "(preview, 2x) and PDF (vector, for the thesis). The legacy "
        "`figure-3-1-framework.svg/.png` predates this set and is kept "
        "unchanged.",
        "",
        "Palette used in every figure: graph `#2f6f9f`, text `#c07020`, "
        "unguarded `#7a7a7a`, HOLD/INVALID_INPUT `#b23b3b`, ACCEPT "
        "`#2e7d32`; neutral boxes `#f5f7fa` with stroke `#2b3a4a`.",
        "",
        "| Figure | Section | Caption | Files | What is drawn |",
        "|---|---|---|---|---|",
    ]
    for fig in FIGURES:
        base = f"fig-{fig['n']}-{fig['slug']}"
        files = f"`{base}.svg`, `{base}.png`, `{base}.pdf`"
        lines.append(
            f"| {fig['n'].replace('-', '.')} | {fig['section']} | "
            f"{fig['caption']} | {files} | {fig['desc']} |"
        )
    lines += [
        "",
        "## Rendering",
        "",
        "```sh",
        "python3 paper/tools/make_figures.py",
        "```",
        "",
        "The script writes the SVGs, renders the PNGs at 2x with "
        "`rsvg-convert -z 2` and the PDFs with `rsvg-convert -f pdf`, then "
        "rewrites this index. `rsvg-convert` 2.63 rejects the `-w 2x` "
        "spelling, so `-z 2` is used for the same 2x pixel dimensions.",
        "",
        "Captions are the exact captions from the Chapter 3 manuscript "
        "(paragraphs 009, 025, 096, 103, 106, 155, 166, 196, 207 of "
        "`.tmp-lead/docx/chapter3.txt`).",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for fig in FIGURES:
        base = f"fig-{fig['n']}-{fig['slug']}"
        svg_path = OUT / f"{base}.svg"
        png_path = OUT / f"{base}.png"
        pdf_path = OUT / f"{base}.pdf"
        svg_path.write_text(fig["make"]().svg(), encoding="utf-8")
        subprocess.run(
            ["rsvg-convert", "-z", "2", "-o", str(png_path), str(svg_path)],
            check=True,
        )
        subprocess.run(
            ["rsvg-convert", "-f", "pdf", "-o", str(pdf_path), str(svg_path)],
            check=True,
        )
        print(f"wrote {base}.svg / .png / .pdf")
    (OUT / "README.md").write_text(make_readme(), encoding="utf-8")
    print("wrote README.md")


if __name__ == "__main__":
    main()
