import pytest

from circuitbreaker.models import Decision
from circuitbreaker.rules import RuleLoadError, load_default_ruleset, parse_ruleset


def test_default_ruleset_is_valid():
    rs = load_default_ruleset()
    assert rs.default is Decision.ASK
    assert len(rs.rules) > 20


def test_scalar_kind_accepted_and_x_keys_ignored():
    rs = parse_ruleset(
        """
x-anything: [1, 2]
rules:
  - id: a
    kind: shell
    action: block
    match: {program_any: [rm]}
"""
    )
    assert rs.rules[0].kind[0].value == "shell"


@pytest.mark.parametrize(
    ("yaml_text", "needle"),
    [
        ("rules: [{id: a, kind: shell, action: block, match: {program_any: [x]}},"
         " {id: a, kind: shell, action: block, match: {program_any: [y]}}]", "duplicate"),
        ("rules: [{id: a, kind: shell, action: block, match: {bogus: 1}}]", "bogus"),
        ("rules: [{id: a, kind: shell, action: block, match: {regex: '('}}]", "regex"),
        ("rules: [{id: a, kind: sql, action: block, match: {program_any: [x]}}]", "cannot be used"),
        ("rules: [{id: a, kind: shell, action: block, match: {}}]", "at least one"),
        ("rules: [{id: 'Bad Id', kind: shell, action: block, match: {regex: x}}]", "id"),
        ("rules: [{id: a, kind: shell, action: explode, match: {regex: x}}]", "action"),
        ("version: 2", "version"),
        ("- just a list", "mapping"),
        ("a: [unclosed", "YAML"),
    ],
)
def test_invalid_rulesets_are_rejected(yaml_text, needle):
    with pytest.raises(RuleLoadError, match=needle):
        parse_ruleset(yaml_text)
