"""Tests for the localhost MemBud ``/verify`` sidecar.

Every test talks to a real in-process ``ThreadingHTTPServer`` bound to
127.0.0.1 on an ephemeral port.  No network egress, no persistence: the
service is expected to be fail-closed and write nothing.
"""
from __future__ import annotations

import json
import os
import re
import select
import socket
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest

from membud import service

REPO_ROOT = Path(__file__).resolve().parents[1]
JSON_HEADERS = {"Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Fixtures: real localhost server on an ephemeral port
# ---------------------------------------------------------------------------


def _start(server: service.VerifyHTTPServer) -> service.VerifyHTTPServer:
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
        name="membud-verify-test",
    )
    thread.start()
    server._test_thread = thread  # type: ignore[attr-defined]
    return server


def _stop(server: service.VerifyHTTPServer) -> None:
    server.shutdown()
    server.server_close()
    thread = getattr(server, "_test_thread", None)
    if thread is not None:
        thread.join(timeout=5)


@pytest.fixture()
def verify_server():
    server = _start(service.make_server("127.0.0.1", 0))
    try:
        yield server
    finally:
        _stop(server)


@pytest.fixture()
def port(verify_server) -> int:
    return verify_server.server_address[1]


@pytest.fixture()
def short_timeout_server():
    server = _start(service.make_server("127.0.0.1", 0, socket_timeout=0.2))
    try:
        yield server
    finally:
        _stop(server)


# ---------------------------------------------------------------------------
# Canonical fixtures
# ---------------------------------------------------------------------------


def accept_transition() -> dict:
    return {
        "schema_version": 2,
        "case_id": "service-accept",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "evidence": [
            {
                "id": "e1",
                "source": "demo:employment",
                "text": "Ia bekerja di Tokopedia.",
                "supports": ["bekerja di Tokopedia"],
            }
        ],
        "required_claims": ["tinggal di Bandung"],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    }


def unsupported_transition() -> dict:
    return {
        "schema_version": 2,
        "case_id": "service-unsupported",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["memiliki kapal pesiar"]},
        ],
        "evidence": [],
        "required_claims": [],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    }


def contradiction_transition() -> dict:
    return {
        "schema_version": 2,
        "case_id": "service-contradiction",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "evidence": [
            {
                "id": "e1",
                "source": "demo:conflict",
                "text": "Ia tidak lagi bekerja di Tokopedia.",
                "supports": ["bekerja di Tokopedia"],
                "contradicts": ["bekerja di Tokopedia"],
            }
        ],
        "required_claims": [],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    }


def omission_transition() -> dict:
    return {
        "schema_version": 2,
        "case_id": "service-omission",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "evidence": [],
        "required_claims": ["tinggal di Bandung", "bekerja di Tokopedia"],
        "operations": [{"type": "NOOP"}],
    }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def post_json(port: int, payload: dict, *, headers: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    connection = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("POST", "/verify", body=body, headers={**JSON_HEADERS, **(headers or {})})
        response = connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw.decode("utf-8"))
    finally:
        connection.close()


def post_to(port: int, path: str, body: bytes, *, headers: dict | None = None) -> tuple[int, dict]:
    connection = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("POST", path, body=body, headers={**JSON_HEADERS, **(headers or {})})
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def post_raw(
    port: int,
    body: bytes,
    *,
    headers: dict | None = None,
) -> tuple[int, dict]:
    connection = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("POST", "/verify", body=body, headers={**JSON_HEADERS, **(headers or {})})
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def raw_exchange(port: int, request_bytes: bytes, *, read_timeout: float = 10.0) -> tuple[int, dict]:
    """Send raw bytes and parse the single JSON response; the server closes."""
    with socket.create_connection(("127.0.0.1", port), timeout=read_timeout) as sock:
        sock.sendall(request_bytes)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    data = b"".join(chunks)
    head, _, body = data.partition(b"\r\n\r\n")
    status = int(head.split(b"\r\n")[0].split()[1])
    return status, (json.loads(body.decode("utf-8")) if body else {})


def get(port: int, path: str) -> tuple[int, dict]:
    connection = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def check_by_name(payload: dict, name: str) -> dict:
    return next(check for check in payload["checks"] if check["name"] == name)


def assert_fail_closed(status: int, payload: dict, expected_code: str) -> None:
    assert status >= 400, (status, payload)
    assert payload["decision"] == "HOLD", payload
    assert payload["transition_valid"] is False, payload
    assert payload["error"]["code"] == expected_code, payload
    assert payload["error"]["http_status"] == status
    assert payload["checks"][0]["reason_code"] == expected_code
    assert payload["checks"][0]["status"] == "FAIL"


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


