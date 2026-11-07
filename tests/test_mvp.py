import json
from dataclasses import replace

import pytest

from membud.demo import cases
from membud.graph import build_graph, to_mermaid
from membud.schemas import Transition
from membud.serialization import textual_representation, transition_dict
from membud.store import commit, run_case
from membud.verification import verify


EXPECTED_RESULTS = {
    "valid": ("ACCEPT", set()),
    "omission": ("HOLD", {"coverage"}),
    "corruption": ("HOLD", {"coverage", "preservation", "faithfulness"}),
    "unsupported": ("HOLD", {"faithfulness"}),
    "valid-noop": ("ACCEPT", set()),
    "preserve-added-claim": ("ACCEPT", set()),
    "remove-record": ("ACCEPT", set()),
    "required-claim-deletion": ("HOLD", {"coverage"}),
    "supported-revision": ("ACCEPT", set()),
    "explicit-supersession": ("ACCEPT", set()),
    "unsupported-plus-valid": ("HOLD", {"faithfulness"}),
}


def case(case_id):
    return next(raw for raw in cases() if raw["case_id"] == case_id)


def store_records(path):
    payload = json.loads(path.read_text())
    return payload["records"] if isinstance(payload, dict) else payload


def test_memory_adapter_protocol_is_structural():
    from membud.adapters import MemoryAdapter
    from membud.schemas import Record

    class FakeAdapter:
        backend_name = "fake"

        def snapshot(self, scope):
            return (Record("r1", ("claim",)),)

        def replace_state(self, scope, records):
            self.written = tuple(records)

    adapter = FakeAdapter()
    assert isinstance(adapter, MemoryAdapter)
    assert adapter.snapshot({"user_id": "u1"}) == (Record("r1", ("claim",)),)


def test_core_preserves_legacy_imports():
    from membud import core

    public_api = {
        name: globals()[name]
        for name in (
            "Transition",
            "build_graph",
            "commit",
            "run_case",
            "textual_representation",
            "to_mermaid",
            "transition_dict",
            "verify",
        )
    }
    for name, exported in public_api.items():
        assert getattr(core, name) is exported


@pytest.mark.parametrize("raw", cases())
def test_demo_cases_match_annotations_in_both_paths(raw):
    transition = Transition.from_dict(raw)
    graph_result = verify(transition, "graph")
    text_result = verify(transition, "text")
    # The graph verifier is structural and the text verifier is a claim-set
    # baseline, so their result payloads differ by design. They must agree on
    # the decision and on which checks failed for these fixtures.
    assert graph_result.decision == text_result.decision
    assert {c.name for c in graph_result.checks if not c.passed} == {
        c.name for c in text_result.checks if not c.passed
    }

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


def test_commit_accepts_complete_passing_external_verification_result():
    from membud.schemas import Check, Result

    transition = Transition.from_dict(case("valid"))
    store = {record.id: record for record in transition.before}
    external_result = Result(transition.case_id, "ACCEPT", [
        Check("coverage", True, "passed"),
        Check("preservation", True, "passed"),
        Check("faithfulness", True, "passed"),
    ], verifier="external:test-model")
    result = commit(store, transition, external_result)
    assert result == {record.id: record for record in transition.candidate}


def test_commit_rejects_accept_decision_with_failed_or_uncertain_check():
    from membud.schemas import Check, Result

    transition = Transition.from_dict(case("valid"))
    store = {record.id: record for record in transition.before}
    for checks in (
        [Check("faithfulness", False, "unsupported claim", ["claim-1"])],
        [Check("faithfulness", False, "conflicting evidence", ["claim-1"], status="UNCERTAIN")],
        [Check("faithfulness", True, "passed")],
        [],
    ):
        forged_accept = Result(transition.case_id, "ACCEPT", checks)
        with pytest.raises(ValueError, match="ACCEPT requires every required check to pass"):
            commit(store, transition, forged_accept)
    assert store == {record.id: record for record in transition.before}


