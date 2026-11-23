#!/usr/bin/env python3
"""Attribute recorded LLM token usage to RQ3 cost components, offline ($0, no network).

Every completed model call made through ``membud.llm`` was stored in the response cache with
the provider-reported ``input_tokens`` / ``output_tokens`` and wall-clock latency.  The cache
key is a SHA-256 of the client settings plus the exact messages, so this script rebuilds the
messages each run script sent (same prompt builders, same recorded inputs) and looks the keys
up.  A rebuilt key that is found is a **measured** call; one that is not found is reported as
unmatched (never guessed).

What the cache cannot show, so this script cannot either: calls that failed without a cached
completion (transport errors, HTTP 402), retries hidden inside one completion (tokens of JSON
retries are summed into it), DeepSeek context-cache hits (the cache stores total input tokens,
not the hit/miss split), and any call not made through ``membud.llm`` (e.g. Mem0's own client in
the live runs).  Token totals are therefore a lower bound of what the provider metered.

Output holds aggregate counts only: no dataset text, claims or case ids.

Inputs are read only: the run directories (gitignored, dataset text), the HaluMem JSONL, the
official HaluMem prompts (local clone), and a *copy* of each cache database.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sqlite3
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PRICES = {  # USD per 1M tokens; src/membud/llm/client.py DEEPSEEK_PRICES_PER_MTOK (published list price, not a bill)
    "deepseek_peak_cache_miss": {"input": 0.30, "output": 1.20},
    "deepseek_offpeak_cache_miss": {"input": 0.15, "output": 0.60},
}


class Cache:
    def __init__(self, path: Path, workdir: Path) -> None:
        copy = workdir / path.name
        shutil.copy2(path, copy)  # never open the live cache
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        self.rows = {k: json.loads(p) for k, p in con.execute("SELECT cache_key, payload FROM completions")}
        con.close()
        self.used: set[str] = set()

    def get(self, key: str) -> dict | None:
        row = self.rows.get(key)
        if row is not None:
            self.used.add(key)
        return row


def deepseek_key_fn():
    from membud.llm import OpenAICompatibleChatClient

    os.environ.setdefault("DEEPSEEK_API_KEY", "offline-attribution-not-a-key")
    client = OpenAICompatibleChatClient.from_deepseek_env(require_cache=True, thinking=False)
    return client._cache_key, {"base_url": client.base_url, "model": str(client.model),
                               "temperature": client.temperature, "extra_body": dict(client.extra_body)}


def summarize_calls(rows: list[dict], expected: int) -> dict[str, Any]:
    ins = [r.get("input_tokens") or 0 for r in rows]
    outs = [r.get("output_tokens") or 0 for r in rows]
    lat = [r.get("latency_ms") for r in rows if r.get("latency_ms")]
    tin, tout = sum(ins), sum(outs)
    d: dict[str, Any] = {
        "calls_expected": expected,
        "calls_matched": len(rows),
        "match_rate": (len(rows) / expected) if expected else None,
        "input_tokens": tin,
        "output_tokens": tout,
        "request_attempts": sum(r.get("request_attempts") or 0 for r in rows),
        "json_retries": sum(r.get("json_retries") or 0 for r in rows),
    }
    if rows:
        d["per_call"] = {
            "input_median": statistics.median(ins), "output_median": statistics.median(outs),
            "input_mean": tin / len(rows), "output_mean": tout / len(rows),
            "latency_ms_median": statistics.median(lat) if lat else None,
            "latency_ms_p90": (statistics.quantiles(lat, n=10)[-1] if len(lat) >= 10 else None),
        }
    for name, p in PRICES.items():
        d[f"est_usd_{name}"] = (tin * p["input"] + tout * p["output"]) / 1e6
    return d


def lookup(cache: Cache, keyfn, message_lists: Iterable[list[dict]]) -> tuple[list[dict], int]:
    rows, n = [], 0
    for msgs in message_lists:
        n += 1
        r = cache.get(keyfn(msgs))
        if r is not None:
            rows.append(r)
    return rows, n


def load_users(path: Path) -> dict[str, dict]:
    users = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        u = json.loads(line)
        users[u["uuid"]] = u
    return users


def proposals(run: Path) -> list[dict]:
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted((run / "proposals").glob("*.json"))]


def fmt_raw(dialogue) -> str:
    return "\n".join(f"[{i}] [{t['role']}] {t['content']}" for i, t in enumerate(dialogue))


# --- claim judge (deployed) ---------------------------------------------------------------


def judge_messages_batch(run: Path, users: dict, system: str) -> list[list[dict]]:
    """scripts/run_halumem_claim_judge.py and run_halumem_inject.py prompt (one call per session with ADDs)."""
    out = []
    for d in proposals(run):
        dlg = users[d["user_uuid"]]["sessions"][d["session_index"] - 1]["dialogue"]
        before = [c for r in d["transition"]["before"] for c in r["claims"]]
        added = [c for c in dict.fromkeys(p["memory"] for p in d["mem0_proposals"] if p.get("event") == "ADD") if c not in set(before)]
        if not added:
            continue
        cl = "\n".join(f"{i}. {c}" for i, c in enumerate(added))
        out.append([{"role": "system", "content": system},
                    {"role": "user", "content": f"Session:\n{fmt_raw(dlg)}\n\nEarlier memory:\n{chr(10).join(before) or '(empty)'}\n\nClaims:\n{cl}"}])
    return out


def judge_messages_injected(run: Path, users: dict, system: str) -> list[list[dict]]:
    """run_halumem_inject.py: only sessions with an injected fault were judged anew."""
    out = []
    for d in proposals(run):
        if not d.get("injected"):
            continue
        s = users[d["user_uuid"]]["sessions"][d["session_index"] - 1]
        prior = [c for r in d["transition"]["before"] for c in r["claims"]]
        new = [p["memory"] for p in d["mem0_proposals"] if p.get("event") == "ADD"]
        cl = "\n".join(f"{i}. {c}" for i, c in enumerate(new))
        out.append([{"role": "system", "content": system},
                    {"role": "user", "content": f"Session:\n{fmt_raw(s['dialogue'])}\n\nEarlier memory:\n{chr(10).join(prior) or '(empty)'}\n\nClaims:\n{cl}"}])
    return out


def judge_messages_phase2(run: Path, halumem_path: Path, system: str, system_accepted: str, flagged_only: bool) -> list[list[dict]]:
    """scripts/halumem_llm_phase2_eval.py (strict pass over all sessions; accepted pass over strict-flagged sessions)."""
    from membud.halumem import load_halumem_dataset
    from membud.schemas import Transition

    _, users = load_halumem_dataset(halumem_path, dataset_revision="local")
    lookup_ = {f"{u.uuid}__{s.session_id}": s for u in users for s in u.sessions}
    flagged = {v["case_id"] for v in map(json.loads, (run / "llm-claim-verdicts.jsonl").read_text().splitlines())
               if not v["judge_supported"]}
    out = []
    for rec in proposals(run):
        cid = rec["case_id"]
        if flagged_only and cid not in flagged:
            continue
        s = lookup_[cid]
        t = Transition.from_dict(rec["transition"])
        before = {c for r in t.before for c in r.claims}
        added = list(dict.fromkeys(c for r in t.candidate for c in r.claims if c not in before))
        if not added:
            continue
        dlg = "\n".join(f"[{i}] [{x.role}] {x.content}" for i, x in enumerate(s.dialogue))
        mem = "\n".join(c for r in t.before for c in r.claims) or "(empty)"
        cl = "\n".join(f"{i}. {c}" for i, c in enumerate(added))
        out.append([{"role": "system", "content": system_accepted if flagged_only else system},
                    {"role": "user", "content": f"Session:\n{dlg}\n\nEarlier memory:\n{mem}\n\nClaims:\n{cl}"}])
    return out


# --- repair (deployed) --------------------------------------------------------------------


def repair_messages(run: Path, users: dict, system_accepted: str, extract_system: str) -> tuple[list[list[dict]], list[list[dict]], dict]:
    """Rebuild run_halumem_repair.py's extractor and verifier calls from repair/results.jsonl."""
    props = {d["case_id"]: d for d in proposals(run)}
    ext, ver = [], []
    stats = {"held_sessions": 0, "recovered": 0, "attempts_after_initial": 0}
    rp = run / "repair" / "results.jsonl"
    if not rp.exists():
        return ext, ver, stats
    for r in map(json.loads, rp.read_text().splitlines()):
        d = props[r["case_id"]]
        s = users[d["user_uuid"]]["sessions"][d["session_index"] - 1]
        dlg = fmt_raw(s["dialogue"])
        prior = "\n".join(c for rr in d["transition"]["before"] for c in rr["claims"]) or "(empty)"
        attempts = r["attempts"]
        stats["held_sessions"] += 1
        stats["recovered"] += r["final_decision"] == "ACCEPT"
        first = tuple(attempts[0]["claims"])
        prev, off, codes = list(first), attempts[0]["offending_claims"], attempts[0]["reason_codes"]
        seen_idx = {0}
        for a in attempts[1:]:
            if a["index"] in seen_idx:  # REVERIFY_FAILED row reuses the index; its call was a cache hit
                continue
            seen_idx.add(a["index"])
            stats["attempts_after_initial"] += 1
            cl = "\n".join(f"{i}. {c}" for i, c in enumerate(prev))
            ol = "\n".join(f"- {c}" for c in off) or "(none listed)"
            ext.append([{"role": "system", "content": extract_system},
                        {"role": "user", "content": f"Session:\n{dlg}\n\nEarlier memory:\n{prior}\n\nCurrent claims:\n{cl}\n\n"
                                                    f"Flagged claims (reason {', '.join(codes)}):\n{ol}"}])
            if a.get("error") or not a["claims"]:
                continue
            if tuple(a["claims"]) != first:
                vl = "\n".join(f"{i}. {c}" for i, c in enumerate(a["claims"]))
                ver.append([{"role": "system", "content": system_accepted},
                            {"role": "user", "content": f"Session:\n{dlg}\n\nEarlier memory:\n{prior}\n\nClaims:\n{vl}"}])
            prev, off, codes = list(a["claims"]), a["offending_claims"], a["reason_codes"]
    return ext, ver, stats


