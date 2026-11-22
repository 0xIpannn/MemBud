#!/usr/bin/env python3
"""Phase 2 conditions judged with a semantic (LLM) evidence grounder (paid; capped).

For every recorded proposal, one DeepSeek request judges all added claims against the
session (full dialogue + prior memory, same rubric as the human claim audit). Supported
claims get verbatim user-turn evidence via `JudgeGrounder`; then the unchanged verifier
runs all four conditions. Also reports agreement with the human claim-level gold
(re-judged here in batch form, so it is an independent check of the batch prompt).

The judge defines both evidence and the "reason" a proposal is held, so hold rates here
are NOT detection performance: nothing in this script is validated ground truth.
Sends HaluMem text to DeepSeek (data policy confirmed by the user). Output: counts only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from membud.conversation import verify_with_transcript  # noqa: E402
from membud.fairness import verify_text_fair  # noqa: E402
from membud.grounding import JudgeGrounder, JudgeVerdict  # noqa: E402
from membud.halumem import derive_silver_label, ground_transition, load_halumem_dataset  # noqa: E402
from membud.llm import OpenAICompatibleChatClient, enforce_deepseek_price, load_dotenv_0600  # noqa: E402
from membud.paired_stats import mcnemar_exact, wilson  # noqa: E402
from membud.schemas import Transition  # noqa: E402
from membud.verification import verify  # noqa: E402

CONDITIONS = ("unguarded", "graph", "text", "text_fair")
SYSTEM = """You verify claims that a memory system wrote about a user. You get one chat session as numbered turns \
labelled [user] or [assistant], earlier memory, and a numbered list of claims. For EACH claim decide whether the USER'S statements support it.
SUPPORTED: the user's words state it or clearly imply it, including paraphrase, different number/date formats \
(1983-05-06 = May 6, 1983; 75000 = $75,000), or a combination of facts the user did state. A fact stated only by the assistant \
does not count unless a user turn confirms or adopts it. Earlier memory is context only.
UNSUPPORTED: the claim adds a fact, number, name, event or detail the user did not give.
Ignore a run-date phrase such as "as of November 2026" or "around November 2026"; judge the rest of the claim.
For SUPPORTED claims, "turns" lists the numbers of the [user] turns that support or confirm it.
Answer with JSON only: {"claims": [{"i": <claim number>, "verdict": "SUPPORTED" or "UNSUPPORTED", "turns": [..]}, ...]} with one entry per claim."""


# Policy v2 (decided by the project owner 2026-11-22): a fact introduced by the assistant counts as
# supported when the user accepts it or when it rests on something the user said earlier.
SYSTEM_ACCEPTED = """You verify claims that a memory system wrote about a user. You get one chat session as numbered turns \
labelled [user] or [assistant], earlier memory, and a numbered list of claims. For EACH claim decide whether it is supported.
SUPPORTED if any of these holds:
 (a) the user's words state it or clearly imply it, including paraphrase, different number/date formats \
(1983-05-06 = May 6, 1983; 75000 = $75,000), or a combination of facts the user did state;
 (b) the assistant introduced it and the user accepted, adopted or acted on it (agreed, thanked, said they would try it, \
asked to continue with it, built on it) without rejecting it;
 (c) the assistant's statement rests on what the user said earlier in the session or on the earlier memory.