def test_healthz_reports_service_identity(port):
    status, payload = get(port, "/healthz")
    assert status == 200
    assert payload["status"] == "ok"
    assert payload["service"] == "membud-verify"
    assert payload["schema_version"] == 2
    assert payload["verifier"] == "rules"
    assert payload["representations"] == ["graph", "text"]
    assert payload["limits"]["max_body_bytes"] > 0


# ---------------------------------------------------------------------------
# ACCEPT / HOLD decisions
# ---------------------------------------------------------------------------


def test_verify_accept_graph(port):
    status, payload = post_json(port, accept_transition())
    assert status == 200
    assert payload["decision"] == "ACCEPT"
    assert payload["transition_valid"] is True
    assert payload["case_id"] == "service-accept"
    assert payload["schema_version"] == 2
    assert payload["representation"] == "graph"
    assert payload["verifier"] == "rules"
    assert payload["error"] is None
    assert payload["elapsed_ms"] >= 0
    assert payload["total_ms"] >= 0
    assert [check["name"] for check in payload["checks"]] == [
        "coverage",
        "preservation",
        "faithfulness",
    ]
    assert all(check["status"] == "PASS" for check in payload["checks"])
    assert all(check["reason_code"] for check in payload["checks"])


def test_verify_accept_text_representation(port):
    payload_in = {**accept_transition(), "representation": "text"}
    status, payload = post_json(port, payload_in)
    assert status == 200
    assert payload["decision"] == "ACCEPT"
    assert payload["representation"] == "text"


def test_verify_hold_unsupported_claim(port):
    status, payload = post_json(port, unsupported_transition())
    assert status == 200  # a valid transition that HOLDs is a normal 200 decision
    assert payload["decision"] == "HOLD"
    assert payload["transition_valid"] is True
    faithfulness = check_by_name(payload, "faithfulness")
    assert faithfulness["status"] == "FAIL"
    assert faithfulness["reason_code"] == "UNSUPPORTED_CLAIM"
    assert "memiliki kapal pesiar" in faithfulness["affected_ids"]


def test_verify_hold_contradiction(port):
    status, payload = post_json(port, contradiction_transition())
    assert status == 200
    assert payload["decision"] == "HOLD"
    faithfulness = check_by_name(payload, "faithfulness")
    assert faithfulness["reason_code"] == "CONTRADICTED_CLAIM"
    assert faithfulness["affected_ids"] == ["bekerja di Tokopedia"]


def test_verify_hold_omitted_required_claim(port):
    status, payload = post_json(port, omission_transition())
    assert status == 200
    assert payload["decision"] == "HOLD"
    coverage = check_by_name(payload, "coverage")
    assert coverage["reason_code"] == "REQUIRED_CLAIM_OMITTED"
    assert coverage["affected_ids"] == ["bekerja di Tokopedia"]


# ---------------------------------------------------------------------------
# Fail-closed request handling
# ---------------------------------------------------------------------------


def test_verify_malformed_json_fails_closed(port):
    status, payload = post_raw(port, b'{"schema_version": 2, "case_id": ')
    assert_fail_closed(status, payload, "INVALID_JSON")


def test_verify_non_object_body_fails_closed(port):
    status, payload = post_raw(port, b"[1, 2, 3]")
    assert_fail_closed(status, payload, "INVALID_REQUEST")


def test_verify_duplicate_json_keys_fails_closed(port):
    body = json.dumps(accept_transition())[:-1] + ', "case_id": "other"}'
    status, payload = post_raw(port, body.encode("utf-8"))
    assert_fail_closed(status, payload, "INVALID_JSON")


def test_verify_non_finite_number_fails_closed(port):
    raw = json.dumps(accept_transition()).replace('"required_claims": ["tinggal di Bandung"]', '"required_claims": [NaN]')
    status, payload = post_raw(port, raw.encode("utf-8"))
    assert_fail_closed(status, payload, "INVALID_JSON")


def test_verify_wrong_content_type_fails_closed(port):
    body = json.dumps(accept_transition()).encode("utf-8")
    connection = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("POST", "/verify", body=body, headers={"Content-Type": "text/plain"})
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        status = response.status
    finally:
        connection.close()
    assert_fail_closed(status, payload, "UNSUPPORTED_MEDIA_TYPE")


def test_verify_missing_content_length_fails_closed(port):
    request = (
        b"POST /verify HTTP/1.0\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"\r\n"
    )
    status, payload = raw_exchange(port, request)
    assert_fail_closed(status, payload, "LENGTH_REQUIRED")


