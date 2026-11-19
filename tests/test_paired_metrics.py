"""Tests for the paired statistical analysis script and committed report.

Everything here runs offline with no real model cache or paid calls. The script
is imported directly because it is a stdlib-only analysis entry point under
``scripts/`` rather than part of the ``membud`` package.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT_PATH = ROOT / "scripts/analyze_paired_metrics.py"
REPORT_PATH = ROOT / "data/results/statistical-analysis/report.json"
DOCS_PATH = ROOT / "docs/statistical-analysis.md"
MANIFEST_PATH = ROOT / "data/manifests/evaluation-v2.json"
AUDIT_PATH = ROOT / "data/results/fairness-audit-v2/audit.json"
RUN_SUMMARY_PATH = ROOT / "data/results/evaluation-v2/pilot-summary.json"
RUN_METRICS_PATH = ROOT / "data/results/evaluation-v2/metrics.json"
RUN_CONFIG_PATH = ROOT / "data/results/evaluation-v2/run-config.json"
LLM_V1_SUMMARY_PATH = ROOT / "data/results/llm-heldout-v2/summary.json"
LLM_V1_METRICS_PATH = ROOT / "data/results/llm-heldout-v2/metrics.json"
LLM_V2_SUMMARY_PATH = ROOT / "data/results/llm-heldout-v2-prompt-v2/summary.json"
LLM_V2_METRICS_PATH = ROOT / "data/results/llm-heldout-v2-prompt-v2/metrics.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("analyze_paired_metrics", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before execution so dataclasses can resolve string annotations.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_committed(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_payload(payload):
    """Deep-copy a committed payload so a test can mutate it without side effects."""
    return copy.deepcopy(payload)


# ---------------------------------------------------------------------------
# Synthetic small manifest tree (for load_manifest and loader fixtures)
# ---------------------------------------------------------------------------


def _synthetic_manifest_tree(tmp_path: Path, *, expected_invalid: tuple[bool, ...] = (False, False, False)):
    """Create a minimal frozen manifest + cases + labels tree."""
    case_ids = tuple(f"SYN-{index + 1:02d}" for index in range(len(expected_invalid)))
    labels = []
    case_entries = []
    for index, case_id in enumerate(case_ids):
        invalid = expected_invalid[index]
        expected = "INVALID_INPUT" if invalid else ("HOLD" if index % 2 else "ACCEPT")
        labels.append(
            {
                "case_id": case_id,
                "split": "dev",
                "category": "valid_write",
                "expected_decision": expected,
                "expected_invalid": invalid,
            }
        )
        case_entries.append(
            {"case_id": case_id, "split": "dev", "expected_invalid": invalid}
        )
    cases_path = tmp_path / "data" / "cases" / "synthetic.jsonl"
    labels_path = tmp_path / "data" / "labels" / "synthetic.jsonl"
    manifest_path = tmp_path / "data" / "manifests" / "synthetic.json"
    _write_jsonl(cases_path, [{"case_id": case_id} for case_id in case_ids])
    _write_jsonl(labels_path, labels)
    manifest = {
        "manifest_id": "membud-evaluation-v2",  # loaders require the frozen id
        "schema_version": 2,
        "status": "frozen_exploratory_single_reviewer_second_review_pending",
        "review": {"reviewer_count": 1, "second_independent_reviewer": False},
        "cases_file": "../cases/synthetic.jsonl",
        "cases_sha256": _sha(cases_path),
        "labels_file": "../labels/synthetic.jsonl",
        "labels_sha256": _sha(labels_path),
        "case_ids": list(case_ids),
        "case_count": len(case_ids),
        "case_entries": case_entries,
        "expected_decision_counts": {
            expected: sum(
                1 for label in labels if label["expected_decision"] == expected
            )
            for expected in ("ACCEPT", "HOLD", "INVALID_INPUT")
        },
    }
    _write_json(manifest_path, manifest)
    return manifest_path, manifest


def _write_manifest(manifest_path: Path, manifest) -> None:
    _write_json(manifest_path, manifest)


def _audit_payload(manifest, manifest_path: Path, decisions_by_case, strategies):
    """Build a consistent fairness-audit payload from case-level decisions."""
    cases = []
    for case_id in manifest["case_ids"]:
        entry = next(e for e in manifest["case_entries"] if e["case_id"] == case_id)
        labels = {
            row["case_id"]: row
            for row in (
                json.loads(line)
                for line in (manifest_path.parent / manifest["labels_file"]).read_text().splitlines()
                if line
            )
        }
        label = labels[case_id]
        cases.append(
            {
                "case_id": case_id,
                "category": label["category"],
                "expected": label["expected_decision"],
                "expected_invalid": label["expected_invalid"],
                "invalid_input": label["expected_invalid"],
                "invalid_reason": "",
                "split": entry["split"],
                "decisions": {
                    strategy: {
                        "decision": decisions_by_case[case_id][strategy],
                        "reason_codes": [],
                        "verified": (
                            strategy != "unguarded"
                            and decisions_by_case[case_id][strategy] != "INVALID_INPUT"
                        ),
                    }
                    for strategy in strategies
                },
            }
        )
    per_strategy = {}
    for strategy in strategies:
        decision_counts = {name: 0 for name in ("ACCEPT", "HOLD", "INVALID_INPUT")}
        for case in cases:
            decision_counts[case["decisions"][strategy]["decision"]] += 1
        valid = [case for case in cases if case["expected"] == "ACCEPT"]
        faulty = [case for case in cases if case["expected"] == "HOLD"]
        invalid = [case for case in cases if case["expected"] == "INVALID_INPUT"]
        per_strategy[strategy] = {
            "decision_counts": decision_counts,
            "valid_acceptance": {
                "numerator": sum(
                    case["decisions"][strategy]["decision"] == "ACCEPT" for case in valid
                ),
                "denominator": len(valid),
                "case_ids": [
                    case["case_id"]
                    for case in valid
                    if case["decisions"][strategy]["decision"] == "ACCEPT"
                ],
            },
            "false_accepts": {
                "count": sum(
                    case["decisions"][strategy]["decision"] == "ACCEPT" for case in faulty
                ),
                "denominator": len(faulty),
                "case_ids": [
                    case["case_id"]
                    for case in faulty
                    if case["decisions"][strategy]["decision"] == "ACCEPT"
                ],
            },
            "invalid_input_handling": {
                "classified_invalid_input": sum(
                    case["decisions"][strategy]["decision"] == "INVALID_INPUT"
                    for case in invalid
                ),
                "expected": len(invalid),
            },
        }
    return {
        "audit_id": "fairness-audit-v2",
        "paid_api_calls": 0,
        "verifier": "rules",
        "strategies": {strategy: {"kind": "test"} for strategy in strategies},
        "manifest": {
            "manifest_id": manifest["manifest_id"],
            "sha256": _sha(manifest_path),
            "case_count": manifest["case_count"],
            "cases_sha256": manifest["cases_sha256"],
            "labels_sha256": manifest["labels_sha256"],
        },
        "case_count": manifest["case_count"],
        "cases": cases,
        "per_strategy": per_strategy,
    }


def _synthetic_fairness_fixture(tmp_path: Path):
    manifest_path, manifest = _synthetic_manifest_tree(
        tmp_path, expected_invalid=(False, False, False, False, True)
    )
    labels = {
        row["case_id"]: row
        for row in (
            json.loads(line)
            for line in (tmp_path / "data" / "labels" / "synthetic.jsonl").read_text().splitlines()
            if line
        )
    }
    decisions_by_case = {}
    false_accept_case = "SYN-02"
    for case_id in manifest["case_ids"]:
        expected = labels[case_id]["expected_decision"]
        if expected == "INVALID_INPUT":
            decisions_by_case[case_id] = {
                strategy: "INVALID_INPUT"
                for strategy in ("unguarded", "text", "text_fair", "graph")
            }
        elif expected == "ACCEPT":
            decisions_by_case[case_id] = {
                strategy: "ACCEPT"
                for strategy in ("unguarded", "text", "text_fair", "graph")
            }
        else:
            decisions_by_case[case_id] = {
                "unguarded": "ACCEPT",
                "text": "ACCEPT" if case_id == false_accept_case else "HOLD",
                "text_fair": "HOLD",
                "graph": "HOLD",
            }
    audit = _audit_payload(
        manifest,
        manifest_path,
        decisions_by_case,
        ("unguarded", "text", "text_fair", "graph"),
    )
    audit_path = tmp_path / "audit.json"
    _write_json(audit_path, audit)
    return manifest_path, manifest, audit_path, audit, decisions_by_case


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def test_load_manifest_verifies_frozen_hashes(mod):
    manifest = mod.load_manifest(MANIFEST_PATH)
    assert manifest.manifest_id == "membud-evaluation-v2"
    assert manifest.sha256 == _sha(MANIFEST_PATH)
    assert manifest.case_count == 36
    assert manifest.split_counts == {"dev": 18, "heldout": 18}
    assert manifest.expected_decision_counts == {
        "ACCEPT": 14,
        "HOLD": 12,
        "INVALID_INPUT": 10,
    }


def test_load_manifest_refuses_tampered_case_file(tmp_path, mod):
    manifest_path, manifest = _synthetic_manifest_tree(tmp_path)
    cases_path = tmp_path / "data" / "cases" / "synthetic.jsonl"
    cases_path.write_text(cases_path.read_text() + '{"case_id": "tampered"}\n')
    with pytest.raises(mod.DataIntegrityError, match="case file hash"):
        mod.load_manifest(manifest_path)


def test_load_manifest_refuses_tampered_label_file(tmp_path, mod):
    manifest_path, manifest = _synthetic_manifest_tree(tmp_path)
    labels_path = tmp_path / "data" / "labels" / "synthetic.jsonl"
    labels_path.write_text("")
    with pytest.raises(mod.DataIntegrityError, match="label file hash"):
        mod.load_manifest(manifest_path)


# ---------------------------------------------------------------------------
# Artifact loaders (synthetic fixture dir + mutated committed artifacts)
# ---------------------------------------------------------------------------


def test_fairness_loader_builds_explicit_denominators(tmp_path, mod):
    manifest_path, _, audit_path, _, _ = _synthetic_fairness_fixture(tmp_path)
    manifest = mod.load_manifest(manifest_path)
    dataset = mod.load_rules_fairness_dataset(manifest, audit_path)

    assert dataset.dataset_id == "rules-fair-v2"
    assert dataset.conditions == ("unguarded", "text", "text_fair", "graph")
    assert dataset.case_ids == manifest.case_ids
    assert len(dataset.invalid_case_ids) == 1
    assert len(dataset.valid_case_ids) == 2
    assert len(dataset.faulty_case_ids) == 2

    text = mod.condition_reliability(dataset, "text")
    assert text["valid_update_acceptance"]["numerator"] == 2
    assert text["valid_update_acceptance"]["denominator"] == 2
    assert text["faulty_detection_hold_only"]["numerator"] == 1
    assert text["faulty_detection_hold_only"]["denominator"] == 2
    assert text["faulty_detection_hold_only"]["false_accept_case_ids"] == ["SYN-02"]
    assert text["invalid_input_handling"]["numerator"] == 1
    assert text["invalid_input_handling"]["denominator"] == 1

    graph = mod.condition_reliability(dataset, "graph")
    assert graph["faulty_detection_hold_only"]["numerator"] == 2
    assert graph["overall_accuracy_non_invalid"]["numerator"] == 4
    assert graph["overall_accuracy_non_invalid"]["denominator"] == 4

    # Invalid cases are explicit exclusions with a recorded reason.
    assert [item["case_id"] for item in dataset.excluded] == ["SYN-05"]
    assert dataset.excluded[0]["reason"] == "expected_invalid_schema_construction_failure"
    assert dataset.validation["case_condition_rows"] == 20


def test_fairness_loader_refuses_wrong_manifest_hash(tmp_path, mod):
    manifest_path, manifest, audit_path, audit, _ = _synthetic_fairness_fixture(tmp_path)
    audit["manifest"]["sha256"] = "0" * 64
    _write_json(audit_path, audit)
    manifest_obj = mod.load_manifest(manifest_path)
    with pytest.raises(mod.DataIntegrityError, match="manifest hash mismatch"):
        mod.load_rules_fairness_dataset(manifest_obj, audit_path)


def test_fairness_loader_refuses_missing_case(tmp_path, mod):
    manifest_path, _, audit_path, audit, _ = _synthetic_fairness_fixture(tmp_path)
    audit["cases"] = audit["cases"][:-1]
    _write_json(audit_path, audit)
    manifest_obj = mod.load_manifest(manifest_path)
    with pytest.raises(mod.DataIntegrityError, match="case IDs/order"):
        mod.load_rules_fairness_dataset(manifest_obj, audit_path)


def test_fairness_loader_refuses_unknown_strategy(tmp_path, mod):
    manifest_path, _, audit_path, audit, _ = _synthetic_fairness_fixture(tmp_path)
    audit["strategies"]["extra"] = {"kind": "test"}
    _write_json(audit_path, audit)
    manifest_obj = mod.load_manifest(manifest_path)
    with pytest.raises(mod.DataIntegrityError, match="strategies"):
        mod.load_rules_fairness_dataset(manifest_obj, audit_path)


def test_fairness_loader_refuses_invalid_input_on_expected_valid_case(tmp_path, mod):
    manifest_path, _, audit_path, audit, _ = _synthetic_fairness_fixture(tmp_path)
    audit["cases"][0]["decisions"]["graph"]["decision"] = "INVALID_INPUT"
    _write_json(audit_path, audit)
    manifest_obj = mod.load_manifest(manifest_path)
    with pytest.raises(mod.DataIntegrityError, match="INVALID_INPUT/unknown decision"):
        mod.load_rules_fairness_dataset(manifest_obj, audit_path)


def test_fairness_loader_refuses_missing_invalid_input_row(tmp_path, mod):
    manifest_path, _, audit_path, audit, _ = _synthetic_fairness_fixture(tmp_path)
    audit["cases"][4]["decisions"]["text"]["decision"] = "HOLD"
    _write_json(audit_path, audit)
    manifest_obj = mod.load_manifest(manifest_path)
    with pytest.raises(mod.DataIntegrityError, match="was not recorded as INVALID_INPUT"):
        mod.load_rules_fairness_dataset(manifest_obj, audit_path)


def test_fairness_loader_refuses_stale_aggregate(tmp_path, mod):
    manifest_path, _, audit_path, audit, _ = _synthetic_fairness_fixture(tmp_path)
    audit["per_strategy"]["graph"]["decision_counts"]["ACCEPT"] += 1
    _write_json(audit_path, audit)
    manifest_obj = mod.load_manifest(manifest_path)
    with pytest.raises(mod.DataIntegrityError, match="decision counts"):
        mod.load_rules_fairness_dataset(manifest_obj, audit_path)


def test_evaluation_run_loader_refuses_duplicate_row(tmp_path, mod):
    manifest = mod.load_manifest(MANIFEST_PATH)
    summary = _copy_payload(_load_committed(RUN_SUMMARY_PATH))
    summary["results"].append(copy.deepcopy(summary["results"][0]))
    summary_path = tmp_path / "summary.json"
    _write_json(summary_path, summary)
    with pytest.raises(mod.DataIntegrityError, match="duplicate row"):
        mod.load_rules_run_dataset(
            manifest, summary_path, RUN_METRICS_PATH, RUN_CONFIG_PATH
        )


def test_evaluation_run_loader_refuses_missing_row(tmp_path, mod):
    manifest = mod.load_manifest(MANIFEST_PATH)
    summary = _copy_payload(_load_committed(RUN_SUMMARY_PATH))
    summary["results"] = summary["results"][:-1]
    summary_path = tmp_path / "summary.json"
    _write_json(summary_path, summary)
    with pytest.raises(mod.DataIntegrityError, match="missing rows"):
        mod.load_rules_run_dataset(
            manifest, summary_path, RUN_METRICS_PATH, RUN_CONFIG_PATH
        )


def test_evaluation_run_loader_refuses_metrics_mismatch(tmp_path, mod):
    manifest = mod.load_manifest(MANIFEST_PATH)
    metrics = _copy_payload(_load_committed(RUN_METRICS_PATH))
    metrics["condition_metrics"]["graph"]["decision_counts"]["HOLD"] += 1
    metrics_path = tmp_path / "metrics.json"
    _write_json(metrics_path, metrics)
    with pytest.raises(mod.DataIntegrityError, match="decision counts"):
        mod.load_rules_run_dataset(
            manifest, RUN_SUMMARY_PATH, metrics_path, RUN_CONFIG_PATH
        )


def test_llm_loader_refuses_prompt_version_mismatch(tmp_path, mod):
    manifest = mod.load_manifest(MANIFEST_PATH)
    with pytest.raises(mod.DataIntegrityError, match="LLM prompt version"):
        mod.load_llm_dataset(
            manifest,
            dataset_id="synthetic",
            label="synthetic",
            summary_path=LLM_V1_SUMMARY_PATH,
            metrics_path=LLM_V1_METRICS_PATH,
            expected_prompt_version="membud-transition-checks-v2",
            notes=(),
        )


def test_llm_loader_refuses_missing_invalid_row(tmp_path, mod):
    manifest = mod.load_manifest(MANIFEST_PATH)
    summary = _copy_payload(_load_committed(LLM_V1_SUMMARY_PATH))
    summary["results"] = summary["results"][:-1]
    summary_path = tmp_path / "summary.json"
    _write_json(summary_path, summary)
    with pytest.raises(mod.DataIntegrityError, match="missing rows"):
        mod.load_llm_dataset(
            manifest,
            dataset_id="synthetic",
            label="synthetic",
            summary_path=summary_path,
            metrics_path=LLM_V1_METRICS_PATH,
            expected_prompt_version="membud-transition-checks-v1",
            notes=(),
        )


def test_llm_loader_refuses_metrics_mismatch(tmp_path, mod):
    manifest = mod.load_manifest(MANIFEST_PATH)
    metrics = _copy_payload(_load_committed(LLM_V1_METRICS_PATH))
    metrics["condition_metrics"]["graph"]["valid_acceptance"]["numerator"] += 1
    metrics_path = tmp_path / "metrics.json"
    _write_json(metrics_path, metrics)
    with pytest.raises(mod.DataIntegrityError, match="valid acceptance"):
        mod.load_llm_dataset(
            manifest,
            dataset_id="synthetic",
            label="synthetic",
            summary_path=LLM_V1_SUMMARY_PATH,
            metrics_path=metrics_path,
            expected_prompt_version="membud-transition-checks-v1",
            notes=(),
        )


# ---------------------------------------------------------------------------
# Dataset compatibility (refuse to mix mismatched manifests/configurations)
# ---------------------------------------------------------------------------


def _synthetic_dataset(mod, *, dataset_id: str, manifest_sha: str = "a" * 64, config_id: str = "synthetic:config", expected=("ACCEPT", "HOLD", "INVALID_INPUT")):
    decisions = {
        "a": {"ACCEPT": "ACCEPT", "HOLD": "HOLD", "INVALID_INPUT": "INVALID_INPUT"},
        "b": {"ACCEPT": "ACCEPT", "HOLD": "HOLD", "INVALID_INPUT": "INVALID_INPUT"},
    }
    cases = tuple(
        mod.CaseObservation(
            case_id=f"CASE-{index + 1}",
            split="dev",
            category="valid_write",
            expected=expected_value,
            decisions={condition: decisions[condition][expected_value] for condition in ("a", "b")},
            raw_decisions={condition: decisions[condition][expected_value] for condition in ("a", "b")},
            statuses={
                condition: (
                    "invalid_input" if expected_value == "INVALID_INPUT" else "completed"
                )
                for condition in ("a", "b")
            },
        )
        for index, expected_value in enumerate(expected)
    )
    return mod.Dataset(
        dataset_id=dataset_id,
        label=dataset_id,
        kind="synthetic",
        conditions=("a", "b"),
        cases=cases,
        manifest_id="toy-manifest",
        manifest_sha256=manifest_sha,
        sources=(),
        config={},
        config_id=config_id,
        notes=(),
        excluded=(),
        validation={},
    )


def test_require_shares_manifest_refuses_different_manifest_hashes(mod):
    left = _synthetic_dataset(mod, dataset_id="left")
    right = _synthetic_dataset(mod, dataset_id="right", manifest_sha="b" * 64)
    with pytest.raises(mod.DataIntegrityError, match="different manifest hashes"):
        mod.require_shared_manifest(left, right)


def test_require_compatible_refuses_different_configs(mod):
    left = _synthetic_dataset(mod, dataset_id="left", config_id="config-a")
    right = _synthetic_dataset(mod, dataset_id="right", config_id="config-b")
    with pytest.raises(mod.DataIntegrityError, match="different configurations"):
        mod.require_compatible_datasets(left, right)
    # Explicitly allowing different configs still requires the same case set.
    mod.require_compatible_datasets(left, right, require_same_config=False)


# ---------------------------------------------------------------------------
# Statistical primitives
# ---------------------------------------------------------------------------


def test_percentile_linear_interpolation(mod):
    assert mod.percentile([0.0, 1.0, 2.0], 0.0) == 0.0
    assert mod.percentile([0.0, 1.0, 2.0], 0.5) == 1.0
    assert mod.percentile([0.0, 1.0, 2.0], 1.0) == 2.0
    assert mod.percentile([0.0, 1.0, 2.0, 3.0], 0.5) == pytest.approx(1.5)


def test_wilson_interval_bounds_and_degenerate_cases(mod):
    interval = mod.wilson_interval(5, 10)
    assert interval["method"] == "wilson_score_95"
    assert 0.0 <= interval["low"] < 0.5 < interval["high"] <= 1.0
    full = mod.wilson_interval(14, 14)
    assert full["low"] > 0.75 and full["high"] == 1.0
    zero = mod.wilson_interval(0, 14)
    assert zero["low"] == 0.0 and zero["high"] < 0.25
    assert mod.wilson_interval(0, 0) == {
        "method": "wilson_score_95",
        "low": None,
        "high": None,
    }


def test_mcnemar_exact_known_values(mod):
    assert mod.mcnemar_exact(0, 0)["p_value"] == 1.0
    assert mod.mcnemar_exact(10, 0)["p_value"] == pytest.approx(2 / 1024, abs=1e-6)
    assert mod.mcnemar_exact(0, 10)["p_value"] == pytest.approx(2 / 1024, abs=1e-6)
    assert mod.mcnemar_exact(2, 0)["p_value"] == pytest.approx(0.5)
    assert mod.mcnemar_exact(5, 5)["p_value"] == pytest.approx(1.0)
    assert mod.mcnemar_exact(1, 4)["p_value"] == pytest.approx(0.375)
    # Symmetric under swapping the two conditions.
    assert mod.mcnemar_exact(3, 7)["p_value"] == mod.mcnemar_exact(7, 3)["p_value"]


def test_bootstrap_paired_difference_is_deterministic(mod):
    a = [1, 1, 0, 1, 0, 1, 1, 0]
    b = [0, 1, 1, 0, 0, 1, 0, 1]
    first = mod.bootstrap_paired_difference(a, b, resamples=500, seed=7, key="k")
    second = mod.bootstrap_paired_difference(a, b, resamples=500, seed=7, key="k")
    assert first == second
    assert first["n_pairs"] == 8
    assert first["observed_difference"] == pytest.approx(0.125)
    assert first["ci_low"] <= first["observed_difference"] <= first["ci_high"]
    assert -1.0 <= first["ci_low"] <= first["ci_high"] <= 1.0
    # A different key derives a different RNG seed.
    other = mod.bootstrap_paired_difference(a, b, resamples=50, seed=7, key="other")
    assert other["seed_key"] == "other"


def test_bootstrap_paired_difference_degenerate_cases(mod):
    identical = mod.bootstrap_paired_difference(
        [1, 1, 1], [1, 1, 1], resamples=100, seed=1, key="same"
    )
    assert identical["observed_difference"] == 0.0
    assert identical["ci_low"] == identical["ci_high"] == 0.0
    empty = mod.bootstrap_paired_difference([], [], resamples=100, seed=1, key="empty")
    assert empty["ci_low"] is None and empty["ci_high"] is None
    single = mod.bootstrap_paired_difference([1], [0], resamples=100, seed=1, key="one")
    assert single["ci_low"] is None and single["ci_high"] is None


def test_bootstrap_paired_difference_refuses_unaligned_vectors(mod):
    with pytest.raises(mod.DataIntegrityError, match="aligned"):
        mod.bootstrap_paired_difference([1, 0], [1], resamples=10, seed=1, key="bad")


def test_compare_binary_outcomes_math(mod):
    case_ids = ["c1", "c2", "c3", "c4"]
    a = {"c1": 1, "c2": 1, "c3": 0, "c4": 0}
    b = {"c1": 1, "c2": 0, "c3": 1, "c4": 0}
    result = mod.compare_binary_outcomes(
        case_ids, a, b, resamples=200, seed=3, key="cmp", a_label="A", b_label="B"
    )
    assert result["n_paired"] == 4
    assert result["a"]["numerator"] == 2
    assert result["b"]["numerator"] == 2
    assert result["difference"] == 0.0
    assert result["discordant_counts"] == {
        "a_correct_b_wrong": 1,
        "b_correct_a_wrong": 1,
        "total": 2,
    }
    assert result["discordant_case_ids"] == ["c2", "c3"]
    assert result["mcnemar"]["p_value"] == pytest.approx(1.0)


def test_compare_binary_outcomes_refuses_missing_case(mod):
    with pytest.raises(mod.DataIntegrityError, match="missing outcomes"):
        mod.compare_binary_outcomes(
            ["c1", "c2"],
            {"c1": 1},
            {"c1": 1, "c2": 0},
            resamples=10,
            seed=1,
            key="missing",
            a_label="A",
            b_label="B",
        )


# ---------------------------------------------------------------------------
# Condition metrics, invalid-input handling, paired comparisons
# ---------------------------------------------------------------------------


def test_condition_metrics_keep_invalid_input_explicit(mod):
    dataset = _synthetic_dataset(
        mod,
        dataset_id="invalid-demo",
        expected=("ACCEPT", "ACCEPT", "HOLD", "INVALID_INPUT"),
    )
    metrics = mod.condition_reliability(dataset, "a")
    assert metrics["valid_update_acceptance"]["denominator"] == 2
    assert metrics["faulty_detection_hold_only"]["denominator"] == 1
    assert metrics["invalid_input_handling"] == {
        "numerator": 1,
        "denominator": 1,
        "rate": 1.0,
        "ci": mod.wilson_interval(1, 1),
        "case_ids": ["CASE-4"],
        "cases_not_recorded_invalid": [],
        "note": (
            "Expected-invalid cases fail schema construction before any verifier runs; "
            "this is handled-explicitly accounting, not verifier detection."
        ),
    }
    assert metrics["execution_errors"]["numerator"] == 0
    assert metrics["decision_counts"]["INVALID_INPUT"] == 1


def test_paired_comparisons_only_pair_cases_within_dataset(mod):
    dataset = _synthetic_dataset(
        mod,
        dataset_id="pair-demo",
        expected=("ACCEPT", "HOLD", "INVALID_INPUT"),
    )
    comparisons = mod.paired_comparisons(dataset, resamples=100, seed=5)
    assert len(comparisons) == 3  # one condition pair x three outcomes
    invalid_id = "CASE-3"
    for comparison in comparisons:
        assert comparison["dataset_id"] == "pair-demo"
        assert comparison["a_condition"] == "a"
        assert comparison["b_condition"] == "b"
        assert invalid_id not in comparison["discordant_case_ids"]
        assert comparison["mcnemar"]["discordant_pairs"] == 0


# ---------------------------------------------------------------------------
# Report construction and committed artifacts
# ---------------------------------------------------------------------------


def test_build_report_is_deterministic_and_well_formed(mod):
    first = mod.build_report(resamples=300, seed=11)
    second = mod.build_report(resamples=300, seed=11)
    assert mod.serialize_report(first) == mod.serialize_report(second)
    assert first["no_network"] is True
    assert first["paid_api_calls"] == 0
    assert first["new_model_runs"] == 0
    assert first["manifest"]["sha256"] == _sha(MANIFEST_PATH)
    assert set(first["datasets"]) == {
        "rules-fair-v2",
        "rules-v2-run",
        "llm-heldout-prompt-v1",
        "llm-heldout-prompt-v2",
    }
    config_ids = [dataset["config_id"] for dataset in first["datasets"].values()]
    assert len(set(config_ids)) == len(config_ids)
    for dataset in first["datasets"].values():
        assert dataset["case_count"] in {36, 18}
        assert dataset["seed"] == 11
        assert dataset["resamples"] == 300
        for condition in dataset["conditions"]:
            metrics = dataset["condition_metrics"][condition]
            assert metrics["valid_update_acceptance"]["denominator"] >= 0
            assert metrics["faulty_detection_hold_only"]["denominator"] >= 0
            assert metrics["invalid_input_handling"]["denominator"] in {5, 10}
        for comparison in dataset["paired_comparisons"]:
            assert comparison["a_condition"] in dataset["conditions"]
            assert comparison["b_condition"] in dataset["conditions"]
            assert comparison["a_condition"] != comparison["b_condition"]
    rationale = first["sample_size_rationale"]
    assert len(rationale["candidate_n_half_width_table"]) == len(mod.CANDIDATE_SAMPLE_SIZES)
    assert len(rationale["required_n_for_target_half_width"]) == len(mod.TARGET_HALF_WIDTHS)
    assert rationale["current_sample_sizes"]["heldout_llm_schema_valid_cases"] == 13
    assert rationale["current_sample_sizes"]["rules_non_invalid_cases"] == 26
    assert first["cross_checks"]["rules_artifacts"]["mismatches"] == []
    assert any("single-reviewer" in limitation for limitation in first["limitations"])
    assert "no superiority" in first["interpretation"].lower()


def test_rules_cross_check_reproduces_committed_paired_differences(mod):
    report = mod.build_report(resamples=50, seed=1)
    cross = report["cross_checks"]["rules_artifacts"]
    assert cross["status"] == "match"
    assert cross["compared_case_condition_rows"] == 108
    assert cross["mismatches"] == []
    assert cross["committed_metrics_graph_vs_text_differences"] == ["DEV-13", "HELD-13"]


def test_committed_report_and_docs_exist_and_match_rebuild(mod):
    assert REPORT_PATH.exists(), (
        "run scripts/analyze_paired_metrics.py to create data/results/statistical-analysis/report.json"
    )
    assert DOCS_PATH.exists(), (
        "run scripts/analyze_paired_metrics.py to create docs/statistical-analysis.md"
    )
    report = mod.build_report()
    serialized = mod.serialize_report(report)
    expected_sha = hashlib.sha256(serialized).hexdigest()
    assert REPORT_PATH.read_bytes() == serialized
    docs = DOCS_PATH.read_text(encoding="utf-8")
    assert expected_sha in docs
    assert "## Method" in docs
    assert "## Limitations (read before citing any number)" in docs
    assert "sample-size" in docs.lower()
    committed = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert committed["analysis_id"] == mod.ANALYSIS_ID
    assert committed["resamples"] == mod.DEFAULT_RESAMPLES
    assert committed["seed"] == mod.DEFAULT_SEED


def test_check_mode_accepts_committed_artifacts(mod, capsys):
    assert mod.main(["--check"]) == 0
    captured = capsys.readouterr()
    assert "reproducible" in captured.out


def test_check_mode_reports_stale_artifacts(tmp_path, mod):
    report = mod.build_report(resamples=20, seed=2)
    ok, problems = mod.check_outputs(
        report, output_path=tmp_path / "report.json", docs_path=tmp_path / "docs.md"
    )
    assert ok is False
    assert any("missing report" in problem for problem in problems)
    assert any("missing analysis" in problem for problem in problems)


def test_source_hashes_in_report_match_files(mod):
    report = mod.build_report(resamples=20, seed=2)
    for dataset in report["datasets"].values():
        for source in dataset["sources"]:
            path = ROOT / source["path"]
            if not path.exists():
                continue  # synthetic-role entries always exist; defensive only
            assert _sha(path) == source["sha256"], source["path"]


# ---------------------------------------------------------------------------
# No-network / stdlib-only guarantees
# ---------------------------------------------------------------------------


def test_script_imports_are_stdlib_only_and_offline():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    module_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_names.add(node.module.split(".")[0])
    allowed = {
        "argparse",
        "hashlib",
        "json",
        "math",
        "random",
        "sys",
        "dataclasses",
        "pathlib",
        "typing",
        "__future__",
    }
    assert module_names <= allowed
    forbidden = {"requests", "httpx", "urllib", "socket", "http", "openai", "aiohttp"}
    assert module_names.isdisjoint(forbidden)


def test_report_declares_artifact_only_scope(mod):
    report = mod.build_report(resamples=20, seed=2)
    assert report["no_network"] is True
    assert report["paid_api_calls"] == 0
    assert report["new_model_runs"] == 0
    assert "No network I/O" in report["scope"]
