"""Tests for the opt-in live opencode denial harness.

These tests are deterministic and make no network or paid model call: they
exercise only the pure event/workspace helpers in
``scripts/run_live_opencode_denial.py`` with synthetic event streams and
throw-away directories.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/run_live_opencode_denial.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("run_live_opencode_denial", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


def _event(kind: str, **part):
    return {"type": kind, "part": part}


def _tool_event(tool: str, state: dict) -> dict:
    return _event("tool_use", type="tool", tool=tool, state=state)


def _step_finish(*, input_tokens: int, output_tokens: int, cost: float) -> dict:
    return _event(
        "step_finish",
        tokens={"input": input_tokens, "output": output_tokens, "reasoning": 0},
        cost=cost,
    )


def _stdout(events) -> str:
    # Mix in non-JSON noise to prove tolerant parsing.
    return "\n".join(["starting up", *[json.dumps(event) for event in events], "done"])


def _write_state(path: Path, payload) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------


def test_parse_events_ignores_non_json_lines():
    events = harness.parse_events('noise\n{"type":"text","part":{"text":"hi"}}\n\n')
    assert events == [{"type": "text", "part": {"text": "hi"}}]


def test_tool_events_filters_by_tool_name():
    events = harness.parse_events(
        _stdout(
            [
                _tool_event("memory_write", {"status": "error"}),
                _tool_event("bash", {"status": "completed"}),
            ]
        )
    )
    found = harness.tool_events(events)
    assert len(found) == 1
    assert found[0]["part"]["tool"] == "memory_write"


def test_text_events_collects_text_parts():
    events = harness.parse_events(
        _stdout([_event("text", text="TOOL_OUTCOME=denied"), _event("tool_use", tool="x")])
    )
    assert harness.text_events(events) == ["TOOL_OUTCOME=denied"]


def test_usage_summary_sums_tokens_and_cost():
    events = harness.parse_events(
        _stdout(
            [
                _step_finish(input_tokens=100, output_tokens=10, cost=0.001),
                _step_finish(input_tokens=50, output_tokens=5, cost=0.0005),
            ]
        )
    )
    usage = harness.usage_summary(events)
    assert usage["steps"] == 2
    assert usage["input_tokens"] == 150
    assert usage["output_tokens"] == 15
    assert usage["estimated_cost_usd"] == pytest.approx(0.0015)


# ---------------------------------------------------------------------------
# Outcome assertions (synthetic live-event fixtures)
# ---------------------------------------------------------------------------


def _hold_evidence(tmp_path: Path):
    state = tmp_path / "state.json"
    _write_state(state, harness.INITIAL_STATE)
    capture = tmp_path / "capture.jsonl"
    sha_before = hashlib.sha256(state.read_bytes()).hexdigest()
    result = {
        "stdout": _stdout(
            [
                _tool_event(
                    "memory_write",
                    {
                        "status": "error",
                        "error": (
                            "MemBud denied memory_write: HOLD [UNSUPPORTED_CLAIM] — "
                            "faithfulness=FAIL(UNSUPPORTED_CLAIM: no provenance)"
                        ),
                    },
                ),
                _event("text", text="TOOL_OUTCOME=denied — HOLD on UNSUPPORTED_CLAIM"),
                _step_finish(input_tokens=100, output_tokens=10, cost=0.001),
            ]
        )
    }
    return state, capture, sha_before, result


def test_assert_hold_outcome_accepts_a_real_denial_shape(tmp_path):
    state, capture, sha_before, result = _hold_evidence(tmp_path)
    evidence = harness.assert_hold_outcome(
        result, state_path=state, state_sha_before=sha_before, capture_path=capture
    )
    assert evidence["tool_call_status"] == "error"
    assert evidence["tool_executed"] is False
    assert "UNSUPPORTED_CLAIM" in evidence["denial"]
    assert evidence["state_sha256_unchanged"] == sha_before


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda r: r["stdout"].replace('"status": "error"', '"status": "completed"'), "status 'error'"),
        (lambda r: r["stdout"].replace("UNSUPPORTED_CLAIM", "SOMETHING_ELSE"), "UNSUPPORTED_CLAIM"),
        (
            lambda r: r["stdout"]
            .replace("TOOL_OUTCOME=denied", "TOOL_OUTCOME=applied")
            .replace("HOLD on UNSUPPORTED_CLAIM", "everything fine"),
            "report the denial",
        ),
    ],
)
def test_assert_hold_outcome_rejects_bad_evidence(tmp_path, mutate, message):
    state, capture, sha_before, result = _hold_evidence(tmp_path)
    broken = dict(result)
    broken["stdout"] = mutate(dict(result))
    with pytest.raises(AssertionError, match=message):
        harness.assert_hold_outcome(
            broken, state_path=state, state_sha_before=sha_before, capture_path=capture
        )


def test_assert_hold_outcome_rejects_changed_state(tmp_path):
    state, capture, sha_before, result = _hold_evidence(tmp_path)
    _write_state(state, {"schema_version": 2, "version": 1, "records": []})
    with pytest.raises(AssertionError, match="state changed"):
        harness.assert_hold_outcome(
            result, state_path=state, state_sha_before=sha_before, capture_path=capture
        )


def test_assert_hold_outcome_rejects_executed_tool(tmp_path):
    state, capture, sha_before, result = _hold_evidence(tmp_path)
    capture.write_text('{"stage": "executed"}\n', encoding="utf-8")
    with pytest.raises(AssertionError, match="tool executed"):
        harness.assert_hold_outcome(
            result, state_path=state, state_sha_before=sha_before, capture_path=capture
        )


def test_assert_accept_outcome_accepts_persisted_candidate(tmp_path):
    state = tmp_path / "state.json"
    capture = tmp_path / "capture.jsonl"
    candidate = harness.ACCEPT_CANDIDATE
    _write_state(state, {"schema_version": 2, "version": 1, "records": candidate})
    capture.write_text('{"stage": "executed"}\n', encoding="utf-8")
    result = {
        "stdout": _stdout(
            [
                _tool_event(
                    "memory_write",
                    {"status": "completed", "output": "memory_write applied 2 record(s)"},
                ),
                _event("text", text="TOOL_OUTCOME=memory_write applied 2 record(s)"),
                _step_finish(input_tokens=40, output_tokens=5, cost=0.0002),
            ]
        )
    }
    evidence = harness.assert_accept_outcome(
        result, state_path=state, candidate=candidate, capture_path=capture
    )
    assert evidence["tool_call_status"] == "completed"
    assert evidence["tool_executed"] is True
    assert json.loads(state.read_text())["records"] == candidate


def test_assert_accept_outcome_rejects_unchanged_state(tmp_path):
    state = tmp_path / "state.json"
    capture = tmp_path / "capture.jsonl"
    _write_state(state, harness.INITIAL_STATE)
    capture.write_text('{"stage": "executed"}\n', encoding="utf-8")
    result = {"stdout": _stdout([_tool_event("memory_write", {"status": "completed"})])}
    with pytest.raises(AssertionError, match="version 1"):
        harness.assert_accept_outcome(
            result, state_path=state, candidate=harness.ACCEPT_CANDIDATE, capture_path=capture
        )


def test_assert_accept_outcome_rejects_excess_capture_lines(tmp_path):
    state = tmp_path / "state.json"
    capture = tmp_path / "capture.jsonl"
    _write_state(state, {"schema_version": 2, "version": 1, "records": harness.ACCEPT_CANDIDATE})
    capture.write_text('{"stage": "executed"}\n{"stage": "executed"}\n', encoding="utf-8")
    result = {"stdout": _stdout([_tool_event("memory_write", {"status": "completed"})])}
    with pytest.raises(AssertionError, match="exactly one executed"):
        harness.assert_accept_outcome(
            result, state_path=state, candidate=harness.ACCEPT_CANDIDATE, capture_path=capture
        )


# ---------------------------------------------------------------------------
# Workspace/config helpers
# ---------------------------------------------------------------------------


def test_build_config_wires_guard_and_tool_plugins(tmp_path):
    config = harness.build_config(
        home=tmp_path / "home",
        project=tmp_path / "project",
        guard_plugin=ROOT / "integrations/opencode/plugin/membud-verify-guard.js",
        tool_plugin=ROOT / "integrations/opencode/live/memory-tool-plugin.mjs",
        sidecar_url="http://127.0.0.1:8787",
    )
    assert config["model"] == "deepseek/deepseek-flash"
    assert config["permission"] == {"edit": "allow", "bash": "allow"}
    assert config["provider"]["deepseek"]["options"]["apiKey"] == "{env:DEEPSEEK_API_KEY}"
    guard_spec, guard_options = config["plugin"][0]
    assert guard_spec.startswith("file://")
    assert guard_options["toolNames"] == ["memory_write"]
    assert guard_options["verifyUrl"] == "http://127.0.0.1:8787/verify"
    assert guard_options["memoryStatePath"].endswith(".membud-memory.json")
    assert config["plugin"][1].startswith("file://")


def test_prepare_workspace_creates_isolated_layout(tmp_path):
    plugin_root = tmp_path / "global-opencode"
    modules = plugin_root / "node_modules/@opencode-ai/plugin"
    modules.mkdir(parents=True)
    (modules / "index.js").write_text("export default {};\n", encoding="utf-8")
    (plugin_root / "package.json").write_text('{"dependencies": {}}\n', encoding="utf-8")
    models_cache = tmp_path / "models.json"
    models_cache.write_text('{"deepseek": {}}\n', encoding="utf-8")
    tool_plugin = tmp_path / "memory-tool-plugin.mjs"
    tool_plugin.write_text("export default {};\n", encoding="utf-8")

    paths = harness.prepare_workspace(
        tmp_path / "work",
        plugin_package_root=plugin_root,
        models_cache=models_cache,
        guard_plugin=ROOT / "integrations/opencode/plugin/membud-verify-guard.js",
        tool_plugin=tool_plugin,
    )

    assert paths["config"].exists()
    assert json.loads(paths["state"].read_text()) == harness.INITIAL_STATE
    assert (paths["home"] / ".cache/opencode/models.json").exists()
    assert (paths["plugins_dir"] / "node_modules").is_symlink()
    assert (paths["config_dir"] / "node_modules").is_dir()
    config = json.loads(paths["config"].read_text())
    assert len(config["plugin"]) == 2


def test_prepare_workspace_rejects_missing_plugin_package(tmp_path):
    with pytest.raises(FileNotFoundError, match="node_modules"):
        harness.prepare_workspace(
            tmp_path / "work",
            plugin_package_root=tmp_path / "missing",
            models_cache=tmp_path / "missing-models.json",
            guard_plugin=ROOT / "integrations/opencode/plugin/membud-verify-guard.js",
            tool_plugin=ROOT / "integrations/opencode/live/memory-tool-plugin.mjs",
        )


# ---------------------------------------------------------------------------
# Paid-call guard
# ---------------------------------------------------------------------------


def test_main_refuses_without_paid_call_confirmation(capsys):
    assert harness.main([]) == 2
    assert "confirm-paid-call" in capsys.readouterr().err


def test_main_refuses_without_api_key(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert harness.main(["--confirm-paid-call"]) == 2
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err
