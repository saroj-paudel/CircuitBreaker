"""Try the engine: python -m circuitbreaker.rules shell "rm -rf /" """

from __future__ import annotations

import argparse
import json
import sys

from circuitbreaker.models import ToolCall, ToolKind
from circuitbreaker.rules.engine import RuleEngine
from circuitbreaker.rules.loader import RuleLoadError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="circuitbreaker rules")
    parser.add_argument("kind", choices=[k.value for k in ToolKind])
    parser.add_argument("target")
    parser.add_argument("--rules", help="path to a rules YAML (default: built-in)")
    ns = parser.parse_args(argv)
    try:
        engine = RuleEngine.from_file(ns.rules) if ns.rules else RuleEngine.from_default()
    except RuleLoadError as exc:
        print(f"circuitbreaker: refusing to evaluate with invalid rules: {exc}", file=sys.stderr)
        return 2
    verdict = engine.evaluate(ToolCall(kind=ToolKind(ns.kind), target=ns.target))
    print(json.dumps(verdict.model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
