"""Tests for the agent-hook protocol layer."""

import pytest

from circuitbreaker.hooks.protocol import HookRequest, HookResponse, run_hook
from circuitbreaker.models import Decision
from circuitbreaker.proxy import Gate
from circuitbreaker.rules import RuleEngine


@pytest.fixture(scope="module")
def gate():
    return Gate(RuleEngine.from_default())


@pytest.fixture(scope="module")
def monitor_gate():
    return Gate(RuleEngine.from_default(), mode="monitor")


# ---------------------------------------------------------------------------
# HookRequest / HookResponse dataclasses
# ---------------------------------------------------------------------------


def test_hook_request_defaults():
    req = HookRequest(tool_name="Bash", arguments={"command": "ls"})
    assert req.agent_id == "unknown"
    assert req.arguments == {"command": "ls"}


def test_hook_response_to_claude_allow():
    resp = HookResponse(allowed=True, reason="All good")
    assert resp.to_claude_payload() == {"decision": "allow"}


def test_hook_response_to_claude_block_with_feedback():
    resp = HookResponse(allowed=False, reason="Too dangerous", feedback="Use git status instead")
    payload = resp.to_claude_payload()
    assert payload["decision"] == "block"
    assert "Too dangerous" in payload["reason"]
    assert payload["feedback"] == "Use git status instead"


def test_hook_response_to_claude_block_no_feedback():
    resp = HookResponse(allowed=False, reason="Blocked")
    payload = resp.to_claude_payload()
    assert "feedback" not in payload


def test_hook_response_to_codex_allow():
    resp = HookResponse(allowed=True, reason="ok")
    assert resp.to_codex_payload() == {"allowed": True}


def test_hook_response_to_codex_block():
    resp = HookResponse(allowed=False, reason="Dangerous", feedback="Be careful")
    payload = resp.to_codex_payload()
    assert payload["allowed"] is False
    assert "Dangerous" in payload["message"]
    assert payload["suggestion"] == "Be careful"


# ---------------------------------------------------------------------------
# run_hook
# ---------------------------------------------------------------------------


def test_run_hook_allows_safe_shell(gate):
    req = HookRequest(tool_name="Bash", arguments={"command": "git status"})
    resp = run_hook(gate, req)
    assert resp.allowed


def test_run_hook_blocks_dangerous_shell(gate):
    req = HookRequest(tool_name="Bash", arguments={"command": "rm -rf /"})
    resp = run_hook(gate, req)
    assert not resp.allowed
    assert resp.record is not None
    assert resp.record.verdict.decision is Decision.BLOCK


def test_run_hook_blocks_secret_file_read(gate):
    req = HookRequest(tool_name="Read", arguments={"path": "/home/user/.env"})
    resp = run_hook(gate, req)
    assert not resp.allowed


def test_run_hook_monitor_mode_allows_despite_block(monitor_gate):
    req = HookRequest(tool_name="Bash", arguments={"command": "rm -rf /"})
    resp = run_hook(monitor_gate, req)
    # In monitor mode the gate still records BLOCK but sets proceed=True.
    assert resp.allowed
    assert resp.record is not None
    assert resp.record.verdict.decision is Decision.BLOCK
