#!/usr/bin/env python3
"""LongMemEval pilot: unguarded vs guard vs guard+repair memory ingestion, then QA and judging.

Pipeline per question (haystack sessions are ingested one at a time, in date order):
  extract  : an LLM extractor writes standalone claims from the session (shared by all conditions)
  verify   : the claim judge (HaluMem accepted-assistant-facts policy, rubric in
             docs/halumem-grounding-rubric.md) marks claims SUPPORTED / UNSUPPORTED (shared)
  conditions
     unguarded     store every extracted claim
     guard         HOLD a session that has any unsupported claim (nothing stored for it)
     guard_repair  on HOLD, run the bounded repair loop (membud.repair); store the repaired claims
                   only if the repaired candidate passes verification and re-verification
  retrieve : top-k stored memories by local IDF overlap (no embeddings, no network)
  read     : reader model answers from the memories (cached per identical context)
  judge    : the benchmark's own prompt (get_anscheck_prompt, read from a local clone) ->
             correct iff the judge reply contains "yes"; aggregation as print_qa_metrics.py

Honest deviations (also written into the output): the judge is DeepSeek, not GPT-4o; the reply is
requested as JSON {"label": "yes"|"no"} because the client is JSON-only; the extractor, reader and
verifier are our own components (the benchmark has no memory system to ingest through);
retrieval is lexical. Results are therefore about this pipeline, not a leaderboard number.
Modes: --estimate (offline), --run (paid; needs --allow-paid and --max-spend-usd), --score (offline).
Data is read from a local copy outside the repo and never committed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from membud.longmemeval import (  # noqa: E402
    QUESTION_TYPES, REFERENCE_COMMIT, aggregate, judge_prompt, load_anscheck, load_instances,
    retrieve, session_text, verdict_is_correct,
)
from membud.paired_stats import mcnemar_exact  # noqa: E402
from membud.repair import VerifyOutcome, run_repair  # noqa: E402

CONDITIONS = ("unguarded", "guard", "guard_repair")
HOME = Path.home()
EXTRACT_SYSTEM = """You write the memories a personal assistant should keep from one chat session. Output short, standalone factual \
statements about the user's life, preferences, plans, possessions, events and relationships, and any fact the assistant gave that the user \
relied on. Do not infer, do not add anything not stated, and do NOT put the session date inside a statement (it is stored separately). \
Answer with JSON only: {"claims": ["...", "..."]}"""
VERIFY_SYSTEM = None  # imported lazily from the HaluMem policy prompt
REPAIR_SYSTEM = """You correct a list of memory claims written from one chat session. A checker marked some claims UNSUPPORTED. Keep every \
claim that was not flagged EXACTLY as written. For each flagged claim, delete it or rewrite it so it states only what the session supports. \
Never add a new fact. Answer with JSON only: {"claims": ["...", "..."]}"""
READER = """I will give you memories extracted from past chats between you and a user, each with the date of its chat. Answer the question \
based on the relevant memories. If the memories do not contain the information, say you do not know.

Memories:
{memories}

Current Date: {date}
Question: {question}
Answer with JSON only: {{"answer": "..."}}"""


def pilot_sample(data, n, seed):
    rng = random.Random(seed)
    by = {}
    for q in data:
        by.setdefault(q["question_type"], []).append(q)
    for v in by.values():
        rng.shuffle(v)
    out, i = [], 0
    types = [t for t in QUESTION_TYPES if t in by]
    while len(out) < n and any(by.values()):  # round-robin across types
        t = types[i % len(types)]
        if by[t]:
            out.append(by[t].pop())
        i += 1
    return out


class Stages:
    def __init__(self, path: Path, client=None, prices=(0.0, 0.0), cap=None):
        self.path, self.client, self.p_in, self.p_out, self.cap = path, client, prices[0], prices[1], cap
        self.cache = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                r = json.loads(line)
                self.cache[r["key"]] = r["json"]
        self.spent = 0.0

    def call(self, key, messages, validator):
        if key in self.cache:
            return self.cache[key]
        if self.client is None:
            raise KeyError(key)
        if self.spent >= self.cap:
            raise RuntimeError("spend cap reached")
        res = self.client.complete_json(messages, validator=validator)
        self.spent += (res.input_tokens or 0) * self.p_in + (res.output_tokens or 0) * self.p_out
        self.cache[key] = res.json
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"key": key, "json": res.json}, ensure_ascii=False) + "\n")
        return res.json


