"""What the black-box path loses on Graphiti: the guarded live graph keeps the facts but not the provenance.

Needs Neo4j and graphiti-core (skipped otherwise); see adapter_harness_graphiti.py.
"""
import pytest

from adapter_harness import HARNESSES, SCOPE, SEED, say

pytest.importorskip("graphiti_core")


@pytest.fixture
def h(tmp_path):
    harness = HARNESSES["graphiti-blackbox"](tmp_path)
    yield harness
    harness.close()


def census(h, suffix):
    nodes = h.q("MATCH (n) WHERE n.group_id STARTS WITH $p AND n.group_id ENDS WITH $s RETURN labels(n) AS l, count(*) AS c",
                p=h.prefix, s=suffix)
    rels = h.q("MATCH (a)-[r]->() WHERE a.group_id STARTS WITH $p AND a.group_id ENDS WITH $s RETURN type(r) AS t, count(*) AS c",
               p=h.prefix, s=suffix)
    return ({"/".join(sorted(r["l"])): r["c"] for r in nodes}, {r["t"]: r["c"] for r in rels})


def test_guarded_accept_keeps_the_facts_but_drops_episodes_and_mentions(h):
    h.queue[:] = list(SEED)
    h._native_add(h.g, f"{h.prefix}u1-native", say(*SEED))
    h.queue.clear()
    out = h.add(list(SEED))
    assert out.decision == "ACCEPT" and out.applied, out

    native_nodes, native_rels = census(h, "-native")
    live_nodes, live_rels = census(h, "u1")
    # identical facts and entity nodes ...
    assert native_rels["RELATES_TO"] == live_rels["RELATES_TO"] == len(SEED)
    assert native_nodes["Entity"] == live_nodes["Entity"]
    assert sorted(h.texts()) == sorted(SEED)
    # ... but the episode (provenance) and its MENTIONS edges exist only on the unguarded path
    assert native_nodes.get("Episodic") == 1 and native_rels.get("MENTIONS") == 3
    assert "Episodic" not in live_nodes and "MENTIONS" not in live_rels
