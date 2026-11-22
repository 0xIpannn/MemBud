#!/usr/bin/env python3
"""Second-judge replication of the claim-level guard judge on a free OpenRouter model.

Runs the same batch claim-judge prompt (accepted-assistant-facts policy) that decided the guard on
the 97 recorded Mem0 proposals, but with a different model family, then compares:
  * claim-level agreement with the DeepSeek verdicts,
  * which sessions each judge would HOLD,
  * agreement with the two reviewers on the 36-claim dual-review sample (flag precision),
  * the guard's recall cost when its hold decisions come from this judge (official-metric scores
    are the cached DeepSeek official judge scores; only the guard decisions change).
Free models only: the zero-price guard refuses any model with a non-zero price. HaluMem text is sent to
OpenRouter's free endpoints (data policy confirmed by the project owner 2026-11-22; --confirm-data-policy
is required). Checkpointed per proposal; resumable; request cap per invocation.
Modes: --run, --analyze (offline).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from membud.paired_stats import wilson  # noqa: E402

RUN = ROOT / "data" / "halumem-runs" / "phase2-deepseek"


def targets(run: Path, halumem_path: Path):
    users = {}
    for line in halumem_path.read_text(encoding="utf-8").splitlines():
        u = json.loads(line)
        users[u["uuid"]] = u
    out = []
    for f in sorted((run / "proposals").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        s = users[d["user_uuid"]]["sessions"][d["session_index"] - 1]
        before = [c for r in d["transition"]["before"] for c in r["claims"]]
        bset = set(before)
        added = [c for c in dict.fromkeys(p["memory"] for p in d["mem0_proposals"] if p.get("event") == "ADD") if c not in bset]
        out.append({"case_id": d["case_id"], "added": added, "prior": before, "dialogue": s["dialogue"]})
    return out


def run_judge(a) -> None:
    if not a.confirm_data_policy:
        raise SystemExit("--confirm-data-policy required: HaluMem text is sent to OpenRouter free endpoints")
    from halumem_llm_phase2_eval import SYSTEM_ACCEPTED
    from membud.llm import OpenAICompatibleChatClient, enforce_openrouter_zero_price, load_dotenv_0600

    load_dotenv_0600(ROOT / ".env", require_0600=True, populate_environ=True)
    os.environ["OPENROUTER_MODEL"] = a.model
    enforce_openrouter_zero_price(a.model)  # refuses any non-free model
    client = OpenAICompatibleChatClient.from_openrouter_env()
    out_dir = RUN / "second-judge" / a.model.replace("/", "__").replace(":", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    res_path = out_dir / "results.jsonl"
    done = {json.loads(l)["case_id"] for l in res_path.read_text().splitlines()} if res_path.exists() else set()
    n_req = 0
    for t in targets(RUN, Path(a.halumem_path)):
        if t["case_id"] in done or not t["added"]:
            if not t["added"] and t["case_id"] not in done:
                with res_path.open("a") as fh:
                    fh.write(json.dumps({"case_id": t["case_id"], "verdicts": [], "error": ""}) + "\n")
            continue
        if n_req >= a.max_requests:
            print(f"STOP: request cap {a.max_requests} reached; resume with --run", file=sys.stderr)
            break
        dlg = "\n".join(f"[{i}] [{x['role']}] {x['content']}" for i, x in enumerate(t["dialogue"]))
        mem = "\n".join(t["prior"]) or "(empty)"
        cl = "\n".join(f"{i}. {c}" for i, c in enumerate(t["added"]))
        msgs = [{"role": "system", "content": SYSTEM_ACCEPTED},
                {"role": "user", "content": f"Session:\n{dlg}\n\nEarlier memory:\n{mem}\n\nClaims:\n{cl}"}]
        err, verdicts = "", None
        for attempt in range(1, a.outer_retries + 1):
            n_req += 1
            try:
                r = client.complete_json(msgs, validator=lambda j: None if isinstance(j.get("claims"), list) else (_ for _ in ()).throw(ValueError("no claims list")))
                sup = {}
                for e in r.json["claims"]:
                    try:
                        sup[int(e["i"])] = e.get("verdict") == "SUPPORTED"
                    except (KeyError, ValueError, TypeError):
                        continue
                verdicts = [{"claim": c, "supported": sup.get(i), "returned": i in sup} for i, c in enumerate(t["added"])]
                err = ""
                break
            except Exception as exc:  # 429s and JSON failures: back off and retry
                err = type(exc).__name__
                time.sleep(min(30 * attempt, 180))
        with res_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"case_id": t["case_id"], "verdicts": verdicts or [], "error": err}, ensure_ascii=False) + "\n")
        print(f"{t['case_id'][-8:]} {'ok' if verdicts else 'FAILED ' + err}", flush=True)
    print(json.dumps({"requests_attempted": n_req, "model": a.model}))


def analyze(a) -> None:
    out_dir = RUN / "second-judge" / a.model.replace("/", "__").replace(":", "_")
    rows = {r["case_id"]: r for r in map(json.loads, (out_dir / "results.jsonl").read_text().splitlines())}
    ds = {}
    for v in map(json.loads, (RUN / "llm-claim-verdicts-accepted-replyctx.jsonl").read_text().splitlines()):
        ds[(v["case_id"], v["claim"])] = v["judge_supported"]
    ok_cases = {c for c, r in rows.items() if r["verdicts"] or not r["error"]}
    failed = sorted(c for c, r in rows.items() if r["error"] and not r["verdicts"])
    pairs, unreturned = [], 0
    for c in ok_cases:
        for v in rows[c]["verdicts"]:
            if not v["returned"]:
                unreturned += 1
            g = bool(v["supported"]) if v["returned"] else False  # a skipped claim counts as unsupported
            if (c, v["claim"]) in ds:
                pairs.append((ds[(c, v["claim"])], g))
    agree = sum(x == y for x, y in pairs)
    both_flag = sum((not x) and (not y) for x, y in pairs)
    ds_flag = sum(not x for x, _ in pairs)
    gm_flag = sum(not y for _, y in pairs)
    gem_hold = {c for c in ok_cases if any(not (v["supported"] if v["returned"] else False) for v in rows[c]["verdicts"])}
    ds_hold = {c for c in ok_cases if any(not ds.get((c, v["claim"]), True) for v in rows[c]["verdicts"])}
    res = {"model": a.model, "proposals_ok": len(ok_cases), "proposals_failed": failed,
           "claims_compared": len(pairs), "claims_not_returned_by_model": unreturned,
           "claim_agreement_with_deepseek": wilson(agree, len(pairs)),
           "deepseek_flags": ds_flag, "model_flags": gm_flag, "flagged_by_both": both_flag,
           "sessions_held_by_model": len(gem_hold), "sessions_held_by_deepseek": len(ds_hold),
           "sessions_held_by_both": len(gem_hold & ds_hold)}
    # Dual-review sample: judge flag precision per reviewer using this model's verdicts.
    dual = RUN / "claim-audit-dual"
    if (dual / "claim-items.jsonl").exists():
        items = {i["item_id"]: i for i in map(json.loads, (dual / "claim-items.jsonl").read_text().splitlines())}
        revs = {}
        for rp in a.reviewer_files:
            j = json.loads(Path(rp).read_text())
            revs[j["reviewer"]] = {k: (v or {}).get("decision") for k, v in j["answers"].items()}
        mv = {}
        for c, r in rows.items():
            for v in r["verdicts"]:
                mv[(c, v["claim"])] = bool(v["supported"]) if v["returned"] else False
        res["dual_sample"] = {}
        for name, lab in revs.items():
            flagged = [i for i, it in items.items() if (it["case_id"], it["claim"]) in mv and not mv[(it["case_id"], it["claim"])] and lab.get(i) in ("SUPPORTED", "UNSUPPORTED")]
            res["dual_sample"][name] = {"model_flags_in_sample": len(flagged),
                                        "flag_precision": wilson(sum(lab[i] == "UNSUPPORTED" for i in flagged), len(flagged))}
    # Guard recall cost with this model's hold decisions (cached official judge scores).
    import importlib.util
    spec = importlib.util.spec_from_file_location("s", ROOT / "scripts/score_halumem_official.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    items_ = m.build_items(RUN, Path(a.halumem_path))
    cache = m.load_cache(RUN / "official/judge-results.jsonl")
    lost = 0
    gold = sum(1 for it in items_ for g in it["gold"] if g["memory_source"] != "interference")
    for it in items_:
        if it["case_id"] in gem_hold:
            lost += sum(1 for g in it["gold"] if g["memory_source"] != "interference" and str(cache[f"I|{it['case_id']}|{g['index']}"]["score"]) == "2")
    res["guard_recall_cost_with_this_judge"] = {"held_sessions": len(gem_hold), "recalled_gold_lost": lost,
                                                "of_gold_points": gold, "recall_points_lost": round(lost / gold, 4)}
    (out_dir / "analysis.json").write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="google/gemma-4-31b-it:free")
    ap.add_argument("--halumem-path", default=str(Path.home() / "Repositories/HaluMem/HaluMem-Medium.jsonl"))
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--analyze", action="store_true")
    ap.add_argument("--confirm-data-policy", action="store_true")
    ap.add_argument("--max-requests", type=int, default=300)
    ap.add_argument("--outer-retries", type=int, default=4)
    ap.add_argument("--reviewer-files", nargs="*", default=[])
    a = ap.parse_args()
    run_judge(a) if a.run else analyze(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
