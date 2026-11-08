"""Paired statistical analysis of committed MemBud evaluation artifacts.

Why this script exists
----------------------
``PLAN.md`` §8.4/§9 and ``TODO.md`` "Next up" #2 require paired case-level
analysis with bootstrap confidence intervals and a sample-size/precision
rationale before any better-powered independent evaluation is attempted. This
script delivers that analysis using **only already-committed artifacts**:

* ``data/results/fairness-audit-v2/audit.json`` -- deterministic rules-only
  audit with four conditions on the frozen v2 manifest (36 cases);
* ``data/results/evaluation-v2/`` -- the committed rules-only three-condition
  run (``pilot-summary.json``, ``metrics.json``, ``run-config.json``), used for
  paired analysis and as an independent cross-check of the audit;
* ``data/results/llm-heldout-v2/summary.json`` + ``metrics.json`` -- held-out
  LLM/semantic run under prompt ``membud-transition-checks-v1``;
* ``data/results/llm-heldout-v2-prompt-v2/summary.json`` + ``metrics.json`` --
  held-out replay under the promoted prompt ``v2``.

No network I/O, no model calls, no cache reads, and no writes outside the
statistical-analysis output directory plus the analysis document. Everything is
stdlib-only and deterministic.

What it computes
----------------
For each committed dataset (a manifest + configuration + case set) it builds
paired case observations and reports:

* per-condition reliability metrics with explicit numerators/denominators:
  valid-update acceptance (false holds), faulty detection / recall (false
  accepts), combined safe rejection, expected-``INVALID_INPUT`` handling, and
  case-level accuracy. ``INVALID_INPUT`` is a schema-construction outcome, not
  a verifier decision: expected-invalid cases are counted explicitly and are
  excluded from paired verifier-decision comparisons with a recorded reason.
* paired differences between conditions with percentile bootstrap confidence
  intervals resampling **cases** (paired, fixed seed, default 10,000 resamples)
  for overall accuracy, faulty detection and valid acceptance, plus discordant
  pair counts and an exact two-sided McNemar/binomial test.
* a sample-size / precision rationale table over candidate ``n``, and the
  ``n`` needed for target CI half-widths, explicitly labelled as planning
  guidance rather than a power guarantee.

Integrity rules
---------------
The loaders refuse to proceed when artifacts disagree about the manifest
identity/hash, the case set, the configuration (run id, verifier, model,
prompt versions, split) or the expected labels, or when duplicate/missing
case-condition rows would otherwise be silently dropped. Committed artifacts
are also cross-checked against their own committed ``metrics.json`` aggregates.

Usage::

    TMPDIR=$PWD/.tmp-stats PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
        .venv/bin/python scripts/analyze_paired_metrics.py

    # Rebuild in memory and verify the committed report/docs are byte-identical
    ... scripts/analyze_paired_metrics.py --check

The report and document contain no timestamps or absolute paths, so two runs
are byte-identical (proven by ``--check`` and by the test suite).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]

MANIFEST_PATH = ROOT / "data/manifests/evaluation-v2.json"
CASES_PATH = ROOT / "data/cases/evaluation-v2.jsonl"
LABELS_PATH = ROOT / "data/labels/evaluation-v2.jsonl"

RULES_AUDIT_PATH = ROOT / "data/results/fairness-audit-v2/audit.json"
RULES_RUN_SUMMARY_PATH = ROOT / "data/results/evaluation-v2/pilot-summary.json"
RULES_RUN_METRICS_PATH = ROOT / "data/results/evaluation-v2/metrics.json"
RULES_RUN_CONFIG_PATH = ROOT / "data/results/evaluation-v2/run-config.json"

LLM_V1_SUMMARY_PATH = ROOT / "data/results/llm-heldout-v2/summary.json"
LLM_V1_METRICS_PATH = ROOT / "data/results/llm-heldout-v2/metrics.json"
LLM_V2_SUMMARY_PATH = ROOT / "data/results/llm-heldout-v2-prompt-v2/summary.json"
LLM_V2_METRICS_PATH = ROOT / "data/results/llm-heldout-v2-prompt-v2/metrics.json"

DEFAULT_OUTPUT_PATH = ROOT / "data/results/statistical-analysis/report.json"
DEFAULT_DOCS_PATH = ROOT / "docs/statistical-analysis.md"

ANALYSIS_ID = "paired-statistical-analysis-v1"
DEFAULT_SEED = 20261108
DEFAULT_RESAMPLES = 10_000
CONFIDENCE_LEVEL = 0.95
Z_95 = 1.959963984540054

DECISIONS = ("ACCEPT", "HOLD", "INVALID_INPUT")
EXPECTED_DECISIONS = ("ACCEPT", "HOLD", "INVALID_INPUT")
RULES_FAIR_CONDITIONS = ("unguarded", "text", "text_fair", "graph")
RULES_RUN_CONDITIONS = ("unguarded", "graph", "text")
LLM_CONDITIONS = ("graph", "text", "semantic")
LLM_SEMANTIC_PROMPT = "membud-semantic-compare-v1"
STATUSES = ("completed", "invalid_input")
ERROR_STATUSES = ("error", "verifier_error")

DEFAULT_OUTCOME_METRICS = ("overall_accuracy", "faulty_detection", "valid_acceptance")
OBSERVED_STRATUM = {
    "overall_accuracy": "non_invalid",
    "faulty_detection": "faulty",
    "valid_acceptance": "valid",
}

PLANNING_DISCORDANCE_RATES = (0.05, 0.10, 0.20, 0.30)
CANDIDATE_SAMPLE_SIZES = (13, 18, 26, 36, 50, 75, 100, 150, 200, 300, 500, 1000)
TARGET_HALF_WIDTHS = (0.20, 0.10, 0.05, 0.025)

REPO_PREFIX = "repo:"


class DataIntegrityError(ValueError):
    """Raised when committed artifacts cannot be safely paired/compared."""


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        raise DataIntegrityError(f"missing committed artifact: {_relative(path)}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return REPO_PREFIX + resolved.as_posix().replace(str(ROOT.resolve()) + "/", "")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DataIntegrityError(message)


def _source(path: Path) -> dict[str, Any]:
    return {"path": _relative(path), "sha256": sha256_file(path)}


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": (numerator / denominator) if denominator else None,
    }


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def wilson_interval(numerator: int, denominator: int, z: float = Z_95) -> dict[str, Any]:
    """Wilson score interval for a binomial proportion (explicit, deterministic)."""
    if denominator <= 0:
        return {"method": "wilson_score_95", "low": None, "high": None}
    p = numerator / denominator
    n = denominator
    denominator_term = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denominator_term
    margin = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denominator_term
    return {
        "method": "wilson_score_95",
        "low": _round(max(0.0, center - margin)),
        "high": _round(min(1.0, center + margin)),
    }


def percentile(sorted_values: Sequence[float], quantile: float) -> float:
    """Linear-interpolation percentile on an already-sorted sequence.

    Uses the numpy-style "linear" definition: position ``(n - 1) * q`` with
    interpolation between adjacent order statistics. Documented because the
    bootstrap bounds must be reproducible and testable.
    """
    if not sorted_values:
        raise ValueError("percentile of empty sequence")
    if quantile <= 0:
        return float(sorted_values[0])
    if quantile >= 1:
        return float(sorted_values[-1])
    position = (len(sorted_values) - 1) * quantile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return float(sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction)


def _seed_for(seed: int, key: str) -> int:
    """Stable per-comparison RNG seed derived from the base seed and a key."""
    digest = hashlib.sha256(f"{seed}|{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Manifest:
    manifest_id: str
    sha256: str
    schema_version: int
    case_ids: tuple[str, ...]
    split_by_id: Mapping[str, str]
    category_by_id: Mapping[str, str]
    expected_by_id: Mapping[str, str]
    case_count: int
    split_counts: Mapping[str, int]
    expected_decision_counts: Mapping[str, int]
    review: Mapping[str, Any]
    status: str
    cases_sha256: str
    labels_sha256: str


def load_manifest(manifest_path: Path = MANIFEST_PATH) -> Manifest:
    """Load and hash-verify the frozen manifest, cases and labels."""
    payload = read_json(manifest_path)
    actual_sha = sha256_file(manifest_path)
    _require(
        payload.get("manifest_id") == "membud-evaluation-v2",
        f"unexpected manifest_id in {_relative(manifest_path)}",
    )
    cases_path = (manifest_path.parent / payload["cases_file"]).resolve()
    labels_path = (manifest_path.parent / payload["labels_file"]).resolve()
    cases_sha = sha256_file(cases_path)
    labels_sha = sha256_file(labels_path)
    _require(
        cases_sha == payload["cases_sha256"],
        "case file hash does not match manifest (cases changed after freeze)",
    )
    _require(
        labels_sha == payload["labels_sha256"],
        "label file hash does not match manifest (labels changed after freeze)",
    )
    case_ids = tuple(payload["case_ids"])
    entries = payload.get("case_entries")
    _require(entries is not None, "manifest has no case_entries split metadata")
    assert entries is not None
    entry_ids = tuple(entry["case_id"] for entry in entries)
    _require(entry_ids == case_ids, "manifest case_entries do not match case_ids")
    split_by_id = {entry["case_id"]: entry["split"] for entry in entries}
    expected_invalid_by_id = {
        entry["case_id"]: bool(entry.get("expected_invalid", False)) for entry in entries
    }
    labels = {row["case_id"]: row for row in read_jsonl(labels_path)}
    _require(set(labels) == set(case_ids), "label case IDs do not match manifest")
    _require(
        len(labels) == len(case_ids),
        "label file has duplicate or missing case IDs",
    )
    for case_id in case_ids:
        label = labels[case_id]
        _require(
            label.get("split") == split_by_id[case_id],
            f"label split mismatch for {case_id}",
        )
        _require(
            bool(label.get("expected_invalid")) == expected_invalid_by_id[case_id],
            f"manifest expected_invalid mismatch for {case_id}",
        )
        _require(
            label.get("expected_decision") in EXPECTED_DECISIONS,
            f"invalid expected_decision for {case_id}",
        )
        _require(
            (label["expected_decision"] == "INVALID_INPUT")
            == expected_invalid_by_id[case_id],
            f"expected_invalid flag and expected_decision disagree for {case_id}",
        )
    expected_counts: dict[str, int] = {name: 0 for name in EXPECTED_DECISIONS}
    for case_id in case_ids:
        expected = labels[case_id]["expected_decision"]
        expected_counts[expected] += 1
    declared_counts = payload.get("expected_decision_counts")
    if declared_counts:
        _require(
            {name: int(declared_counts.get(name, 0)) for name in EXPECTED_DECISIONS}
            == expected_counts,
            "manifest expected_decision_counts do not match labels",
        )
    split_counts = {
        split: sum(1 for value in split_by_id.values() if value == split)
        for split in sorted(set(split_by_id.values()))
    }
    _require(
        payload.get("case_count") == len(case_ids),
        "manifest case_count does not match case_ids",
    )
    review = payload.get("review") or {}
    return Manifest(
        manifest_id=payload["manifest_id"],
        sha256=actual_sha,
        schema_version=payload.get("schema_version", 1),
        case_ids=case_ids,
        split_by_id=split_by_id,
        category_by_id={case_id: labels[case_id].get("category", "unlabelled") for case_id in case_ids},
        expected_by_id={case_id: labels[case_id]["expected_decision"] for case_id in case_ids},
        case_count=len(case_ids),
        split_counts=split_counts,
        expected_decision_counts=expected_counts,
        review=review,
        status=payload.get("status", "unknown"),
        cases_sha256=cases_sha,
        labels_sha256=labels_sha,
    )


# ---------------------------------------------------------------------------
# Paired case observations and datasets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseObservation:
    case_id: str
    split: str
    category: str
    expected: str
    decisions: Mapping[str, str]
    raw_decisions: Mapping[str, str]
    statuses: Mapping[str, str]

    @property
    def invalid(self) -> bool:
        return self.expected == "INVALID_INPUT"


@dataclass(frozen=True)
class Dataset:
    """One committed run: a manifest, a configuration and paired case decisions."""

    dataset_id: str
    label: str
    kind: str
    conditions: tuple[str, ...]
    cases: tuple[CaseObservation, ...]
    manifest_id: str
    manifest_sha256: str
    sources: tuple[dict[str, Any], ...]
    config: Mapping[str, Any]
    config_id: str
    notes: tuple[str, ...]
    excluded: tuple[dict[str, Any], ...]
    validation: Mapping[str, Any]

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)

    @property
    def case_by_id(self) -> dict[str, CaseObservation]:
        return {case.case_id: case for case in self.cases}

    @property
    def expected_by_id(self) -> dict[str, str]:
        return {case.case_id: case.expected for case in self.cases}

    def ids_with_expected(self, expected: str) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases if case.expected == expected)

    @property
    def valid_case_ids(self) -> tuple[str, ...]:
        return self.ids_with_expected("ACCEPT")

    @property
    def faulty_case_ids(self) -> tuple[str, ...]:
        return self.ids_with_expected("HOLD")

    @property
    def invalid_case_ids(self) -> tuple[str, ...]:
        return self.ids_with_expected("INVALID_INPUT")

    @property
    def non_invalid_case_ids(self) -> tuple[str, ...]:
        return tuple(
            case.case_id for case in self.cases if case.expected != "INVALID_INPUT"
        )

    def decisions(self, condition: str) -> dict[str, str]:
        _require(
            condition in self.conditions,
            f"condition {condition!r} is not part of dataset {self.dataset_id}",
        )
        return {case.case_id: case.decisions[condition] for case in self.cases}

    def statuses(self, condition: str) -> dict[str, str]:
        return {case.case_id: case.statuses[condition] for case in self.cases}


def require_shared_manifest(left: Dataset, right: Dataset) -> None:
    """Refuse to co-analyze artifacts from different manifests/labels.

    Unlike :func:`require_compatible_datasets` this permits one dataset to cover
    a strict subset of the manifest (for example the held-out split) while
    still refusing different manifests, different label sets, or conflicting
    labels for an overlapping case. Datasets are still analyzed separately.
    """
    _require(
        left.manifest_id == right.manifest_id,
        f"cannot mix datasets with different manifest_id: {left.manifest_id} vs {right.manifest_id}",
    )
    _require(
        left.manifest_sha256 == right.manifest_sha256,
        "cannot mix datasets with different manifest hashes",
    )
    left_by_id = left.case_by_id
    right_by_id = right.case_by_id
    overlaps = set(left_by_id) & set(right_by_id)
    _require(bool(overlaps), "datasets share no cases to validate")
    for case_id in sorted(overlaps):
        _require(
            left_by_id[case_id].expected == right_by_id[case_id].expected,
            f"datasets disagree about the expected label for {case_id}",
        )
        _require(
            left_by_id[case_id].split == right_by_id[case_id].split,
            f"datasets disagree about the split for {case_id}",
        )
        _require(
            left_by_id[case_id].category == right_by_id[case_id].category,
            f"datasets disagree about the category for {case_id}",
        )


def require_compatible_datasets(
    left: Dataset,
    right: Dataset,
    *,
    require_same_config: bool = True,
) -> None:
    """Refuse to compare artifacts from different manifests/case sets/configs.

    Conditions may only be paired within one committed dataset. Two datasets may
    share case-level consistency checks (same frozen manifest, same case IDs) but
    they must not be pooled unless they also share the same configuration
    identity (run, verifier, model, prompt versions, split scope).
    """
    require_shared_manifest(left, right)
    _require(
        left.case_ids == right.case_ids,
        "cannot mix datasets covering different case sets",
    )
    if require_same_config:
        _require(
            left.config_id == right.config_id,
            f"cannot mix datasets with different configurations: "
            f"{left.config_id!r} vs {right.config_id!r}",
        )


def _bind_manifest(
    *,
    artifact: str,
    payload_manifest_id: Any,
    payload_manifest_sha: Any,
    manifest: Manifest,
) -> None:
    _require(
        payload_manifest_id == manifest.manifest_id,
        f"{artifact} manifest_id mismatch: {payload_manifest_id!r}",
    )
    _require(
        payload_manifest_sha == manifest.sha256,
        f"{artifact} manifest hash mismatch (artifact was produced from another manifest)",
    )


def _normalize_decision(
    raw: str, *, dataset_id: str, case_id: str, condition: str
) -> str:
    if raw == "APPLIED":
        return "ACCEPT"
    if raw in DECISIONS:
        return raw
    raise DataIntegrityError(
        f"{dataset_id}: unexpected decision {raw!r} for {case_id}/{condition}"
    )


# ---------------------------------------------------------------------------
# Artifact loaders
# ---------------------------------------------------------------------------


def _validate_expected_invalid_rows(
    *,
    dataset_id: str,
    cases: Sequence[CaseObservation],
    conditions: Sequence[str],
) -> list[dict[str, Any]]:
    """INVALID_INPUT is explicit: verify it only occurs on expected-invalid rows.

    This function does not drop cases; it raises when an expected-valid or
    expected-faulty case receives a schema-construction INVALID_INPUT outcome,
    because treating that as a verifier decision would silently change the
    denominator. Expected-invalid rows are returned as explicit exclusions for
    reporting.
    """
    exclusions: list[dict[str, Any]] = []
    for case in cases:
        for condition in conditions:
            decision = case.decisions[condition]
            status = case.statuses[condition]
            if case.expected == "INVALID_INPUT":
                _require(
                    decision == "INVALID_INPUT" and status == "invalid_input",
                    f"{dataset_id}: expected-invalid case {case.case_id}/{condition} "
                    f"was not recorded as INVALID_INPUT (decision={decision!r}, status={status!r})",
                )
            elif status in ERROR_STATUSES:
                raise DataIntegrityError(
                    f"{dataset_id}: execution/verifier error on expected-valid case "
                    f"{case.case_id}/{condition} would be silently dropped"
                )
            else:
                _require(
                    decision in {"ACCEPT", "HOLD"},
                    f"{dataset_id}: expected-valid case {case.case_id}/{condition} has "
                    f"INVALID_INPUT/unknown decision {decision!r} (status={status!r})",
                )
                _require(
                    status == "completed",
                    f"{dataset_id}: expected-valid case {case.case_id}/{condition} has "
                    f"status {status!r}",
                )
    for case in cases:
        if case.expected == "INVALID_INPUT":
            exclusions.append(
                {
                    "case_id": case.case_id,
                    "split": case.split,
                    "reason": "expected_invalid_schema_construction_failure",
                    "note": (
                        "INVALID_INPUT is a construction failure recorded before "
                        "verification; it is not a verifier decision and is excluded "
                        "from paired verifier comparisons, but counted in "
                        "invalid_input_handling."
                    ),
                }
            )
    return exclusions


def load_rules_fairness_dataset(
    manifest: Manifest, audit_path: Path = RULES_AUDIT_PATH
) -> Dataset:
    """Load the four-condition rules-only fairness audit artifact."""
    payload = read_json(audit_path)
    audit_manifest = payload.get("manifest") or {}
    _bind_manifest(
        artifact="fairness audit",
        payload_manifest_id=audit_manifest.get("manifest_id"),
        payload_manifest_sha=audit_manifest.get("sha256"),
        manifest=manifest,
    )
    _require(
        audit_manifest.get("cases_sha256") == manifest.cases_sha256,
        "fairness audit case hash does not match manifest",
    )
    _require(
        audit_manifest.get("labels_sha256") == manifest.labels_sha256,
        "fairness audit label hash does not match manifest",
    )
    _require(
        audit_manifest.get("case_count") == manifest.case_count,
        "fairness audit case count does not match manifest",
    )
    _require(payload.get("paid_api_calls") == 0, "fairness audit reports paid API calls")
    _require(payload.get("verifier") == "rules", "fairness audit is not rules-only")
    declared_strategies = tuple(payload.get("strategies") or ())
    _require(
        set(declared_strategies) == set(RULES_FAIR_CONDITIONS)
        and len(declared_strategies) == len(RULES_FAIR_CONDITIONS),
        f"fairness audit strategies {declared_strategies!r} do not match expected "
        f"{RULES_FAIR_CONDITIONS!r}",
    )
    # Analyze conditions in the canonical order regardless of artifact key order.
    conditions = RULES_FAIR_CONDITIONS
    cases_payload = payload.get("cases") or []
    _require(
        tuple(case.get("case_id") for case in cases_payload) == manifest.case_ids,
        "fairness audit case IDs/order do not match manifest",
    )
    observations: list[CaseObservation] = []
    for case in cases_payload:
        case_id = case["case_id"]
        expected = manifest.expected_by_id[case_id]
        _require(
            case.get("expected") == expected,
            f"fairness audit expected label mismatch for {case_id}",
        )
        _require(
            case.get("split") == manifest.split_by_id[case_id],
            f"fairness audit split mismatch for {case_id}",
        )
        _require(
            case.get("category") == manifest.category_by_id[case_id],
            f"fairness audit category mismatch for {case_id}",
        )
        _require(
            bool(case.get("invalid_input")) == (expected == "INVALID_INPUT"),
            f"fairness audit invalid_input flag mismatch for {case_id}",
        )
        decisions = {}
        raw_decisions = {}
        for condition in conditions:
            block = (case.get("decisions") or {}).get(condition)
            _require(
                isinstance(block, dict) and block.get("decision") in DECISIONS,
                f"fairness audit missing decision for {case_id}/{condition}",
            )
            expected_verified = (
                expected != "INVALID_INPUT" and condition != "unguarded"
            )
            _require(
                bool(block.get("verified")) == expected_verified,
                f"fairness audit verified flag for {case_id}/{condition} does not match "
                "its decision semantics",
            )
            decisions[condition] = block["decision"]
            raw_decisions[condition] = block["decision"]
        statuses = {
            condition: ("invalid_input" if expected == "INVALID_INPUT" else "completed")
            for condition in conditions
        }
        observations.append(
            CaseObservation(
                case_id=case_id,
                split=manifest.split_by_id[case_id],
                category=manifest.category_by_id[case_id],
                expected=expected,
                decisions=decisions,
                raw_decisions=raw_decisions,
                statuses=statuses,
            )
        )
    excluded = _validate_expected_invalid_rows(
        dataset_id="rules-fair-v2", cases=observations, conditions=conditions
    )

    # Validate the committed per-strategy aggregates against the case rows so a
    # stale aggregate is a refusal, not a silent inconsistency.
    per_strategy = payload.get("per_strategy") or {}
    _require(
        set(per_strategy) == set(conditions),
        "fairness audit per_strategy conditions do not match strategies",
    )
    computed_counts = _decision_counts_per_condition(observations, conditions)
    decisions_by_condition = {
        condition: {case.case_id: case.decisions[condition] for case in observations}
        for condition in conditions
    }
    valid_ids = [case.case_id for case in observations if case.expected == "ACCEPT"]
    faulty_ids = [case.case_id for case in observations if case.expected == "HOLD"]
    invalid_ids = [
        case.case_id for case in observations if case.expected == "INVALID_INPUT"
    ]
    for condition in conditions:
        summary = per_strategy[condition]
        declared_counts = summary.get("decision_counts") or {}
        _require(
            {name: int(declared_counts.get(name, 0)) for name in DECISIONS}
            == computed_counts[condition],
            f"fairness audit decision counts for {condition} disagree with case rows",
        )
        false_accepts = [
            case_id
            for case_id in faulty_ids
            if decisions_by_condition[condition][case_id] == "ACCEPT"
        ]
        _require(
            int((summary.get("false_accepts") or {}).get("count", -1))
            == len(false_accepts),
            f"fairness audit false_accepts for {condition} disagree with case rows",
        )
        valid_accepted = [
            case_id
            for case_id in valid_ids
            if decisions_by_condition[condition][case_id] == "ACCEPT"
        ]
        _require(
            int((summary.get("valid_acceptance") or {}).get("numerator", -1))
            == len(valid_accepted),
            f"fairness audit valid_acceptance for {condition} disagrees with case rows",
        )
        _require(
            int(
                (summary.get("invalid_input_handling") or {}).get(
                    "classified_invalid_input", -1
                )
            )
            == len(
                [
                    case_id
                    for case_id in invalid_ids
                    if decisions_by_condition[condition][case_id] == "INVALID_INPUT"
                ]
            ),
            f"fairness audit invalid-input handling for {condition} disagrees with case rows",
        )

    config = {
        "kind": "rules",
        "verifier": "rules",
        "conditions": {
            condition: (payload["strategies"][condition] or {}).get("kind")
            for condition in conditions
        },
        "reads_contradicts": {
            condition: (payload["strategies"][condition] or {}).get("reads_contradicts")
            for condition in conditions
        },
        "decision_normalization": (
            "unguarded records ACCEPT for every schema-valid candidate (no verifier "
            "was consulted); it is a no-verification baseline, not a verifier result."
        ),
        "split_scope": "dev+heldout",
    }
    sources = (
        _source(audit_path),
        {
            "path": _relative(MANIFEST_PATH),
            "sha256": manifest.sha256,
            "role": "manifest",
        },
        {
            "path": _relative(CASES_PATH),
            "sha256": manifest.cases_sha256,
            "role": "cases",
        },
        {
            "path": _relative(LABELS_PATH),
            "sha256": manifest.labels_sha256,
            "role": "labels",
        },
    )
    return Dataset(
        dataset_id="rules-fair-v2",
        label="Rules-only fairness audit v2 (four conditions)",
        kind="rules",
        conditions=conditions,
        cases=tuple(observations),
        manifest_id=manifest.manifest_id,
        manifest_sha256=manifest.sha256,
        sources=sources,
        config=config,
        config_id="rules:fairness-audit-v2:verifier=rules:splits=dev+heldout",
        notes=(
            "Rules-only deterministic audit; 36 cases (14 ACCEPT / 12 HOLD / 10 INVALID_INPUT).",
            "text -> text_fair isolates contradiction information access; text_fair -> graph "
            "isolates verifier construction with information held equal.",
        ),
        excluded=tuple(excluded),
        validation={
            "manifest_bound": True,
            "cases_sha256_verified": True,
            "labels_sha256_verified": True,
            "case_condition_rows": len(observations) * len(conditions),
            "duplicate_rows": 0,
            "missing_rows": 0,
            "aggregate_decision_counts_verified": True,
            "aggregate_false_accepts_verified": True,
            "aggregate_valid_acceptance_verified": True,
            "aggregate_invalid_input_verified": True,
        },
    )


def _decision_counts_per_condition(
    cases: Sequence[CaseObservation], conditions: Sequence[str]
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for condition in conditions:
        block = {name: 0 for name in DECISIONS}
        for case in cases:
            block[case.decisions[condition]] += 1
        counts[condition] = block
    return counts


def load_rules_run_dataset(
    manifest: Manifest,
    summary_path: Path = RULES_RUN_SUMMARY_PATH,
    metrics_path: Path = RULES_RUN_METRICS_PATH,
    config_path: Path = RULES_RUN_CONFIG_PATH,
) -> Dataset:
    """Load the committed rules-only three-condition evaluation-v2 run."""
    config_payload = read_json(config_path)
    _bind_manifest(
        artifact="evaluation-v2 run-config",
        payload_manifest_id=config_payload.get("manifest_id"),
        payload_manifest_sha=config_payload.get("manifest_sha256"),
        manifest=manifest,
    )
    _require(
        tuple(config_payload.get("conditions") or ()) == RULES_RUN_CONDITIONS,
        "evaluation-v2 run-config conditions do not match the expected three conditions",
    )
    _require(
        config_payload.get("verifier") == "rules",
        "evaluation-v2 run-config is not a rules-only run",
    )
    _require(
        config_payload.get("paid_api_calls") == 0,
        "evaluation-v2 run-config reports paid API calls",
    )
    summary = read_json(summary_path)
    _bind_manifest(
        artifact="evaluation-v2 pilot summary",
        payload_manifest_id=summary.get("manifest_id"),
        payload_manifest_sha=summary.get("manifest_sha256"),
        manifest=manifest,
    )
    _require(
        summary.get("verifier") == "rules", "evaluation-v2 summary is not rules-only"
    )
    _require(
        summary.get("run_id") == config_payload.get("run_id"),
        "evaluation-v2 run IDs disagree between summary and run-config",
    )
    conditions = tuple(summary.get("conditions") or ())
    _require(
        conditions == RULES_RUN_CONDITIONS,
        f"evaluation-v2 summary conditions {conditions!r} do not match expected",
    )
    results = summary.get("results") or []
    expected_pairs = {
        (case_id, condition)
        for case_id in manifest.case_ids
        for condition in conditions
    }
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in results:
        key = (row.get("case_id"), row.get("condition"))
        _require(key in expected_pairs, f"evaluation-v2 summary has unexpected row {key}")
        _require(key not in rows_by_key, f"evaluation-v2 summary has duplicate row {key}")
        rows_by_key[key] = row
    missing = expected_pairs - set(rows_by_key)
    _require(not missing, f"evaluation-v2 summary is missing rows: {sorted(missing)}")

    observations: list[CaseObservation] = []
    for case_id in manifest.case_ids:
        expected = manifest.expected_by_id[case_id]
        decisions: dict[str, str] = {}
        raw_decisions: dict[str, str] = {}
        statuses: dict[str, str] = {}
        for condition in conditions:
            row = rows_by_key[(case_id, condition)]
            _require(
                row.get("label_expected_decision") == expected,
                f"evaluation-v2 summary expected label mismatch for {case_id}/{condition}",
            )
            status = row.get("status")
            _require(
                status in STATUSES,
                f"evaluation-v2 summary has unexpected status {status!r} for {case_id}/{condition}",
            )
            raw = row.get("decision")
            decisions[condition] = _normalize_decision(
                raw, dataset_id="rules-v2-run", case_id=case_id, condition=condition
            )
            raw_decisions[condition] = raw
            statuses[condition] = status
        observations.append(
            CaseObservation(
                case_id=case_id,
                split=manifest.split_by_id[case_id],
                category=manifest.category_by_id[case_id],
                expected=expected,
                decisions=decisions,
                raw_decisions=raw_decisions,
                statuses=statuses,
            )
        )
    excluded = _validate_expected_invalid_rows(
        dataset_id="rules-v2-run", cases=observations, conditions=conditions
    )

    # Verify the committed metrics aggregate that will be quoted by docs/README.
    metrics = read_json(metrics_path)
    _bind_manifest(
        artifact="evaluation-v2 metrics",
        payload_manifest_id=metrics.get("manifest_id"),
        payload_manifest_sha=metrics.get("manifest_sha256"),
        manifest=manifest,
    )
    _require(
        metrics.get("run_id") == summary.get("run_id"),
        "evaluation-v2 metrics run_id disagrees with summary",
    )
    _require(
        metrics.get("case_count") == manifest.case_count,
        "evaluation-v2 metrics case count disagrees with manifest",
    )
    condition_metrics = metrics.get("condition_metrics") or {}
    _require(
        set(condition_metrics) == set(conditions),
        "evaluation-v2 metrics conditions disagree with summary",
    )
    computed_counts = _decision_counts_per_condition(observations, conditions)
    per_condition_decisions = {
        condition: {case.case_id: case.decisions[condition] for case in observations}
        for condition in conditions
    }
    for condition in conditions:
        block = condition_metrics[condition]
        if condition == "unguarded":
            declared = {
                "APPLIED": int(block.get("applied_count", -1)),
                "INVALID_INPUT": int(
                    (block.get("decision_counts") or {}).get("INVALID_INPUT", -1)
                ),
            }
            applied = sum(
                1
                for case in observations
                if case.raw_decisions[condition] == "APPLIED"
                and case.expected != "INVALID_INPUT"
            )
            _require(
                declared["APPLIED"] == applied,
                "evaluation-v2 metrics unguarded APPLIED count disagrees with rows",
            )
            _require(
                declared["INVALID_INPUT"] == computed_counts[condition]["INVALID_INPUT"],
                "evaluation-v2 metrics unguarded INVALID_INPUT count disagrees with rows",
            )
        else:
            declared = {
                name: int((block.get("decision_counts") or {}).get(name, -1))
                for name in DECISIONS
            }
            _require(
                declared == computed_counts[condition],
                f"evaluation-v2 metrics decision counts for {condition} disagree with rows",
            )
            faulty = [
                case.case_id for case in observations if case.expected == "HOLD"
            ]
            false_accepts = [
                case_id
                for case_id in faulty
                if per_condition_decisions[condition][case_id] == "ACCEPT"
            ]
            faulty_detection = block.get("faulty_detection") or {}
            declared_false_accept = int(
                (faulty_detection.get("false_accept_rate") or {}).get("numerator", -1)
            )
            _require(
                declared_false_accept == len(false_accepts),
                f"evaluation-v2 metrics false accepts for {condition} disagree with rows",
            )
            declared_held = int(faulty_detection.get("true_positives", -1))
            # The committed metrics.json "faulty" gold set is expected-HOLD plus
            # expected-INVALID (handled construction failures), so its
            # true_positives counts both explicit HOLDs and handled invalid rows.
            expected_invalid = [
                case.case_id for case in observations if case.expected == "INVALID_INPUT"
            ]
            expected_held = (
                len(
                    [
                        case_id
                        for case_id in faulty
                        if per_condition_decisions[condition][case_id] == "HOLD"
                    ]
                )
                + len(
                    [
                        case_id
                        for case_id in expected_invalid
                        if per_condition_decisions[condition][case_id] == "INVALID_INPUT"
                    ]
                )
            )
            _require(
                declared_held == expected_held,
                f"evaluation-v2 metrics faulty detection for {condition} disagrees with rows",
            )
            _require(
                int((block.get("valid_update_acceptance") or {}).get("accepted", -1))
                == sum(
                    1
                    for case in observations
                    if case.expected == "ACCEPT"
                    and case.decisions[condition] == "ACCEPT"
                ),
                f"evaluation-v2 metrics valid acceptance for {condition} disagrees with rows",
            )
            invalid_expected = [
                case.case_id for case in observations if case.expected == "INVALID_INPUT"
            ]
            invalid_block = block.get("invalid_input") or {}
            _require(
                int(invalid_block.get("expected_invalid_cases", -1))
                == len(invalid_expected),
                f"evaluation-v2 metrics invalid-input expected count for {condition} disagree",
            )
            _require(
                int(invalid_block.get("handled_invalid_cases", -1))
                == sum(
                    1
                    for case_id in invalid_expected
                    if per_condition_decisions[condition][case_id] == "INVALID_INPUT"
                ),
                f"evaluation-v2 metrics invalid-input handled count for {condition} disagree",
            )

    # Cross-check the committed paired-difference listing for graph vs text.
    paired = (metrics.get("paired_differences") or {}).get("graph_vs_text") or {}
    differing = [
        case.case_id
        for case in observations
        if case.expected != "INVALID_INPUT"
        and case.decisions["graph"] != case.decisions["text"]
    ]
    _require(
        list(paired.get("differing_case_ids") or []) == differing,
        "evaluation-v2 metrics committed graph-vs-text paired differences disagree with rows",
    )

    config = {
        "kind": "rules",
        "verifier": "rules",
        "run_id": summary.get("run_id"),
        "conditions": list(conditions),
        "decision_normalization": (
            "unguarded APPLIED rows are normalized to ACCEPT for paired comparisons; "
            "raw APPLIED decisions are retained in the report."
        ),
        "split_scope": "dev+heldout",
    }
    sources = (
        _source(summary_path),
        _source(metrics_path),
        _source(config_path),
        {"path": _relative(MANIFEST_PATH), "sha256": manifest.sha256, "role": "manifest"},
    )
    return Dataset(
        dataset_id="rules-v2-run",
        label="Committed rules-only evaluation-v2 run (three conditions)",
        kind="rules",
        conditions=conditions,
        cases=tuple(observations),
        manifest_id=manifest.manifest_id,
        manifest_sha256=manifest.sha256,
        sources=sources,
        config=config,
        config_id=f"rules:{summary.get('run_id')}:verifier=rules:splits=dev+heldout",
        notes=(
            "Same frozen manifest and verifier family as the fairness audit; used both for "
            "paired analysis and as an independent artifact-level cross-check.",
            "Unguarded decisions are raw APPLIED rows normalized to ACCEPT; no verifier ran.",
        ),
        excluded=tuple(excluded),
        validation={
            "manifest_bound": True,
            "run_config_bound": True,
            "case_condition_rows": len(observations) * len(conditions),
            "duplicate_rows": 0,
            "missing_rows": 0,
            "aggregate_decision_counts_verified": True,
            "aggregate_invalid_input_verified": True,
            "committed_paired_differences_verified": True,
        },
    )


def load_llm_dataset(
    manifest: Manifest,
    *,
    dataset_id: str,
    label: str,
    summary_path: Path,
    metrics_path: Path,
    expected_prompt_version: str,
    notes: tuple[str, ...],
) -> Dataset:
    """Load a committed held-out LLM/semantic summary and verify its aggregate."""
    summary = read_json(summary_path)
    _bind_manifest(
        artifact=f"{dataset_id} summary",
        payload_manifest_id=summary.get("manifest_id"),
        payload_manifest_sha=summary.get("manifest_sha256"),
        manifest=manifest,
    )
    _require(
        summary.get("cases_sha256") == manifest.cases_sha256,
        f"{dataset_id}: case hash does not match manifest",
    )
    _require(
        summary.get("labels_sha256") == manifest.labels_sha256,
        f"{dataset_id}: label hash does not match manifest",
    )
    _require(
        summary.get("split") == "heldout",
        f"{dataset_id}: expected a held-out split artifact",
    )
    conditions = tuple(summary.get("conditions") or ())
    _require(
        conditions == LLM_CONDITIONS,
        f"{dataset_id}: conditions {conditions!r} do not match {LLM_CONDITIONS!r}",
    )
    prompt_versions = summary.get("prompt_versions") or {}
    _require(
        prompt_versions.get("llm") == expected_prompt_version,
        f"{dataset_id}: LLM prompt version {prompt_versions.get('llm')!r} != "
        f"{expected_prompt_version!r}",
    )
    _require(
        prompt_versions.get("semantic") == LLM_SEMANTIC_PROMPT,
        f"{dataset_id}: semantic prompt version mismatch",
    )
    heldout_ids = tuple(
        case_id
        for case_id in manifest.case_ids
        if manifest.split_by_id[case_id] == "heldout"
    )
    _require(
        summary.get("case_count") == len(heldout_ids),
        f"{dataset_id}: case_count does not match held-out manifest size",
    )
    expected_valid = sum(
        1 for case_id in heldout_ids if manifest.expected_by_id[case_id] == "ACCEPT"
    )
    expected_faulty = sum(
        1 for case_id in heldout_ids if manifest.expected_by_id[case_id] == "HOLD"
    )
    expected_invalid = sum(
        1
        for case_id in heldout_ids
        if manifest.expected_by_id[case_id] == "INVALID_INPUT"
    )
    expected_schema_valid = expected_valid + expected_faulty
    _require(
        summary.get("valid_case_count") == expected_schema_valid,
        f"{dataset_id}: valid_case_count (schema-valid cases) does not match manifest labels",
    )
    _require(
        summary.get("invalid_case_count") == expected_invalid,
        f"{dataset_id}: invalid_case_count does not match manifest labels",
    )
    _require(
        (summary.get("llm_verifier_errors") or {}).get("rows", 0) == 0,
        f"{dataset_id}: run reports LLM verifier errors; refusing to silently drop them",
    )
    results = summary.get("results") or []
    expected_pairs = {
        (case_id, condition) for case_id in heldout_ids for condition in conditions
    }
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in results:
        key = (row.get("case_id"), row.get("condition"))
        _require(key in expected_pairs, f"{dataset_id}: unexpected row {key}")
        _require(key not in rows_by_key, f"{dataset_id}: duplicate row {key}")
        rows_by_key[key] = row
    missing = expected_pairs - set(rows_by_key)
    _require(not missing, f"{dataset_id}: missing rows: {sorted(missing)}")

    observations: list[CaseObservation] = []
    for case_id in heldout_ids:
        expected = manifest.expected_by_id[case_id]
        decisions = {}
        raw_decisions = {}
        statuses = {}
        for condition in conditions:
            row = rows_by_key[(case_id, condition)]
            _require(
                row.get("label_expected_decision") == expected,
                f"{dataset_id}: expected label mismatch for {case_id}/{condition}",
            )
            status = row.get("status")
            _require(
                status in STATUSES,
                f"{dataset_id}: unexpected status {status!r} for {case_id}/{condition}",
            )
            raw = row.get("decision")
            decisions[condition] = _normalize_decision(
                raw, dataset_id=dataset_id, case_id=case_id, condition=condition
            )
            raw_decisions[condition] = raw
            statuses[condition] = status
            if expected == "INVALID_INPUT":
                _require(
                    row.get("error") is not None,
                    f"{dataset_id}: expected-invalid row {case_id}/{condition} has no "
                    "construction error recorded",
                )
            else:
                _require(
                    row.get("error") is None,
                    f"{dataset_id}: non-invalid row {case_id}/{condition} has an error: "
                    f"{row.get('error')!r}",
                )
        observations.append(
            CaseObservation(
                case_id=case_id,
                split=manifest.split_by_id[case_id],
                category=manifest.category_by_id[case_id],
                expected=expected,
                decisions=decisions,
                raw_decisions=raw_decisions,
                statuses=statuses,
            )
        )
    excluded = _validate_expected_invalid_rows(
        dataset_id=dataset_id, cases=observations, conditions=conditions
    )

    # Verify committed aggregate metrics against the summary rows.
    metrics = read_json(metrics_path)
    _bind_manifest(
        artifact=f"{dataset_id} metrics",
        payload_manifest_id=metrics.get("manifest_id"),
        payload_manifest_sha=metrics.get("manifest_sha256"),
        manifest=manifest,
    )
    _require(
        metrics.get("run_id") == summary.get("run_id"),
        f"{dataset_id}: metrics run_id disagrees with summary",
    )
    _require(
        tuple(metrics.get("conditions") or ()) == conditions,
        f"{dataset_id}: metrics conditions disagree with summary",
    )
    for condition in conditions:
        block = (metrics.get("condition_metrics") or {}).get(condition) or {}
        decisions = {case.case_id: case.decisions[condition] for case in observations}
        declared_counts = {
            name: int((block.get("decision_counts") or {}).get(name, -1))
            for name in DECISIONS
        }
        computed_counts = {name: 0 for name in DECISIONS}
        for case in observations:
            computed_counts[case.decisions[condition]] += 1
        _require(
            declared_counts == computed_counts,
            f"{dataset_id}: committed decision counts for {condition} disagree with rows",
        )
        _require(
            int((block.get("invalid_input_rows", -1))) == computed_counts["INVALID_INPUT"],
            f"{dataset_id}: committed invalid_input_rows for {condition} disagree with rows",
        )
        valid_ids = [case.case_id for case in observations if case.expected == "ACCEPT"]
        faulty_ids = [case.case_id for case in observations if case.expected == "HOLD"]
        declared_valid = block.get("valid_acceptance") or {}
        _require(
            int(declared_valid.get("numerator", -1))
            == sum(1 for case_id in valid_ids if decisions[case_id] == "ACCEPT"),
            f"{dataset_id}: committed valid acceptance for {condition} disagrees with rows",
        )
        declared_false_accepts = block.get("false_accepts") or {}
        _require(
            int(declared_false_accepts.get("numerator", -1))
            == sum(1 for case_id in faulty_ids if decisions[case_id] == "ACCEPT"),
            f"{dataset_id}: committed false accepts for {condition} disagree with rows",
        )
        declared_false_holds = block.get("false_holds") or {}
        _require(
            int(declared_false_holds.get("numerator", -1))
            == sum(1 for case_id in valid_ids if decisions[case_id] != "ACCEPT"),
            f"{dataset_id}: committed false holds for {condition} disagree with rows",
        )

    config = {
        "kind": "llm",
        "verifier": "llm+semantic",
        "run_id": summary.get("run_id"),
        "model": summary.get("model"),
        "provider": summary.get("provider"),
        "prompt_versions": dict(prompt_versions),
        "split_scope": "heldout",
        "conditions": list(conditions),
    }
    sources = (
        _source(summary_path),
        _source(metrics_path),
        {"path": _relative(MANIFEST_PATH), "sha256": manifest.sha256, "role": "manifest"},
    )
    return Dataset(
        dataset_id=dataset_id,
        label=label,
        kind="llm",
        conditions=conditions,
        cases=tuple(observations),
        manifest_id=manifest.manifest_id,
        manifest_sha256=manifest.sha256,
        sources=sources,
        config=config,
        config_id=(
            f"llm:{summary.get('model')}:{summary.get('provider')}:"
            f"prompt={prompt_versions.get('llm')}:semantic={prompt_versions.get('semantic')}:"
            "split=heldout"
        ),
        notes=notes,
        excluded=tuple(excluded),
        validation={
            "manifest_bound": True,
            "cases_sha256_verified": True,
            "labels_sha256_verified": True,
            "case_condition_rows": len(observations) * len(conditions),
            "duplicate_rows": 0,
            "missing_rows": 0,
            "aggregate_decision_counts_verified": True,
            "aggregate_valid_acceptance_verified": True,
            "aggregate_false_accept_house_verified": True,
            "invalid_rows_construction_failures": True,
        },
    )


def cross_check_rules_artifacts(
    manifest: Manifest,
    fairness: Dataset,
    run: Dataset,
    metrics_path: Path = RULES_RUN_METRICS_PATH,
) -> dict[str, Any]:
    """Assert the two independent rules artifacts agree case-by-case."""
    require_compatible_datasets(fairness, run, require_same_config=False)
    shared_conditions = tuple(
        condition for condition in run.conditions if condition in fairness.conditions
    )
    _require(
        set(shared_conditions) == set(RULES_RUN_CONDITIONS)
        and len(shared_conditions) == len(RULES_RUN_CONDITIONS),
        "rules artifacts do not share the expected conditions",
    )
    mismatches: list[dict[str, Any]] = []
    compared = 0
    for case in fairness.cases:
        run_case = run.case_by_id[case.case_id]
        for condition in shared_conditions:
            compared += 1
            if case.decisions[condition] != run_case.decisions[condition]:
                mismatches.append(
                    {
                        "case_id": case.case_id,
                        "condition": condition,
                        "fairness_audit": case.decisions[condition],
                        "evaluation_v2_run": run_case.decisions[condition],
                    }
                )
    _require(
        not mismatches,
        "committed rules artifacts disagree case-by-case and cannot be treated as "
        f"cross-checks: {mismatches[:5]}",
    )
    committed_metrics = read_json(metrics_path)
    paired = (committed_metrics.get("paired_differences") or {}).get("graph_vs_text") or {}
    differing_case_ids = [
        case.case_id
        for case in fairness.cases
        if case.expected != "INVALID_INPUT"
        and case.decisions["graph"] != case.decisions["text"]
    ]
    _require(
        list(paired.get("differing_case_ids") or []) == differing_case_ids,
        "committed evaluation-v2 paired differences disagree with reconstructed cases",
    )
    return {
        "status": "match",
        "datasets": [fairness.dataset_id, run.dataset_id],
        "shared_conditions": list(shared_conditions),
        "compared_case_condition_rows": compared,
        "mismatches": mismatches,
        "committed_metrics_graph_vs_text_differences": differing_case_ids,
        "note": (
            "The fairness audit and the evaluation-v2 rules run are independent committed "
            "artifacts over the same frozen manifest; their overlapping case decisions match "
            "exactly, and the committed graph-vs-text paired differences are reproduced from "
            "case rows. They remain separate configurations and are not pooled."
        ),
    }


# ---------------------------------------------------------------------------
# Statistical primitives
# ---------------------------------------------------------------------------


def bootstrap_paired_difference(
    a_values: Sequence[float],
    b_values: Sequence[float],
    *,
    resamples: int,
    seed: int,
    key: str,
) -> dict[str, Any]:
    """Percentile bootstrap CI for mean(A) - mean(B), resampling paired cases.

    ``a_values[i]`` and ``b_values[i]`` are the two conditions' binary outcomes
    for the same case ``i``. Each resample draws case indices with replacement
    and keeps both outcomes of a drawn case together, so the pairing is
    preserved. The reported interval is the 2.5th-97.5th percentile of the
    resampled differences (95% by default).
    """
    _require(
        len(a_values) == len(b_values),
        "paired bootstrap requires aligned A/B outcome vectors",
    )
    n = len(a_values)
    if n == 0:
        return {
            "method": "percentile_bootstrap_paired_cases",
            "resamples": resamples,
            "seed": seed,
            "seed_key": key,
            "n_pairs": 0,
            "observed_difference": None,
            "ci_low": None,
            "ci_high": None,
            "note": "no paired cases with a well-defined outcome",
        }
    observed = sum(a_values) / n - sum(b_values) / n
    if n < 2 or resamples <= 0:
        return {
            "method": "percentile_bootstrap_paired_cases",
            "resamples": max(0, resamples),
            "seed": seed,
            "seed_key": key,
            "n_pairs": n,
            "observed_difference": _round(observed),
            "ci_low": None,
            "ci_high": None,
            "note": "insufficient pairs for a resampling interval",
        }
    rng = random.Random(_seed_for(seed, key))
    diffs: list[float] = []
    for _ in range(resamples):
        sum_a = 0.0
        sum_b = 0.0
        for _ in range(n):
            index = rng.randrange(n)
            sum_a += a_values[index]
            sum_b += b_values[index]
        diffs.append(sum_a / n - sum_b / n)
    diffs.sort()
    return {
        "method": "percentile_bootstrap_paired_cases",
        "resamples": resamples,
        "seed": seed,
        "seed_key": key,
        "n_pairs": n,
        "observed_difference": _round(observed),
        "ci_low": _round(percentile(diffs, 0.025)),
        "ci_high": _round(percentile(diffs, 0.975)),
        "note": (
            "Percentile interval over case-resampling replicates; paired outcomes are "
            "drawn together. Percentile intervals are not bias-corrected and do not "
            "account for label uncertainty or condition selection."
        ),
    }


def mcnemar_exact(discordant_a_correct: int, discordant_b_correct: int) -> dict[str, Any]:
    """Exact two-sided McNemar test implemented as a binomial sign test.

    ``b`` = cases where A is correct and B is wrong; ``c`` = cases where B is
    correct and A is wrong. Under the null the discordant pairs are fair coin
    flips, so the exact two-sided p-value is ``2 * P(X <= min(b, c))`` for
    ``X ~ Binomial(b + c, 0.5)``, capped at 1.0. No normal approximation and no
    continuity correction are used, which is the appropriate small-n choice.
    """
    b = int(discordant_a_correct)
    c = int(discordant_b_correct)
    _require(b >= 0 and c >= 0, "discordant counts must be non-negative")
    discordant = b + c
    if discordant == 0:
        return {
            "method": "exact_two_sided_mcnemar_binomial",
            "a_correct_b_wrong": 0,
            "b_correct_a_wrong": 0,
            "discordant_pairs": 0,
            "p_value": 1.0,
            "note": (
                "No discordant pairs: the conditions agree on every paired case, so the "
                "exact test provides no evidence of a difference (p = 1); it also cannot "
                "estimate the discordance rate. Absence of disagreement is not equivalence."
            ),
        }
    tail = sum(math.comb(discordant, k) for k in range(0, min(b, c) + 1)) / (2**discordant)
    p_value = min(1.0, 2.0 * tail)
    smallest_attainable = min(1.0, 2.0 / (2**discordant))
    return {
        "method": "exact_two_sided_mcnemar_binomial",
        "a_correct_b_wrong": b,
        "b_correct_a_wrong": c,
        "discordant_pairs": discordant,
        "p_value": _round(p_value),
        "smallest_attainable_p_value": _round(smallest_attainable),
        "note": (
            "Exact two-sided binomial test on discordant pairs. With few discordant "
            "pairs only large asymmetries can reach p < 0.05; a non-significant result "
            "must not be read as equivalence, and p-values are unadjusted for the many "
            "descriptive comparisons in this report."
        ),
    }


def compare_binary_outcomes(
    case_ids: Sequence[str],
    a_outcomes: Mapping[str, int],
    b_outcomes: Mapping[str, int],
    *,
    resamples: int,
    seed: int,
    key: str,
    a_label: str,
    b_label: str,
) -> dict[str, Any]:
    """Paired comparison of two condition-level binary outcomes over cases."""
    missing = [case_id for case_id in case_ids if case_id not in a_outcomes or case_id not in b_outcomes]
    _require(not missing, f"paired comparison is missing outcomes for cases: {missing}")
    a_values = [int(a_outcomes[case_id]) for case_id in case_ids]
    b_values = [int(b_outcomes[case_id]) for case_id in case_ids]
    n = len(case_ids)
    a_correct = sum(a_values)
    b_correct = sum(b_values)
    a_correct_b_wrong = [
        case_id
        for case_id, a_value, b_value in zip(case_ids, a_values, b_values)
        if a_value and not b_value
    ]
    b_correct_a_wrong = [
        case_id
        for case_id, a_value, b_value in zip(case_ids, a_values, b_values)
        if b_value and not a_value
    ]
    discordant_ids = [
        case_id
        for case_id, a_value, b_value in zip(case_ids, a_values, b_values)
        if a_value != b_value
    ]
    bootstrap = bootstrap_paired_difference(
        a_values, b_values, resamples=resamples, seed=seed, key=key
    )
    return {
        "a": {"condition": a_label, **_rate(a_correct, n)},
        "b": {"condition": b_label, **_rate(b_correct, n)},
        "n_paired": n,
        "difference": _round((a_correct - b_correct) / n) if n else None,
        "bootstrap_ci": bootstrap,
        "discordant_counts": {
            "a_correct_b_wrong": len(a_correct_b_wrong),
            "b_correct_a_wrong": len(b_correct_a_wrong),
            "total": len(discordant_ids),
        },
        "discordant_case_ids": discordant_ids,
        "a_correct_b_wrong_case_ids": a_correct_b_wrong,
        "b_correct_a_wrong_case_ids": b_correct_a_wrong,
        "mcnemar": mcnemar_exact(len(a_correct_b_wrong), len(b_correct_a_wrong)),
    }


# ---------------------------------------------------------------------------
# Condition metrics and paired comparisons
# ---------------------------------------------------------------------------


def _outcome_map(
    dataset: Dataset,
    condition: str,
    case_ids: Sequence[str],
    *,
    outcome: str,
) -> dict[str, int]:
    decisions = dataset.decisions(condition)
    expected = {case.case_id: case.expected for case in dataset.cases}
    if outcome == "correct":
        return {
            case_id: int(decisions[case_id] == expected[case_id]) for case_id in case_ids
        }
    if outcome == "held":
        return {case_id: int(decisions[case_id] == "HOLD") for case_id in case_ids}
    if outcome == "accepted":
        return {case_id: int(decisions[case_id] == "ACCEPT") for case_id in case_ids}
    raise ValueError(f"unknown outcome {outcome!r}")


def condition_reliability(dataset: Dataset, condition: str) -> dict[str, Any]:
    """Explicit-denominator reliability metrics for one condition."""
    _require(condition in dataset.conditions, f"unknown condition {condition!r}")
    cases = dataset.cases
    decisions = dataset.decisions(condition)
    statuses = dataset.statuses(condition)
    valid_ids = dataset.valid_case_ids
    faulty_ids = dataset.faulty_case_ids
    invalid_ids = dataset.invalid_case_ids
    non_invalid_ids = dataset.non_invalid_case_ids

    valid_accepted = [case_id for case_id in valid_ids if decisions[case_id] == "ACCEPT"]
    false_holds = [case_id for case_id in valid_ids if decisions[case_id] != "ACCEPT"]
    unexpected_invalid_on_valid = [
        case_id for case_id in valid_ids if decisions[case_id] == "INVALID_INPUT"
    ]
    faulty_held = [case_id for case_id in faulty_ids if decisions[case_id] == "HOLD"]
    false_accepts = [case_id for case_id in faulty_ids if decisions[case_id] == "ACCEPT"]
    unexpected_invalid_on_faulty = [
        case_id for case_id in faulty_ids if decisions[case_id] == "INVALID_INPUT"
    ]
    safe_rejected = [case_id for case_id in faulty_ids if decisions[case_id] in {"HOLD", "INVALID_INPUT"}]
    correctly_decided = [
        case_id
        for case_id in non_invalid_ids
        if decisions[case_id] == dataset.expected_by_id[case_id]
    ]
    invalid_recorded = [
        case_id for case_id in invalid_ids if decisions[case_id] == "INVALID_INPUT"
    ]
    execution_errors = [
        case_id for case_id in dataset.case_ids if statuses[case_id] not in STATUSES
    ]
    raw_counts: dict[str, int] = {}
    for case in cases:
        raw = case.raw_decisions[condition]
        raw_counts[raw] = raw_counts.get(raw, 0) + 1
    normalized_counts = {name: 0 for name in DECISIONS}
    for case in cases:
        normalized_counts[case.decisions[condition]] += 1

    def rate_block(numerator: int, denominator: int) -> dict[str, Any]:
        return {**_rate(numerator, denominator), "ci": wilson_interval(numerator, denominator)}

    return {
        "condition": condition,
        "case_count": len(cases),
        "decision_counts": normalized_counts,
        "raw_decision_counts": dict(sorted(raw_counts.items())),
        "valid_update_acceptance": {
            **rate_block(len(valid_accepted), len(valid_ids)),
            "case_ids": valid_accepted,
            "false_hold_case_ids": false_holds,
            "false_hold_decisions": {case_id: decisions[case_id] for case_id in false_holds},
            "false_hold_rate": {
                **rate_block(len(false_holds), len(valid_ids)),
                "case_ids": false_holds,
            },
            "unexpected_invalid_on_expected_valid": unexpected_invalid_on_valid,
        },
        "faulty_detection_hold_only": {
            **rate_block(len(faulty_held), len(faulty_ids)),
            "case_ids": faulty_held,
            "false_accept_case_ids": false_accepts,
            "false_accept_rate": {
                **rate_block(len(false_accepts), len(faulty_ids)),
                "case_ids": false_accepts,
            },
            "unexpected_invalid_on_expected_faulty": unexpected_invalid_on_faulty,
        },
        "faulty_detection_combined": {
            **rate_block(len(safe_rejected), len(faulty_ids)),
            "case_ids": safe_rejected,
            "note": (
                "Combined safe-rejection view: HOLD or INVALID_INPUT on expected-faulty "
                "cases. INVALID_INPUT here would be a construction failure, not a verifier "
                "decision; it never occurs on expected-faulty rows in the committed artifacts."
            ),
        },
        "overall_accuracy_non_invalid": {
            **rate_block(len(correctly_decided), len(non_invalid_ids)),
            "case_ids": correctly_decided,
            "incorrect_case_ids": [
                case_id for case_id in non_invalid_ids if case_id not in set(correctly_decided)
            ],
        },
        "invalid_input_handling": {
            **rate_block(len(invalid_recorded), len(invalid_ids)),
            "case_ids": invalid_recorded,
            "cases_not_recorded_invalid": [
                case_id for case_id in invalid_ids if case_id not in set(invalid_recorded)
            ],
            "note": (
                "Expected-invalid cases fail schema construction before any verifier runs; "
                "this is handled-explicitly accounting, not verifier detection."
            ),
        },
        "execution_errors": {
            "numerator": len(execution_errors),
            "denominator": len(cases),
            "case_ids": execution_errors,
        },
        "stratum_sizes": {
            "valid": len(valid_ids),
            "faulty": len(faulty_ids),
            "invalid_input": len(invalid_ids),
            "non_invalid": len(non_invalid_ids),
        },
    }


def paired_comparisons(
    dataset: Dataset, *, resamples: int, seed: int
) -> list[dict[str, Any]]:
    """All unordered condition pairs within a dataset, for three paired outcomes."""
    _require(
        len(dataset.conditions) >= 2,
        f"dataset {dataset.dataset_id} has fewer than two conditions",
    )
    comparisons: list[dict[str, Any]] = []
    conditions = dataset.conditions
    for index, a_label in enumerate(conditions):
        for b_label in conditions[index + 1 :]:
            for metric in DEFAULT_OUTCOME_METRICS:
                stratum = OBSERVED_STRATUM[metric]
                if stratum == "non_invalid":
                    case_ids = dataset.non_invalid_case_ids
                elif stratum == "faulty":
                    case_ids = dataset.faulty_case_ids
                else:
                    case_ids = dataset.valid_case_ids
                if not case_ids:
                    comparisons.append(
                        {
                            "a_condition": a_label,
                            "b_condition": b_label,
                            "metric": metric,
                            "n_paired": 0,
                            "note": f"no cases in the {stratum} stratum",
                        }
                    )
                    continue
                outcome = {
                    "overall_accuracy": "correct",
                    "faulty_detection": "held",
                    "valid_acceptance": "accepted",
                }[metric]
                a_outcomes = _outcome_map(dataset, a_label, case_ids, outcome=outcome)
                b_outcomes = _outcome_map(dataset, b_label, case_ids, outcome=outcome)
                key = f"{dataset.dataset_id}|{metric}|{a_label}|{b_label}"
                comparison = compare_binary_outcomes(
                    case_ids,
                    a_outcomes,
                    b_outcomes,
                    resamples=resamples,
                    seed=seed,
                    key=key,
                    a_label=a_label,
                    b_label=b_label,
                )
                comparison.update(
                    {
                        "dataset_id": dataset.dataset_id,
                        "a_condition": a_label,
                        "b_condition": b_label,
                        "metric": metric,
                        "stratum": stratum,
                        "denominator_note": {
                            "overall_accuracy": (
                                "denominator = non-invalid paired cases (valid + expected-faulty)"
                            ),
                            "faulty_detection": (
                                "denominator = expected-HOLD (faulty) cases; outcome = decision HOLD"
                            ),
                            "valid_acceptance": (
                                "denominator = expected-ACCEPT (valid) cases; outcome = decision ACCEPT"
                            ),
                        }[metric],
                    }
                )
                comparisons.append(comparison)
    return comparisons


# ---------------------------------------------------------------------------
# Sample-size / precision rationale
# ---------------------------------------------------------------------------


def _minimum_n_for_half_width(discordance: float, half_width: float, z: float = Z_95) -> int:
    if discordance <= 0 or half_width <= 0:
        raise ValueError("discordance and half-width must be positive")
    return math.ceil((z * z * discordance) / (half_width * half_width))


def precision_rationale(
    datasets: Sequence[Dataset],
    paired_by_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Sample-size/precision table anchored to the committed analysis.

    The paired-difference standard error under the null is approximately
    ``sqrt(pi_discordant / n)``, so the expected 95% CI half-width is
    ``1.95996 * sqrt(pi_discordant / n)``. This is a precision target, not a
    power guarantee: McNemar power also depends on how discordant pairs split
    between the two directions, and the calculations assume independent,
    identically distributed cases (they are not: labels are single-reviewer and
    cases were hand-authored).
    """
    observed = []
    for dataset in datasets:
        for comparison in paired_by_dataset.get(dataset.dataset_id, ()):
            if "bootstrap_ci" not in comparison:
                continue
            if comparison["bootstrap_ci"]["ci_low"] is None:
                continue
            interval = comparison["bootstrap_ci"]
            discordant = comparison["discordant_counts"]["total"]
            n = comparison["n_paired"]
            observed.append(
                {
                    "dataset_id": dataset.dataset_id,
                    "metric": comparison["metric"],
                    "comparison": f"{comparison['a_condition']} vs {comparison['b_condition']}",
                    "n_paired": n,
                    "discordant_pairs": discordant,
                    "discordant_rate": _round(discordant / n) if n else None,
                    "observed_difference": comparison["difference"],
                    "ci_half_width": _round(
                        (interval["ci_high"] - interval["ci_low"]) / 2
                    ),
                    "mcnemar_p_value": comparison["mcnemar"]["p_value"],
                }
            )

    half_width_table = []
    for n in CANDIDATE_SAMPLE_SIZES:
        row: dict[str, Any] = {"n_paired_cases": n}
        for pi in PLANNING_DISCORDANCE_RATES:
            row[f"discordance_{pi:.2f}"] = {
                "expected_discordant_pairs": round(n * pi, 1),
                "expected_ci_half_width": _round(Z_95 * math.sqrt(pi / n)),
            }
        half_width_table.append(row)

    required_n_table = []
    for half_width in TARGET_HALF_WIDTHS:
        row = {"target_ci_half_width": half_width}
        for pi in PLANNING_DISCORDANCE_RATES:
            row[f"discordance_{pi:.2f}"] = _minimum_n_for_half_width(pi, half_width)
        required_n_table.append(row)

    observed_discordance = [
        item for item in observed if item["n_paired"] > 0
    ]
    examples = []
    for item in observed_discordance:
        if item["discordant_pairs"] == 0 or item["n_paired"] == 0:
            continue
        discordance = item["discordant_pairs"] / item["n_paired"]
        examples.append(
            {
                **item,
                "required_n_for_half_width_0.05": _minimum_n_for_half_width(
                    discordance, 0.05
                ),
                "required_n_for_half_width_0.10": _minimum_n_for_half_width(
                    discordance, 0.10
                ),
            }
        )
    rules_graph_text = next(
        (
            item
            for item in observed
            if item["dataset_id"] == "rules-fair-v2"
            and item["metric"] == "overall_accuracy"
            and item["comparison"] == "text vs graph"
        ),
        None,
    )
    current = {
        "heldout_llm_schema_valid_cases": 13,
        "heldout_llm_total_cases": 18,
        "heldout_llm_expected_invalid_cases": 5,
        "rules_non_invalid_cases": 26,
        "rules_total_cases": 36,
        "rules_valid_cases": 14,
        "rules_faulty_cases": 12,
        "rules_expected_invalid_cases": 10,
        "rules_graph_vs_text_observed_discordance": rules_graph_text,
        "statement": (
            "The held-out LLM analysis rests on 13 schema-valid cases (7 valid, 6 "
            "expected-faulty; 5 expected-invalid are construction failures). The rules "
            "analysis rests on 26 non-invalid cases (14 valid, 12 expected-faulty). "
            "These are exploratory samples: the intervals are wide, several paired "
            "comparisons have zero or two discordant cases, and no result supports a "
            "superiority or equivalence claim."
        ),
    }
    return {
        "method": (
            "Paired-difference precision approximation: 95% CI half-width ~ "
            "1.95996 * sqrt(pi_discordant / n), where pi_discordant is the probability "
            "that the two conditions disagree on a paired case. This is a planning "
            "approximation, not a power calculation and not a guarantee; McNemar power "
            "depends on the split of discordant pairs and the case-level outcomes are not "
            "independent draws from a representative population."
        ),
        "observed_comparisons": observed_discordance,
        "observed_based_n_examples": examples,
        "current_sample_sizes": current,
        "candidate_n_half_width_table": half_width_table,
        "required_n_for_target_half_width": required_n_table,
        "caveat": (
            "The table answers 'how wide would the paired-difference interval be at n?' "
            "It does not answer 'what n guarantees significance?' and it assumes a "
            "discordance rate. No power guarantee is made, and no threshold is selected "
            "from this table."
        ),
    }


