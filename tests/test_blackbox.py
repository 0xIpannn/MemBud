"""Behaviour of the black-box adapter on a fake file-backed memory system (stdlib only)."""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from membud.adapters import (
    AdapterContractError,
    BlackBoxOps,
    BlackBoxStaged,
)
from blackbox_fakes import FakeStore, make_ops

SCOPE = {"user_id": "u1"}
MSG = [{"role": "user", "content": "I live in Bandung. I work at Tokopedia."}]


def file_digest(store: FakeStore) -> str:
    return hashlib.sha256(store.path.read_bytes()).hexdigest()


def build(tmp_path, **kw):
    live = FakeStore(tmp_path / "live.sqlite")
    counter = {"n": 0}
    twins: list[FakeStore] = []

    def factory():
        counter["n"] += 1
        twin = FakeStore(tmp_path / f"twin{counter['n']}.sqlite")
        twins.append(twin)
        if kw.get("twin_hook"):
            twin.native_hook = kw["twin_hook"]
        return make_ops(twin, **kw.get("ops", {}))

    guard = BlackBoxStaged(make_ops(live, **kw.get("ops", {})), factory, workdir=tmp_path / "wd")
    return live, guard, twins


def test_accept_persists_exactly_the_staged_state(tmp_path):
    live, guard, _ = build(tmp_path)
    out = guard.add(MSG, scope=SCOPE)
    assert out.decision == "ACCEPT" and out.applied
    assert [r["text"] for r in live.list(SCOPE)] == ["I live in Bandung", "I work at Tokopedia"]
    assert set(out.id_map) == {c.live_id and f"staged:{c.live_id}" for c in out.changes}
    # the live backend ran its own hidden bookkeeping during the replay
    assert len(live.dump_all()["hidden_index"]) == 2


def test_hold_leaves_live_file_byte_identical(tmp_path):
    live, guard, _ = build(tmp_path)
    guard.add(MSG, scope=SCOPE)
    before = file_digest(live)
    # the native write extracts a claim that the trusted user message does not support
    out = guard.add("Hello there.", scope=SCOPE, evidence=[])
    assert out.decision == "HOLD" and not out.applied
    assert file_digest(live) == before
    assert out.changes and out.changes[0].kind == "add"  # side effects were enumerated before suppression


def test_stale_state_is_refused(tmp_path):
    live, guard, _ = build(tmp_path)
    guard.add(MSG, scope=SCOPE)
    orig = guard.adapter.replace_state

    def racing(scope, records):
        live.add(SCOPE, "Intruder wrote this.", raw=True)  # unguarded writer between stage and commit
        return orig(scope, records)

    guard.adapter.replace_state = racing
    out = guard.add("I like tea.", scope=SCOPE)
    assert not out.applied and out.decision == "REJECTED"
    assert [r["text"] for r in live.list(SCOPE)][-1] == "Intruder wrote this."
    assert "I like tea" not in [r["text"] for r in live.list(SCOPE)]


def test_foreign_scope_write_is_rejected(tmp_path):
    def hook(twin):
        twin.add({"user_id": "other"}, "planted", raw=True)

    live, guard, _ = build(tmp_path, twin_hook=hook)
    before = file_digest(live)
    out = guard.add(MSG, scope=SCOPE)
    assert out.decision == "REJECTED" and out.error_type == "UnmodeledChange"
    assert file_digest(live) == before


def test_schema_change_is_rejected(tmp_path):
    def hook(twin):
        twin.db.execute("CREATE TABLE side(x)")

    live, guard, _ = build(tmp_path, twin_hook=hook)
    out = guard.add(MSG, scope=SCOPE)
    assert out.error_type == "UnmodeledChange" and "schema" in out.reason


def test_history_rewrite_is_rejected(tmp_path):
    def hook(twin):
        twin.db.execute("UPDATE history SET new='forged'")

    live, guard, _ = build(tmp_path, twin_hook=hook)
    guard.add(MSG, scope=SCOPE)  # nothing to rewrite yet -> accepted
    out = guard.add("I like tea.", scope=SCOPE)
    assert out.error_type == "UnmodeledChange" and "history" in out.reason


def test_invisible_hidden_write_is_not_detected(tmp_path):
    """Documented limit: a hidden-surface write the public API cannot show is neither seen nor replayed."""

    def hook(twin):
        twin.db.execute("INSERT INTO hidden_index VALUES('ghost','secret')")

    live, guard, twins = build(tmp_path, twin_hook=hook)
    out = guard.add(MSG, scope=SCOPE)
    assert out.decision == "ACCEPT"
    assert ("ghost", "secret") not in {tuple(r) for r in live.dump_all()["hidden_index"]}


def test_without_history_callable_history_rewrite_is_invisible(tmp_path):
    def hook(twin):
        twin.db.execute("UPDATE history SET new='forged'")

    live, guard, _ = build(tmp_path, twin_hook=hook, ops={"with_history": False})
    guard.add(MSG, scope=SCOPE)
    assert guard.add("I like tea.", scope=SCOPE).decision == "ACCEPT"


def test_commit_failure_is_compensated(tmp_path):
    live, guard, _ = build(tmp_path)
    guard.add(MSG, scope=SCOPE)
    before = sorted(r["text"] for r in live.list(None))
    live.fail_at = {live.writes + 2}  # first replay write succeeds, the second fails once
    out = guard.add("I like tea. I own a cat.", scope=SCOPE)
    assert out.decision == "REJECTED" and not out.applied
    assert sorted(r["text"] for r in live.list(None)) == before


def test_failed_compensation_is_loud(tmp_path):
    live, guard, _ = build(tmp_path)
    live.fail_from = live.writes + 2  # replay's 2nd write and every compensating write fail
    out = guard.add("I like tea. I own a cat.", scope=SCOPE)
    assert out.decision == "REJECTED" and not out.applied
    assert "could not" in out.reason.lower()


def test_direct_replace_state_is_refused(tmp_path):
    live, guard, _ = build(tmp_path)
    with pytest.raises(AdapterContractError):
        guard.adapter.replace_state(SCOPE, [])


def test_backend_error_during_native_write_leaves_live_untouched(tmp_path):
    def hook(twin):
        raise RuntimeError("llm down")

    live, guard, _ = build(tmp_path, twin_hook=hook)
    before = file_digest(live)
    with pytest.raises(RuntimeError, match="llm down"):
        guard.add(MSG, scope=SCOPE)
    assert file_digest(live) == before


def test_update_and_delete_roundtrip(tmp_path):
    live, guard, _ = build(tmp_path)
    guard.add(MSG, scope=SCOPE)
    mid = live.list(SCOPE)[0]["id"]
    upd = guard.update(mid, "I live in Jakarta", source_text="I live in Jakarta.")
    assert upd.decision == "ACCEPT" and live.list(SCOPE)[0]["text"] == "I live in Jakarta"
    held = guard.update(mid, "I live on Mars", source_text="I like tea.")
    assert held.decision == "HOLD" and live.list(SCOPE)[0]["text"] == "I live in Jakarta"
    no_auth = guard.delete(mid, authorization=None, evidence=[])
    assert no_auth.decision == "REJECTED" and len(live.list(SCOPE)) == 2
    ok = guard.delete(
        mid,
        authorization={"source": "chat:1", "evidence_id": "e1", "rationale": "user asked to forget"},
        evidence=[{"id": "e1", "supports": [], "source": "chat:1", "text": "forget where I live"}],
    )
    assert ok.decision == "ACCEPT" and len(live.list(SCOPE)) == 1
