"""Agent-hook protocol: the shared request/response contract.

Every agent-specific hook runner (Claude Code, Codex, …) converts its own
wire format into a ``HookRequest`` and then calls ``run_hook``, which returns
a ``HookResponse``.  No I/O happens here — all transports are in the
agent-specific modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from circuitbreaker.models import Decision
from circuitbreaker.proxy.gate import DecisionRecord, Gate


@dataclass
class HookRequest:
    """Normalised representation of one agent tool invocation."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    agent_id: str = "unknown"


@dataclass
class HookResponse:
    """What the hook runner returns to the calling agent."""

    allowed: bool
    reason: str
    feedback: str | None = None
    # The full gate record is kept so callers (audit log, tests) can inspect it.
    record: DecisionRecord | None = None

    # Convenience -------------------------------------------------------

    def to_claude_payload(self) -> dict[str, Any]:
        """JSON payload for Claude Code / Antigravity ``pre_tool_use`` hooks.

        Claude reads ``decision`` and ``reason`` from the hook's stdout.
        An optional ``feedback`` surfaces in the model's context.
        """
        if self.allowed:
            return {"decision": "allow"}
        payload: dict[str, Any] = {
            "decision": "block",
            "reason": self.reason,
        }
        if self.feedback:
            payload["feedback"] = self.feedback
        return payload

    def to_codex_payload(self) -> dict[str, Any]:
        """JSON payload for the Codex ``shell_hooks.pre`` hook."""
        if self.allowed:
            return {"allowed": True}
        payload: dict[str, Any] = {
            "allowed": False,
            "message": self.reason,
        }
        if self.feedback:
            payload["suggestion"] = self.feedback
        return payload


# ---------------------------------------------------------------------------


def run_hook(gate: Gate, request: HookRequest) -> HookResponse:
    """Feed a ``HookRequest`` through the ``Gate`` and return a ``HookResponse``.

    This is the only place where ``Gate`` is called from the hooks layer.
    All agent-specific modules call this function — they never touch ``Gate``
    directly.
    """
    record = gate.check(request.tool_name, request.arguments, request.agent_id)
    allowed = record.verdict.decision is Decision.ALLOW or record.mode == "monitor"
    return HookResponse(
        allowed=allowed,
        reason=record.verdict.reason,
        feedback=record.verdict.feedback,
        record=record,
    )