# ---------------------------------------------------------------------------
# Report and document
# ---------------------------------------------------------------------------


def _dataset_block(
    dataset: Dataset,
    *,
    comparisons: Sequence[Mapping[str, Any]],
    resamples: int,
    seed: int,
) -> dict[str, Any]:
    condition_metrics = {
        condition: condition_reliability(dataset, condition)
        for condition in dataset.conditions
    }
    return {
        "dataset_id": dataset.dataset_id,
        "label": dataset.label,
        "kind": dataset.kind,
        "config_id": dataset.config_id,
        "config": dict(dataset.config),
        "conditions": list(dataset.conditions),
        "case_count": len(dataset.cases),
        "split_counts": {
            split: sum(1 for case in dataset.cases if case.split == split)
            for split in sorted({case.split for case in dataset.cases})
        },
        "expected_counts": {
            name: sum(1 for case in dataset.cases if case.expected == name)
            for name in EXPECTED_DECISIONS
        },
        "sources": [dict(source) for source in dataset.sources],
        "validation": dict(dataset.validation),
        "excluded_cases": [dict(item) for item in dataset.excluded],
        "notes": list(dataset.notes),
        "condition_metrics": condition_metrics,
        "paired_comparisons": [dict(comparison) for comparison in comparisons],
        "resamples": resamples,
        "seed": seed,
    }


