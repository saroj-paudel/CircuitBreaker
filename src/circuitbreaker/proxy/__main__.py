"""Run CircuitBreaker in front of an MCP server.

    python -m circuitbreaker.proxy [--rules FILE] [--mode enforce|monitor] [-v] -- <server command>
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from circuitbreaker.audit import AuditLog, AuditLogError, default_audit_path
from circuitbreaker.judge import (
    JudgeConfigurationError,
    add_judge_arguments,
    build_judge,
)
from circuitbreaker.proxy.gate import Gate
from circuitbreaker.proxy.interceptor import Interceptor
from circuitbreaker.proxy.server import Proxy, ProxyError
from circuitbreaker.rules import RuleEngine, RuleLoadError


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    own, upstream = (argv[: argv.index("--")], argv[argv.index("--") + 1 :]) if "--" in argv else (argv, [])  # noqa: E501

    parser = argparse.ArgumentParser(
        prog="circuitbreaker run",
        usage="%(prog)s [--rules FILE] [--mode {enforce,monitor}] [-v] -- <server command ...>",
        description="Security proxy between an AI agent and an MCP server.",
    )
    parser.add_argument("--rules", help="rules YAML (default: built-in rules)")
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
        help="monitor logs what would be blocked but forwards everything (for tuning rules)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log tool targets (may contain secrets)")  # noqa: E501
    ns = parser.parse_args(own)
    if not upstream:
        parser.error("no server command given; put it after '--'")

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if ns.verbose else logging.INFO,
        format="%(asctime)s circuitbreaker %(levelname)s %(message)s",
    )
    try:
        engine = RuleEngine.from_file(ns.rules) if ns.rules else RuleEngine.from_default()
        judge = build_judge(
            ns.judge_url,
            ns.judge_model,
            ns.judge_api_key_env,
            ns.judge_timeout,
        )
        audit_log = AuditLog(ns.audit_log)
    except RuleLoadError as exc:
        print(f"circuitbreaker: refusing to start: {exc}", file=sys.stderr)
        return 2
    except AuditLogError as exc:
        print(f"circuitbreaker: cannot initialize audit log: {exc}", file=sys.stderr)
        return 2
    except JudgeConfigurationError as exc:
        print(f"circuitbreaker: invalid judge configuration: {exc}", file=sys.stderr)
        return 2

    if ns.mode == "monitor":
        logging.getLogger("circuitbreaker").warning("MONITOR MODE: nothing is being blocked")
    try:
        return Proxy(
            Interceptor(Gate(engine, mode=ns.mode, audit_log=audit_log, judge=judge)), upstream
        ).run()
    except ProxyError as exc:
        print(f"circuitbreaker: {exc}", file=sys.stderr)
        return 2


def run() -> None:
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # A daemon thread may still be blocked reading stdin; exit without waiting for it.
    os._exit(code)


if __name__ == "__main__":
    run()
