"""Pure JSON-RPC interception: one client line in, one action out. No I/O.

Fail-closed rules: a line we cannot parse is never forwarded, a malformed
`tools/call` is never forwarded, and an internal error never forwards.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from circuitbreaker.errors import BlockedError
from circuitbreaker.proxy.gate import Gate

log = logging.getLogger("circuitbreaker.interceptor")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


@dataclass(frozen=True)
class Forward:
    """Send these exact bytes to the upstream server."""

    data: bytes


@dataclass(frozen=True)
class Reply:
    """Answer the client directly. The upstream server never sees the request."""

    data: bytes


@dataclass(frozen=True)
class Drop:
    """Discard silently."""


Action = Forward | Reply | Drop


def _encode(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def rpc_error(msg_id: Any, code: int, message: str) -> bytes:
    return _encode({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _is_tool_call(msg: Any) -> bool:
    return isinstance(msg, dict) and msg.get("method") == "tools/call"


class Interceptor:
    def __init__(self, gate: Gate) -> None:
        self.gate = gate
        self.agent_id = "unknown"

    def process(self, line: bytes) -> Action:
        try:
            return self._process(line)
        except Exception:
            log.exception("interceptor error; request not forwarded")
            return Reply(rpc_error(self._safe_id(line), INTERNAL_ERROR, "CircuitBreaker internal error; request not forwarded."))  # noqa: E501

    # ------------------------------------------------------------------

    @staticmethod
    def _safe_id(line: bytes) -> Any:
        try:
            msg = json.loads(line)
            return msg.get("id") if isinstance(msg, dict) else None
        except Exception:
            return None

    def _process(self, line: bytes) -> Action:
        if not line.strip():
            return Drop()
        try:
            msg = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return Reply(rpc_error(None, PARSE_ERROR, "Parse error: request was not forwarded."))

        if isinstance(msg, list):
            if any(_is_tool_call(m) for m in msg):
                return Reply(
                    rpc_error(None, INVALID_REQUEST, "Batched tools/call is not supported by CircuitBreaker.")  # noqa: E501
                )
            return Forward(line)

        if not isinstance(msg, dict):
            return Forward(line)

        method = msg.get("method")
        if method == "initialize":
            self._remember_client(msg)
            return Forward(line)
        if method == "tools/call":
            return self._tool_call(msg, line)
        return Forward(line)

    def _remember_client(self, msg: dict[str, Any]) -> None:
        params = msg.get("params")
        info = params.get("clientInfo") if isinstance(params, dict) else None
        name = info.get("name") if isinstance(info, dict) else None
        if isinstance(name, str) and name.strip():
            self.agent_id = name.strip()[:100]

    def _tool_call(self, msg: dict[str, Any], line: bytes) -> Action:
        if "id" not in msg:  # a notification cannot be answered, so it must not run
            log.warning("dropped tools/call sent as a notification (no id)")
            return Drop()
        msg_id = msg["id"]
        params = msg.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        if not isinstance(name, str) or not name:
            return Reply(rpc_error(msg_id, INVALID_PARAMS, "tools/call requires a string 'name'."))

        record = self.gate.check(name, params.get("arguments", {}), self.agent_id)
        if record.proceed:
            return Forward(line)  # the original bytes, exactly as analysed
        return Reply(self._blocked_response(msg_id, record))

    @staticmethod
    def _blocked_response(msg_id: Any, record: Any) -> bytes:
        payload = BlockedError(record.calls[0], record.verdict).to_agent_payload()
        lines = [f"Blocked by CircuitBreaker: {record.verdict.reason}"]
        if record.verdict.feedback:
            lines.append(f"Suggested alternative: {record.verdict.feedback}")
        lines.append(json.dumps(payload, ensure_ascii=False))
        # Tool failures are reported inside the result (isError) so the model can read
        # them and adapt, rather than as a protocol error it never sees.
        result = {"content": [{"type": "text", "text": "\n".join(lines)}], "isError": True}
        return _encode({"jsonrpc": "2.0", "id": msg_id, "result": result})
