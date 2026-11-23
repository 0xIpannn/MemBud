#!/usr/bin/env python
"""Real-model Graphiti (Neo4j) run through the black-box guard: free OpenRouter model, synthetic data, $0.

Real graphiti-core + real Neo4j server; real free chat model for extraction; hash embedder and flat
cross-encoder are offline fakes.  Includes a contradiction pair (Berlin -> Lisbon).  Hard request/token caps.
Usage: OPENROUTER_API_KEY=... GRAPHITI_TELEMETRY_ENABLED=false PYTHONPATH=src \
       .venv-graphiti/bin/python scripts/run_graphiti_realmodel.py   (Neo4j must listen on 127.0.0.1:7687)
"""
from __future__ import annotations
import argparse, asyncio, importlib.metadata as md, json, os, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path

os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tests"))
import logging; logging.getLogger("neo4j").setLevel(logging.ERROR); logging.getLogger("graphiti_core").setLevel(logging.ERROR)

from graphiti_core import Graphiti
from graphiti_core.driver.neo4j_driver import Neo4jDriver
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from membud.adapters import BlackBoxStaged
from membud.adapters.blackbox_backends import graphiti_ops
from membud.adapters.mem0_staged import SpeakerNormalizedGrounding
from membud.llm.client import enforce_openrouter_zero_price
import adapter_harness
GB = adapter_harness.HARNESSES["graphiti-blackbox"]

SCOPE = {"user_id": "u1"}
DIALOGUES = ["I adopted a dog named Biscuit.", "I live in Berlin.", "I moved to Lisbon and I no longer live in Berlin.", "I am allergic to peanuts."]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="nvidia/nemotron-3.5-lightning:free")
    ap.add_argument("--max-requests", type=int, default=40)
    ap.add_argument("--max-tokens", type=int, default=150000)
    ap.add_argument("--out", default=str(ROOT / "data/results/graphiti-blackbox-realmodel-v1"))
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    pricing = enforce_openrouter_zero_price(a.model)
    usage = {"requests": 0, "tokens": 0, "errors": []}

    class Metered(OpenAIGenericClient):
        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)
            real = self.client.chat.completions.create

            async def create(*aa, **kk):
                if usage["requests"] >= a.max_requests or usage["tokens"] >= a.max_tokens:
                    raise RuntimeError("budget cap reached")
                usage["requests"] += 1
                try:
                    r = await real(*aa, **kk)
                except Exception as e:
                    usage["errors"].append(type(e).__name__); raise
                usage["tokens"] += int(getattr(getattr(r, "usage", None), "total_tokens", 0) or 0)
                return r
            self.client.chat.completions.create = create

    llm = Metered(LLMConfig(api_key=os.environ["OPENROUTER_API_KEY"], model=a.model, small_model=a.model,
                            base_url="https://openrouter.ai/api/v1", temperature=0),
                  structured_output_mode="json_schema")
    loop = asyncio.new_event_loop(); run = loop.run_until_complete
    g = Graphiti(graph_driver=Neo4jDriver(uri="bolt://127.0.0.1:7687", user="neo4j", password="x"),
                 llm_client=llm, embedder=GB._embedder(), cross_encoder=GB._cross_encoder())
    run(g.build_indices_and_constraints())
    prefix = f"rm{uuid.uuid4().hex[:8]}-"
    n = [0]

    def native_add(graphiti, group, messages):
        return run(graphiti.add_episode(name="ep", episode_body=" ".join(m["content"] for m in messages),
                                        source_description="chat", reference_time=datetime.now(timezone.utc), group_id=group))

    def ops(suffix, name="graphiti"):
        return graphiti_ops(g, run, native_add, group_prefix=prefix, group_suffix=suffix, name=name)

    def factory():
        n[0] += 1
        return ops(f"__twin{n[0]}", f"graphiti-twin{n[0]}")

    def closer(o):
        run(g.driver.execute_query("MATCH (n) WHERE n.group_id STARTS WITH $p AND n.group_id ENDS WITH $s DETACH DELETE n",
                                   params={"p": prefix, "s": "__" + o.name.rsplit("-", 1)[1]}))

    guard = BlackBoxStaged(ops(""), factory, workdir=Path(tempfile.mkdtemp(prefix="gr-", dir=out)),
                           grounding=SpeakerNormalizedGrounding(), scratch_closer=closer)
    rows = []
    for i, text in enumerate(DIALOGUES):
        try:
            o = guard.add([{"role": "user", "content": text}], scope=SCOPE, case_id=f"s{i}")
            rows.append({"session": i, "input": text, "decision": o.decision, "applied": o.applied, "reason": o.reason,
                         "error_type": o.error_type, "changes": [[c.kind, c.old_text, c.new_text] for c in o.changes]})
        except Exception as e:
            rows.append({"session": i, "input": text, "error": type(e).__name__, "message": str(e)[:300]})
    final = [[r["text"], r["extra"]] for r in ops("").list(SCOPE)]
    ev = {
        "claim": "Real free-model extraction (real graphiti-core on a real Neo4j) through BlackBoxStaged; synthetic dialogues incl. a contradiction.",
        "grounding": "SpeakerNormalizedGrounding (deterministic, offline)",
        "environment": {k: md.version(k) for k in ("graphiti-core", "neo4j")} | {"model": a.model, "neo4j_server": "community 5.26.0", "embedder": "hash (offline)", "structured_output_mode": "json_schema"},
        "pricing": {k: pricing[k] for k in ("id", "is_free", "prompt_per_mtok", "completion_per_mtok")},
        "caps": {"max_requests": a.max_requests, "max_tokens": a.max_tokens, "cost_cap_usd": 0},
        "usage": usage, "sessions": rows, "final_live_facts": final,
    }
    (out / "evidence.json").write_text(json.dumps(ev, indent=2, default=str), encoding="utf-8")
    run(g.driver.execute_query("MATCH (n) WHERE n.group_id STARTS WITH $p DETACH DELETE n", params={"p": prefix}))
    run(g.close())
    import shutil
    for d in out.glob("gr-*"): shutil.rmtree(d, ignore_errors=True)
    print(json.dumps({"usage": usage, "final": final}, indent=1, default=str))
    for r in rows: print(r.get("decision"), r.get("error"), (r.get("reason") or r.get("message") or "")[:140], r.get("changes"))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
