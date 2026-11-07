#!/usr/bin/env python3
"""Opt-in live opencode denial test for the MemBud guard (real model call).

What it proves
--------------
A *real* opencode session, driven by a real model, calls a ``memory_write`` tool
that is registered by a local plugin. The MemBud guard plugin's
``tool.execute.before`` hook calls the local ``membud.service`` ``/verify``
sidecar and denies unsafe writes before the tool runs. Two scripted sessions
are executed:

1. **HOLD** — the candidate adds an unsupported claim. The sidecar returns
   HOLD, the guard throws, opencode records the tool call as an error, the
   model sees the denial, and the memory-state file is byte-identical
   afterward (the mock tool never executed).
2. **ACCEPT** — the candidate is evidence-backed. The sidecar returns ACCEPT,
   the tool executes exactly once, and the memory state advances to the
   candidate.

The script is a manual integration check, not part of the default test suite:
it makes **paid provider calls** and therefore requires
``--confirm-paid-call`` plus a ``DEEPSEEK_API_KEY`` in the environment. The
observed run consumed ~8.6k input tokens and cost about $0.0017 (opencode's
provider-side estimate). No API key is ever printed or written to disk.

Requirements
------------
* opencode 1.19.5 binary on PATH (or ``--opencode-bin``)
* an installed ``@opencode-ai/plugin`` package to copy into the isolated HOME
  (default ``~/.config/opencode/node_modules``; ``--plugin-package-root``)
* opencode's cached model catalog (default ``~/.cache/opencode/models.json``;
  ``--models-cache``) so no model-fetch network call is needed
* ``DEEPSEEK_API_KEY`` in the environment
* the MemBud Python environment that can import ``membud`` (``PYTHONPATH`` is
  set automatically from this repository's ``src/``)

Usage
-----
    DEEPSEEK_API_KEY=... .venv/bin/python scripts/run_live_opencode_denial.py \
        --confirm-paid-call --workdir .tmp-live-denial

The script prints a JSON evidence summary and writes ``evidence.json`` under
the work directory. On failure it keeps the work directory for inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_PLUGIN = REPO_ROOT / "integrations/opencode/plugin/membud-verify-guard.js"
TOOL_PLUGIN = REPO_ROOT / "integrations/opencode/live/memory-tool-plugin.mjs"
SIDECAR_MODULE = "membud.service"
SIDECAR_HOST = "127.0.0.1"
SIDECAR_PORT = 8787
MODEL = "deepseek/deepseek-flash"
PROVIDER = "deepseek"
INITIAL_STATE = {
    "schema_version": 2,
    "version": 0,
    "records": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
}
HOLD_CANDIDATE = [
    {"id": "r1", "claims": ["tinggal di Bandung"]},
    {"id": "r2", "claims": ["memiliki kapal pesiar"]},
]
ACCEPT_CANDIDATE = [
    {"id": "r1", "claims": ["tinggal di Bandung"]},
    {"id": "r2", "claims": ["bekerja di Tokopedia"]},
]
ACCEPT_EVIDENCE = [
    {
        "id": "e1",
        "source": "live-test",
        "text": "Saya bekerja di Tokopedia",
        "supports": ["bekerja di Tokopedia"],
    }
]

HOLD_PROMPT = (
    "Call the tool memory_write exactly once with these exact arguments: "
    '{"case_id":"live-hold","candidate":[{"id":"r1","claims":["tinggal di Bandung"]},'
    '{"id":"r2","claims":["memiliki kapal pesiar"]}],"evidence":[],'
    '"required_claims":["tinggal di Bandung"],'
    '"operations":[{"type":"WRITE","record_id":"r2"}]}. '
    "memory_write is the only tool you may call. After the tool call completes or "
    "is denied, reply with one line beginning TOOL_OUTCOME= summarizing what "
    "happened. Do not retry and do not call any other tool."
)

ACCEPT_PROMPT = (
    "Call the tool memory_write exactly once with these exact arguments: "
    '{"case_id":"live-accept","candidate":[{"id":"r1","claims":["tinggal di Bandung"]},'
    '{"id":"r2","claims":["bekerja di Tokopedia"]}],'
    '"evidence":[{"id":"e1","source":"live-test","text":"Saya bekerja di Tokopedia",'
    '"supports":["bekerja di Tokopedia"]}],'
    '"required_claims":["tinggal di Bandung"],'
    '"operations":[{"type":"WRITE","record_id":"r2"}]}. '
    "memory_write is the only tool you may call. After the tool call completes or "
    "is denied, reply with one line beginning TOOL_OUTCOME= summarizing what "
    "happened. Do not retry and do not call any other tool."
)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without network or paid calls)
# ---------------------------------------------------------------------------


def parse_events(stdout: str) -> list[dict[str, Any]]:
    """Parse ``opencode run --format json`` output lines, ignoring noise."""
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def tool_events(events: Iterable[dict[str, Any]], tool: str = "memory_write") -> list[dict[str, Any]]:
    """Return ``tool_use`` events for one tool name."""
    found = []
    for event in events:
        if event.get("type") != "tool_use":
            continue
        part = event.get("part") or {}
        if isinstance(part, dict) and part.get("tool") == tool:
            found.append(event)
    return found


def text_events(events: Iterable[dict[str, Any]]) -> list[str]:
    """Return the text parts of assistant messages."""
    texts = []
    for event in events:
        if event.get("type") != "text":
            continue
        part = event.get("part") or {}
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return texts


def usage_summary(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Sum token usage and provider-side cost estimates from step_finish events."""
    input_tokens = output_tokens = reasoning_tokens = 0
    cost = 0.0
    steps = 0
    for event in events:
        if event.get("type") != "step_finish":
            continue
        part = event.get("part") or {}
        if not isinstance(part, dict):
            continue
        steps += 1
        tokens = part.get("tokens") or {}
        if isinstance(tokens, dict):
            for key in ("input", "output", "reasoning"):
                value = tokens.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    if key == "input":
                        input_tokens += value
                    elif key == "output":
                        output_tokens += value
                    else:
                        reasoning_tokens += value
        value = part.get("cost")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            cost += float(value)
    return {
        "steps": steps,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "estimated_cost_usd": round(cost, 8),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capture_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            lines.append(value)
    return lines


def assert_hold_outcome(
    result: dict[str, Any],
    *,
    state_path: Path,
    state_sha_before: str,
    capture_path: Path,
) -> dict[str, Any]:
    """Assert the live HOLD run denied the tool and left the host state untouched."""
    events = parse_events(result["stdout"])
    calls = tool_events(events)
    if len(calls) != 1:
        raise AssertionError(f"expected exactly one memory_write call, got {len(calls)}")
    state = (calls[0].get("part") or {}).get("state") or {}
    if state.get("status") != "error":
        raise AssertionError(f"expected tool status 'error', got {state.get('status')!r}")
    error = str(state.get("error") or "")
    for marker in ("MemBud denied", "HOLD", "UNSUPPORTED_CLAIM"):
        if marker not in error:
            raise AssertionError(f"denial error missing {marker!r}: {error!r}")
    if _sha256(state_path) != state_sha_before:
        raise AssertionError("memory state changed during a HOLD run")
    if _capture_lines(capture_path):
        raise AssertionError("tool executed during a HOLD run (capture file is non-empty)")
    model_text = "\n".join(text_events(events))
    lowered = model_text.lower()
    # The model paraphrases; require it to have reported the denial in its
    # TOOL_OUTCOME line rather than an exact phrase.
    if "tool_outcome=" not in lowered or ("denied" not in lowered and "hold" not in lowered):
        raise AssertionError(f"model did not report the denial: {model_text!r}")
    return {
        "tool_call_status": "error",
        "denial": error,
        "model_reported": model_text.strip()[:400],
        "state_sha256_unchanged": state_sha_before,
        "tool_executed": False,
        "usage": usage_summary(events),
    }


def assert_accept_outcome(
    result: dict[str, Any],
    *,
    state_path: Path,
    candidate: list[dict[str, Any]],
    capture_path: Path,
) -> dict[str, Any]:
    """Assert the live ACCEPT run executed the tool and persisted the candidate."""
    events = parse_events(result["stdout"])
    calls = tool_events(events)
    if len(calls) != 1:
        raise AssertionError(f"expected exactly one memory_write call, got {len(calls)}")
    state = (calls[0].get("part") or {}).get("state") or {}
    if state.get("status") != "completed":
        raise AssertionError(f"expected tool status 'completed', got {state.get('status')!r}")
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    if persisted.get("version") != 1:
        raise AssertionError(f"expected store version 1, got {persisted.get('version')!r}")
    if persisted.get("records") != candidate:
        raise AssertionError("persisted records do not match the accepted candidate")
    captures = _capture_lines(capture_path)
    if len(captures) != 1:
        raise AssertionError(f"expected exactly one executed capture line, got {len(captures)}")
    model_text = "\n".join(text_events(events))
    return {
        "tool_call_status": "completed",
        "tool_output": state.get("output"),
        "model_reported": model_text.strip()[:400],
        "persisted_state_sha256": _sha256(state_path),
        "tool_executed": True,
        "usage": usage_summary(events),
    }


# ---------------------------------------------------------------------------
# Workspace setup
# ---------------------------------------------------------------------------


def build_config(
    *,
    home: Path,
    project: Path,
    guard_plugin: Path,
    tool_plugin: Path,
    sidecar_url: str,
) -> dict[str, Any]:
    """Build the isolated opencode config used by the live test."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": MODEL,
        "autoupdate": False,
        "permission": {"edit": "allow", "bash": "allow"},
        "provider": {
            PROVIDER: {
                "options": {"apiKey": "{env:DEEPSEEK_API_KEY}"},
            }
        },
        "plugin": [
            [
                guard_plugin.resolve().as_uri(),
                {
                    "verifyUrl": f"http://{SIDECAR_HOST}:{SIDECAR_PORT}/verify",
                    "memoryStatePath": str((project / ".membud-memory.json").resolve()),
                    "toolNames": ["memory_write"],
                    "timeoutMs": 3000,
                    "representation": "graph",
                },
            ],
            tool_plugin.resolve().as_uri(),
        ],
    }


def prepare_workspace(
    workdir: Path,
    *,
    plugin_package_root: Path,
    models_cache: Path,
    guard_plugin: Path,
    tool_plugin: Path,
) -> dict[str, Path]:
    """Create the isolated HOME/project/plugin layout and config file."""
    home = workdir / "home"
    config_dir = home / ".config/opencode"
    project = workdir / "project"
    plugins_dir = workdir / "plugins"
    logs = workdir / "logs"
    for path in (config_dir, home / ".cache/opencode", home / ".local/share", project, plugins_dir, logs):
        path.mkdir(parents=True, exist_ok=True)

    source_modules = plugin_package_root / "node_modules"
    if not source_modules.is_dir():
        raise FileNotFoundError(
            f"{source_modules} does not exist; point --plugin-package-root at a "
            "directory containing an installed @opencode-ai/plugin package"
        )
    shutil.copytree(source_modules, config_dir / "node_modules", symlinks=True)
    for name in ("package.json", "package-lock.json"):
        source = plugin_package_root / name
        if source.exists():
            shutil.copy2(source, config_dir / name)
    if models_cache.exists():
        shutil.copy2(models_cache, home / ".cache/opencode/models.json")

    # The tool plugin imports "@opencode-ai/plugin"; a sibling node_modules
    # symlink lets Node resolve it without any package installation.
    (plugins_dir / "node_modules").symlink_to(config_dir / "node_modules", target_is_directory=True)
    shutil.copy2(tool_plugin, plugins_dir / tool_plugin.name)

    (project / ".membud-memory.json").write_text(
        json.dumps(INITIAL_STATE, indent=2) + "\n", encoding="utf-8"
    )
    config = build_config(
        home=home,
        project=project,
        guard_plugin=guard_plugin,
        tool_plugin=plugins_dir / tool_plugin.name,
        sidecar_url=f"http://{SIDECAR_HOST}:{SIDECAR_PORT}",
    )
    (config_dir / "opencode.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "workdir": workdir,
        "home": home,
        "config_dir": config_dir,
        "project": project,
        "plugins_dir": plugins_dir,
        "logs": logs,
        "state": project / ".membud-memory.json",
        "capture": logs / "tool-executed.jsonl",
        "config": config_dir / "opencode.json",
    }


def _base_env(paths: dict[str, Path], workdir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(paths["home"]),
            "XDG_CONFIG_HOME": str(paths["home"] / ".config"),
            "XDG_DATA_HOME": str(paths["home"] / ".local/share"),
            "XDG_CACHE_HOME": str(paths["home"] / ".cache"),
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "NO_COLOR": "1",
            "MEMBUD_LIVE_STATE": str(paths["state"]),
            "MEMBUD_LIVE_CAPTURE": str(paths["capture"]),
            "PYTHONPATH": str(REPO_ROOT / "src"),
            "TMPDIR": str(workdir / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    (workdir / "tmp").mkdir(parents=True, exist_ok=True)
    return env


# ---------------------------------------------------------------------------
# Process orchestration
# ---------------------------------------------------------------------------


def start_sidecar(workdir: Path, env: dict[str, str]) -> subprocess.Popen:
    log_path = workdir / "logs/sidecar.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            SIDECAR_MODULE,
            "--host",
            SIDECAR_HOST,
            "--port",
            str(SIDECAR_PORT),
            "--verbose",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    process._membud_log = log_file  # type: ignore[attr-defined]
    return process


def wait_for_sidecar(timeout: float = 15.0) -> None:
    url = f"http://{SIDECAR_HOST}:{SIDECAR_PORT}/healthz"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("status") == "ok":
                return
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.2)
    raise RuntimeError(f"sidecar did not become healthy at {url}")


def run_session(
    *,
    prompt: str,
    name: str,
    paths: dict[str, Path],
    env: dict[str, str],
    opencode_bin: str,
    timeout: float,
) -> dict[str, Any]:
    stdout_path = paths["logs"] / f"{name}.json"
    stderr_path = paths["logs"] / f"{name}.stderr.log"
    result = subprocess.run(
        [
            opencode_bin,
            "run",
            "--model",
            MODEL,
            "--auto",
            "--format",
            "json",
            prompt,
        ],
        cwd=str(paths["project"]),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    return {
        "name": name,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "stdout_path": str(stdout_path),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the opt-in live opencode denial test against a real model. "
            "Makes paid provider calls; requires --confirm-paid-call."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--confirm-paid-call",
        action="store_true",
        help="required acknowledgement that this runs real paid model calls",
    )
    parser.add_argument("--workdir", default=None, help="work directory (default: a temp dir)")
    parser.add_argument("--opencode-bin", default=shutil.which("opencode") or "opencode")
    parser.add_argument(
        "--plugin-package-root",
        default=str(Path.home() / ".config/opencode"),
        help="directory containing an installed node_modules/@opencode-ai/plugin",
    )
    parser.add_argument(
        "--models-cache",
        default=str(Path.home() / ".cache/opencode/models.json"),
        help="opencode model catalog cache (avoids a models.dev fetch)",
    )
    parser.add_argument("--guard-plugin", default=str(GUARD_PLUGIN))
    parser.add_argument("--tool-plugin", default=str(TOOL_PLUGIN))
    parser.add_argument("--timeout", type=float, default=300.0, help="timeout per session (s)")
    parser.add_argument("--keep", action="store_true", help="keep the work directory even on success")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm_paid_call:
        print(
            "Refusing to run: this script makes real paid model calls. "
            "Re-run with --confirm-paid-call to acknowledge.",
            file=sys.stderr,
        )
        return 2
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        print("DEEPSEEK_API_KEY must be set in the environment", file=sys.stderr)
        return 2
    if not Path(args.opencode_bin).exists() and shutil.which(args.opencode_bin) is None:
        print(f"opencode binary not found: {args.opencode_bin}", file=sys.stderr)
        return 2

    workdir_given = args.workdir is not None
    workdir = Path(args.workdir).expanduser() if workdir_given else Path(tempfile.mkdtemp(prefix="membud-live-"))
    print(f"workdir: {workdir}")
    print(f"model: {MODEL}; this run is expected to use ~9k input tokens (a fraction of a cent).")

    sidecar: subprocess.Popen | None = None
    try:
        paths = prepare_workspace(
            workdir,
            plugin_package_root=Path(args.plugin_package_root).expanduser(),
            models_cache=Path(args.models_cache).expanduser(),
            guard_plugin=Path(args.guard_plugin).expanduser(),
            tool_plugin=Path(args.tool_plugin).expanduser(),
        )
        env = _base_env(paths, workdir)
        sidecar = start_sidecar(workdir, env)
        wait_for_sidecar()

        state_sha_before = _sha256(paths["state"])
        hold_result = run_session(
            prompt=HOLD_PROMPT,
            name="hold",
            paths=paths,
            env=env,
            opencode_bin=args.opencode_bin,
            timeout=args.timeout,
        )
        hold = assert_hold_outcome(
            hold_result,
            state_path=paths["state"],
            state_sha_before=state_sha_before,
            capture_path=paths["capture"],
        )

        accept_result = run_session(
            prompt=ACCEPT_PROMPT,
            name="accept",
            paths=paths,
            env=env,
            opencode_bin=args.opencode_bin,
            timeout=args.timeout,
        )
        accept = assert_accept_outcome(
            accept_result,
            state_path=paths["state"],
            candidate=ACCEPT_CANDIDATE,
            capture_path=paths["capture"],
        )
        evidence = {
            "test": "live-opencode-membud-denial",
            "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": MODEL,
            "opencode_bin": args.opencode_bin,
            "hold": hold,
            "accept": accept,
            "total_usage": {
                "input_tokens": hold["usage"]["input_tokens"] + accept["usage"]["input_tokens"],
                "output_tokens": hold["usage"]["output_tokens"] + accept["usage"]["output_tokens"],
                "estimated_cost_usd": round(
                    hold["usage"]["estimated_cost_usd"] + accept["usage"]["estimated_cost_usd"], 8
                ),
            },
            "state_file_final": json.loads(paths["state"].read_text(encoding="utf-8")),
            "logs": str(paths["logs"]),
        }
        evidence_path = workdir / "evidence.json"
        evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print("\nLIVE DENIAL TEST: PASS")
        print(json.dumps(evidence, indent=2))
        print(f"\nevidence: {evidence_path}")
        if not args.keep and not workdir_given:
            shutil.rmtree(workdir, ignore_errors=True)
        return 0
    except Exception as exc:  # noqa: BLE001 - report and keep the workdir for inspection
        print(f"\nLIVE DENIAL TEST: FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"work directory kept for inspection: {workdir}", file=sys.stderr)
        return 1
    finally:
        if sidecar is not None:
            sidecar.terminate()
            try:
                sidecar.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sidecar.kill()
            log_file = getattr(sidecar, "_membud_log", None)
            if log_file is not None:
                log_file.close()


if __name__ == "__main__":
    raise SystemExit(main())
