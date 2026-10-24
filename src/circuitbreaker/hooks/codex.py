"""OpenAI Codex pre-tool hook runner.

## How Codex hooks work

Codex calls a hook script before executing shell commands or file operations.
The hook receives the tool call as a JSON object — either via the
``CODEX_TOOL_CALL`` environment variable **or** on stdin (depending on the
Codex version / configuration).

### Input payload
```json
{
  "type": "shell",
  "command": "rm -rf /"
}
```
or for file operations:
```json
{
  "type": "file_write",
  "path": "/etc/passwd",
  "content": "..."
}
```

### Response
* **Exit code 0** → allow the action.
* **Non-zero exit code** → block; the text written to **stderr** is shown to
  the model as the reason for blocking.

Optionally the hook may also write a JSON object to **stdout**:
```json
{"allowed": false, "message": "...", "suggestion": "..."}
```

## codex.yaml integration
```yaml
shell_hooks:
  pre:
    - command: python -m circuitbreaker.hooks --agent codex
```

## Tool-type mapping

| Codex ``type``  | ToolKind   |
|-----------------|------------|
| shell / exec    | SHELL      |
| file_read       | FILE_READ  |
| file_write      | FILE_WRITE |
| http / fetch    | HTTP       |
| *               | OTHER      |
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, TextIO

from circuitbreaker.hooks.protocol import HookRequest, HookResponse, run_hook
from circuitbreaker.proxy.gate import Gate

log = logging.getLogger("circuitbreaker.hooks.codex")

_TYPE_TO_ARG: dict[str, tuple[str, str]] = {
    # codex type  → (argument key the mapper expects, fallback value key in payload)
    "shell": ("command", "command"),
    "exec": ("command", "command"),
    "file_read": ("path", "path"),
    "file_write": ("path", "path"),
    "http": ("url", "url"),
    "fetch": ("url", "url"),
}


def _normalise_arguments(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract tool_name and mapper-friendly arguments from a Codex payload.

    Returns ``(tool_name, arguments)``.
    """
    tool_type = str(payload.get("type", "other")).lower()
    tool_name = f"codex_{tool_type}"

    mapping = _TYPE_TO_ARG.get(tool_type)
    if mapping is None:
        # Unknown type — pass through raw; the mapper will classify it as OTHER.
        return tool_name, dict(payload)

    arg_key, payload_key = mapping
    target = payload.get(payload_key, payload.get("command", payload.get("path", "")))

    args: dict[str, Any] = {arg_key: target}

    # Forward HTTP method if present.
    if tool_type in ("http", "fetch") and "method" in payload:
        args["method"] = payload["method"]

    return tool_name, args


def _read_payload(stdin: TextIO) -> dict[str, Any]:
    """Read the JSON payload from the ``CODEX_TOOL_CALL`` env var or stdin."""
    env_json = os.environ.get("CODEX_TOOL_CALL", "").strip()
    raw = env_json if env_json else stdin.read().strip()

    if not raw:
        raise ValueError("no tool-call payload found (set CODEX_TOOL_CALL or pipe JSON to stdin)")

    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"Codex hook input is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Codex hook input must be a JSON object")

    return payload


def process(
    gate: Gate,
    payload: dict[str, Any],
    agent_id: str = "codex",
) -> HookResponse:
    """Apply the gate to a parsed Codex payload.  Pure logic, no I/O."""
    tool_name, arguments = _normalise_arguments(payload)
    request = HookRequest(tool_name=tool_name, arguments=arguments, agent_id=agent_id)
    return run_hook(gate, request)


def run_stdio(
    gate: Gate,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    agent_id: str = "codex",
) -> int:
    """Read from env/stdin, write decision to stdout+stderr, return exit code.

    Exit 0 = allow.  Exit 1 = block (Codex checks the exit code).
    The block reason is written to *stderr* so the model sees it.
    The JSON response is written to *stdout* for Codex versions that read it.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    try:
        payload = _read_payload(stdin)
    except ValueError as exc:
        log.error("codex hook: %s", exc)
        print(str(exc), file=stderr)
        return 1

    response = process(gate, payload, agent_id=agent_id)

    # Write the JSON response to stdout for Codex versions that check it.
    try:
        json.dump(response.to_codex_payload(), stdout, ensure_ascii=False)
        stdout.write("\n")
        stdout.flush()
    except (OSError, ValueError) as exc:
        log.error("codex hook: cannot write stdout: %s", exc)

    if not response.allowed:
        # Codex shows stderr to the model as the blocking reason.
        lines = [f"CircuitBreaker blocked this action: {response.reason}"]
        if response.feedback:
            lines.append(f"Suggestion: {response.feedback}")
        print("\n".join(lines), file=stderr)
        return 1

    return 0
