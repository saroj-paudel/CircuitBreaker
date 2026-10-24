"""Unified command-line entry point for CircuitBreaker."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from circuitbreaker import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="circuitbreaker",
        description="Local-first security controls for AI coding agents.",
        epilog=(
            "Examples:\n"
            "  circuitbreaker run --mode monitor -- <MCP server command>\n"
            "  circuitbreaker hook --agent claude_code\n"
            "  circuitbreaker audit verify\n"
            "  circuitbreaker rules shell \"rm -rf /\""
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["run", "hook", "audit", "rules"],
        help="run an MCP server through the proxy, run an agent hook, inspect audit, or test rules",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _parser().print_help()
        return 0
    if args[0] in ("-h", "--help", "--version"):
        _parser().parse_args(args)
        return 0

    command, command_args = args[0], args[1:]
    if command == "run":
        from circuitbreaker.proxy.__main__ import main as proxy_main

        return proxy_main(command_args)
    if command == "hook":
        from circuitbreaker.hooks.__main__ import main as hook_main

        return hook_main(command_args)
    if command == "audit":
        from circuitbreaker.audit import main as audit_main

        return audit_main(command_args)
    if command == "rules":
        from circuitbreaker.rules.__main__ import main as rules_main

        return rules_main(command_args)

    _parser().error(f"unknown command: {command}")
    return 2


def run() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    run()
