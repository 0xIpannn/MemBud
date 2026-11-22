"""LongMemEval harness pieces: loading, retrieval, official judge prompts, official aggregation.

Reference: https://github.com/xiaowu0162/LongMemEval (MIT). The judge prompt function
``get_anscheck_prompt`` is read from a local clone at run time (``src/evaluation/evaluate_qa.py``)
so the prompts are exactly the benchmark's, not copies. Aggregation mirrors
``evaluate_qa.py`` / ``print_qa_metrics.py``: a response is correct iff the judge's reply contains
"yes" (case-insensitive); per-type accuracy, task-averaged accuracy (unweighted mean of the six
types), overall accuracy, and abstention accuracy (``_abs`` ids, also counted in their own type).

Pure and offline: no model calls, no dataset text stored here.
"""
from __future__ import annotations

import ast
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, Sequence

QUESTION_TYPES = (
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
)
REFERENCE_COMMIT = "9e0b455f4ef0e2ab8f2e582289761153549043fc"


def load_instances(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for q in data:
        if q["question_type"] not in QUESTION_TYPES:
            raise ValueError(f"unknown question_type {q['question_type']!r}")
        if len(q["haystack_sessions"]) != len(q["haystack_dates"]):
            raise ValueError(f"{q['question_id']}: sessions/dates length mismatch")
    return data


def is_abstention(question_id: str) -> bool:
    return "_abs" in question_id


def load_anscheck(repo: str | Path) -> Callable[..., str]:
    """Return the benchmark's ``get_anscheck_prompt`` without importing its module (it needs openai)."""
    src = (Path(repo) / "src" / "evaluation" / "evaluate_qa.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "get_anscheck_prompt")
    ns: dict = {}
    exec(compile(ast.Module([fn], []), "evaluate_qa.get_anscheck_prompt", "exec"), ns)  # noqa: S102 - vetted local clone
    return ns["get_anscheck_prompt"]


def judge_prompt(anscheck: Callable[..., str], q: dict, hypothesis: str) -> str:
    return anscheck(q["question_type"], q["question"], str(q["answer"]), hypothesis, abstention=is_abstention(q["question_id"]))


def verdict_is_correct(judge_reply: str) -> bool:
    return "yes" in judge_reply.strip().lower()


def session_text(session: Sequence[dict], date: str) -> str:
    """Session as plain text for an extractor (``has_answer`` is never shown)."""
    lines = [f"Session date: {date}"]
    lines += [f"{t['role']}: {t['content']}" for t in session]
    return "\n".join(lines)


_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an the and or of to in on at for with is are was were be been it this that i you my me your user".split())


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


def retrieve(query: str, memories: Sequence[str], k: int) -> list[str]:
    """Top-k memories by an IDF-weighted token overlap (local, deterministic, no network)."""
    if not memories:
        return []
    docs = [set(tokens(m)) for m in memories]
    n = len(docs)
    df: dict[str, int] = defaultdict(int)
    for d in docs:
        for t in d:
            df[t] += 1
    q = set(tokens(query))
    scored = []
    for i, d in enumerate(docs):
        s = sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in q & d)
        scored.append((-s, i))
    scored.sort()
    return [memories[i] for _, i in scored[:k]]


def aggregate(results: Iterable[tuple[str, str, bool]]) -> dict:
    """results: (question_id, question_type, correct). Mirrors print_qa_metrics.py."""
    by_type: dict[str, list[bool]] = defaultdict(list)
    allv, absv = [], []
    for qid, qtype, ok in results:
        by_type[qtype].append(ok)
        allv.append(ok)
        if is_abstention(qid):
            absv.append(ok)
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
    per = {t: {"accuracy": mean(by_type[t]), "n": len(by_type[t])} for t in QUESTION_TYPES if by_type[t]}
    task_avg = mean([v["accuracy"] for v in per.values()])
    return {"per_type": per, "task_averaged_accuracy": task_avg, "overall_accuracy": mean(allv), "n": len(allv),
            "abstention_accuracy": mean(absv), "abstention_n": len(absv)}
