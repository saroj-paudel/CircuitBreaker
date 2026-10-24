"""Map an MCP `tools/call` (tool name + arguments) onto one or more `ToolCall`s.

MCP tools are arbitrary, so we infer what an action *is* from its argument names
(`command` -> shell, `path` -> file, `url` -> http, `query` -> sql). Anything we
cannot classify becomes a `kind=other` call, which the ruleset default (ask) then
handles, so unknown tools fail closed instead of slipping through.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Any

from circuitbreaker.models import ToolCall, ToolKind

_SHELL_KEYS = {"command", "cmd", "script", "shell_command", "commandline", "command_line"}
_ARGV_KEYS = {"args", "arguments", "argv"}
_SQL_KEYS = {"query", "sql", "statement"}
_URL_KEYS = {"url", "uri"}
_METHOD_KEYS = {"method", "http_method"}
_PATH_KEYS = {
    "path", "file_path", "filepath", "filename", "file", "directory", "dir", "folder",
    "paths", "file_paths", "files",
}  # fmt: skip
_SRC_KEYS = {"source", "src", "from", "old_path", "oldpath"}
_DEST_KEYS = {"destination", "dest", "dst", "to", "new_path", "newpath", "output", "output_path"}

# Tool names that modify things. Unknown names are treated as writes (stricter).
_WRITE_HINT = re.compile(
    r"write|edit|create|delete|remove|move|rename|save|append|put|patch|update|mkdir|touch|"
    r"set|upload|copy|replace|insert|truncate|chmod|chown|run|exec|rm|mv|cp",
    re.IGNORECASE,
)
_READ_HINT = re.compile(
    r"read|get|list|view|search|find|stat|cat|head|tail|tree|info", re.IGNORECASE
)
_MOVE_HINT = re.compile(r"move|rename|delete|remove|rm|mv", re.IGNORECASE)


@dataclass(frozen=True)
class ToolOverride:
    """Explicit mapping for a tool the heuristics cannot classify."""

    kind: ToolKind
    arg: str  # which argument holds the command / path / url / sql


@dataclass(frozen=True)
class MappedCall:
    call: ToolCall
    # Advisory calls only count when a rule explicitly matched them. This is how a rule
    # such as `kind: other, regex: '^tool:delete_'` can block a tool by name without the
    # ruleset default (ask) hitting every tool.
    advisory: bool = False


def _as_strings(value: Any) -> list[str] | None:
    """A string or a list of strings -> non-empty strings. Anything else -> None (bad shape)."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [v for v in value if v.strip()]
    return None


def other_target(tool_name: str, arguments: Any) -> str:
    try:
        payload = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(arguments)
    return f"tool:{tool_name} {payload}"


class ToolMapper:
    def __init__(self, overrides: dict[str, ToolOverride] | None = None) -> None:
        self.overrides = overrides or {}

    def map(self, tool_name: str, arguments: Any, agent_id: str = "unknown") -> list[MappedCall]:
        def mk(kind: ToolKind, target: str, **args: Any) -> ToolCall:
            return ToolCall(kind=kind, target=target, args=args, agent_id=agent_id)

        fallback = MappedCall(mk(ToolKind.OTHER, other_target(tool_name, arguments)))
        if not isinstance(arguments, dict):
            return [fallback]

        calls: list[ToolCall] = []
        bad_shape = False

        override = self.overrides.get(tool_name)
        if override is not None:
            values = _as_strings(arguments.get(override.arg))
            if values is None:
                bad_shape = True
            else:
                calls += [mk(override.kind, v) for v in values]
        else:
            extra_argv: list[str] = []
            for key, value in arguments.items():
                if str(key).lower() in _ARGV_KEYS:
                    parts = _as_strings(value)
                    if parts is None:
                        bad_shape = True
                    else:
                        extra_argv += parts

            method = "GET"
            for key, value in arguments.items():
                if str(key).lower() in _METHOD_KEYS and isinstance(value, str):
                    method = value.upper()

            is_write_tool = bool(_WRITE_HINT.search(tool_name)) or not _READ_HINT.search(tool_name)
            moves = bool(_MOVE_HINT.search(tool_name))

            for key, value in arguments.items():
                k = str(key).lower()
                values = _as_strings(value)
                if k in _SHELL_KEYS:
                    if values is None:
                        bad_shape = True
                    elif isinstance(value, list):  # argv-style command
                        calls.append(mk(ToolKind.SHELL, shlex.join([*values, *extra_argv])))
                    else:
                        tail = f" {shlex.join(extra_argv)}" if extra_argv else ""
                        calls += [mk(ToolKind.SHELL, v + tail) for v in values]
                elif k in _SQL_KEYS:
                    if values is None:
                        bad_shape = True
                    else:
                        calls += [mk(ToolKind.SQL, v) for v in values]
                elif k in _URL_KEYS:
                    if values is None:
                        bad_shape = True
                    else:
                        calls += [mk(ToolKind.HTTP, v, method=method) for v in values]
                elif k in _PATH_KEYS or k in _SRC_KEYS or k in _DEST_KEYS:
                    if values is None:
                        bad_shape = True
                        continue
                    if k in _DEST_KEYS:
                        kind = ToolKind.FILE_WRITE
                    elif k in _SRC_KEYS:
                        kind = ToolKind.FILE_WRITE if moves else ToolKind.FILE_READ
                    else:
                        kind = ToolKind.FILE_WRITE if is_write_tool else ToolKind.FILE_READ
                    calls += [mk(kind, v) for v in values]

        if bad_shape or not calls:
            return [*(MappedCall(c) for c in calls), fallback]
        return [
            *(MappedCall(c) for c in calls),
            MappedCall(mk(ToolKind.OTHER, f"tool:{tool_name}"), advisory=True),
        ]
