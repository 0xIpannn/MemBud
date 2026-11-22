#!/usr/bin/env python3
"""Higher-error extractor condition on the same 97 HaluMem sessions (paid DeepSeek; capped).

Mem0 hallucinates on ~1.5% of claims, too few for any guard to show a benefit. This script
replaces the extractor with an over-inferring DeepSeek prompt (EAGER_SYSTEM) so unsupported
claims are frequent, then judges every claim with the same batch claim judge and policy
(SYSTEM_ACCEPTED) that decided the Mem0 guard. It writes a run directory in the same layout as
phase2-deepseek, so scripts/run_halumem_repair.py and scripts/score_halumem_official.py run on it
unchanged (--run-dir).

These errors are INDUCED by the prompt; they are not Mem0's natural errors. The extractor is an LLM prompt, not Mem0.
Prior memory ("before") is copied from the recorded Mem0 transitions, so only the session's new claims differ.
The reply-context quote check of the Mem0 pipeline is not applied (documented deviation).
Modes: --estimate (offline), --run (needs --allow-paid and --max-spend-usd). Checkpointed and resumable.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

_spec = importlib.util.spec_from_file_location("llm_phase2", ROOT / "scripts" / "halumem_llm_phase2_eval.py")
_llm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_llm)
SYSTEM_ACCEPTED = _llm.SYSTEM_ACCEPTED

SRC = ROOT / "data" / "halumem-runs" / "phase2-deepseek"
EAGER_SYSTEM = """You are the memory module of a personal assistant. Read one chat session and write memories about the user \
that will help the assistant personalize future replies. Be proactive and complete: record what the user states, and also the \
preferences, habits, plans and background that the conversation implies, and the recommendations or details the assistant gave \
as things relevant to the user. Prefer specific memories with numbers, names and dates. One fact per claim. \
Do not repeat what earlier memory already says.
Answer with JSON only: {"claims": ["...", "..."]}"""


NEUTRAL_SYSTEM = """You write memories about a user from one chat session, for a personal assistant. Record the facts the user \
states about themselves, one fact per claim, with specific names, numbers and dates. Do not repeat what earlier memory already says.
Answer with JSON only: {"claims": ["...", "..."]}"""


def fmt(dialogue) -> str:
    return "\n".join(f"[{i}] [{t['role']}] {t['content']}" for i, t in enumerate(dialogue))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--halumem-path", default=str(Path.home() / "Repositories/HaluMem/HaluMem-Medium.jsonl"))
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--estimate", action="store_true")
    mode.add_argument("--run", action="store_true")
    ap.add_argument("--extractor", choices=("deepseek", "openrouter-free"), default="deepseek",
                    help="openrouter-free sends HaluMem text to a free OpenRouter endpoint (needs --confirm-data-policy)")
    ap.add_argument("--extractor-model", default="particle-ai/particle-1:free")
    ap.add_argument("--confirm-data-policy", action="store_true")
    ap.add_argument("--out-name", default="phase3-eager")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-spend-usd", type=float)
    a = ap.parse_args()

    global DST
    DST = ROOT / "data" / "halumem-runs" / a.out_name
    users = {}
    for line in Path(a.halumem_path).read_text(encoding="utf-8").splitlines():
        u = json.loads(line)
        users[u["uuid"]] = u
    srcs = [json.loads(f.read_text(encoding="utf-8")) for f in sorted((SRC / "proposals").glob("*.json"))]
    (DST / "proposals").mkdir(parents=True, exist_ok=True)
    verdicts_path = DST / "llm-claim-verdicts-accepted-replyctx.jsonl"  # name kept so downstream scripts run unchanged

    if a.estimate:
        tok = sum(len(fmt(users[d["user_uuid"]]["sessions"][d["session_index"] - 1]["dialogue"])) // 4 + 700 for d in srcs)
        est_in, est_out = tok * 2, len(srcs) * 2 * 500
        print(json.dumps({"sessions": len(srcs), "calls": 2 * len(srcs), "est_input_tokens": est_in, "est_output_tokens": est_out,
                          "est_cost_usd_peak_cache_miss": round(est_in * 3e-7 + est_out * 1.2e-6, 3)}, indent=2))
        return 0

    if not a.allow_paid or a.max_spend_usd is None:
        raise SystemExit("--run needs --allow-paid and --max-spend-usd")
    from membud.llm import OpenAICompatibleChatClient, enforce_deepseek_price, load_dotenv_0600

    load_dotenv_0600(ROOT / ".env", require_0600=True, populate_environ=True)
    client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
    price = enforce_deepseek_price(str(client.model), allow_paid=True)
    p_in, p_out = price["prompt_per_mtok"] / 1e6, price["completion_per_mtok"] / 1e6
    ext_client = client
    if a.extractor == "openrouter-free":
        import os

        if not a.confirm_data_policy:
            raise SystemExit("--confirm-data-policy required: HaluMem text is sent to OpenRouter free endpoints")
        from membud.llm import enforce_openrouter_zero_price

        os.environ["OPENROUTER_MODEL"] = a.extractor_model
        enforce_openrouter_zero_price(a.extractor_model)  # refuses any non-free model
        ext_client = OpenAICompatibleChatClient.from_openrouter_env()
    spent = 0.0
    judged = {json.loads(l)["case_id"] for l in verdicts_path.read_text().splitlines()} if verdicts_path.exists() else set()

    def call(messages, validator, cl=None):
        nonlocal spent
        for attempt in range(1, 6 if cl else 2):  # free endpoint: back off on upstream 429s
            try:
                res = (cl or client).complete_json(messages, validator=validator)
                break
            except Exception:
                if attempt >= (5 if cl else 1):
                    raise
                time.sleep(min(30 * attempt, 120))
        spent += (res.input_tokens or 0) * p_in + (res.output_tokens or 0) * p_out
        return res.json

    def need_list(key):
        return lambda j: None if isinstance(j.get(key), list) else (_ for _ in ()).throw(ValueError(f"no {key} list"))

    for d in srcs:
        cid = d["case_id"]
        if cid in judged:
            continue
        if spent >= a.max_spend_usd:
            print(f"STOP: estimated spend ${spent:.4f} reached cap; resume with --run", file=sys.stderr)
            break
        dlg = fmt(users[d["user_uuid"]]["sessions"][d["session_index"] - 1]["dialogue"])
        before = [c for r in d["transition"]["before"] for c in r["claims"]]
        prior = "\n".join(before) or "(empty)"
        pf = DST / "proposals" / f"{cid}.json"
        if pf.exists():
            claims = [p["memory"] for p in json.loads(pf.read_text())["mem0_proposals"]]
        else:
            system = EAGER_SYSTEM if a.extractor == "deepseek" else NEUTRAL_SYSTEM
            j = call([{"role": "system", "content": system},
                      {"role": "user", "content": f"Session:\n{dlg}\n\nEarlier memory:\n{prior}"}], need_list("claims"), ext_client)
            claims = [c.strip() for c in dict.fromkeys(j["claims"]) if isinstance(c, str) and c.strip() and c.strip() not in set(before)]
            rec = {"case_id": cid, "user_uuid": d["user_uuid"], "session_index": d["session_index"],
                   "transition": d["transition"], "extractor": f"{a.extractor}:{a.extractor_model if a.extractor != 'deepseek' else 'eager-prompt'}",
                   "mem0_proposals": [{"event": "ADD", "memory": c} for c in claims]}
            pf.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        rows = []
        if claims:
            cl = "\n".join(f"{i}. {c}" for i, c in enumerate(claims))
            j = call([{"role": "system", "content": SYSTEM_ACCEPTED},
                      {"role": "user", "content": f"Session:\n{dlg}\n\nEarlier memory:\n{prior}\n\nClaims:\n{cl}"}], need_list("claims"))
            sup = {}
            for e in j["claims"]:
                try:
                    sup[int(e["i"])] = e.get("verdict") == "SUPPORTED"
                except (KeyError, ValueError, TypeError):
                    continue
            rows = [{"case_id": cid, "claim": c, "judge_supported": bool(sup.get(i, False))} for i, c in enumerate(claims)]
        with verdicts_path.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            if not rows:  # marker so an empty session is not redone
                fh.write(json.dumps({"case_id": cid, "claim": "", "judge_supported": True, "empty": True}) + "\n")
        judged.add(cid)
    print(json.dumps({"sessions_done": len(judged), "of": len(srcs), "estimated_spend_usd": round(spent, 4)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
