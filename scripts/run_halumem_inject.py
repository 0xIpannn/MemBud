#!/usr/bin/env python3
"""Injected-fault condition built on the recorded Mem0 extractions (paid DeepSeek; capped).

Natural Mem0 errors are too rare (1.6% of claims) to show a guard's effect. This script injects faults
into the recorded extractions at a known rate and writes a run directory (phase3-injected) in the layout
of phase2-deepseek, so scripts/run_halumem_repair.py and scripts/score_halumem_official.py run on it
unchanged (--run-dir). In a seeded 50% of sessions it (1) replaces one extracted claim with a corrupted
version (membud.halumem._corrupt_claim_text, the phase-1 injector) and (2) appends one hallucinated claim
(membud.halumem._pick_hallucinated_claim). Injected claims are recorded in each proposal under "injected".

These faults are SYNTHETIC. Results show what the guard does when errors exist at this rate, not how often
errors occur naturally. Because the injector creates fault and label together, the injected set is not a
validated label of "unsupported"; it only tells whether the claim judge catches the injected claims.
Untouched sessions reuse the recorded claim verdicts and official judge scores (same keys). Touched sessions
are judged anew with the same batch claim judge (SYSTEM_ACCEPTED).
Modes: --estimate (offline), --run (needs --allow-paid and --max-spend-usd). Checkpointed and resumable.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from membud import halumem as hm  # noqa: E402

_spec = importlib.util.spec_from_file_location("llm_phase2", ROOT / "scripts" / "halumem_llm_phase2_eval.py")
_llm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_llm)
SYSTEM_ACCEPTED = _llm.SYSTEM_ACCEPTED

SRC = ROOT / "data" / "halumem-runs" / "phase2-deepseek"
SEED = 20261123
RATE = 0.5  # defaults; --rate / --band-from select sessions with band_from <= u < rate (u = seeded hash in [0,1))
BAND_FROM = 0.0


def fmt(dialogue) -> str:
    return "\n".join(f"[{i}] [{t['role']}] {t['content']}" for i, t in enumerate(dialogue))


def session_u(case_id: str) -> float:
    return (int(hashlib.sha256(f"{SEED}|{case_id}".encode()).hexdigest(), 16) % 1000) / 1000


def inject(case_id: str, claims: list[str], dialogue, prior: list[str], rate: float = RATE, band_from: float = BAND_FROM):
    """Return (new_claims, injected_records) or (claims, []) when this session is not selected."""
    h = int(hashlib.sha256(f"{SEED}|{case_id}".encode()).hexdigest(), 16)
    if not (band_from <= (h % 1000) / 1000 < rate) or not claims:
        return claims, []
    rng = random.Random(h)
    tokens = hm._content_tokens(" ".join(t["content"] for t in dialogue))
    new, rec = list(claims), []
    i = rng.randrange(len(new))
    bad = hm._corrupt_claim_text(new[i], rng, tokens)
    if bad != new[i]:
        rec.append({"kind": "corruption", "original": new[i], "claim": bad})
        new[i] = bad
    halluc = hm._pick_hallucinated_claim(rng, set(new) | set(prior), tokens)
    rec.append({"kind": "hallucination", "claim": halluc})
    new.append(halluc)
    return list(dict.fromkeys(new)), rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--halumem-path", default=str(Path.home() / "Repositories/HaluMem/HaluMem-Medium.jsonl"))
    ap.add_argument("--out-name", default="phase3-injected")
    ap.add_argument("--rate", type=float, default=RATE, help="inject sessions with u < rate (nested: larger rate = superset)")
    ap.add_argument("--band-from", type=float, default=BAND_FROM, help="inject only sessions with u >= this (to add a new band)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--estimate", action="store_true")
    mode.add_argument("--run", action="store_true")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-spend-usd", type=float)
    a = ap.parse_args()

    dst = ROOT / "data" / "halumem-runs" / a.out_name
    (dst / "proposals").mkdir(parents=True, exist_ok=True)
    (dst / "official").mkdir(exist_ok=True)
    users = {}
    for line in Path(a.halumem_path).read_text(encoding="utf-8").splitlines():
        u = json.loads(line)
        users[u["uuid"]] = u
    old_verdicts: dict[str, list[dict]] = {}
    for l in (SRC / "llm-claim-verdicts-accepted-replyctx.jsonl").read_text().splitlines():
        v = json.loads(l)
        old_verdicts.setdefault(v["case_id"], []).append(v)

    plan = []
    for f in sorted((SRC / "proposals").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        s = users[d["user_uuid"]]["sessions"][d["session_index"] - 1]
        prior = [c for r in d["transition"]["before"] for c in r["claims"]]
        adds = [p["memory"] for p in d["mem0_proposals"] if p.get("event") == "ADD"]
        new, rec = inject(d["case_id"], adds, s["dialogue"], prior, a.rate, a.band_from)
        plan.append((d, s, prior, new, rec))
    touched = [p for p in plan if p[4]]

    if a.estimate:
        tok = sum(len(fmt(s["dialogue"])) // 4 + 700 for _, s, *_ in touched)
        print(json.dumps({"sessions": len(plan), "sessions_injected": len(touched),
                          "claims_injected": sum(len(p[4]) for p in plan), "judge_calls": len(touched),
                          "est_cost_usd_peak_cache_miss": round(tok * 3e-7 + len(touched) * 500 * 1.2e-6, 3)}, indent=2))
        return 0

    if not a.allow_paid or a.max_spend_usd is None:
        raise SystemExit("--run needs --allow-paid and --max-spend-usd")
    from membud.llm import OpenAICompatibleChatClient, enforce_deepseek_price, load_dotenv_0600

    load_dotenv_0600(ROOT / ".env", require_0600=True, populate_environ=True)
    client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
    price = enforce_deepseek_price(str(client.model), allow_paid=True)
    p_in, p_out = price["prompt_per_mtok"] / 1e6, price["completion_per_mtok"] / 1e6
    spent = 0.0
    vpath = dst / "llm-claim-verdicts-accepted-replyctx.jsonl"
    done = {json.loads(l)["case_id"] for l in vpath.read_text().splitlines()} if vpath.exists() else set()
    old_cache = {}
    for l in (SRC / "official" / "judge-results.jsonl").read_text().splitlines():
        r = json.loads(l)
        old_cache[r["key"]] = r
    for d, s, prior, new, rec in plan:
        cid = d["case_id"]
        if cid in done:
            continue
        if spent >= a.max_spend_usd:
            print(f"STOP: estimated spend ${spent:.4f} reached cap; resume with --run", file=sys.stderr)
            break
        props = [{"event": "ADD", "memory": c} for c in new]
        out = {"case_id": cid, "user_uuid": d["user_uuid"], "session_index": d["session_index"], "transition": d["transition"],
               "injected": rec, "mem0_proposals": props}
        (dst / "proposals" / f"{cid}.json").write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
        if not rec:  # untouched: reuse verdicts verbatim
            rows = old_verdicts.get(cid, [])
        else:
            cl = "\n".join(f"{i}. {c}" for i, c in enumerate(new))
            res = client.complete_json(
                [{"role": "system", "content": SYSTEM_ACCEPTED},
                 {"role": "user", "content": f"Session:\n{fmt(s['dialogue'])}\n\nEarlier memory:\n{chr(10).join(prior) or '(empty)'}\n\nClaims:\n{cl}"}],
                validator=lambda j: None if isinstance(j.get("claims"), list) else (_ for _ in ()).throw(ValueError("no claims list")))
            spent += (res.input_tokens or 0) * p_in + (res.output_tokens or 0) * p_out
            sup = {}
            for e in res.json["claims"]:
                try:
                    sup[int(e["i"])] = e.get("verdict") == "SUPPORTED"
                except (KeyError, ValueError, TypeError):
                    continue
            rows = [{"case_id": cid, "claim": c, "judge_supported": bool(sup.get(i, False))} for i, c in enumerate(new)]
        with vpath.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            if not rows:
                fh.write(json.dumps({"case_id": cid, "claim": "", "judge_supported": True, "empty": True}) + "\n")
        if not rec:  # untouched session: copy its official judge scores (identical keys and inputs)
            with (dst / "official" / "judge-results.jsonl").open("a", encoding="utf-8") as fh:
                for k, r in old_cache.items():
                    if k.split("|")[1] == cid and k[0] in "IA" and k[1] == "|":
                        fh.write(json.dumps(r) + "\n")
        done.add(cid)
    print(json.dumps({"sessions_done": len(done), "of": len(plan), "estimated_spend_usd": round(spent, 4)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
