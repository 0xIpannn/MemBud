"""MemoryAgentBench Conflict Resolution (FactConsolidation) pieces: parsing, scoring, memory store, guarded ingestion.

Reference: https://github.com/HUST-AI-HYZ/MemoryAgentBench. The scoring functions and the FactConsolidation
RAG-agent prompt are read from a local clone at run time (``utils/eval_other_utils.py`` and ``utils/templates.py``),
not copied. Accuracy is the reference's ``substring_exact_match``: the normalized gold answer is a substring of the
normalized prediction, maximum over gold aliases.

The context is a numbered fact list; a later (larger-serial) fact overrides an earlier one about the same subject and
relation. ``ingest`` writes the facts chunk by chunk into three memory stores and decides, per store, how to treat a
new fact that contradicts a still-active older one:

* ``unguarded``    append every fact; stale and new values both stay active.
* ``guard``        a verifier reports unresolved conflicts; the new (conflicting) fact is HELD (not stored).
* ``guard_repair`` on HOLD the bounded repair loop (``membud.repair``) re-proposes the write with the stale record
                   superseded; it is committed only if the final candidate re-verifies. If every retry fails, the write
                   falls back to the guard behaviour (conflicting new fact not stored).

Pure: the conflict finder and the supersession chooser are injected, so this module makes no model calls and holds no
dataset text.
"""
from __future__ import annotations

import ast
import math
import re
import string
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, Sequence

from membud.longmemeval import tokens
from membud.repair import VerifyOutcome, run_repair

REFERENCE_COMMIT = "538026089d1a8a8eff05121d0db89b388f360eba"
CONDITIONS = ("unguarded", "guard", "guard_repair")
_FACT = re.compile(r"^(\d+)\. (.*\S)\s*$")

Fact = tuple[int, str]


def parse_facts(context: str) -> list[Fact]:
    out = []
    for line in context.split("\n"):
        m = _FACT.match(line)
        if m:
            out.append((int(m.group(1)), m.group(2)))
    return out


def chunked(items: Sequence, size: int) -> list[list]:
    if size < 1:
        raise ValueError("size must be >= 1")
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def _normalize_answer(text: str) -> str:
    text = text.lower()
    text = "".join(c for c in text if c not in string.punctuation)
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return " ".join(text.split())


def load_scorer(repo: str | Path) -> Callable[[str, Sequence[str]], bool]:
    """Return ``score(prediction, gold_answers) -> bool`` built from the reference's own functions (no module import)."""
    src = (Path(repo) / "utils" / "eval_other_utils.py").read_text(encoding="utf-8")
    want = {"normalize_answer", "substring_exact_match_score", "drqa_metric_max_over_ground_truths"}
    fns = [n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name in want]
    if {f.name for f in fns} != want:
        raise ValueError("reference scoring functions not found")
    ns: dict = {"string": string, "re": re}
    exec(compile(ast.Module(fns, []), "eval_other_utils(subset)", "exec"), ns)  # noqa: S102 - vetted local clone

    def score(prediction: str, gold: Sequence[str]) -> bool:
        return bool(ns["drqa_metric_max_over_ground_truths"](ns["substring_exact_match_score"], prediction, list(gold)))

    return score


def load_rag_template(repo: str | Path) -> str:
    """FactConsolidation ``rag_agent`` query template from the reference ``utils/templates.py``."""
    tree = ast.parse((Path(repo) / "utils" / "templates.py").read_text(encoding="utf-8"))

    def get(d: ast.Dict, key: str):
        for k, v in zip(d.keys, d.values):
            if isinstance(k, ast.Constant) and k.value == key:
                return v
        raise KeyError(key)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "BASE_TEMPLATES" for t in node.targets):
            q = get(get(node.value, "factconsolidation"), "query")
            return ast.literal_eval(get(q, "rag_agent"))
    raise KeyError("BASE_TEMPLATES")


def rank(query: str, facts: Sequence[Fact], k: int) -> list[Fact]:
    """Top-k facts by IDF-weighted token overlap with ``query`` (local, deterministic); ties keep serial order."""
    if not facts or k <= 0:
        return []
    docs = [set(tokens(t)) for _, t in facts]
    df: dict[str, int] = defaultdict(int)
    for d in docs:
        for tok in d:
            df[tok] += 1
    q, n = set(tokens(query)), len(docs)
    scored = sorted(
        (-sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in q & d), facts[i][0], i) for i, d in enumerate(docs)
    )
    return [facts[i] for _, _, i in scored[:k]]


