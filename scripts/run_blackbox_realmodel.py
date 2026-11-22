#!/usr/bin/env python
"""Real-model Mem0 2.2.1 run through the black-box guarded path (free model, synthetic data).

Only Mem0's extraction LLM is real (free OpenRouter model, zero-price enforced). Embedder is
the local hash embedder; no HaluMem data is sent. Hard caps on requests and tokens.
Usage: OPENROUTER_API_KEY=... PYTHONPATH=src .venv-mem0/bin/python scripts/run_blackbox_realmodel.py
"""
from __future__ import annotations
import argparse, importlib.metadata as md, json, os, sys, tempfile, warnings
from pathlib import Path

os.environ["MEM0_TELEMETRY"] = "False"
os.environ["MEM0_DIR"] = tempfile.mkdtemp(prefix="membud-rm-mem0-dir-")
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import logging; logging.disable(logging.CRITICAL)

from membud.adapters import BlackBoxStaged
from membud.adapters.blackbox_backends import mem0_ops
from membud.adapters.mem0_offline import OfflineHost
from membud.llm.client import OpenAICompatibleChatClient, enforce_openrouter_zero_price

SCOPE = {"user_id": "u1"}
DIALOGUES = [
    [{"role": "user", "content": "I live in Berlin and I work at Acme Corp as an engineer."}],
    [{"role": "user", "content": "My favourite food is ramen and I am allergic to peanuts."}],
    [{"role": "user", "content": "I moved to Lisbon last month."}],
    [{"role": "user", "content": "I adopted a dog named Biscuit."}],
    [{"role": "user", "content": "I switched jobs and now work at Globex."}],
]


class CappedLLM:
    def __init__(self, client, max_requests, max_tokens):
        self.client, self.max_requests, self.max_tokens = client, max_requests, max_tokens
        self.requests = 0; self.tokens = 0; self.errors = []

    def generate_response(self, messages, response_format=None, **kw):
        if self.requests >= self.max_requests or self.tokens >= self.max_tokens:
            raise RuntimeError("budget cap reached")
        self.requests += 1
        try:
            c = self.client.complete_json(messages)
        except Exception as e:
            self.errors.append(type(e).__name__); raise
        self.tokens += int(c.total_tokens or 0)
        return c.content


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/gemma-4-31b-it:free")
    ap.add_argument("--max-requests", type=int, default=40)
    ap.add_argument("--max-tokens", type=int, default=40000)
    ap.add_argument("--out", default=str(ROOT / "data/results/mem0-blackbox-realmodel-v1"))
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    pricing = enforce_openrouter_zero_price(a.model)  # refuses paid models
    os.environ["OPENROUTER_MODEL"] = a.model
    client = OpenAICompatibleChatClient.from_openrouter_env()
    llm = CappedLLM(client, a.max_requests, a.max_tokens)
    work = Path(tempfile.mkdtemp(prefix="rm-", dir=out))
    host = OfflineHost(work / "live"); host.memory.llm = llm
    n = [0]
    def factory():
        n[0] += 1
        t = OfflineHost(work / f"twin{n[0]}"); t.memory.llm = llm
        ops = mem0_ops(t.memory, [SCOPE], name="mem0-twin"); ops.handle_host = t
        return ops
    g = BlackBoxStaged(mem0_ops(host.memory, [SCOPE]), factory, workdir=work / "membud",
                       scratch_closer=lambda o: o.handle_host.close())
    rows = []
    for i, d in enumerate(DIALOGUES):
        try:
            o = g.add(d, scope=SCOPE, case_id=f"s{i}")
            rows.append({"session": i, **o.summary(), "proposals": [dict(p) for p in o.proposals]})
        except Exception as e:
            rows.append({"session": i, "error": type(e).__name__, "message": str(e)[:200]})
    live = mem0_ops(host.memory, [SCOPE]).list(SCOPE)
    ev = {
        "claim": "Real free-model extraction through BlackBoxStaged on Mem0; synthetic dialogues.",
        "environment": {"mem0ai": md.version("mem0ai"), "model": a.model, "embedder": "local hash (offline)"},
        "pricing": {k: pricing[k] for k in ("id", "is_free", "prompt_per_mtok", "completion_per_mtok")},
        "caps": {"max_requests": a.max_requests, "max_tokens": a.max_tokens, "cost_cap_usd": 0},
        "usage": {"requests": llm.requests, "tokens": llm.tokens, "llm_errors": llm.errors},
        "sessions": rows,
        "final_live_memories": [getattr(x, "text", str(x)) for x in live],
    }
    (out / "evidence.json").write_text(json.dumps(ev, indent=2, default=str), encoding="utf-8")
    import shutil; shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({k: ev[k] for k in ("usage", "final_live_memories")}, indent=2, default=str))
    print([ (r.get("decision"), r.get("error")) for r in rows])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
