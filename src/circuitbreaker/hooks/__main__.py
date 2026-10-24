"""CLI entry point for the agent hook front-end.

    python -m circuitbreaker.hooks --agent claude_code [--rules FILE] [--mode enforce|monitor] [-v]
    python -m circuitbreaker.hooks --agent codex       [--rules FILE] [--mode enforce|monitor] [-v]

The process is called *per tool invocation* by the agent (not once at startup),
so startup time matters.  We keep imports lean: the rule engine is the only
heavy load and it is cached in-process across repeated calls anyway.
"""

from __future__ import annotations

import argparse
import logging
import sys

from circuitbreaker.audit import AuditLog, AuditLogError, default_audit_path
from circuitbreaker.judge import (
    JudgeConfigurationError,
    add_judge_arguments,
    build_judge,
)
from circuitbreaker.proxy.gate import Gate
from circuitbreaker.rules import RuleEngine, RuleLoadError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m circuitbreaker.hooks",
        description="CircuitBreaker hook front-end for built-in agent tools.",
    )
    parser.add_argument(
        "--agent",
        choices=["claude_code", "codex"],
        required=True,
        help="Which agent's hook protocol to speak.",
    )
    parser.add_argument("--rules", help="Rules YAML (default: built-in rules).")
    parser.add_argument(
        "--audit-log",
        default=str(default_audit_path()),
        metavar="FILE",
        help=f"append decisions to this hash-chained JSONL file (default: {default_audit_path()})",
    )
    add_judge_arguments(parser)
    parser.add_argument(
        "--mode",
        choices=["enforce", "monitor"],
        default="enforce",
        help="monitor logs decisions but allows everything (for tuning rules).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log tool targets to stderr (may contain secrets).",
    )
    ns = parser.parse_args(argv)

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if ns.verbose else logging.INFO,
        format="%(asctime)s circuitbreaker %(levelname)s %(message)s",
    )

    try:
        engine = RuleEngine.from_file(ns.rules) if ns.rules else RuleEngine.from_default()
    except RuleLoadError as exc:
        print(f"circuitbreaker-hook: refusing to start: {exc}", file=sys.stderr)
        return 2
    try:
        judge = build_judge(
            ns.judge_url,
            ns.judge_model,
            ns.judge_api_key_env,
            ns.judge_timeout,
        )
    except JudgeConfigurationError as exc:
        print(f"circuitbreaker-hook: invalid judge configuration: {exc}", file=sys.stderr)
        return 2
    try:
        audit_log = AuditLog(ns.audit_log)
    except AuditLogError as exc:
        print(f"circuitbreaker-hook: cannot initialize audit log: {exc}", file=sys.stderr)
        return 2

    if ns.mode == "monitor":
        logging.getLogger("circuitbreaker").warning("MONITOR MODE: nothing is being blocked")

    gate = Gate(engine, mode=ns.mode, audit_log=audit_log, judge=judge)

    if ns.agent == "claude_code":
        from circuitbreaker.hooks.claude_code import run_stdio

        return run_stdio(gate)

    if ns.agent == "codex":
        from circuitbreaker.hooks.codex import run_stdio

        return run_stdio(gate)

    # Should be unreachable (argparse enforces choices).
    print(f"circuitbreaker-hook: unknown agent: {ns.agent!r}", file=sys.stderr)
    return 2


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
