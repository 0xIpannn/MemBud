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

from membud.checkpoint import CheckpointConfigMismatch, RunCheckpoint
from membud.experiment import load_manifest_entries
from membud.llm.client import CacheMissError
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
    assert args.provider == "claude"
    assert args.manifest.endswith("data/manifests/evaluation-v2.json")
    assert args.split == "heldout"
    assert args.max_total_tokens == 250_000
    assert args.max_attempts == 3
    assert args.conditions == "graph,text,semantic"
    assert harness.parse_conditions("semantic,graph") == ("graph", "semantic")
    with pytest.raises(ValueError):
        harness.parse_conditions("graph,bogus")


def test_build_client_selects_claude_and_gemini_providers(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    claude = harness.build_client(
        max_attempts=2,
        cache_path=str(tmp_path / "claude.sqlite3"),
        require_cache=True,
        provider="claude",
    )
    assert claude.configuration["provider"] == "claude-openai-compatible"
    assert claude.model == "claude-haiku-4-5-20251001"
    assert claude.require_cache is True
    assert claude.cache_path == tmp_path / "claude.sqlite3"

    gemini = harness.build_client(
        max_attempts=2,
        cache_path=str(tmp_path / "gemini.sqlite3"),
        require_cache=False,
        provider="gemini",
    )
    assert gemini.configuration["provider"] == "gemini-openai-compatible"
    assert gemini.model == "gemini-2.5-flash"
    assert gemini.require_cache is False
    assert gemini.cache_path == tmp_path / "gemini.sqlite3"


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



# ---------------------------------------------------------------------------
# Cache-only / offline mode
# ---------------------------------------------------------------------------


class CacheMissClient(FakeClient):
    """Fake client that reports a cache-only miss on every completion.

    Mirrors the real client's miss counter as well as its exception, because
    fail-closed verifier layers (``verify_with_llm``) swallow the exception and
    the harness must still abort loudly.
    """

    def __init__(self):
        super().__init__()
        self.cache_misses = 0

    def complete_json(self, messages, validator=None):
        self.calls += 1
        self.cache_misses += 1
        raise CacheMissError("cache-only mode: no cached completion")


def test_cli_require_cache_flags_parse():
    parser = harness.build_parser()
    assert parser.parse_args(["--output", "out"]).require_cache is False
    assert parser.parse_args(["--output", "out", "--require-cache"]).require_cache is True
    assert parser.parse_args(["--output", "out", "--offline"]).require_cache is True


def test_build_client_forwards_require_cache(monkeypatch, tmp_path):
    created = {}

    class RecordingClient:
        def __init__(self, **kwargs):
            created.update(kwargs)
            for name, value in kwargs.items():
                setattr(self, name, value)
            self.max_attempts = None
            self.cache_path = None

        @classmethod
        def from_deepseek_env(cls, **kwargs):
            return cls(**kwargs)

    monkeypatch.setattr(harness, "OpenAICompatibleChatClient", RecordingClient)
    cache_path = tmp_path / "cache.sqlite3"
    client = harness.build_client(
        max_attempts=2, cache_path=str(cache_path), require_cache=True
    )

    assert created == {"require_cache": True}
    assert client.require_cache is True
    assert client.max_attempts == 2
    assert client.cache_path == cache_path


def test_cli_passes_require_cache_flag_to_build_client(tmp_path, monkeypatch):
    manifest_path = _write_manifest(
        tmp_path, [_valid_raw()], [_label("HELD-01", "ACCEPT")]
    )
    captured = {}

    def fake_build_client(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(harness, "build_client", fake_build_client)
    harness.main(
        [
            "--manifest", str(manifest_path),
            "--output", str(tmp_path / "out-require-cache"),
            "--conditions", "graph",
            "--require-cache",
        ]
    )
    assert captured["require_cache"] is True

    captured.clear()
    harness.main(
        [
            "--manifest", str(manifest_path),
            "--output", str(tmp_path / "out-default"),
            "--conditions", "graph",
        ]
    )
    assert captured["require_cache"] is False


def test_cache_miss_aborts_run_before_next_row(tmp_path):
    client = CacheMissClient()
    with pytest.raises(CacheMissError):
        _run(
            tmp_path,
            cases=[_valid_raw("HELD-01"), _valid_raw("HELD-11")],
            labels=[_label("HELD-01", "ACCEPT"), _label("HELD-11", "ACCEPT")],
            conditions=("graph",),
            client=client,
        )

    assert client.calls == 1  # aborted on the miss instead of trying the next row
    assert client.cache_misses == 1
    assert not (tmp_path / "out" / "summary.json").exists()


def test_cache_miss_in_semantic_condition_is_not_swallowed(tmp_path):
    client = CacheMissClient()
    with pytest.raises(CacheMissError):
        _run(
            tmp_path,
            cases=[_valid_raw()],
            labels=[_label("HELD-01", "ACCEPT")],
            conditions=("semantic",),
            client=client,
            comparator_factory=harness.LLMSemanticComparator,
        )

    assert client.calls == 1


def test_require_cache_flag_recorded_in_summary(tmp_path):
    cached_client = FakeClient()
    cached_client.require_cache = True
    cached_client.cache_path = "cache.sqlite3"
    summary, _, _ = _run(
        tmp_path,
        cases=[_valid_raw()],
        labels=[_label("HELD-01", "ACCEPT")],
        conditions=("graph",),
        client=cached_client,
        output_name="out-cache-only",
    )
    assert summary["require_cache"] is True
    assert summary["cache_path"] == "cache.sqlite3"
    assert summary["cache_misses"] == 0

    default_summary, _, _ = _run(
        tmp_path,
        cases=[_valid_raw()],
        labels=[_label("HELD-01", "ACCEPT")],
        conditions=("graph",),
        output_name="out-default",
    )
    assert default_summary["require_cache"] is False


# ---------------------------------------------------------------------------
# Checkpoint / resume
# ---------------------------------------------------------------------------


def _run_kwargs(
    manifest_path: Path,
    output: Path,
    client: FakeClient,
    **overrides,
) -> dict:
    kwargs = {
        "manifest_path": manifest_path,
        "output": output,
        "client": client,
        "split": "heldout",
        "conditions": ("graph",),
        "max_total_tokens": 250_000,
        "max_attempts": 3,
        "run_id": "resume-run",
        "code_revision": "test-revision",
    }
    kwargs.update(overrides)
    return kwargs


def _partial_run(
    tmp_path: Path,
    *,
    cases: list[dict],
    labels: list[dict],
    max_total_tokens: int = 100,
    output_name: str = "out",
):
    manifest_path = _write_manifest(tmp_path, cases, labels)
    output = tmp_path / output_name
    client = FakeClient(total_tokens=150)
    summary = harness.run_heldout(
        **_run_kwargs(
            manifest_path,
            output,
            client,
            max_total_tokens=max_total_tokens,
        )
    )
    assert client.calls == 1
    assert summary["budget"]["aborted"] is True
    assert (output / "checkpoint.jsonl").exists()
    return manifest_path, output, summary, client


def test_fresh_run_writes_checkpoint_and_still_refuses_reuse(tmp_path):
    manifest_path = _write_manifest(
        tmp_path, [_valid_raw()], [_label("HELD-01", "ACCEPT")]
    )
    output = tmp_path / "out"
    summary = harness.run_heldout(
        **_run_kwargs(manifest_path, output, FakeClient())
    )
    assert summary["resumed"] is False
    assert summary["resumed_rows"] == 0
    assert Path(summary["checkpoint_path"]).resolve() == (output / "checkpoint.jsonl").resolve()
    assert summary["config_fingerprint"]
    assert summary["checkpoint_is_complete"] is True
    assert summary["checkpoint_torn_final_line"] is False
    with pytest.raises(ValueError, match="fresh --output"):
        harness.run_heldout(
            **_run_kwargs(manifest_path, output, FakeClient(), max_total_tokens=10_000)
        )


def test_resume_skips_completed_rows_without_client_calls(tmp_path):
    manifest_path, output, first, _ = _partial_run(
        tmp_path,
        cases=[_valid_raw("HELD-01"), _valid_raw("HELD-11")],
        labels=[_label("HELD-01", "ACCEPT"), _label("HELD-11", "ACCEPT")],
    )
    assert len(first["results"]) == 1

    second_client = FakeClient(total_tokens=150)
    summary = harness.run_heldout(
        **_run_kwargs(
            manifest_path,
            output,
            second_client,
            max_total_tokens=250_000,
            resume=True,
        )
    )
    assert second_client.calls == 1  # only the uncompleted row was called
    assert summary["resumed"] is True
    assert summary["resumed_rows"] == 1
    assert summary["budget"]["resumed"] is True
    assert summary["budget"]["resumed_rows"] == 1
    assert summary["budget"]["accounted_tokens_used"] == 300
    assert summary["budget"]["aborted"] is False
    assert summary["attempts"] == 2
    assert len(summary["results"]) == 2
    assert {row["case_id"] for row in summary["results"]} == {"HELD-01", "HELD-11"}
    assert summary["checkpoint_is_complete"] is True
    assert (output / "metrics.json").exists()

    # A second resume of the completed checkpoint makes no calls at all and
    # rewrites the same artifacts.
    third_client = FakeClient()
    again = harness.run_heldout(
        **_run_kwargs(manifest_path, output, third_client, resume=True)
    )
    assert third_client.calls == 0
    assert again["resumed_rows"] == 2
    assert again["budget"]["accounted_tokens_used"] == 300
    assert again["checkpoint_is_complete"] is True


def test_resume_with_require_cache_makes_no_calls_for_completed_rows(tmp_path):
    manifest_path, output, _, _ = _partial_run(
        tmp_path,
        cases=[_valid_raw("HELD-01"), _valid_raw("HELD-11")],
        labels=[_label("HELD-01", "ACCEPT"), _label("HELD-11", "ACCEPT")],
    )
    client = FakeClient(total_tokens=150)
    client.require_cache = True
    client.cache_path = "cache.sqlite3"
    summary = harness.run_heldout(
        **_run_kwargs(manifest_path, output, client, resume=True)
    )
    assert client.calls == 1  # only the new row; the skipped row never dialled
    assert getattr(client, "cache_misses", 0) == 0
    assert summary["require_cache"] is True
    assert summary["resumed_rows"] == 1


def test_resume_fingerprint_mismatch_aborts_before_any_call(tmp_path):
    manifest_path, output, _, _ = _partial_run(
        tmp_path,
        cases=[_valid_raw("HELD-01"), _valid_raw("HELD-11")],
        labels=[_label("HELD-01", "ACCEPT"), _label("HELD-11", "ACCEPT")],
    )
    client = FakeClient()
    with pytest.raises(CheckpointConfigMismatch) as excinfo:
        harness.run_heldout(
            **_run_kwargs(manifest_path, output, client, max_attempts=5, resume=True)
        )
    assert client.calls == 0
    assert "max_attempts" in str(excinfo.value)

    # A different selected-condition set is also a mismatch.
    with pytest.raises(CheckpointConfigMismatch):
        harness.run_heldout(
            **_run_kwargs(
                manifest_path,
                output,
                FakeClient(),
                conditions=("graph", "text"),
                resume=True,
            )
        )
    assert client.calls == 0


def test_resume_without_checkpoint_raises(tmp_path):
    manifest_path = _write_manifest(
        tmp_path, [_valid_raw()], [_label("HELD-01", "ACCEPT")]
    )
    output = tmp_path / "out"
    output.mkdir()
    (output / "stray.txt").write_text("old run", encoding="utf-8")
    client = FakeClient()
    with pytest.raises(ValueError, match="--resume requires an existing checkpoint"):
        harness.run_heldout(
            **_run_kwargs(manifest_path, output, client, resume=True)
        )
    assert client.calls == 0


def test_resume_tolerates_and_repairs_torn_checkpoint_tail(tmp_path):
    manifest_path, output, _, _ = _partial_run(
        tmp_path,
        cases=[_valid_raw("HELD-01"), _valid_raw("HELD-11"), _valid_raw("HELD-12")],
        labels=[
            _label("HELD-01", "ACCEPT"),
            _label("HELD-11", "ACCEPT"),
            _label("HELD-12", "ACCEPT"),
        ],
    )
    checkpoint_path = output / "checkpoint.jsonl"
    with checkpoint_path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind":"row","row":{"condition":"graph","case_id":"HELD')  # torn

    client = FakeClient(total_tokens=150)
    summary = harness.run_heldout(
        **_run_kwargs(manifest_path, output, client, resume=True)
    )
    assert client.calls == 2  # the torn row and the never-started row re-ran
    assert summary["resumed_rows"] == 1
    assert summary["checkpoint_torn_final_line"] is True
    assert len(summary["results"]) == 3
    assert summary["budget"]["accounted_tokens_used"] == 450

    reloaded = RunCheckpoint.load(checkpoint_path)
    assert reloaded.torn_final_line is False  # torn tail repaired before append
    assert reloaded.is_complete is True
    assert len(reloaded.completed_identities()) == 3


def test_resume_preserves_budget_and_aborts_before_overspending(tmp_path):
    manifest_path, output, first, _ = _partial_run(
        tmp_path,
        cases=[_valid_raw("HELD-01"), _valid_raw("HELD-11")],
        labels=[_label("HELD-01", "ACCEPT"), _label("HELD-11", "ACCEPT")],
    )
    assert first["budget"]["accounted_tokens_used"] == 150

    client = FakeClient(total_tokens=150)
    summary = harness.run_heldout(
        **_run_kwargs(manifest_path, output, client, max_total_tokens=100, resume=True)
    )
    assert client.calls == 0  # the preserved ledger already reached the cap
    assert summary["budget"]["aborted"] is True
    assert summary["budget"]["accounted_tokens_used"] == 150
    assert summary["resumed_rows"] == 1
    assert "token budget reached before graph/HELD-11" in summary["budget"]["abort_reason"]


def test_resume_does_not_replay_invalid_rows_or_duplicate_audit(tmp_path):
    manifest_path, output, first, _ = _partial_run(
        tmp_path,
        cases=[_valid_raw("HELD-01"), _invalid_raw("HELD-14")],
        labels=[_label("HELD-01", "ACCEPT"), _label("HELD-14", "INVALID_INPUT")],
        max_total_tokens=50,
    )
    assert {row["decision"] for row in first["results"]} == {"ACCEPT", "INVALID_INPUT"}
    audit_path = output / "graph" / "audit.jsonl"
    audit_lines_before = len(audit_path.read_text(encoding="utf-8").splitlines())
    assert audit_lines_before == 2

    client = FakeClient()
    summary = harness.run_heldout(
        **_run_kwargs(manifest_path, output, client, resume=True)
    )
    assert client.calls == 0
    assert summary["resumed_rows"] == 2
    assert len(summary["results"]) == 2
    assert summary["budget"]["aborted"] is False
    assert summary["checkpoint_is_complete"] is True
    # Reused rows must not re-append audit events.
    assert len(audit_path.read_text(encoding="utf-8").splitlines()) == audit_lines_before


# ---------------------------------------------------------------------------
# Token-type-aware cost accounting
# ---------------------------------------------------------------------------


def test_cost_estimate_present_per_row_condition_and_run(tmp_path):
    summary, metrics, _ = _run(
        tmp_path,
        cases=[_valid_raw()],
        labels=[_label("HELD-01", "ACCEPT")],
        conditions=("graph",),
        client=FakeClient(total_tokens=150),
    )
    row_cost = summary["results"][0]["cost_estimate"]
    assert row_cost["estimated"] is True
    assert row_cost["billing_data_available"] is False
    assert row_cost["verified_billed"] is False
    assert row_cost["pricing"]["verified_billed"] is False
    assert row_cost["pricing"]["placeholder"] is True
    assert row_cost["pricing"]["rate_basis"] == "placeholder_range"
    assert row_cost["pricing"]["source"]
    assert row_cost["pricing"]["date"]
    assert row_cost["low_usd"] <= row_cost["estimated_usd"] <= row_cost["high_usd"]
    assert row_cost["tokens"]["input"] == 120
    assert row_cost["tokens"]["output"] == 30
    assert row_cost["tokens"]["accounted"] == 150
    assert "not a billed amount" in row_cost["basis"]

    run_cost = summary["cost_estimate"]
    assert run_cost["accounted_tokens"] == 150
    block = metrics["condition_metrics"]["graph"]
    assert block["cost_estimate"] == block["estimated_cost_range_usd"]
    assert block["cost_estimate"]["accounted_tokens"] == 150
    assert metrics["cost_estimate"]["accounted_tokens"] == 150
    assert metrics["cost_assumption"]["estimated"] is True
    assert metrics["cost_assumption"]["billing_data_available"] is False
    assert metrics["cost_assumption"]["verified_billed"] is False
    assert metrics["cost_assumption"]["pricing"]["placeholder"] is True


def test_cache_hit_row_has_zero_cost(tmp_path):
    summary, metrics, _ = _run(
        tmp_path,
        cases=[_valid_raw()],
        labels=[_label("HELD-01", "ACCEPT")],
        conditions=("graph",),
        client=FakeClient(cached=True),
    )
    row_cost = summary["results"][0]["cost_estimate"]
    assert row_cost["estimated_usd"] == 0.0
    assert row_cost["low_usd"] == 0.0
    assert row_cost["high_usd"] == 0.0
    assert row_cost["tokens"]["accounted"] == 0
    assert summary["cost_estimate"]["estimated_usd"] == 0.0
    assert metrics["condition_metrics"]["graph"]["cost_estimate"]["estimated_usd"] == 0.0


def test_cli_price_flags_parse_and_apply_explicit_rates(tmp_path, monkeypatch):
    parser = harness.build_parser()
    defaults = parser.parse_args(["--output", "out"])
    assert defaults.resume is False
    assert defaults.price_input_per_mtok is None
    assert defaults.price_output_per_mtok is None
    assert defaults.price_cached_per_mtok is None
    assert defaults.price_source == ""
    assert defaults.price_date == ""

    parsed = parser.parse_args(
        [
            "--output",
            "out",
            "--resume",
            "--price-input-per-mtok",
            "1.0",
            "--price-output-per-mtok",
            "2.0",
            "--price-source",
            "quote-page",
            "--price-date",
            "2026-01-02",
        ]
    )
    assert parsed.resume is True
    assert parsed.price_input_per_mtok == 1.0
    assert parsed.price_output_per_mtok == 2.0

    manifest_path = _write_manifest(
        tmp_path, [_valid_raw()], [_label("HELD-01", "ACCEPT")]
    )
    monkeypatch.setattr(harness, "build_client", lambda **_: FakeClient())
    summary = harness.main(
        [
            "--manifest",
            str(manifest_path),
            "--output",
            str(tmp_path / "priced-out"),
            "--conditions",
            "graph",
            "--price-input-per-mtok",
            "1.0",
            "--price-output-per-mtok",
            "2.0",
            "--price-source",
            "quote-page",
            "--price-date",
            "2026-01-02",
        ]
    )
    cost = summary["cost_estimate"]
    assert cost["pricing"]["source"] == "quote-page"
    assert cost["pricing"]["date"] == "2026-01-02"
    assert cost["pricing"]["rate_basis"] == "explicit_per_mtok"
    assert cost["pricing"]["placeholder"] is False
    assert cost["low_usd"] == cost["high_usd"]
    # 120 input tokens * $1/M + 30 output tokens * $2/M = $0.00018
    assert cost["low_usd"] == pytest.approx(0.00018)


# ---------------------------------------------------------------------------
# Crash-window recovery and pre-request accounting
# ---------------------------------------------------------------------------


def _strip_checkpoint_row(path: Path, case_id: str) -> None:
    """Simulate a crash after the store/audit write but before the row append."""
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kept = [
        record
        for record in records
        if not (
            record.get("kind") == "row"
            and (record.get("row") or {}).get("case_id") == case_id
        )
        and record.get("kind") != "complete"
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in kept) + "\n", encoding="utf-8"
    )


def test_resume_recovers_accept_whose_checkpoint_row_was_lost(tmp_path):
    manifest_path = _write_manifest(
        tmp_path, [_valid_raw("HELD-01")], [_label("HELD-01", "ACCEPT")]
    )
    output = tmp_path / "out"
    first = harness.run_heldout(**_run_kwargs(manifest_path, output, FakeClient(total_tokens=150)))
    assert first["results"][0]["decision"] == "ACCEPT"
    assert first["budget"]["accounted_tokens_used"] == 150

    # Crash simulation: the store + audit event are durable, the checkpoint row
    # (and the now-unreachable completion marker) are lost.
    _strip_checkpoint_row(output / "checkpoint.jsonl", "HELD-01")
    audit_before = (output / "graph" / "audit.jsonl").read_text(encoding="utf-8")

    resume_client = FakeClient(total_tokens=150)
    resumed = harness.run_heldout(
        **_run_kwargs(manifest_path, output, resume_client, resume=True)
    )

    assert resume_client.calls == 0  # recovery needs no provider/verifier call
    assert resumed["recovered_rows"] == 1
    assert resumed["recovered_without_audit_rows"] == 0
    row = resumed["results"][0]
    assert row["decision"] == "ACCEPT"
    assert row["status"] == "completed"
    assert row["recovered_from_audit"] is True
    assert row["accounted_tokens"] == 150  # original reported usage preserved
    assert row["attempts"] == 1
    assert row["error"] is None
    assert resumed["budget"]["accounted_tokens_used"] == 150
    assert resumed["budget"]["aborted"] is False
    assert resumed["checkpoint_is_complete"] is True
    # Recovery does not duplicate the durable audit event.
    assert (output / "graph" / "audit.jsonl").read_text(encoding="utf-8") == audit_before


def test_pre_request_failure_is_charged_zero_tokens(tmp_path):
    manifest_path = _write_manifest(
        tmp_path, [_valid_raw("HELD-01")], [_label("HELD-01", "ACCEPT")]
    )
    output = tmp_path / "out"
    harness.run_heldout(**_run_kwargs(manifest_path, output, FakeClient(total_tokens=150)))

    # Divergent store residue: recovery must refuse it and run_case then fails
    # its prior-state/version reconciliation *before* any provider call.
    store_path = output / "graph" / "HELD-01.json"
    store_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "version": 1,
                "records": [{"id": "r9", "claims": ["unrelated state"]}],
            }
        ),
        encoding="utf-8",
    )
    _strip_checkpoint_row(output / "checkpoint.jsonl", "HELD-01")

    resume_client = FakeClient(total_tokens=150)
    resumed = harness.run_heldout(
        **_run_kwargs(manifest_path, output, resume_client, resume=True)
    )

    assert resume_client.calls == 0
    row = resumed["results"][0]
    assert row["decision"] == "ERROR"
    assert row["status"] == "error"
    assert row["accounted_tokens"] == 0
    assert row["attempts"] == 0
    assert row["usage_estimated"] is False
    assert "state version conflict" in row["error"]
    assert resumed["budget"]["accounted_tokens_used"] == 0
    assert resumed["budget"]["estimated_tokens_used"] == 0


