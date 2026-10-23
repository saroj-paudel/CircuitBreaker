"""CircuitBreaker: a security proxy for AI coding agents."""

from circuitbreaker.errors import BlockedError
from circuitbreaker.models import Decision, ToolCall, ToolKind, Verdict

__all__ = ["BlockedError", "Decision", "ToolCall", "ToolKind", "Verdict"]
__version__ = "0.0.1"