def test_pilot_runner_executes_unguarded_graph_and_text_on_same_candidates(tmp_path):
    from membud.experiment import run_pilot

    transitions = [Transition.from_dict(case(case_id)) for case_id in ("valid", "unsupported")]
    results = run_pilot(
        transitions,
        tmp_path,
        run_id="pilot-1",
        code_revision="abc123",
        manifest_id="manifest-1",
        manifest_sha256="hash-1",
    )
    assert all("latency_ms" in row for row in results)
    assert all(row["preparation_ms"] is None for row in results if row["condition"] == "unguarded")
    assert all(row["preparation_ms"] is not None for row in results if row["condition"] != "unguarded")
    assert [(row["case_id"], row["condition"], row["decision"]) for row in results] == [
        (case_id, condition, decision)
        for case_id, unguarded, guarded in (("valid", "APPLIED", "ACCEPT"), ("unsupported", "APPLIED", "HOLD"))
        for condition, decision in (("unguarded", unguarded), ("graph", guarded), ("text", guarded))
    ]
    for case_id in ("valid", "unsupported"):
        raw = case(case_id)
        unguarded = json.loads((tmp_path / "unguarded" / f"{case_id}.json").read_text())
        graph_store = tmp_path / "graph" / f"{case_id}.json"
        text_store = tmp_path / "text" / f"{case_id}.json"
        assert unguarded == raw["candidate"]
        if case_id == "valid":
            assert store_records(graph_store) == raw["candidate"]
            assert store_records(text_store) == raw["candidate"]
        else:
            # HOLD must not create a store file at all.
            assert not graph_store.exists()
            assert not text_store.exists()
    for condition in ("unguarded", "graph", "text"):
        events = [json.loads(line) for line in (tmp_path / condition / "audit.jsonl").read_text().splitlines()]
        assert {event["run_id"] for event in events} == {"pilot-1"}
        assert {event["code_revision"] for event in events} == {"abc123"}
        assert {event["manifest_id"] for event in events} == {"manifest-1"}
        assert {event["manifest_sha256"] for event in events} == {"hash-1"}
        if condition == "unguarded":
            assert all(event["preparation_ms"] is None for event in events)
            assert all(event["verification_ms"] is None for event in events)
        else:
            assert all(event["preparation_ms"] >= 0 for event in events)
            assert all(event["verification_ms"] >= 0 for event in events)
    summary = json.loads((tmp_path / "pilot-summary.json").read_text())
    assert summary["manifest_id"] == "manifest-1"
    assert summary["manifest_sha256"] == "hash-1"


def test_pilot_runner_rejects_duplicate_case_ids_before_writing(tmp_path):
    from membud.experiment import run_pilot

    transition = Transition.from_dict(case("valid"))
    with pytest.raises(ValueError, match="case IDs must be unique"):
        run_pilot([transition, transition], tmp_path)
    assert not list(tmp_path.rglob("*.json"))


def test_guarded_json_store_and_append_only_log(tmp_path):
    t = Transition.from_dict(cases()[2])
    store, log = tmp_path / "store.json", tmp_path / "audit.jsonl"
    original = json.dumps(cases()[2]["before"]).encode()
    store.write_bytes(original)
    result = run_case(t, store, log)
    assert result.decision == "HOLD"
    assert store.read_bytes() == original
    assert len(log.read_text().splitlines()) == 1


def test_guarded_json_store_commits_accepted_update(tmp_path):
    raw = case("valid")
    transition = Transition.from_dict(raw)
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    store.write_text(json.dumps(raw["before"]))
    result = run_case(transition, store, log, run_id="run-1", code_revision="abc123")
    assert result.decision == "ACCEPT"
    assert store_records(store) == raw["candidate"]
    event = json.loads(log.read_text())
    assert event["run_id"] == "run-1"
    assert event["code_revision"] == "abc123"
    assert event["status"] == "completed"
    assert event["latency_ms"] >= 0


