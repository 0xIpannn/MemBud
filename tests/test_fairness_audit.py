"""Tests for the fair text verifier and the fairness audit artifact."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from membud.fairness import verify_text_fair
from membud.schemas import Result, Transition
from membud.verification import _verify_claim_sets, verify

ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "data/manifests/evaluation-v2.json"
ARTIFACT_PATH = ROOT / "data/results/fairness-audit-v2/audit.json"
SCRIPT_PATH = ROOT / "scripts/run_fairness_audit.py"
STRATEGIES = ("unguarded", "text", "text_fair", "graph")
DECISIONS = ("ACCEPT", "HOLD", "INVALID_INPUT")


def _transition(payload):
    return Transition.from_dict(payload)


def _contradiction_transition():
    """Retained candidate claims are directly contradicted; plain text accepts."""
    return _transition({
        "case_id": "FAIR-CONTRADICTED",
        "schema_version": 2,
        "before": [{"id": "r1", "claims": ["tinggal di Bandung", "bekerja di Tokopedia"]}],
        "candidate": [{"id": "r1", "claims": ["tinggal di Bandung", "bekerja di Tokopedia"]}],
        "evidence": [{
            "id": "e1",
            "source": "synthetic:correction",
            "text": "Saya sekarang tinggal di Surabaya.",
            "supports": ["tinggal di Surabaya"],
            "contradicts": ["bekerja di Tokopedia", "tinggal di Bandung", "tidak relevan"],
        }],
        "required_claims": ["tinggal di Bandung"],
        "operations": [{"type": "NOOP"}],
    })


def _valid_write_transition():
    """Ordinary supported write with no contradiction annotations."""
    return _transition({
        "case_id": "FAIR-NO-CONTRADICTION",
        "schema_version": 2,
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "evidence": [{
            "id": "e1",
            "source": "synthetic:employment",
            "text": "Ia bekerja di Tokopedia.",
            "supports": ["bekerja di Tokopedia"],
        }],
        "required_claims": ["tinggal di Bandung", "bekerja di Tokopedia"],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    })


def _resolved_correction_transition():
    """Contradiction evidence retires an old claim; the candidate is supported."""
    return _transition({
        "case_id": "FAIR-RESOLVED",
        "schema_version": 2,
        "before": [{"id": "r1", "claims": ["bekerja di PT Sinar"]}],
        "candidate": [{"id": "r1", "claims": ["bekerja di PT Astra"]}],
        "evidence": [{
            "id": "e1",
            "source": "synthetic:correction",
            "text": "Koreksi: saya bekerja di PT Astra, bukan PT Sinar.",
            "supports": ["bekerja di PT Astra"],
            "contradicts": ["bekerja di PT Sinar"],
        }],
        "required_claims": ["bekerja di PT Astra"],
        "operations": [{"type": "REVISE", "record_id": "r1", "target_id": "r1"}],
    })


def _load_audit_module():
    spec = importlib.util.spec_from_file_location("run_fairness_audit", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _strip_timestamps(value):
    """Drop timestamp-like keys so two runs can be compared for determinism."""
    if isinstance(value, dict):
        return {
            key: _strip_timestamps(item)
            for key, item in value.items()
            if key != "timestamp" and not key.endswith("_at")
        }
    if isinstance(value, list):
        return [_strip_timestamps(item) for item in value]
    return value


def test_fair_text_catches_contradiction_where_plain_text_accepts():
    transition = _contradiction_transition()

    plain = verify(transition, "text")
    fair = verify_text_fair(transition)

    assert plain.decision == "ACCEPT"
    assert fair.decision == "HOLD"
    faithfulness = next(check for check in fair.checks if check.name == "faithfulness")
    assert faithfulness.passed is False
    assert faithfulness.reason_code == "CONTRADICTED_CLAIM"
    assert faithfulness.affected_ids == ["bekerja di Tokopedia", "tinggal di Bandung"]


def test_fair_text_returns_baseline_result_unchanged_without_contradictions():
    transition = _valid_write_transition()

    sentinel = Result(transition.case_id, "ACCEPT", [])

    import membud.fairness as fairness

    original = fairness._verify_claim_sets
    fairness._verify_claim_sets = lambda t: sentinel
    try:
        assert verify_text_fair(transition) is sentinel
    finally:
        fairness._verify_claim_sets = original

    plain = _verify_claim_sets(transition)
    fair = verify_text_fair(transition)
    assert fair.decision == plain.decision == "ACCEPT"
    assert [check.name for check in fair.checks] == [check.name for check in plain.checks]
    assert [check.reason_code for check in fair.checks] == [
        check.reason_code for check in plain.checks
    ]
    assert all(check.passed for check in fair.checks)


def test_fair_text_ignores_contradictions_of_retired_claims():
    transition = _resolved_correction_transition()

    assert verify(transition, "text").decision == "ACCEPT"
    fair = verify_text_fair(transition)
    assert fair.decision == "ACCEPT"
    assert next(check for check in fair.checks if check.name == "faithfulness").reason_code == (
        "FAITHFULNESS_OK"
    )


def test_fair_text_contradiction_overrides_baseline_unsupported_failure():
    transition = _transition({
        "case_id": "FAIR-BOTH-FAIL",
        "schema_version": 2,
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["memiliki kapal pesiar"]},
        ],
        "evidence": [{
            "id": "e1",
            "supports": [],
            "contradicts": ["memiliki kapal pesiar"],
        }],
        "required_claims": ["tinggal di Bandung"],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    })

    plain = verify(transition, "text")
    fair = verify_text_fair(transition)
    assert plain.decision == "HOLD"
    assert next(check for check in plain.checks if check.name == "faithfulness").reason_code == (
        "UNSUPPORTED_CLAIM"
    )
    fairness = next(check for check in fair.checks if check.name == "faithfulness")
    assert fairness.reason_code == "CONTRADICTED_CLAIM"
    assert fairness.affected_ids == ["memiliki kapal pesiar"]


def test_audit_classifies_expected_invalid_cases_without_calling_verifiers(monkeypatch):
    module = _load_audit_module()
    calls = {"verify": [], "text_fair": []}
    original_verify = module.verify
    original_fair = module.verify_text_fair

    def recorded_verify(transition, representation="graph", **kwargs):
        calls["verify"].append((transition.case_id, representation))
        return original_verify(transition, representation, **kwargs)

    def recorded_fair(transition):
        calls["text_fair"].append(transition.case_id)
        return original_fair(transition)

    monkeypatch.setattr(module, "verify", recorded_verify)
    monkeypatch.setattr(module, "verify_text_fair", recorded_fair)

    audit = module.build_audit(MANIFEST_PATH)
    invalid_ids = {
        case["case_id"] for case in audit["cases"] if case["expected_invalid"]
    }
    assert len(invalid_ids) == 10
    called_ids = {case_id for case_id, _ in calls["verify"]} | set(calls["text_fair"])
    assert invalid_ids.isdisjoint(called_ids)
    valid_count = len(audit["cases"]) - len(invalid_ids)
    # text and graph each call verify() once per valid case; fair text once per valid case.
    assert len(calls["verify"]) == 2 * valid_count
    assert len(calls["text_fair"]) == valid_count

    for case in audit["cases"]:
        if case["expected_invalid"]:
            assert case["invalid_input"] is True
            assert {
                case["decisions"][strategy]["decision"] for strategy in STRATEGIES
            } == {"INVALID_INPUT"}
            assert all(
                case["decisions"][strategy]["verified"] is False for strategy in STRATEGIES
            )


def test_audit_output_schema():
    module = _load_audit_module()
    audit = module.build_audit(MANIFEST_PATH)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert audit["audit_id"] == "fairness-audit-v2"
    assert audit["paid_api_calls"] == 0
    assert audit["verifier"] == "rules"
    assert audit["manifest"]["manifest_id"] == manifest["manifest_id"]
    assert audit["manifest"]["sha256"] == hashlib.sha256(
        MANIFEST_PATH.read_bytes()
    ).hexdigest()
    assert audit["manifest"]["cases_sha256"] == manifest["cases_sha256"]
    assert audit["manifest"]["labels_sha256"] == manifest["labels_sha256"]
    assert audit["case_count"] == manifest["case_count"] == 36
    assert set(audit["strategies"]) == set(STRATEGIES)
    assert audit["decision_vocabulary"] == list(DECISIONS)

    for strategy in STRATEGIES:
        summary = audit["per_strategy"][strategy]
        assert set(summary["decision_counts"]) == set(DECISIONS)
        assert sum(summary["decision_counts"].values()) == 36
        agreement = summary["label_agreement"]
        assert agreement["agreed"] + len(agreement["mismatches"]) == agreement["evaluated"]
        assert agreement["evaluated"] == 36
        assert "case_ids" in summary["false_accepts"]
        assert "case_ids" in summary["false_holds"]
        assert summary["valid_acceptance"]["denominator"] == 14
        assert summary["invalid_input_handling"]["expected"] == 10
        assert summary["invalid_input_handling"]["classified_invalid_input"] == 10

    assert set(audit["paired_differences"]) == {"text_vs_text_fair", "text_fair_vs_graph"}
    for differences in audit["paired_differences"].values():
        for difference in differences:
            assert {"case_id", "expected", "decisions"} <= set(difference)
            assert difference["expected"] in DECISIONS
            assert set(difference["decisions"]) == {"text", "text_fair"} or set(
                difference["decisions"]
            ) == {"text_fair", "graph"}

    assert set(audit["breakdown"]) == {"by_split", "by_category"}
    assert set(audit["breakdown"]["by_split"]) == {"dev", "heldout"}
    assert audit["breakdown"]["by_split"]["dev"]["case_count"] == 18
    assert audit["breakdown"]["by_split"]["heldout"]["case_count"] == 18
    assert len(audit["cases"]) == 36
    for case in audit["cases"]:
        assert set(case["decisions"]) == set(STRATEGIES)
        for decision in case["decisions"].values():
            assert decision["decision"] in DECISIONS
    assert isinstance(audit["analysis_note"], str) and audit["analysis_note"].strip()


def test_audit_is_deterministic_across_two_runs(tmp_path):
    module = _load_audit_module()
    first = module.write_audit(MANIFEST_PATH, tmp_path / "run-a")
    second = module.write_audit(MANIFEST_PATH, tmp_path / "run-b")

    assert _strip_timestamps(first) == _strip_timestamps(second)
    assert (tmp_path / "run-a" / "audit.json").read_bytes() == (
        tmp_path / "run-b" / "audit.json"
    ).read_bytes()


def test_committed_artifact_has_graph_and_fair_text_agreeing_on_non_invalid_cases():
    assert ARTIFACT_PATH.exists(), "run scripts/run_fairness_audit.py to create the artifact"
    audit = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    assert audit["audit_id"] == "fairness-audit-v2"
    assert audit["case_count"] == 36
    assert audit["manifest"]["sha256"] == hashlib.sha256(
        MANIFEST_PATH.read_bytes()
    ).hexdigest()

    disagreements = [
        case["case_id"]
        for case in audit["cases"]
        if not case["invalid_input"]
        and case["decisions"]["text_fair"]["decision"] != case["decisions"]["graph"]["decision"]
    ]
    assert disagreements == []
    assert audit["paired_differences"]["text_fair_vs_graph"] == []
    assert audit["per_strategy"]["text_fair"]["decision_counts"] == (
        audit["per_strategy"]["graph"]["decision_counts"]
    )
