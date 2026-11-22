#!/usr/bin/env python3
"""Repair loop on the recorded Mem0 proposals that the guard HELD (paid DeepSeek; capped).

For each session whose proposal the claim-level guard held, the original extraction is the
starting candidate. The verifier is the batch claim judge (same prompt/policy as the guard
evaluation); its UNSUPPORTED claims are the offending claims. On HOLD the extractor (a
DeepSeek re-prompt, NOT Mem0 itself: Mem0 has no feedback hook) receives the reason codes and
offending claims and returns a corrected claim list, up to --max-retries times (default 2).
Every attempt is logged. A retry never authorizes a commit: only a final candidate that passes
verification and a second re-verification is committed. Results are checkpointed per session.

Deviations (documented, not hidden):
* The guard here is the judge-claim guard, not a per-session re-run of the structural verifier.
* The initial verification reuses the recorded verdicts (no call). Re-verification of an
  unchanged prompt is served from the client's cache, so it confirms determinism, not a new opinion.
* The repair extractor is a different component from Mem0; "repair" results are about
  this extractor, not about repairing Mem0.
Modes: --estimate (offline), --run (needs --allow-paid and --max-spend-usd), --summary (offline).
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

from membud.repair import VerifyOutcome, run_repair, summarize  # noqa: E402

_spec = importlib.util.spec_from_file_location("llm_phase2", ROOT / "scripts" / "halumem_llm_phase2_eval.py")
_llm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_llm)
SYSTEM_ACCEPTED = _llm.SYSTEM_ACCEPTED

RUN = ROOT / "data" / "halumem-runs" / "phase2-deepseek"
EXTRACT_SYSTEM = """You correct a list of memory claims that a memory system wrote about a user after reading one chat session. \
A checker marked some claims UNSUPPORTED. You get the session as numbered turns, earlier memory, the current claims, the \
offending claims and the reason codes.
Return the corrected claim list. Keep every claim that was not flagged EXACTLY as written. For each flagged claim, either delete it \
or rewrite it so it states only what the session supports: the user's own statements, or an assistant fact the user adopted, or \
what follows from earlier memory. Do NOT add any new fact, number, name or detail that the session does not support. Do not \
invent. If a flagged claim cannot be repaired, delete it.
Answer with JSON only: {"claims": ["...", "..."]}"""


def fmt_dialogue(dialogue) -> str:
    return "\n".join(f"[{i}] [{t['role']}] {t['content']}" for i, t in enumerate(dialogue))


def load_targets(run: Path, halumem_path: Path):
    verdicts = [json.loads(l) for l in (run / "llm-claim-verdicts-accepted-replyctx.jsonl").read_text().splitlines()]
    by_case: dict[str, dict[str, bool]] = {}
    for v in verdicts:
        by_case.setdefault(v["case_id"], {})[v["claim"]] = v["judge_supported"]
    users = {}
    for line in halumem_path.read_text(encoding="utf-8").splitlines():
        u = json.loads(line)
        users[u["uuid"]] = u
    out = []
    for cid, vs in sorted(by_case.items()):
        if all(vs.values()):
            continue
        d = json.loads((run / "proposals" / f"{cid}.json").read_text())
        s = users[d["user_uuid"]]["sessions"][d["session_index"] - 1]
        out.append({
            "case_id": cid,
            "claims": [p["memory"] for p in d["mem0_proposals"] if p.get("event") == "ADD"],
            "prior": [c for r in d["transition"]["before"] for c in r["claims"]],
            "dialogue": s["dialogue"],
            "recorded": vs,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=str(RUN))
    ap.add_argument("--halumem-path", default=str(Path.home() / "Repositories/HaluMem/HaluMem-Medium.jsonl"))
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--estimate", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--summary", action="store_true")
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-spend-usd", type=float)
    a = ap.parse_args()

    run = Path(a.run_dir)
    out_dir = run / "repair"
    out_dir.mkdir(exist_ok=True)
    results_path, attempts_path = out_dir / "results.jsonl", out_dir / "attempts.jsonl"
    targets = load_targets(run, Path(a.halumem_path))
    done = {r["case_id"]: r for r in map(json.loads, results_path.read_text().splitlines())} if results_path.exists() else {}

    if a.estimate:
        todo = [t for t in targets if t["case_id"] not in done]
        tok = sum(len(fmt_dialogue(t["dialogue"])) // 4 + 600 for t in todo)
        calls = len(todo) * 2 * a.max_retries  # worst case: extractor + verifier per retry
        est_in, est_out = tok * 2 * a.max_retries, calls * 500
        print(json.dumps({
            "held_sessions": len(targets), "todo": len(todo), "worst_case_calls": calls,
            "est_input_tokens": est_in, "est_output_tokens": est_out,
            "est_cost_usd_peak_cache_miss": round(est_in * 3e-7 + est_out * 1.2e-6, 3)}, indent=2))
        return 0

    if a.summary:
        res = [r for r in done.values()]
        from membud.repair import RepairResult, Attempt
        rr = []
        for r in res:
            at = tuple(Attempt(x["index"], tuple(x["claims"]), x["accepted"], tuple(x["reason_codes"]),
                               tuple(x["offending_claims"]), x.get("error", "")) for x in r["attempts"])
            rr.append(RepairResult(at, r["final_decision"], tuple(r["final_claims"]), r["initial_decision"], r["retries_used"]))
        s = summarize(rr)
        print(json.dumps(s.__dict__, indent=2))
        return 0

    if not a.allow_paid or a.max_spend_usd is None:
        raise SystemExit("--run needs --allow-paid and --max-spend-usd")
    from membud.llm import OpenAICompatibleChatClient, enforce_deepseek_price, load_dotenv_0600

    load_dotenv_0600(ROOT / ".env", require_0600=True, populate_environ=True)
    client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
    price = enforce_deepseek_price(str(client.model), allow_paid=True)
    p_in, p_out = price["prompt_per_mtok"] / 1e6, price["completion_per_mtok"] / 1e6
    spent = 0.0

    def call(messages, validator):
        nonlocal spent
        if spent >= a.max_spend_usd:
            raise RuntimeError("spend cap reached")
        res = client.complete_json(messages, validator=validator)
        spent += (res.input_tokens or 0) * p_in + (res.output_tokens or 0) * p_out
        return res.json

    for t in targets:
        if t["case_id"] in done:
            continue
        if spent >= a.max_spend_usd:
            print(f"STOP: estimated spend ${spent:.4f} reached cap; resume with --run", file=sys.stderr)
            break
        dlg, prior = fmt_dialogue(t["dialogue"]), "\n".join(t["prior"]) or "(empty)"
        first = {"claims": tuple(t["claims"])}

        def verify(claims, t=t, dlg=dlg, prior=prior, first=first):
            if tuple(claims) == first["claims"]:  # initial proposal: reuse the recorded verdicts
                bad = tuple(c for c in claims if not t["recorded"].get(c, False))
                return VerifyOutcome(not bad, ("UNSUPPORTED_CLAIM",) if bad else (), bad)
            cl = "\n".join(f"{i}. {c}" for i, c in enumerate(claims))
            j = call([{"role": "system", "content": SYSTEM_ACCEPTED},
                      {"role": "user", "content": f"Session:\n{dlg}\n\nEarlier memory:\n{prior}\n\nClaims:\n{cl}"}],
                     lambda j: None if isinstance(j.get("claims"), list) else (_ for _ in ()).throw(ValueError("no claims list")))
            sup = {}
            for e in j["claims"]:
                try:
                    sup[int(e["i"])] = e.get("verdict") == "SUPPORTED"
                except (KeyError, ValueError, TypeError):
                    continue
            bad = tuple(c for i, c in enumerate(claims) if not sup.get(i, False))  # skipped = unsupported
            return VerifyOutcome(not bad, ("UNSUPPORTED_CLAIM",) if bad else (), bad)

        def extract(prev, offenders, codes, n, dlg=dlg, prior=prior):
            cl = "\n".join(f"{i}. {c}" for i, c in enumerate(prev))
            off = "\n".join(f"- {c}" for c in offenders) or "(none listed)"
            j = call([{"role": "system", "content": EXTRACT_SYSTEM},
                      {"role": "user", "content": f"Session:\n{dlg}\n\nEarlier memory:\n{prior}\n\nCurrent claims:\n{cl}\n\n"
                                                   f"Flagged claims (reason {', '.join(codes)}):\n{off}"}],
                     lambda j: None if isinstance(j.get("claims"), list) and all(isinstance(x, str) for x in j["claims"])
                     else (_ for _ in ()).throw(ValueError("no claims list")))
            return [c.strip() for c in j["claims"] if c.strip()]

        try:
            r = run_repair(t["claims"], verify, extract, max_retries=a.max_retries)
        except RuntimeError as exc:  # cap reached mid-session: leave it un-done so --run resumes it
            print(f"STOP: {exc}", file=sys.stderr)
            break
        rec = {"case_id": t["case_id"], "initial_decision": r.initial_decision, "final_decision": r.final_decision,
               "final_claims": list(r.final_claims), "retries_used": r.retries_used,
               "attempts": [{"index": x.index, "claims": list(x.claims), "accepted": x.accepted, "reason_codes": list(x.reason_codes),
                             "offending_claims": list(x.offending_claims), "error": x.error} for x in r.attempts]}
        with attempts_path.open("a", encoding="utf-8") as fh:
            for x in rec["attempts"]:
                fh.write(json.dumps({"case_id": t["case_id"], **x}, ensure_ascii=False) + "\n")
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        done[t["case_id"]] = rec
    print(json.dumps({"sessions_done": len(done), "of": len(targets), "estimated_spend_usd": round(spent, 4)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
