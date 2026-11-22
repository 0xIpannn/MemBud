#!/usr/bin/env python3
"""Score recorded Mem0 proposals with HaluMem's official extraction metrics.

Inputs: the recorded Phase 2 proposals (Mem0 output per session), the local HaluMem JSONL,
and the official judge prompts, which are read from a local clone of MemTensor/HaluMem
(never vendored). Judge outputs are cached per item, so the guarded conditions reuse the
unguarded judgments (a committed session has the identical extraction; a held session writes
nothing and every gold point scores 0 by the reference rule for empty extractions).

Modes
  --estimate   offline: count judge calls and estimate tokens/cost. No network.
  --run        paid DeepSeek calls; requires --allow-paid and --max-spend-usd; checkpointed.
  --score      offline: aggregate whatever is cached and report per condition.

What is NOT computed here: update metrics (need Mem0 retrieval per update point, not recorded)
and QA metrics (need answers generated from retrieved memory, not recorded). The 108
`is_update` gold points in the sampled sessions are excluded from recall because the reference
code routes them to the update evaluation when retrieval output exists.
Judge deviation: official code uses GPT-4o; this uses the configured DeepSeek model.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from membud.halumem_metrics import (  # noqa: E402
    DEVIATIONS_FROM_PAPER,
    REFERENCE_COMMIT,
    AccuracyRecord,
    IntegrityRecord,
    aggregate_extraction,
    apply_commit_decision,
)

DEFAULT_RUN = ROOT / "data" / "halumem-runs" / "phase2-deepseek"
DEFAULT_REPO = Path.home() / "Repositories" / "HaluMem-repo"
CONDITIONS = ("unguarded", "guard_strict_recorded", "guard_judge_claims", "guard_repair", "guard_claim_drop")


def load_prompts(repo: Path) -> dict[str, str]:
    tree = ast.parse((repo / "eval" / "eval_tools.py").read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id.startswith("EVALUATION_PROMPT"):
            out[node.targets[0].id] = ast.literal_eval(node.value)
    need = {"EVALUATION_PROMPT_FOR_MEMORY_INTEGRITY", "EVALUATION_PROMPT_FOR_MEMORY_ACCURACY"}
    if not need <= set(out):
        raise SystemExit(f"official prompts not found in {repo}")
    return out


def dialogue_string(session: dict) -> str:
    # Mirrors eval/evaluation.py process_user().
    lines = []
    for t in session["dialogue"]:
        lines.append(f'[{t["timestamp"]}]{t["role"]}: {t["content"]}')
        if t["role"] == "assistant":
            lines.append("")
    return "\n".join(lines)


def build_items(run: Path, halumem_path: Path) -> list[dict]:
    props = {}
    for f in sorted((run / "proposals").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        props[d["case_id"]] = d
    users = {}
    for line in halumem_path.read_text(encoding="utf-8").splitlines():
        u = json.loads(line)
        users[u["uuid"]] = u
    flagged: dict[str, set] = {}
    for v in map(json.loads, (run / "llm-claim-verdicts-accepted-replyctx.jsonl").read_text().splitlines()):
        if not v["judge_supported"]:
            flagged.setdefault(v["case_id"], set()).add(v["claim"])
    unsupported_cases = set(flagged)
    repaired = {}
    rp = run / "repair" / "results.jsonl"
    if rp.exists():
        for r in map(json.loads, rp.read_text().splitlines()):
            repaired[r["case_id"]] = r
    items = []
    for cid, d in props.items():
        sid = d["session_index"] - 1
        s = users[d["user_uuid"]]["sessions"][sid]
        extracted = [p["memory"] for p in d["mem0_proposals"] if p.get("event") == "ADD"]
        gold = s["memory_points"]
        items.append({
            "case_id": cid, "sid": sid, "uuid": d["user_uuid"],
            "extracted": extracted,
            "injected": d.get("injected") or [],
            "dialogue": dialogue_string(s),
            "gold_str": "\n".join(m["memory_content"] for m in gold if m["memory_source"] != "interference"),
            "gold": [m for m in gold if str(m.get("is_update")) != "True"],
            "update_points_excluded": sum(str(m.get("is_update")) == "True" for m in gold),
            "questions": len(s.get("questions", [])),
            "guard_judge_commits": cid not in unsupported_cases,
            # claim-level guard: drop only the flagged claims, keep the rest (original indices kept for A| keys)
            "drop_kept": ([(k, m) for k, m in enumerate(extracted) if m not in flagged[cid]] if cid in flagged else None),
            "repair_attempted": cid in repaired,
            "repair_claims": (repaired[cid]["final_claims"] if cid in repaired and repaired[cid]["final_decision"] == "ACCEPT" else None),
        })
    return items


def jobs(items, prompts):
    """Yield (key, kind, prompt) for every judge call the unguarded extraction needs."""
    for it in items:
        ext = "\n".join(it["extracted"])
        if ext.strip():
            for g in it["gold"]:
                p = prompts["EVALUATION_PROMPT_FOR_MEMORY_INTEGRITY"].format(memories=ext, expected_memory_point=g["memory_content"])
                yield f"I|{it['case_id']}|{g['index']}", "integrity", p
        for k, m in enumerate(it["extracted"]):
            cand = {"uuid": it["uuid"], "ssession_id": it["sid"], "memory_content": m}
            p = prompts["EVALUATION_PROMPT_FOR_MEMORY_ACCURACY"].format(
                dialogue=it["dialogue"], golden_memories=it["gold_str"], candidate_memory=cand)
            yield f"A|{it['case_id']}|{k}", "accuracy", p


def repair_jobs(items, prompts):
    """Judge calls for repaired (committed) extractions, keyed with an R prefix."""
    for it in items:
        rc = it.get("repair_claims")
        if not rc:
            continue
        ext = "\n".join(rc)
        for g in it["gold"]:
            p = prompts["EVALUATION_PROMPT_FOR_MEMORY_INTEGRITY"].format(memories=ext, expected_memory_point=g["memory_content"])
            yield f"RI|{it['case_id']}|{g['index']}", "integrity", p
        for k, m in enumerate(rc):
            cand = {"uuid": it["uuid"], "ssession_id": it["sid"], "memory_content": m}
            p = prompts["EVALUATION_PROMPT_FOR_MEMORY_ACCURACY"].format(
                dialogue=it["dialogue"], golden_memories=it["gold_str"], candidate_memory=cand)
            yield f"RA|{it['case_id']}|{k}", "accuracy", p


def drop_jobs(items, prompts):
    """Integrity judge calls for the claim-level guard: every gold point re-scored against the kept claims."""
    for it in items:
        kept = it.get("drop_kept")
        if kept is None:
            continue
        ext = "\n".join(m for _, m in kept)
        if not ext.strip():
            continue
        for g in it["gold"]:
            p = prompts["EVALUATION_PROMPT_FOR_MEMORY_INTEGRITY"].format(memories=ext, expected_memory_point=g["memory_content"])
            yield f"DI|{it['case_id']}|{g['index']}", "integrity", p


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    return {r["key"]: r for r in map(json.loads, path.read_text().splitlines()) if r.get("key")}


def _int_or_none(x):
    try:
        v = int(x)
        return v if v in (0, 1, 2) else None
    except (TypeError, ValueError):
        return None


def _records(it, cache, pre="", extracted=None, acc_pre=None, indices=None):
    ext = it["extracted"] if extracted is None else extracted
    acc_pre = pre if acc_pre is None else acc_pre
    indices = list(range(len(ext))) if indices is None else indices
    nonempty = bool("\n".join(ext).strip())
    integ, acc, missing = [], [], 0
    for g in it["gold"]:
        r = cache.get(f"{pre}I|{it['case_id']}|{g['index']}")
        sc = 0 if not nonempty else (_int_or_none(r["score"]) if r else None)
        missing += (r is None and nonempty)
        integ.append(IntegrityRecord(g["memory_source"], float(g.get("importance", 0)), sc))
    for k in indices:
        r = cache.get(f"{acc_pre}A|{it['case_id']}|{k}")
        missing += r is None
        acc.append(AccuracyRecord(_int_or_none(r["score"]) if r else None, str(r.get("included")).lower() == "true" if r else False))
    return integ, acc, missing


def _session_conditions(it, cache):
    """{condition: (integrity_records, accuracy_records, committed, missing)} for one session."""
    integ, acc, m = _records(it, cache)
    rep = None
    if not it["guard_judge_commits"] and it.get("repair_claims"):
        rep = _records(it, cache, pre="R", extracted=it["repair_claims"])
    drop = None
    if it.get("drop_kept") is not None:
        kept = it["drop_kept"]
        drop = _records(it, cache, pre="D", extracted=[m for _, m in kept], acc_pre="", indices=[k for k, _ in kept])
    commits = {"unguarded": True, "guard_strict_recorded": False, "guard_judge_claims": it["guard_judge_commits"]}
    out = {}
    for c in CONDITIONS:
        if c == "guard_claim_drop":
            i2, a2, committed = (drop[0], drop[1], True) if drop is not None else (integ, acc, True)
        elif c == "guard_repair":
            if it["guard_judge_commits"]:
                i2, a2, committed = integ, acc, True
            elif rep is not None:
                i2, a2, committed = rep[0], rep[1], True
            else:
                i2, a2 = apply_commit_decision(integ, acc, False)
                committed = False
        else:
            i2, a2 = apply_commit_decision(integ, acc, commits[c])
            committed = commits[c]
        out[c] = (i2, a2, committed)
    return out, m + (rep[2] if rep else 0) + (drop[2] if drop else 0)


def score(items, cache) -> dict:
    out, per_session = {}, {c: [] for c in CONDITIONS}
    missing = 0
    for it in items:
        conds, m = _session_conditions(it, cache)
        missing += m
        for c in CONDITIONS:
            i2, a2, committed = conds[c]
            per_session[c].append({"case_id": it["case_id"], "committed": committed, "integrity": i2, "accuracy": a2})
    for c in CONDITIONS:
        out[c] = aggregate_extraction([r for s in per_session[c] for r in s["integrity"]],
                                      [r for s in per_session[c] for r in s["accuracy"]])
        out[c]["sessions_committed"] = sum(s["committed"] for s in per_session[c])
    out["_missing_judge_results"] = missing
    return out


def fully_judged(items, cache):
    out = []
    for it in items:
        keys = [f"A|{it['case_id']}|{k}" for k in range(len(it["extracted"]))] + [f"I|{it['case_id']}|{g['index']}" for g in it["gold"]]
        if it.get("repair_claims"):
            keys += [f"RA|{it['case_id']}|{k}" for k in range(len(it["repair_claims"]))] + [f"RI|{it['case_id']}|{g['index']}" for g in it["gold"]]
        if it.get("drop_kept"):
            keys += [f"DI|{it['case_id']}|{g['index']}" for g in it["gold"]] if "\n".join(m for _, m in it["drop_kept"]).strip() else []
        if all(k in cache for k in keys):
            out.append(it)
    return out


def paired_bootstrap(items, cache, base="unguarded", other="guard_judge_claims", resamples=2000, seed=20261122):
    """Paired session-level bootstrap of (other - base) for each pooled official metric.

    Per-session records are computed once; each resample re-aggregates the pooled metrics.
    """
    import random

    rng = random.Random(seed)
    per = []
    for it in items:
        r, _ = _session_conditions(it, cache)
        per.append((r[base][:2], r[other][:2]))
    keys = ("recall(all)", "weighted_recall(all)", "fmr(all)", "target_precision(all)", "weighted_accuracy(all)", "f1")

    def agg(sample, side):
        integ = [x for p in sample for x in p[side][0]]
        acc = [x for p in sample for x in p[side][1]]
        return aggregate_extraction(integ, acc)

    d0 = {k: None for k in keys}
    a0, b0 = agg(per, 0), agg(per, 1)
    diffs = {k: [] for k in keys}
    for _ in range(resamples):
        samp = [per[rng.randrange(len(per))] for _ in per]
        a, b = agg(samp, 0), agg(samp, 1)
        for k in keys:
            if a[k] is not None and b[k] is not None:
                diffs[k].append(b[k] - a[k])
    res = {}
    for k in keys:
        d = sorted(diffs[k])
        if not d:  # metric undefined in every resample (e.g. FMR without interference points)
            res[k] = {"diff": None, "ci95": None, "n_sessions": len(items)}
            continue
        d0 = (b0[k] or 0) - (a0[k] or 0)  # held-everything -> undefined metric counts as 0 here
        res[k] = {"diff": round(d0, 4), "ci95": [round(d[int(0.025 * len(d))], 4), round(d[int(0.975 * len(d)) - 1], 4)],
                  "n_sessions": len(items)}
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=str(DEFAULT_RUN))
    ap.add_argument("--halumem-path", default=str(Path.home() / "Repositories/HaluMem/HaluMem-Medium.jsonl"))
    ap.add_argument("--reference-repo", default=str(DEFAULT_REPO))
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--estimate", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--score", action="store_true")
    ap.add_argument("--only-injected", action="store_true",
                    help="score only sessions into which run_halumem_inject.py injected faults")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-spend-usd", type=float)
    a = ap.parse_args()

    run = Path(a.run_dir)
    out_dir = run / "official"
    out_dir.mkdir(exist_ok=True)
    cache_path = out_dir / "judge-results.jsonl"
    prompts = load_prompts(Path(a.reference_repo))
    items = build_items(run, Path(a.halumem_path))
    if a.only_injected:
        items = [i for i in items if i["injected"]]
    job_list = list(jobs(items, prompts)) + list(repair_jobs(items, prompts)) + list(drop_jobs(items, prompts))
    job_list.sort(key=lambda j: j[0].split("|")[1])  # per-session order: a spend-cap stop leaves whole sessions judged
    cache = load_cache(cache_path)

    if a.estimate:
        todo = [j for j in job_list if j[0] not in cache]
        n_i = sum(k == "integrity" for _, k, _ in todo)
        n_a = sum(k == "accuracy" for _, k, _ in todo)
        tok_in = sum(len(p) for *_, p in todo) // 4
        tok_out = n_i * 120 + n_a * 200
        p_in, p_out = 3e-7, 1.2e-6  # deepseek-flash peak list price, cache miss (run-config.json)
        est = {
            "sessions": len(items), "gold_points_scored": sum(len(i["gold"]) for i in items),
            "update_points_excluded": sum(i["update_points_excluded"] for i in items),
            "extracted_memories": sum(len(i["extracted"]) for i in items),
            "calls_integrity": n_i, "calls_accuracy": n_a, "already_cached": len(job_list) - len(todo),
            "est_input_tokens": tok_in, "est_output_tokens": tok_out,
            "est_cost_usd_peak_cache_miss": round(tok_in * p_in + tok_out * p_out, 3),
            "note": "estimate (chars/4); real billing was ~1/5 of peak estimate on earlier runs, unverified",
        }
        print(json.dumps(est, indent=2))
        return 0

    if a.run:
        if not a.allow_paid or a.max_spend_usd is None:
            raise SystemExit("--run needs --allow-paid and --max-spend-usd")
        from membud.llm import OpenAICompatibleChatClient, enforce_deepseek_price, load_dotenv_0600

        load_dotenv_0600(ROOT / ".env", require_0600=True, populate_environ=True)
        client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
        price = enforce_deepseek_price(str(client.model), allow_paid=True)
        p_in, p_out = price["prompt_per_mtok"] / 1e6, price["completion_per_mtok"] / 1e6
        spent, done = 0.0, 0
        with cache_path.open("a", encoding="utf-8") as fh:
            for key, kind, prompt in job_list:
                if key in cache:
                    continue
                if spent >= a.max_spend_usd:
                    print(f"STOP: estimated spend ${spent:.4f} reached cap; resume with --run", file=sys.stderr)
                    break
                key_field = "score" if kind == "integrity" else "accuracy_score"
                try:
                    res = client.complete_json(
                        [{"role": "user", "content": prompt}],
                        validator=lambda j, f=key_field: None if f in j else (_ for _ in ()).throw(ValueError("missing field")),
                    )
                    spent += (res.input_tokens or 0) * p_in + (res.output_tokens or 0) * p_out
                    rec = {"key": key, "kind": kind, "score": res.json.get(key_field),
                           "included": res.json.get("is_included_in_golden_memories")}
                except Exception as exc:  # judge failure -> score None, counted in the (all) denominators
                    rec = {"key": key, "kind": kind, "score": None, "included": None, "error": type(exc).__name__}
                cache[key] = rec
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                done += 1
        print(json.dumps({"calls_made": done, "estimated_spend_usd": round(spent, 4)}))

    res = score(items, cache)
    res["_meta"] = {
        "reference_commit": REFERENCE_COMMIT, "deviations": list(DEVIATIONS_FROM_PAPER),
        "judge": "deepseek-flash (official: gpt-4o)", "validated": False,
        "update_metrics": "not computed: no retrieval recorded", "qa_metrics": "not computed: no answers recorded",
        "update_points_excluded": sum(i["update_points_excluded"] for i in items),
        "prompt_sha256": hashlib.sha256(json.dumps(prompts, sort_keys=True).encode()).hexdigest(),
    }
    full = fully_judged(items, cache)
    res["_paired_fully_judged_sessions"] = {
        "n_sessions": len(full), "n_users": len({i["uuid"] for i in full}),
        "guard_judge_claims_minus_unguarded": paired_bootstrap(full, cache) if full else None,
        "guard_repair_minus_unguarded": paired_bootstrap(full, cache, other="guard_repair") if full else None,
        "guard_repair_minus_guard_judge_claims": paired_bootstrap(full, cache, base="guard_judge_claims", other="guard_repair") if full else None,
        "guard_claim_drop_minus_unguarded": paired_bootstrap(full, cache, other="guard_claim_drop") if full else None,
        "sessions_held_by_guard": sum(not i["guard_judge_commits"] for i in full),
    }
    # Tuning/final split by user (deterministic hash). Nothing was tuned on either half:
    # every rule (grounding rubric, guard, repair prompt, retry limit) was fixed before these results
    # existed, so the split is descriptive and shows whether the effect is consistent across halves.
    def half(it):
        return "tuning" if int(hashlib.sha256(f"20261122|{it['uuid']}".encode()).hexdigest(), 16) % 2 == 0 else "final"

    res["_split_by_user"] = {}
    for name in ("tuning", "final"):
        sub = [i for i in full if half(i) == name]
        if not sub:
            continue
        res["_split_by_user"][name] = {
            "n_sessions": len(sub), "n_users": len({i["uuid"] for i in sub}),
            "sessions_held_by_guard": sum(not i["guard_judge_commits"] for i in sub),
            "guard_minus_unguarded": paired_bootstrap(sub, cache),
            "guard_repair_minus_unguarded": paired_bootstrap(sub, cache, other="guard_repair"),
            "guard_repair_minus_guard": paired_bootstrap(sub, cache, base="guard_judge_claims", other="guard_repair"),
        }
    (out_dir / "official-metrics.json").write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
    print(json.dumps(res, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
