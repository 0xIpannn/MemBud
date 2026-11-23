#!/usr/bin/env python3
"""MemoryAgentBench Conflict Resolution (FactConsolidation): unguarded vs guard vs guard+repair memory writes.

Facts arrive in chunks; a later fact overrides an earlier one about the same subject and relation. Three stores are built
(see membud.memagentbench): unguarded (append everything), guard (hold a new fact that contradicts a still-active older one),
guard_repair (bounded repair loop supersedes the stale record). Retrieval is lexical top-k; the reader is DeepSeek using the
benchmark's own FactConsolidation RAG prompt (read from a local clone); accuracy is the benchmark's substring_exact_match.

Pre-registered before the first paid call (nothing tuned on these questions): chunk 20 facts, retrieval k=20, max 2 repair
retries, all 100 questions per source, variant 6k first.
Honest deviations: DeepSeek is the reader/verifier/repairer (the benchmark's agents use GPT-4o-mini/4.1-mini); the reply is
requested as JSON {"answer": ...}; the conflict verifier and the repair chooser are the same model, so results are about this
pipeline, not a leaderboard number; verifier detections are not reviewer-validated; retrieval is single-pass lexical, so
multi-hop questions (which need facts about an intermediate entity the question does not name) are expected to be hard for all
conditions. Data is read from a local copy outside the repo and never committed.
Modes: --estimate (offline), --run (paid; needs --allow-paid and --max-spend-usd), --score (offline).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from membud.memagentbench import (  # noqa: E402
    CONDITIONS, REFERENCE_COMMIT, ingest, load_rag_template, load_scorer, parse_facts, rank,
)
from membud.paired_stats import mcnemar_exact, wilson  # noqa: E402
from run_longmemeval import Stages, h  # noqa: E402

HOME = Path.home()
CONFLICT_SYSTEM = """You maintain a knowledge pool of facts. Each fact has a serial number; a larger serial number means a newer fact. \
You get NEW facts and EXISTING facts that may be related. Report every pair where a NEW fact and an EXISTING fact give different values \
for the same subject and the same relation, so both cannot be true at once. Ignore facts that only mention the same entity with a \
different relation, and ignore pairs that state the same value. Use the serial numbers shown.
Answer with JSON only: {"conflicts": [{"new": <serial>, "old": <serial>}]}"""
SUPERSEDE_SYSTEM = """You repair a rejected memory write. Each pair below is a NEW fact and an OLDER fact about the same subject and relation \
with different values. The knowledge pool rule is that the newer fact (larger serial number) replaces the older one. Return the serial \
numbers of the OLDER facts that must be superseded. Do not return the new facts.
Answer with JSON only: {"supersede": [<serial>, ...]}"""
ANSWER_SUFFIX = '\nAnswer with JSON only: {"answer": "..."}'
K, CHUNK, RETRIES = 20, 20, 2


def _need(key):
    return lambda j: None if isinstance(j.get(key), list) else (_ for _ in ()).throw(ValueError(f"no {key} list"))


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def make_llm_fns(st: Stages, conflict_log: Path | None):
    def find_conflicts(new, pool):
        cand: dict[int, str] = {}
        new_serials = {s for s, _ in new}
        for s, t in new:
            for o, ot in rank(t, [p for p in pool if p[0] < s], 5):
                cand[o] = ot
        if not cand:
            return []
        body = "NEW facts:\n" + "\n".join(f"[{s}] {t}" for s, t in new) + "\n\nEXISTING facts:\n" + "\n".join(f"[{s}] {t}" for s, t in sorted(cand.items()))
        key = "C|" + h(CONFLICT_SYSTEM, body)
        j = st.call(key, [{"role": "system", "content": CONFLICT_SYSTEM}, {"role": "user", "content": body}], _need("conflicts"))
        out = []
        for e in j["conflicts"]:
            if isinstance(e, dict):
                n, o = _int(e.get("new")), _int(e.get("old"))
                if n in new_serials and o in cand:
                    out.append((n, o))
                    if conflict_log is not None:
                        with conflict_log.open("a", encoding="utf-8") as fh:
                            fh.write(json.dumps({"new": [n, dict(new)[n]], "old": [o, cand[o]]}, ensure_ascii=False) + "\n")
        return out

    def choose_supersede(info):
        body = "\n".join(f"NEW [{n[0]}] {n[1]}  ||  OLD [{o[0]}] {o[1]}" for n, o in info)
        j = st.call("S|" + h(SUPERSEDE_SYSTEM, body), [{"role": "system", "content": SUPERSEDE_SYSTEM}, {"role": "user", "content": body}], _need("supersede"))
        return [x for x in map(_int, j["supersede"]) if x is not None]

    return find_conflicts, choose_supersede


def answer(st: Stages, template: str, store, question: str, k: int) -> str:
    facts = sorted(rank(question, store.active(), k))
    pool = "\n".join(f"{s}. {t}" for s, t in facts) or "(empty)"
    prompt = f"Knowledge Pool:\n{pool}\n\n" + template.replace("{question}", question) + ANSWER_SUFFIX
    j = st.call("A|" + h(prompt), [{"role": "user", "content": prompt}],
                lambda j: None if isinstance(j.get("answer"), str) else (_ for _ in ()).throw(ValueError("no answer")))
    return j["answer"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(HOME / "Repositories/MemoryAgentBench-data/Conflict_Resolution.json"))
    ap.add_argument("--reference-repo", default=str(HOME / "Repositories/MemoryAgentBench-repo"))
    ap.add_argument("--variant", default="6k")
    ap.add_argument("--n-questions", type=int, default=100)
    ap.add_argument("--out", default=str(ROOT / "data/memagentbench-runs/conflict"))
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--estimate", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--score", action="store_true")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-spend-usd", type=float)
    a = ap.parse_args()

    data = {r["source"]: r for r in json.loads(Path(a.data).read_text(encoding="utf-8"))}
    sources = [f"factconsolidation_sh_{a.variant}", f"factconsolidation_mh_{a.variant}"]
    ctxs = {data[s]["context"] for s in sources}
    if len(ctxs) != 1:
        raise SystemExit("sh and mh contexts differ; ingestion is shared per variant, extend the script first")
    facts = parse_facts(next(iter(ctxs)))
    out = Path(a.out) / a.variant
    out.mkdir(parents=True, exist_ok=True)
    nq = a.n_questions

    if a.estimate:
        chunks = -(-len(facts) // CHUNK)
        ing_tok = chunks * 2 * 2 * 3000  # guard + guard_repair, verify + (some) repair, ~3k tokens each (rough)
        ans_tok = len(sources) * nq * len(CONDITIONS) * (K * 30 + 450)
        print(json.dumps({"facts": len(facts), "chunks": chunks, "questions": nq * len(sources),
                          "est_input_tokens_rough": ing_tok + ans_tok,
                          "est_cost_usd_peak_cache_miss_rough": round((ing_tok + ans_tok) * 3e-7 + 5e4 * 1.2e-6, 3),
                          "note": "rough: ingestion calls depend on how many conflicts are found"}, indent=2))
        return 0

    if a.run:
        if not a.allow_paid or a.max_spend_usd is None:
            raise SystemExit("--run needs --allow-paid and --max-spend-usd")
        from membud.llm import OpenAICompatibleChatClient, enforce_deepseek_price, load_dotenv_0600

        load_dotenv_0600(ROOT / ".env", require_0600=True, populate_environ=True)
        client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
        price = enforce_deepseek_price(str(client.model), allow_paid=True)
        st = Stages(out / "stages.jsonl", client, (price["prompt_per_mtok"] / 1e6, price["completion_per_mtok"] / 1e6), a.max_spend_usd)
        fc, cs = make_llm_fns(st, out / "conflicts.jsonl")
        try:
            stores, stats = ingest(facts, fc, cs, chunk_size=CHUNK, max_retries=RETRIES)
            (out / "ingest-stats.json").write_text(json.dumps(stats, indent=2) + "\n")
            template = load_rag_template(a.reference_repo)
            score = load_scorer(a.reference_repo)
            rows = []
            for src in sources:
                for i, (q, gold) in enumerate(zip(data[src]["questions"][:nq], data[src]["answers"][:nq])):
                    for c in CONDITIONS:
                        pred = answer(st, template, stores[c], q, K)
                        rows.append({"source": src, "q": i, "condition": c, "prediction": pred, "correct": score(pred, gold)})
            (out / "results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        except RuntimeError as exc:
            print(f"STOP: {exc}; estimated spend ${st.spent:.4f}; resume with --run", file=sys.stderr)
            return 1
        print(json.dumps({"estimated_spend_usd": round(st.spent, 4), "answers": len(rows)}))

    rows = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines()]
    res = {"reference_commit": REFERENCE_COMMIT, "variant": a.variant, "k": K, "chunk": CHUNK, "max_retries": RETRIES,
           "metric": "substring_exact_match (reference functions)", "validated": False, "accuracy": {}, "paired": {}}
    if (out / "ingest-stats.json").exists():
        res["ingest"] = json.loads((out / "ingest-stats.json").read_text())
    for src in sources:
        by = {c: {r["q"]: r["correct"] for r in rows if r["source"] == src and r["condition"] == c} for c in CONDITIONS}
        res["accuracy"][src] = {c: wilson(sum(v.values()), len(v)) for c, v in by.items()}
        for x, y in (("unguarded", "guard"), ("unguarded", "guard_repair"), ("guard", "guard_repair")):
            b = sum(by[x][q] and not by[y][q] for q in by[x])
            c_ = sum(by[y][q] and not by[x][q] for q in by[x])
            res["paired"][f"{src}:{y}_vs_{x}"] = {"only_" + x + "_correct": b, "only_" + y + "_correct": c_, "mcnemar_p": mcnemar_exact(b, c_),
                                                   "diff": round((sum(by[y].values()) - sum(by[x].values())) / len(by[x]), 4)}
    (out / "report.json").write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