def test_account_usage_zero_attempts_charges_nothing():
    accounting = harness.account_usage({"api_attempts": 0}, max_attempts=3)
    assert accounting["accounted_tokens"] == 0
    assert accounting["attempts"] == 0
    assert accounting["usage_estimated"] is False
    # Unknown attempt count still uses the conservative fallback.
    fallback = harness.account_usage({}, max_attempts=3)
    assert fallback["accounted_tokens"] == 3 * harness.ESTIMATED_TOKENS_PER_ATTEMPT
    assert fallback["usage_estimated"] is True


def test_resume_recovers_hold_whose_checkpoint_row_was_lost(tmp_path):
    hold_payload = {
        "checks": [
            ALL_PASS_CHECKS[0],
            ALL_PASS_CHECKS[1],
            {
                "name": "faithfulness",
                "status": "FAIL",
                "reason": "Unsupported candidate claim.",
                "reason_code": "UNSUPPORTED_CLAIM",
                "affected_ids": ["memiliki kapal pesiar"],
            },
        ]
    }
    manifest_path = _write_manifest(
        tmp_path, [_faulty_raw("HELD-02")], [_label("HELD-02", "HOLD")]
    )
    output = tmp_path / "out"
    first = harness.run_heldout(
        **_run_kwargs(
            manifest_path,
            output,
            FakeClient(total_tokens=150, payload=hold_payload),
        )
    )
    assert first["results"][0]["decision"] == "HOLD"
    audit_path = output / "graph" / "audit.jsonl"
    audit_before = audit_path.read_text(encoding="utf-8")

    _strip_checkpoint_row(output / "checkpoint.jsonl", "HELD-02")
    resume_client = FakeClient(total_tokens=150)
    resumed = harness.run_heldout(
        **_run_kwargs(manifest_path, output, resume_client, resume=True)
    )

    # A HOLD is durable in the run audit too: it must not be re-executed (paid)
    # nor re-appended a second time.
    assert resume_client.calls == 0
    assert resumed["recovered_rows"] == 1
    row = resumed["results"][0]
    assert row["decision"] == "HOLD"
    assert row["status"] == "completed"
    assert row["recovered_from_audit"] is True
    assert row["accounted_tokens"] == 150
    assert resumed["budget"]["accounted_tokens_used"] == 150
    assert audit_path.read_text(encoding="utf-8") == audit_before


