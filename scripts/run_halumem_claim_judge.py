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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--halumem-path", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--estimate", action="store_true")
    mode.add_argument("--run", action="store_true")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-spend-usd", type=float)
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
    vpath = run / "llm-claim-verdicts-accepted-replyctx.jsonl"
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
                [{"role": "system", "content": SYSTEM_ACCEPTED},
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
