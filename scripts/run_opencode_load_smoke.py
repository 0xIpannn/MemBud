#!/usr/bin/env python3
"""Run and verify the no-model opencode plugin module-load smoke.

Starts an isolated ``opencode debug agents --standalone`` invocation with
``OPENCODE_DISABLE_MODELS_FETCH=true``, ``OPENCODE_DISABLE_AUTOUPDATE=true``,
and ``MEMBUD_PLUGIN_PROBE_FILE`` pointing to a temporary probe log.  The real
``opencode`` binary loads ``integrations/opencode/plugin`` directly from disk
(no npm install, no model calls, no network), invokes the guard plugin hook
entry point, and writes a probe event.

Outputs (redacted, repo-relative):
- ``integrations/opencode/smoke/probe.log``
- ``integrations/opencode/smoke/smoke-evidence.json``

Pass ``--check`` to verify that the retained smoke artifacts are well-formed
and match each other's SHA-256 digests (usable in offline/CI environments
without an installed ``opencode`` binary).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "integrations/opencode/plugin"
PLUGIN_FILE = PLUGIN_DIR / "membud-verify-guard.js"
SMOKE_DIR = REPO_ROOT / "integrations/opencode/smoke"
PROBE_ARTIFACT = SMOKE_DIR / "probe.log"
EVIDENCE_ARTIFACT = SMOKE_DIR / "smoke-evidence.json"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _redact_probe_entry(raw_entry: dict[str, Any], project_dir: Path) -> dict[str, Any]:
    project_prefix = str(project_dir.resolve())
    redacted = dict(raw_entry)
    directory = redacted.get("directory")
    if isinstance(directory, str):
        redacted["directory"] = (
            directory.replace(project_prefix, "smoke/project")
            if project_prefix in directory
            else "smoke/project"
        )
    state_path = redacted.get("memoryStatePath")
    if isinstance(state_path, str):
        redacted["memoryStatePath"] = (
            state_path.replace(project_prefix, "smoke/project")
            if project_prefix in state_path
            else "smoke/project/memory-state.json"
        )
    return redacted


def _detect_opencode_version(opencode_bin: str) -> str:
    result = subprocess.run(
        [opencode_bin, "--version"],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0].strip() if output else "unknown"


def run_module_load_smoke(
    *,
    opencode_bin: str = "/usr/bin/opencode",
    plugin_dir: Path = PLUGIN_DIR,
    timeout_seconds: float = 30.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute the real opencode binary in an isolated temp dir and return (probe, evidence)."""
    resolved_bin = shutil.which(opencode_bin) or opencode_bin
    if not Path(resolved_bin).exists():
        raise FileNotFoundError(f"opencode binary not found: {opencode_bin}")

    version = _detect_opencode_version(resolved_bin)
    with tempfile.TemporaryDirectory(prefix="membud-opencode-smoke-") as tmp_root:
        tmp = Path(tmp_root)
        home = tmp / "home"
        cfg_dir = home / ".config" / "opencode"
        cache_dir = home / ".cache" / "opencode"
        data_dir = home / ".local" / "share"
        state_dir = home / ".local" / "state"
        project = tmp / "project"
        for path in (cfg_dir, cache_dir, data_dir, state_dir, project):
            path.mkdir(parents=True, exist_ok=True)

        plugin_spec = f"file://{plugin_dir.resolve()}"
        cfg_path = cfg_dir / "opencode.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "autoupdate": False,
                    "plugin": [
                        [
                            plugin_spec,
                            {
                                "verifyUrl": "http://127.0.0.1:1/verify",
                                "memoryStatePath": "./memory-state.json",
                                "timeoutMs": 500,
                            },
                        ]
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        probe_path = tmp / "probe.log"
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_CACHE_HOME": str(cache_dir),
                "XDG_DATA_HOME": str(data_dir),
                "XDG_STATE_HOME": str(state_dir),
                "OPENCODE_CONFIG": str(cfg_path),
                "OPENCODE_DISABLE_MODELS_FETCH": "true",
                "OPENCODE_DISABLE_AUTOUPDATE": "true",
                "MEMBUD_PLUGIN_PROBE_FILE": str(probe_path),
            }
        )
        proc = subprocess.run(
            [resolved_bin, "debug", "agents", "--standalone", "--print-logs", "--log-level", "info"],
            cwd=str(project),
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout_seconds,
            check=True,
        )
        if not probe_path.is_file():
            raise RuntimeError(
                "opencode exited 0 but did not invoke plugin load probe "
                f"(stderr tail: {proc.stderr[-500:]!r})"
            )
        lines = [line for line in probe_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("opencode created an empty probe.log")
        raw_entry = json.loads(lines[-1])
        if raw_entry.get("event") != "server_hook_created" or raw_entry.get("plugin") != "membud-verify-guard":
            raise RuntimeError(f"unexpected probe payload: {raw_entry!r}")

        redacted_probe = _redact_probe_entry(raw_entry, project)
        probe_bytes = (json.dumps(redacted_probe, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        evidence = {
            "schema_version": 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "opencode_version": version,
            "command": [
                "opencode",
                "debug",
                "agents",
                "--standalone",
                "--print-logs",
                "--log-level",
                "info",
            ],
            "plugin_path": "integrations/opencode/plugin/membud-verify-guard.js",
            "plugin_sha256": _sha256_bytes(PLUGIN_FILE.read_bytes()),
            "probe_log_path": "integrations/opencode/smoke/probe.log",
            "probe_log_sha256": _sha256_bytes(probe_bytes),
            "probe_event": redacted_probe,
            "proves": "module load and plugin entry-point invocation only (no model call, no tool call)",
        }
        return redacted_probe, evidence


def verify_smoke_artifacts(
    probe_path: Path = PROBE_ARTIFACT,
    evidence_path: Path = EVIDENCE_ARTIFACT,
    plugin_file: Path = PLUGIN_FILE,
) -> dict[str, Any]:
    """Validate that retained smoke artifacts exist, parse, and agree on digests."""
    if not probe_path.is_file():
        raise AssertionError(f"missing smoke probe artifact: {probe_path}")
    if not evidence_path.is_file():
        raise AssertionError(f"missing smoke evidence artifact: {evidence_path}")

    probe_bytes = probe_path.read_bytes()
    probe_lines = [line for line in probe_bytes.decode("utf-8").splitlines() if line.strip()]
    if len(probe_lines) != 1:
        raise AssertionError(f"expected 1 JSON line in {probe_path}, got {len(probe_lines)}")
    probe = json.loads(probe_lines[0])
    if probe.get("event") != "server_hook_created":
        raise AssertionError(f"unexpected probe event: {probe.get('event')!r}")
    if probe.get("plugin") != "membud-verify-guard":
        raise AssertionError(f"unexpected probe plugin: {probe.get('plugin')!r}")
    if probe.get("toolNames") != ["memory_write"]:
        raise AssertionError(f"unexpected probe toolNames: {probe.get('toolNames')!r}")

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if evidence.get("plugin_sha256") != _sha256_bytes(plugin_file.read_bytes()):
        raise AssertionError("smoke-evidence.json plugin_sha256 does not match membud-verify-guard.js")
    if evidence.get("probe_log_sha256") != _sha256_bytes(probe_bytes):
        raise AssertionError("smoke-evidence.json probe_log_sha256 does not match probe.log")
    if evidence.get("probe_event") != probe:
        raise AssertionError("smoke-evidence.json probe_event does not match probe.log")
    if not evidence.get("opencode_version"):
        raise AssertionError("smoke-evidence.json missing opencode_version")
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--opencode-bin",
        default="/usr/bin/opencode",
        help="Path or command name for the opencode binary",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify retained smoke artifacts without invoking opencode",
    )
    args = parser.parse_args(argv)

    if args.check:
        evidence = verify_smoke_artifacts()
        print(
            f"OK: retained opencode load smoke verified "
            f"(version={evidence['opencode_version']}, sha256={evidence['probe_log_sha256'][:12]})"
        )
        return 0

    probe, evidence = run_module_load_smoke(opencode_bin=args.opencode_bin)
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    PROBE_ARTIFACT.write_text(
        json.dumps(probe, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    EVIDENCE_ARTIFACT.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    verify_smoke_artifacts()
    print(
        f"wrote {PROBE_ARTIFACT.relative_to(REPO_ROOT)} and "
        f"{EVIDENCE_ARTIFACT.relative_to(REPO_ROOT)} "
        f"(opencode={evidence['opencode_version']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
