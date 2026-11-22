#!/usr/bin/env python3
"""Does a semantic (LLM) grounder agree with the human claim audit better than lexical?

Reads the claim-audit items and the reviewer's answers (claim-level gold: SUPPORTED /
UNSUPPORTED), runs a DeepSeek judge per claim over the session's USER turns only,
and compares three claim-level verdicts against the human: the LLM judge, the
lexical CoverageGrounder (theta 0.6, N + numeral normalization) and the silver rule.

Paid (DeepSeek); sends HaluMem session text to DeepSeek (data policy confirmed by the
user for this project). Estimated spend (peak list price) is hard-capped and persisted
via the local response cache (cache hits cost 0). Output stays in the gitignored run dir.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from membud.grounding import CoverageGrounder, GroundingSource, JudgeGrounder, JudgeVerdict  # noqa: E402
from membud.halumem import persona_name_from_info  # noqa: E402
from membud.llm import OpenAICompatibleChatClient, enforce_deepseek_price, load_dotenv_0600  # noqa: E402
from membud.paired_stats import wilson  # noqa: E402

SYSTEM = """You verify claims that a memory system wrote about a user. You are given the user's \
numbered statements from one chat session and ONE claim. Decide whether the USER'S statements support the claim.
SUPPORTED: the user's words state it or clearly imply it, including paraphrase, different number/date formats \
(1983-05-06 = May 6, 1983; 75000 = $75,000), or a combination of facts the user did state.
UNSUPPORTED: the claim adds a fact, number, name, event or detail the user did not give.
Ignore a run-date phrase such as "as of November 2026" or "around November 2026"; judge the rest of the claim.
"""
FULL = """
The statements are labelled [user] or [assistant]. A fact stated only by the assistant does not count unless a user statement confirms or adopts it. Earlier memory is context only.
"""
ANSWER = 'Answer with JSON only: {"verdict": "SUPPORTED" or "UNSUPPORTED", "turns": [numbers of the statements that support it], "reason": "<=20 words"}'



def make_judge(client, spend, full=False):
    def judge(claim: str, sources) -> JudgeVerdict:
        if spend["est"] >= spend["cap"]:
            raise RuntimeError(f"spend cap reached (estimated ${spend['est']:.4f} >= ${spend['cap']})")
        turns = "\n".join(f"[{i}] {s.text}" for i, s in enumerate(sources))
        if full and spend.get("prior"):
            turns += "\n\nEarlier memory:\n" + "\n".join(spend["prior"])
        res = client.complete_json(
            [{"role": "system", "content": SYSTEM + (FULL if full else "") + ANSWER},
             {"role": "user", "content": f"User statements:\n{turns}\n\nClaim: {claim}"}],
            validator=lambda j: (_ for _ in ()).throw(ValueError("bad verdict")) if j.get("verdict") not in ("SUPPORTED", "UNSUPPORTED") else None,
        )
        spend["est"] += (res.input_tokens or 0) * spend["p_in"] + (res.output_tokens or 0) * spend["p_out"]
        spend["calls"] += 0 if res.cached else 1
        spend["tokens"] += (res.total_tokens or 0)
        j = res.json
        idx = tuple(int(i) for i in j.get("turns", []) if isinstance(i, int) or str(i).isdigit())
        return JudgeVerdict(j["verdict"] == "SUPPORTED", idx, str(j.get("reason", ""))[:200])
    return judge


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--answers", required=True)
    ap.add_argument("--full-dialogue", action="store_true", help="show the judge user AND assistant turns plus prior memory (the human reviewer saw these)")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-spend-usd", type=float, required=True)
    ap.add_argument("--halumem-path", required=True)
    ap.add_argument("--dataset-revision", default="")
    a = ap.parse_args()
    if a.max_spend_usd <= 0:
        raise SystemExit("--max-spend-usd must be > 0")
    run = Path(a.run_dir) / "claim-audit"
    items = [json.loads(l) for l in (run / "claim-items.jsonl").read_text().splitlines()]
    key = {k["item_id"]: k for k in map(json.loads, (run / "claim-silver-key.jsonl").read_text().splitlines())}
    ans = json.loads(Path(a.answers).read_text())["answers"]

    from membud.halumem import load_halumem_dataset
    _, users = load_halumem_dataset(a.halumem_path, dataset_revision=a.dataset_revision or "local")
    lookup = {f"{u.uuid}__{s.session_id}": (u, s) for u in users for s in u.sessions}

    load_dotenv_0600(ROOT / ".env", require_0600=True, populate_environ=True)
    client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
    price = enforce_deepseek_price(str(client.model), allow_paid=a.allow_paid)
    spend = {"est": 0.0, "cap": a.max_spend_usd, "p_in": price["prompt_per_mtok"] / 1e6,
             "p_out": price["completion_per_mtok"] / 1e6, "calls": 0, "tokens": 0}
    judge = JudgeGrounder(make_judge(client, spend, a.full_dialogue), label="deepseek-judge")

    rows, aborted = [], ""
    for it in items:
        human = (ans.get(it["item_id"]) or {}).get("decision")
        u, s = lookup[it["case_id"]]
        user_sources = [GroundingSource(f"user[{i}]", t.content) for i, t in enumerate(x for x in s.dialogue if x.role == "user")]
        sources = [GroundingSource(f"{t.role}[{i}]", f"[{t.role}] {t.content}") for i, t in enumerate(s.dialogue)] if a.full_dialogue else user_sources
        spend["prior"] = it["prior_memory"]
        try:
            rep = judge.ground([it["claim"]], sources)
        except RuntimeError as exc:
            aborted = str(exc)
            break
        lex = CoverageGrounder(0.6, persona_name_from_info(u.persona_info), True, True).ground([it["claim"]], user_sources)
        rows.append({"item_id": it["item_id"], "human": human, "silver": key[it["item_id"]]["silver"],
                     "llm": "SUPPORTED" if rep.claims[0].is_supported else "UNSUPPORTED",
                     "lexical": "SUPPORTED" if lex.claims[0].is_supported else "UNSUPPORTED",
                     "llm_reason_code": rep.claims[0].reason})

    gold = [r for r in rows if r["human"] in ("SUPPORTED", "UNSUPPORTED")]
    res = {"mode": "full_dialogue" if a.full_dialogue else "user_turns_only", "items_run": len(rows), "gold_decisive": len(gold), "aborted": aborted,
           "spend_estimated_peak_rate_usd": round(spend["est"], 6), "cap_usd": spend["cap"],
           "paid_calls": spend["calls"], "tokens": spend["tokens"], "model": str(client.model)}
    for who in ("llm", "lexical", "silver"):
        n = len(gold); ag = sum(r[who] == r["human"] for r in gold)
        res[who] = {"agreement": wilson(ag, n),
                    "says_unsupported_human_supported": sum(r[who] == "UNSUPPORTED" and r["human"] == "SUPPORTED" for r in gold),
                    "says_supported_human_unsupported": sum(r[who] == "SUPPORTED" and r["human"] == "UNSUPPORTED" for r in gold)}
    (run / ("claim-llm-eval-full.json" if a.full_dialogue else "claim-llm-eval.json")).write_text(json.dumps({"summary": res, "rows": rows}, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    return 1 if aborted else 0


if __name__ == "__main__":
    raise SystemExit(main())
