import json
from dataclasses import replace

import pytest

from membud.core import (
    Transition,
    build_graph,
    commit,
    run_case,
    textual_representation,
    to_mermaid,
    transition_dict,
    verify,
)
from membud.demo import cases


EXPECTED_RESULTS = {
    "valid": ("ACCEPT", set()),
    "omission": ("HOLD", {"coverage"}),
    "corruption": ("HOLD", {"coverage", "preservation", "faithfulness"}),
    "unsupported": ("HOLD", {"faithfulness"}),
    "valid-noop": ("ACCEPT", set()),
    "preserve-added-claim": ("ACCEPT", set()),
    "remove-record": ("ACCEPT", set()),
    "implicit-removal": ("HOLD", {"coverage", "preservation"}),
    "supported-revision": ("ACCEPT", set()),
    "explicit-supersession": ("ACCEPT", set()),
    "unsupported-plus-valid": ("HOLD", {"faithfulness"}),
}


def case(case_id):
    return next(raw for raw in cases() if raw["case_id"] == case_id)


@pytest.mark.parametrize("raw", cases())
def test_demo_cases_match_annotations_in_both_paths(raw):
    transition = Transition.from_dict(raw)
    graph_result = verify(transition, "graph")
    text_result = verify(transition, "text")
    assert graph_result.to_dict() == text_result.to_dict()

    expected_decision, expected_failures = EXPECTED_RESULTS[raw["case_id"]]
    assert graph_result.decision == expected_decision
    assert {check.name for check in graph_result.checks if not check.passed} == expected_failures

    serialized = textual_representation(transition)
    restored = Transition.from_dict(json.loads(serialized))
    assert transition_dict(restored) == transition_dict(transition)


def test_case_annotations_cover_exactly_the_demo_cases():
    assert set(EXPECTED_RESULTS) == {raw["case_id"] for raw in cases()}


@pytest.mark.parametrize("raw", cases())
def test_fixture_graphs_are_connected_and_have_no_dangling_edges(raw):
    graph = build_graph(Transition.from_dict(raw))
    node_ids = {node["id"] for node in graph["nodes"]}
    assert len(node_ids) == len(graph["nodes"])

    adjacency = {node_id: set() for node_id in node_ids}
    for edge in graph["edges"]:
        assert edge["from"] in node_ids
        assert edge["to"] in node_ids
        adjacency[edge["from"]].add(edge["to"])
        adjacency[edge["to"]].add(edge["from"])

    root = next(node["id"] for node in graph["nodes"] if node["type"] == "transition")
    reachable, pending = set(), [root]
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        pending.extend(adjacency[node_id] - reachable)
    assert reachable == node_ids
    assert to_mermaid(Transition.from_dict(raw)).startswith("flowchart LR\n")


@pytest.mark.parametrize("raw", cases())
def test_only_accepted_updates_commit(raw):
    transition = Transition.from_dict(raw)
    store = {record.id: record for record in transition.before}
    verification = verify(transition)
    result = commit(store, transition, verification)
    if verification.decision == "ACCEPT":
        assert result == {record.id: record for record in transition.candidate}
    else:
        assert result == store
    assert result is not store


def test_guarded_json_store_and_append_only_log(tmp_path):
    t = Transition.from_dict(cases()[2])
    store, log = tmp_path / "store.json", tmp_path / "audit.jsonl"
    store.write_text(json.dumps(cases()[2]["before"]))
    result = run_case(t, store, log)
    assert result.decision == "HOLD"
    assert json.loads(store.read_text()) == cases()[2]["before"]
    assert len(log.read_text().splitlines()) == 1


def test_validation_rejects_duplicate_record_ids():
    raw = cases()[0]
    raw["candidate"] = [raw["candidate"][0], raw["candidate"][0]]
    with pytest.raises(ValueError, match="unique"):
        Transition.from_dict(raw)


def test_delete_requires_a_reason():
    raw = case("remove-record")
    raw["operations"][0]["reason"] = " "
    with pytest.raises(ValueError, match="DELETE requires an explicit reason"):
        Transition.from_dict(raw)


def test_noop_rejects_a_changed_candidate():
    raw = case("valid-noop")
    raw["candidate"] = [{"id": "r1", "claims": ["tinggal di Surabaya"]}]
    with pytest.raises(ValueError, match="NOOP candidate state"):
        Transition.from_dict(raw)


