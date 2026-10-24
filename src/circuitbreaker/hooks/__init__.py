"""Agent-hook front-end for CircuitBreaker.

Re-exports the public surface so callers can write::

    from circuitbreaker.hooks import ClaudeCodeHook, HookRequest, HookResponse, run_hook
"""

from circuitbreaker.hooks.claude_code import process as claude_code_process
from circuitbreaker.hooks.claude_code import run_stdio as claude_code_hook
from circuitbreaker.hooks.codex import process as codex_process
from circuitbreaker.hooks.codex import run_stdio as codex_hook
from circuitbreaker.hooks.protocol import HookRequest, HookResponse, run_hook

__all__ = [
    "HookRequest",
    "HookResponse",
    "run_hook",
    "claude_code_hook",
    "claude_code_process",
    "codex_hook",
    "codex_process",
]