def test_invalid_transition_does_not_write_store_or_audit(tmp_path):
    transition = Transition.from_dict(case("remove-record"))
    invalid = replace(transition, operations=(replace(transition.operations[0], reason=""),))
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    original = json.dumps(case("remove-record")["before"])
    store.write_text(original)
    with pytest.raises(ValueError, match="DELETE requires an explicit reason"):
        run_case(invalid, store, log)
    assert store.read_text() == original
    event = json.loads(log.read_text())
    assert event["status"] == "error"
    assert event["error_type"] == "ValueError"
    assert event["input"]["case_id"] == "remove-record"


def test_replayed_case_is_audited_and_fails_closed(tmp_path):
    transition = Transition.from_dict(case("valid"))
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    run_case(transition, store, log)
    with pytest.raises(ValueError, match="does not match transition.before"):
        run_case(transition, store, log)
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(events) == 2
    assert [event["input"]["case_id"] for event in events] == ["valid", "valid"]
    assert [event["status"] for event in events] == ["completed", "error"]


def test_invalid_store_json_does_not_append_audit_event(tmp_path):
    transition = Transition.from_dict(case("valid"))
    store, log = tmp_path / "state.json", tmp_path / "audit.jsonl"
    store.write_text("not json")
    with pytest.raises(json.JSONDecodeError):
        run_case(transition, store, log)
    assert store.read_text() == "not json"
    event = json.loads(log.read_text())
    assert event["status"] == "error"
    assert event["error_type"] == "JSONDecodeError"


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


def test_write_operation_must_reference_new_candidate_record():
    raw = case("valid")
    raw["operations"] = [{"type": "WRITE", "record_id": "missing"}]
    with pytest.raises(ValueError, match="WRITE record_id must reference a candidate record"):
        Transition.from_dict(raw)


def test_prior_record_cannot_be_targeted_by_multiple_operations():
    raw = case("supported-revision")
    raw["operations"].append({"type": "DELETE", "target_id": "r1", "reason": "duplicate"})
    with pytest.raises(ValueError, match="only be targeted by one operation"):
        Transition.from_dict(raw)


def test_verification_results_include_machine_readable_status_and_reason_codes():
    result = verify(Transition.from_dict(case("unsupported")))
    faithfulness = next(check for check in result.checks if check.name == "faithfulness")
    assert faithfulness.status == "FAIL"
    assert faithfulness.reason_code == "UNSUPPORTED_CLAIM"
    assert faithfulness.affected_ids == ["memiliki kapal pesiar"]
    serialized = result.to_dict()["checks"][-1]
    assert serialized["status"] == "FAIL"
    assert serialized["reason_code"] == "UNSUPPORTED_CLAIM"


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


def test_graph_builder_rejects_invalid_directly_constructed_transition():
    transition = Transition.from_dict(case("remove-record"))
    invalid = replace(transition, operations=(replace(transition.operations[0], reason=" "),))
    with pytest.raises(ValueError, match="DELETE requires an explicit reason"):
        build_graph(invalid)


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
    diagram = to_mermaid(transition)
    assert "Ia bekerja di Tokopedia." in diagram
    assert "tinggal di Bandung" in diagram
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


def test_undeclared_removal_is_rejected():
    raw = {
        "schema_version": 2,
        "case_id": "silent-drop",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [{"id": "r2", "claims": ["bekerja di Tokopedia"]}],
        "evidence": [{"id": "e1", "source": "demo", "text": "Ia bekerja di Tokopedia.", "supports": ["bekerja di Tokopedia"]}],
        "required_claims": ["tinggal di Bandung", "bekerja di Tokopedia"],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    }
    with pytest.raises(ValueError, match="undeclared removal of prior record r1"):
        Transition.from_dict(raw)


def test_undeclared_modification_is_rejected():
    raw = {
        "schema_version": 2,
        "case_id": "undeclared-mutation",
        "before": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Surabaya"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
            {"id": "r3", "claims": ["belajar bahasa Jepang"]},
        ],
        "evidence": [{"id": "e1", "source": "demo", "text": "Ia mulai belajar bahasa Jepang.", "supports": ["belajar bahasa Jepang"]}],
        "required_claims": ["tinggal di Bandung", "bekerja di Tokopedia", "belajar bahasa Jepang"],
        "operations": [{"type": "WRITE", "record_id": "r3"}],
    }
    with pytest.raises(ValueError, match="undeclared modification of prior record r1"):
        Transition.from_dict(raw)


