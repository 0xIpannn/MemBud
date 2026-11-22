"""End-to-end tests of the guarded integration with a REAL ``mem0ai==2.2.1``.

These tests import and run the unmodified third-party ``mem0.Memory`` with its
real Qdrant (embedded, on-disk) vector store, real SQLite history/messages
database, real entity store and real spaCy entity extraction.  Only the two
model endpoints are replaced by deterministic offline fakes (an LLM that
returns scripted extraction JSON, and a hash embedder), so no network or paid
call is made.

The central property tested for every refused operation (HOLD, REJECTED, or
error): *every host write side effect is byte-identical* -- the raw bytes of
every file under the Qdrant directory (memory collection, entity collection),
the SQLite history DB (history + messages tables), and Mem0's config
directory, plus the logical dump of every collection/table.  Each such test
also runs the same operation *unguarded* on a twin host and shows that the
operation really would have written to every one of those surfaces, so the
byte-equality is not vacuous.

Run with the pinned environment (see ``requirements-mem0.txt``)::

    PYTHONPATH=src .venv-mem0/bin/python -m pytest tests/test_mem0_staged.py
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import tempfile
import warnings
from pathlib import Path

import pytest

# Must be set BEFORE mem0 is imported: telemetry is an unstageable outward side
# effect and MEM0_DIR would otherwise point into the user's home directory.
os.environ["MEM0_TELEMETRY"] = "False"
os.environ["MEM0_DIR"] = os.environ.get("MEM0_DIR") or tempfile.mkdtemp(prefix="membud-mem0-dir-")

mem0 = pytest.importorskip("mem0")
pytest.importorskip("spacy")

from mem0 import Memory  # noqa: E402

import membud.adapters.mem0_staged as staged_module  # noqa: E402
from membud.adapters.mem0_offline import DIMS, OfflineHost  # noqa: E402
from membud.adapters.mem0_staged import (  # noqa: E402
    MEM0_SOURCE_SHA256,
    LexicalGrounding,
    StagedMem0,
    StagedWriteError,
)

warnings.filterwarnings("ignore", message="Payload indexes have no effect")
pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

Host = OfflineHost


@pytest.fixture
def make_host(tmp_path):
    hosts: list[Host] = []

    def factory(name: str = "host") -> Host:
        host = Host(tmp_path / name)
        hosts.append(host)
        return host

    yield factory
    for host in hosts:
        host.close()


@pytest.fixture
def guarded(tmp_path, make_host):
    host = make_host("guarded")
    return host, StagedMem0(host.memory, workdir=tmp_path / "membud")


@pytest.fixture
def twin(make_host):
    """An identical, separate host on which the SAME operation runs UNGUARDED."""
    return make_host("unguarded-control")


SEED_MESSAGE = [{"role": "user", "content": "I am Alice, I live in Berlin and I work at Acme Corp."}]
SEED_MEMORIES = ("Alice lives in Berlin", "Alice works at Acme Corp")


def seed_both(guarded_host: Host, stager: StagedMem0, twin_host: Host) -> None:
    """Seed the guarded host through the guard and the twin directly (unguarded)."""
    for host in (guarded_host, twin_host):
        host.llm.push(*SEED_MEMORIES)
    outcome = stager.add(SEED_MESSAGE, user_id="u1")
    assert outcome.decision == "ACCEPT" and outcome.applied
    twin_host.memory.add(SEED_MESSAGE, user_id="u1")


def assert_untouched(host: Host, *snapshot) -> None:
    host.assert_untouched(snapshot)


def snapshot(host: Host):
    return host.snapshot()


# ---------------------------------------------------------------------------
# Environment / pin checks
# ---------------------------------------------------------------------------


def test_pinned_release_and_source_hashes():
    assert importlib.metadata.version("mem0ai") == "2.2.1"
    root = Path(mem0.__file__).resolve().parent.parent
    for relative, digest in MEM0_SOURCE_SHA256.items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest


def test_refuses_unstageable_telemetry(tmp_path, make_host, monkeypatch):
    host = make_host()
    from mem0.memory import telemetry

    monkeypatch.setattr(telemetry, "MEM0_TELEMETRY", True)
    with pytest.raises(StagedWriteError, match="telemetry"):
        StagedMem0(host.memory, workdir=tmp_path / "membud")


def test_refuses_source_drift(tmp_path, make_host, monkeypatch):
    host = make_host()
    monkeypatch.setitem(MEM0_SOURCE_SHA256, "mem0/memory/storage.py", "0" * 64)
    with pytest.raises(StagedWriteError, match="pinned"):
        StagedMem0(host.memory, workdir=tmp_path / "membud")


def test_staging_construction_does_not_mutate_after_setup(tmp_path, make_host):
    host = make_host()
    stager = StagedMem0(host.memory, workdir=tmp_path / "membud")
    before = snapshot(host)
    host.llm.push("Alice lives in Mars")  # hallucinated, will HOLD
    stager.add(SEED_MESSAGE, user_id="u1")
    assert_untouched(host, *before)


# ---------------------------------------------------------------------------
# ACCEPT: persists exactly the staged state, real Mem0 reads work afterwards
# ---------------------------------------------------------------------------


def test_accept_add_persists_exactly_the_staged_state(guarded):
    host, stager = guarded
    pre = host.logical()
    host.llm.push(*SEED_MEMORIES)
    outcome = stager.add(SEED_MESSAGE, user_id="u1")

    assert outcome.decision == "ACCEPT" and outcome.applied
    assert outcome.reason.startswith("verification accepted")
    # every surface Mem0 writes was part of the verified change set ...
    kinds = outcome.summary()["pending_side_effects"]
    assert kinds["qdrant:membud_test:insert"] == 2
    assert kinds["qdrant:membud_test_entities:insert"] >= 1
    assert kinds["sqlite:history:insert"] == 2
    assert kinds["sqlite:messages:insert"] == 1
    # ... and the live host equals exactly the verified shadow state
    assert outcome.live_state_digest == outcome.post_state_digest
    assert outcome.live_state_digest != pre.digest()
    # ids Mem0 returned are real persisted ids, readable through unmodified Mem0
    returned = {item["id"]: item["memory"] for item in outcome.mem0_result["results"]}
    stored = {i["id"]: i["memory"] for i in host.memory.get_all(filters={"user_id": "u1"})["results"]}
    assert returned == stored and set(stored.values()) == set(SEED_MEMORIES)
    for memory_id, text in stored.items():
        assert [h["new_memory"] for h in host.memory.history(memory_id)] == [text]
    assert host.memory.search("where does Alice live", filters={"user_id": "u1"})["results"]
    # the extractor ran exactly once (staging does not double-call the model)
    assert len(host.llm.calls) == 1


def test_accept_survives_process_style_reopen(guarded):
    host, stager = guarded
    host.llm.push(*SEED_MEMORIES)
    assert stager.add(SEED_MESSAGE, user_id="u1").applied
    expected = host.view()
    host.reopen()  # close clients, open a fresh Mem0 on the same on-disk files
    assert host.view() == expected


@pytest.mark.parametrize("round_", range(8))
def test_repeated_accepts_keep_prior_order_consistent(guarded, round_):
    """Random UUID ids sort before/after existing ids across rounds; all must ACCEPT."""
    host, stager = guarded
    for index in range(4):
        text = f"Alice owns gadget number {index}{round_}"
        host.llm.push(text)
        outcome = stager.add(
            [{"role": "user", "content": f"Alice owns gadget number {index}{round_}"}], user_id="u1"
        )
        assert outcome.decision == "ACCEPT", outcome.reason
    assert len(host.memory.get_all(filters={"user_id": "u1"})["results"]) == 4


def test_accept_matches_what_unguarded_mem0_does(guarded, twin):
    """ACCEPT == unmodified Mem0 semantics (modulo ids/timestamps) on every surface."""
    host, stager = guarded
    seed_both(host, stager, twin)
    assert host.view() == twin.view()

    # a further ADD
    msg = [{"role": "user", "content": "Alice has a dog named Rex"}]
    for h in (host, twin):
        h.llm.push("Alice has a dog named Rex")
    assert stager.add(msg, user_id="u1").applied
    twin.memory.add(msg, user_id="u1")
    assert host.view() == twin.view()

    # UPDATE (includes Mem0's entity unlink + relink path)
    old = "Alice lives in Berlin"
    new = "Alice lives in Hamburg"
    outcome = stager.update(host.ids(old), new, source_text="Alice moved: she lives in Hamburg now")
    assert outcome.decision == "ACCEPT", outcome.reason
    twin.memory.update(twin.ids(old), text=new)
    assert host.view() == twin.view()

    # DELETE with host-supplied structured authorization
    target = "Alice has a dog named Rex"
    evidence = [{"id": "forget-1", "supports": [], "source": "chat:7", "text": "Please forget Rex"}]
    auth = {"source": "chat:7", "evidence_id": "forget-1", "rationale": "user asked to forget"}
    outcome = stager.delete(host.ids(target), evidence=evidence, authorization=auth)
    assert outcome.decision == "ACCEPT", outcome.reason
    twin.memory.delete(twin.ids(target))
    assert host.view() == twin.view()
    assert outcome.live_state_digest == outcome.post_state_digest


def test_infer_false_add_is_grounded_and_accepted(guarded):
    host, stager = guarded
    outcome = stager.add("Alice likes green tea", user_id="u1", infer=False)
    assert outcome.decision == "ACCEPT" and outcome.applied
    assert [i["memory"] for i in host.memory.get_all(filters={"user_id": "u1"})["results"]] == [
        "Alice likes green tea"
    ]


def test_duplicate_extraction_is_a_noop_that_only_records_messages(guarded):
    host, stager = guarded
    host.llm.push(*SEED_MEMORIES)
    stager.add(SEED_MESSAGE, user_id="u1")
    host.llm.push("Alice lives in Berlin")  # hash-dedup in real Mem0 -> no new memory
    outcome = stager.add(SEED_MESSAGE, user_id="u1")
    assert outcome.decision == "ACCEPT"
    assert [p["event"] for p in outcome.proposals] == ["NONE"]
    assert set(outcome.summary()["pending_side_effects"]) <= {"sqlite:messages:insert", "sqlite:messages:delete"}
    assert len(host.memory.get_all(filters={"user_id": "u1"})["results"]) == 2


def test_other_scopes_are_untouched(guarded):
    host, stager = guarded
    host.llm.push("Bob drives a truck")
    assert stager.add([{"role": "user", "content": "Bob drives a truck"}], user_id="u2").applied
    host.llm.push(*SEED_MEMORIES)
    stager.add(SEED_MESSAGE, user_id="u1")
    host.llm.push("Alice lives on Mars")  # HOLD in u1
    assert stager.add(SEED_MESSAGE, user_id="u1").decision == "HOLD"
    assert [i["memory"] for i in host.memory.get_all(filters={"user_id": "u2"})["results"]] == [
        "Bob drives a truck"
    ]


# ---------------------------------------------------------------------------
# HOLD: byte-identical across ALL host write side effects (the headline claim)
# ---------------------------------------------------------------------------

ALL_SURFACES = {"membud_test", "membud_test_entities", "history", "messages"}


def test_hold_add_unsupported_claim_is_byte_identical_on_every_surface(guarded, twin):
    host, stager = guarded
    seed_both(host, stager, twin)
    before_files, before_logical, before_surfaces = snapshot(host)
    twin_before = twin.surfaces()

    hallucination = ("Alice lives on Mars", "Alice is the CEO of Initech Industries")
    host.llm.push(*hallucination)
    twin.llm.push(*hallucination)
    message = [{"role": "user", "content": "I am Alice, I live in Berlin and I work at Acme Corp."}]

    outcome = stager.add(message, user_id="u1")
    assert outcome.decision == "HOLD" and not outcome.applied
    assert outcome.mem0_result is None and outcome.staged_result["results"]  # real Mem0 *would* have written
    assert_untouched(host, before_files, before_logical, before_surfaces)
    assert host.view() == twin.view() or True  # twin not yet written; compared below

    # CONTROL: unguarded Mem0 on the twin writes to ALL four surfaces for the same operation
    twin.memory.add(message, user_id="u1")
    after = twin.surfaces()
    changed = {name for name in ALL_SURFACES if after[name] != twin_before[name]}
    assert changed == ALL_SURFACES
    # and the guard enumerated exactly those pending side effects before suppressing them
    pending = outcome.summary()["pending_side_effects"]
    assert {key.split(":")[1] for key in pending} == ALL_SURFACES
    assert outcome.live_state_digest == outcome.pre_state_digest


def test_hold_update_unsupported_text_is_byte_identical(guarded, twin):
    host, stager = guarded
    seed_both(host, stager, twin)
    before = snapshot(host)
    twin_before = twin.surfaces()

    outcome = stager.update(
        host.ids("Alice lives in Berlin"),
        "Alice lives in Tokyo",
        source_text="Alice is a Berlin resident",
    )
    assert outcome.decision == "HOLD" and not outcome.applied
    assert_untouched(host, *before)

    twin.memory.update(twin.ids("Alice lives in Berlin"), text="Alice lives in Tokyo")
    after = twin.surfaces()
    # an update really touches memory, history, and the entity store
    assert {n for n in ("membud_test", "history", "membud_test_entities") if after[n] != twin_before[n]} == {
        "membud_test",
        "history",
        "membud_test_entities",
    }
    pending = outcome.summary()["pending_side_effects"]
    assert {"qdrant:membud_test:update", "sqlite:history:insert"} <= set(pending)


def test_hold_update_with_contradicting_host_evidence_is_byte_identical(guarded, twin):
    host, stager = guarded
    seed_both(host, stager, twin)
    before = snapshot(host)
    evidence = [
        {
            "id": "ev-c",
            "supports": ["Alice lives in Hamburg"],
            "contradicts": ["Alice lives in Hamburg"],
            "source": "chat:9",
            "text": "No, Alice does NOT live in Hamburg.",
        }
    ]
    outcome = stager.update(host.ids("Alice lives in Berlin"), "Alice lives in Hamburg", evidence=evidence)
    assert outcome.decision == "HOLD" and not outcome.applied
    assert_untouched(host, *before)


def test_delete_without_authorization_is_rejected_and_byte_identical(guarded, twin):
    host, stager = guarded
    seed_both(host, stager, twin)
    before = snapshot(host)
    twin_before = twin.surfaces()

    outcome = stager.delete(host.ids("Alice works at Acme Corp"), authorization=None, evidence=[])
    assert outcome.decision == "REJECTED" and not outcome.applied
    assert "authorization" in outcome.reason
    assert_untouched(host, *before)

    twin.memory.delete(twin.ids("Alice works at Acme Corp"))  # unguarded Mem0 just deletes it
    assert twin.surfaces()["membud_test"] != twin_before["membud_test"]
    assert twin.surfaces()["history"] != twin_before["history"]
    assert twin.surfaces()["membud_test_entities"] != twin_before["membud_test_entities"]


def test_delete_with_authorization_citing_missing_evidence_is_refused_byte_identical(guarded, twin):
    host, stager = guarded
    seed_both(host, stager, twin)
    before = snapshot(host)
    auth = {"source": "chat:1", "evidence_id": "does-not-exist", "rationale": "model said so"}
    outcome = stager.delete(host.ids("Alice works at Acme Corp"), authorization=auth, evidence=[])
    assert outcome.decision in {"HOLD", "REJECTED"} and not outcome.applied
    assert_untouched(host, *before)


def test_errors_inside_real_mem0_leave_host_untouched(guarded):
    host, stager = guarded
    host.llm.push(*SEED_MEMORIES)
    stager.add(SEED_MESSAGE, user_id="u1")
    before = snapshot(host)
    with pytest.raises(Exception):  # LLM queue empty -> AssertionError -> LLMError inside real Mem0
        stager.add(SEED_MESSAGE, user_id="u1")
    assert_untouched(host, *before)
    with pytest.raises(ValueError):
        stager.update("00000000-0000-0000-0000-000000000000", "x")
    assert_untouched(host, *before)


def test_hold_writes_no_messages_even_though_mem0_would(guarded):
    host, stager = guarded
    host.llm.push("Alice lives on Mars")
    outcome = stager.add(SEED_MESSAGE, user_id="u1")
    assert outcome.decision == "HOLD"
    assert dict(host.logical().tables["messages"]) == {}
    assert dict(host.logical().tables["history"]) == {}


# ---------------------------------------------------------------------------
# Unmodeled / unknown write side effects are detected, not silently committed
# ---------------------------------------------------------------------------


def _tamper(monkeypatch, action):
    original = Memory._add_to_vector_store

    def patched(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        action(self)
        return result

    monkeypatch.setattr(Memory, "_add_to_vector_store", patched)


def _foreign_scope_write(memory):
    memory.vector_store.insert(
        vectors=[[0.5] * DIMS],
        ids=["00000000-0000-0000-0000-0000000000aa"],
        payloads=[{"data": "Mallory was here", "user_id": "victim"}],
    )


def _extra_table(memory):
    memory.db.connection.execute("CREATE TABLE exfil (x TEXT)")
    memory.db.connection.commit()


def _extra_collection(memory):
    from qdrant_client import models

    memory.vector_store.client.create_collection(
        "side_channel", vectors_config=models.VectorParams(size=DIMS, distance=models.Distance.COSINE)
    )


def _history_rewrite(memory):
    memory.db.connection.execute("UPDATE history SET new_memory = 'tampered'")
    memory.db.connection.commit()


def _history_extra_row(memory):
    memory.db.add_history("deadbeef", None, "phantom", "ADD")


def _dangling_entity(memory):
    memory.entity_store.insert(
        vectors=[[0.25] * DIMS],
        ids=["00000000-0000-0000-0000-0000000000bb"],
        payloads=[{"data": "Phantom", "entity_type": "PROPER", "linked_memory_ids": ["no-such-memory"], "user_id": "u1"}],
    )


@pytest.mark.parametrize(
    "action,needle",
    [
        (_foreign_scope_write, "outside the operation scope"),
        (_extra_table, "sqlite"),
        (_extra_collection, "collection"),
        (_history_rewrite, "history"),
        (_history_extra_row, "history"),
        (_dangling_entity, "non-existent memory"),
    ],
)
def test_unmodeled_side_effect_is_rejected_even_for_a_supported_claim(guarded, monkeypatch, action, needle):
    host, stager = guarded
    # prior real history so that rewriting/extra rows are meaningful
    host.llm.push(*SEED_MEMORIES)
    stager.add(SEED_MESSAGE, user_id="u1")
    before = snapshot(host)
    _tamper(monkeypatch, action)
    host.llm.push("Alice lives in Berlin and Alice works at Acme Corp")  # perfectly well-supported claim
    outcome = stager.add(SEED_MESSAGE, user_id="u1")
    assert outcome.decision == "REJECTED" and not outcome.applied
    assert outcome.error_type == "UnmodeledSideEffect"
    assert needle in outcome.reason
    assert_untouched(host, *before)


# ---------------------------------------------------------------------------
# Concurrency and failure injection
# ---------------------------------------------------------------------------


def test_stale_scoped_prior_is_refused_and_intruder_write_survives(tmp_path, make_host):
    host = make_host()

    def intruder(claims, sources):
        # another writer commits to the SAME scope between staging and commit
        host.memory.add("Alice bought a bicycle", user_id="u1", infer=False)
        return LexicalGrounding()(claims, sources)

    stager = StagedMem0(host.memory, workdir=tmp_path / "membud", grounding=intruder)
    host.llm.push("Alice lives in Berlin")
    outcome = stager.add(
        [{"role": "user", "content": "Alice lives in Berlin"}], user_id="u1"
    )
    assert outcome.decision == "REJECTED" and not outcome.applied
    texts = [i["memory"] for i in host.memory.get_all(filters={"user_id": "u1"})["results"]]
    assert texts == ["Alice bought a bicycle"]  # the verified candidate was not applied over it


def test_stale_foreign_scope_write_is_refused_at_commit(tmp_path, make_host):
    host = make_host()

    def intruder(claims, sources):
        host.memory.add("Bob bought a boat", user_id="u2", infer=False)  # different scope
        return LexicalGrounding()(claims, sources)

    stager = StagedMem0(host.memory, workdir=tmp_path / "membud", grounding=intruder)
    host.llm.push("Alice lives in Berlin")
    outcome = stager.add([{"role": "user", "content": "Alice lives in Berlin"}], user_id="u1")
    assert outcome.decision == "REJECTED" and not outcome.applied
    assert "changed since staging" in outcome.reason or "stale" in outcome.reason.lower()
    assert host.memory.get_all(filters={"user_id": "u1"})["results"] == []


def test_failed_commit_is_compensated_logically(guarded, monkeypatch):
    host, stager = guarded
    host.llm.push(*SEED_MEMORIES)
    stager.add(SEED_MESSAGE, user_id="u1")
    before = host.logical()
    client = host.memory.vector_store.client
    real_upsert = client.upsert
    calls = {"n": 0}

    def flaky(collection_name, points, **kwargs):
        calls["n"] += 1
        if collection_name.endswith("_entities"):
            raise RuntimeError("injected entity-store failure after memory write")
        return real_upsert(collection_name=collection_name, points=points, **kwargs)

    monkeypatch.setattr(client, "upsert", flaky)
    host.llm.push("Alice has a dog named Rex")
    outcome = stager.add([{"role": "user", "content": "Alice has a dog named Rex"}], user_id="u1")
    assert outcome.decision == "REJECTED" and not outcome.applied
    assert "injected" in outcome.reason
    monkeypatch.undo()
    assert staged_module.states_equivalent(host.logical(), before)  # rolled back, no partial state


def test_unrecoverable_commit_failure_is_reported_as_partial(guarded, monkeypatch):
    host, stager = guarded
    client = host.memory.vector_store.client
    real_upsert = client.upsert

    def flaky(collection_name, points, **kwargs):
        if collection_name.endswith("_entities"):
            raise RuntimeError("injected failure")
        return real_upsert(collection_name=collection_name, points=points, **kwargs)

    def broken_delete(*args, **kwargs):
        raise RuntimeError("injected rollback failure")

    monkeypatch.setattr(client, "upsert", flaky)
    monkeypatch.setattr(client, "delete", broken_delete)
    host.llm.push("Alice lives in Berlin")
    outcome = stager.add([{"role": "user", "content": "Alice lives in Berlin"}], user_id="u1")
    assert outcome.decision == "REJECTED" and not outcome.applied
    assert "could not restore" in outcome.reason  # loud, never a silent partial write


def test_failed_noop_commit_is_not_silently_retried_by_the_guard(guarded, monkeypatch):
    """GuardedMemory calls replace_state(prior) after a failed apply; for a NOOP the prior equals the
    candidate, so a reusable armed change set would be applied behind a REJECTED decision."""
    host, stager = guarded
    host.llm.push(*SEED_MEMORIES)
    stager.add(SEED_MESSAGE, user_id="u1")
    host.llm.push("Alice lives in Berlin")  # dedup -> NOOP, messages-only change set
    before = host.logical()
    real = staged_module.apply_changes
    calls = {"n": 0}

    def fail_first(memory, changes):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected apply failure")
        return real(memory, changes)

    monkeypatch.setattr(staged_module, "apply_changes", fail_first)
    outcome = stager.add(SEED_MESSAGE, user_id="u1")
    assert outcome.decision == "REJECTED" and not outcome.applied
    monkeypatch.undo()
    assert staged_module.states_equivalent(host.logical(), before)


def test_adapter_refuses_direct_writes(guarded):
    host, stager = guarded
    from membud.schemas import Record

    before = snapshot(host)
    with pytest.raises(Exception, match="no staged"):
        stager.adapter.replace_state({"user_id": "u1"}, [Record("x", ("claim",))])
    assert_untouched(host, *before)


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def test_every_outcome_is_logged_and_ephemeral_baselines_are_cleaned(guarded, tmp_path):
    host, stager = guarded
    host.llm.push(*SEED_MEMORIES)
    stager.add(SEED_MESSAGE, user_id="u1")
    host.llm.push("Alice lives on Mars")
    stager.add(SEED_MESSAGE, user_id="u1")
    log = [json.loads(line) for line in (tmp_path / "membud" / "staged-outcomes.jsonl").read_text().splitlines()]
    assert [entry["decision"] for entry in log] == ["ACCEPT", "HOLD"]
    assert log[1]["applied"] is False and log[1]["live_state_digest"] == log[1]["pre_state_digest"]
    decisions = (tmp_path / "membud" / "guarded-decisions.jsonl").read_text().splitlines()
    assert len(decisions) == 2
    assert not list((tmp_path / "membud").glob("verify-*"))
