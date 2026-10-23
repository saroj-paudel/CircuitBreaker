"""Structured errors returned to the agent when an action is blocked."""

from __future__ import annotations

from typing import Any

from circuitbreaker.models import Decision, ToolCall, Verdict


class BlockedError(Exception):
    """Raised internally when a tool call is denied."""

    def __init__(self, call: ToolCall, verdict: Verdict) -> None:
        super().__init__(verdict.reason)
        self.call = call
        self.verdict = verdict

    def to_agent_payload(self) -> dict[str, Any]:
        """JSON-safe error the agent receives, guiding it to a safer path."""
        return {
            "error": "blocked_by_circuitbreaker",
            "decision": Decision.BLOCK.value,
            "reason": self.verdict.reason,
            "suggested_alternative": self.verdict.feedback,
            "source": self.verdict.source,
        }
