import json
from types import SimpleNamespace

from membud.demo import cases
from membud.schemas import Transition
from membud.store import run_case
from membud.verification import verify_with_llm


CHECKS = [
    {"name": "coverage", "status": "PASS", "reason": "All required claims are present.", "reason_code": "COVERAGE_OK", "affected_ids": []},
    {"name": "preservation", "status": "PASS", "reason": "The prior fact is retained.", "reason_code": "PRESERVATION_OK", "affected_ids": []},
    {"name": "faithfulness", "status": "PASS", "reason": "Claims have evidence.", "reason_code": "FAITHFULNESS_OK", "affected_ids": []},
]


class FakeClient:
    model = "deepseek/deepseek-v4.1-flash"

    def __init__(self, payload=None, error=None):
        self.payload = payload or {"checks": CHECKS}
        self.error = error
        self.messages = None

    def complete_json(self, messages, validator=None):
        self.messages = messages
        if self.error:
            raise self.error
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
            latency_ms=42.5,
            request_attempts=1,
            json_retries=0,
            cached=False,
        )


def transition(case_id="valid"):
    raw = next(raw for raw in cases() if raw["case_id"] == case_id)
    return Transition.from_dict(raw)


def test_llm_verifier_returns_structured_decision_and_metadata():
    t = transition()
    client = FakeClient()
    result = verify_with_llm(t, client, representation="graph")
    assert result.decision == "ACCEPT"
    assert result.verifier == client.model
    assert result.prompt_version
    assert result.usage == {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "api_attempts": 1,
        "json_retries": 0,
        "cache_hit": 0,
    }
    assert result.latency_ms == 42.5
    assert result.raw_response == {"checks": CHECKS}
    assert {check.name for check in result.checks} == {"coverage", "preservation", "faithfulness"}


def test_graph_and_text_prompts_include_equivalent_transition_information():
    t = transition()
    graph_client, text_client = FakeClient(), FakeClient()
    graph_result = verify_with_llm(t, graph_client, representation="graph")
    text_result = verify_with_llm(t, text_client, representation="text")
    graph_prompt = graph_client.messages[1]["content"]
    text_prompt = text_client.messages[1]["content"]
    assert "required_claims" in graph_client.messages[0]["content"]
    assert "REQUIRES" in graph_prompt
    assert "required_claims" in text_prompt
    assert "Ia bekerja di Tokopedia." in graph_prompt
    assert "Ia bekerja di Tokopedia." in text_prompt
    assert graph_result.decision == text_result.decision == "ACCEPT"


def test_llm_verifier_holds_on_invalid_schema_or_request_failure():
    t = transition()
    invalid = FakeClient(payload={"checks": [{"name": "coverage", "status": "PASS"}]})
    result = verify_with_llm(t, invalid, representation="text")
    assert result.decision == "HOLD"
    assert all(check.status == "UNCERTAIN" for check in result.checks)
    assert all(check.reason_code == "INVALID_VERIFIER_OUTPUT" for check in result.checks)

    failed = verify_with_llm(t, FakeClient(error=RuntimeError("provider unavailable")))
    assert failed.decision == "HOLD"
    assert all(check.status == "UNCERTAIN" for check in failed.checks)


def test_run_case_logs_llm_provider_failure_and_keeps_state(tmp_path):
    t = transition()
    store, audit = tmp_path / "state.json", tmp_path / "audit.jsonl"
    result = run_case(
        t,
        store,
        audit,
        verifier=lambda transition, representation, prepared: verify_with_llm(
            transition, FakeClient(error=RuntimeError("provider unavailable")),
            representation=representation, prepared_input=prepared,
        ),
    )
    assert result.decision == "HOLD"
    assert not store.exists()
    assert [json.loads(event) for event in audit.read_text().splitlines()][0]["status"] == "verifier_error"
    event = json.loads(audit.read_text())
    assert event["status"] == "verifier_error"
    assert event["result"]["checks"][0]["status"] == "UNCERTAIN"


def test_run_case_can_commit_from_llm_verification_callback(tmp_path):
    t = transition()
    client = FakeClient()
    result = run_case(
        t,
        tmp_path / "state.json",
        tmp_path / "audit.jsonl",
        representation="graph",
        verifier=lambda transition, representation, prepared: verify_with_llm(
            transition, client, representation=representation, prepared_input=prepared
        ),
    )
    assert result.verifier == client.model
    assert json.loads((tmp_path / "state.json").read_text()) == [
        {"id": record.id, "claims": list(record.claims)} for record in t.candidate
    ]
    event = json.loads((tmp_path / "audit.jsonl").read_text())
    assert event["result"]["usage"]["total_tokens"] == 150
    assert event["result"]["raw_response"] == {"checks": CHECKS}
