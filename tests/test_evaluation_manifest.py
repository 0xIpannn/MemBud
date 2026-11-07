import hashlib
import json
from pathlib import Path

from membud.experiment import load_manifest_cases, run_pilot, source_fingerprint
from membud.metrics import analyze_manifest_run
from membud.schemas import Transition
from membud.verification import verify


ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "data/manifests/evaluation-v1.json"


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_source_fingerprint_pins_current_working_tree():
    fingerprint = source_fingerprint()
    assert fingerprint.startswith("working-tree-sha256:")
    assert len(fingerprint.rsplit(":", 1)[1]) == 64


def test_manifest_loader_returns_only_transitions_not_gold_labels():
    manifest_path = MANIFEST_PATH
    transitions = load_manifest_cases(manifest_path)
    assert [transition.case_id for transition in transitions] == json.loads(
        manifest_path.read_text(encoding="utf-8")
    )["case_ids"]
    assert all(isinstance(transition, Transition) for transition in transitions)


def test_frozen_manifest_hashes_and_case_ids_match():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    cases_path = (MANIFEST_PATH.parent / manifest["cases_file"]).resolve()
    labels_path = (MANIFEST_PATH.parent / manifest["labels_file"]).resolve()
    assert hashlib.sha256(cases_path.read_bytes()).hexdigest() == manifest["cases_sha256"]
    assert hashlib.sha256(labels_path.read_bytes()).hexdigest() == manifest["labels_sha256"]
    cases = load_jsonl(cases_path)
    labels = load_jsonl(labels_path)
    assert [case["case_id"] for case in cases] == manifest["case_ids"]
    assert [label["case_id"] for label in labels] == manifest["case_ids"]


def test_metrics_report_case_and_claim_denominators(tmp_path):
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    transitions = load_manifest_cases(MANIFEST_PATH)
    manifest_hash = hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
    run_pilot(
        transitions,
        tmp_path / "run",
        run_id="metrics-test",
        code_revision="test-revision",
        manifest_id=manifest["manifest_id"],
        manifest_sha256=manifest_hash,
    )
    report = analyze_manifest_run(MANIFEST_PATH, tmp_path / "run/pilot-summary.json")
    assert report["candidate_error_rates"]["omission"] == {"numerator": 7, "denominator": 23, "rate": 7 / 23}
    assert report["candidate_error_rates"]["unsupported_addition"] == {"numerator": 4, "denominator": 20, "rate": 0.2}
    assert report["candidate_error_rates"]["prior_record_corruption"] == {"numerator": 4, "denominator": 13, "rate": 4 / 13}
    assert report["run_id"] == "metrics-test"
    assert report["code_revision"] == "test-revision"
    assert len(report["summary_sha256"]) == 64
    for condition in ("graph", "text"):
        metrics = report["condition_metrics"][condition]
        assert metrics["as_completed"]["faulty"]["detection_rate"] == {"numerator": 7, "denominator": 7, "rate": 1.0}
        assert metrics["as_completed"]["faulty"]["false_accept_rate"] == {"numerator": 0, "denominator": 7, "rate": 0.0}
        assert metrics["as_completed"]["valid"]["false_hold_rate"] == {"numerator": 0, "denominator": 5, "rate": 0.0}
        assert metrics["fail_closed_itt"]["faulty"]["detection_rate"] == {"numerator": 7, "denominator": 7, "rate": 1.0}
        assert metrics["fail_closed_itt"]["faulty"]["error_holds"] == 0
        assert metrics["fail_closed_itt"]["valid"]["false_hold_rate"] == {"numerator": 0, "denominator": 5, "rate": 0.0}
        assert metrics["fail_closed_itt"]["valid"]["error_holds"] == 0
        timing = metrics["timing_ms"]
        assert timing["latency_ms"]["case_count"] == 12
        assert timing["preparation_ms"]["case_count"] == 12
        assert timing["verification_ms"]["case_count"] == 12
        assert metrics["llm_usage"]["response_count"] == 0
    assert report["condition_metrics"]["graph"]["timing_ms"]["graph_node_count"]["case_count"] == 12
    assert report["condition_metrics"]["graph"]["timing_ms"]["graph_edge_count"]["case_count"] == 12
    assert report["condition_metrics"]["text"]["timing_ms"]["graph_node_count"]["case_count"] == 0
    assert report["condition_metrics"]["unguarded"]["timing_ms"]["verification_ms"]["case_count"] == 0


