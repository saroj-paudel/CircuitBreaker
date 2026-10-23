"""Try the engine: python -m circuitbreaker.rules shell "rm -rf /" """

from __future__ import annotations

import argparse
import json

from circuitbreaker.models import ToolCall, ToolKind
from circuitbreaker.rules.engine import RuleEngine


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m circuitbreaker.rules")
    parser.add_argument("kind", choices=[k.value for k in ToolKind])
    parser.add_argument("target")
    parser.add_argument("--rules", help="path to a rules YAML (default: built-in)")
    ns = parser.parse_args()
    engine = RuleEngine.from_file(ns.rules) if ns.rules else RuleEngine.from_default()
    verdict = engine.evaluate(ToolCall(kind=ToolKind(ns.kind), target=ns.target))
    print(json.dumps(verdict.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
