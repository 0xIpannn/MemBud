"""OpenAI-compatible chat client for Claude, Gemini, DeepSeek, and Command Code."""
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

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        json_retries: int = 0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.json_retries = json_retries
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens

    @property
    def usage(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "api_attempts": self.attempts,
            "json_retries": self.json_retries,
            "cache_hit": 0,
        }


class LLMJSONError(ValueError):
    """Provider responses failed JSON/schema validation after repair attempts."""

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        json_retries: int = 0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.json_retries = json_retries
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = total_tokens

    @property
    def usage(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "api_attempts": self.attempts,
            "json_retries": self.json_retries,
            "cache_hit": 0,
        }


def _is_valid_cached_payload(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("json"), dict)
        and isinstance(payload.get("content"), str)
        and isinstance(payload.get("model"), str)
        and bool(payload["model"].strip())
    )


class CacheMissError(RuntimeError):
    """Cache-only mode is active and no usable cached completion exists.

    Raised before any network I/O so a cache-only run can never silently fall
    back to a paid provider request.  ``cache_miss`` marks the exception so
    layers that normally fail closed on transport errors (for example the
    semantic comparator) can let this safety abort propagate instead.
    """

    cache_miss = True

    def __init__(self, message: str, *, cache_key: str = "") -> None:
        super().__init__(message)
        self.cache_key = cache_key


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


_PROVIDER_ALIASES: dict[str, str] = {
    "anthropic": "claude",
    "google": "gemini",
}


def _first_env(*names: str, default: str = "") -> str:
    """Return the first non-empty environment variable value among ``names``."""
    for name in names:
        value = os.environ.get(name, "")
        if value.strip():
            return value
    return default


