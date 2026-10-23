"""Load and validate rule files."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import yaml
from pydantic import ValidationError

from circuitbreaker.rules.schema import RuleSet


class RuleLoadError(ValueError):
    """The rule file is unreadable or invalid. Callers should refuse to start."""


def parse_ruleset(text: str, source: str = "<string>") -> RuleSet:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RuleLoadError(f"{source}: invalid YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise RuleLoadError(f"{source}: top level must be a mapping")
    # Keys starting with "x-" are free-form (YAML anchors for shared lists).
    data = {k: v for k, v in data.items() if not str(k).startswith("x-")}
    try:
        return RuleSet.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise RuleLoadError(f"{source}: {problems}") from exc


def load_ruleset(path: str | Path) -> RuleSet:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuleLoadError(f"cannot read {path}: {exc}") from exc
    return parse_ruleset(text, source=str(path))


def load_default_ruleset() -> RuleSet:
    text = files("circuitbreaker.rules").joinpath("default.yaml").read_text(encoding="utf-8")
    return parse_ruleset(text, source="default.yaml")
