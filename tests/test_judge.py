import json
from io import BytesIO
from urllib.error import URLError

import pytest

from circuitbreaker.judge import (
    JsonRiskJudge,
    JudgeConfigurationError,
    JudgeResponseError,
    OpenAICompatibleClient,
    build_judge,
)
from circuitbreaker.models import Decision, ToolCall, ToolKind, Verdict


class StubClient:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete(self, *, system_prompt, user_prompt):
        self.prompts.append((system_prompt, user_prompt))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def call(target="pip install example"):
    return ToolCall(kind=ToolKind.SHELL, target=target, agent_id="test-agent")


def ask_verdict():
    return Verdict(decision=Decision.ASK, reason="No rule matched.", source="default")


@pytest.mark.parametrize(
    ("content", "decision"),
    [
        ('{"decision":"allow","reason":"Low risk","feedback":null}', Decision.ALLOW),
        (
            '{"decision":"block","reason":"Destructive action","feedback":"Ask first"}',
            Decision.BLOCK,
        ),
    ],
)
def test_json_risk_judge_maps_strict_response(content, decision):
    client = StubClient(content)
    result = JsonRiskJudge(client).judge(call(), ask_verdict())

    assert result.decision is decision
    assert result.source == "judge"
    assert client.prompts
    user_payload = json.loads(client.prompts[0][1])
    assert user_payload["action"]["target"] == "pip install example"
    assert user_payload["deterministic_result"]["decision"] == "ask"
    assert "untrusted data" in client.prompts[0][0]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"decision":"ask","reason":"uncertain"}',
        '{"decision":"allow","reason":"ok","extra":true}',
        '{"decision":"allow","reason":""}',
        '{"decision":"allow","decision":"block","reason":"duplicate"}',
        "```json\n{\"decision\":\"allow\",\"reason\":\"ok\"}\n```",
    ],
)
def test_json_risk_judge_rejects_invalid_responses(content):
    with pytest.raises(JudgeResponseError):
        JsonRiskJudge(StubClient(content)).judge(call(), ask_verdict())


def test_json_risk_judge_propagates_client_errors():
    with pytest.raises(RuntimeError, match="unavailable"):
        JsonRiskJudge(StubClient(RuntimeError("unavailable"))).judge(call(), ask_verdict())


def test_openai_compatible_client_extracts_content_and_auth_header(monkeypatch):
    seen = {}

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    class Opener:
        def open(self, request, timeout):
            return fake_response(request, timeout)

    def fake_response(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.data)
        seen["timeout"] = timeout
        content = json.dumps({"decision": "block", "reason": "risk"})
        payload = {"choices": [{"message": {"content": content}}]}
        return Response(json.dumps(payload).encode())

    monkeypatch.setattr("urllib.request.build_opener", lambda *_args: Opener())
    client = OpenAICompatibleClient(
        "https://judge.example/v1/chat/completions",
        "model-name",
        api_key="test-key",
        timeout=3.5,
    )

    assert json.loads(client.complete(system_prompt="system", user_prompt="user")) == {
        "decision": "block",
        "reason": "risk",
    }
    assert seen["url"] == "https://judge.example/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["body"]["model"] == "model-name"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["timeout"] == 3.5


def test_openai_compatible_client_fails_without_leaking_transport_error(monkeypatch):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise URLError("sensitive transport details")

    def build_opener(*_args):
        return Opener()

    monkeypatch.setattr("urllib.request.build_opener", build_opener)
    client = OpenAICompatibleClient("http://localhost:8000/v1/chat/completions", "local")
    with pytest.raises(RuntimeError, match="risk-judge request failed") as exc:
        client.complete(system_prompt="system", user_prompt="user")
    assert "sensitive transport details" not in str(exc.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://judge.example/v1/chat/completions",
        "https://user:password@judge.example/api",
        "https://judge.example/api?token=secret",
        "ftp://judge.example/api",
    ],
)
def test_remote_client_requires_safe_http_endpoint(endpoint):
    with pytest.raises(JudgeConfigurationError):
        OpenAICompatibleClient(endpoint, "model")


def test_local_http_endpoint_is_supported():
    OpenAICompatibleClient("http://127.0.0.1:8000/v1/chat/completions", "local")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_client_rejects_invalid_timeout(timeout):
    with pytest.raises(JudgeConfigurationError, match="timeout"):
        OpenAICompatibleClient(
            "http://127.0.0.1:8000/v1/chat/completions",
            "local",
            timeout=timeout,
        )


def test_build_judge_is_disabled_without_endpoint():
    assert build_judge(None, None, "UNUSED_KEY", 20) is None


@pytest.mark.parametrize(
    ("endpoint", "model", "message"),
    [
        ("https://judge.example/api", None, "supplied together"),
        ("https://judge.example/api", "model", "environment variable"),
    ],
)
def test_build_judge_rejects_incomplete_or_missing_credentials(
    monkeypatch, endpoint, model, message
):
    monkeypatch.delenv("TEST_JUDGE_KEY", raising=False)
    with pytest.raises(JudgeConfigurationError, match=message):
        build_judge(endpoint, model, "TEST_JUDGE_KEY", 20)
