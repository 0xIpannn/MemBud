"""Localhost-only MemBud ``/verify`` sidecar (standard library only).

Why a sidecar
-------------
The deterministic MemBud verifier is a pure function over a canonical
schema-v2 transition.  Product surfaces that are not Python (an editor agent,
a browser, a shell hook) can therefore ask a small local process for a
decision instead of re-implementing the checks.  This module is deliberately a
*spike*:

* it never persists anything (no store, no audit log, no temp files);
* it binds to the loopback interface only and rejects non-loopback peers;
* it has an explicit body-size cap, socket timeout, JSON depth/shape limits,
  and a strict request envelope;
* it fails closed: every malformed, oversized, unsupported or internal-error
  request produces ``decision: "HOLD"`` and never ``ACCEPT``.

Endpoints
---------
``GET /healthz``
    ``{"status": "ok", "service": "membud-verify", "schema_version": 2, ...}``

``POST /verify``
    Request body: a canonical schema-v2 transition dictionary (the same shape
    :meth:`membud.schemas.Transition.from_dict` accepts) plus an optional
    ``representation`` field (``"graph"``, the default, or ``"text"``)::

        {
          "schema_version": 2,
          "representation": "graph",
          "case_id": "...",
          "before": [{"id": "...", "claims": ["..."]}],
          "candidate": [{"id": "...", "claims": ["..."]}],
          "evidence": [{"id": "...", "supports": ["..."]}],
          "required_claims": ["..."],
          "operations": [{"type": "WRITE", "record_id": "..."}]
        }

    Success response (HTTP 200)::

        {
          "decision": "ACCEPT" | "HOLD",
          "transition_valid": true,
          "case_id": "...",
          "schema_version": 2,
          "representation": "graph",
          "verifier": "rules",
          "checks": [{name, passed, status, reason, reason_code, affected_ids}],
          "elapsed_ms": 0.1,
          "total_ms": 0.3,
          "error": null
        }

    Any rejection returns a non-200 status with the *same* envelope shape, a
    ``decision`` of ``"HOLD"``, ``transition_valid`` false, a synthetic
    ``input`` check carrying the error code as ``reason_code``, and
    ``error: {code, message, http_status}``.

CLI
---
``python -m membud.service --host 127.0.0.1 --port 0``

The chosen URL is printed to stdout on one line before serving, e.g.::

    membud-verify listening on http://127.0.0.1:43211

Limits and trust model
----------------------
The peer is assumed to be a process on the same machine; loopback binding is
the security boundary, not authentication.  Requests are untrusted data: the
envelope is validated before any dataclass is constructed, JSON depth and
collection sizes are bounded, and the verifier itself is a pure function.
Nothing is written to disk by this module.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import sys
import time
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .schemas import SCHEMA_VERSION, Transition
from .verification import verify

SERVICE_NAME = "membud-verify"
SERVICE_VERSION = "0.1.0"
VERIFIER_NAME = "rules"
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 0

DEFAULT_MAX_BODY_BYTES = 256 * 1024
DEFAULT_SOCKET_TIMEOUT = 10.0

MAX_JSON_DEPTH = 16
MAX_RECORDS = 5_000
MAX_EVIDENCE = 5_000
MAX_OPERATIONS = 5_000
MAX_CLAIMS = 20_000
MAX_STRING_LENGTH = 10_000

ALLOWED_REPRESENTATIONS = ("graph", "text")
ALLOWED_TRANSITION_KEYS = frozenset(
    {
        "schema_version",
        "case_id",
        "before",
        "candidate",
        "evidence",
        "required_claims",
        "operations",
        # Legacy lowercase alias accepted by Transition.from_dict; retained so
        # archived v1 transitions are still loadable by the service, although
        # the service itself requires schema_version 2.
        "supersedes",
    }
)

_STATIC_ERROR_CODES = frozenset(
    {
        "INVALID_JSON",
        "JSON_TOO_DEEP",
        "INVALID_REQUEST",
        "UNSUPPORTED_SCHEMA_VERSION",
        "INVALID_TRANSITION",
        "UNKNOWN_REPRESENTATION",
        "INPUT_TOO_LARGE",
        "PAYLOAD_TOO_LARGE",
        "UNSUPPORTED_MEDIA_TYPE",
        "LENGTH_REQUIRED",
        "UNSUPPORTED_TRANSFER_ENCODING",
        "REQUEST_TIMEOUT",
        "NOT_FOUND",
        "METHOD_NOT_ALLOWED",
        "NON_LOOPBACK_PEER",
        "INTERNAL_ERROR",
    }
)


class RequestError(Exception):
    """A fail-closed request rejection with an HTTP status and stable code."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        representation: str | None = None,
        transition_valid: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code if code in _STATIC_ERROR_CODES else "INTERNAL_ERROR"
        self.message = message
        self.representation = representation
        self.transition_valid = transition_valid
        self.extra = extra or {}


