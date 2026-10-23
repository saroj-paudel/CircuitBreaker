import pytest
from pydantic import ValidationError

from circuitbreaker import BlockedError, Decision, ToolCall, ToolKind, Verdict


def test_toolcall_is_immutable():
    call = ToolCall(kind=ToolKind.SHELL, target="ls -la")
    with pytest.raises(ValidationError):
        call.target = "rm -rf /"


def test_toolcall_rejects_empty_target():
    with pytest.raises(ValidationError):
        ToolCall(kind=ToolKind.SHELL, target="")


def test_toolcall_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        ToolCall(kind="telepathy", target="x")


def test_blocked_error_payload():
    call = ToolCall(kind=ToolKind.SHELL, target="rm -rf /")
    verdict = Verdict(
        decision=Decision.BLOCK,
        reason="Recursive delete of root",
        feedback="Delete specific files inside the project directory instead.",
        source="rule:no-rm-rf",
    )
    payload = BlockedError(call, verdict).to_agent_payload()
    assert payload["error"] == "blocked_by_circuitbreaker"
    assert payload["decision"] == "block"
    assert payload["suggested_alternative"].startswith("Delete specific")
    assert payload["source"] == "rule:no-rm-rf"
