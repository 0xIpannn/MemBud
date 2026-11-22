"""Keep docs/quickstart.md honest: its examples, snippets and links must run and resolve."""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import re
import shutil
import threading
import tomllib
from http.client import HTTPConnection
from pathlib import Path

import pytest

from membud import service

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
QUICKSTART = DOCS / "quickstart.md"
EXAMPLES = DOCS / "examples"


def _fenced_blocks(text: str, lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", text, flags=re.S)


@pytest.fixture()
def sidecar_port():
    server = service.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post_example(port: int, name: str) -> dict:
    body = (EXAMPLES / f"{name}.json").read_bytes()
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/verify", body=body, headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    payload = json.loads(response.read())
    conn.close()
    return payload


def test_example_accept_is_accepted_by_the_sidecar(sidecar_port):
    payload = _post_example(sidecar_port, "accept")
    assert payload["decision"] == "ACCEPT"
    assert payload["transition_valid"] is True


def test_example_hold_is_held_for_the_documented_reason(sidecar_port):
    payload = _post_example(sidecar_port, "hold")
    assert payload["decision"] == "HOLD"
    failed = [(c["name"], c["reason_code"]) for c in payload["checks"] if not c["passed"]]
    assert ("faithfulness", "UNSUPPORTED_CLAIM") in failed


def test_quickstart_python_snippet_runs_and_prints_the_documented_output(tmp_path, monkeypatch):
    text = QUICKSTART.read_text(encoding="utf-8")
    snippets = _fenced_blocks(text, "python")
    expected_blocks = _fenced_blocks(text, "text")
    assert len(snippets) == 1 and len(expected_blocks) == 1
    shutil.copytree(EXAMPLES, tmp_path / "docs" / "examples")
    monkeypatch.chdir(tmp_path)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(snippets[0], "quickstart-snippet", "exec"), {"__name__": "__quickstart__"})
    assert out.getvalue().strip() == expected_blocks[0].strip()
    assert (tmp_path / "membud-output/quickstart/accept-store.json").exists()
    # HOLD must never create or rewrite a store.
    assert not (tmp_path / "membud-output/quickstart/hold-store.json").exists()


@pytest.mark.parametrize(
    "doc", ["quickstart.md", "reference.md", "running-experiments.md", "research-status.md"]
)
def test_relative_markdown_links_resolve(doc):
    path = DOCS / doc
    for target in re.findall(r"\]\(([^)\s]+)\)", path.read_text(encoding="utf-8")):
        if re.match(r"[a-z]+:", target) or target.startswith("#"):
            continue
        resolved = (path.parent / target.split("#", 1)[0]).resolve()
        assert resolved.exists(), f"{doc}: broken link {target}"


def test_readme_links_resolve():
    path = REPO_ROOT / "README.md"
    for target in re.findall(r"\]\(([^)\s]+)\)", path.read_text(encoding="utf-8")):
        if re.match(r"[a-z]+:", target) or target.startswith("#"):
            continue
        assert (REPO_ROOT / target.split("#", 1)[0]).exists(), f"README: broken link {target}"


def test_console_scripts_point_at_real_callables():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert {"membud-verify", "membud-demo", "membud-conversation", "membud-experiment"} <= set(
        project["scripts"]
    )
    for target in project["scripts"].values():
        module, _, attr = target.partition(":")
        assert callable(getattr(importlib.import_module(module), attr))


def test_license_is_present_and_declared():
    assert (REPO_ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "MIT"