def test_recovered_store_without_audit_stays_visible_to_metrics(tmp_path):
    # A crash between the store write and the audit append leaves an inferable
    # ACCEPT; it must not disappear from the attempted/false-accept views.
    assert "recovered_without_audit" in harness._ATTEMPTED_STATUSES

    entry = load_manifest_entries(ROOT / "data/manifests/evaluation-v2.json")
    transition = next(item.transition for item in entry if item.case_id == "HELD-01")
    store_path = tmp_path / "graph" / "HELD-01.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "version": 1,
                "records": [
                    {"id": record.id, "claims": list(record.claims)}
                    for record in transition.candidate
                ],
            }
        ),
        encoding="utf-8",
    )
    row = harness._recover_unrecorded_row(
        transition=transition,
        condition="graph",
        representation="graph",
        store_path=store_path,
        audit_path=tmp_path / "graph" / "audit.jsonl",
        max_attempts=3,
    )
    assert row is not None
    assert row["status"] == "recovered_without_audit"
    assert row["decision"] == "ACCEPT"
    assert row["accounted_tokens"] == 0
    assert row["usage_estimated"] is False


def test_run_heldout_metrics_report_as_completed_and_fail_closed_itt_for_errors(tmp_path):
    from membud.cost import build_price_tables
    from membud.experiment import ManifestCase

    entries = [
        ManifestCase(case_id="HELD-01", split="heldout"),
        ManifestCase(case_id="HELD-02", split="heldout"),
        ManifestCase(case_id="HELD-03", split="heldout"),
        ManifestCase(case_id="HELD-04", split="heldout"),
    ]
    labels = {
        "HELD-01": {"expected_decision": "ACCEPT"},
        "HELD-02": {"expected_decision": "ACCEPT"},
        "HELD-03": {"expected_decision": "HOLD"},
        "HELD-04": {"expected_decision": "HOLD"},
    }
    rows = [
        {
            "case_id": "HELD-01",
            "condition": "graph",
            "decision": "ACCEPT",
            "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "reported": True},
            "accounted_tokens": 15,
            "usage_estimated": False,
            "attempts": 1,
            "cache_hit": 0,
            "latency_ms": 1.0,
        },
        {
            "case_id": "HELD-02",
            "condition": "graph",
            "decision": "ERROR",
            "status": "error",
            "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "reported": False},
            "accounted_tokens": 0,
            "usage_estimated": False,
            "attempts": 0,
            "cache_hit": 0,
            "latency_ms": None,
        },
        {
            "case_id": "HELD-03",
            "condition": "graph",
            "decision": "HOLD",
            "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "reported": True},
            "accounted_tokens": 15,
            "usage_estimated": False,
            "attempts": 1,
            "cache_hit": 0,
            "latency_ms": 1.0,
        },
        {
            "case_id": "HELD-04",
            "condition": "graph",
            "decision": "HOLD",
            "status": "verifier_error",
            "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None, "reported": False},
            "accounted_tokens": 7500,
            "usage_estimated": True,
            "attempts": 3,
            "cache_hit": 0,
            "latency_ms": 1.0,
        },
    ]

    metrics = harness._condition_metrics(
        condition="graph",
        rows=rows,
        heldout_entries=entries,
        labels=labels,
        price_tables=build_price_tables(),
    )

    # Top-level false_holds and valid_acceptance complement to 1.0 over attempted valid rows.
    assert metrics["valid_acceptance"] == {"numerator": 1, "denominator": 2, "rate": 0.5, "case_ids": ["HELD-01"]}
    assert metrics["false_holds"]["numerator"] == 1
    assert metrics["false_holds"]["denominator"] == 2
    assert metrics["false_holds"]["rate"] == pytest.approx(0.5)
    assert metrics["false_holds"]["decision_holds"] == 0
    assert metrics["false_holds"]["error_holds"] == 1
    assert metrics["false_holds"]["error_hold_case_ids"] == ["HELD-02"]

    # Error-hold on faulty HELD-04 is not credited as an explicit HOLD detection.
    assert metrics["faulty_detection_hold_only"]["numerator"] == 1
    assert metrics["faulty_detection_hold_only"]["denominator"] == 2
    assert metrics["faulty_detection_hold_only"]["error_holds"] == 1
    assert metrics["faulty_detection_hold_only"]["error_hold_case_ids"] == ["HELD-04"]

    # Explicit as_completed vs fail_closed_itt blocks mirror membud.metrics.
    assert metrics["as_completed"]["valid"] == {
        "completed": 1,
        "accepted": 1,
        "acceptance_rate": {"numerator": 1, "denominator": 1, "rate": 1.0},
        "held": 0,
        "false_hold_rate": {"numerator": 0, "denominator": 1, "rate": 0.0},
        "case_ids": ["HELD-01"],
    }
    assert metrics["as_completed"]["faulty"] == {
        "completed": 1,
        "flagged": 1,
        "detection_rate": {"numerator": 1, "denominator": 1, "rate": 1.0},
        "false_accepts": 0,
        "false_accept_rate": {"numerator": 0, "denominator": 1, "rate": 0.0},
        "case_ids": ["HELD-03"],
    }
    assert metrics["fail_closed_itt"]["valid"]["error_holds"] == 1
    assert metrics["fail_closed_itt"]["valid"]["false_hold_rate"]["rate"] == pytest.approx(0.5)
    assert metrics["fail_closed_itt"]["faulty"]["error_holds"] == 1
    assert metrics["fail_closed_itt"]["faulty"]["detection_rate"]["rate"] == pytest.approx(0.5)
    assert metrics["fail_closed_itt"]["faulty"]["not_accepted_rate"]["rate"] == pytest.approx(1.0)


