#!/usr/bin/env python
"""Reproduce the guarded real-Mem0 evidence (no network, no paid calls).

Runs the unmodified ``mem0ai==2.2.1`` (embedded Qdrant + SQLite history, real
spaCy entity extraction) behind the host-owned staged write wrapper
:class:`membud.adapters.mem0_staged.StagedMem0`, with deterministic offline
model fakes, and writes:

* ``<out>/evidence.json``  -- deterministic scenario results (byte-stable across
  runs on the same pinned environment; no ids, timestamps or hashes of
  run-specific content);
* ``<out>/timing.json``    -- latency/scaling measurements (machine-dependent).

For every refused operation it records whether the raw bytes of EVERY host file
(Qdrant memory + entity collections, SQLite history/messages DB, Mem0 config
directory) and every logical surface were byte-identical afterwards, and shows
that the same operation run UNGUARDED on a twin host does change each surface.

Usage (pinned environment, see requirements-mem0.txt)::

    PYTHONPATH=src .venv-mem0/bin/python scripts/run_mem0_guarded_evidence.py
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
import warnings
from pathlib import Path

# Telemetry is an unstageable outward side effect: disable it before mem0 imports.
os.environ["MEM0_TELEMETRY"] = "False"
_MEM0_DIR = tempfile.mkdtemp(prefix="membud-evidence-mem0-dir-")
os.environ["MEM0_DIR"] = _MEM0_DIR
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import logging  # noqa: E402

logging.disable(logging.CRITICAL)

from mem0 import Memory  # noqa: E402

from membud.adapters.mem0_offline import OfflineHost  # noqa: E402
from membud.adapters.mem0_staged import (  # noqa: E402
    MEM0_SOURCE_SHA256,
    StagedMem0,
    states_equivalent,
)
from membud.adapters.mem0_bridge import MEM0_WHEEL_SHA256  # noqa: E402

SEED_MESSAGE = [{"role": "user", "content": "I am Alice, I live in Berlin and I work at Acme Corp."}]
SEED = ("Alice lives in Berlin", "Alice works at Acme Corp")
SURFACES = ("membud_test", "membud_test_entities", "history", "messages")


def _revision() -> dict:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()

    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def _environment() -> dict:
    return {
        "python": platform.python_version(),
        "mem0ai": metadata.version("mem0ai"),
        "mem0ai_wheel_sha256": MEM0_WHEEL_SHA256,
        "mem0_source_sha256": dict(MEM0_SOURCE_SHA256),
        "qdrant_client": metadata.version("qdrant-client"),
        "spacy": metadata.version("spacy"),
        "en_core_web_sm": metadata.version("en_core_web_sm"),
        "vector_store": "qdrant (embedded, on-disk)",
        "history_store": "sqlite (mem0 SQLiteManager: history + messages)",
        "llm": "ScriptedLLM (offline fake)",
        "embedder": "HashEmbedder (offline fake, 16 dims)",
        "telemetry": "disabled (MEM0_TELEMETRY=False; wrapper refuses otherwise)",
    }


def _fresh(workdir: Path, name: str):
    guarded = OfflineHost(workdir / name / "guarded")
    twin = OfflineHost(workdir / name / "unguarded-control")
    stager = StagedMem0(guarded.memory, workdir=workdir / name / "membud")
    for host in (guarded, twin):
        host.llm.push(*SEED)
    assert stager.add(SEED_MESSAGE, user_id="u1").applied
    twin.memory.add(SEED_MESSAGE, user_id="u1")
    return guarded, twin, stager


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _stable(text: str) -> str:
    return _UUID.sub("<uuid>", text)


def _failed_checks(outcome) -> list[str]:
    result = None if outcome.guarded is None else outcome.guarded.result
    if result is None:
        return []
    return sorted(f"{c.name}:{c.reason_code}" for c in result.checks if not c.passed)


def _surface_diff(before: dict, after: dict) -> list[str]:
    return sorted(name for name in SURFACES if before.get(name) != after.get(name))


def refusal(workdir: Path, name: str, op, control, *, tamper=None) -> dict:
    """Run a refused operation; prove every host surface is untouched; run the control."""
    guarded, twin, stager = _fresh(workdir, name)
    try:
        before = guarded.snapshot()
        twin_before = twin.surfaces()
        calls_before = len(guarded.llm.calls)
        restore = tamper() if tamper else None
        try:
            outcome = op(stager, guarded)
        except Exception as exc:  # errors raised by real Mem0 inside the shadow
            outcome = exc
        finally:
            if restore:
                restore()
        files_before, logical_before, surfaces_before = before
        files_after, logical_after, surfaces_after = (
            guarded.file_bytes(),
            guarded.logical().digest(),
            guarded.surfaces(),
        )
        record = {
            "scenario": name,
            "host_file_count": len(files_after),
            "host_files_byte_identical": files_after == files_before,
            "logical_state_identical": logical_after == logical_before,
            "surfaces_identical": {s: surfaces_after[s] == surfaces_before[s] for s in SURFACES},
            "llm_calls": len(guarded.llm.calls) - calls_before,
        }
        if isinstance(outcome, Exception):
            record.update(
                {"decision": "ERROR_FROM_REAL_MEM0", "applied": False, "error_type": type(outcome).__name__}
            )
        else:
            record.update(
                {
                    "decision": outcome.decision,
                    "applied": outcome.applied,
                    "error_type": outcome.error_type,
                    "reason": _stable(outcome.reason),
                    "failed_verifier_checks": _failed_checks(outcome),
                    "pending_side_effects_enumerated_before_suppression": outcome.summary()[
                        "pending_side_effects"
                    ],
                }
            )
        if control is not None:
            if tamper:
                restore = tamper()
            try:
                control(twin)
            except Exception as exc:  # pragma: no cover - recorded, not hidden
                record["control_error"] = type(exc).__name__
            finally:
                if tamper:
                    restore()
            record["unguarded_control_changed_surfaces"] = _surface_diff(twin_before, twin.surfaces())
        return record
    finally:
        guarded.close()
        twin.close()


def acceptance(workdir: Path) -> list[dict]:
    """ACCEPT paths: persisted == verified staged state and == unmodified-Mem0 semantics."""
    guarded, twin, stager = _fresh(workdir, "accept")
    records = []
    try:
        def step(name, guarded_op, twin_op):
            outcome = guarded_op()
            twin_op()
            records.append(
                {
                    "scenario": name,
                    "decision": outcome.decision,
                    "applied": outcome.applied,
                    "live_state_equals_verified_staged_state": outcome.live_state_digest
                    == outcome.post_state_digest,
                    "semantic_parity_with_unguarded_mem0": guarded.view() == twin.view(),
                    "pending_side_effects": outcome.summary()["pending_side_effects"],
                }
            )

        message = [{"role": "user", "content": "Alice has a dog named Rex"}]
        for host in (guarded, twin):
            host.llm.push("Alice has a dog named Rex")
        step("accept_add", lambda: stager.add(message, user_id="u1"), lambda: twin.memory.add(message, user_id="u1"))
        step(
            "accept_update",
            lambda: stager.update(
                guarded.ids("Alice lives in Berlin"),
                "Alice lives in Hamburg",
                source_text="Alice lives in Hamburg now",
            ),
            lambda: twin.memory.update(twin.ids("Alice lives in Berlin"), text="Alice lives in Hamburg"),
        )
        evidence = [{"id": "forget-1", "supports": [], "source": "chat:7", "text": "Please forget Rex"}]
        auth = {"source": "chat:7", "evidence_id": "forget-1", "rationale": "user asked to forget"}
        step(
            "accept_delete_with_host_authorization",
            lambda: stager.delete(guarded.ids("Alice has a dog named Rex"), evidence=evidence, authorization=auth),
            lambda: twin.memory.delete(twin.ids("Alice has a dog named Rex")),
        )
        reopened = guarded.view()
        guarded.reopen()
        records.append({"scenario": "accepted_state_survives_reopen_from_disk", "ok": guarded.view() == reopened})
        return records
    finally:
        guarded.close()
        twin.close()


def injected_failures(workdir: Path) -> list[dict]:
    records = []
    for variant in ("entity_write_fails_then_rollback_ok", "entity_write_fails_and_rollback_fails"):
        guarded, twin, stager = _fresh(workdir, f"inject-{variant}")
        try:
            before = guarded.logical()
            client = guarded.memory.vector_store.client
            real_upsert, real_delete = client.upsert, client.delete

            def flaky(collection_name, points, **kwargs):
                if collection_name.endswith("_entities"):
                    raise RuntimeError("injected entity-store failure")
                return real_upsert(collection_name=collection_name, points=points, **kwargs)

            client.upsert = flaky
            if variant.endswith("rollback_fails"):
                def broken(*args, **kwargs):
                    raise RuntimeError("injected rollback failure")

                client.delete = broken
            guarded.llm.push("Alice has a dog named Rex")
            outcome = stager.add([{"role": "user", "content": "Alice has a dog named Rex"}], user_id="u1")
            client.upsert, client.delete = real_upsert, real_delete
            restored = states_equivalent(guarded.logical(), before)
            records.append(
                {
                    "scenario": variant,
                    "decision": outcome.decision,
                    "applied": outcome.applied,
                    "reported_loudly": "could not restore" in outcome.reason
                    if variant.endswith("rollback_fails")
                    else "injected" in outcome.reason,
                    "logical_state_restored": restored,
                }
            )
        finally:
            guarded.close()
            twin.close()
    return records


def timing(workdir: Path) -> dict:
    results = {}
    for size in (0, 100, 500):
        host = OfflineHost(workdir / f"timing-{size}")
        try:
            for index in range(size):
                host.memory.add(f"Alice owns item {index} of kind {index % 7}", user_id="u1", infer=False)
            stager = StagedMem0(host.memory, workdir=workdir / f"timing-{size}-membud")
            hold, accept = [], []
            for trial in range(3):
                host.llm.push("Alice lives on Mars")
                start = time.perf_counter()
                stager.add(SEED_MESSAGE, user_id="u1")
                hold.append((time.perf_counter() - start) * 1000)
            for trial in range(3):
                text = f"Alice acquired thing {trial}"
                host.llm.push(text)
                start = time.perf_counter()
                stager.add([{"role": "user", "content": text}], user_id="u1")
                accept.append((time.perf_counter() - start) * 1000)
            results[str(size)] = {
                "hold_ms_median": round(statistics.median(hold), 1),
                "accept_ms_median": round(statistics.median(accept), 1),
            }
        finally:
            host.close()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=ROOT / "data/results/mem0-guarded-v1")
    parser.add_argument("--skip-timing", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="membud-mem0-evidence-") as tmp:
        workdir = Path(tmp)
        hallucination = ("Alice lives on Mars", "Alice is the CEO of Initech Industries")

        def add_hold(stager, host):
            host.llm.push(*hallucination)
            return stager.add(SEED_MESSAGE, user_id="u1")

        def add_hold_control(twin):
            twin.llm.push(*hallucination)
            twin.memory.add(SEED_MESSAGE, user_id="u1")

        def tamper_with(action):
            original = Memory._add_to_vector_store

            def patched(self, *a, **kw):
                result = original(self, *a, **kw)
                action(self)
                return result

            def install():
                Memory._add_to_vector_store = patched
                return lambda: setattr(Memory, "_add_to_vector_store", original)

            return install

        def foreign_scope(memory):
            memory.vector_store.insert(
                vectors=[[0.5] * 16],
                ids=["00000000-0000-0000-0000-0000000000aa"],
                payloads=[{"data": "Mallory was here", "user_id": "victim"}],
            )

        def extra_table(memory):
            memory.db.connection.execute("CREATE TABLE exfil (x TEXT)")
            memory.db.connection.commit()

        def history_rewrite(memory):
            memory.db.connection.execute("UPDATE history SET new_memory = 'tampered'")
            memory.db.connection.commit()

        supported = "Alice lives in Berlin and Alice works at Acme Corp"

        def tampered_add(stager, host):
            host.llm.push(supported)
            return stager.add(SEED_MESSAGE, user_id="u1")

        def ghost_update(stager, host):
            return stager.update(
                host.ids("Alice lives in Berlin"), "Alice lives in Tokyo", source_text="Alice is a Berlin resident"
            )

        def ghost_update_control(twin):
            twin.memory.update(twin.ids("Alice lives in Berlin"), text="Alice lives in Tokyo")

        contradicting = [
            {
                "id": "ev-c",
                "supports": ["Alice lives in Hamburg"],
                "contradicts": ["Alice lives in Hamburg"],
                "source": "chat:9",
                "text": "No, Alice does NOT live in Hamburg.",
            }
        ]

        refusals = [
            refusal(workdir, "hold_add_unsupported_claims", add_hold, add_hold_control),
            refusal(workdir, "hold_update_unsupported_text", ghost_update, ghost_update_control),
            refusal(
                workdir,
                "hold_update_contradicted_by_host_evidence",
                lambda s, h: s.update(h.ids("Alice lives in Berlin"), "Alice lives in Hamburg", evidence=contradicting),
                lambda t: t.memory.update(t.ids("Alice lives in Berlin"), text="Alice lives in Hamburg"),
            ),
            refusal(
                workdir,
                "rejected_delete_without_host_authorization",
                lambda s, h: s.delete(h.ids("Alice works at Acme Corp"), authorization=None, evidence=[]),
                lambda t: t.memory.delete(t.ids("Alice works at Acme Corp")),
            ),
            refusal(
                workdir,
                "refused_delete_citing_nonexistent_evidence",
                lambda s, h: s.delete(
                    h.ids("Alice works at Acme Corp"),
                    authorization={"source": "chat:1", "evidence_id": "nope", "rationale": "model said so"},
                    evidence=[],
                ),
                lambda t: t.memory.delete(t.ids("Alice works at Acme Corp")),
            ),
            refusal(
                workdir,
                "error_inside_real_mem0_extraction",
                lambda s, h: s.add(SEED_MESSAGE, user_id="u1"),  # empty LLM queue -> real Mem0 raises
                None,
            ),
        ]
        for name, action in (
            ("rejected_unmodeled_write_to_foreign_scope", foreign_scope),
            ("rejected_unmodeled_ddl", extra_table),
            ("rejected_unmodeled_history_rewrite", history_rewrite),
        ):
            refusals.append(refusal(workdir, name, tampered_add, None, tamper=tamper_with(action)))

        evidence = {
            "schema": "membud-mem0-guarded-evidence-v1",
            "claim": (
                "Behind the host-owned staged write wrapper, unmodified mem0ai==2.2.1 (embedded Qdrant + SQLite "
                "history, real entity linking) leaves every host write surface byte-identical when the operation "
                "is HOLD/REJECTED/errors; ACCEPT persists exactly the verified staged state."
            ),
            "environment": _environment(),
            "code_revision": _revision(),
            "refusals": refusals,
            "accepts": acceptance(workdir),
            "injected_commit_failures": injected_failures(workdir),
        }
        (args.output / "evidence.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        if not args.skip_timing:
            (args.output / "timing.json").write_text(
                json.dumps(
                    {"note": "machine-dependent latency of guarded add (shadow clone is O(N))", "by_store_size": timing(workdir)},
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

    ok = all(
        r["host_files_byte_identical"] and r["logical_state_identical"] and all(r["surfaces_identical"].values())
        and not r["applied"]
        for r in refusals
    )
    ok = ok and all(a.get("applied", True) and a.get("semantic_parity_with_unguarded_mem0", True) for a in evidence["accepts"])
    print(f"refusals: {len(refusals)}  all host surfaces byte-identical and nothing applied: {ok}")
    print(f"wrote {args.output}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
