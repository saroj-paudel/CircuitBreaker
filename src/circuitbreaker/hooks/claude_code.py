"""Claude Code / Antigravity pre-tool-use hook runner.

## How Claude Code hooks work

Claude Code calls an executable before every built-in tool invocation.
The hook receives a JSON object on **stdin** and must write a JSON response
to **stdout**.  Logs / diagnostics go to **stderr**.

### Input payload (stdin)
```json
{
  "tool_name": "Bash",
  "tool_input": {"command": "rm -rf /"}
}
```

### Response payload (stdout)
```json
{"decision": "block", "reason": "...", "feedback": "..."}
```
`{"decision": "allow"}` (or exit 0 with no output) lets the tool proceed.
Any non-zero exit code also blocks the tool.

## Tool-name mapping

| Claude tool       | ToolKind   | Argument key          |
|-------------------|------------|-----------------------|
| Bash              | SHELL      | command               |
| Read              | FILE_READ  | file_path             |
| Write             | FILE_WRITE | file_path             |
| Edit / MultiEdit  | FILE_WRITE | file_path             |
| WebFetch          | HTTP       | url                   |
| WebSearch         | HTTP       | query (treated as URL)|
| NotebookRead      | FILE_READ  | notebook_path         |
| NotebookEdit      | FILE_WRITE | notebook_path         |
| anything else     | OTHER      | (full JSON)           |

## Integration

### ~/.claude/settings.json  (Claude Code)
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python -m circuitbreaker.hooks --agent claude_code"
          }
        ]
      }
    ]
  }
}
```

### Antigravity / AGY
The same hook fires for Antigravity since it shares the Claude Code infrastructure.
Point the hook command at the same entry point.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, TextIO

from circuitbreaker.hooks.protocol import HookRequest, HookResponse, run_hook
from circuitbreaker.proxy.gate import Gate

log = logging.getLogger("circuitbreaker.hooks.claude_code")

# ---------------------------------------------------------------------------
# Tool-name → argument key table
# ---------------------------------------------------------------------------

# Maps the Claude tool name to the argument key that holds the primary target.
# Keys are lower-cased for case-insensitive lookup.
_TOOL_ARG_KEY: dict[str, str] = {
    "bash": "command",
    "read": "file_path",
    "write": "file_path",
    "edit": "file_path",
    "multiedit": "file_path",
    "webfetch": "url",
    "websearch": "query",
    "notebookread": "notebook_path",
    "notebookedit": "notebook_path",
    "task": "description",  # agent sub-task; classify as OTHER via fallback
    "todoread": "__other__",
    "todowrite": "__other__",
    "exit_plan_mode": "__other__",
}

# Tools that write or modify files.
_WRITE_TOOLS = {"write", "edit", "multiedit", "notebookedit"}

# Tools that perform HTTP actions.
_HTTP_TOOLS = {"webfetch", "websearch"}


def _normalise_arguments(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Rewrite Claude's tool_input into a shape the ToolMapper understands.

    The MCP mapper in ``circuitbreaker.proxy.mapping`` inspects argument *keys*
    like ``command``, ``path``, ``url``, etc.  Claude Code uses different keys
    (``file_path``, not ``path``).  We translate here so the mapper does the
    right thing without needing Claude-specific logic inside the core.
    """
    name_lc = tool_name.lower()

    if name_lc in ("bash",):
        # Already has ``command`` — mapper handles it.
        return tool_input

    if name_lc in ("read", "notebookread"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path", "")
        return {"path": path}

    if name_lc in _WRITE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path", "")
        # Use "file_path" — it's in the mapper's _PATH_KEYS; the tool name
        # ("Write", "Edit") triggers _WRITE_HINT, so the mapper classifies it
        # as FILE_WRITE correctly.
        return {"file_path": path}

    if name_lc == "webfetch":
        return {"url": tool_input.get("url", ""), "method": "GET"}

    if name_lc == "websearch":
        # We don't have a URL to check; represent as an HTTP target so HTTP
        # rules can still fire on the query string if needed.
        return {"url": f"search:{tool_input.get('query', '')}", "method": "GET"}

    # Fallback — pass through as-is; the mapper will classify what it can.
    return tool_input


def _parse_input(raw: str) -> tuple[str, dict[str, Any]]:
    """Parse the JSON payload Claude Code sends to the hook on stdin.

    Returns ``(tool_name, tool_input)`` or raises ``ValueError`` on bad input.
    """
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"hook input is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("hook input must be a JSON object")

    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError("hook input missing string 'tool_name'")

    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}

    return tool_name.strip(), tool_input


def process(
    gate: Gate,
    raw_input: str,
    agent_id: str = "claude_code",
) -> HookResponse:
    """Parse Claude's stdin payload, run it through the gate, return a response.

    This is the pure-logic entry point.  ``run_stdio`` wraps it with real I/O.
    """
    try:
        tool_name, tool_input = _parse_input(raw_input)
    except ValueError as exc:
        log.warning("claude_code hook: bad input: %s", exc)
        # Fail closed: if we can't parse the request we block it.
        import time

        from circuitbreaker.models import Decision, Verdict
        from circuitbreaker.proxy.gate import DecisionRecord

        dummy_verdict = Verdict(
            decision=Decision.BLOCK,
            reason=f"CircuitBreaker could not parse the hook input: {exc}",
            source="hook",
        )
        # Build a minimal DecisionRecord so HookResponse is consistent.
        from circuitbreaker.models import ToolCall, ToolKind

        dummy_call = ToolCall(kind=ToolKind.OTHER, target="<unparseable>")
        record = DecisionRecord(
            timestamp=time.time(),
            agent_id=agent_id,
            tool_name="<unknown>",
            calls=(dummy_call,),
            verdict=dummy_verdict,
            proceed=False,
            mode=gate.mode,
        )
        return HookResponse(
            allowed=False,
            reason=dummy_verdict.reason,
            record=record,
        )

    normalised_args = _normalise_arguments(tool_name, tool_input)
    request = HookRequest(
        tool_name=tool_name,
        arguments=normalised_args,
        agent_id=agent_id,
    )
    return run_hook(gate, request)


def run_stdio(
    gate: Gate,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    agent_id: str = "claude_code",
) -> int:
    """Read one JSON payload from *stdin*, write the decision to *stdout*.

    Returns the exit code (0 = allow, 1 = block).  Claude Code also treats
    a non-zero exit code as a block, so we use it as a belt-and-braces signal.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    try:
        raw = stdin.read()
    except (OSError, ValueError) as exc:
        log.error("claude_code hook: cannot read stdin: %s", exc)
        return 1

    response = process(gate, raw, agent_id=agent_id)

    try:
        payload = response.to_claude_payload()
        json.dump(payload, stdout, ensure_ascii=False)
        stdout.write("\n")
        stdout.flush()
    except (OSError, ValueError) as exc:
        log.error("claude_code hook: cannot write stdout: %s", exc)
        return 1

    return 0 if response.allowed else 1
