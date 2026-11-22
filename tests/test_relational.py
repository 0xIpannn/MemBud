"""Relational format, verifiers, generated suite, and frozen package."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from membud.relational import RelationalTransition, parse_relational_text, relational_text
from membud.relational_cases import generate_suite
from membud.relational_verify import (
    STRATEGIES,
    build_relational_graph,
    run_strategy,
    verify_relational_graph,
    verify_relational_text,
)

ROOT = Path(__file__).resolve().parents[1]
RELATIONS = {"lives_in": {"cardinality": "one"}, "likes": {"cardinality": "many"}}


def case(**overrides) -> dict:
    base = {
        "case_id": "T1",
        "relations": RELATIONS,
        "sources": [
            {"id": "u1", "kind": "user_turn", "trusted": True, "time": 1},
            {"id": "u5", "kind": "user_turn", "trusted": True, "time": 5},
            {"id": "a3", "kind": "assistant_turn", "trusted": False, "time": 3},
        ],
        "evidence": [
            {"id": "e1", "source": "u1", "supports": ["ifan|lives_in|Bandung"]},
            {"id": "e5", "source": "u5", "supports": ["ifan|lives_in|Jakarta"]},
        ],
        "before": [{"id": "r1", "fact": "ifan|lives_in|Bandung", "evidence": ["e1"]}],
        "operations": [{
            "type": "SUPERSEDE", "target_id": "r1",
            "record": {"id": "n1", "fact": "ifan|lives_in|Jakarta", "evidence": ["e5"]},
        }],
    }
    base.update(overrides)
    return base


def decide_all(data: dict) -> dict[str, str]:
    t = RelationalTransition.from_dict(data)
    return {name: run_strategy(name, t).decision for name in STRATEGIES}


def fail_codes(result) -> set[str]:
    return {c.reason_code for c in result.checks if not c.passed}


# --------------------------------------------------------------------------- #
# Format
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["evidence"].append({"id": "e9", "derived_from": ["missing"], "supports": []}), "unknown evidence"),
    (lambda d: d["evidence"].append({"id": "e9", "source": "u1", "derived_from": ["e1"], "supports": []}), "exactly one"),
    (lambda d: d["before"].append({"id": "r2", "fact": "ifan|age|30", "evidence": ["e1"]}), "undeclared relation"),
    (lambda d: d["relations"].update({"same_as": {"cardinality": "many"}}), "built in"),
    (lambda d: d["operations"][0].update({"target_id": "nope"}), "not a prior record"),
    (lambda d: d["operations"].append({"type": "WRITE", "target_id": "r1", "record": {"id": "n2", "fact": "ifan|likes|jazz", "evidence": ["e1"]}}), "WRITE forbids"),
    (lambda d: d["before"][0].update({"fact": "ifan|lives in|Bandung"}), "token"),
    (lambda d: d.update({"operations": []}), "at least one operation"),
])
def test_structural_validation_rejects_malformed_input(mutate, message):
    data = json.loads(json.dumps(case()))
    mutate(data)
    with pytest.raises(ValueError, match=message):
        RelationalTransition.from_dict(data)


def test_text_serialization_is_lossless_for_the_whole_suite():
    for t in generate_suite(per_cell=2):
        assert parse_relational_text(relational_text(t)).to_dict() == t.to_dict()


# --------------------------------------------------------------------------- #
# Hand-written mechanism cases
# --------------------------------------------------------------------------- #

def test_valid_supersession_is_accepted_by_every_strategy():
    assert set(decide_all(case()).values()) == {"ACCEPT"}


def test_two_hop_untrusted_provenance_needs_reach():
    data = case()
    data["evidence"] += [
        {"id": "a", "source": "a3", "supports": ["ifan|likes|jazz"]},
        {"id": "s1", "derived_from": ["a"], "supports": ["ifan|likes|jazz"]},
        {"id": "s2", "derived_from": ["s1"], "supports": ["ifan|likes|jazz"]},
    ]
    data["operations"] = [{"type": "WRITE", "record": {"id": "n2", "fact": "ifan|likes|jazz", "evidence": ["s2"]}}]
    got = decide_all(data)
    assert got["graph"] == got["text_closure"] == got["text_k2_optimistic"] == "HOLD"
    assert got["text_k1_optimistic"] == "ACCEPT"  # false accept: root is 2 hops down
    assert got["text_k1_pessimistic"] == "HOLD"
    t = RelationalTransition.from_dict(data)
    assert fail_codes(verify_relational_graph(t)) == {"UNGROUNDED_CLAIM"}


def test_unsupported_claim_is_caught_without_any_hops():
    data = case()
    data["operations"][0]["record"]["evidence"] = ["e1"]  # cites evidence for a different fact
    got = decide_all(data)
    assert set(got.values()) == {"HOLD"}


def test_derivation_cycle_is_never_grounded():
    data = case()
    data["evidence"] += [
        {"id": "c1", "derived_from": ["c2"], "supports": ["ifan|likes|jazz"]},
        {"id": "c2", "derived_from": ["c1", "e1"], "supports": ["ifan|likes|jazz"]},
        {"id": "top", "derived_from": ["c1"], "supports": ["ifan|likes|jazz"]},
    ]
    data["operations"] = [{"type": "WRITE", "record": {"id": "n2", "fact": "ifan|likes|jazz", "evidence": ["top"]}}]
    t = RelationalTransition.from_dict(data)
    assert verify_relational_graph(t).decision == "HOLD"
    assert verify_relational_text(t, max_hops=None).decision == "HOLD"


def test_alias_contradiction_across_records():
    data = case()
    data["sources"].append({"id": "u2", "kind": "user_turn", "trusted": True, "time": 2})
    data["evidence"] += [
        {"id": "ea", "source": "u2", "supports": ["ifan|same_as|ifan.n1", "ifan.n1|same_as|ifan.n2"]},
        {"id": "ew", "source": "u5", "supports": ["ifan.n2|lives_in|Medan"]},
    ]
    data["before"] += [
        {"id": "a1", "fact": "ifan|same_as|ifan.n1", "evidence": ["ea"]},
        {"id": "a2", "fact": "ifan.n1|same_as|ifan.n2", "evidence": ["ea"]},
    ]
    data["operations"] = [{"type": "WRITE", "record": {"id": "n2", "fact": "ifan.n2|lives_in|Medan", "evidence": ["ew"]}}]
    got = decide_all(data)
    assert got["graph"] == got["text_closure"] == got["text_k2_optimistic"] == "HOLD"
    assert got["text_k1_optimistic"] == "ACCEPT"
    assert fail_codes(verify_relational_graph(RelationalTransition.from_dict(data))) == {"FUNCTIONAL_CONFLICT"}


def test_many_valued_relation_never_conflicts():
    data = case()
    data["evidence"] += [{"id": "l", "source": "u5", "supports": ["ifan|likes|chess"]}]
    data["before"].append({"id": "r2", "fact": "ifan|likes|jazz", "evidence": ["e1"]})
    data["operations"] = [{"type": "WRITE", "record": {"id": "n2", "fact": "ifan|likes|chess", "evidence": ["l"]}}]
    assert set(decide_all(data).values()) == {"ACCEPT"}


def test_stale_target_and_temporal_order():
    data = case()
    data["before"].append({"id": "r2", "fact": "ifan|lives_in|Jakarta", "evidence": ["e5"], "supersedes": "r1"})
    data["evidence"].append({"id": "e9", "source": "u5", "supports": ["ifan|lives_in|Medan"]})
    data["operations"] = [{"type": "SUPERSEDE", "target_id": "r1",
                           "record": {"id": "n1", "fact": "ifan|lives_in|Medan", "evidence": ["e9"]}}]
    t = RelationalTransition.from_dict(data)
    assert "STALE_TARGET" in fail_codes(verify_relational_graph(t))
    assert "STALE_TARGET" in fail_codes(verify_relational_text(t, max_hops=1))

    stale = case()
    stale["evidence"][1]["source"] = "u1"  # replacement observed no later than the target
    t = RelationalTransition.from_dict(stale)
    assert fail_codes(verify_relational_graph(t)) == {"TEMPORAL_ORDER_VIOLATION"}
    assert fail_codes(verify_relational_text(t, max_hops=None)) == {"TEMPORAL_ORDER_VIOLATION"}


def test_superseding_another_persons_record_is_a_slot_mismatch():
    data = case()
    data["evidence"].append({"id": "eb", "source": "u1", "supports": ["budi|lives_in|Bandung"]})
    data["before"] = [{"id": "r1", "fact": "budi|lives_in|Bandung", "evidence": ["eb"]}]
    t = RelationalTransition.from_dict(data)
    assert fail_codes(verify_relational_graph(t)) == {"SLOT_MISMATCH"}
    assert fail_codes(verify_relational_text(t, max_hops=None)) == {"SLOT_MISMATCH"}


def test_pessimistic_marks_unreachable_checks_uncertain():
    data = case()
    data["evidence"] += [
        {"id": "s1", "derived_from": ["e5"], "supports": ["ifan|lives_in|Jakarta"]},
        {"id": "s2", "derived_from": ["s1"], "supports": ["ifan|lives_in|Jakarta"]},
    ]
    data["operations"][0]["record"]["evidence"] = ["s2"]
    t = RelationalTransition.from_dict(data)
    pess = verify_relational_text(t, max_hops=1, unknown="pessimistic")
    assert pess.decision == "HOLD"
    assert {c.status for c in pess.checks} >= {"UNCERTAIN"}
    assert verify_relational_text(t, max_hops=1, unknown="optimistic").decision == "ACCEPT"
    assert verify_relational_graph(t).decision == "ACCEPT"


def test_text_verifier_accepts_serialized_text_input():
    t = RelationalTransition.from_dict(case())
    assert verify_relational_text(relational_text(t), max_hops=None).decision == "ACCEPT"
    with pytest.raises(ValueError):
        verify_relational_text(t, max_hops=0)
    with pytest.raises(ValueError):
        verify_relational_text(t, unknown="maybe")


def test_graph_contains_typed_relational_edges():
    g = build_relational_graph(RelationalTransition.from_dict(case()))
    kinds = {e["type"] for e in g["edges"]}
    assert {"OBSERVED_IN", "CITES", "SUPPORTS", "SUBJECT", "OBJECT", "OF_RELATION", "SUPERSEDES"} <= kinds


# --------------------------------------------------------------------------- #
# Generated suite properties
# --------------------------------------------------------------------------- #

SUITE = generate_suite()


def test_suite_is_balanced_minimal_pairs_and_deterministic():
    assert len(SUITE) == 480
    assert sum(c.metadata["valid"] for c in SUITE) == 240
    pairs: dict[str, list] = {}
    for c in SUITE:
        pairs.setdefault(c.metadata["pair_id"], []).append(c.metadata["valid"])
    assert all(sorted(v) == [False, True] for v in pairs.values())
    assert [c.to_dict() for c in generate_suite()[:20]] == [c.to_dict() for c in SUITE[:20]]


def test_graph_and_closure_text_agree_with_labels_everywhere():
    for c in SUITE:
        g = verify_relational_graph(c)
        t = verify_relational_text(c, max_hops=None)
        assert g.decision == t.decision == c.metadata["expected_decision"], c.case_id
        assert fail_codes(g) == fail_codes(t)
        if not c.metadata["valid"]:
            assert c.metadata["fault_code"] in fail_codes(g)


@pytest.mark.parametrize("k", [1, 2, 3])
def test_bounded_text_fails_exactly_beyond_its_reach(k):
    for c in SUITE:
        hops, valid = c.metadata["required_hops"], c.metadata["valid"]
        optimistic = verify_relational_text(c, max_hops=k, unknown="optimistic").decision
        pessimistic = verify_relational_text(c, max_hops=k, unknown="pessimistic").decision
        within = hops <= k
        assert optimistic == ("ACCEPT" if valid or not within else "HOLD"), c.case_id
        assert pessimistic == ("HOLD" if not valid or not within else "ACCEPT"), c.case_id


def test_frozen_package_matches_generator():
    spec = importlib.util.spec_from_file_location("run_relational_eval", ROOT / "scripts/run_relational_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest, cases, labels = module.load_frozen()
    assert manifest["case_count"] == len(cases) == len(labels) == 480