def test_operation_targets_must_exist():
    raw = case("supported-revision")
    raw["operations"][0]["target_id"] = "missing"
    with pytest.raises(ValueError, match="target_id must reference a prior record"):
        Transition.from_dict(raw)


def test_verify_validates_directly_constructed_transitions():
    transition = Transition.from_dict(case("remove-record"))
    invalid = replace(transition, operations=(replace(transition.operations[0], reason=""),))
    with pytest.raises(ValueError, match="DELETE requires an explicit reason"):
        verify(invalid)


def test_legacy_supersedes_is_normalized_to_an_explicit_operation():
    raw = case("explicit-supersession")
    del raw["operations"]
    raw["supersedes"] = ["r1"]
    transition = Transition.from_dict(raw)
    assert [operation.type for operation in transition.operations] == ["SUPERSEDE"]
    assert verify(transition).decision == "ACCEPT"
    assert "supersedes" not in transition_dict(transition)


@pytest.mark.parametrize(
    ("case_id", "relation", "replacement_id"),
    [
        ("corruption", "REVISES", "r1"),
        ("supported-revision", "REVISES", "r1"),
        ("explicit-supersession", "SUPERSEDES", "r2"),
    ],
)
def test_change_operations_link_old_and_new_records(case_id, relation, replacement_id):
    graph = build_graph(Transition.from_dict(case(case_id)))
    operation_nodes = {node["id"] for node in graph["nodes"] if node["type"] == "operation"}
    assert any(
        edge["from"] in operation_nodes
        and edge["to"] == "record:before:r1"
        and edge["type"] == relation
        for edge in graph["edges"]
    )
    assert any(
        edge["from"] in operation_nodes
        and edge["to"] == f"record:candidate:{replacement_id}"
        and edge["type"] == "PROPOSES"
        for edge in graph["edges"]
    )


def test_delete_operation_is_explicit_in_graph():
    graph = build_graph(Transition.from_dict(case("remove-record")))
    assert any(edge["type"] == "DELETES" and edge["to"] == "record:before:r1" for edge in graph["edges"])


def test_omission_graph_marks_the_required_claim():
    graph = build_graph(Transition.from_dict(case("omission")))
    assert any(
        edge["type"] == "REQUIRES" and edge["to"] == "claim:bekerja di Tokopedia"
        for edge in graph["edges"]
    )
    assert not any(
        edge["type"] == "CONTAINS"
        and edge["from"].startswith("record:candidate:")
        and edge["to"] == "claim:bekerja di Tokopedia"
        for edge in graph["edges"]
    )


def test_revision_and_addition_fixtures_are_not_duplicates():
    revision = Transition.from_dict(case("supported-revision"))
    addition = Transition.from_dict(case("preserve-added-claim"))
    assert transition_dict(revision)["candidate"] != transition_dict(addition)["candidate"]
    assert transition_dict(revision)["evidence"] != transition_dict(addition)["evidence"]


def test_noop_fixture_has_no_unrelated_evidence():
    assert case("valid-noop")["evidence"] == []


def test_evidence_provenance_survives_both_representations():
    transition = Transition.from_dict(case("valid"))
    evidence_node = next(node for node in build_graph(transition)["nodes"] if node["type"] == "evidence")
    assert evidence_node["source"] == "demo:employment"
    assert evidence_node["text"] == "Ia bekerja di Tokopedia."
    serialized = json.loads(textual_representation(transition))
    assert serialized["evidence"][0]["source"] == "demo:employment"
    assert serialized["evidence"][0]["text"] == "Ia bekerja di Tokopedia."


def test_only_the_unsupported_part_of_a_mixed_update_is_flagged():
    result = verify(Transition.from_dict(case("unsupported-plus-valid")))
    faithfulness = next(check for check in result.checks if check.name == "faithfulness")
    assert faithfulness.affected_ids == ["adalah pilot"]


def test_supported_revision_needs_evidence_for_preservation():
    raw = case("supported-revision")
    raw["evidence"] = []
    result = verify(Transition.from_dict(raw))
    assert result.decision == "HOLD"
    assert {check.name for check in result.checks if not check.passed} == {"preservation", "faithfulness"}
