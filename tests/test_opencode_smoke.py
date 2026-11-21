"""Tests for the retained opencode module-load smoke artifacts and runner."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/run_opencode_load_smoke.py"


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("run_opencode_load_smoke", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke_module()


def test_retained_opencode_smoke_artifacts_are_valid():
    evidence = smoke.verify_smoke_artifacts()
    assert evidence["plugin_path"] == "integrations/opencode/plugin/membud-verify-guard.js"
    assert evidence["probe_log_path"] == "integrations/opencode/smoke/probe.log"
    assert evidence["probe_event"]["event"] == "server_hook_created"
    assert evidence["probe_event"]["plugin"] == "membud-verify-guard"
    assert evidence["probe_event"]["directory"] == "smoke/project"
    assert evidence["probe_event"]["memoryStatePath"] == "smoke/project/memory-state.json"
    assert smoke.main(["--check"]) == 0


def test_verify_smoke_artifacts_detects_digest_tampering(tmp_path):
    probe_path = tmp_path / "probe.log"
    evidence_path = tmp_path / "smoke-evidence.json"
    probe_path.write_text(smoke.PROBE_ARTIFACT.read_text(encoding="utf-8"), encoding="utf-8")
    evidence = json.loads(smoke.EVIDENCE_ARTIFACT.read_text(encoding="utf-8"))
    evidence["probe_log_sha256"] = "0" * 64
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(AssertionError, match="probe_log_sha256"):
        smoke.verify_smoke_artifacts(probe_path=probe_path, evidence_path=evidence_path)


def test_run_module_load_smoke_orchestration_and_fail_closed_probe_check(tmp_path):
    fake_bin = tmp_path / "fake-opencode"
    fake_bin.write_text(
        """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("opencode fake-1.0")
    raise SystemExit(0)
probe_file = os.environ.get("MEMBUD_PLUGIN_PROBE_FILE")
if os.environ.get("WRITE_PROBE") == "1" and probe_file:
    Path(probe_file).write_text(json.dumps({
        "event": "server_hook_created",
        "plugin": "membud-verify-guard",
        "directory": os.getcwd(),
        "verifyUrl": "http://127.0.0.1:1/verify",
        "memoryStatePath": str(Path(os.getcwd()) / "memory-state.json"),
        "toolNames": ["memory_write"],
        "at": "2026-11-21T00:00:00Z",
    }) + "\\n", encoding="utf-8")
""",
        encoding="utf-8",
    )
    fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IEXEC)

    # Without WRITE_PROBE, runner must fail loudly when no probe line is emitted.
    with pytest.raises(RuntimeError, match="did not invoke plugin load probe"):
        smoke.run_module_load_smoke(opencode_bin=str(fake_bin))

    # With WRITE_PROBE=1, temp directories are redacted to repo-relative strings.
    env_before = os.environ.get("WRITE_PROBE")
    os.environ["WRITE_PROBE"] = "1"
    try:
        probe, evidence = smoke.run_module_load_smoke(opencode_bin=str(fake_bin))
    finally:
        if env_before is None:
            os.environ.pop("WRITE_PROBE", None)
        else:
            os.environ["WRITE_PROBE"] = env_before

    assert probe["directory"] == "smoke/project"
    assert probe["memoryStatePath"] == "smoke/project/memory-state.json"
    assert evidence["opencode_version"] == "opencode fake-1.0"

    with pytest.raises(FileNotFoundError):
        smoke.run_module_load_smoke(opencode_bin=str(tmp_path / "missing-opencode"))


def test_plugin_exports_both_server_and_setup_hooks(tmp_path):
    node_bin = shutil.which("node")
    if node_bin is None:
        return
    probe_file = tmp_path / "node-probe.log"
    plugin_uri = (ROOT / "integrations/opencode/plugin/membud-verify-guard.js").as_uri()
    script = f"""
import assert from "node:assert/strict";
import plugin, {{ server, setup }} from {json.dumps(plugin_uri)};
assert.equal(typeof plugin.server, "function");
assert.equal(typeof plugin.setup, "function");
assert.equal(server, plugin.server);
assert.equal(setup, plugin.setup);
const registered = [];
const ctx = {{
  directory: {json.dumps(str(tmp_path))},
  options: {{ verifyUrl: "http://127.0.0.1:1/verify" }},
  tool: {{ hook: (name, fn) => registered.push([name, fn]) }},
}};
const hooks = await plugin.setup(ctx);
assert.equal(typeof hooks["tool.execute.before"], "function");
assert.equal(registered.length, 1);
assert.equal(registered[0][0], "execute.before");
"""
    env = os.environ.copy()
    env["MEMBUD_PLUGIN_PROBE_FILE"] = str(probe_file)
    res = subprocess.run(
        [node_bin, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
        check=True,
    )
    assert res.returncode == 0
    lines = [json.loads(line) for line in probe_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["event"] == "server_hook_created"
    assert lines[0]["plugin"] == "membud-verify-guard"
