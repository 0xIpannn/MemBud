"""Offline tests for the DeepSeek route: price guard, thinking switch, key hygiene."""
from __future__ import annotations

import json

import pytest

from membud.llm.client import (
    DEEPSEEK_PRICES_PER_MTOK,
    DeepSeekPricingError,
    OpenAICompatibleChatClient,
    enforce_deepseek_price,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def _ok(content='{"ok": true}'):
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


def test_price_guard_refuses_paid_model_without_allow_paid():
    with pytest.raises(DeepSeekPricingError, match="--allow-paid"):
        enforce_deepseek_price("deepseek-flash")


def test_price_guard_refuses_unknown_model_even_with_allow_paid():
    with pytest.raises(DeepSeekPricingError, match="unknown DeepSeek model"):
        enforce_deepseek_price("deepseek-mystery", allow_paid=True)


def test_price_guard_uses_peak_rate_conservatively():
    info = enforce_deepseek_price("deepseek-flash", allow_paid=True)
    assert info["is_free"] is False
    assert info["prompt_per_mtok"] == DEEPSEEK_PRICES_PER_MTOK["deepseek-flash"]["input_miss_peak"]
    assert info["completion_per_mtok"] == DEEPSEEK_PRICES_PER_MTOK["deepseek-flash"]["output_peak"]
    assert info["offpeak_prompt_per_mtok"] < info["prompt_per_mtok"]
    assert enforce_deepseek_price("deepseek-v4-flash", allow_paid=True)["prompt_per_mtok"] == info["prompt_per_mtok"]


def test_thinking_disabled_is_sent_and_keyed_in_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-secret-value")
    monkeypatch.setenv("DEEPSEEK_CACHE_PATH", str(tmp_path / "cache.sqlite3"))
    sent = []

    def fake_urlopen(request, timeout=None):
        sent.append(json.loads(request.data))
        return FakeResponse(_ok())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    off = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
    default = OpenAICompatibleChatClient.from_deepseek_env()
    off.complete_json([{"role": "user", "content": "hi"}])
    default.complete_json([{"role": "user", "content": "hi"}])
    assert sent[0]["thinking"] == {"type": "disabled"}
    assert "thinking" not in sent[1]
    msgs = [{"role": "user", "content": "hi"}]
    assert off._cache_key(msgs) != default._cache_key(msgs)
    assert off.configuration["extra_body"] == {"thinking": {"type": "disabled"}}


def test_default_deepseek_cache_key_is_unchanged_when_no_extra_body(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-secret-value")
    monkeypatch.setenv("DEEPSEEK_CACHE_PATH", str(tmp_path / "cache.sqlite3"))
    client = OpenAICompatibleChatClient.from_deepseek_env()
    assert client.extra_body == {}
    # extra_body must not appear in the cache material unless set.
    assert "extra_body" not in json.dumps(
        {"k": client._cache_key([{"role": "user", "content": "x"}])}
    )


def test_api_key_never_appears_in_repr_or_configuration(monkeypatch, tmp_path):
    secret = "sk-test-secret-value"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    monkeypatch.setenv("DEEPSEEK_CACHE_PATH", str(tmp_path / "cache.sqlite3"))
    client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
    assert secret not in repr(client)
    assert secret not in json.dumps(client.configuration)
