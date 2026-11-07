"""OpenAI-compatible chat client for Command Code and compatible providers."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class LLMRequestError(RuntimeError):
    """Provider request failed after a bounded number of attempts."""

    def __init__(self, message: str, *, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True)
class JSONCompletion:
    """Validated JSON response plus provider usage and request metadata."""

    json: dict[str, Any]
    content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    request_id: str | None
    latency_ms: float
    request_attempts: int
    json_retries: int
    cached: bool = False


class OpenAICompatibleChatClient:
    """Small standard-library client for OpenAI Chat Completions APIs.

    Credentials are passed explicitly or read from ``CMD_API_KEY``. The client
    never logs credentials or request content. Transient network/429/5xx
    failures get bounded exponential-backoff retries; other HTTP errors fail
    immediately. Malformed JSON gets a separate bounded repair request.
    """

    DEFAULT_BASE_URL = "https://api.commandcode.ai/provider/v1"
    DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
    DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    DEEPSEEK_MODEL = "deepseek-flash"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        max_attempts: int = 3,
        max_json_retries: int = 1,
        backoff_seconds: float = 0.5,
        temperature: float = 0.0,
        zero_data_retention: bool = False,
        cache_path: str | Path | None = None,
        provider_name: str = "openai-compatible",
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must be non-empty")
        if not model.strip():
            raise ValueError("model must be non-empty")
        if not base_url.startswith("https://"):
            raise ValueError("base_url must use https")
        if timeout <= 0 or max_attempts < 1 or max_json_retries < 0 or backoff_seconds < 0:
            raise ValueError("invalid timeout, retry count, or backoff configuration")
        self._api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.max_json_retries = max_json_retries
        self.backoff_seconds = backoff_seconds
        self.temperature = temperature
        self.zero_data_retention = zero_data_retention
        self.cache_path = Path(cache_path).expanduser() if cache_path is not None else None
        self.provider_name = provider_name
        self._sleep = sleeper

    @property
    def configuration(self) -> dict[str, Any]:
        """Non-secret model/request settings suitable for an audit record."""
        return {
            "provider": self.provider_name,
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "timeout_seconds": self.timeout,
            "max_attempts": self.max_attempts,
            "max_json_retries": self.max_json_retries,
            "backoff_seconds": self.backoff_seconds,
            "response_format": "json_object",
            "zero_data_retention": self.zero_data_retention,
            "persistent_cache": self.cache_path is not None,
        }

    @classmethod
    def from_env(cls) -> "OpenAICompatibleChatClient":
        """Build a client from Command Code environment variables."""
        api_key = os.environ.get("CMD_API_KEY", "")
        if not api_key:
            raise ValueError("CMD_API_KEY must be set")
        return cls(
            api_key=api_key,
            model=os.environ.get("CMD_MODEL", cls.DEFAULT_MODEL),
            base_url=os.environ.get("CMD_BASE_URL", cls.DEFAULT_BASE_URL),
            provider_name="commandcode-openai-compatible",
            zero_data_retention=os.environ.get("CMD_ZDR", "0").lower() in {"1", "true", "yes"},
            cache_path=os.environ.get(
                "CMD_CACHE_PATH", "~/.cache/membud/commandcode-cache.sqlite3"
            ),
        )

    @classmethod
    def from_deepseek_env(cls) -> "OpenAICompatibleChatClient":
        """Build a client for DeepSeek's official OpenAI-compatible API."""
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY must be set in the process environment")
        return cls(
            api_key=api_key,
            model=os.environ.get("DEEPSEEK_MODEL", cls.DEEPSEEK_MODEL),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", cls.DEEPSEEK_BASE_URL),
            cache_path=os.environ.get(
                "DEEPSEEK_CACHE_PATH", "~/.cache/membud/deepseek-cache.sqlite3"
            ),
            provider_name="deepseek-openai-compatible",
        )

    def complete_json(
        self,
        messages: Sequence[Mapping[str, str]],
        validator: Callable[[dict[str, Any]], None] | None = None,
    ) -> JSONCompletion:
        """Request a JSON object, repair malformed responses at most N times."""
        if not messages:
            raise ValueError("messages must not be empty")
        request_messages = [dict(message) for message in messages]
        cache_key = self._cache_key(request_messages)
        cached = self._load_cache(cache_key)
        if cached is not None:
            try:
                if validator is not None:
                    validator(cached["json"])
                return JSONCompletion(
                    json=cached["json"],
                    content=cached["content"],
                    model=cached["model"],
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                    request_id=None,
                    latency_ms=0.0,
                    request_attempts=0,
                    json_retries=0,
                    cached=True,
                )
            except (ValueError, TypeError):
                self._delete_cache(cache_key)
        started = time.perf_counter()
        total_attempts = 0
        token_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        seen_usage = {key: False for key in token_totals}
        for json_retry in range(self.max_json_retries + 1):
            response, attempts = self._request_json_object(request_messages)
            total_attempts += attempts
            for key in token_totals:
                value = response.get(key)
                if value is not None:
                    token_totals[key] += int(value)
                    seen_usage[key] = True
            try:
                parsed = json.loads(response["content"])
                if not isinstance(parsed, dict):
                    raise ValueError("JSON response must be an object")
                if validator is not None:
                    validator(parsed)
            except (json.JSONDecodeError, ValueError) as exc:
                if json_retry >= self.max_json_retries:
                    raise ValueError("model did not return a valid JSON object") from exc
                request_messages.extend([
                    {"role": "assistant", "content": response["content"]},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was invalid: " + str(exc)
                            + ". Return a valid JSON object only, with no markdown fences."
                        ),
                    },
                ])
                continue
            completion = JSONCompletion(
                json=parsed,
                content=response["content"],
                model=response.get("model") or self.model,
                input_tokens=token_totals["input_tokens"] if seen_usage["input_tokens"] else None,
                output_tokens=token_totals["output_tokens"] if seen_usage["output_tokens"] else None,
                total_tokens=token_totals["total_tokens"] if seen_usage["total_tokens"] else None,
                request_id=response.get("request_id"),
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                request_attempts=total_attempts,
                json_retries=json_retry,
            )
            self._save_cache(cache_key, completion)
            return completion
        raise AssertionError("unreachable")

    def _cache_key(self, messages: Sequence[Mapping[str, str]]) -> str:
        material = {
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "response_format": "json_object",
            "zero_data_retention": self.zero_data_retention,
            "messages": list(messages),
        }
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _ensure_cache_dir(self) -> bool:
        parent = self.cache_path.parent
        try:
            created = not parent.exists()
            parent.mkdir(parents=True, exist_ok=True)
            if created:
                parent.chmod(0o700)
        except OSError:
            return False
        return True

    def _chmod_cache_file(self) -> None:
        try:
            self.cache_path.chmod(0o600)
        except OSError:
            pass

    def _load_cache(self, key: str) -> dict[str, Any] | None:
        if self.cache_path is None:
            return None
        if not self._ensure_cache_dir():
            return None
        try:
            with sqlite3.connect(self.cache_path, timeout=30) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS completions (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                row = connection.execute(
                    "SELECT payload FROM completions WHERE cache_key = ?", (key,)
                ).fetchone()
        except sqlite3.Error:
            # A read-only or unavailable cache must not fail every verification.
            return None
        self._chmod_cache_file()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            # A corrupt cache row must degrade to a cache miss, not a HOLD loop.
            self._delete_cache(key)
            return None

    def _save_cache(self, key: str, completion: JSONCompletion) -> None:
        if self.cache_path is None:
            return
        if not self._ensure_cache_dir():
            return
        payload = {
            "json": completion.json,
            "content": completion.content,
            "model": completion.model,
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
            "total_tokens": completion.total_tokens,
            "request_id": completion.request_id,
            "latency_ms": completion.latency_ms,
            "request_attempts": completion.request_attempts,
            "json_retries": completion.json_retries,
        }
        try:
            with sqlite3.connect(self.cache_path, timeout=30) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS completions (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT OR REPLACE INTO completions(cache_key, payload) VALUES (?, ?)",
                    (key, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
                )
        except sqlite3.Error:
            return
        self._chmod_cache_file()

    def _delete_cache(self, key: str) -> None:
        if self.cache_path is None:
            return
        try:
            with sqlite3.connect(self.cache_path, timeout=30) as connection:
                connection.execute("DELETE FROM completions WHERE cache_key = ?", (key,))
        except sqlite3.Error:
            return

    def _request_json_object(
        self, messages: Sequence[Mapping[str, str]]
    ) -> tuple[dict[str, Any], int]:
        body = json.dumps({
            "model": self.model,
            "messages": list(messages),
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self.zero_data_retention:
            headers["x-cmd-zdr"] = "1"

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, headers=headers, method="POST"
        )
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                choice = payload["choices"][0]["message"]
                content = choice.get("content")
                if not isinstance(content, str):
                    raise ValueError("chat completion response is missing text content")
                usage = payload.get("usage") or {}
                return ({
                    "content": content,
                    "model": payload.get("model"),
                    "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
                    "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
                    "total_tokens": usage.get("total_tokens"),
                    "request_id": payload.get("id"),
                }, attempt)
            except urllib.error.HTTPError as exc:
                status = exc.code
                body_text = exc.read().decode("utf-8", "replace")
                retryable = status in {429, 500, 502, 503, 504}
                if not retryable or attempt == self.max_attempts:
                    detail = self._error_message(body_text)
                    suffix = f" after {attempt} attempts" if retryable else ""
                    raise LLMRequestError(
                        f"{self.provider_name} API HTTP {status}{suffix}: {detail}",
                        attempts=attempt,
                    ) from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = self._retry_delay(attempt, retry_after)
            except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                if attempt == self.max_attempts:
                    raise LLMRequestError(
                        f"{self.provider_name} API request failed after {attempt} attempts",
                        attempts=attempt,
                    ) from exc
                delay = self._retry_delay(attempt, None)
            except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
                # Provider-side truncation or a malformed envelope is treated as
                # transient: retry within the same bound before failing closed.
                if attempt == self.max_attempts:
                    raise LLMRequestError(
                        f"{self.provider_name} API returned a malformed Chat Completions "
                        f"response after {attempt} attempts: {exc}",
                        attempts=attempt,
                    ) from exc
                delay = self._retry_delay(attempt, None)
            self._sleep(delay)
        raise AssertionError("unreachable")

    def _retry_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after is not None:
            try:
                return min(max(0.0, float(retry_after)), 60.0)
            except ValueError:
                pass
        return self.backoff_seconds * (2 ** (attempt - 1))

    @staticmethod
    def _error_message(body: str) -> str:
        try:
            payload = json.loads(body)
            error = payload.get("error", {})
            message = error.get("message") if isinstance(error, dict) else None
            return str(message or error or "request failed")
        except (json.JSONDecodeError, AttributeError):
            return body[:500] or "request failed"
