"""CircuitBreaker: a security proxy for AI coding agents."""

from circuitbreaker.errors import BlockedError
from circuitbreaker.models import Decision, ToolCall, ToolKind, Verdict
from circuitbreaker.rules import RuleEngine

__all__ = ["BlockedError", "Decision", "RuleEngine", "ToolCall", "ToolKind", "Verdict"]
__version__ = "0.1.0"
