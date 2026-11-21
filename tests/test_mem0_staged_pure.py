"""Mem0-free tests for the pure parts of the staged write wrapper.

The end-to-end tests against a real ``mem0ai==2.2.1`` live in
``tests/test_mem0_staged.py`` and need the pinned environment
(``requirements-mem0.txt``); these run everywhere and import no Mem0.
"""
import json

from membud.adapters.mem0_staged import (
    HostState,
    LexicalGrounding,
    changeset_digest,
    diff_states,
    states_equivalent,
)


def _state(points=None, rows=None):
    return HostState(
        collections={"mem": dict(points or {})},
        params={"mem": "{}"},
        tables={"history": dict(rows or {})},
        schema={"history": "CREATE TABLE history (id TEXT)"},
    )


def _point(vector, **payload):
    return (json.dumps(vector), json.dumps(payload, sort_keys=True))


def test_diff_classifies_inserts_updates_deletes_in_every_store():
    before = _state(
        {"a": _point([1.0, 0.0], data="x"), "b": _point([0.0, 1.0], data="y")},
        {"1": json.dumps({"rowid": 1})},
    )
    after = _state(
        {"a": _point([1.0, 0.0], data="x2"), "c": _point([0.5, 0.5], data="z")},
        {"1": json.dumps({"rowid": 1}), "2": json.dumps({"rowid": 2})},
    )
    changes, problems = diff_states(before, after)
    assert problems == ()
    assert sorted((c.store, c.container, c.key, c.kind) for c in changes) == [
        ("qdrant", "mem", "a", "update"),
        ("qdrant", "mem", "b", "delete"),
        ("qdrant", "mem", "c", "insert"),
        ("sqlite", "history", "2", "insert"),
    ]


def test_vectors_compare_with_tolerance_but_payload_is_exact():
    base = _state({"a": _point([0.1, 0.2], data="x")})
    jitter = _state({"a": _point([0.1 + 2e-7, 0.2 - 2e-7], data="x")})
    moved = _state({"a": _point([0.1 + 1e-3, 0.2], data="x")})
    edited = _state({"a": _point([0.1, 0.2], data="X")})
    assert states_equivalent(base, jitter)
    assert not states_equivalent(base, moved)
    assert not states_equivalent(base, edited)


def test_structural_changes_are_reported_as_problems():
    before = _state()
    after = HostState(
        collections={"mem": {}, "extra": {}},
        params={"mem": "{}", "extra": "{}"},
        tables={"history": {}, "exfil": {}},
        schema={"history": "CREATE TABLE history (id TEXT)", "exfil": "CREATE TABLE exfil (x)"},
    )
    _, problems = diff_states(before, after)
    assert any("collection set" in p for p in problems)
    assert any("DDL" in p for p in problems)


def test_changeset_digest_is_order_sensitive_and_deterministic():
    before = _state({}, {})
    after = _state({"a": _point([1.0], data="x")}, {"1": json.dumps({"rowid": 1})})
    changes, _ = diff_states(before, after)
    assert changeset_digest(changes) == changeset_digest(tuple(changes))
    assert changeset_digest(changes) != changeset_digest(tuple(reversed(changes)))


def test_lexical_grounding_never_invents_support():
    ground = LexicalGrounding()
    sources = [("m0", "I am Alice, I live in Berlin and I work at Acme Corp.")]
    supported = ground(["Alice lives in Berlin", "Alice works at Acme Corp"], sources)
    assert [e["supports"] for e in supported] == [["Alice lives in Berlin", "Alice works at Acme Corp"]]
    assert ground(["Alice lives on Mars"], sources) == []
    assert ground([], sources) == []
    assert ground(["Alice lives in Berlin"], []) == []