def test_metrics_do_not_misclassify_verifier_errors_as_decisions(tmp_path):
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    transitions = load_manifest_cases(MANIFEST_PATH)
    manifest_hash = hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
    output = tmp_path / "run"
    run_pilot(
        transitions,
        output,
        run_id="error-metrics-test",
        code_revision="test-revision",
        manifest_id=manifest["manifest_id"],
        manifest_sha256=manifest_hash,
    )
    summary_path = output / "pilot-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    labels = {row["case_id"]: row for row in load_jsonl(MANIFEST_PATH.parent / manifest["labels_file"])}
    faulty_id = next(cid for cid in manifest["case_ids"] if labels[cid]["expected_decision"] == "HOLD")
    valid_id = next(cid for cid in manifest["case_ids"] if labels[cid]["expected_decision"] == "ACCEPT")
    for row in summary["results"]:
        if row["condition"] == "graph" and row["case_id"] in {faulty_id, valid_id}:
            row["status"] = "verifier_error"
            row["decision"] = "HOLD"
            row["result"] = {"verifier": "deepseek-flash", "usage": {}}
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    report = analyze_manifest_run(MANIFEST_PATH, summary_path)
    graph = report["condition_metrics"]["graph"]
    # As-completed: the two error rows leave both numerator and denominator.
    assert graph["as_completed"]["faulty"]["completed"] == 6
    assert graph["as_completed"]["faulty"]["detection_rate"] == {"numerator": 6, "denominator": 6, "rate": 1.0}
    assert graph["as_completed"]["valid"]["completed"] == 4
    assert graph["as_completed"]["valid"]["acceptance_rate"] == {"numerator": 4, "denominator": 4, "rate": 1.0}
    # Fail-closed ITT: error rows are non-accepting but are not credited as detections.
    assert graph["fail_closed_itt"]["faulty"]["detection_rate"] == {"numerator": 6, "denominator": 7, "rate": 6 / 7}
    assert graph["fail_closed_itt"]["faulty"]["error_holds"] == 1
    assert graph["fail_closed_itt"]["faulty"]["false_accept_rate"] == {"numerator": 0, "denominator": 7, "rate": 0.0}
    assert graph["fail_closed_itt"]["valid"]["false_hold_rate"] == {"numerator": 1, "denominator": 5, "rate": 0.2}
    assert graph["fail_closed_itt"]["valid"]["error_holds"] == 1
    assert graph["verifier_errors"] == graph["execution_errors"] == 2
    assert graph["decision_counts"]["HOLD"] == 6
    assert graph["decision_counts"]["ACCEPT"] == 4


def test_frozen_case_labels_match_current_deterministic_verifier():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    cases_path = (MANIFEST_PATH.parent / manifest["cases_file"]).resolve()
    labels_path = (MANIFEST_PATH.parent / manifest["labels_file"]).resolve()
    cases = load_jsonl(cases_path)
    labels_by_id = {row["case_id"]: row for row in load_jsonl(labels_path)}
    decisions = {"ACCEPT": 0, "HOLD": 0}
    for raw in cases:
        result = verify(Transition.from_dict(raw))
        label = labels_by_id[raw["case_id"]]
        failed = {check.name: check.reason_code for check in result.checks if check.status == "FAIL"}
        affected = {check.name: check.affected_ids for check in result.checks if check.status == "FAIL"}
        assert result.decision == label["expected_decision"]
        assert failed == label["expected_failed_checks"]
        assert affected == label["expected_affected_ids"]
        decisions[result.decision] += 1
    assert decisions == manifest["expected_decision_counts"]


def test_metrics_tolerate_runner_error_rows(tmp_path):
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    transitions = load_manifest_cases(MANIFEST_PATH)
    manifest_hash = hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
    output = tmp_path / "run"
    run_pilot(
        transitions,
        output,
        run_id="error-row-test",
        code_revision="test-revision",
        manifest_id=manifest["manifest_id"],
        manifest_sha256=manifest_hash,
    )
    summary_path = output / "pilot-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    row = next(row for row in summary["results"] if row["condition"] == "graph")
    row["status"] = "error"
    row["decision"] = "ERROR"
    row["result"] = None
    row["error"] = "RuntimeError: simulated runner failure"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    report = analyze_manifest_run(MANIFEST_PATH, summary_path)
    graph = report["condition_metrics"]["graph"]
    assert graph["execution_errors"] == 1
    assert graph["llm_usage"]["response_count"] == 0
    # The first graph case (DRAFT-01) is a valid case; its error row is a
    # non-accepting ITT outcome and must not crash or become a phantom decision.
    assert graph["fail_closed_itt"]["valid"]["error_holds"] == 1
    assert graph["fail_closed_itt"]["valid"]["false_hold_rate"] == {"numerator": 1, "denominator": 5, "rate": 0.2}
