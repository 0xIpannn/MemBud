"""Tests for the controlled graph false-hold ablation (G0/G1/G2).

All tests are deterministic: fake LLM clients only, no network, no paid calls.
The real manifest is used only to read frozen cases; no evaluation is rerun.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from membud.experiment import load_manifest_entries
from membud.graph import build_graph, build_llm_graph
from membud.llm.client import CacheMissError
from membud.verification import (
    LLM_PROMPT_VERSION,
    LLM_PROMPT_VERSION_V1,
    POLICY_CLARIFICATION_SENTENCE,
    _llm_system_prompt,
    verify_with_llm,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/run_graph_false_hold_ablation.py"
MANIFEST = ROOT / "data/manifests/evaluation-v2.json"


def _load_ablation():
    spec = importlib.util.spec_from_file_location("run_graph_false_hold_ablation", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ablation = _load_ablation()

CHECKS = [
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


class FakeClient:
    model = "fake-model"

    def __init__(self, payload=None):
        self.payload = payload or {"checks": CHECKS}
        self.messages = None
        self.calls = 0

    def complete_json(self, messages, validator=None):
        self.calls += 1
        self.messages = messages
        if validator:
            validator(self.payload)
        return SimpleNamespace(
            json=self.payload,
            content=json.dumps(self.payload),
            model=self.model,
            input_tokens=120,
            output_tokens=30,
            total_tokens=150,
            request_id="request-1",
            latency_ms=12.0,
            request_attempts=1,
            json_retries=0,
            cached=False,
        )


class CacheMissClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.cache_misses = 0

    def complete_json(self, messages, validator=None):
        self.cache_misses += 1
        raise CacheMissError("cache-only miss", cache_key="k")


def _heldout_transition(case_id: str):
    entries = {entry.case_id: entry for entry in load_manifest_entries(MANIFEST)}
    return entries[case_id].transition


# ---------------------------------------------------------------------------
# Graph serialization variant (G1)
# ---------------------------------------------------------------------------


def test_build_llm_graph_default_is_canonical_graph():
    t = _heldout_transition("HELD-01")
    assert build_llm_graph(t) == build_graph(t)


def test_build_llm_graph_omits_only_null_authorization_keys():
    t = _heldout_transition("HELD-01")
    canonical = build_graph(t)
    variant = build_llm_graph(t, omit_null_authorization=True)

    assert len(variant["nodes"]) == len(canonical["nodes"])
    assert variant["edges"] == canonical["edges"]
    canonical_ops = [node for node in canonical["nodes"] if node["type"] == "operation"]
    variant_ops = [node for node in variant["nodes"] if node["type"] == "operation"]
    assert canonical_ops and variant_ops
    assert all(node.get("authorization") is None for node in canonical_ops)
    assert all("authorization" not in node for node in variant_ops)
    # Non-operation nodes are untouched.
    assert [
        node for node in variant["nodes"] if node["type"] != "operation"
    ] == [
        node for node in canonical["nodes"] if node["type"] != "operation"
    ]


def test_build_llm_graph_keeps_structured_authorization():
    t = _heldout_transition("HELD-01")
    # HELD-01 has a single NOOP; build a transition with a structured DELETE
    # authorization to prove the variant only removes nulls.
    delete_transition = type(t).from_dict(
        {
            "case_id": "g1-delete-auth",
            "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
            "candidate": [],
            "evidence": [
                {
                    "id": "e1",
                    "source": "test",
                    "text": "The user moved away.",
                    "supports": [],
                }
            ],
            "required_claims": [],
            "operations": [
                {
                    "type": "DELETE",
                    "target_id": "r1",
                    "reason": "Relocation confirmed by evidence.",
                    "authorization": {
                        "source": "test",
                        "evidence_id": "e1",
                        "target_id": "r1",
                        "rationale": "The user moved away.",
                    },
                }
            ],
        }
    )
    variant = build_llm_graph(delete_transition, omit_null_authorization=True)
    operation = next(node for node in variant["nodes"] if node["type"] == "operation")
    assert operation["authorization"]["evidence_id"] == "e1"
    assert any(edge["type"] == "AUTHORIZED_BY" for edge in variant["edges"])


def test_build_llm_graph_does_not_mutate_canonical_graph():
    t = _heldout_transition("HELD-01")
    build_llm_graph(t, omit_null_authorization=True)
    canonical = build_graph(t)
    assert any(
        node["type"] == "operation" and "authorization" in node and node["authorization"] is None
        for node in canonical["nodes"]
    )


# ---------------------------------------------------------------------------
# Prompt variant (G2)
# ---------------------------------------------------------------------------


def test_default_prompt_is_clarified():
    prompt = _llm_system_prompt()
    assert POLICY_CLARIFICATION_SENTENCE in prompt
    assert "required only for DELETE" in prompt


def test_legacy_prompt_available_without_clarification():
    prompt = _llm_system_prompt(policy_clarification=False)
    assert POLICY_CLARIFICATION_SENTENCE not in prompt
    assert "An explicit operation alone does not authorize an unsupported change." in prompt


def test_verify_with_llm_defaults_to_clarified_prompt():
    t = _heldout_transition("HELD-01")
    client = FakeClient()
    result = verify_with_llm(t, client, "graph")
    assert result.prompt_version == LLM_PROMPT_VERSION
    assert POLICY_CLARIFICATION_SENTENCE in client.messages[0]["content"]


def test_verify_with_llm_legacy_prompt_selection():
    t = _heldout_transition("HELD-01")
    client = FakeClient()
    result = verify_with_llm(t, client, "graph", policy_clarification=False)
    assert result.prompt_version == LLM_PROMPT_VERSION_V1
    assert POLICY_CLARIFICATION_SENTENCE not in client.messages[0]["content"]


def test_verify_with_llm_omit_null_authorization_changes_rendered_input():
    t = _heldout_transition("HELD-01")
    default_client, variant_client = FakeClient(), FakeClient()
    verify_with_llm(t, default_client, "graph")
    verify_with_llm(t, variant_client, "graph", omit_null_authorization=True)
    assert '"authorization": null' in default_client.messages[1]["content"]
    assert '"authorization": null' not in variant_client.messages[1]["content"]
    # Node/edge content is otherwise preserved in the serialization.
    assert '"type": "operation"' in variant_client.messages[1]["content"]
    assert '"AUTHORIZED_BY"' not in variant_client.messages[1]["content"]


# ---------------------------------------------------------------------------
# CLI/helper functions
# ---------------------------------------------------------------------------


def test_parse_cases_and_variants():
    assert ablation.parse_cases("HELD-01, HELD-02 ,,") == ("HELD-01", "HELD-02")
    assert ablation.parse_variants("graph_policy_clarified,graph_cache") == (
        "graph_cache",
        "graph_policy_clarified",
    )
    with pytest.raises(ValueError, match="at least one case"):
        ablation.parse_cases(" , ")
    with pytest.raises(ValueError, match="unsupported variant"):
        ablation.parse_variants("graph_cache,nope")


def test_any_paid_variant():
    assert ablation.any_paid_variant(("graph_cache",)) is False
    assert ablation.any_paid_variant(("graph_cache", "graph_no_null_auth")) is True
    assert ablation.any_paid_variant(("graph_policy_clarified",)) is True


def test_account_usage_reported_estimate_and_cache_hit():
    reported = ablation.account_usage(
        {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cache_hit": 0}, max_attempts=3
    )
    assert reported["accounted_tokens"] == 120
    assert reported["usage_estimated"] is False

    cached = ablation.account_usage({"cache_hit": 1}, max_attempts=3)
    assert cached["accounted_tokens"] == 0
    assert cached["usage_estimated"] is False

    estimated = ablation.account_usage({}, max_attempts=3)
    assert estimated["accounted_tokens"] == 3 * ablation.ESTIMATED_TOKENS_PER_ATTEMPT
    assert estimated["usage_estimated"] is True


def test_failed_check_classification_helpers():
    payload = {
        "checks": [
            {"name": "coverage", "status": "PASS", "reason": "ok", "reason_code": "COVERAGE_OK"},
            {
                "name": "preservation",
                "status": "FAIL",
                "reason": "REVISE operation has authorization null",
                "reason_code": "PRIOR_RECORD_UNAUTHORIZED_CHANGE",
            },
        ]
    }
    assert ablation.preservation_failed(payload) is True
    assert ablation.authorization_mentions(payload) == 1
    assert ablation.preservation_failed(None) is False


def test_summarize_variant_counts_conversions_and_failures():
    rows = [
        {
            "variant": "graph_policy_clarified",
            "decision": "ACCEPT",
            "expected_decision": "ACCEPT",
            "preservation_failed": False,
            "authorization_mentions": 0,
            "accounted_tokens": 100,
            "cache_hit": 0,
        },
        {
            "variant": "graph_policy_clarified",
            "decision": "HOLD",
            "expected_decision": "ACCEPT",
            "preservation_failed": True,
            "authorization_mentions": 1,
            "accounted_tokens": 100,
            "cache_hit": 0,
        },
        {
            "variant": "graph_cache",
            "decision": "HOLD",
            "expected_decision": "ACCEPT",
            "preservation_failed": True,
            "authorization_mentions": 1,
            "accounted_tokens": 50,
            "cache_hit": 1,
        },
    ]
    summary = ablation.summarize_variant(rows, "graph_policy_clarified")
    assert summary["rows"] == 2
    assert summary["decisions"] == {"ACCEPT": 1, "HOLD": 1, "ERROR": 0}
    assert summary["false_hold_conversions"] == 1
    assert summary["false_holds_remaining"] == 1
    assert summary["preservation_failure_rows"] == 1
    assert summary["authorization_reason_mentions"] == 1
    assert summary["accounted_tokens"] == 200


# ---------------------------------------------------------------------------
# run_ablation (fake clients; no network)
# ---------------------------------------------------------------------------


def test_run_ablation_writes_summary_with_fake_clients(tmp_path):
    output = tmp_path / "out"
    summary = ablation.run_ablation(
        manifest_path=MANIFEST,
        output=output,
        clients={"graph_cache": FakeClient(), "graph_no_null_auth": FakeClient()},
        cases=["HELD-01"],
        variants=["graph_cache", "graph_no_null_auth"],
        max_total_tokens=10_000,
        max_attempts=3,
        run_id="test-run",
        code_revision="test-rev",
    )
    assert (output / "summary.json").exists()
    written = json.loads((output / "summary.json").read_text())
    assert written["run_id"] == "test-run"
    assert len(written["rows"]) == 2
    assert written["budget"]["aborted"] is False
    assert set(written["variant_metrics"]) == {"graph_cache", "graph_no_null_auth"}
    assert summary["rows"][0]["prompt_version"] == LLM_PROMPT_VERSION_V1


def test_run_ablation_policy_variant_uses_clarified_prompt(tmp_path):
    client = FakeClient()
    ablation.run_ablation(
        manifest_path=MANIFEST,
        output=tmp_path / "out",
        clients={"graph_policy_clarified": client},
        cases=["HELD-01"],
        variants=["graph_policy_clarified"],
        max_total_tokens=10_000,
        max_attempts=3,
    )
    assert POLICY_CLARIFICATION_SENTENCE in client.messages[0]["content"]
    row = json.loads((tmp_path / "out" / "summary.json").read_text())["rows"][0]
    assert row["prompt_version"] == LLM_PROMPT_VERSION


def test_run_ablation_aborts_on_budget_before_next_row(tmp_path):
    summary = ablation.run_ablation(
        manifest_path=MANIFEST,
        output=tmp_path / "out",
        clients={"graph_cache": FakeClient()},
        cases=["HELD-01", "HELD-02"],
        variants=["graph_cache"],
        max_total_tokens=1,
        max_attempts=3,
    )
    assert summary["budget"]["aborted"] is True
    assert len(summary["rows"]) == 1
    assert "token budget" in summary["budget"]["abort_reason"]


def test_run_ablation_cache_only_miss_aborts(tmp_path):
    with pytest.raises(CacheMissError):
        ablation.run_ablation(
            manifest_path=MANIFEST,
            output=tmp_path / "out",
            clients={"graph_cache": CacheMissClient()},
            cases=["HELD-01"],
            variants=["graph_cache"],
            max_total_tokens=10_000,
            max_attempts=3,
        )


def test_main_refuses_paid_variants_without_confirmation(tmp_path, capsys):
    result = ablation.main(
        [
            "--variants",
            "graph_cache,graph_no_null_auth",
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert result == 2
    assert "confirm-paid-call" in capsys.readouterr().err


def test_build_client_cache_only_needs_no_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    client = ablation.build_client(
        max_attempts=1, cache_path=str(tmp_path / "cache.sqlite3"), cache_only=True
    )
    assert client.require_cache is True
    assert client.cache_path == tmp_path / "cache.sqlite3"