def build_report(
    *,
    seed: int = DEFAULT_SEED,
    resamples: int = DEFAULT_RESAMPLES,
    manifest_path: Path = MANIFEST_PATH,
) -> dict[str, Any]:
    """Load every committed artifact and build the deterministic report payload."""
    _require(resamples > 0, "resamples must be positive")
    manifest = load_manifest(manifest_path)
    fairness = load_rules_fairness_dataset(manifest)
    rules_run = load_rules_run_dataset(manifest)
    llm_v1 = load_llm_dataset(
        manifest,
        dataset_id="llm-heldout-prompt-v1",
        label="Held-out LLM/semantic run, prompt v1 (development evidence)",
        summary_path=LLM_V1_SUMMARY_PATH,
        metrics_path=LLM_V1_METRICS_PATH,
        expected_prompt_version="membud-transition-checks-v1",
        notes=(
            "13 schema-valid held-out cases; 5 expected-invalid construction failures.",
            "Prompt v1 run kept only as archival/development evidence; it is not pooled with v2.",
        ),
    )
    llm_v2 = load_llm_dataset(
        manifest,
        dataset_id="llm-heldout-prompt-v2",
        label="Held-out LLM/semantic replay, prompt v2 (development evidence)",
        summary_path=LLM_V2_SUMMARY_PATH,
        metrics_path=LLM_V2_METRICS_PATH,
        expected_prompt_version="membud-transition-checks-v2",
        notes=(
            "13 schema-valid held-out cases; 5 expected-invalid construction failures.",
            "Prompt v2 was selected using this held-out split, so these numbers are "
            "development evidence, not a clean independent test.",
        ),
    )
    datasets = (fairness, rules_run, llm_v1, llm_v2)
    # The manifest must be identical across all datasets; configurations differ
    # by design and are analyzed separately, never pooled.
    for dataset in datasets[1:]:
        require_shared_manifest(datasets[0], dataset)
    _require(
        len({dataset.config_id for dataset in datasets}) == len(datasets),
        "datasets share a configuration identity and would duplicate evidence",
    )
    comparisons_by_dataset = {
        dataset.dataset_id: paired_comparisons(dataset, resamples=resamples, seed=seed)
        for dataset in datasets
    }
    cross_check = cross_check_rules_artifacts(manifest, fairness, rules_run)
    report = {
        "analysis_id": ANALYSIS_ID,
        "scope": (
            "Artifact-only paired statistical analysis of committed MemBud evaluation "
            "evidence. No network I/O, no paid model calls, no new model runs, and no "
            "changes to existing result artifacts."
        ),
        "no_network": True,
        "paid_api_calls": 0,
        "new_model_runs": 0,
        "method": {
            "unit_of_analysis": "transition case (one fixed package per case)",
            "pairing": (
                "Every condition is evaluated on the same frozen cases; paired differences "
                "compare the same cases across conditions."
            ),
            "bootstrap": {
                "method": "percentile bootstrap resampling cases",
                "paired": True,
                "resampling_unit": "case",
                "resamples": resamples,
                "confidence_level": CONFIDENCE_LEVEL,
                "seed": seed,
                "per_comparison_rng_seed": (
                    "int.from_bytes(sha256(f'{seed}|{dataset}|{metric}|{A}|{B}')[:8], 'big')"
                ),
                "ci_quantiles": [0.025, 0.975],
                "percentile_definition": (
                    "linear interpolation between order statistics at position (n-1)*q"
                ),
            },
            "mcnemar": {
                "test": "exact two-sided McNemar (binomial sign test on discordant pairs)",
                "p_value": "min(1, 2 * P(X <= min(b, c))), X ~ Binomial(b+c, 0.5)",
                "normal_approximation": False,
            },
            "per_condition_ci": "Wilson score interval (95%) for each binomial rate",
            "invalid_input": (
                "INVALID_INPUT is a schema-construction failure recorded before verification. "
                "Expected-invalid cases are counted in invalid_input_handling and listed as "
                "explicit exclusions from paired verifier-decision comparisons; no case is "
                "silently dropped."
            ),
            "multiplicity": (
                "No multiplicity correction: comparisons are descriptive and no confirmatory "
                "test is claimed."
            ),
            "threshold_tuning": (
                "No threshold was selected or tuned on any case from these artifacts."
            ),
            "datasets_are_not_pooled": (
                "Rules and LLM artifacts differ in verifier construction, and LLM prompt v1 "
                "and v2 differ in configuration; each dataset is analyzed separately."
            ),
        },
        "seed": seed,
        "resamples": resamples,
        "manifest": {
            "manifest_id": manifest.manifest_id,
            "sha256": manifest.sha256,
            "schema_version": manifest.schema_version,
            "status": manifest.status,
            "case_count": manifest.case_count,
            "split_counts": dict(manifest.split_counts),
            "expected_decision_counts": dict(manifest.expected_decision_counts),
            "review": dict(manifest.review),
            "cases_sha256": manifest.cases_sha256,
            "labels_sha256": manifest.labels_sha256,
            "cases_source": _relative(CASES_PATH),
            "labels_source": _relative(LABELS_PATH),
        },
        "cross_checks": {"rules_artifacts": cross_check},
        "datasets": {
            dataset.dataset_id: _dataset_block(
                dataset,
                comparisons=comparisons_by_dataset[dataset.dataset_id],
                resamples=resamples,
                seed=seed,
            )
            for dataset in datasets
        },
        "sample_size_rationale": precision_rationale(datasets, comparisons_by_dataset),
        "limitations": [
            "Labels are single-reviewer exploratory annotations; the second independent "
            "human review is pending. Label error is not modelled in these intervals.",
            "Cases are synthetic and were authored alongside the verifiers (circularity "
            "risk); they are not a representative benchmark and no population inference "
            "is supported.",
            "The v2 LLM prompt was selected using the held-out split; its held-out numbers "
            "are development evidence, not a clean independent test. Rules artifacts reuse "
            "the dev and held-out splits for descriptive analysis.",
            "Samples are small: 13 schema-valid held-out cases for the LLM analysis and 26 "
            "non-invalid rules cases (12 expected-faulty, 14 valid). Several paired "
            "comparisons have zero or two discordant cases.",
            "Zero discordance (text_fair vs graph on the rules set; all v2 LLM condition "
            "pairs) cannot establish equivalence; the exact test gives p = 1 and the "
            "discordance rate is not estimable from the data.",
            "Paired bootstrap intervals resample cases within outcome strata, assume "
            "exchangeable cases, and are not corrected for multiplicity or condition "
            "selection. Percentile intervals are descriptive.",
            "INVALID_INPUT rows are schema-construction failures, not verifier decisions; "
            "they are excluded from paired verifier comparisons and counted separately.",
            "The unguarded condition has no verifier decisions; its APPLIED/ACCEPT rows are "
            "a raw-effect baseline, not a verifier outcome.",
            "No superiority, equivalence, or causality claim is made, and no threshold was "
            "tuned on these cases.",
        ],
        "interpretation": (
            "On the committed rules-only evidence, graph and the fair-text control agree on "
            "every non-invalid case (26/26); the plain text baseline misses exactly the two "
            "constructed contradiction cases, and the paired difference is not statistically "
            "distinguishable with 26 non-invalid cases (2 discordant pairs, exact McNemar "
            "p = 0.5). The prompt-v2 held-out LLM replay shows all three conditions tied at "
            "7/7 valid accepted and 6/6 expected-faulty detected, but zero discordant pairs "
            "cannot support an equivalence claim. The prompt-v1 run is retained as "
            "development evidence only. The intervals and the sample-size table show why a "
            "fresh, better-powered, independently reviewed evaluation is required before any "
            "comparative claim. No superiority or equivalence claim is made for any condition."
        ),
    }
    return report