def test_valid_update_preserves_multiple_prior_records():
    raw = {
        "case_id": "multi-record-preservation",
        "before": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
            {"id": "r3", "claims": ["belajar bahasa Jepang"]},
        ],
        "evidence": [{"id": "e1", "source": "demo", "text": "Ia mulai belajar bahasa Jepang.", "supports": ["belajar bahasa Jepang"]}],
        "required_claims": ["tinggal di Bandung", "bekerja di Tokopedia", "belajar bahasa Jepang"],
        "operations": [{"type": "WRITE", "record_id": "r3"}],
    }
    result = verify(Transition.from_dict(raw))
    assert result.decision == "ACCEPT"
    assert all(check.passed for check in result.checks)


def test_pilot_runner_refuses_to_reuse_an_output_directory(tmp_path):
    from membud.experiment import run_pilot

    transitions = [Transition.from_dict(case("valid"))]
    run_pilot(transitions, tmp_path, run_id="first")
    with pytest.raises(ValueError, match="fresh --output"):
        run_pilot(transitions, tmp_path, run_id="second")


def test_delete_requires_structured_authorization():
    raw = case("remove-record")
    del raw["operations"][0]["authorization"]
    with pytest.raises(ValueError, match="structured authorization"):
        Transition.from_dict(raw)


def test_delete_authorization_must_reference_existing_evidence():
    raw = case("remove-record")
    raw["operations"][0]["authorization"]["evidence_id"] = "missing-evidence"
    with pytest.raises(ValueError, match="evidence_id must reference"):
        Transition.from_dict(raw)


def test_delete_authorization_target_must_match_operation_target():
    raw = case("remove-record")
    raw["operations"][0]["authorization"]["target_id"] = "r2"
    with pytest.raises(ValueError, match="target_id must match"):
        Transition.from_dict(raw)


def test_undeclared_insertion_is_rejected():
    raw = case("valid")
    raw["operations"] = []
    with pytest.raises(ValueError, match="undeclared insertion"):
        Transition.from_dict(raw)


def test_undeclared_modification_is_rejected_without_revise():
    raw = {
        "schema_version": 2,
        "case_id": "undeclared-revise",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [{"id": "r1", "claims": ["tinggal di Surabaya"]}],
        "evidence": [],
        "required_claims": [],
        "operations": [],
    }
    with pytest.raises(ValueError, match="undeclared modification"):
        Transition.from_dict(raw)


def test_revise_must_actually_change_the_record():
    raw = {
        "schema_version": 2,
        "case_id": "no-op-revise",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "evidence": [],
        "required_claims": [],
        "operations": [{"type": "REVISE", "record_id": "r1", "target_id": "r1"}],
    }
    with pytest.raises(ValueError, match="REVISE must change"):
        Transition.from_dict(raw)


def test_unsupported_schema_version_is_rejected():
    raw = case("valid")
    raw["schema_version"] = 99
    with pytest.raises(ValueError, match="unsupported schema_version"):
        Transition.from_dict(raw)


def test_versionless_input_keeps_v1_legacy_semantics():
    raw = case("valid")
    raw.pop("schema_version", None)
    transition = Transition.from_dict(raw)
    assert transition.schema_version == 1
    assert transition_dict(transition)["schema_version"] == 1
    # Legacy input skips strict operation-coverage invariants for archival
    # reproducibility, but v2 input enforces them.
    legacy_undeclared = {
        "case_id": "legacy-undeclared",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [{"id": "r2", "claims": ["bekerja di Tokopedia"]}],
        "evidence": [],
        "required_claims": ["tinggal di Bandung"],
        "operations": [],
    }
    Transition.from_dict(legacy_undeclared)  # loads under v1 semantics
    strict = dict(legacy_undeclared, schema_version=2)
    with pytest.raises(ValueError, match="undeclared"):
        Transition.from_dict(strict)