UNSUPPORTED: the claim adds a fact, number, name, event or detail that the user did not give, did not accept, and that does not rest on \
what the user said or on the earlier memory (for example an assistant-invented name or figure the user never engaged with, or \
something the user rejected).
Ignore a run-date phrase such as "as of November 2026" or "around November 2026"; judge the rest of the claim.
For SUPPORTED claims, "turns" lists the numbers of the [user] turns that state, accept or are the earlier basis for it.
Answer with JSON only: {"claims": [{"i": <claim number>, "verdict": "SUPPORTED" or "UNSUPPORTED", "turns": [..]}, ...]} with one entry per claim."""


def decide(cond, t, transcript):
    if cond == "unguarded":
        return "ACCEPT"
    if cond == "text_fair":
        return verify_with_transcript(t, transcript, "text", fair_text=True).decision
    return verify_with_transcript(t, transcript, cond, fair_text=False).decision


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--halumem-path", required=True)
    ap.add_argument("--dataset-revision", default="")
    ap.add_argument("--answers", required=True, help="human claim-audit answers JSON")
    ap.add_argument("--policy", choices=("strict", "accepted"), default="strict",
                    help="accepted: assistant-introduced facts count when the user accepts them or they rest on earlier user statements; "
                         "only proposals the strict run flagged are re-judged (the rest reuse cached strict verdicts)")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-spend-usd", type=float, required=True)
    a = ap.parse_args()
    run = Path(a.run_dir)
    _, users = load_halumem_dataset(a.halumem_path, dataset_revision=a.dataset_revision or "local")
    lookup = {f"{u.uuid}__{s.session_id}": (u, s) for u in users for s in u.sessions}
    load_dotenv_0600(ROOT / ".env", require_0600=True, populate_environ=True)
    client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
    price = enforce_deepseek_price(str(client.model), allow_paid=a.allow_paid)
    p_in, p_out = price["prompt_per_mtok"] / 1e6, price["completion_per_mtok"] / 1e6
    spend = {"est": 0.0, "calls": 0, "cached": 0, "tokens": 0}

    gold_h = json.loads(Path(a.answers).read_text())["answers"]
    items = [json.loads(l) for l in (run / "claim-audit" / "claim-items.jsonl").read_text().splitlines()]
    gold = {(i["case_id"], i["claim"]): gold_h[i["item_id"]]["decision"] for i in items
            if gold_h.get(i["item_id"], {}).get("decision") in ("SUPPORTED", "UNSUPPORTED")}

    flagged_cases = set()
    if a.policy == "accepted":
        flagged_cases = {v["case_id"] for v in map(json.loads, (run / "llm-claim-verdicts.jsonl").read_text().splitlines())
                         if not v["judge_supported"]}
    suffix = "-accepted" if a.policy == "accepted" else ""
    out = {"schema": "membud-halumem-phase2-llm-evidence-v1", "policy": a.policy, "validated": False, "model": str(client.model),
           "cap_usd": a.max_spend_usd, "per_proposal": [], "aborted": ""}
    cases, verdicts_all = [], {}
    for f in sorted((run / "proposals").glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        cid = rec["case_id"]
        u, s = lookup[cid]
        t_rec = Transition.from_dict(rec["transition"])
        # Recorded StagedMem0 evidence cites "messages[i]:user" refs that do not exist in the
        # HaluMem transcript (transcript grounding rejects them), so it is discarded here and
        # every added claim is judged afresh.
        t = Transition(case_id=t_rec.case_id, before=t_rec.before, candidate=t_rec.candidate, evidence=(),
                       required_claims=t_rec.required_claims, operations=t_rec.operations, schema_version=t_rec.schema_version)
        tr = s.to_transcript(user_uuid=u.uuid)
        before = {c for r in t.before for c in r.claims}
        supported_rec = {c for ev in t.evidence for c in ev.supports}
        added = [c for c in dict.fromkeys(c for r in t.candidate for c in r.claims if c not in before) if c not in supported_rec]
        user_srcs = tr.user_turns()
        # dialogue index -> user source index
        d2u, k = {}, 0
        for di, turn in enumerate(s.dialogue):
            if turn.role == "user":
                d2u[di] = k
                k += 1
        memo: dict[str, JudgeVerdict] = {}

        def judge(claim, sources, _added=added, _s=s, _t=t, _memo=memo, _d2u=d2u, _cid=cid):
            if not _memo:
                if spend["est"] >= a.max_spend_usd:
                    raise RuntimeError(f"spend cap reached (estimated ${spend['est']:.4f})")
                dlg = "\n".join(f"[{i}] [{x.role}] {x.content}" for i, x in enumerate(_s.dialogue))
                mem = "\n".join(c for r in _t.before for c in r.claims) or "(empty)"
                cl = "\n".join(f"{i}. {c}" for i, c in enumerate(_added))
                res = client.complete_json(
                    [{"role": "system", "content": SYSTEM_ACCEPTED if _cid in flagged_cases else SYSTEM},
                     {"role": "user", "content": f"Session:\n{dlg}\n\nEarlier memory:\n{mem}\n\nClaims:\n{cl}"}],
                    validator=lambda j: None if isinstance(j.get("claims"), list) else (_ for _ in ()).throw(ValueError("no claims list")),
                )
                spend["est"] += (res.input_tokens or 0) * p_in + (res.output_tokens or 0) * p_out
                spend["tokens"] += res.total_tokens or 0
                spend["cached" if res.cached else "calls"] += 1
                for e in res.json["claims"]:
                    try:
                        i = int(e["i"]); c = _added[i]
                    except (KeyError, ValueError, IndexError, TypeError):
                        continue
                    ut = tuple(_d2u[int(x)] for x in e.get("turns", []) if str(x).lstrip("-").isdigit() and int(x) in _d2u)
                    _memo[c] = JudgeVerdict(e.get("verdict") == "SUPPORTED", ut)
                for c in _added:  # a claim the judge skipped is unsupported (never assumed supported)
                    _memo.setdefault(c, JudgeVerdict(False))
            return _memo[claim]

        try:
            tt, rep = ground_transition(t, tr, JudgeGrounder(judge, label="deepseek-batch-judge")) if added else (t, None)
        except RuntimeError as exc:
            out["aborted"] = str(exc)
            break
        reasons = rep.reason_counts() if rep else {}
        for cg in (rep.claims if rep else ()):
            verdicts_all[(cid, cg.claim)] = cg.is_supported
        silver = derive_silver_label(t_rec, s, user_uuid=u.uuid, omission_scope="prior_only", normalize_numerals=True).primary_label
        cases.append((cid, tt, tr, silver, len(added), reasons))

    dec = {c: [decide(c, tt, tr) for _, tt, tr, _, _, _ in cases] for c in CONDITIONS}
    n = len(cases)
    summ = {c: {"hold": sum(d == "HOLD" for d in dec[c]), "of": n} for c in CONDITIONS}
    g, tf = dec["graph"], dec["text_fair"]
    b = sum(x == "HOLD" and y != "HOLD" for x, y in zip(g, tf)); cc = sum(x != "HOLD" and y == "HOLD" for x, y in zip(g, tf))
    codes: dict[str, int] = {}
    held_unsupported_only = held_other = 0
    for (_, tt, tr, _, _, rs), d in zip(cases, g):
        if d != "HOLD":
            continue
        res = verify_with_transcript(tt, tr, "graph", fair_text=False)
        cs = sorted({c.reason_code for c in res.checks if getattr(c, "reason_code", None) and getattr(c, "status", "") != "PASS"})
        for c in cs:
            codes[c] = codes.get(c, 0) + 1
        if rs.get("judge_unsupported"):
            held_unsupported_only += 1
        else:
            held_other += 1
    reason_tot: dict[str, int] = {}
    for *_, rs in cases:
        for k, v in rs.items():
            reason_tot[k] = reason_tot.get(k, 0) + v
    tot_added = sum(x[4] for x in cases)
    fully_supported = sum(1 for x in cases if x[4] and all(k in ("grounded", "already_supported") for k in x[5]))
    gh = [(verdicts_all[k], v) for k, v in gold.items() if k in verdicts_all]
    ag = sum((("SUPPORTED" if s_ else "UNSUPPORTED") == h) for s_, h in gh)
    silver_x = {}
    for (_, _, _, sv, _, _), gd in zip(cases, g):
        silver_x.setdefault(sv, {"ACCEPT": 0, "HOLD": 0})[gd] += 1
    out.update({
        "n_proposals": n, "added_claims_judged": tot_added, "claim_reason_counts": dict(sorted(reason_tot.items())),
        "proposals_with_every_claim_supported": fully_supported, "conditions": summ,
        "graph_vs_text_fair_discordant": {"graph_only_hold": b, "text_fair_only_hold": cc, "mcnemar_p": mcnemar_exact(b, cc)},
        "agreement_with_human_claim_gold": {"claims": len(gh), "agree": wilson(ag, len(gh))},
        "graph_decision_by_silver_numnorm_label": silver_x,
        "graph_hold_reason_codes_by_proposal": dict(sorted(codes.items())),
        "graph_holds_with_judge_unsupported_claim": held_unsupported_only,
        "graph_holds_without_judge_unsupported_claim": held_other,
        "spend_estimated_peak_rate_usd": round(spend["est"], 6), "paid_requests": spend["calls"], "cached_requests": spend["cached"], "tokens": spend["tokens"],
    })
    (run / f"llm-claim-verdicts{suffix}.jsonl").write_text("".join(
        json.dumps({"case_id": k[0], "claim": k[1], "judge_supported": v}, ensure_ascii=False) + "\n" for k, v in verdicts_all.items()))
    (run / f"llm-phase2-eval{suffix}.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "per_proposal"}, indent=2, sort_keys=True))
    return 1 if out["aborted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