@dataclass(frozen=True)
class ServiceConfig:
    """Bounded runtime settings for one sidecar instance."""

    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    socket_timeout: float = DEFAULT_SOCKET_TIMEOUT
    verbose: bool = False


# ---------------------------------------------------------------------------
# Request validation helpers (all raise RequestError)
# ---------------------------------------------------------------------------


def _ok_message(exc: BaseException) -> str:
    """Return a single-line, bounded description of an exception."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:300]


def parse_json_body(raw: bytes) -> Any:
    """Decode UTF-8 JSON, rejecting duplicate keys, NaN/Infinity and deep input."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RequestError(400, "INVALID_JSON", "request body is not valid UTF-8 JSON") from exc
    if not text.strip():
        raise RequestError(400, "INVALID_JSON", "request body is empty")

    def _reject_constant(value: str) -> Any:
        raise RequestError(400, "INVALID_JSON", f"non-finite JSON number is not allowed: {value}")

    def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RequestError(400, "INVALID_JSON", f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_object_pairs,
        )
    except RequestError:
        raise
    except RecursionError as exc:
        raise RequestError(400, "JSON_TOO_DEEP", "request JSON nesting is too deep") from exc
    except json.JSONDecodeError as exc:
        raise RequestError(
            400,
            "INVALID_JSON",
            f"malformed JSON at line {exc.lineno} column {exc.colno}: {exc.msg}",
        ) from exc


def check_json_depth(value: Any, *, max_depth: int = MAX_JSON_DEPTH) -> None:
    """Reject JSON nesting deeper than ``max_depth`` before building objects."""

    def walk(node: Any, depth: int) -> None:
        if depth > max_depth:
            raise RequestError(400, "JSON_TOO_DEEP", f"request JSON exceeds max depth {max_depth}")
        if isinstance(node, dict):
            for key, item in node.items():
                if not isinstance(key, str):
                    raise RequestError(400, "INVALID_REQUEST", "JSON object keys must be strings")
                walk(item, depth + 1)
        elif isinstance(node, list):
            for item in node:
                walk(item, depth + 1)

    walk(value, 0)


def validate_request_envelope(payload: Any) -> tuple[dict[str, Any], str]:
    """Split the optional representation from the canonical transition payload."""
    if not isinstance(payload, dict):
        raise RequestError(400, "INVALID_REQUEST", "request body must be a JSON object")
    representation = payload.get("representation", "graph")
    if not isinstance(representation, str) or representation not in ALLOWED_REPRESENTATIONS:
        raise RequestError(
            400,
            "UNKNOWN_REPRESENTATION",
            "representation must be one of "
            + ", ".join(repr(item) for item in ALLOWED_REPRESENTATIONS)
            + f"; got {representation!r}",
        )
    transition_payload = {key: value for key, value in payload.items() if key != "representation"}
    unexpected = sorted(set(transition_payload) - ALLOWED_TRANSITION_KEYS)
    if unexpected:
        raise RequestError(
            400,
            "INVALID_REQUEST",
            "unexpected transition fields: " + ", ".join(unexpected),
            representation=representation,
        )
    return transition_payload, representation


def _count_claims(items: Any, keys: tuple[str, ...] = ("claims",)) -> int:
    """Count claim strings defensively; invalid item types are left to schema validation."""
    if not isinstance(items, list):
        return 0
    total = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            claims = item.get(key)
            if isinstance(claims, list):
                total += len(claims)
    return total


