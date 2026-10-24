import json

import pytest

from circuitbreaker.proxy import Gate, Interceptor
from circuitbreaker.proxy.interceptor import Drop, Forward, Reply
from circuitbreaker.rules import RuleEngine


@pytest.fixture
def icp():
    return Interceptor(Gate(RuleEngine.from_default()))


def line(obj):
    return json.dumps(obj).encode() + b"\n"


def call(name, arguments=None, id=1):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return line({"jsonrpc": "2.0", "id": id, "method": "tools/call", "params": params})


def body(reply: Reply):
    return json.loads(reply.data)


def test_non_tool_messages_pass_through_unchanged(icp):
    for msg in (
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "result": {}},
    ):
        data = line(msg)
        assert icp.process(data) == Forward(data)


def test_allowed_call_forwards_the_original_bytes(icp):
    data = call("run_command", {"command": "ls"})
    assert icp.process(data) == Forward(data)


def test_blocked_call_gets_structured_tool_error_with_same_id(icp):
    action = icp.process(call("run_command", {"command": "rm -rf /"}, id="abc"))
    assert isinstance(action, Reply)
    msg = body(action)
    assert msg["id"] == "abc" and msg["result"]["isError"] is True
    text = msg["result"]["content"][0]["text"]
    assert "Blocked by CircuitBreaker" in text and "Suggested alternative" in text
    payload = json.loads(text.splitlines()[-1])
    assert payload["error"] == "blocked_by_circuitbreaker"
    assert payload["source"] == "rule:shell-rm-dangerous-target"


def test_id_zero_is_valid(icp):
    msg = body(icp.process(call("run_command", {"command": "rm -rf /"}, id=0)))
    assert msg["id"] == 0


def test_response_is_one_line_of_json(icp):
    action = icp.process(call("run_command", {"command": "rm -rf /\nsecond line"}))
    assert action.data.count(b"\n") == 1 and action.data.endswith(b"\n")


@pytest.mark.parametrize("raw", [b"not json\n", b'{"unterminated": \n', b"\xff\xfe\n"])
def test_unparseable_lines_are_never_forwarded(icp, raw):
    action = icp.process(raw)
    assert isinstance(action, Reply) and body(action)["error"]["code"] == -32700


def test_blank_lines_are_dropped(icp):
    assert icp.process(b"\n") == Drop()


@pytest.mark.parametrize(
    "params",
    [None, [], "x", {}, {"name": ""}, {"name": 5}, {"arguments": {}}],
)
def test_malformed_tools_call_is_rejected_not_forwarded(icp, params):
    data = line({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    action = icp.process(data)
    assert isinstance(action, Reply) and body(action)["error"]["code"] == -32602


def test_tools_call_notification_is_dropped(icp):
    data = line({"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "x"}})
    assert icp.process(data) == Drop()


def test_batch_with_tools_call_is_rejected_but_plain_batch_passes(icp):
    bad = line([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "x"}}])
    assert isinstance(icp.process(bad), Reply)
    ok = line([{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    assert icp.process(ok) == Forward(ok)


def test_client_name_becomes_agent_id(icp):
    init = line({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"clientInfo": {"name": "cursor"}}})  # fmt: skip
    assert icp.process(init) == Forward(init)
    assert icp.agent_id == "cursor"


def test_internal_errors_never_forward(icp, monkeypatch):
    monkeypatch.setattr(icp.gate, "check", lambda *a, **k: 1 / 0)
    action = icp.process(call("run_command", {"command": "ls"}, id=9))
    assert isinstance(action, Reply)
    assert body(action)["id"] == 9 and body(action)["error"]["code"] == -32603


def test_missing_arguments_default_to_empty(icp):
    action = icp.process(call("mystery"))  # no arguments key at all
    assert isinstance(action, Reply)  # unknown tool -> ask -> denied
