"""Tests for the budget-capped LLM + semantic held-out harness.

Every model and comparator here is a deterministic fake: no network call and no
paid API request is made.  The harness is loaded directly from ``scripts/``
because it is an executable script, not an installed module.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from membud.semantic import SemanticRelation

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/run_llm_heldout.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("run_llm_heldout", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = _load_harness()

ALL_PASS_CHECKS = [
    {
        "name": "coverage",
        "status": "PASS",
        "reason": "All required claims are present.",
        "reason_code": "COVERAGE_OK",
        "affected_ids": [],
    },
    {
        "name": "preservation",
        "status": "PASS",
        "reason": "The prior fact is retained.",
        "reason_code": "PRESERVATION_OK",
        "affected_ids": [],
    },
    {
        "name": "faithfulness",
        "status": "PASS",
        "reason": "Claims have evidence.",
        "reason_code": "FAITHFULNESS_OK",
        "affected_ids": [],
    },
]


# ---------------------------------------------------------------------------
# Fake clients/comparators
# ---------------------------------------------------------------------------


class FakeClient:
    """Deterministic stand-in for OpenAICompatibleChatClient."""

    model = "fake-deepseek"
    configuration = {"provider": "fake", "model": "fake-deepseek"}
    cache_path = None

    def __init__(
        self,
        *,
        payload: dict | None = None,
        error: Exception | None = None,
        total_tokens: int = 150,
        cached: bool = False,
    ) -> None:
        self.payload = payload or {"checks": ALL_PASS_CHECKS}
        self.error = error
        self.total_tokens = total_tokens
        self.cached = cached
        self.calls = 0
        self.messages = None
        self.request_attempts = 0 if cached else 1

    def complete_json(self, messages, validator=None):
        self.calls += 1
        self.messages = messages
        if self.error is not None:
            raise self.error
        if validator is not None:
            validator(self.payload)
        if self.cached:
            return SimpleNamespace(
                json=self.payload,
                content=json.dumps(self.payload),
                model=self.model,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                request_id=None,
                latency_ms=0.0,
                request_attempts=0,
                json_retries=0,
                cached=True,
            )
        return SimpleNamespace(
            json=self.payload,
            content=json.dumps(self.payload),
            model=self.model,
            input_tokens=max(1, self.total_tokens - 30),
            output_tokens=30,
            total_tokens=self.total_tokens,
            request_id="fake-request",
            latency_ms=12.5,
            request_attempts=1,
            json_retries=0,
            cached=False,
        )


class FakeComparator:
    """Deterministic SemanticComparator with a per-row usage block."""

    name = "fake-semantic"
    prompt_version = "fake-semantic-v1"
    configuration = {"type": "fake-semantic"}
    usage: dict[str, int] | None = None

    def __init__(self, relation: SemanticRelation = SemanticRelation.EQUIVALENT) -> None:
        self.relation = relation
        self.calls: list[tuple[str, str]] = []
        self.usage = {
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
            "api_attempts": 1,
            "json_retries": 0,
            "cache_hit": 0,
        }

    def compare(self, a: str, b: str) -> SemanticRelation:
        self.calls.append((a, b))
        return self.relation


def _comparator_factory(relation=SemanticRelation.EQUIVALENT):
    created: list[FakeComparator] = []

    def factory(client):
        comparator = FakeComparator(relation)
        created.append(comparator)
        return comparator

    return factory, created


# ---------------------------------------------------------------------------
# Synthetic hash-pinned manifests
# ---------------------------------------------------------------------------


def _valid_raw(case_id: str = "HELD-01") -> dict:
    return {
        "case_id": case_id,
        "schema_version": 2,
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "evidence": [
            {
                "id": "e1",
                "source": "test",
                "text": "Ia bekerja di Tokopedia.",
                "supports": ["bekerja di Tokopedia"],
            }
        ],
        "required_claims": ["tinggal di Bandung"],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    }


def _faulty_raw(case_id: str = "HELD-02") -> dict:
    return {
        "case_id": case_id,
        "schema_version": 2,
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["memiliki kapal pesiar"]},
        ],
        "evidence": [],
        "required_claims": ["tinggal di Bandung"],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    }


def _invalid_raw(case_id: str = "HELD-03") -> dict:
    # DELETE without a target (and therefore without authorization) fails schema
    # validation before any verifier runs.
    return {
        "case_id": case_id,
        "schema_version": 2,
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [],
        "evidence": [],
        "required_claims": [],
        "operations": [{"type": "DELETE"}],
    }


def _label(case_id: str, expected_decision: str) -> dict:
    return {
        "case_id": case_id,
        "split": "heldout",
        "category": "test",
        "expected_decision": expected_decision,
        "expected_invalid": expected_decision == "INVALID_INPUT",
        "fault_types": [],
        "valid_prior_records_expected_to_persist": [],
        "corrupted_prior_record_ids": [],
    }


def _write_manifest(tmp_path: Path, raw_cases: list[dict], labels: list[dict]) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cases_path = data_dir / "cases.jsonl"
    labels_path = data_dir / "labels.jsonl"
    cases_path.write_text(
        "\n".join(json.dumps(case) for case in raw_cases) + "\n", encoding="utf-8"
    )
    labels_path.write_text(
        "\n".join(json.dumps(label) for label in labels) + "\n", encoding="utf-8"
    )
    manifest = {
        "manifest_id": "test-manifest",
        "schema_version": 2,
        "cases_file": "cases.jsonl",
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "labels_file": "labels.jsonl",
        "labels_sha256": hashlib.sha256(labels_path.read_bytes()).hexdigest(),
        "case_ids": [case["case_id"] for case in raw_cases],
        "case_count": len(raw_cases),
        "case_entries": [
            {
                "case_id": label["case_id"],
                "split": label["split"],
                "expected_invalid": label["expected_invalid"],
            }
            for label in labels
        ],
    }
    manifest_path = data_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _run(
    tmp_path: Path,
    *,
    cases: list[dict],
    labels: list[dict],
    conditions=("graph",),
    client=None,
    comparator_factory=None,
    max_total_tokens: int = 250_000,
    max_attempts: int = 3,
    output_name: str = "out",
) -> tuple[dict, dict, Path]:
    manifest_path = _write_manifest(tmp_path, cases, labels)
    output = tmp_path / output_name
    summary = harness.run_heldout(
        manifest_path=manifest_path,
        output=output,
        client=client if client is not None else FakeClient(),
        split="heldout",
        conditions=conditions,
        max_total_tokens=max_total_tokens,
        max_attempts=max_attempts,
        run_id="test-run",
        code_revision="test-revision",
        comparator_factory=comparator_factory
        or (lambda client: FakeComparator()),
    )
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    return summary, metrics, output


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_success_rows_all_conditions(tmp_path):
    client = FakeClient(total_tokens=150)
    comparator_factory, comparators = _comparator_factory()
    summary, metrics, output = _run(
        tmp_path,
        cases=[_valid_raw()],
        labels=[_label("HELD-01", "ACCEPT")],
        conditions=("graph", "text", "semantic"),
        client=client,
        comparator_factory=comparator_factory,
    )

    assert (output / "summary.json").exists()
    assert (output / "metrics.json").exists()
    rows = {row["condition"]: row for row in summary["results"]}
    assert set(rows) == {"graph", "text", "semantic"}
    for condition in ("graph", "text", "semantic"):
        assert rows[condition]["decision"] == "ACCEPT"
        assert rows[condition]["status"] == "completed"
        assert rows[condition]["label_expected_decision"] == "ACCEPT"
    assert rows["graph"]["usage"]["total_tokens"] == 150
    assert rows["text"]["usage"]["total_tokens"] == 150
    assert rows["graph"]["cache_hit"] == 0
    # Semantic rows report the comparator's combined usage and four checks.
    assert rows["semantic"]["usage"]["total_tokens"] == 14
    assert len(rows["semantic"]["checks"]) == 4
    assert {check["name"] for check in rows["semantic"]["checks"]} == {
        "coverage",
        "preservation",
        "faithfulness",
        "semantic",
    }
    assert client.calls == 2  # graph + text only; semantic uses the fake comparator
    assert sum(len(comparator.calls) for comparator in comparators) > 0
    assert summary["budget"]["aborted"] is False
    assert summary["budget"]["accounted_tokens_used"] == 150 + 150 + 14
    assert summary["cache_hits"] == 0
    assert summary["semantic_uncertain_rows"] == 0
    assert metrics["condition_metrics"]["graph"]["valid_acceptance"]["rate"] == 1.0
    assert metrics["condition_metrics"]["semantic"]["semantic_abstention_rate"]["rate"] == 0.0


def test_budget_abort_stops_before_next_row(tmp_path):
    client = FakeClient(total_tokens=150)  # one row uses 150 tokens
    summary, metrics, _ = _run(
        tmp_path,
        cases=[_valid_raw("HELD-01"), _valid_raw("HELD-11")],
        labels=[_label("HELD-01", "ACCEPT"), _label("HELD-11", "ACCEPT")],
        conditions=("graph",),
        client=client,
        max_total_tokens=100,
    )

    assert len(summary["results"]) == 1
    assert client.calls == 1  # second case was never attempted
    assert summary["budget"]["aborted"] is True
    assert summary["budget"]["accounted_tokens_used"] == 150
    assert summary["budget"]["overshoot_tokens"] == 50
    assert "stopped before the next row" in summary["budget"]["abort_reason"]
    assert metrics["condition_metrics"]["graph"]["case_coverage"]["rate"] == 0.5


def test_abort_still_records_expected_invalid_rows_with_zero_calls(tmp_path):
    client = FakeClient(total_tokens=150)
    summary, metrics, _ = _run(
        tmp_path,
        cases=[_valid_raw("HELD-01"), _invalid_raw("HELD-14")],
        labels=[_label("HELD-01", "ACCEPT"), _label("HELD-14", "INVALID_INPUT")],
        conditions=("graph", "text"),
        client=client,
        max_total_tokens=50,
    )

    assert client.calls == 1  # only the first valid graph row was attempted
    assert summary["budget"]["aborted"] is True
    invalid_rows = [row for row in summary["results"] if row["decision"] == "INVALID_INPUT"]
    assert len(invalid_rows) == 2  # once for each condition, despite the abort
    assert {row["condition"] for row in invalid_rows} == {"graph", "text"}
    assert all(row["accounted_tokens"] == 0 for row in invalid_rows)
    assert metrics["condition_metrics"]["text"]["case_coverage"]["rate"] == 0.0


def test_failed_row_without_usage_gets_conservative_fallback(tmp_path):
    client = FakeClient(error=RuntimeError("provider unavailable"))
    summary, metrics, _ = _run(
        tmp_path,
        cases=[_valid_raw()],
        labels=[_label("HELD-01", "ACCEPT")],
        conditions=("graph",),
        client=client,
        max_attempts=3,
    )

    row = summary["results"][0]
    assert row["decision"] == "HOLD"
    assert row["status"] == "verifier_error"
    assert row["usage_estimated"] is True
    assert row["accounted_tokens"] == harness.ESTIMATED_TOKENS_PER_ATTEMPT * 3
    assert row["attempts"] == 3
    assert row["attempts_estimated"] is True
    assert row["usage"]["reported"] is False
    assert summary["reported_tokens_used"] == 0
    assert summary["estimated_tokens_used"] == 7500
    assert summary["accounted_tokens_used"] == 7500
    assert summary["llm_verifier_errors"]["rows"] == 1
    assert metrics["condition_metrics"]["graph"]["verifier_error_rate"]["rate"] == 1.0


def test_expected_invalid_cases_zero_client_calls(tmp_path):
    client = FakeClient()
    comparator_factory, comparators = _comparator_factory()
    summary, metrics, output = _run(
        tmp_path,
        cases=[_invalid_raw()],
        labels=[_label("HELD-03", "INVALID_INPUT")],
        conditions=("graph", "text", "semantic"),
        client=client,
        comparator_factory=comparator_factory,
    )

    assert client.calls == 0
    assert comparators == []
    assert len(summary["results"]) == 3
    for row in summary["results"]:
        assert row["decision"] == "INVALID_INPUT"
        assert row["status"] == "invalid_input"
        assert row["accounted_tokens"] == 0
        assert row["attempts"] == 0
        assert row["cache_hit"] == 0
    assert summary["usage"]["accounted_total_tokens"] == 0
    for condition in ("graph", "text", "semantic"):
        block = metrics["condition_metrics"][condition]
        assert block["invalid_input_rows"] == 1
        assert block["decision_counts"] == {"INVALID_INPUT": 1}
        # No store file is created for an invalid input, only an audit log.
        assert not (output / condition / "HELD-03.json").exists()


def test_semantic_abstention_is_counted(tmp_path):
    comparator_factory, comparators = _comparator_factory(SemanticRelation.UNCERTAIN)
    summary, metrics, _ = _run(
        tmp_path,
        cases=[_valid_raw()],
        labels=[_label("HELD-01", "ACCEPT")],
        conditions=("semantic",),
        client=FakeClient(),
        comparator_factory=comparator_factory,
    )

    row = summary["results"][0]
    assert row["decision"] == "HOLD"
    semantic_check = next(check for check in row["checks"] if check["name"] == "semantic")
    assert semantic_check["status"] == "UNCERTAIN"
    assert summary["semantic_uncertain_rows"] == 1
    assert comparators  # one comparator was created for the semantic row
    assert sum(len(comparator.calls) for comparator in comparators) > 0
    block = metrics["condition_metrics"]["semantic"]
    assert block["semantic_abstention_rate"] == {
        "numerator": 1,
        "denominator": 1,
        "rate": 1.0,
        "case_ids": ["HELD-01"],
    }


def test_cache_hit_reports_zero_new_tokens(tmp_path):
    client = FakeClient(cached=True)
    summary, metrics, _ = _run(
        tmp_path,
        cases=[_valid_raw()],
        labels=[_label("HELD-01", "ACCEPT")],
        conditions=("graph",),
        client=client,
    )

    row = summary["results"][0]
    assert row["cache_hit"] == 1
    assert row["accounted_tokens"] == 0
    assert row["usage_estimated"] is False
    assert summary["cache_hits"] == 1
    assert metrics["condition_metrics"]["graph"]["cache_hit_rate"]["rate"] == 1.0


def test_summary_and_metrics_schema(tmp_path):
    summary, metrics, output = _run(
        tmp_path,
        cases=[_valid_raw(), _faulty_raw(), _invalid_raw()],
        labels=[
            _label("HELD-01", "ACCEPT"),
            _label("HELD-02", "HOLD"),
            _label("HELD-03", "INVALID_INPUT"),
        ],
        conditions=("graph", "text", "semantic"),
    )

    summary_keys = {
        "run_id",
        "generated_at",
        "code_revision",
        "manifest_id",
        "manifest_sha256",
        "cases_sha256",
        "labels_sha256",
        "split",
        "conditions",
        "case_count",
        "valid_case_count",
        "invalid_case_count",
        "model",
        "results",
        "budget",
        "usage",
        "reported_tokens_used",
        "estimated_tokens_used",
        "accounted_tokens_used",
        "attempts",
        "cache_hits",
        "llm_verifier_errors",
        "semantic_uncertain_rows",
    }
    assert summary_keys <= set(summary)
    assert summary["split"] == "heldout"
    assert summary["case_count"] == 3
    assert summary["valid_case_count"] == 2
    assert summary["invalid_case_count"] == 1
    manifest_path = tmp_path / "data" / "manifest.json"
    assert summary["manifest_sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    row_keys = {
        "case_id",
        "condition",
        "decision",
        "status",
        "usage",
        "accounted_tokens",
        "usage_estimated",
        "attempts",
        "cache_hit",
        "checks",
        "label_expected_decision",
    }
    for row in summary["results"]:
        assert row_keys <= set(row)

    assert {"condition_metrics", "cost_assumption", "comparison_policy", "labels_caveat"} <= set(metrics)
    for condition in ("graph", "text", "semantic"):
        block = metrics["condition_metrics"][condition]
        assert {
            "decision_counts",
            "faulty_detection_hold_only",
            "faulty_detection_combined",
            "false_accepts",
            "false_holds",
            "valid_acceptance",
            "verifier_error_rate",
            "semantic_abstention_rate",
            "cache_hit_rate",
            "mean_latency_ms",
            "usage",
            "estimated_cost_range_usd",
        } <= set(block)
        assert block["decision_counts"]["INVALID_INPUT"] == 1
        cost = block["estimated_cost_range_usd"]
        assert cost["estimated"] is True
        assert cost["billing_data_available"] is False
        assert cost["low_usd"] <= cost["high_usd"]
        assert "not a billed amount" in cost["assumption"]["note"]
    assert metrics["cost_assumption"]["estimated"] is True
    assert metrics["cost_assumption"]["billing_data_available"] is False
    assert "does not establish or claim superiority" in metrics["comparison_policy"]
    assert "single-reviewer" in metrics["labels_caveat"]
    # Fake graph/text LLM accepts the faulty case; semantic baseline holds it.
    assert metrics["condition_metrics"]["graph"]["false_accepts"]["rate"] == 1.0
    assert metrics["condition_metrics"]["semantic"]["faulty_detection_hold_only"]["rate"] == 1.0
    assert (output / "summary.json").exists()


def test_refuses_non_fresh_output(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "stale.txt").write_text("old run", encoding="utf-8")
    manifest_path = _write_manifest(
        tmp_path, [_valid_raw()], [_label("HELD-01", "ACCEPT")]
    )
    client = FakeClient()
    with pytest.raises(ValueError, match="fresh --output"):
        harness.run_heldout(
            manifest_path=manifest_path,
            output=output,
            client=client,
            split="heldout",
            conditions=("graph",),
            run_id="test-run",
            code_revision="test-revision",
        )
    assert client.calls == 0


def test_cli_defaults_and_condition_parsing():
    parser = harness.build_parser()
    args = parser.parse_args(["--output", "out"])
    assert args.manifest.endswith("data/manifests/evaluation-v2.json")
    assert args.split == "heldout"
    assert args.max_total_tokens == 250_000
    assert args.max_attempts == 3
    assert args.conditions == "graph,text,semantic"
    assert harness.parse_conditions("semantic,graph") == ("graph", "semantic")
    with pytest.raises(ValueError):
        harness.parse_conditions("graph,bogus")


def test_cli_writes_outputs_and_console_summary(tmp_path, monkeypatch, capsys):
    manifest_path = _write_manifest(
        tmp_path, [_valid_raw()], [_label("HELD-01", "ACCEPT")]
    )
    monkeypatch.setattr(harness, "build_client", lambda **_: FakeClient())
    output = tmp_path / "cli-out"
    summary = harness.main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(output),
            "--conditions",
            "graph",
            "--run-id",
            "cli-run",
            "--code-revision",
            "cli-rev",
        ]
    )

    assert summary["run_id"] == "cli-run"
    assert (output / "summary.json").exists()
    assert (output / "metrics.json").exists()
    captured = capsys.readouterr()
    assert "manifest=test-manifest" in captured.out
    assert "ESTIMATE" in captured.out
    assert "does not establish or claim superiority" in captured.out

