"""Conversational QA utility benchmark and paired statistics."""
from __future__ import annotations

from membud.paired_stats import mcnemar_exact, paired_bootstrap, wilson
from membud.qa_benchmark import (
    POLICIES,
    BenchmarkConfig,
    MemoryStore,
    conversation_metrics,
    run_benchmark,
    run_conversation,
    simulate_conversation,
)
from membud.relational import Fact, RelEvidence, RelOperation, RelRecord, Source
from membud.relational_cases import RELATIONS

SMALL = BenchmarkConfig(conversations=12, sessions=4)


def _commit(store: MemoryStore, rid: str, fact: Fact, time: int, supersedes: str | None = None) -> None:
    src = Source(f"s{rid}", "user_turn", True, time)
    ev = RelEvidence(f"e{rid}", (fact,), src.id, ())
    op = RelOperation("SUPERSEDE" if supersedes else "WRITE", RelRecord(rid, fact, (ev.id,), supersedes), supersedes)
    store.commit(store.transition(f"c{rid}", [src], [ev], [op]))


def test_reader_resolves_aliases_and_prefers_latest_commit():
    store = MemoryStore(dict(RELATIONS))
    _commit(store, "r1", Fact("ifan", "same_as", "ifan.n1"), 1)
    _commit(store, "r2", Fact("ifan", "lives_in", "Bandung"), 2)
    assert store.answer("ifan.n1", "lives_in") == "Bandung"
    _commit(store, "r3", Fact("ifan.n1", "lives_in", "Medan"), 3)  # unguarded-style conflict
    assert store.answer("ifan", "lives_in") == "Medan"
    _commit(store, "r4", Fact("ifan", "lives_in", "Jakarta"), 4, supersedes="r3")
    assert store.answer("ifan", "lives_in") == "Jakarta"
    assert store.answer("ifan", "likes") == set()
    assert store.answer("budi", "works_at") is None


def test_simulation_is_deterministic():
    def strip(row):  # latencies are wall-clock and legitimately differ
        return {p: {k: v for k, v in d.items() if k != "latency_ms"} for p, d in row["policies"].items()}

    assert strip(run_conversation(3, SMALL)) == strip(run_conversation(3, SMALL))
    w1, s1, t1 = simulate_conversation(5, SMALL)
    w2, s2, t2 = simulate_conversation(5, SMALL)
    assert [p.fact for ps in s1 for p in ps] == [p.fact for ps in s2 for p in ps]
    assert t1 == t2


def test_policy_invariants_hold():
    rows = run_benchmark(SMALL)
    for row in rows:
        unguarded = row["policies"]["unguarded"]["commits"]
        assert unguarded["held_valid"] == unguarded["held_invalid"] == 0
        oracle = row["policies"]["oracle_gate"]["commits"]
        assert oracle["accepted_invalid"] == oracle["held_valid"] == 0
        # Graph and closure text implement the same specification.
        assert row["policies"]["graph"]["sessions"] == row["policies"]["text_closure"]["sessions"]
        assert row["policies"]["graph"]["commits"] == row["policies"]["text_closure"]["commits"]
    for policy in POLICIES:
        metrics = [conversation_metrics(r, policy) for r in rows]
        for m in metrics:
            assert 0.0 <= m["overall_em"] <= 1.0
            assert abs(m["user_em"] + m["user_stale"] + m["user_wrong"] + m["user_missing"] - 1.0) < 1e-9


def test_guarding_reduces_wrong_answers_and_protects_the_friend():
    rows = run_benchmark(BenchmarkConfig(conversations=40, fault_rate=0.5))
    mean = lambda p, k: sum(conversation_metrics(r, p)[k] for r in rows) / len(rows)  # noqa: E731
    assert mean("graph", "user_wrong") < mean("unguarded", "user_wrong")
    assert mean("graph", "friend_em") > mean("unguarded", "friend_em")
    assert mean("graph", "overall_em") > mean("text_k1_optimistic", "overall_em")


def test_paired_stats():
    assert wilson(0, 0)["rate"] is None
    w = wilson(5, 10)
    assert w["low"] < 0.5 < w["high"]
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(10, 0) < 0.01
    assert mcnemar_exact(3, 3) == 1.0
    boot = paired_bootstrap([1.0, 1.0, 0.0, 1.0], [0.0, 1.0, 0.0, 0.0], seed=1, resamples=500)
    assert boot["mean_diff"] == 0.5
    assert boot["ci95_low"] <= 0.5 <= boot["ci95_high"]
    assert boot == paired_bootstrap([1.0, 1.0, 0.0, 1.0], [0.0, 1.0, 0.0, 0.0], seed=1, resamples=500)
