"""Tests for the expanded v2 evaluation dataset, runner, and metrics."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from membud.experiment import (
    ManifestCase,
    load_manifest_cases,
    load_manifest_entries,
    load_manifest_labels,
    run_pilot,
)
from membud.metrics import analyze_manifest_run
from membud.schemas import Transition


ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "data/manifests/evaluation-v2.json"
V1_MANIFEST_PATH = ROOT / "data/manifests/evaluation-v1.json"
REQUIRED_CATEGORIES = {
    "valid_write",
    "valid_revise",
    "valid_supersede",
    "valid_delete_authorized",
    "valid_noop",
    "valid_multi_op",
    "valid_contradiction_correction",
    "omission",
    "corruption_no_evidence",
    "corruption_insufficient_evidence",
    "unsupported_addition",
    "unsupported_provenance",
    "contradiction_unresolved",
    "invalid_unauthorized_delete",
    "invalid_delete_authorization_target",
    "invalid_undeclared_insertion",
    "invalid_undeclared_modification",
    "invalid_undeclared_removal",
}


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def label_rows():
    return {row["case_id"]: row for row in load_jsonl(ROOT / "data/labels/evaluation-v2.jsonl")}


def case_rows():
    return load_jsonl(ROOT / "data/cases/evaluation-v2.jsonl")


def test_v2_manifest_hashes_and_unique_case_ids():
    data = manifest()
    assert data["schema_version"] == 2
    assert data["manifest_id"] == "membud-evaluation-v2"
    cases_path = (MANIFEST_PATH.parent / data["cases_file"]).resolve()
    labels_path = (MANIFEST_PATH.parent / data["labels_file"]).resolve()
    assert hashlib.sha256(cases_path.read_bytes()).hexdigest() == data["cases_sha256"]
    assert hashlib.sha256(labels_path.read_bytes()).hexdigest() == data["labels_sha256"]
    cases, labels = load_jsonl(cases_path), load_jsonl(labels_path)
    assert [row["case_id"] for row in cases] == data["case_ids"]
    assert [row["case_id"] for row in labels] == data["case_ids"]
    assert len(data["case_ids"]) == len(set(data["case_ids"])) == data["case_count"] >= 30
    assert all(row["schema_version"] == 2 for row in cases)


def test_v2_dev_heldout_split_integrity():
    data = manifest()
    entries = data["case_entries"]
    assert [entry["case_id"] for entry in entries] == data["case_ids"]
    assert {entry["split"] for entry in entries} == {"dev", "heldout"}
    dev = [entry["case_id"] for entry in entries if entry["split"] == "dev"]
    heldout = [entry["case_id"] for entry in entries if entry["split"] == "heldout"]
    assert len(dev) == len(heldout) == 18
    assert set(dev).isdisjoint(heldout)
    assert dev + heldout == data["case_ids"]
    labels = label_rows()
    for entry in entries:
        assert labels[entry["case_id"]]["split"] == entry["split"]
        assert labels[entry["case_id"]]["expected_invalid"] == entry["expected_invalid"]
    # Every split carries valid, faulty, and invalid-input cases.
    for split in ("dev", "heldout"):
        split_labels = [labels[cid] for cid in dev + heldout if labels[cid]["split"] == split]
        decisions = [row["expected_decision"] for row in split_labels]
        assert decisions.count("ACCEPT") == 7
        assert decisions.count("HOLD") == 6
        assert decisions.count("INVALID_INPUT") == 5
    expected_counts = {
        decision: sum(1 for row in labels.values() if row["expected_decision"] == decision)
        for decision in ("ACCEPT", "HOLD", "INVALID_INPUT")
    }
    assert data["expected_decision_counts"] == expected_counts
    assert data["expected_decision_counts"] == {"ACCEPT": 14, "HOLD": 12, "INVALID_INPUT": 10}


def test_v2_cases_cover_required_error_categories():
    categories = {row["category"] for row in label_rows().values()}
    assert REQUIRED_CATEGORIES <= categories
    invalid = [row for row in label_rows().values() if row["expected_invalid"]]
    assert len(invalid) == 10
    # Valid deletion and contradiction cases must actually use the new schema fields;
    # the intentionally invalid DELETE cases must be missing authorization.
    cases = {row["case_id"]: row for row in case_rows()}
    labels_by_id = label_rows()
    valid_delete_cases = [
        row for row in cases.values()
        if any(operation["type"] == "DELETE" for operation in row["operations"])
        and not labels_by_id[row["case_id"]]["expected_invalid"]
    ]
    assert valid_delete_cases
    assert all(
        operation.get("authorization")
        for row in valid_delete_cases
        for operation in row["operations"]
        if operation["type"] == "DELETE"
    )
    invalid_delete_cases = [
        row for row in cases.values()
        if any(operation["type"] == "DELETE" for operation in row["operations"])
        and labels_by_id[row["case_id"]]["expected_invalid"]
    ]
    assert invalid_delete_cases
    assert any(
        not operation.get("authorization")
        for row in invalid_delete_cases
        for operation in row["operations"]
        if operation["type"] == "DELETE"
    )
    contradiction_cases = [
        row for row in cases.values()
        if any(evidence.get("contradicts") for evidence in row["evidence"])
    ]
    assert len(contradiction_cases) >= 4


def test_expected_invalid_cases_raise_schema_errors():
    raw_by_id = {row["case_id"]: row for row in case_rows()}
    for case_id, label in label_rows().items():
        if label["expected_invalid"]:
            with pytest.raises(ValueError):
                Transition.from_dict(raw_by_id[case_id])
        else:
            Transition.from_dict(raw_by_id[case_id])


def test_v2_loader_records_invalid_cases_without_aborting():
    entries = load_manifest_entries(MANIFEST_PATH)
    assert len(entries) == 36
    invalid = [entry for entry in entries if entry.invalid]
    assert len(invalid) == 10
    assert all(entry.expected_invalid is True for entry in invalid)
    assert all(entry.invalid_reason.startswith("ValueError:") for entry in invalid)
    assert all(entry.transition is not None for entry in entries if not entry.invalid)
    labels = load_manifest_labels(MANIFEST_PATH)
    assert set(labels) == {entry.case_id for entry in entries}
    # Strict v1-compatible loader refuses the v2 invalid-input set.
    with pytest.raises(ValueError, match="invalid transition inputs"):
        load_manifest_cases(MANIFEST_PATH)


def test_manifest_hash_tampering_is_rejected(tmp_path):
    package = tmp_path / "package"
    (package / "cases").mkdir(parents=True)
    (package / "labels").mkdir()
    (package / "manifests").mkdir()
    for relative in ("cases/evaluation-v2.jsonl", "labels/evaluation-v2.jsonl",
                     "manifests/evaluation-v2.json"):
        shutil.copy(ROOT / "data" / relative, package / relative)
    tampered_manifest = package / "manifests/evaluation-v2.json"
    cases_path = package / "cases/evaluation-v2.jsonl"
    rows = cases_path.read_text(encoding="utf-8").splitlines()
    rows[0] = rows[0].replace("Bandung", "Bogor")
    cases_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="case file hash does not match manifest"):
        load_manifest_entries(tampered_manifest)
    assert tampered_manifest.exists()


def test_manifest_rejects_expected_invalid_flag_mismatch(tmp_path):
    package = tmp_path / "package"
    (package / "cases").mkdir(parents=True)
    (package / "labels").mkdir()
    (package / "manifests").mkdir()
    for relative in ("cases/evaluation-v2.jsonl", "labels/evaluation-v2.jsonl",
                     "manifests/evaluation-v2.json"):
        shutil.copy(ROOT / "data" / relative, package / relative)
    manifest_path = package / "manifests/evaluation-v2.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Mark a case that actually validates as expected-invalid.
    valid_id = next(
        entry["case_id"] for entry in data["case_entries"]
        if entry["expected_invalid"] is False
    )
    for entry in data["case_entries"]:
        if entry["case_id"] == valid_id:
            entry["expected_invalid"] = True
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="marks expected_invalid=true"):
        load_manifest_entries(manifest_path)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    output = tmp_path_factory.mktemp("v2-run")
    data = manifest()
    entries = load_manifest_entries(MANIFEST_PATH)
    labels = load_manifest_labels(MANIFEST_PATH)
    results = run_pilot(
        entries,
        output,
        run_id="v2-test",
        code_revision="test-revision",
        manifest_id=data["manifest_id"],
        manifest_sha256=hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest(),
        expected_decisions={cid: row["expected_decision"] for cid, row in labels.items()},
    )
    report = analyze_manifest_run(MANIFEST_PATH, output / "pilot-summary.json")
    return {"output": output, "results": results, "report": report, "labels": labels}


def test_run_pilot_records_invalid_input_without_aborting(report):
    results, output, labels = report["results"], report["output"], report["labels"]
    assert len(results) == 36 * 3
    assert sum(row["status"] in {"error", "verifier_error"} for row in results) == 0
    invalid_ids = {cid for cid, row in labels.items() if row["expected_invalid"]}
    for row in results:
        if row["case_id"] in invalid_ids:
            assert row["decision"] == "INVALID_INPUT"
            assert row["status"] == "invalid_input"
            assert row["invalid_input"] is True
            # No memory store may be written for invalid input in any condition.
            for condition in ("unguarded", "graph", "text"):
                assert not (output / condition / f"{row['case_id']}.json").exists()
        else:
            assert row["invalid_input"] is False
            assert row["status"] == "completed"
    summary = json.loads((output / "pilot-summary.json").read_text(encoding="utf-8"))
    assert summary["invalid_input_case_count"] == 10
    assert summary["case_count"] == 36
    for condition in ("unguarded", "graph", "text"):
        events = load_jsonl(output / condition / "audit.jsonl")
        invalid_events = [event for event in events if event["status"] == "invalid_input"]
        assert len(invalid_events) == 10
        assert all(event["decision"] == "INVALID_INPUT" for event in invalid_events)


def test_metrics_invalid_input_and_denominators(report):
    metrics = report["report"]["condition_metrics"]
    for condition in ("graph", "text"):
        invalid = metrics[condition]["invalid_input"]
        assert invalid["expected_invalid_cases"] == 10
        assert invalid["handled_invalid_cases"] == 10
        assert invalid["handled_rate"] == {"numerator": 10, "denominator": 10, "rate": 1.0}
        assert invalid["unexpected_invalid_rows"] == 0
        assert metrics[condition]["decision_counts"]["INVALID_INPUT"] == 10
        assert metrics[condition]["reason_code_counts"]["invalid_input_rows"] == 10
        assert metrics[condition]["valid_update_acceptance"] == {
            "accepted": 14, "cases": 14, "rate": {"numerator": 14, "denominator": 14, "rate": 1.0}
        }
    # Graph catches both unresolved-contradiction cases; the claim-set baseline cannot.
    graph = metrics["graph"]
    text = metrics["text"]
    assert graph["faulty_detection"]["gold_faulty_cases"] == 22
    assert graph["faulty_detection"]["true_positives"] == 22
    assert graph["faulty_detection"]["false_positives"] == 0
    assert graph["faulty_detection"]["recall"] == {"numerator": 22, "denominator": 22, "rate": 1.0}
    assert text["faulty_detection"]["true_positives"] == 20
    assert text["faulty_detection"]["false_negatives"] == 2
    assert text["faulty_detection"]["recall"] == {"numerator": 20, "denominator": 22, "rate": 20 / 22}
    assert text["faulty_detection"]["false_accept_rate"] == {
        "numerator": 2, "denominator": 22, "rate": 2 / 22
    }
    assert graph["reason_code_counts"]["fail"]["CONTRADICTED_CLAIM"] == 2
    assert "CONTRADICTED_CLAIM" not in text["reason_code_counts"]["fail"]
    differences = report["report"]["paired_differences"]["graph_vs_text"]
    assert differences["differing_case_ids"] == ["DEV-13", "HELD-13"]
    assert differences["differing_case_count"] == 2


def test_metrics_split_breakdown_sums_to_pooled(report):
    metrics = report["report"]["condition_metrics"]
    assert report["report"]["split_counts"] == {"dev": 18, "heldout": 18}
    for condition in ("graph", "text"):
        pooled = metrics[condition]
        assert set(pooled["splits"]) == {"dev", "heldout"}
        for name in ("ACCEPT", "HOLD", "INVALID_INPUT", "ERROR"):
            assert (
                pooled["splits"]["dev"]["decision_counts"][name]
                + pooled["splits"]["heldout"]["decision_counts"][name]
                == pooled["decision_counts"][name]
            )
        for split in ("dev", "heldout"):
            block = pooled["splits"][split]
            assert block["case_count"] == 18
            assert block["invalid_input"]["expected_invalid_cases"] == 5
            assert block["valid_update_acceptance"]["cases"] == 7
            assert block["faulty_detection"]["gold_faulty_cases"] == 11
    unguarded = metrics["unguarded"]
    assert unguarded["decision_counts"] == {"APPLIED": 26, "INVALID_INPUT": 10}
    assert unguarded["raw_effect"]["gold_faulty_cases"] == 12
    assert unguarded["raw_effect"]["faulty_updates_applied"] == 12


def test_metrics_cost_timing_and_reason_codes(report):
    metrics = report["report"]["condition_metrics"]
    for condition in ("graph", "text", "unguarded"):
        block = metrics[condition]
        cost = block.get("estimated_cost")
        if cost is not None:
            assert cost["estimated_cost_usd"] == 0.0
            assert cost["billing_data_available"] is False
            assert "no paid model calls" in cost["note"].lower()
        timing = block["timing_ms"]
        assert timing["latency_ms"]["case_count"] == 36
        if condition != "unguarded":
            # Preparation/verification only exist for the 26 constructible cases;
            # invalid-input rows still record end-to-end latency.
            assert timing["preparation_ms"]["case_count"] == 26
            assert timing["verification_ms"]["case_count"] == 26
            breakdown = block["timing_breakdown"]
            assert breakdown["graph_build_separated_from_verification"] is True
            assert breakdown["preparation_ms_mean"] is not None
            assert breakdown["verification_ms_mean"] is not None
            assert block["llm_usage"]["response_count"] == 0
            assert block["token_usage"]["total_tokens"] == 0


def test_v1_manifest_remains_loadable_and_metric_semantics_preserved(tmp_path):
    data = json.loads(V1_MANIFEST_PATH.read_text(encoding="utf-8"))
    transitions = load_manifest_cases(V1_MANIFEST_PATH)
    assert len(transitions) == 12
    output = tmp_path / "v1-run"
    run_pilot(
        transitions,
        output,
        run_id="v1-compat",
        code_revision="test-revision",
        manifest_id=data["manifest_id"],
        manifest_sha256=hashlib.sha256(V1_MANIFEST_PATH.read_bytes()).hexdigest(),
    )
    report = analyze_manifest_run(V1_MANIFEST_PATH, output / "pilot-summary.json")
    assert report["candidate_error_rates"]["omission"] == {
        "numerator": 7, "denominator": 23, "rate": 7 / 23
    }
    assert report["condition_metrics"]["graph"]["as_completed"]["faulty"]["detection_rate"] == {
        "numerator": 7, "denominator": 7, "rate": 1.0
    }
    assert report["condition_metrics"]["graph"]["invalid_input"]["expected_invalid_cases"] == 0
    assert report["split_counts"] == {}
    assert "faulty_detection" in report["condition_metrics"]["graph"]
