import json
import urllib.error

import pytest

from membud.llm.client import OpenAICompatibleChatClient


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def api_response(content, model="deepseek/deepseek-v4.1-flash"):
    return {
        "id": "req-1",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def test_commandcode_chat_completion_sends_model_and_json_mode(monkeypatch):
    observed = {}

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["auth"] = request.get_header("Authorization")
        observed["body"] = json.loads(request.data)
        observed["timeout"] = timeout
        return FakeResponse(api_response('{"decision":"HOLD"}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(api_key="secret", model="deepseek/deepseek-v4.1-flash")
    result = client.complete_json([{"role": "user", "content": "Assess transition"}])

    assert observed["url"] == "https://api.commandcode.ai/provider/v1/chat/completions"
    assert observed["auth"] == "Bearer secret"
    assert observed["body"]["model"] == "deepseek/deepseek-v4.1-flash"
    assert observed["body"]["temperature"] == 0
    assert observed["body"]["response_format"] == {"type": "json_object"}
    assert result.json == {"decision": "HOLD"}
    assert result.model == "deepseek/deepseek-v4.1-flash"
    assert result.input_tokens == 11
    assert result.output_tokens == 7
    assert result.total_tokens == 18


def test_completion_cache_persists_across_client_instances(monkeypatch, tmp_path):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse(api_response('{"decision":"ACCEPT"}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    cache_path = tmp_path / "responses.sqlite3"
    messages = [{"role": "user", "content": "Assess transition"}]
    first = OpenAICompatibleChatClient(
        api_key="secret", model="deepseek/deepseek-v4.1-flash", cache_path=cache_path
    ).complete_json(messages)
    second = OpenAICompatibleChatClient(
        api_key="secret", model="deepseek/deepseek-v4.1-flash", cache_path=cache_path
    ).complete_json(messages)

    assert first.cached is False
    assert second.cached is True
    assert second.json == first.json
    assert second.input_tokens is None
    assert second.request_attempts == 0
    assert len(calls) == 1


def test_cache_key_changes_when_prompt_changes(monkeypatch, tmp_path):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse(api_response('{"decision":"HOLD"}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        api_key="secret", model="deepseek/deepseek-v4.1-flash", cache_path=tmp_path / "cache.sqlite3"
    )
    client.complete_json([{"role": "user", "content": "Input one"}])
    client.complete_json([{"role": "user", "content": "Input two"}])
    assert len(calls) == 2


def test_client_adds_zdr_header_only_when_requested(monkeypatch):
    observed = {}

    def fake_urlopen(request, timeout):
        observed["zdr"] = request.get_header("X-cmd-zdr")
        return FakeResponse(api_response('{"decision":"HOLD"}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        api_key="secret", model="deepseek/deepseek-v4.1-flash", zero_data_retention=True
    )
    client.complete_json([{"role": "user", "content": "Assess transition"}])
    assert observed["zdr"] == "1"


def test_deepseek_environment_factory_uses_official_endpoint_and_model(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        OpenAICompatibleChatClient.from_deepseek_env()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    client = OpenAICompatibleChatClient.from_deepseek_env()
    assert client.base_url == "https://api.deepseek.com"
    assert client.model == "deepseek-flash"
    assert client.configuration["provider"] == "deepseek-openai-compatible"


def test_deepseek_client_uses_official_chat_completions_path(monkeypatch):
    observed = {}

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["body"] = json.loads(request.data)
        return FakeResponse(api_response('{"decision":"HOLD"}', model="deepseek-flash"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        api_key="secret", model="deepseek-flash", base_url="https://api.deepseek.com",
        provider_name="deepseek-openai-compatible",
    )
    result = client.complete_json([{"role": "user", "content": "Assess transition"}])
    assert observed["url"] == "https://api.deepseek.com/chat/completions"
    assert observed["body"]["model"] == "deepseek-flash"
    assert result.model == "deepseek-flash"


def test_client_uses_bounded_retry_for_rate_limit(monkeypatch):
    calls, delays = [], []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "rate limited", {"Retry-After": "2"}, None)
        return FakeResponse(api_response('{"decision":"ACCEPT"}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        api_key="secret", model="deepseek/deepseek-v4.1-flash", max_attempts=2,
        sleeper=delays.append,
    )
    result = client.complete_json([{"role": "user", "content": "Assess transition"}])
    assert result.json == {"decision": "ACCEPT"}
    assert len(calls) == 2
    assert delays == [2.0]


def test_malformed_json_gets_only_one_bounded_repair_call(monkeypatch):
    calls = []
    replies = ["not json", '{"decision":"HOLD"}']

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data))
        return FakeResponse(api_response(replies.pop(0)))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        api_key="secret", model="deepseek/deepseek-v4.1-flash", max_json_retries=1,
    )
    result = client.complete_json([{"role": "user", "content": "Assess transition"}])
    assert result.json == {"decision": "HOLD"}
    assert result.json_retries == 1
    assert result.input_tokens == 22
    assert result.output_tokens == 14
    assert result.total_tokens == 36
    assert result.request_attempts == 2
    assert len(calls) == 2
    assert any("valid JSON" in message["content"] for message in calls[1]["messages"])


def test_json_schema_validator_triggers_bounded_repair_retry(monkeypatch):
    replies = ["{}", '{"decision":"HOLD"}']
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data))
        return FakeResponse(api_response(replies.pop(0)))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(api_key="secret", model="deepseek/deepseek-v4.1-flash")

    def validate(payload):
        if payload.get("decision") not in {"ACCEPT", "HOLD"}:
            raise ValueError("decision field missing or invalid")

    result = client.complete_json([{"role": "user", "content": "Assess transition"}], validator=validate)
    assert result.json == {"decision": "HOLD"}
    assert result.json_retries == 1
    assert len(calls) == 2
    assert "decision field missing or invalid" in calls[1]["messages"][-1]["content"]


def test_client_does_not_retry_authentication_errors(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        api_key="bad", model="deepseek/deepseek-v4.1-flash", max_attempts=3,
        sleeper=lambda _: None,
    )
    with pytest.raises(RuntimeError, match="HTTP 401"):
        client.complete_json([{"role": "user", "content": "Assess transition"}])
    assert len(calls) == 1


def test_client_from_environment_requires_key_and_pins_model(monkeypatch):
    monkeypatch.delenv("CMD_API_KEY", raising=False)
    with pytest.raises(ValueError, match="CMD_API_KEY"):
        OpenAICompatibleChatClient.from_env()

    monkeypatch.setenv("CMD_API_KEY", "secret")
    monkeypatch.setenv("CMD_MODEL", "deepseek/deepseek-v4.1-flash")
    client = OpenAICompatibleChatClient.from_env()
    assert client.model == "deepseek/deepseek-v4.1-flash"
    assert client.base_url == "https://api.commandcode.ai/provider/v1"


def test_malformed_envelope_is_retried(monkeypatch):
    replies = [FakeResponse(b"not json"), FakeResponse(api_response('{"decision":"HOLD"}'))]
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return replies.pop(0)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        api_key="secret", model="deepseek/deepseek-v4.1-flash", sleeper=lambda _: None
    )
    result = client.complete_json([{"role": "user", "content": "Assess transition"}])

    assert result.json == {"decision": "HOLD"}
    assert result.request_attempts == 2
    assert len(calls) == 2


def test_malformed_envelope_exhausts_bounded_retries(monkeypatch):
    from membud.llm.client import LLMRequestError

    def fake_urlopen(request, timeout):
        return FakeResponse(b"<html>bad gateway</html>")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        api_key="secret", model="deepseek/deepseek-v4.1-flash", max_attempts=2,
        sleeper=lambda _: None,
    )
    with pytest.raises(LLMRequestError, match="malformed") as info:
        client.complete_json([{"role": "user", "content": "Assess transition"}])
    assert info.value.attempts == 2


def test_missing_choices_field_is_retried(monkeypatch):
    replies = [FakeResponse({"choices": []}), FakeResponse(api_response('{"decision":"HOLD"}'))]

    def fake_urlopen(request, timeout):
        return replies.pop(0)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        api_key="secret", model="deepseek/deepseek-v4.1-flash", sleeper=lambda _: None
    )
    result = client.complete_json([{"role": "user", "content": "Assess transition"}])
    assert result.request_attempts == 2


def test_client_rejects_non_https_base_url():
    with pytest.raises(ValueError, match="https"):
        OpenAICompatibleChatClient(
            api_key="secret", model="deepseek/deepseek-v4.1-flash",
            base_url="http://api.example.com",
        )


def test_corrupt_cache_entry_is_treated_as_a_miss(monkeypatch, tmp_path):
    import sqlite3

    cache_path = tmp_path / "responses.sqlite3"
    client = OpenAICompatibleChatClient(
        api_key="secret", model="deepseek/deepseek-v4.1-flash", cache_path=cache_path
    )
    key = client._cache_key([{"role": "user", "content": "Assess transition"}])
    with sqlite3.connect(cache_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS completions (cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO completions(cache_key, payload) VALUES (?, ?)",
            (key, "this is not json"),
        )

    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse(api_response('{"decision":"HOLD"}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = client.complete_json([{"role": "user", "content": "Assess transition"}])
    assert result.cached is False
    assert len(calls) == 1


def test_cache_file_is_private(monkeypatch, tmp_path):
    cache_path = tmp_path / "responses.sqlite3"
    client = OpenAICompatibleChatClient(
        api_key="secret", model="deepseek/deepseek-v4.1-flash", cache_path=cache_path
    )

    def fake_urlopen(request, timeout):
        return FakeResponse(api_response('{"decision":"HOLD"}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client.complete_json([{"role": "user", "content": "Assess transition"}])
    assert cache_path.stat().st_mode & 0o777 == 0o600


def test_unwritable_cache_degrades_to_a_miss(monkeypatch):
    import sqlite3

    def failing_connect(*args, **kwargs):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr("sqlite3.connect", failing_connect)

    def fake_urlopen(request, timeout):
        return FakeResponse(api_response('{"decision":"HOLD"}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleChatClient(
        api_key="secret",
        model="deepseek/deepseek-v4.1-flash",
        cache_path="/nonexistent/readonly/cache.sqlite3",
    )
    result = client.complete_json([{"role": "user", "content": "Assess transition"}])
    assert result.json == {"decision": "HOLD"}
    assert result.cached is False