def test_verify_transfer_encoding_fails_closed(port):
    request = (
        b"POST /verify HTTP/1.0\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"\r\n"
    )
    status, payload = raw_exchange(port, request)
    assert_fail_closed(status, payload, "UNSUPPORTED_TRANSFER_ENCODING")


def test_verify_truncated_body_fails_closed(port):
    body = json.dumps(accept_transition()).encode("utf-8")
    request = (
        b"POST /verify HTTP/1.0\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body) + 50}\r\n".encode()
        + b"\r\n"
        + body
    )
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(request)
        sock.shutdown(socket.SHUT_WR)  # signal EOF so the short read is observable
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    data = b"".join(chunks)
    head, _, payload_bytes = data.partition(b"\r\n\r\n")
    assert int(head.split(b"\r\n")[0].split()[1]) == 400
    payload = json.loads(payload_bytes.decode("utf-8"))
    assert_fail_closed(400, payload, "INVALID_REQUEST")


def test_verify_oversized_body_fails_closed_without_persisting(port, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cap = service.DEFAULT_MAX_BODY_BYTES
    request = (
        b"POST /verify HTTP/1.0\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {cap + 1}\r\n".encode()
        + b"\r\n"
    )
    status, payload = raw_exchange(port, request)
    assert_fail_closed(status, payload, "PAYLOAD_TOO_LARGE")
    assert list(tmp_path.iterdir()) == []


def test_verify_json_depth_limit_fails_closed(port):
    nested: dict = {"leaf": True}
    for _ in range(service.MAX_JSON_DEPTH + 5):
        nested = {"nested": nested}
    body = {"schema_version": 2, "case_id": "deep", **nested}
    status, payload = post_json(port, body)
    assert_fail_closed(status, payload, "JSON_TOO_DEEP")


def test_verify_input_size_limit_fails_closed(port):
    payload = {
        "schema_version": 2,
        "case_id": "too-many-claims",
        "before": [{"id": "r1", "claims": ["c0"]}],
        "candidate": [{"id": "r1", "claims": [f"c{i}" for i in range(service.MAX_CLAIMS + 1)]}],
        "evidence": [],
        "required_claims": [],
        "operations": [],
    }
    status, body = post_json(port, payload)
    assert_fail_closed(status, body, "INPUT_TOO_LARGE")


def test_verify_unknown_representation_fails_closed(port):
    payload = {**accept_transition(), "representation": "table"}
    status, body = post_json(port, payload)
    assert_fail_closed(status, body, "UNKNOWN_REPRESENTATION")


def test_verify_non_string_representation_fails_closed(port):
    payload = {**accept_transition(), "representation": None}
    status, body = post_json(port, payload)
    assert_fail_closed(status, body, "UNKNOWN_REPRESENTATION")


def test_verify_unsupported_schema_version_fails_closed(port):
    payload = {**accept_transition(), "schema_version": 1}
    status, body = post_json(port, payload)
    assert_fail_closed(status, body, "UNSUPPORTED_SCHEMA_VERSION")


def test_verify_missing_schema_version_fails_closed(port):
    payload = accept_transition()
    del payload["schema_version"]
    status, body = post_json(port, payload)
    assert_fail_closed(status, body, "UNSUPPORTED_SCHEMA_VERSION")


def test_verify_unexpected_field_fails_closed(port):
    payload = {**accept_transition(), "extra": "ignored by schema but not by the service"}
    status, body = post_json(port, payload)
    assert_fail_closed(status, body, "INVALID_REQUEST")


def test_verify_invalid_transition_missing_case_id_fails_closed(port):
    payload = accept_transition()
    del payload["case_id"]
    status, body = post_json(port, payload)
    assert_fail_closed(status, body, "INVALID_TRANSITION")


def test_verify_invalid_transition_undeclared_operation_fails_closed(port):
    payload = accept_transition()
    payload["operations"] = []  # candidate adds r2 without a declared WRITE
    status, body = post_json(port, payload)
    assert_fail_closed(status, body, "INVALID_TRANSITION")


def test_verify_invalid_transition_duplicate_record_ids_fails_closed(port):
    payload = accept_transition()
    payload["candidate"] = [{"id": "r2", "claims": ["a"]}, {"id": "r2", "claims": ["b"]}]
    status, body = post_json(port, payload)
    assert_fail_closed(status, body, "INVALID_TRANSITION")


@pytest.mark.parametrize(
    "case",
    [
        {"schema_version": 2, "case_id": "", "before": [], "candidate": [], "evidence": [], "operations": []},
        {"schema_version": 2, "case_id": "x", "before": [{"id": "r1"}], "candidate": [], "evidence": []},
        {"schema_version": 2, "case_id": "x", "before": [], "candidate": "not-a-list", "evidence": []},
        {"schema_version": 2, "case_id": "x", "before": [], "candidate": [], "evidence": {}},
        {"schema_version": 2, "case_id": "x", "before": [], "candidate": [], "evidence": [], "operations": [{"type": "TELEPORT"}]},
    ],
)
def test_verify_various_invalid_transitions_fail_closed(port, case):
    status, body = post_json(port, case)
    assert status == 400
    assert body["decision"] == "HOLD"
    assert body["transition_valid"] is False
    assert body["error"]["code"] in {"INVALID_TRANSITION", "INVALID_REQUEST"}


def test_verify_verifier_exception_fails_closed(port, monkeypatch):
    def boom(transition, representation):
        raise RuntimeError("injected verifier crash")

    monkeypatch.setattr(service, "verify", boom)
    status, body = post_json(port, accept_transition())
    assert status == 500
    assert body["decision"] == "HOLD"
    assert body["transition_valid"] is True  # the transition was valid; the verifier failed
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["checks"][0]["reason_code"] == "INTERNAL_ERROR"
    assert "injected verifier crash" in body["error"]["message"]


def test_verify_socket_timeout_fails_closed(short_timeout_server):
    port = short_timeout_server.server_address[1]
    request = (
        b"POST /verify HTTP/1.0\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 10\r\n"
        b"\r\n"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(request)
        time.sleep(0.6)  # exceed the 0.2s per-connection timeout
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    data = b"".join(chunks)
    head, _, body = data.partition(b"\r\n\r\n")
    assert int(head.split(b"\r\n")[0].split()[1]) == 408
    payload = json.loads(body.decode("utf-8"))
    assert_fail_closed(408, payload, "REQUEST_TIMEOUT")


# ---------------------------------------------------------------------------
# Routing / method handling
# ---------------------------------------------------------------------------


def test_unknown_path_returns_structured_404(port):
    status, payload = get(port, "/nope")
    assert status == 404
    assert payload["decision"] == "HOLD"
    assert payload["error"]["code"] == "NOT_FOUND"


def test_get_verify_is_method_not_allowed(port):
    status, payload = get(port, "/verify")
    assert status == 405
    assert payload["decision"] == "HOLD"
    assert payload["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_post_healthz_is_method_not_allowed(port):
    status, payload = post_to(port, "/healthz", json.dumps({"status": "ok"}).encode("utf-8"))
    assert status == 405
    assert payload["error"]["code"] == "METHOD_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# No persistence
# ---------------------------------------------------------------------------


def test_service_never_writes_store_or_audit_files(port, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert post_json(port, accept_transition())[1]["decision"] == "ACCEPT"
    assert post_json(port, unsupported_transition())[1]["decision"] == "HOLD"
    assert post_json(port, accept_transition())[1]["decision"] == "ACCEPT"
    # The sidecar is a pure verifier: no store, lock, audit, or temp file.
    assert list(tmp_path.iterdir()) == []
    for name in ("run_case", "commit", "run_store"):
        assert not hasattr(service, name)


# ---------------------------------------------------------------------------
# Server factory / CLI
# ---------------------------------------------------------------------------


def test_make_server_rejects_non_loopback_host():
    with pytest.raises(ValueError, match="127.0.0.1"):
        service.make_server("0.0.0.0", 0)
    with pytest.raises(ValueError, match="127.0.0.1"):
        service.make_server("localhost", 0)


def test_make_server_rejects_invalid_limits():
    with pytest.raises(ValueError):
        service.make_server("127.0.0.1", 0, max_body_bytes=0)
    with pytest.raises(ValueError):
        service.make_server("127.0.0.1", 0, socket_timeout=0)
    with pytest.raises(ValueError):
        service.make_server("127.0.0.1", 70000)


def test_cli_prints_chosen_port_and_serves_healthz():
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": "src",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(REPO_ROOT / ".tmp-plugin"),
        }
    )
    (REPO_ROOT / ".tmp-plugin").mkdir(exist_ok=True)
    process = subprocess.Popen(
        [sys.executable, "-m", "membud.service", "--host", "127.0.0.1", "--port", "0"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert process.stdout is not None
        ready, _, _ = select.select([process.stdout], [], [], 15)
        assert ready, "sidecar did not print its listening URL within 15s"
        line = process.stdout.readline()
        match = re.search(r"listening on http://127\.0\.0\.1:(\d+)", line)
        assert match, f"unexpected startup line: {line!r}"
        port = int(match.group(1))
        status, payload = get(port, "/healthz")
        assert status == 200
        assert payload["status"] == "ok"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
            process.wait(timeout=10)