# --- official scoring (evaluation only) ----------------------------------------------------


def official_messages(score_mod, run: Path, halumem_path: Path, prompts: dict) -> dict[str, list[list[dict]]]:
    items = score_mod.build_items(run, halumem_path)
    groups: dict[str, list[list[dict]]] = {"official_unguarded_IA": [], "official_repair_RI_RA": [], "official_drop_DI": []}
    for _, _, p in score_mod.jobs(items, prompts):
        groups["official_unguarded_IA"].append([{"role": "user", "content": p}])
    for _, _, p in score_mod.repair_jobs(items, prompts):
        groups["official_repair_RI_RA"].append([{"role": "user", "content": p}])
    for _, _, p in score_mod.drop_jobs(items, prompts):
        groups["official_drop_DI"].append([{"role": "user", "content": p}])
    return groups, len(items)


def longmemeval_messages(run_dir: Path, data: Path, reference_repo: Path, n: int, seed: int = 20261122) -> tuple[dict[str, list[list[dict]]], int, int]:
    """Re-execute scripts/run_longmemeval.py offline with a stage cache that records each call's messages.

    The recorded stage outputs (stages.jsonl) are replayed, so the same branches (repair, reader
    contexts) are taken; no model is contacted.  Groups: E extract (host), V claim judge, X repair
    extractor (deployed), A reader and J benchmark judge (evaluation / QA side).
    """
    lme = _load("run_longmemeval", ROOT / "scripts" / "run_longmemeval.py")
    from membud.longmemeval import load_anscheck, load_instances

    groups: dict[str, list[list[dict]]] = {}

    class Recording(lme.Stages):
        def call(self, key, messages, validator):
            if key not in self.cache:
                raise KeyError(key)
            if key not in self.seen:
                self.seen.add(key)
                groups.setdefault(key.split("|", 1)[0], []).append([dict(m) for m in messages])
            return self.cache[key]

    st = Recording(run_dir / "stages.jsonl")
    st.seen = set()
    sample = lme.pilot_sample(load_instances(str(data)), n, seed)
    anscheck = load_anscheck(str(reference_repo))
    done = {json.loads(l)["question_id"] for l in (run_dir / "results.jsonl").read_text().splitlines()}
    questions = sessions = 0
    for q in sample:
        if q["question_id"] not in done:
            continue
        stores, stats = lme.ingest_question(q, st)
        lme.answer_and_judge(q, stores, st, anscheck, 10)
        questions += 1
        sessions += stats["sessions"]
    return groups, questions, sessions


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-root", required=True, help="directory holding phase2-deepseek, phase3-* run dirs (read only)")
    ap.add_argument("--halumem-dir", default=str(Path.home() / "Repositories/HaluMem"))
    ap.add_argument("--reference-repo", default=str(Path.home() / "Repositories/HaluMem-repo"))
    ap.add_argument("--cache-dir", default=str(Path.home() / ".cache/membud"))
    ap.add_argument("--output", required=True)
    ap.add_argument("--longmemeval-run", help="run dir of scripts/run_longmemeval.py (stages.jsonl, results.jsonl)")
    ap.add_argument("--longmemeval-n", type=int, default=200)
    ap.add_argument("--longmemeval-data", default=str(Path.home() / "Repositories/LongMemEval/longmemeval_oracle.json"))
    ap.add_argument("--longmemeval-repo", default=str(Path.home() / "Repositories/LongMemEval-repo"))
    a = ap.parse_args()
    runs, hdir = Path(a.runs_root), Path(a.halumem_dir)
    work = Path(tempfile.mkdtemp(prefix="rq3-attr-"))
    try:
        ds = Cache(Path(a.cache_dir) / "deepseek-cache.sqlite3", work)
        orc = Cache(Path(a.cache_dir) / "openrouter-cache.sqlite3", work)
        keyfn, client_settings = deepseek_key_fn()
        llm = _load("llm_phase2", ROOT / "scripts" / "halumem_llm_phase2_eval.py")
        cj = _load("claim_judge", ROOT / "scripts" / "run_halumem_claim_judge.py")
        rep = _load("repair", ROOT / "scripts" / "run_halumem_repair.py")
        score = _load("score_official", ROOT / "scripts" / "score_halumem_official.py")
        prompts = score.load_prompts(Path(a.reference_repo))
        medium = load_users(hdir / "HaluMem-Medium.jsonl")
        long_ = load_users(hdir / "HaluMem-Long.jsonl")
        out: dict[str, Any] = {"client_settings_for_keys": {k: v for k, v in client_settings.items()}, "runs": {}}

        def comp(cache, msgs, label=None):
            rows, n = lookup(cache, keyfn, msgs)
            return summarize_calls(rows, n)

        # phase2-deepseek: Mem0 + DeepSeek extractor, natural errors (97 sessions)
        r2 = runs / "phase2-deepseek"
        e, v, st = repair_messages(r2, medium, llm.SYSTEM_ACCEPTED, rep.EXTRACT_SYSTEM)
        og, n_items = official_messages(score, r2, hdir / "HaluMem-Medium.jsonl", prompts)
        out["runs"]["mem0_deepseek_medium"] = {
            "sessions": len(proposals(r2)),
            "claim_judge_strict_all_sessions": comp(ds, judge_messages_phase2(r2, hdir / "HaluMem-Medium.jsonl", llm.SYSTEM, llm.SYSTEM_ACCEPTED, False)),
            "claim_judge_accepted_policy_rejudge_flagged": comp(ds, judge_messages_phase2(r2, hdir / "HaluMem-Medium.jsonl", llm.SYSTEM, llm.SYSTEM_ACCEPTED, True)),
            "repair_extractor": comp(ds, e), "repair_verifier": comp(ds, v), "repair_stats": st,
            **{k: comp(ds, m) for k, m in og.items()},
        }
        # phase3-weakmem0: Mem0 + Llama-3.1-8B (100 sessions)
        rw = runs / "phase3-weakmem0"
        e, v, st = repair_messages(rw, medium, llm.SYSTEM_ACCEPTED, rep.EXTRACT_SYSTEM)
        og, _ = official_messages(score, rw, hdir / "HaluMem-Medium.jsonl", prompts)
        out["runs"]["mem0_llama8b_medium"] = {
            "sessions": len(proposals(rw)),
            "claim_judge_accepted": comp(ds, judge_messages_batch(rw, medium, cj.SYSTEM_ACCEPTED)),
            "claim_judge_rubric": comp(ds, judge_messages_batch(rw, medium, cj.SYSTEM_RUBRIC)),
            "repair_extractor": comp(ds, e), "repair_verifier": comp(ds, v), "repair_stats": st,
            **{k: comp(ds, m) for k, m in og.items()},
        }
        # phase3-injected: 47 injected sessions (7.1% dose)
        ri = runs / "phase3-injected"
        e, v, st = repair_messages(ri, medium, llm.SYSTEM_ACCEPTED, rep.EXTRACT_SYSTEM)
        og, _ = official_messages(score, ri, hdir / "HaluMem-Medium.jsonl", prompts)
        out["runs"]["mem0_deepseek_medium_injected"] = {
            "sessions": len(proposals(ri)),
            "sessions_injected": sum(bool(d.get("injected")) for d in proposals(ri)),
            "claim_judge_injected_sessions": comp(ds, judge_messages_injected(ri, medium, llm.SYSTEM_ACCEPTED)),
            "repair_extractor": comp(ds, e), "repair_verifier": comp(ds, v), "repair_stats": st,
            **{k: comp(ds, m) for k, m in og.items()},
        }
        # phase3-long-mem0: Mem0 + DeepSeek on HaluMem-Long (30 sessions), judge only
        rl = runs / "phase3-long-mem0"
        out["runs"]["mem0_deepseek_long"] = {
            "sessions": len(proposals(rl)),
            "claim_judge_accepted": comp(ds, judge_messages_batch(rl, long_, cj.SYSTEM_ACCEPTED)),
        }

        # LongMemEval (oracle split): our extractor -> claim judge -> {unguarded, hold, repair} -> reader -> benchmark judge
        if a.longmemeval_run:
            groups, nq, nsess = longmemeval_messages(Path(a.longmemeval_run), Path(a.longmemeval_data),
                                                     Path(a.longmemeval_repo), a.longmemeval_n)
            names = {"E": "host_extractor", "V": "claim_judge", "X": "repair_extractor", "A": "reader_qa", "J": "benchmark_judge_eval_only"}
            out["runs"]["longmemeval_oracle"] = {"questions": nq, "sessions": nsess,
                                                 **{names.get(k, k): comp(ds, m) for k, m in sorted(groups.items())}}

        # host extraction reference: Mem0's own extraction calls, attributed by response shape only
        def shape(rows, model, keyset):
            sel = [r for r in rows.values() if r.get("model") == model and isinstance(r.get("json"), dict)
                   and set(r["json"]) == keyset]
            return summarize_calls(sel, len(sel))

        out["host_extraction_reference_by_response_shape"] = {
            "note": "Mem0 extraction calls cannot be re-keyed (Mem0 builds its own prompts); selected by model and "
                    "response shape {'memory'} from the whole cache, so they mix runs (Medium, Long, smoke tests).",
            "mem0_deepseek_flash": shape(ds.rows, "deepseek-flash", {"memory"}),
            "mem0_llama_3_1_8b": shape(orc.rows, "meta-llama/llama-3.1-8b-instruct", {"memory"}),
        }
        # everything in the DeepSeek cache, by response shape (for dashboard reconciliation)
        by_shape: dict[str, list[dict]] = {}
        for r in ds.rows.values():
            j = r.get("json")
            by_shape.setdefault("|".join(sorted(j)) if isinstance(j, dict) else "other", []).append(r)
        out["deepseek_cache_totals_by_response_shape"] = {k: summarize_calls(v, len(v)) for k, v in sorted(by_shape.items())}
        out["deepseek_cache_total"] = summarize_calls(list(ds.rows.values()), len(ds.rows))
        out["deepseek_cache_attributed_keys"] = len(ds.used)
        out["openrouter_cache_by_model"] = {}
        by_model: dict[str, list[dict]] = {}
        for r in orc.rows.values():
            by_model.setdefault(r.get("model", "?"), []).append(r)
        for m, rows in sorted(by_model.items()):
            out["openrouter_cache_by_model"][m] = summarize_calls(rows, len(rows))
        out["prices_usd_per_mtok"] = PRICES
        out["price_source"] = "src/membud/llm/client.py DEEPSEEK_PRICES_PER_MTOK (published list price, retrieved 2026-11-21); not a bill"
        out["caveats"] = [
            "Token counts are provider-reported per completion (measured); dollars are estimates.",
            "Lower bound: failed calls without a cached completion and calls outside membud.llm are not in the cache.",
            "DeepSeek context-cache hits are billed lower but the cache stores only total input tokens, so the "
            "cache-miss price over-estimates input cost.",
            "OpenRouter rows of DeepSeek-family prices do not apply to OpenRouter models; Llama-3.1-8B is priced by OpenRouter.",
        ]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: {kk: (vv.get("calls_matched"), vv.get("calls_expected")) for kk, vv in v.items() if isinstance(vv, dict) and "calls_matched" in vv}
                      for k, v in out["runs"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
