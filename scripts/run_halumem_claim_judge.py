#!/usr/bin/env python3
"""Batch claim judge (SYSTEM_ACCEPTED policy, DeepSeek, paid, capped) over a run directory's Mem0 proposals.

Same prompt and policy that decided the Medium guard. Writes llm-claim-verdicts-accepted-replyctx.jsonl
in the run directory (file name kept so repair/scoring scripts run unchanged). The reply-context quote
check of the Medium pipeline is NOT applied (documented deviation). Checkpointed per session; --estimate offline.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
_spec = importlib.util.spec_from_file_location("llm_phase2", ROOT / "scripts" / "halumem_llm_phase2_eval.py")
_llm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_llm)
SYSTEM_ACCEPTED = _llm.SYSTEM_ACCEPTED

# Rubric-aligned prompt (docs/halumem-grounding-rubric.md, frozen v1). Written 2026-11-23 before any of its outputs were seen.
# Differences from SYSTEM_ACCEPTED: adoption must refer to the specific fact (rubric rule 2; generic thanks is not adoption),
# and wording that only restates, summarizes or frames what the user said (motive, feeling, consequence) without adding a new
# checkable fact, number, name, event or detail is SUPPORTED (rubric rule 1 "clearly imply"; "UNSUPPORTED if the claim adds ...").
# Disclosure: the decision to write it was prompted by the weak-extractor reviewer quiz (halumem-claim-audit-weak-v1), which
# includes final-split users; no threshold was fitted, and it is evaluated on a fresh quiz that excludes those claims.
SYSTEM_RUBRIC = """You verify claims that a memory system wrote about a user. You get one chat session as numbered turns \
labelled [user] or [assistant], earlier memory, and a numbered list of claims. For EACH claim decide whether it is supported.
SUPPORTED if any of these holds:
 (1) the user's words state it or clearly imply it. Paraphrase, different number/date formats (1983-05-06 = May 6, 1983; \
75000 = $75,000) and combining facts the user stated all count. A claim that restates or summarizes what the user said and only \
adds framing (a motive, feeling, consequence or significance that a careful reader would take from the user's words) is SUPPORTED;
 (2) the assistant introduced the fact and a later user turn adopts that specific fact: restates or uses it, agrees in a way that \
refers to it, says they will act or have acted on it, asks to build on it, or reacts to its specific content. Generic \
acknowledgement ("thank you", "that's helpful", "great ideas") is NOT adoption;
 (3) it restates or follows directly from something the user said earlier in the session or from the earlier memory.
UNSUPPORTED only if the claim adds a checkable fact, number, name, event or detail that none of (1)-(3) covers: for example a \
number that appears nowhere in the dialogue or earlier memory, an event or relationship the user never mentioned, an \
assistant-introduced fact the user never engaged with specifically, or something the user rejected or corrected.
Ignore a run-date phrase such as "as of November 2026"; judge the rest of the claim.
For SUPPORTED claims, "turns" lists the numbers of the [user] turns that state, adopt or are the earlier basis for it.
Answer with JSON only: {"claims": [{"i": <claim number>, "verdict": "SUPPORTED" or "UNSUPPORTED", "turns": [..]}, ...]} with one entry per claim."""
PROMPTS = {"accepted": SYSTEM_ACCEPTED, "rubric": SYSTEM_RUBRIC}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--halumem-path", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--estimate", action="store_true")
    mode.add_argument("--run", action="store_true")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-spend-usd", type=float)
    ap.add_argument("--prompt", choices=tuple(PROMPTS), default="accepted")
    ap.add_argument("--verdicts-name", default="llm-claim-verdicts-accepted-replyctx.jsonl",
                    help="output file in --run-dir (default name kept so repair/scoring scripts run unchanged)")
    a = ap.parse_args()
    run = Path(a.run_dir)
    users = {}
    for line in Path(a.halumem_path).read_text(encoding="utf-8").splitlines():
        u = json.loads(line)
        users[u["uuid"]] = u
    plan = []
    for f in sorted((run / "proposals").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        dlg = users[d["user_uuid"]]["sessions"][d["session_index"] - 1]["dialogue"]
        before = [c for r in d["transition"]["before"] for c in r["claims"]]
        added = [c for c in dict.fromkeys(p["memory"] for p in d["mem0_proposals"] if p.get("event") == "ADD") if c not in set(before)]
        plan.append((d["case_id"], dlg, before, added))
    fmt = lambda dl: "\n".join(f"[{i}] [{t['role']}] {t['content']}" for i, t in enumerate(dl))  # noqa: E731
    if a.estimate:
        tok = sum(len(fmt(p[1])) // 4 + 800 for p in plan if p[3])
        print(json.dumps({"sessions": len(plan), "claims": sum(len(p[3]) for p in plan),
                          "est_cost_usd_peak_cache_miss": round(tok * 3e-7 + len(plan) * 600 * 1.2e-6, 3)}))
        return 0
    if not a.allow_paid or a.max_spend_usd is None:
        raise SystemExit("--run needs --allow-paid and --max-spend-usd")
    from membud.llm import OpenAICompatibleChatClient, enforce_deepseek_price, load_dotenv_0600

    load_dotenv_0600(ROOT / ".env", require_0600=True, populate_environ=True)
    client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
    price = enforce_deepseek_price(str(client.model), allow_paid=True)
    p_in, p_out = price["prompt_per_mtok"] / 1e6, price["completion_per_mtok"] / 1e6
    vpath = run / a.verdicts_name
    done = {json.loads(l)["case_id"] for l in vpath.read_text().splitlines()} if vpath.exists() else set()
    spent = 0.0
    for cid, dlg, before, added in plan:
        if cid in done:
            continue
        if spent >= a.max_spend_usd:
            print(f"STOP: estimated spend ${spent:.4f} reached cap; resume with --run", file=sys.stderr)
            break
        rows = []
        if added:
            cl = "\n".join(f"{i}. {c}" for i, c in enumerate(added))
            res = client.complete_json(
                [{"role": "system", "content": PROMPTS[a.prompt]},
                 {"role": "user", "content": f"Session:\n{fmt(dlg)}\n\nEarlier memory:\n{chr(10).join(before) or '(empty)'}\n\nClaims:\n{cl}"}],
                validator=lambda j: None if isinstance(j.get("claims"), list) else (_ for _ in ()).throw(ValueError("no claims list")))
            spent += (res.input_tokens or 0) * p_in + (res.output_tokens or 0) * p_out
            sup = {}
            for e in res.json["claims"]:
                try:
                    sup[int(e["i"])] = e.get("verdict") == "SUPPORTED"
                except (KeyError, ValueError, TypeError):
                    continue
            rows = [{"case_id": cid, "claim": c, "judge_supported": bool(sup.get(i, False))} for i, c in enumerate(added)]
        with vpath.open("a", encoding="utf-8") as fh:
            for r in rows or [{"case_id": cid, "claim": "", "judge_supported": True, "empty": True}]:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        done.add(cid)
    print(json.dumps({"sessions_done": len(done), "of": len(plan), "estimated_spend_usd": round(spent, 4)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