def validate_transition_shape(payload: dict[str, Any]) -> None:
    """Enforce the canonical schema version and deliberate collection limits."""
    schema_version = payload.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != SCHEMA_VERSION
    ):
        raise RequestError(
            400,
            "UNSUPPORTED_SCHEMA_VERSION",
            f"service accepts canonical schema_version {SCHEMA_VERSION}; got {schema_version!r}",
        )

    limits = (
        ("before", MAX_RECORDS),
        ("candidate", MAX_RECORDS),
        ("evidence", MAX_EVIDENCE),
        ("operations", MAX_OPERATIONS),
        ("required_claims", MAX_CLAIMS),
    )
    normalised: dict[str, list[Any]] = {}
    for field, limit in limits:
        value = payload.get(field)
        if value is None and field in {"operations", "required_claims"}:
            value = []
        if not isinstance(value, list):
            raise RequestError(400, "INVALID_REQUEST", f"{field} must be a JSON array")
        if len(value) > limit:
            raise RequestError(
                400,
                "INPUT_TOO_LARGE",
                f"{field} has {len(value)} entries; the per-request limit is {limit}",
            )
        normalised[field] = value

    total_claims = (
        _count_claims(normalised["before"])
        + _count_claims(normalised["candidate"])
        + _count_claims(normalised["evidence"], keys=("supports", "contradicts"))
        + len(normalised["required_claims"])
    )
    if total_claims > MAX_CLAIMS:
        raise RequestError(
            400,
            "INPUT_TOO_LARGE",
            f"request declares {total_claims} claims; the per-request limit is {MAX_CLAIMS}",
        )


def build_transition(payload: dict[str, Any], representation: str) -> Transition:
    """Construct and validate a canonical transition, mapping errors to INVALID_TRANSITION."""
    try:
        return Transition.from_dict(payload)
    except Exception as exc:  # KeyError/TypeError/ValueError from untrusted input
        raise RequestError(
            400,
            "INVALID_TRANSITION",
            f"transition is invalid: {_ok_message(exc)}",
            representation=representation,
        ) from exc


def run_verification(transition: Transition, representation: str) -> Any:
    """Run the deterministic verifier; any internal failure fails closed."""
    try:
        return verify(transition, representation)
    except Exception as exc:
        raise RequestError(
            500,
            "INTERNAL_ERROR",
            f"verifier failed: {type(exc).__name__}: {_ok_message(exc)}",
            representation=representation,
            transition_valid=True,
        ) from exc


# ---------------------------------------------------------------------------
# Response construction
# ---------------------------------------------------------------------------


def _check_dict(check: Any) -> dict[str, Any]:
    return asdict(check)


def success_payload(transition: Transition, result: Any, representation: str, elapsed_ms: float) -> dict[str, Any]:
    return {
        "decision": result.decision,
        "transition_valid": True,
        "case_id": transition.case_id,
        "schema_version": transition.schema_version,
        "representation": representation,
        "verifier": result.verifier,
        "checks": [_check_dict(check) for check in result.checks],
        "elapsed_ms": elapsed_ms,
        "error": None,
    }


def error_payload(
    error: RequestError,
    *,
    elapsed_ms: float,
    total_ms: float,
) -> dict[str, Any]:
    return {
        "decision": "HOLD",
        "transition_valid": error.transition_valid,
        "case_id": None,
        "schema_version": None,
        "representation": error.representation,
        "verifier": VERIFIER_NAME,
        "checks": [
            {
                "name": "input",
                "passed": False,
                "reason": error.message,
                "affected_ids": [],
                "status": "FAIL",
                "reason_code": error.code,
            }
        ],
        "elapsed_ms": elapsed_ms,
        "total_ms": total_ms,
        "error": {
            "code": error.code,
            "message": error.message,
            "http_status": error.status,
        },
        **error.extra,
    }