class OpenAICompatibleChatClient:
    """Small standard-library client for Claude, Gemini, and OpenAI-compatible APIs.

    Credentials are passed explicitly or read from provider environment
    variables (``ANTHROPIC_API_KEY``, ``GEMINI_API_KEY``, or legacy
    ``DEEPSEEK_API_KEY`` / ``CMD_API_KEY``). The client never logs credentials
    or request content. Transient network/429/5xx failures get bounded
    exponential-backoff retries; other HTTP errors fail immediately. Malformed
    JSON gets a separate bounded repair request.
    """

    CLAUDE_BASE_URL = "https://api.anthropic.com/v1"
    CLAUDE_MODEL = "claude-haiku-4-5-20251001"
    CLAUDE_SONNET_MODEL = "claude-sonnet-4-5-20250929"
    ANTHROPIC_VERSION = "2023-06-01"

    GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
    GEMINI_MODEL = "gemini-2.5-flash"
    GEMINI_PRO_MODEL = "gemini-2.5-pro"

    DEFAULT_BASE_URL = "https://api.commandcode.ai/provider/v1"
    DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
    DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    DEEPSEEK_MODEL = "deepseek-flash"

    PRIMARY_PROVIDERS = ("claude", "gemini")
    SUPPORTED_PROVIDERS = ("claude", "gemini", "deepseek", "commandcode")

    _PROVIDER_ENV_SPECS: dict[str, dict[str, Any]] = {
        "claude": {
            "key_envs": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
            "model_envs": ("CLAUDE_MODEL", "ANTHROPIC_MODEL"),
            "default_model": CLAUDE_MODEL,
            "base_url_envs": ("CLAUDE_BASE_URL", "ANTHROPIC_BASE_URL"),
            "default_base_url": CLAUDE_BASE_URL,
            "cache_envs": ("CLAUDE_CACHE_PATH", "ANTHROPIC_CACHE_PATH"),
            "default_cache": "~/.cache/membud/claude-cache.sqlite3",
            "provider_name": "claude-openai-compatible",
            "missing_key_message": "ANTHROPIC_API_KEY must be set in the process environment",
            "zdr_env": None,
        },
        "gemini": {
            "key_envs": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            "model_envs": ("GEMINI_MODEL",),
            "default_model": GEMINI_MODEL,
            "base_url_envs": ("GEMINI_BASE_URL",),
            "default_base_url": GEMINI_BASE_URL,
            "cache_envs": ("GEMINI_CACHE_PATH",),
            "default_cache": "~/.cache/membud/gemini-cache.sqlite3",
            "provider_name": "gemini-openai-compatible",
            "missing_key_message": "GEMINI_API_KEY must be set in the process environment",
            "zdr_env": None,
        },
        "deepseek": {
            "key_envs": ("DEEPSEEK_API_KEY",),
            "model_envs": ("DEEPSEEK_MODEL",),
            "default_model": DEEPSEEK_MODEL,
            "base_url_envs": ("DEEPSEEK_BASE_URL",),
            "default_base_url": DEEPSEEK_BASE_URL,
            "cache_envs": ("DEEPSEEK_CACHE_PATH",),
            "default_cache": "~/.cache/membud/deepseek-cache.sqlite3",
            "provider_name": "deepseek-openai-compatible",
            "missing_key_message": "DEEPSEEK_API_KEY must be set in the process environment",
            "zdr_env": None,
        },
        "commandcode": {
            "key_envs": ("CMD_API_KEY",),
            "model_envs": ("CMD_MODEL",),
            "default_model": DEFAULT_MODEL,
            "base_url_envs": ("CMD_BASE_URL",),
            "default_base_url": DEFAULT_BASE_URL,
            "cache_envs": ("CMD_CACHE_PATH",),
            "default_cache": "~/.cache/membud/commandcode-cache.sqlite3",
            "provider_name": "commandcode-openai-compatible",
            "missing_key_message": "CMD_API_KEY must be set",
            "zdr_env": "CMD_ZDR",
        },
    }

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
        require_cache: bool = False,
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
        if require_cache and cache_path is None:
            raise ValueError("require_cache mode requires an explicit cache_path")
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
        self.require_cache = require_cache
        # Telemetry: cache-only misses observed on this client instance.  A miss
        # always raises, but layers that fail closed on transport errors (e.g.
        # verify_with_llm) may swallow the exception; the counter lets the harness
        # still abort the run loudly.
        self.cache_misses = 0
        self.last_cache_miss_key = ""
        # Telemetry: provider request cycles attempted by this client instance
        # (cache hits are not counted).  Runners use this to distinguish a row
        # that never reached the provider from one whose response was lost, so
        # a pre-request failure is not charged a phantom token estimate.
        self.provider_calls = 0
        self._sleep = sleeper

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider_name={self.provider_name!r}, "
            f"model={self.model!r}, base_url={self.base_url!r})"
        )

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
            "response_format": "prompt_json" if self._is_anthropic() else "json_object",
            "zero_data_retention": self.zero_data_retention,
            "persistent_cache": self.cache_path is not None,
            "require_cache": self.require_cache,
        }

    @classmethod
    def _from_spec(
        cls,
        provider: str,
        *,
        require_cache: bool = False,
        cache_only_without_key: bool = False,
    ) -> "OpenAICompatibleChatClient":
        spec = cls._PROVIDER_ENV_SPECS[provider]
        api_key = _first_env(*spec["key_envs"])
        if not api_key.strip():
            if cache_only_without_key and require_cache:
                api_key = "offline-cache-only"
            else:
                raise ValueError(spec["missing_key_message"])
        zdr = False
        if spec["zdr_env"]:
            zdr = os.environ.get(spec["zdr_env"], "0").lower() in {"1", "true", "yes"}
        return cls(
            api_key=api_key,
            model=_first_env(*spec["model_envs"], default=spec["default_model"]),
            base_url=_first_env(*spec["base_url_envs"], default=spec["default_base_url"]),
            cache_path=_first_env(*spec["cache_envs"], default=spec["default_cache"]),
            provider_name=spec["provider_name"],
            zero_data_retention=zdr,
            require_cache=require_cache,
        )

    @classmethod
    def from_claude_env(
        cls, *, require_cache: bool = False
    ) -> "OpenAICompatibleChatClient":
        """Build a client for Anthropic Claude's OpenAI-compatible API."""
        return cls._from_spec("claude", require_cache=require_cache)

    @classmethod
    def from_gemini_env(
        cls, *, require_cache: bool = False
    ) -> "OpenAICompatibleChatClient":
        """Build a client for Google Gemini's OpenAI-compatible API."""
        return cls._from_spec("gemini", require_cache=require_cache)

    @classmethod
    def from_env(
        cls, *, require_cache: bool = False
    ) -> "OpenAICompatibleChatClient":
        """Build a client from Command Code environment variables."""
        return cls._from_spec("commandcode", require_cache=require_cache)

    @classmethod
    def from_deepseek_env(
        cls, *, require_cache: bool = False
    ) -> "OpenAICompatibleChatClient":
        """Build a client for DeepSeek's official OpenAI-compatible API."""
        return cls._from_spec("deepseek", require_cache=require_cache)

    @classmethod
    def normalize_provider(cls, provider: str) -> str:
        """Normalize provider aliases and validate against ``SUPPORTED_PROVIDERS``."""
        normalized = _PROVIDER_ALIASES.get(provider.strip().lower(), provider.strip().lower())
        if normalized not in cls.SUPPORTED_PROVIDERS:
            supported = ", ".join(cls.SUPPORTED_PROVIDERS)
            raise ValueError(f"unsupported provider {provider!r}; expected one of: {supported}")
        return normalized

    @classmethod
    def from_provider_env(
        cls,
        provider: str = "claude",
        *,
        require_cache: bool = False,
        cache_only_without_key: bool = False,
    ) -> "OpenAICompatibleChatClient":
        """Build a client for a named provider (``claude``, ``gemini``, ``deepseek``, ``commandcode``)."""
        normalized = cls.normalize_provider(provider)
        if cache_only_without_key and require_cache:
            return cls._from_spec(
                normalized, require_cache=require_cache, cache_only_without_key=True
            )
        factories = {
            "claude": cls.from_claude_env,
            "gemini": cls.from_gemini_env,
            "deepseek": cls.from_deepseek_env,
            "commandcode": cls.from_env,
        }
        return factories[normalized](require_cache=require_cache)

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
                if not _is_valid_cached_payload(cached):
                    raise ValueError("cached payload missing required completion fields")
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
            except (KeyError, ValueError, TypeError):
                self._delete_cache(cache_key)
                cached = None
        if cached is None and self.require_cache:
            # Cache-only safety guard: raise before any network I/O so a run can
            # never turn a cache miss into a paid provider request.
            self.cache_misses += 1
            self.last_cache_miss_key = cache_key
            raise CacheMissError(
                "cache-only mode is enabled but the request has no usable cached "
                f"completion (cache_key={cache_key}); refusing to contact the provider",
                cache_key=cache_key,
            )
        started = time.perf_counter()
        total_attempts = 0
        token_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        seen_usage = {key: False for key in token_totals}
        for json_retry in range(self.max_json_retries + 1):
            self.provider_calls += 1
            try:
                response, attempts = self._request_json_object(request_messages)
            except LLMRequestError as exc:
                if total_attempts or any(seen_usage.values()):
                    raise LLMRequestError(
                        str(exc),
                        attempts=total_attempts + exc.attempts,
                        json_retries=json_retry,
                        input_tokens=(
                            token_totals["input_tokens"] if seen_usage["input_tokens"] else None
                        ),
                        output_tokens=(
                            token_totals["output_tokens"] if seen_usage["output_tokens"] else None
                        ),
                        total_tokens=(
                            token_totals["total_tokens"] if seen_usage["total_tokens"] else None
                        ),
                    ) from exc
                raise
            total_attempts += attempts
            for key in token_totals:
                value = response.get(key)
                if value is not None:
                    token_totals[key] += int(value)
                    seen_usage[key] = True
            try:
                content_text = (
                    self._strip_markdown_fence(response["content"])
                    if self._is_anthropic()
                    else response["content"]
                )
                parsed = json.loads(content_text)
                if not isinstance(parsed, dict):
                    raise ValueError("JSON response must be an object")
                if validator is not None:
                    validator(parsed)
            except (json.JSONDecodeError, ValueError) as exc:
                if json_retry >= self.max_json_retries:
                    raise LLMJSONError(
                        "model did not return a valid JSON object",
                        attempts=total_attempts,
                        json_retries=json_retry,
                        input_tokens=(
                            token_totals["input_tokens"] if seen_usage["input_tokens"] else None
                        ),
                        output_tokens=(
                            token_totals["output_tokens"] if seen_usage["output_tokens"] else None
                        ),
                        total_tokens=(
                            token_totals["total_tokens"] if seen_usage["total_tokens"] else None
                        ),
                    ) from exc
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
                content=content_text,
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
            "response_format": "prompt_json" if self._is_anthropic() else "json_object",
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
            parsed = json.loads(row[0])
        except json.JSONDecodeError:
            # A corrupt cache row must degrade to a cache miss, not a HOLD loop.
            self._delete_cache(key)
            return None
        if not _is_valid_cached_payload(parsed):
            self._delete_cache(key)
            return None
        return parsed

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

    def _is_anthropic(self) -> bool:
        return (
            self.provider_name.startswith(("claude", "anthropic"))
            or "api.anthropic.com" in self.base_url
        )

    @staticmethod
    def _strip_markdown_fence(text: str) -> str:
        """Unwrap a single outer markdown code fence when Anthropic adds one."""
        stripped = text.strip()
        if not (stripped.startswith("```") and stripped.endswith("```")):
            return text
        lines = stripped.splitlines()
        if len(lines) < 3 or lines[-1].strip() != "```":
            return text
        return "\n".join(lines[1:-1]).strip()

    @staticmethod
    def _extract_response_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
        """Extract text content and normalized usage from OpenAI or Anthropic payloads."""
        if "choices" not in payload and isinstance(payload.get("content"), list):
            text_parts = [
                block["text"]
                for block in payload["content"]
                if isinstance(block, Mapping)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ]
            if not text_parts:
                raise ValueError("anthropic response is missing text content")
            content = "".join(text_parts)
        else:
            choice = payload["choices"][0]["message"]
            content = choice.get("content")
            if not isinstance(content, str):
                raise ValueError("chat completion response is missing text content")

        usage = payload.get("usage") or {}
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        total_tokens = usage.get("total_tokens")
        if (
            total_tokens is None
            and isinstance(input_tokens, int)
            and not isinstance(input_tokens, bool)
            and isinstance(output_tokens, int)
            and not isinstance(output_tokens, bool)
        ):
            total_tokens = input_tokens + output_tokens
        return {
            "content": content,
            "model": payload.get("model"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "request_id": payload.get("id"),
        }

    def _request_json_object(
        self, messages: Sequence[Mapping[str, str]]
    ) -> tuple[dict[str, Any], int]:
        if self.require_cache:  # defense in depth: cache-only mode never dials out
            raise CacheMissError(
                "cache-only mode is enabled; provider request refused"
            )
        payload_body: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": self.temperature,
        }
        if self._is_anthropic():
            # Anthropic's OpenAI-compatible endpoint rejects response_format=json_object.
            payload_body["max_tokens"] = 4096
        else:
            payload_body["response_format"] = {"type": "json_object"}
        body = json.dumps(payload_body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._is_anthropic():
            headers["x-api-key"] = self._api_key
            headers["anthropic-version"] = self.ANTHROPIC_VERSION
        if self.zero_data_retention:
            headers["x-cmd-zdr"] = "1"

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, headers=headers, method="POST"
        )
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return (self._extract_response_fields(payload), attempt)
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