def test_run_heldout_preserves_usage_on_json_repair_exhaustion(tmp_path):
    from membud.llm.client import LLMJSONError

    class BadJSONClient:
        model = "claude-haiku-4-5-20251001"
        configuration = {"provider": "claude-openai-compatible"}
        calls = 0

        def complete_json(self, messages, validator=None):
            self.calls += 1
            raise LLMJSONError(
                "model did not return a valid JSON object",
                attempts=2,
                json_retries=1,
                input_tokens=40,
                output_tokens=15,
                total_tokens=55,
            )

    manifest_path = _write_manifest(
        tmp_path, [_valid_raw("HELD-01")], [_label("HELD-01", "ACCEPT")]
    )
    output = tmp_path / "out"
    client = BadJSONClient()
    summary = harness.run_heldout(
        manifest_path=manifest_path,
        output=output,
        client=client,
        split="heldout",
        conditions=("graph",),
        max_attempts=3,
        run_id="json-repair-usage",
        code_revision="rev",
    )

    row = summary["results"][0]
    assert row["status"] == "verifier_error"
    assert row["decision"] == "HOLD"
    assert row["usage_estimated"] is False
    assert row["accounted_tokens"] == 55
    assert row["attempts"] == 2
    assert row["usage"]["json_retries"] == 1
    assert summary["budget"]["accounted_tokens_used"] == 55
    assert summary["budget"]["estimated_tokens_used"] == 0


def test_resume_rejects_runner_fingerprint_change(tmp_path):
    from membud.experiment import source_fingerprint

    fake_runner = tmp_path / "custom_runner.py"
    fake_runner.write_text("# v1\n", encoding="utf-8")
    manifest_path = _write_manifest(
        tmp_path, [_valid_raw("HELD-01")], [_label("HELD-01", "ACCEPT")]
    )
    output = tmp_path / "out"
    first_client = FakeClient(total_tokens=150)
    kwargs = _run_kwargs(manifest_path, output, first_client)
    kwargs["code_revision"] = source_fingerprint((fake_runner,))
    harness.run_heldout(**kwargs)

    # Simulate an edit to the runner script before resuming.
    fake_runner.write_text("# v2\n", encoding="utf-8")
    resume_client = FakeClient(total_tokens=150)
    resume_kwargs = _run_kwargs(manifest_path, output, resume_client, resume=True)
    resume_kwargs["code_revision"] = source_fingerprint((fake_runner,))
    with pytest.raises(ValueError, match="code_revision"):
        harness.run_heldout(**resume_kwargs)
    assert resume_client.calls == 0
