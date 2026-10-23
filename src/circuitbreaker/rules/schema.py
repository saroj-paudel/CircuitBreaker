"""Pydantic schema for YAML rule files. Strict: unknown keys are errors."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from circuitbreaker.models import Decision, ToolKind


class Match(BaseModel):
    """Conditions of a rule. Every field you set must hold (logical AND)."""

    model_config = ConfigDict(extra="forbid")

    # shell
    program_any: list[str] | None = None  # program basename globs, e.g. [rm, mkfs*]
    flags_all: list[str] | None = None  # all of these flags present (-rf -> r, f)
    flags_any: list[str] | None = None
    subcommand_any: list[str] | None = None  # first positional arg, e.g. git [push]
    args_any: list[str] | None = None  # path globs on positional args; "!x" excludes
    tokens_any: list[str] | None = None  # globs on every raw token (find -delete)
    # file_read / file_write
    path_any: list[str] | None = None
    # http
    host_any: list[str] | None = None
    method_any: list[str] | None = None
    # shell, http, sql, other: case-insensitive regex
    regex: str | None = None

    @field_validator("regex")
    @classmethod
    def _valid_regex(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from exc
        return value

    @model_validator(mode="after")
    def _not_empty(self) -> Match:
        if not self.used_fields():
            raise ValueError("match must set at least one condition")
        return self

    def used_fields(self) -> set[str]:
        return {name for name, value in self if value is not None}


_ALLOWED_FIELDS: dict[ToolKind, set[str]] = {
    ToolKind.SHELL: {
        "program_any", "flags_all", "flags_any", "subcommand_any",
        "args_any", "tokens_any", "regex",
    },  # fmt: skip
    ToolKind.FILE_READ: {"path_any"},
    ToolKind.FILE_WRITE: {"path_any"},
    ToolKind.HTTP: {"host_any", "method_any", "regex"},
    ToolKind.SQL: {"regex"},
    ToolKind.OTHER: {"regex"},
}


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    description: str = ""
    kind: list[ToolKind] = Field(min_length=1)
    action: Decision
    match: Match
    feedback: str | None = None  # guidance returned to the agent when it is blocked

    @field_validator("kind", mode="before")
    @classmethod
    def _scalar_kind(cls, value: Any) -> Any:
        return [value] if isinstance(value, str) else value

    @model_validator(mode="after")
    def _fields_fit_kind(self) -> Rule:
        used = self.match.used_fields()
        for kind in self.kind:
            extra = used - _ALLOWED_FIELDS[kind]
            if extra:
                raise ValueError(
                    f"rule '{self.id}': {sorted(extra)} cannot be used with kind '{kind.value}'"
                )
        return self


class RuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    default: Decision = Decision.ASK  # used when no rule matches
    rules: list[Rule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> RuleSet:
        seen: set[str] = set()
        for rule in self.rules:
            if rule.id in seen:
                raise ValueError(f"duplicate rule id '{rule.id}'")
            seen.add(rule.id)
        return self