def health_payload(config: ServiceConfig) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "service_version": SERVICE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "verifier": VERIFIER_NAME,
        "representations": list(ALLOWED_REPRESENTATIONS),
        "limits": {
            "max_body_bytes": config.max_body_bytes,
            "socket_timeout_seconds": config.socket_timeout,
            "max_json_depth": MAX_JSON_DEPTH,
            "max_records": MAX_RECORDS,
            "max_claims": MAX_CLAIMS,
        },
    }


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class _VerifyHandler(BaseHTTPRequestHandler):
    """One request per connection; every failure path returns structured JSON."""

    server_version = f"{SERVICE_NAME}/{SERVICE_VERSION}"
    sys_version = ""
    protocol_version = "HTTP/1.0"
    timeout: float | None = None  # replaced per handler class in make_server

    config = ServiceConfig()
    _request_started: float = 0.0
    _verify_started: float | None = None
    _response_sent: bool = False

    # -- plumbing ---------------------------------------------------------
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        if self.config.verbose:
            super().log_message(format, *args)

    def log_error(self, format: str, *args: Any) -> None:
        if self.config.verbose:
            super().log_error(format, *args)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._guarded(self._handle_get)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        self._guarded(self._handle_post)

    def _guarded(self, handler: Any) -> None:
        self._request_started = time.perf_counter()
        self._verify_started = None
        self._response_sent = False
        try:
            if not _is_loopback(str(self.client_address[0])):
                raise RequestError(403, "NON_LOOPBACK_PEER", "only loopback peers are accepted")
            handler()
        except RequestError as exc:
            self._send_error(exc)
        except Exception as exc:  # never let a handler exception escape
            self._send_error(
                RequestError(
                    500,
                    "INTERNAL_ERROR",
                    f"unhandled service error: {type(exc).__name__}: {_ok_message(exc)}",
                )
            )
        except BaseException:
            # Last-resort fail-closed path (e.g. an interrupted handler thread);
            # never re-raise out of the HTTP handler.
            try:
                self._send_error(
                    RequestError(500, "INTERNAL_ERROR", "unhandled service error")
                )
            except BaseException:
                self.close_connection = True

    def _path(self) -> str:
        return urlsplit(self.path).path

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise RequestError(
                411,
                "UNSUPPORTED_TRANSFER_ENCODING",
                "Transfer-Encoding is not supported; send Content-Length",
            )
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise RequestError(411, "LENGTH_REQUIRED", "Content-Length is required")
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise RequestError(400, "INVALID_REQUEST", "Content-Length must be an integer") from exc
        if content_length < 0:
            raise RequestError(400, "INVALID_REQUEST", "Content-Length must be non-negative")
        cap = self.config.max_body_bytes
        if content_length > cap:
            raise RequestError(
                413,
                "PAYLOAD_TOO_LARGE",
                f"request body declares {content_length} bytes; the cap is {cap} bytes",
            )
        if content_length == 0:
            raise RequestError(400, "INVALID_JSON", "request body is empty")
        try:
            body = self.rfile.read(content_length)
        except TimeoutError as exc:
            raise RequestError(408, "REQUEST_TIMEOUT", "timed out reading the request body") from exc
        if len(body) != content_length:
            raise RequestError(400, "INVALID_REQUEST", "request body was truncated")
        return body

    # -- endpoints --------------------------------------------------------
    def _handle_get(self) -> None:
        path = self._path()
        if path == "/healthz":
            self._send_json(200, health_payload(self.config))
            return
        if path == "/verify":
            raise RequestError(405, "METHOD_NOT_ALLOWED", "use POST /verify")
        raise RequestError(404, "NOT_FOUND", f"unknown path: {path}")

    def _handle_post(self) -> None:
        path = self._path()
        if path == "/healthz":
            raise RequestError(405, "METHOD_NOT_ALLOWED", "use GET /healthz")
        if path != "/verify":
            raise RequestError(404, "NOT_FOUND", f"unknown path: {path}")

        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise RequestError(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                "Content-Type must be application/json",
            )

        body = self._read_body()
        payload = parse_json_body(body)
        check_json_depth(payload)
        transition_payload, representation = validate_request_envelope(payload)
        try:
            validate_transition_shape(transition_payload)
        except RequestError as exc:
            exc.representation = exc.representation or representation
            raise

        transition = build_transition(transition_payload, representation)
        self._verify_started = time.perf_counter()
        result = run_verification(transition, representation)
        elapsed_ms = round((time.perf_counter() - self._verify_started) * 1000, 3)
        total_ms = round((time.perf_counter() - self._request_started) * 1000, 3)
        response = success_payload(transition, result, representation, elapsed_ms)
        response["total_ms"] = total_ms
        self._send_json(200, response)

    # -- responses --------------------------------------------------------
    def _send_error(self, error: RequestError) -> None:
        if self._response_sent:
            self.close_connection = True
            return
        total_ms = round((time.perf_counter() - self._request_started) * 1000, 3)
        if self._verify_started is not None:
            elapsed_ms = round((time.perf_counter() - self._verify_started) * 1000, 3)
        else:
            elapsed_ms = total_ms
        self._send_json(error.status, error_payload(error, elapsed_ms=elapsed_ms, total_ms=total_ms))

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        try:
            data = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            data = json.dumps(
                {
                    "decision": "HOLD",
                    "error": {"code": "INTERNAL_ERROR", "message": "response serialization failed"},
                },
                sort_keys=True,
            ).encode("utf-8")
            status = 500
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
            self._response_sent = True
        except (BrokenPipeError, ConnectionResetError, OSError):
            # The client went away; there is nobody left to fail closed to.
            pass
        finally:
            self.close_connection = True