def _claims_ok(j):
    if not (isinstance(j.get("claims"), list) and all(isinstance(x, str) for x in j["claims"])):
        raise ValueError("no claims list")


def verdicts_ok(j):
    if not isinstance(j.get("claims"), list):
        raise ValueError("no claims list")


def h(*parts):
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def ingest_question(q, st: Stages, max_retries=2):
    """Returns {condition: [memory strings]} and per-question stats."""
    from halumem_llm_phase2_eval import SYSTEM_ACCEPTED

    order = sorted(range(len(q["haystack_sessions"])), key=lambda i: q["haystack_dates"][i])
    stores = {c: [] for c in CONDITIONS}
    stats = {"sessions": 0, "held": 0, "recovered": 0, "claims": 0, "claims_unsupported": 0}
    for i in order:
        sess, date = q["haystack_sessions"][i], q["haystack_dates"][i]
        text = session_text(sess, date)
        turns = "\n".join(f"[{k}] [{t['role']}] {t['content']}" for k, t in enumerate(sess))
        qid = q["question_id"]
        ex = st.call(f"E|{qid}|{i}", [{"role": "system", "content": EXTRACT_SYSTEM}, {"role": "user", "content": text}], _claims_ok)
        claims = [c.strip() for c in ex["claims"] if c.strip()]
        stats["sessions"] += 1
        stats["claims"] += len(claims)

        def verify(cl, i=i, turns=turns, qid=qid):
            body = "\n".join(f"{k}. {c}" for k, c in enumerate(cl))
            j = st.call(f"V|{qid}|{i}|{h(*cl)}", [{"role": "system", "content": SYSTEM_ACCEPTED},
                        {"role": "user", "content": f"Session:\n{turns}\n\nEarlier memory:\n(empty)\n\nClaims:\n{body}"}], verdicts_ok)
            sup = {}
            for e in j["claims"]:
                try:
                    sup[int(e["i"])] = e.get("verdict") == "SUPPORTED"
                except (KeyError, ValueError, TypeError):
                    continue
            bad = tuple(c for k, c in enumerate(cl) if not sup.get(k, False))
            return VerifyOutcome(not bad, ("UNSUPPORTED_CLAIM",) if bad else (), bad)

        def extract(prev, off, codes, n, i=i, turns=turns, qid=qid):
            body = "\n".join(f"{k}. {c}" for k, c in enumerate(prev))
            fl = "\n".join(f"- {c}" for c in off) or "(none)"
            j = st.call(f"X|{qid}|{i}|{n}|{h(*prev)}", [{"role": "system", "content": REPAIR_SYSTEM},
                        {"role": "user", "content": f"Session:\n{turns}\n\nCurrent claims:\n{body}\n\nFlagged ({', '.join(codes)}):\n{fl}"}], _claims_ok)
            return [c.strip() for c in j["claims"] if c.strip()]

        r = run_repair(claims, verify, extract, max_retries=max_retries) if claims else None
        tag = lambda cl: [f"[{date}] {c}" for c in cl]  # noqa: E731
        stores["unguarded"] += tag(claims)
        if r is None or r.initial_decision == "ACCEPT":
            stores["guard"] += tag(claims)
            stores["guard_repair"] += tag(claims)
        else:
            stats["held"] += 1
            stats["claims_unsupported"] += len(r.attempts[0].offending_claims)
            if r.recovered:
                stats["recovered"] += 1
                stores["guard_repair"] += tag(r.final_claims)
    return stores, stats


