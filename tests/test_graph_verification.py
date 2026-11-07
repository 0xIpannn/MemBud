import copy

import pytest

from membud.demo import cases
from membud.graph import build_graph
from membud.graph_verification import verify_graph
from membud.schemas import Transition
from membud.verification import verify


def case(case_id):
    return next(raw for raw in cases() if raw["case_id"] == case_id)


def transition(case_id):
    return Transition.from_dict(case(case_id))


def check(result, name):
    return next(check for check in result.checks if check.name == name)


def test_graph_verifier_reports_required_claim_omission():
    result = verify(transition("omission"), "graph")
    assert result.decision == "HOLD"
    coverage = check(result, "coverage")
    assert coverage.status == "FAIL"
    assert coverage.reason_code == "REQUIRED_CLAIM_OMITTED"
    assert coverage.affected_ids == ["bekerja di Tokopedia"]


def test_graph_verifier_detects_contradiction_edges_where_text_baseline_passes():
    raw = {
        "schema_version": 2,
        "case_id": "contradiction",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "evidence": [{
            "id": "e1",
            "source": "demo:conflict",
            "text": "Ia tidak lagi bekerja di Tokopedia.",
            "supports": ["bekerja di Tokopedia"],
            "contradicts": ["bekerja di Tokopedia"],
        }],
        "required_claims": [],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    }
    t = Transition.from_dict(raw)
    graph_result = verify(t, "graph")
    text_result = verify(t, "text")
    assert graph_result.decision == "HOLD"
    faithfulness = check(graph_result, "faithfulness")
    assert faithfulness.reason_code == "CONTRADICTED_CLAIM"
    assert faithfulness.affected_ids == ["bekerja di Tokopedia"]
    # The claim-set baseline has no relationship edges: it sees only exact
    # support and accepts this transition. This is the behavioral difference
    # the graph verifier is meant to contribute.
    assert text_result.decision == "ACCEPT"


def test_graph_verifier_detects_disconnected_claim_nodes():
    t = transition("valid")
    graph = copy.deepcopy(build_graph(t))
    target = "claim:bekerja di Tokopedia"
    graph["edges"] = [
        edge for edge in graph["edges"] if edge["from"] != target and edge["to"] != target
    ]
    result = verify_graph(t, graph=graph)
    assert result.decision == "HOLD"
    faithfulness = check(result, "faithfulness")
    assert faithfulness.reason_code == "DISCONNECTED_CLAIM"
    assert faithfulness.affected_ids == ["bekerja di Tokopedia"]
    # The same transition passes with the intact canonical graph.
    assert verify(t, "graph").decision == "ACCEPT"


def test_graph_verifier_rejects_dangling_edges_as_structural_inconsistency():
    t = transition("valid")
    graph = copy.deepcopy(build_graph(t))
    graph["edges"].append({
        "from": f"transition:{t.case_id}",
        "to": "claim:missing-node",
        "type": "REQUIRES",
    })
    result = verify_graph(t, graph=graph)
    assert result.decision == "HOLD"
    faithfulness = check(result, "faithfulness")
    assert faithfulness.reason_code == "GRAPH_STRUCTURE_INVALID"


def test_graph_verifier_rejects_missing_operation_relation_edge():
    t = transition("remove-record")
    graph = copy.deepcopy(build_graph(t))
    graph["edges"] = [edge for edge in graph["edges"] if edge["type"] != "DELETES"]
    result = verify_graph(t, graph=graph)
    assert result.decision == "HOLD"
    assert check(result, "faithfulness").reason_code == "GRAPH_STRUCTURE_INVALID"


def test_graph_verifier_rejects_unattached_required_claims():
    t = transition("valid")
    graph = copy.deepcopy(build_graph(t))
    graph["edges"] = [
        edge
        for edge in graph["edges"]
        if not (edge["type"] == "REQUIRES" and edge["to"] == "claim:tinggal di Bandung")
    ]
    result = verify_graph(t, graph=graph)
    assert result.decision == "HOLD"
    assert check(result, "faithfulness").reason_code == "GRAPH_STRUCTURE_INVALID"


def test_graph_verifier_detects_legacy_undeclared_removal():
    # v1 legacy transitions bypass strict operation-coverage validation for
    # archival compatibility; the graph verifier still detects the structural
    # removal, which is the defense-in-depth behavior.
    raw = {
        "case_id": "legacy-removal",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [],
        "evidence": [],
        "required_claims": [],
        "operations": [],
    }
    t = Transition.from_dict(raw)
    result = verify(t, "graph")
    assert result.decision == "HOLD"
    preservation = check(result, "preservation")
    assert preservation.reason_code == "PRIOR_RECORD_UNAUTHORIZED_CHANGE"
    assert preservation.affected_ids == ["r1"]


def test_delete_authorization_is_explicit_in_graph():
    graph = build_graph(transition("remove-record"))
    operation_nodes = {node["id"] for node in graph["nodes"] if node["type"] == "operation"}
    assert any(
        edge["from"] in operation_nodes
        and edge["type"] == "AUTHORIZED_BY"
        and edge["to"] == "evidence:e3"
        for edge in graph["edges"]
    )


def test_graph_and_text_verifiers_agree_on_fixture_decisions():
    for raw in cases():
        t = Transition.from_dict(raw)
        graph_decision = verify(t, "graph").decision
        text_decision = verify(t, "text").decision
        assert graph_decision == text_decision


def test_graph_verifier_rejects_unknown_representation():
    with pytest.raises(ValueError, match="representation"):
        verify(transition("valid"), "table")
