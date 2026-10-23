"""Core data models shared by every module (proxy, rules, judge, audit)."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolKind(str, Enum):
    """The category of action an agent is attempting."""

    SHELL = "shell"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    HTTP = "http"
    SQL = "sql"
    OTHER = "other"


class Decision(str, Enum):
    """Outcome of evaluating a tool call.

    ASK means "no deterministic rule matched": hand off to the AI judge.
    """

    ALLOW = "allow"
    BLOCK = "block"
    ASK = "ask"


class ToolCall(BaseModel):
    """A single action the agent wants to perform. Immutable once created."""

    model_config = ConfigDict(frozen=True)

    kind: ToolKind
    # Primary payload: the shell command, file path, URL, or SQL text.
    target: str = Field(min_length=1)
    # Extra structured arguments (file content, headers, etc.).
    args: dict[str, Any] = Field(default_factory=dict)
    # Which agent / session produced the call (used by the audit log).
    agent_id: str = "unknown"


class Verdict(BaseModel):
    """The decision plus the reasoning, returned by rules and by the judge."""

    model_config = ConfigDict(frozen=True)

    decision: Decision
    reason: str
    # Plain-English guidance the agent can act on.
    feedback: str | None = None
    # Who decided: "rule:<id>", "judge", or "default".
    source: str = "default"
