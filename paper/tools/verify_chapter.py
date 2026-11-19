#!/usr/bin/env python3
"""Quality gate for MemBud paper chapters.

Checks the constraints that a chapter can violate mechanically:
  * every [@citation] key exists in paper/references.bib
  * no TODO markers
  * no post-evaluation artifact names are cited (paper is pinned to 1ce84ce / 4190526)
  * forbidden positive claims are not asserted (negations are reported as WARN, not FAIL)
  * the pinned evaluated revision is mentioned by chapters that report facts
  * chapter 3 keeps the user's exact template headings (3.1/3.2/3.2.1-3.2.6)
  * "gold" label vocabulary is flagged for human review

Usage:
  .venv/bin/python paper/tools/verify_chapter.py                 # all paper/chapter-*.md
  .venv/bin/python paper/tools/verify_chapter.py paper/chapter-3-implementation-method.md
Exit code 0 = no FAIL; 1 = at least one FAIL.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "paper"
BIB = PAPER / "references.bib"

POST_EVAL = [
    "llm-heldout-v2-rerun",
    "graph-llm-false-hold",
    "mem0-bridge",
    "service.py",
    "sidecar",
    "opencode",
]
STRONG_FORBIDDEN = [
    r"\boutperforms?\b",
    r"\bsuperior to\b",
    r"\bproves? that\b",
    r"\bstatistically significant\b",
    r"\bstate[- ]of[- ]the[- ]art\b",
    r"\bdouble[- ]annotated\b",
    r"\bproduction[- ]ready\b",
    r"\bgold[- ]standard\b",
    r"\bgold data\b",
]
NEGATIONS = ("no ", "not ", "never ", "neither ", "cannot ", "without ", "n't ", "no such", "rather than", "instead of")
TEMPLATE3 = [
    "# CHAPTER 3 IMPLEMENTATION METHOD",
    "## 3.1 Thinking Framework",
    "## 3.2 Research Methods",
    "### 3.2.1 Data Collection",
    "### 3.2.2 Exploratory Data Analysis",
    "### 3.2.3 Data Preprocessing",
    "### 3.2.4 Data Splitting",
    "### 3.2.5 Modelling",
    "### 3.2.6 Model Evaluation",
]


def bib_keys() -> set[str]:
    text = BIB.read_text(encoding="utf-8")
    return set(re.findall(r"^@[a-zA-Z]+\{([A-Za-z0-9_]+),", text, flags=re.M))


def negated(text: str, start: int) -> bool:
    window = text[max(0, start - 220) : start].lower()
    return any(n in window for n in NEGATIONS)


def check(path: Path, keys: set[str]) -> tuple[list[str], list[str]]:
    fails: list[str] = []
    warns: list[str] = []
    text = path.read_text(encoding="utf-8")
    scan = re.sub(r"```.*?```", " ", text, flags=re.S)
    scan = re.sub(r"`[^`]*`", " ", scan)
    low = scan.lower()

    # citations
    cite_ok = 0
    for group in re.findall(r"\[@([^\]]+)\]", scan):
        for item in re.split(r"[;,]\s*", group):
            key = item.strip().lstrip("@").split()[0] if item.strip() else ""
            if not key:
                continue
            if key in keys:
                cite_ok += 1
            else:
                fails.append(f"unknown citation key: @{key}")
    # TODO markers
    for m in re.finditer(r"TODO", scan):
        fails.append(f"TODO marker at offset {m.start()}: {text[m.start():m.start()+60]!r}")
    # post-evaluation artifacts
    for name in POST_EVAL:
        if name.lower() in low:
            fails.append(f"post-evaluation artifact referenced: {name!r}")
    # forbidden claims
    for pat in STRONG_FORBIDDEN:
        for m in re.finditer(pat, scan, flags=re.I):
            kind = "WARN (negated?)" if negated(scan, m.start()) else "FAIL"
            msg = f"{kind}: forbidden phrase {m.group(0)!r} at offset {m.start()}"
            (warns if kind.startswith("WARN") else fails).append(msg)
    for m in re.finditer(r"\bindependently reviewed\b", scan, flags=re.I):
        warns.append(f"context-check 'independently reviewed' at offset {m.start()}: {scan[max(0,m.start()-70):m.start()+60]!r}")
    # pin mention
    if "1ce84ce" not in text and "4190526" not in text:
        warns.append("pinned evaluated revision (1ce84ce / 4190526) is not mentioned")
    # label status
    if "label" in low and "single-reviewer" not in low:
        warns.append("chapter mentions labels but not 'single-reviewer'")
    # gold vocabulary
    for m in re.finditer(r"\bgold\b", scan, flags=re.I):
        if not negated(scan, m.start()):
            warns.append(f"'gold' used non-negated at offset {m.start()}: {text[m.start():m.start()+50]!r}")
    # template for chapter 3
    if path.name.startswith("chapter-3"):
        heads = [ln.strip() for ln in text.splitlines() if ln.startswith("#")]
        expected = TEMPLATE3
        if heads[: len(expected)] != expected:
            fails.append("chapter 3 headings do not match the user template")
        if len(heads) != len(expected):
            fails.append(f"chapter 3 has {len(heads)} headings, expected {len(expected)}")
    # generic: exactly one H1 and sequential subheads
    heads = [ln for ln in text.splitlines() if ln.startswith("# ")]
    if len(heads) != 1:
        fails.append(f"expected exactly one H1 chapter title, found {len(heads)}")
    return fails, warns


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv[1:]] or sorted(PAPER.glob("chapter-*.md"))
    if not files:
        print("no chapter files found")
        return 1
    keys = bib_keys()
    total_fail = 0
    for path in files:
        fails, warns = check(path, keys)
        total_fail += len(fails)
        status = "FAIL" if fails else ("WARN" if warns else "PASS")
        print(f"\n=== {path.name}: {status} ===")
        for f in fails:
            print(f"  FAIL  {f}")
        for w in warns:
            print(f"  WARN  {w}")
        if not fails and not warns:
            print(f"  clean: {len(keys)} bib keys loaded")
    print(f"\nsummary: {len(files)} file(s), {total_fail} failure(s)")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