def serialize_report(report: Mapping[str, Any]) -> bytes:
    return (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _fmt_pct(block: Mapping[str, Any]) -> str:
    rate = block.get("rate")
    if rate is None:
        return f"{block.get('numerator')}/{block.get('denominator')} (n/a)"
    return f"{block['numerator']}/{block['denominator']} ({100.0 * rate:.1f}%)"


def _fmt_diff(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def _fmt_interval(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "n/a"
    return f"[{low:.3f}, {high:.3f}]"


def _fmt_p(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value < 0.001:
        return "<0.001"
    return f"{value:.3f}"


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join("---:" if header.startswith("(") else "---" for header in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def _metric_label(metric: str) -> str:
    return {
        "overall_accuracy": "case accuracy",
        "faulty_detection": "faulty detection (HOLD)",
        "valid_acceptance": "valid acceptance (ACCEPT)",
    }[metric]


def render_markdown(report: Mapping[str, Any], report_sha256: str) -> str:
    lines: list[str] = []
    dataset_map: Mapping[str, Any] = report["datasets"]
    manifest = report["manifest"]
    bootstrap = report["method"]["bootstrap"]
    lines += [
        "# Paired statistical analysis of committed evaluation evidence",
        "",
        "**Status:** exploratory, artifact-only analysis. No network I/O, no paid model calls, "
        "and no new model runs. Report ID: `"
        + report["analysis_id"]
        + "`.",
        "",
        "**Report artifact:** `data/results/statistical-analysis/report.json` "
        "(SHA-256 `" + report_sha256 + "`).",
        "",
        "**Reproduce:**",
        "",
        "```bash",
        "TMPDIR=$PWD/.tmp-stats PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \\",
        "    .venv/bin/python scripts/analyze_paired_metrics.py",
        "```",
        "",
        "## Scope and data",
        "",
        "All numbers below are computed from committed artifacts. Conditions are paired by "
        "case: every condition sees the same frozen transitions and labels. The analysis "
        "never pools rules and LLM artifacts, never pools prompt v1 and v2, and refuses to "
        "run if artifacts disagree about the manifest, case set, labels or configuration.",
        "",
        "Manifest `" + manifest["manifest_id"] + "` (`" + manifest["sha256"][:12] + "`), "
        + str(manifest["case_count"]) + " cases, splits "
        + ", ".join(f"{split} {count}" for split, count in manifest["split_counts"].items())
        + "; expected decisions "
        + ", ".join(
            f"{name} {count}" for name, count in manifest["expected_decision_counts"].items()
        )
        + ". Review status: single reviewer, second independent review pending.",
        "",
        "| Dataset | Kind | Conditions | Cases | Configuration |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for dataset_id, dataset in dataset_map.items():
        lines.append(
            f"| `{dataset_id}` | {dataset['kind']} | "
            + ", ".join(f"`{condition}`" for condition in dataset["conditions"])
            + f" | {dataset['case_count']} | `{dataset['config_id']}` |"
        )
    lines += [
        "",
        "Artifact hashes, per-condition numerators/denominators and Wilson 95% intervals "
        "are in `report.json`.",
        "",
        "Cross-check: "
        + str(report["cross_checks"]["rules_artifacts"]["compared_case_condition_rows"])
        + " overlapping case-condition decisions agree between the fairness audit and the "
        "evaluation-v2 rules run (mismatches: "
        + str(len(report["cross_checks"]["rules_artifacts"]["mismatches"]))
        + "), and the committed graph-vs-text paired-difference listing is reproduced from "
        "case rows.",
        "",
        "## Method",
        "",
        f"- Unit of analysis: transition case. Bootstrap: percentile interval over "
        f"{bootstrap['resamples']:,} resampling replicates of cases (paired, seed "
        f"{bootstrap['seed']}); each replicate draws cases with replacement and keeps both "
        f"conditions' outcomes for a drawn case together.",
        "- Per-condition rates report explicit numerators/denominators with Wilson 95% "
        "confidence intervals.",
        "- McNemar: exact two-sided binomial test on discordant pairs (no normal "
        "approximation); the table reports `b` = A correct/B wrong, `c` = B correct/A wrong.",
        "- `INVALID_INPUT` is a schema-construction failure recorded before verification. "
        "Expected-invalid cases are counted in the invalid-handling denominator and listed "
        "as explicit exclusions from paired verifier comparisons; no case is silently "
        "dropped.",
        "- When every paired case disagrees in one direction, the bootstrap distribution "
        "collapses and the percentile interval is a single point (for example unguarded vs "
        "guarded on faulty detection). That reflects the all-or-nothing committed case "
        "design, not high precision.",
        "- No multiplicity correction and no threshold tuning; all comparisons are "
        "descriptive.",
        "",
    ]

    for dataset_id, dataset in dataset_map.items():
        metrics = dataset["condition_metrics"]
        lines += [
            f"## {dataset['label']} (`{dataset_id}`)",
            "",
            "| Condition | Valid accepted | False holds | Faulty detected (HOLD) | "
            "False accepts | Expected-invalid handled | Accuracy (non-invalid) | "
            "Decision counts (A/H/I) |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for condition in dataset["conditions"]:
            block = metrics[condition]
            counts = block["decision_counts"]
            lines.append(
                "| `"
                + condition
                + "` | "
                + _fmt_pct(block["valid_update_acceptance"])
                + " | "
                + _fmt_pct(
                    {
                        "numerator": block["valid_update_acceptance"]["denominator"]
                        - block["valid_update_acceptance"]["numerator"],
                        "denominator": block["valid_update_acceptance"]["denominator"],
                        "rate": (
                            None
                            if block["valid_update_acceptance"]["rate"] is None
                            else 1.0 - block["valid_update_acceptance"]["rate"]
                        ),
                    }
                )
                + " | "
                + _fmt_pct(block["faulty_detection_hold_only"])
                + " | "
                + _fmt_pct(
                    {
                        "numerator": block["faulty_detection_hold_only"]["denominator"]
                        - block["faulty_detection_hold_only"]["numerator"],
                        "denominator": block["faulty_detection_hold_only"]["denominator"],
                        "rate": (
                            None
                            if block["faulty_detection_hold_only"]["rate"] is None
                            else 1.0 - block["faulty_detection_hold_only"]["rate"]
                        ),
                    }
                )
                + " | "
                + _fmt_pct(block["invalid_input_handling"])
                + " | "
                + _fmt_pct(block["overall_accuracy_non_invalid"])
                + " | "
                + f"{counts['ACCEPT']}/{counts['HOLD']}/{counts['INVALID_INPUT']}"
                + " |"
            )
        lines += [
            "",
            "Paired differences resample cases within each outcome stratum "
            "(`overall_accuracy`: non-invalid cases; `faulty_detection`: expected-faulty; "
            "`valid_acceptance`: valid).",
            "",
            "| Comparison | Outcome | Paired n | b / c | Difference | 95% bootstrap CI | "
            "McNemar exact p |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for comparison in dataset["paired_comparisons"]:
            if "bootstrap_ci" not in comparison:
                continue
            interval = comparison["bootstrap_ci"]
            lines.append(
                "| `"
                + comparison["a_condition"]
                + "` vs `"
                + comparison["b_condition"]
                + "` | "
                + _metric_label(comparison["metric"])
                + f" | {comparison['n_paired']} | "
                + f"{comparison['discordant_counts']['a_correct_b_wrong']} / "
                + f"{comparison['discordant_counts']['b_correct_a_wrong']} | "
                + _fmt_diff(comparison["difference"])
                + " | "
                + _fmt_interval(interval["ci_low"], interval["ci_high"])
                + " | "
                + _fmt_p(comparison["mcnemar"]["p_value"])
                + " |"
            )
        if dataset["excluded_cases"]:
            lines += [
                "",
                str(len(dataset["excluded_cases"]))
                + " expected-invalid cases (each across "
                + str(len(dataset["conditions"]))
                + " conditions) are explicit exclusions "
                "(`invalid_input_schema_construction_failure`), not dropped rows.",
            ]
        lines += ["", ]

    rationale = report["sample_size_rationale"]
    lines += [
        "## Sample-size / precision rationale",
        "",
        rationale["current_sample_sizes"]["statement"],
        "",
        "Expected 95% CI half-width for a paired difference under the planning "
        "approximation `1.95996 * sqrt(pi_discordant / n)`. This is a precision target, "
        "not a power guarantee.",
        "",
    ]
    headers = ["n (paired cases)"] + [
        f"π={pi:.2f}" for pi in PLANNING_DISCORDANCE_RATES
    ]
    rows = []
    for row in rationale["candidate_n_half_width_table"]:
        rows.append(
            [str(row["n_paired_cases"])]
            + [
                f"{row[f'discordance_{pi:.2f}']['expected_ci_half_width']:.3f}"
                for pi in PLANNING_DISCORDANCE_RATES
            ]
        )
    lines += _md_table(headers, rows)
    lines += [
        "",
        "Cases needed for a target 95% CI half-width:",
        "",
    ]
    headers = ["Target half-width"] + [
        f"π={pi:.2f}" for pi in PLANNING_DISCORDANCE_RATES
    ]
    rows = []
    for row in rationale["required_n_for_target_half_width"]:
        rows.append(
            [f"{row['target_ci_half_width']:.3f}"]
            + [
                str(row[f"discordance_{pi:.2f}"])
                for pi in PLANNING_DISCORDANCE_RATES
            ]
        )
    lines += _md_table(headers, rows)
    lines += [
        "",
        "Observed paired comparisons with at least one discordant pair (the full list is "
        "in `report.json`):",
        "",
    ]
    headers = ["Dataset", "Comparison", "Outcome", "Paired n", "Discordant", "Half-width"]
    rows = []
    for item in rationale["observed_comparisons"]:
        if item["discordant_pairs"] == 0:
            continue
        rows.append(
            [
                f"`{item['dataset_id']}`",
                item["comparison"],
                _metric_label(item["metric"]),
                str(item["n_paired"]),
                str(item["discordant_pairs"]),
                "n/a"
                if item["ci_half_width"] is None
                else f"{item['ci_half_width']:.3f}",
            ]
        )
    lines += _md_table(headers, rows)
    graph_text_example = next(
        (
            item
            for item in rationale.get("observed_based_n_examples", ())
            if item["dataset_id"] == "rules-fair-v2"
            and item["metric"] == "overall_accuracy"
            and item["comparison"] == "text vs graph"
        ),
        None,
    )
    if graph_text_example is not None:
        lines += [
            "",
            "At-n precision example: the rules `text vs graph` case-accuracy difference "
            f"has n = {graph_text_example['n_paired']}, "
            f"{graph_text_example['discordant_pairs']} discordant pairs, and an observed "
            f"95% half-width of about {graph_text_example['ci_half_width']:.3f}; a 0.05 "
            "half-width at that observed discordance rate would need roughly "
            f"{graph_text_example['required_n_for_half_width_0.05']} non-invalid paired "
            "cases (planning estimate, not a power guarantee).",
        ]
    lines += [
        "",
        rationale["caveat"],
        "",
        "## Determinism",
        "",
        f"- Seed `{report['seed']}`, resamples `{report['resamples']:,}`, confidence "
        f"level `{CONFIDENCE_LEVEL}`.",
        "- Two consecutive runs produce byte-identical `report.json`; verify with "
        "`scripts/analyze_paired_metrics.py --check`.",
        "",
        "## Limitations (read before citing any number)",
        "",
    ]
    lines += [f"- {limitation}" for limitation in report["limitations"]]
    lines += [
        "",
        "## Interpretation",
        "",
        report["interpretation"],
        "",
    ]
    return "\n".join(lines)


def write_outputs(
    report: Mapping[str, Any],
    *,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    docs_path: Path = DEFAULT_DOCS_PATH,
) -> tuple[bytes, str]:
    serialized = serialize_report(report)
    report_sha256 = sha256_bytes(serialized)
    markdown = render_markdown(report, report_sha256).encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(serialized)
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_bytes(markdown)
    return serialized, report_sha256


def check_outputs(
    report: Mapping[str, Any],
    *,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    docs_path: Path = DEFAULT_DOCS_PATH,
) -> tuple[bool, list[str]]:
    serialized = serialize_report(report)
    report_sha256 = sha256_bytes(serialized)
    markdown = render_markdown(report, report_sha256).encode("utf-8")
    problems: list[str] = []
    if not output_path.exists():
        problems.append(f"missing report artifact: {_relative(output_path)}")
    elif output_path.read_bytes() != serialized:
        problems.append(f"report artifact is stale/different: {_relative(output_path)}")
    if not docs_path.exists():
        problems.append(f"missing analysis document: {_relative(docs_path)}")
    elif docs_path.read_bytes() != markdown:
        problems.append(f"analysis document is stale/different: {_relative(docs_path)}")
    return not problems, problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Paired bootstrap/McNemar statistical analysis of committed MemBud "
            "evaluation artifacts (artifact-only; no network or paid calls)."
        )
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="report JSON path (default: data/results/statistical-analysis/report.json)",
    )
    parser.add_argument(
        "--docs",
        default=str(DEFAULT_DOCS_PATH),
        help="analysis document path (default: docs/statistical-analysis.md)",
    )
    parser.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--check",
        action="store_true",
        help="rebuild in memory and verify the committed report and document are identical",
    )
    args = parser.parse_args(argv)

    try:
        report = build_report(seed=args.seed, resamples=args.resamples)
    except DataIntegrityError as exc:
        print(f"refusing to analyze: {exc}", file=sys.stderr)
        return 2

    output_path = Path(args.output)
    docs_path = Path(args.docs)
    if args.check:
        ok, problems = check_outputs(
            report, output_path=output_path, docs_path=docs_path
        )
        if ok:
            serialized = serialize_report(report)
            print(
                "committed artifacts are reproducible: "
                f"report sha256={sha256_bytes(serialized)}"
            )
            return 0
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1

    _, report_sha256 = write_outputs(
        report, output_path=output_path, docs_path=docs_path
    )
    print(f"wrote {_relative(output_path)}")
    print(f"wrote {_relative(docs_path)}")
    print(f"report sha256={report_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
