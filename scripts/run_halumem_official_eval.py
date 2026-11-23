#!/usr/bin/env python3
"""Score a results directory with the official HaluMem ``eval/evaluation.py`` (unchanged), capped and metered.

Runs ``evaluation.main("memzero", <version>, user_num)`` from a local clone of MemTensor/HaluMem with the
working directory set to ``--results-root`` (which holds ``results/memzero-<version>/``), so the official code
reads ``memzero_eval_results.jsonl``, caches per user in ``tmp2/`` and writes ``memzero_eval_stat_result.json``
exactly as it does upstream. See ``scripts/halumem_official_shim.py`` for the run-time changes (endpoint via
environment variables, metered client, thread pool, DeepSeek thinking disabled). Judge deviation: the
configured DeepSeek model instead of GPT-4o.

Spend: the meter stops new calls once the estimated spend reaches ``--max-spend-usd``; calls already in
flight (at most ``--max-workers``) still finish, so the cap can be exceeded by a few calls. A user
interrupted by the cap is not cached and is redone on resume. Judge failures that the official code records
as ``None`` are reported; ``--purge-failed`` deletes those users' cache files so a rerun retries them.
Modes: --estimate (offline), --run (needs --allow-paid and --max-spend-usd), --audit (offline).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import halumem_official_shim as shim  # noqa: E402

SCORE_FIELDS = (("memory_integrity_records", "memory_integrity_score"), ("memory_accuracy_records", "memory_accuracy_score"),
                ("memory_update_records", "memory_update_type"), ("question_answering_records", "result_type"))


def results_dir(root: Path, version: str) -> Path:
    return Path(root) / "results" / f"memzero-{version}"


def estimate(path: Path) -> dict:
    """Peak-price estimate from the results file, mirroring which calls evaluation.process_user makes."""
    n_int = n_acc = n_upd = n_qa = 0
    tok_in = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        u = json.loads(line)
        for s in u["sessions"]:
            if s.get("is_generated_qa_session"):
                continue
            ext = "\n".join(s["extracted_memories"])
            dlg = sum(len(t["content"]) + 30 for t in s["dialogue"]) // 4
            gold = sum(len(m["memory_content"]) for m in s["memory_points"]) // 4
            for m in s["memory_points"]:
                if m["is_update"] == "True" and m.get("memories_from_system"):
                    n_upd += 1
                    tok_in += 700 + sum(len(x) for x in m["memories_from_system"]) // 4
                elif ext.strip():
                    n_int += 1
                    tok_in += 600 + len(ext) // 4
            n_acc += len(s["extracted_memories"])
            tok_in += len(s["extracted_memories"]) * (dlg + gold + 800)
            for q in s.get("questions", []):
                n_qa += 1
                tok_in += 900
    calls = n_int + n_acc + n_upd + n_qa
    tok_out = calls * 150
    return {"calls_integrity": n_int, "calls_accuracy": n_acc, "calls_update": n_upd, "calls_qa": n_qa,
            "est_input_tokens": tok_in, "est_output_tokens": tok_out,
            "est_cost_usd_peak": round(tok_in * 3e-7 + tok_out * 1.2e-6, 3)}


def audit(base: Path) -> dict:
    out = {}
    for f in sorted((base / "tmp2").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        missing = {k: sum(1 for r in d.get(k, []) if r.get(field) is None) for k, field in SCORE_FIELDS}
        if any(missing.values()):
            out[f.stem] = missing
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", default=str(ROOT / "data" / "halumem-live"))
    ap.add_argument("--version", required=True)
    ap.add_argument("--reference-repo", default=str(shim.DEFAULT_REPO))
    ap.add_argument("--max-workers", type=int, default=10)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--estimate", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--audit", action="store_true")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-spend-usd", type=float)
    ap.add_argument("--purge-failed", action="store_true")
    a = ap.parse_args()
    base = results_dir(Path(a.results_root), a.version)
    data = base / "memzero_eval_results.jsonl"
    if a.estimate:
        print(json.dumps(estimate(data), indent=2))
        return 0
    if a.audit:
        bad = audit(base)
        if a.purge_failed:
            for uuid in bad:
                (base / "tmp2" / f"{uuid}.json").unlink()
        print(json.dumps({"users_with_missing_scores": bad, "purged": bool(a.purge_failed and bad)}, indent=2))
        return 0
    if not a.allow_paid or a.max_spend_usd is None:
        raise SystemExit("--run needs --allow-paid and --max-spend-usd")
    from membud.llm import OpenAICompatibleChatClient, enforce_deepseek_price, load_dotenv_0600

    load_dotenv_0600(ROOT / ".env", require_0600=True, populate_environ=True)
    client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
    price = enforce_deepseek_price(str(client.model), allow_paid=True)
    meter = shim.Meter(price["prompt_per_mtok"] / 1e6, price["completion_per_mtok"] / 1e6, a.max_spend_usd)
    official = shim.load_official(Path(a.reference_repo), base_url=client.base_url, api_key=os.environ["DEEPSEEK_API_KEY"],
                                  model=str(client.model), meter=meter, extra_body=dict(client.extra_body) or None)
    n_users = sum(1 for line in data.read_text(encoding="utf-8").splitlines() if line.strip())
    stopped = ""
    cwd = os.getcwd()
    os.chdir(a.results_root)
    try:
        official["evaluation"].main("memzero", a.version, user_num=n_users, max_workers=a.max_workers)
    except shim.BudgetExceeded as exc:
        stopped = str(exc)
    finally:
        os.chdir(cwd)
    bad = audit(base)
    print(json.dumps({"version": a.version, "stopped": stopped, "users_with_missing_scores": bad,
                      "stat_file": str(base / "memzero_eval_stat_result.json") if not stopped else None, **meter.as_dict()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
