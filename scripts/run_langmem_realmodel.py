#!/usr/bin/env python
"""Real-model LangMem run through the black-box guard (free OpenRouter model, synthetic data, $0).

Real ``langmem`` + real LangGraph ``InMemoryStore`` + a real free chat model; hard caps on requests/tokens.
Usage: OPENROUTER_API_KEY=... PYTHONPATH=src .venv-langmem/bin/python scripts/run_langmem_realmodel.py
"""
from __future__ import annotations
import argparse, importlib.metadata as md, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from langchain_openai import ChatOpenAI
from langgraph.store.memory import InMemoryStore
from langmem import create_memory_store_manager
from membud.adapters import BlackBoxStaged
from membud.adapters.blackbox_backends import langgraph_store_ops
from membud.llm.client import enforce_openrouter_zero_price

SCOPE = {"user_id": "u1"}
DIALOGUES = [
    "I live in Berlin.",
    "I work at Acme Corp as an engineer.",
    "My favourite food is ramen.",
    "I am allergic to peanuts.",
    "I adopted a dog named Biscuit.",
]


class Counting(ChatOpenAI):
    pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="nvidia/nemotron-3.5-lightning:free")
    ap.add_argument("--max-requests", type=int, default=40)
    ap.add_argument("--max-tokens", type=int, default=40000)
    ap.add_argument("--out", default=str(ROOT / "data/results/langmem-blackbox-realmodel-v1"))
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    pricing = enforce_openrouter_zero_price(a.model)  # refuses paid models
    usage = {"requests": 0, "tokens": 0, "errors": []}

    from langchain_core.callbacks import BaseCallbackHandler

    class Meter(BaseCallbackHandler):
        def on_chat_model_start(self, *args, **kw):
            if usage["requests"] >= a.max_requests or usage["tokens"] >= a.max_tokens:
                raise RuntimeError("budget cap reached")
            usage["requests"] += 1

        def on_llm_end(self, response, **kw):
            for g in response.generations:
                for x in g:
                    u = getattr(x.message, "usage_metadata", None) or {}
                    usage["tokens"] += int(u.get("total_tokens", 0))

        def on_llm_error(self, error, **kw):
            usage["errors"].append(type(error).__name__)

    meter = Meter()
    meter.raise_error = True
    model = ChatOpenAI(model=a.model, api_key=os.environ["OPENROUTER_API_KEY"],
                       base_url="https://openrouter.ai/api/v1", temperature=0, max_retries=1,
                       callbacks=[meter])

    def native_add(store, ns, messages):
        return create_memory_store_manager(model, namespace=ns, store=store).invoke({"messages": messages})

    live = InMemoryStore()
    mk = lambda s, n="langmem": langgraph_store_ops(s, native_add, name=n)
    g = BlackBoxStaged(mk(live), lambda: mk(InMemoryStore(), "langmem-twin"),
                       workdir=Path(tempfile.mkdtemp(prefix="lm-", dir=out)), scratch_closer=lambda o: None)
    rows = []
    for i, text in enumerate(DIALOGUES):
        try:
            o = g.add([{"role": "user", "content": text}], scope=SCOPE, case_id=f"s{i}")
            rows.append({"session": i, "input": text, **o.summary(), "extracted": [c.new_text for c in o.changes]})
        except Exception as e:
            rows.append({"session": i, "input": text, "error": type(e).__name__, "message": str(e)[:200]})
    final = [r["text"] for r in mk(live).list(SCOPE)]
    ev = {
        "claim": "Real free-model extraction (real langmem + LangGraph InMemoryStore) through BlackBoxStaged; synthetic dialogues.",
        "environment": {k: md.version(k) for k in ("langmem", "langgraph", "trustcall", "langchain-openai")} | {"model": a.model},
        "pricing": {k: pricing[k] for k in ("id", "is_free", "prompt_per_mtok", "completion_per_mtok")},
        "caps": {"max_requests": a.max_requests, "max_tokens": a.max_tokens, "cost_cap_usd": 0},
        "usage": usage, "sessions": rows, "final_live_memories": final,
    }
    (out / "evidence.json").write_text(json.dumps(ev, indent=2, default=str), encoding="utf-8")
    import shutil
    for d in out.glob("lm-*"): shutil.rmtree(d, ignore_errors=True)
    print(json.dumps({"usage": usage, "final": final}, indent=1))
    for r in rows: print(r.get("decision"), r.get("error"), r.get("reason", r.get("message", ""))[:120], r.get("extracted"))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
