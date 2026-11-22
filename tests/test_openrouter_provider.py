"""Offline tests for the OpenRouter provider, zero-price guard, and secret hygiene."""
from __future__ import annotations

import io
import json
import os
import urllib.error

import pytest

from membud.llm.client import (
    LLMRequestError,
    OpenAICompatibleChatClient,
    OpenRouterPricingError,
    enforce_openrouter_zero_price,
    fetch_openrouter_models,
    load_dotenv_0600,
    resolve_openrouter_model_pricing,
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def _catalog():
    return [
        {
            "id": "free/model:free",
            "name": "Free Model",
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {
            "id": "deepseek/deepseek-v4.1-flash",
            "name": "DeepSeek V4.1 Flash",
            "pricing": {
                "prompt": "0.0000000498",
                "completion": "0.0000001992",
            },
        },
    ]


def _api_response(content, model="free/model:free"):
    return {
        "id": "req-openrouter",
        "model": model,
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


# ---------------------------------------------------------------------------
# Catalog and pricing guard
# ---------------------------------------------------------------------------


def test_fetch_openrouter_models_uses_public_models_endpoint(monkeypatch):
    observed = {}

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["accept"] = request.get_header("Accept")
        observed["method"] = request.get_method()
        return FakeResponse({"data": _catalog()})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    models = fetch_openrouter_models("https://openrouter.ai/api/v1", timeout=5)
    assert len(models) == 2
    assert observed["url"] == "https://openrouter.ai/api/v1/models"
    assert observed["accept"] == "application/json"
    assert observed["method"] == "GET"


def test_fetch_openrouter_models_requires_https_and_valid_payload(monkeypatch):
    with pytest.raises(ValueError, match="https"):
        fetch_openrouter_models("http://openrouter.ai/api/v1")

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda request, timeout: FakeResponse({"nope": []})
    )
    with pytest.raises(OpenRouterPricingError, match="missing 'data'"):
        fetch_openrouter_models()


def test_resolve_model_pricing_by_exact_slug():
    free = resolve_openrouter_model_pricing("free/model:free", _catalog())
    assert free["is_free"] is True
    assert free["prompt_price_per_token"] == 0.0
    assert free["completion_price_per_token"] == 0.0
    assert free["prompt_per_mtok"] == 0.0

    paid = resolve_openrouter_model_pricing("deepseek/deepseek-v4.1-flash", _catalog())
    assert paid["is_free"] is False
    assert paid["prompt_per_mtok"] == pytest.approx(0.0498)
    assert paid["completion_per_mtok"] == pytest.approx(0.1992)


def test_resolve_model_pricing_rejects_missing_slug_and_lists_free_hint():
    with pytest.raises(OpenRouterPricingError, match="was not found") as info:
        resolve_openrouter_model_pricing("missing/model", _catalog())
    assert "free/model:free" in str(info.value)


@pytest.mark.parametrize(
    "pricing",
    [
        None,
        {"prompt": None, "completion": "0"},
        {"prompt": "-1", "completion": "0"},
        {"prompt": "not-a-number", "completion": "0"},
        {"prompt": True, "completion": "0"},
    ],
)
def test_resolve_model_pricing_rejects_missing_or_invalid_prices(pricing):
    catalog = [{"id": "m", "name": "M", "pricing": pricing}]
    with pytest.raises(OpenRouterPricingError):
        resolve_openrouter_model_pricing("m", catalog)


def test_zero_price_guard_blocks_paid_model_without_allow_paid():
    with pytest.raises(OpenRouterPricingError, match="non-zero pricing"):
        enforce_openrouter_zero_price(
            "deepseek/deepseek-v4.1-flash", models=_catalog()
        )

    allowed = enforce_openrouter_zero_price(
        "deepseek/deepseek-v4.1-flash", models=_catalog(), allow_paid=True
    )
    assert allowed["is_free"] is False
    assert enforce_openrouter_zero_price("free/model:free", models=_catalog())[
        "is_free"
    ] is True


# ---------------------------------------------------------------------------
# Client construction, requests, and key hygiene
# ---------------------------------------------------------------------------


def _clear_openrouter_env(monkeypatch):
    for name in (
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_BASE_URL",
        "OPENROUTER_CACHE_PATH",
        "OPENROUTER_ZDR",
    ):
        monkeypatch.delenv(name, raising=False)


def test_openrouter_factory_requires_key(monkeypatch):
    _clear_openrouter_env(monkeypatch)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        OpenAICompatibleChatClient.from_openrouter_env()


def test_openrouter_client_sends_openai_compatible_request(monkeypatch, tmp_path):
    _clear_openrouter_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setenv("OPENROUTER_MODEL", "free/model:free")
    monkeypatch.setenv("OPENROUTER_CACHE_PATH", str(tmp_path / "cache.sqlite3"))
    observed = {}

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["auth"] = request.get_header("Authorization")
        observed["body"] = json.loads(request.data)
        return FakeResponse(_api_response('{"decision":"HOLD"}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient.from_openrouter_env()
    result = client.complete_json([{"role": "user", "content": "Assess transition"}])

    assert client.base_url == "https://openrouter.ai/api/v1"
    assert client.model == "free/model:free"
    assert observed["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert observed["auth"] == "Bearer sk-or-secret"
    assert observed["body"]["model"] == "free/model:free"
    assert observed["body"]["response_format"] == {"type": "json_object"}
    assert result.json == {"decision": "HOLD"}
    assert result.total_tokens == 8


def test_openrouter_client_redacts_key_from_provider_errors(monkeypatch):
    secret = "sk-or-super-secret-value"

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "unauthorized",
            {},
            io.BytesIO(f'{{"error":"invalid key {secret}"}}'.encode()),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        api_key=secret,
        model="free/model:free",
        base_url="https://openrouter.ai/api/v1",
        provider_name="openrouter-openai-compatible",
        max_attempts=1,
    )
    with pytest.raises(LLMRequestError) as info:
        client.complete_json([{"role": "user", "content": "Assess transition"}])
    message = str(info.value)
    assert secret not in message
    assert "[REDACTED_API_KEY]" in message


def test_openrouter_repr_and_configuration_never_contain_key():
    secret = "sk-or-never-log"
    client = OpenAICompatibleChatClient(
        api_key=secret,
        model="free/model:free",
        base_url="https://openrouter.ai/api/v1",
        provider_name="openrouter-openai-compatible",
    )
    assert secret not in repr(client)
    assert secret not in json.dumps(client.configuration)
    assert client.configuration["provider"] == "openrouter-openai-compatible"


def test_from_provider_env_supports_openrouter(monkeypatch):
    _clear_openrouter_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-alias")
    monkeypatch.setenv("OPENROUTER_MODEL", "free/model:free")
    client = OpenAICompatibleChatClient.from_provider_env("openrouter")
    assert client.model == "free/model:free"
    assert client.provider_name == "openrouter-openai-compatible"


# ---------------------------------------------------------------------------
# Private .env handling
# ---------------------------------------------------------------------------


def test_load_dotenv_0600_parses_quoted_values_and_preserves_environment(
    monkeypatch, tmp_path
):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# comment\nexport OPENROUTER_API_KEY='sk-or-file'\n"
        'OPENROUTER_MODEL="free/model:free"\nIGNORED\n',
        encoding="utf-8",
    )
    dotenv.chmod(0o600)
    monkeypatch.setenv("OPENROUTER_MODEL", "env-wins")

    parsed = load_dotenv_0600(dotenv, populate_environ=True)
    assert parsed["OPENROUTER_API_KEY"] == "sk-or-file"
    assert parsed["OPENROUTER_MODEL"] == "free/model:free"
    assert "IGNORED" not in parsed
    assert os.environ["OPENROUTER_MODEL"] == "env-wins"


def test_load_dotenv_0600_refuses_loose_permissions(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("OPENROUTER_API_KEY=sk-or-file\n", encoding="utf-8")
    dotenv.chmod(0o644)
    with pytest.raises(ValueError, match="permissions"):
        load_dotenv_0600(dotenv)
    assert load_dotenv_0600(dotenv, require_0600=False)["OPENROUTER_API_KEY"] == "sk-or-file"


def test_load_dotenv_0600_missing_file_returns_empty(tmp_path):
    assert load_dotenv_0600(tmp_path / "missing.env") == {}


def test_openrouter_factory_can_read_private_dotenv_without_exporting_secret(
    monkeypatch, tmp_path
):
    _clear_openrouter_env(monkeypatch)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "OPENROUTER_API_KEY=sk-or-dotenv\n"
        "OPENROUTER_MODEL=free/model:free\n"
        "OPENROUTER_BASE_URL=https://openrouter.ai/api/v1\n"
        f"OPENROUTER_CACHE_PATH={tmp_path / 'cache.sqlite3'}\n",
        encoding="utf-8",
    )
    dotenv.chmod(0o600)

    observed = {}

    def fake_urlopen(request, timeout):
        observed["auth"] = request.get_header("Authorization")
        return FakeResponse(_api_response('{"decision":"ACCEPT"}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient.from_openrouter_env(dotenv_path=dotenv)
    client.complete_json([{"role": "user", "content": "Assess transition"}])

    assert client.model == "free/model:free"
    assert observed["auth"] == "Bearer sk-or-dotenv"
    assert "OPENROUTER_API_KEY" not in os.environ


def test_openrouter_factory_rejects_world_readable_dotenv(monkeypatch, tmp_path):
    _clear_openrouter_env(monkeypatch)
    dotenv = tmp_path / ".env"
    dotenv.write_text("OPENROUTER_API_KEY=sk-or-dotenv\n", encoding="utf-8")
    dotenv.chmod(0o644)
    with pytest.raises(ValueError, match="permissions"):
        OpenAICompatibleChatClient.from_openrouter_env(dotenv_path=dotenv)