class Store:
    """Active memory records keyed by serial."""

    def __init__(self) -> None:
        self.records: dict[int, str] = {}
        self.superseded: set[int] = set()

    def add(self, serial: int, text: str) -> None:
        self.records[serial] = text

    def supersede(self, serial: int) -> None:
        if serial in self.records:
            self.superseded.add(serial)

    def active(self) -> list[Fact]:
        return [(s, t) for s, t in sorted(self.records.items()) if s not in self.superseded]


FindConflicts = Callable[[Sequence[Fact], Sequence[Fact]], Iterable[tuple[int, int]]]
ChooseSupersede = Callable[[Sequence[tuple[Fact, Fact]]], Iterable[int]]


def _parse_candidate(lines: Sequence[str]) -> tuple[list[Fact], set[int]]:
    adds, sup = [], set()
    for ln in lines:
        kind, _, rest = ln.partition("|")
        if kind == "ADD":
            s, _, t = rest.partition("|")
            adds.append((int(s), t))
        elif kind == "SUPERSEDE":
            sup.add(int(rest))
    return adds, sup


def ingest(
    facts: Sequence[Fact],
    find_conflicts: FindConflicts,
    choose_supersede: ChooseSupersede,
    chunk_size: int = 20,
    max_retries: int = 2,
) -> tuple[dict[str, Store], dict[str, dict]]:
    stores = {c: Store() for c in CONDITIONS}
    stats = {c: {"chunks": 0, "chunks_held": 0, "chunks_recovered": 0, "conflicts": 0, "superseded": 0, "dropped_adds": 0,
                 "repair_attempts": 0} for c in CONDITIONS}
    for chunk in chunked(list(facts), chunk_size):
        for s, t in chunk:
            stores["unguarded"].add(s, t)
        for cond in ("guard", "guard_repair"):
            store, st = stores[cond], stats[cond]
            text_of = {**store.records, **dict(chunk)}

            def verify(lines: Sequence[str], store=store, text_of=text_of) -> VerifyOutcome:
                adds, sup = _parse_candidate(lines)
                pool = [(s, t) for s, t in sorted({**dict(store.active()), **dict(adds)}.items()) if s not in sup]
                pool_serials = {s for s, _ in pool}
                bad = sorted({(n, o) for n, o in find_conflicts(adds, pool) if o in pool_serials and n in dict(adds) and o < n})
                if not bad:
                    return VerifyOutcome(True)
                offenders = tuple(f"ADD|{n}|{text_of[n]}" for n in sorted({n for n, _ in bad}))
                return VerifyOutcome(False, tuple(f"CONFLICT|{n}|{o}" for n, o in bad), offenders)

            def repair(prev, offenders, codes, n, text_of=text_of):
                pairs = [tuple(int(x) for x in c.split("|")[1:]) for c in codes if c.startswith("CONFLICT|")]
                info = [((nw, text_of[nw]), (od, text_of[od])) for nw, od in pairs if nw in text_of and od in text_of]
                chosen = [o for o in choose_supersede(info) if o in text_of]
                extra = [f"SUPERSEDE|{o}" for o in dict.fromkeys(chosen) if f"SUPERSEDE|{o}" not in prev]
                return tuple(prev) + tuple(extra)

            initial = tuple(f"ADD|{s}|{t}" for s, t in chunk)
            st["chunks"] += 1
            first = verify(initial)
            if first.accepted:
                for s, t in chunk:
                    store.add(s, t)
                continue
            st["chunks_held"] += 1
            st["conflicts"] += len(first.reason_codes)
            drop = {int(o.split("|")[1]) for o in first.offending_claims}
            final_adds, final_sup = [(s, t) for s, t in chunk if s not in drop], set()
            if cond == "guard_repair":
                r = run_repair(initial, verify, repair, max_retries=max_retries)
                st["repair_attempts"] += len(r.attempts) - 1
                if r.final_decision == "ACCEPT":
                    final_adds, final_sup = _parse_candidate(r.final_claims)
                    st["chunks_recovered"] += 1
            st["dropped_adds"] += len(chunk) - len(final_adds)
            for s, t in final_adds:
                store.add(s, t)
            for s in final_sup:
                store.supersede(s)
            st["superseded"] += len(final_sup)
    return stores, stats