class VerifyHTTPServer(ThreadingHTTPServer):
    """Threaded sidecar server; threads are daemons so shutdown is immediate."""

    daemon_threads = True
    allow_reuse_address = True
    block_on_close = False

    def __init__(self, server_address: tuple[str, int], handler: type[BaseHTTPRequestHandler], config: ServiceConfig) -> None:
        self.config = config
        super().__init__(server_address, handler)


def _make_handler(config: ServiceConfig) -> type[_VerifyHandler]:
    class Handler(_VerifyHandler):
        pass

    Handler.config = config
    Handler.timeout = config.socket_timeout
    Handler.__name__ = "_ConfiguredVerifyHandler"
    return Handler


def make_server(
    host: str = LOOPBACK_HOST,
    port: int = DEFAULT_PORT,
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    socket_timeout: float = DEFAULT_SOCKET_TIMEOUT,
    verbose: bool = False,
) -> VerifyHTTPServer:
    """Create (but do not start) a loopback-only verify server.

    Raises ``ValueError`` for a non-loopback bind address or invalid limits and
    ``OSError`` when the port cannot be bound.  Callers run ``serve_forever``
    or use the server in tests; ``server.server_address`` reports the chosen
    ephemeral port.
    """
    if host != LOOPBACK_HOST:
        raise ValueError(f"the verify service may only bind to {LOOPBACK_HOST}, not {host!r}")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError(f"port must be an integer in [0, 65535], not {port!r}")
    if not isinstance(max_body_bytes, int) or isinstance(max_body_bytes, bool) or max_body_bytes <= 0:
        raise ValueError("max_body_bytes must be a positive integer")
    if not isinstance(socket_timeout, (int, float)) or isinstance(socket_timeout, bool) or socket_timeout <= 0:
        raise ValueError("socket_timeout must be a positive number")
    config = ServiceConfig(
        max_body_bytes=max_body_bytes,
        socket_timeout=float(socket_timeout),
        verbose=verbose,
    )
    return VerifyHTTPServer((host, port), _make_handler(config), config)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m membud.service",
        description=(
            "Localhost-only MemBud deterministic /verify sidecar. "
            "Runs the same graph/text verifier as the library and persists nothing."
        ),
    )
    parser.add_argument(
        "--host",
        default=LOOPBACK_HOST,
        help=f"bind address; only {LOOPBACK_HOST} (loopback) is accepted (default: {LOOPBACK_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="TCP port; 0 picks an ephemeral port and prints it (default: 0)",
    )
    parser.add_argument(
        "--max-body-bytes",
        type=int,
        default=DEFAULT_MAX_BODY_BYTES,
        help=f"request body cap in bytes (default: {DEFAULT_MAX_BODY_BYTES})",
    )
    parser.add_argument(
        "--socket-timeout",
        type=float,
        default=DEFAULT_SOCKET_TIMEOUT,
        help=f"per-connection socket timeout in seconds (default: {DEFAULT_SOCKET_TIMEOUT})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="log HTTP requests to stderr",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        server = make_server(
            args.host,
            args.port,
            max_body_bytes=args.max_body_bytes,
            socket_timeout=args.socket_timeout,
            verbose=args.verbose,
        )
    except ValueError as exc:
        print(f"{SERVICE_NAME}: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"{SERVICE_NAME}: cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1
    host, port = server.server_address[0], server.server_address[1]
    # Single machine-readable stdout line so scripts can parse the chosen port.
    print(f"{SERVICE_NAME} listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