def answer_and_judge(q, stores, st: Stages, anscheck, k):
    out = {}
    for c in CONDITIONS:
        mem = retrieve(q["question"], stores[c], k)
        body = "\n".join(f"- {m}" for m in mem) or "(no memories)"
        prompt = READER.format(memories=body, date=q["question_date"], question=q["question"])
        a = st.call(f"A|{q['question_id']}|{h(prompt)}", [{"role": "user", "content": prompt}],
                    lambda j: None if isinstance(j.get("answer"), str) else (_ for _ in ()).throw(ValueError("no answer")))
        jp = judge_prompt(anscheck, q, a["answer"]) + '\nRespond with JSON only: {"label": "yes" or "no"}'
        j = st.call(f"J|{q['question_id']}|{h(jp)}", [{"role": "user", "content": jp}],
                    lambda j: None if isinstance(j.get("label"), str) else (_ for _ in ()).throw(ValueError("no label")))
        out[c] = {"hypothesis": a["answer"], "correct": verdict_is_correct(j["label"]), "n_memories": len(stores[c])}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(HOME / "Repositories/LongMemEval/longmemeval_oracle.json"))
    ap.add_argument("--reference-repo", default=str(HOME / "Repositories/LongMemEval-repo"))
    ap.add_argument("--out", default=str(ROOT / "data/longmemeval-runs/pilot"))
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20261122)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--max-retries", type=int, default=2)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--estimate", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--score", action="store_true")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-spend-usd", type=float)
    a = ap.parse_args()

    data = load_instances(a.data)
    sample = pilot_sample(data, a.n, a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    anscheck = load_anscheck(a.reference_repo)
    sessions = sum(len(q["haystack_sessions"]) for q in sample)

    if a.estimate:
        chars = sum(len(session_text(s, d)) for q in sample for s, d in zip(q["haystack_sessions"], q["haystack_dates"]))
        tok_in = chars // 4 * 2 + len(sample) * 3 * 2500  # extract + verify per session; reader/judge small
        tok_out = sessions * 700 + len(sample) * 3 * 150
        print(json.dumps({"questions": len(sample), "sessions": sessions, "type_mix": {t: sum(q["question_type"] == t for q in sample) for t in QUESTION_TYPES},
                          "est_input_tokens": tok_in, "est_output_tokens": tok_out,
                          "est_cost_usd_peak_cache_miss_before_repair": round(tok_in * 3e-7 + tok_out * 1.2e-6, 3),
                          "note": "repair adds <= 2 extra calls per held session; judge is deepseek-flash not gpt-4o"}, indent=2))
        return 0

    client, prices = None, (0.0, 0.0)
    if a.run:
        if not a.allow_paid or a.max_spend_usd is None:
            raise SystemExit("--run needs --allow-paid and --max-spend-usd")
        from membud.llm import OpenAICompatibleChatClient, enforce_deepseek_price, load_dotenv_0600

        load_dotenv_0600(ROOT / ".env", require_0600=True, populate_environ=True)
        client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
        pr = enforce_deepseek_price(str(client.model), allow_paid=True)
        prices = (pr["prompt_per_mtok"] / 1e6, pr["completion_per_mtok"] / 1e6)
    st = Stages(out / "stages.jsonl", client, prices, a.max_spend_usd)
    results_path = out / "results.jsonl"
    done = {json.loads(l)["question_id"]: json.loads(l) for l in results_path.read_text().splitlines()} if results_path.exists() else {}

    stopped = ""
    for q in sample:
        if q["question_id"] in done:
            continue
        try:
            stores, stats = ingest_question(q, st, a.max_retries)
            res = answer_and_judge(q, stores, st, anscheck, a.k)
        except (RuntimeError, KeyError) as exc:
            stopped = str(exc)
            break
        rec = {"question_id": q["question_id"], "question_type": q["question_type"], "stats": stats, "conditions": res}
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        done[q["question_id"]] = rec
    if a.run:
        print(json.dumps({"questions_done": len(done), "of": len(sample), "estimated_spend_usd": round(st.spent, 4), "stopped": stopped}))

    recs = [done[q["question_id"]] for q in sample if q["question_id"] in done]
    report = {"n_questions": len(recs), "of_sample": len(sample), "data": Path(a.data).name, "reference_commit": REFERENCE_COMMIT,
              "validated": False, "judge": "deepseek-flash with the benchmark prompts (official: gpt-4o)",
              "memory_stats": {k: sum(r["stats"][k] for r in recs) for k in ("sessions", "held", "recovered", "claims", "claims_unsupported")}}
    for c in CONDITIONS:
        report[c] = aggregate((r["question_id"], r["question_type"], r["conditions"][c]["correct"]) for r in recs)
    report["paired_vs_unguarded"] = {}
    for c in ("guard", "guard_repair"):
        both = [(r["conditions"]["unguarded"]["correct"], r["conditions"][c]["correct"]) for r in recs]
        b = sum(x and not y for x, y in both)  # unguarded only correct
        cc = sum(y and not x for x, y in both)  # condition only correct
        report["paired_vs_unguarded"][c] = {"condition_only_correct": cc, "unguarded_only_correct": b,
                                            "diff_accuracy": (cc - b) / len(both) if both else None, "mcnemar_p": mcnemar_exact(cc, b) if both else None}
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k in ("n_questions", "memory_stats", "paired_vs_unguarded")}
                     | {c: {"overall": report[c]["overall_accuracy"], "task_avg": report[c]["task_averaged_accuracy"]} for c in CONDITIONS}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
