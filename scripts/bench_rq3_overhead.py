#!/usr/bin/env python3
"""RQ3 overhead benchmark (offline, deterministic inputs, $0).

Two parts:

``--part verifier`` (stdlib venv): times graph construction, graph verification, text
serialization, the text verifier and the fair text verifier on synthetic schema-v2
transitions across prior-store sizes N and transition sizes k.  Each callable is
calibrated to a sample of at least ``--min-sample-ms`` (loop count doubled until reached),
warmed up, then sampled ``--repeats`` times in interleaved rounds with a shuffled order per
round (garbage collector disabled during a sample, as ``timeit`` does).  Two noise
references are measured in the same rounds: an empty call (harness floor) and an exact
duplicate of the graph path (A/A), whose difference from the original shows what "the same
code" looks like under this harness.

``--part adapters`` (pinned Mem0 venv, ``requirements-mem0.txt``): times one guarded
``add`` through ``StagedMem0`` (shadow clone of Mem0's storage) and through
``BlackBoxStaged`` (public-API twin) on a real ``mem0ai==2.2.1`` with offline fakes for the
LLM and embedder, across live-store sizes N and k extracted facts, for HOLD and ACCEPT.
Phases are attributed by wrapping module functions with an exclusive (stack-based) timer:
time inside a nested wrapped call is charged to the inner phase only.  Nothing in ``src`` is
modified; the wrappers are installed only in this process.

Inputs are seeded and deterministic; timings are machine-dependent and not byte-stable.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SEED = 20261124


# ---------------------------------------------------------------------------
# environment and statistics
# ---------------------------------------------------------------------------


def environment() -> dict[str, Any]:
    cpu = ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    governor = ""
    try:
        governor = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").read_text().strip()
    except OSError:
        pass
    try:
        rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        rev = ""
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_model": cpu,
        "logical_cpus": os.cpu_count(),
        "cpu_governor": governor,
        "timer": "time.perf_counter_ns",
        "timer_resolution_s": time.get_clock_info("perf_counter").resolution,
        "git_head": rev,
    }


def summarize(samples: list[float]) -> dict[str, float]:
    s = sorted(samples)
    q = statistics.quantiles(s, n=4, method="inclusive") if len(s) >= 2 else [s[0]] * 3
    return {
        "n": len(s),
        "median": statistics.median(s),
        "q1": q[0],
        "q3": q[2],
        "iqr": q[2] - q[0],
        "min": s[0],
        "max": s[-1],
    }


def detectable(a: dict[str, float], b: dict[str, float], rel_floor: float) -> bool:
    """A difference counts only if the IQRs do not overlap and it exceeds the A/A floor."""
    disjoint = a["q3"] < b["q1"] or b["q3"] < a["q1"]
    rel = abs(a["median"] - b["median"]) / max(min(a["median"], b["median"]), 1e-12)
    return disjoint and rel > rel_floor


# ---------------------------------------------------------------------------
# part 1: verifier micro-benchmark
# ---------------------------------------------------------------------------


def make_transition(n: int, k: int, *, faulty: bool = False):
    """Valid schema-v2 transition: N prior records, k operations (WRITE/REVISE/SUPERSEDE)."""
    from membud.schemas import Evidence, Operation, Record, Transition

    rng = random.Random(SEED + 7919 * n + k)
    before = [Record(f"r{i}", (f"user fact {i} about topic {i % 13} value {rng.randrange(10**6)}",)) for i in range(n)]
    targets = list(range(n))
    rng.shuffle(targets)
    kinds = ["WRITE", "REVISE", "SUPERSEDE"] if n else ["WRITE"]
    candidate = {r.id: r for r in before}
    order = [r.id for r in before]
    ops, evidence, new_claims = [], [], []
    for j in range(k):
        kind = kinds[j % len(kinds)]
        if kind != "WRITE" and not targets:
            kind = "WRITE"
        claim = f"new fact {j} stated by the user value {rng.randrange(10**6)}"
        new_claims.append(claim)
        evidence.append(Evidence(f"e{j}", (claim,), source="synthetic:user", text=f"The user said: {claim}."))
        if kind == "WRITE":
            rid = f"w{j}"
            candidate[rid] = Record(rid, (claim,))
            order.append(rid)
            ops.append(Operation("WRITE", record_id=rid))
        elif kind == "REVISE":
            tid = f"r{targets.pop()}"
            candidate[tid] = Record(tid, (claim,))
            ops.append(Operation("REVISE", record_id=tid, target_id=tid))
        else:
            tid = f"r{targets.pop()}"
            rid = f"s{j}"
            del candidate[tid]
            order[order.index(tid)] = rid
            candidate[rid] = Record(rid, (claim,))
            ops.append(Operation("SUPERSEDE", record_id=rid, target_id=tid, reason="user update"))
    if faulty:
        # one unsupported claim attached to the first new record
        first = ops[0].record_id
        rec = candidate[first]
        candidate[first] = Record(rec.id, rec.claims + ("an unsupported claim nobody stated",))
    cand = tuple(candidate[i] for i in order)
    surviving = [c for r in cand for c in r.claims if not c.startswith("new fact") and "unsupported" not in c]
    required = tuple(dict.fromkeys(surviving + new_claims))
    return Transition(f"bench-n{n}-k{k}", tuple(before), cand, tuple(evidence), required, tuple(ops), 2)


def calibrate(fn: Callable[[], Any], min_sample_ns: int) -> int:
    loops = 1
    while True:
        t0 = time.perf_counter_ns()
        for _ in range(loops):
            fn()
        if time.perf_counter_ns() - t0 >= min_sample_ns or loops >= 1 << 20:
            return loops
        loops *= 2


def sample(fn: Callable[[], Any], loops: int) -> float:
    gc_was = gc.isenabled()
    gc.disable()
    try:
        t0 = time.perf_counter_ns()
        for _ in range(loops):
            fn()
        dt = time.perf_counter_ns() - t0
    finally:
        if gc_was:
            gc.enable()
    return dt / loops / 1e3  # microseconds per call


def run_verifier(args) -> dict[str, Any]:
    from membud.fairness import verify_text_fair
    from membud.graph import build_graph
    from membud.graph_verification import verify_graph
    from membud.serialization import textual_representation
    from membud.verification import _verify_claim_sets, verify

    sizes = [int(x) for x in args.sizes.split(",")]
    ks = [int(x) for x in args.ks.split(",")]
    rng = random.Random(SEED)
    cells = []
    for n in sizes:
        for k in ks:
            for faulty in (False, True) if k == ks[0] or args.faulty_all else (False,):
                t = make_transition(n, k, faulty=faulty)
                g = build_graph(t)
                decisions = {
                    "graph": verify(t, "graph").decision,
                    "text": verify(t, "text").decision,
                    "text_fair": verify_text_fair(t).decision,
                }
                expected = "HOLD" if faulty else "ACCEPT"
                if set(decisions.values()) != {expected}:
                    raise SystemExit(f"unexpected decisions at n={n} k={k} faulty={faulty}: {decisions}")
                fns: dict[str, Callable[[], Any]] = {
                    "empty_call": lambda: None,
                    "validate": t.validate,
                    "graph_build": lambda t=t: build_graph(t),
                    "graph_verify": lambda t=t, g=g: verify_graph(t, graph=g),
                    "graph_total": lambda t=t: verify(t, "graph"),
                    "graph_total_AA": lambda t=t: verify(t, "graph"),
                    "text_serialize": lambda t=t: textual_representation(t),
                    "text_verify": lambda t=t: _verify_claim_sets(t),
                    "text_fair_verify": lambda t=t: verify_text_fair(t),
                }
                warmups = {}
                for name, fn in fns.items():
                    t0 = time.perf_counter_ns()
                    fn()
                    one = max(time.perf_counter_ns() - t0, 1)
                    # adaptive warmup: the configured count, but at most ~0.5 s per callable (min 2)
                    warmups[name] = max(2, min(args.warmup, int(5e8 // one)))
                    for _ in range(warmups[name]):
                        fn()
                loops = {name: calibrate(fn, int(args.min_sample_ms * 1e6)) for name, fn in fns.items()}
                samples: dict[str, list[float]] = {name: [] for name in fns}
                names = list(fns)
                for _ in range(args.repeats):
                    rng.shuffle(names)
                    for name in names:
                        samples[name].append(sample(fns[name], loops[name]))
                stats = {name: summarize(v) for name, v in samples.items()}
                # attribution of the super-linear term: graph_verification.py rebuilds the full
                # (from, type, to) edge set once per required claim (lines 104-108).
                edges = g["edges"]
                edge_set_us = []
                for _ in range(5):
                    t0 = time.perf_counter_ns()
                    _ = {(e["from"], e["type"], e["to"]) for e in edges}
                    edge_set_us.append((time.perf_counter_ns() - t0) / 1e3)
                quad = statistics.median(edge_set_us) * len(t.required_claims)
                cells.append({
                    "n_prior_records": n,
                    "k_operations": k,
                    "faulty": faulty,
                    "decision": expected,
                    "graph_nodes": len(g["nodes"]),
                    "graph_edges": len(g["edges"]),
                    "text_bytes": len(textual_representation(t).encode()),
                    "loops_per_sample": loops,
                    "warmup_calls": warmups,
                    "required_claims": len(t.required_claims),
                    "edge_set_rebuild_estimate_us": quad,
                    "edge_set_rebuild_share_of_graph_verify": quad / stats["graph_verify"]["median"],
                    "us_per_call": stats,
                })
                gt = stats["graph_total"]["median"]
                print(f"n={n:5d} k={k:3d} faulty={faulty!s:5} graph_total={gt:9.1f}us "
                      f"text={stats['text_verify']['median']:8.1f}us fair={stats['text_fair_verify']['median']:8.1f}us "
                      f"AA={stats['graph_total_AA']['median']:9.1f}us", flush=True)
    # A/A relative noise floor across cells
    aa = [abs(c["us_per_call"]["graph_total"]["median"] - c["us_per_call"]["graph_total_AA"]["median"])
          / c["us_per_call"]["graph_total"]["median"] for c in cells]
    aa_floor = sorted(aa)[max(0, int(round(0.95 * len(aa))) - 1)]
    for c in cells:
        s = c["us_per_call"]
        c["comparisons"] = {
            "graph_total_vs_text_verify": {
                "ratio": s["graph_total"]["median"] / s["text_verify"]["median"],
                "detectable": detectable(s["graph_total"], s["text_verify"], aa_floor),
            },
            "graph_total_vs_text_fair_verify": {
                "ratio": s["graph_total"]["median"] / s["text_fair_verify"]["median"],
                "detectable": detectable(s["graph_total"], s["text_fair_verify"], aa_floor),
            },
            "text_fair_vs_text_verify": {
                "ratio": s["text_fair_verify"]["median"] / s["text_verify"]["median"],
                "detectable": detectable(s["text_fair_verify"], s["text_verify"], aa_floor),
            },
            "graph_total_AA": {
                "ratio": s["graph_total_AA"]["median"] / s["graph_total"]["median"],
                "detectable": detectable(s["graph_total_AA"], s["graph_total"], aa_floor),
            },
            "graph_build_share_of_graph_total": s["graph_build"]["median"] / s["graph_total"]["median"],
        }
    return {
        "part": "verifier",
        "settings": {"sizes": sizes, "ks": ks, "repeats": args.repeats, "warmup_max": args.warmup, "warmup_rule": "min(warmup_max, 0.5 s / one call), at least 2",
                     "min_sample_ms": args.min_sample_ms, "seed": SEED, "gc_disabled_during_sample": True},
        "noise": {
            "aa_relative_median_differences": aa,
            "aa_relative_floor_p95": aa_floor,
            "rule": "a difference is 'detectable' only if the two IQRs do not overlap AND the relative "
                    "median difference exceeds the A/A floor (95th percentile, across cells, of the "
                    "relative median difference between two identical graph_total callables)",
        },
        "cells": cells,
    }


# ---------------------------------------------------------------------------
# part 2: adapters on real Mem0 (offline fakes)
# ---------------------------------------------------------------------------


class PhaseTimer:
    """Exclusive phase timer: time inside a nested wrapped call goes to the inner phase."""

    def __init__(self) -> None:
        self.stack: list[list[Any]] = []  # [label, start_ns, child_ns]
        self.totals: dict[str, int] = {}
        self.calls: dict[str, int] = {}
        self.active = False

    def reset(self) -> None:
        self.stack.clear()
        self.totals = {}
        self.calls = {}

    def wrap(self, owner: Any, attr: str, label: str | Callable[["PhaseTimer"], str | None]) -> None:
        original = getattr(owner, attr)
        timer = self

        def wrapper(*a, **kw):
            name = label(timer) if callable(label) else label
            if not timer.active or name is None:
                return original(*a, **kw)
            frame = [name, time.perf_counter_ns(), 0]
            timer.stack.append(frame)
            try:
                return original(*a, **kw)
            finally:
                timer.stack.pop()
                elapsed = time.perf_counter_ns() - frame[1]
                own = elapsed - frame[2]
                timer.totals[name] = timer.totals.get(name, 0) + own
                timer.calls[name] = timer.calls.get(name, 0) + 1
                if timer.stack:
                    timer.stack[-1][2] += elapsed

        wrapper.__wrapped__ = original  # type: ignore[attr-defined]
        setattr(owner, attr, wrapper)

    def top(self) -> str | None:
        return self.stack[-1][0] if self.stack else None


def run_adapters(args) -> dict[str, Any]:
    os.environ.setdefault("MEM0_TELEMETRY", "False")
    mem0_dir = tempfile.mkdtemp(prefix="rq3-mem0-dir-")
    os.environ["MEM0_DIR"] = mem0_dir
    import mem0
    from mem0 import Memory

    import membud.adapters.blackbox as bb
    import membud.adapters.mem0_staged as ms
    import membud.adapters.reference as ref
    import membud.store as store
    import membud.verification as ver
    from membud.adapters import BlackBoxStaged
    from membud.adapters.blackbox_backends import mem0_ops
    from membud.adapters.mem0_offline import OfflineHost

    timer = PhaseTimer()
    # --- shared verification phases (both adapters go through GuardedMemory -> run_case -> verify)
    timer.wrap(ref.GuardedMemory, "propose_update", "guard_pipeline")
    timer.wrap(store, "build_graph", "graph_build")
    timer.wrap(store, "textual_representation", "text_serialize")
    timer.wrap(ref, "verify", "verify_dispatch")
    timer.wrap(ver, "build_graph", "graph_build")
    timer.wrap(ver, "verify_graph", "graph_verify")
    timer.wrap(ver, "_verify_claim_sets", "text_verify")
    timer.wrap(ver, "textual_representation", "text_serialize")
    timer.wrap(ms.LexicalGrounding, "__call__", "grounding")
    # --- StagedMem0 phases
    timer.wrap(ms, "dump_host_state", "snapshot_dump")
    timer.wrap(ms._Shadow, "__init__", "shadow_clone")
    timer.wrap(ms._Shadow, "close", "shadow_close")
    timer.wrap(ms, "diff_states", "diff")
    timer.wrap(ms.StagedMem0, "_analyze", "classify")
    timer.wrap(ms.Mem0HostAdapter, "replace_state", "commit")
    # --- BlackBoxStaged phases
    timer.wrap(bb, "observe", "observe")
    timer.wrap(bb.BlackBoxStaged, "_seed", "twin_seed")
    timer.wrap(bb, "_diff", "diff")
    timer.wrap(bb.BlackBoxHostAdapter, "replace_state", "commit")
    # native Mem0 write: attributed to its caller when inside seeding or commit
    timer.wrap(Memory, "add", lambda t: None if t.top() in {"twin_seed", "commit"} else "native_write")
    timer.wrap(Memory, "update", lambda t: None if t.top() in {"commit"} else "native_write")
    timer.wrap(Memory, "delete", lambda t: None if t.top() in {"commit"} else "native_write")

    sizes = [int(x) for x in args.adapter_sizes.split(",")]
    ks = [int(x) for x in args.adapter_ks.split(",")]
    work = Path(tempfile.mkdtemp(prefix="rq3-adapters-"))
    results = []
    try:
        for n in sizes:
            for adapter in args.adapters.split(","):
                for rep_name in args.representations.split(","):
                    for k in (ks if n == args.k_sweep_size else ks[:1]):
                        root = work / f"{adapter}-{rep_name}-n{n}-k{k}"
                        host = OfflineHost(root / "live")
                        timer.active = False
                        t0 = time.perf_counter()
                        for i in range(n):
                            host.memory.add(f"Alice owns item {i} of kind {i % 7}", user_id="u1", infer=False)
                        populate_s = time.perf_counter() - t0
                        twins: list[Any] = []
                        if adapter == "staged":
                            guard = ms.StagedMem0(host.memory, workdir=root / "membud", representation=rep_name)

                            def do_add(msgs, guard=guard):
                                return guard.add(msgs, user_id="u1")
                        else:
                            counter = {"n": 0}

                            def factory(root=root, host=host, counter=counter, twins=twins):
                                counter["n"] += 1
                                twin = OfflineHost(root / f"twin{counter['n']}")
                                twin.memory.llm = host.llm
                                ops = mem0_ops(twin.memory, [{"user_id": "u1"}], name="mem0-twin")
                                ops.handle_host = twin
                                twins.append(twin)
                                return ops

                            def closer(ops):
                                ops.handle_host.close()
                                shutil.rmtree(ops.handle_host.root, ignore_errors=True)

                            hooks = types.SimpleNamespace(factory=factory, closer=closer)
                            timer.wrap(hooks, "factory", "twin_create")
                            timer.wrap(hooks, "closer", "twin_close")
                            guard = BlackBoxStaged(mem0_ops(host.memory, [{"user_id": "u1"}]), hooks.factory,
                                                   workdir=root / "membud", representation=rep_name,
                                                   scratch_closer=hooks.closer)

                            def do_add(msgs, guard=guard):
                                return guard.add(msgs, scope={"user_id": "u1"})
                        reps = args.adapter_repeats if n < 1000 else args.adapter_repeats_large
                        for decision in ("HOLD", "ACCEPT"):
                            rows = []
                            for trial in range(args.adapter_warmup + reps):
                                facts = [f"Alice acquired thing {trial} part {j}" for j in range(k)]
                                if decision == "HOLD":
                                    host.llm.push(*[f"Alice lives on Mars sector {j}" for j in range(k)])
                                    msgs = [{"role": "user", "content": "Hello, how are you today?"}]
                                else:
                                    host.llm.push(*facts)
                                    msgs = [{"role": "user", "content": ". ".join(facts) + "."}]
                                before_ids = {m["id"] for m in host.memory.get_all(filters={"user_id": "u1"}, top_k=100_000)["results"]}
                                gc.collect()
                                timer.reset()
                                timer.active = True
                                t1 = time.perf_counter_ns()
                                out = do_add(msgs)
                                total_ns = time.perf_counter_ns() - t1
                                timer.active = False
                                if out.decision != decision:
                                    raise SystemExit(f"{adapter} n={n} k={k}: expected {decision}, got {out.decision} ({out.reason})")
                                # restore N so every trial sees the same store size (untimed, unguarded)
                                for m in host.memory.get_all(filters={"user_id": "u1"}, top_k=100_000)["results"]:
                                    if m["id"] not in before_ids:
                                        host.memory.delete(m["id"])
                                if trial < args.adapter_warmup:
                                    continue
                                phases = {name: ns / 1e6 for name, ns in timer.totals.items()}
                                phases["other"] = total_ns / 1e6 - sum(phases.values())
                                rows.append({"total_ms": total_ns / 1e6, "phases_ms": phases, "calls": dict(timer.calls)})
                            phase_names = sorted({p for r in rows for p in r["phases_ms"]})
                            summary = {
                                "total_ms": summarize([r["total_ms"] for r in rows]),
                                "phases_ms": {p: summarize([r["phases_ms"].get(p, 0.0) for r in rows]) for p in phase_names},
                                "calls_per_write": rows[0]["calls"],
                            }
                            results.append({
                                "adapter": adapter, "representation": rep_name, "n_live_records": n, "k_facts": k,
                                "decision": decision, "repeats": reps, "populate_s": populate_s, **summary,
                            })
                            print(f"{adapter:7s} {rep_name:5s} n={n:5d} k={k:2d} {decision:6s} "
                                  f"total={summary['total_ms']['median']:9.1f}ms "
                                  f"[IQR {summary['total_ms']['iqr']:.1f}] "
                                  f"verify={sum(summary['phases_ms'].get(p, {}).get('median', 0) for p in ('graph_build', 'graph_verify', 'text_verify', 'text_serialize', 'verify_dispatch')):.3f}ms",
                                  flush=True)
                        host.close()
                        for tw in twins:
                            tw.close()
                        shutil.rmtree(root, ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(mem0_dir, ignore_errors=True)
    return {
        "part": "adapters",
        "settings": {
            "sizes": sizes, "ks": ks, "k_sweep_size": args.k_sweep_size, "repeats": args.adapter_repeats,
            "repeats_at_n_ge_1000": args.adapter_repeats_large, "warmup": args.adapter_warmup,
            "mem0ai": getattr(mem0, "__version__", "2.2.1"), "llm": "ScriptedLLM (offline)",
            "embedder": "HashEmbedder, 16 dims (offline)", "vector_store": "embedded on-disk Qdrant",
            "gc": "collected before each write, enabled during it",
            "store_size_held_fixed": "memories added by an ACCEPT are deleted (untimed, unguarded) after each trial",
            "phase_attribution": "exclusive stack timer over wrapped module functions; 'other' = total minus attributed",
        },
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", choices=("verifier", "adapters"), required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--sizes", default="0,10,100,250,500,1000,2000")
    ap.add_argument("--ks", default="1,4,16,64")
    ap.add_argument("--faulty-all", action="store_true")
    ap.add_argument("--repeats", type=int, default=25)
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--min-sample-ms", type=float, default=5.0)
    ap.add_argument("--adapter-sizes", default="0,100,500,1000,2000")
    ap.add_argument("--adapter-ks", default="1,4,16")
    ap.add_argument("--k-sweep-size", type=int, default=500)
    ap.add_argument("--adapters", default="staged,blackbox")
    ap.add_argument("--representations", default="graph,text")
    ap.add_argument("--adapter-repeats", type=int, default=11)
    ap.add_argument("--adapter-repeats-large", type=int, default=7)
    ap.add_argument("--adapter-warmup", type=int, default=2)
    a = ap.parse_args()
    started = time.time()
    payload = run_verifier(a) if a.part == "verifier" else run_adapters(a)
    payload["environment"] = environment()
    payload["wall_clock_s"] = round(time.time() - started, 1)
    payload["note"] = "machine-dependent wall-clock timings; inputs are seeded and deterministic; not byte-stable"
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} in {payload['wall_clock_s']} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
