import pytest

from circuitbreaker.judge import JsonRiskJudge
from circuitbreaker.models import Decision, ToolCall, Verdict
from circuitbreaker.proxy import Gate
from circuitbreaker.rules import RuleEngine, parse_ruleset

A, B, K = Decision.ALLOW, Decision.BLOCK, Decision.ASK


@pytest.fixture(scope="module")
def engine():
    return RuleEngine.from_default()


class StubJudge:
    def __init__(self, verdict=None, error=None):
        self.verdict, self.error, self.calls = verdict, error, []

    def judge(self, call: ToolCall, rule_verdict: Verdict) -> Verdict:
        self.calls.append(call)
        if self.error:
            raise self.error
        return self.verdict


class StaticLLMClient:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def complete(self, *, system_prompt, user_prompt):
        self.calls += 1
        return self.response


def verdict(decision, reason="because"):
    return Verdict(decision=decision, reason=reason, source="judge")


def test_allowed_call_proceeds(engine):
    rec = Gate(engine).check("run_command", {"command": "ls"})
    assert rec.verdict.decision is A and rec.proceed


def test_blocked_call_does_not_proceed(engine):
    rec = Gate(engine).check("run_command", {"command": "rm -rf /"})
    assert rec.verdict.decision is B and not rec.proceed
    assert rec.verdict.source == "rule:shell-rm-dangerous-target"


def test_ask_without_judge_is_denied_with_guidance(engine):
    rec = Gate(engine).check("run_command", {"command": "pip install requests"})
    assert rec.verdict.decision is B and not rec.proceed
    assert "approval" in rec.verdict.reason.lower()
    assert rec.verdict.feedback


def test_unknown_tool_fails_closed(engine):
    rec = Gate(engine).check("mystery_tool", {"anything": "x"})
    assert not rec.proceed


def test_one_blocked_part_blocks_the_whole_call(engine):
    rec = Gate(engine).check("copy", {"source": "README.md", "destination": ".env"})
    assert rec.verdict.decision is B


def test_monitor_mode_logs_block_but_forwards(engine):
    rec = Gate(engine, mode="monitor").check("run_command", {"command": "rm -rf /"})
    assert rec.verdict.decision is B and rec.proceed and rec.mode == "monitor"


def test_judge_can_allow_an_ask(engine):
    judge = StubJudge(verdict(A))
    rec = Gate(engine, judge=judge).check("run_command", {"command": "pip install requests"})
    assert rec.verdict.decision is A and rec.proceed and len(judge.calls) == 1
    assert rec.verdict.source == "judge"


def test_judge_is_never_consulted_for_hard_blocks_or_allows(engine):
    judge = StubJudge(verdict(A))
    gate = Gate(engine, judge=judge)
    assert not gate.check("run_command", {"command": "rm -rf /"}).proceed
    assert gate.check("run_command", {"command": "ls"}).proceed
    assert judge.calls == []


def test_json_judge_is_only_called_for_ambiguous_actions(engine):
    client = StaticLLMClient(
        '{"decision":"allow","reason":"accepted","feedback":null}'
    )
    judge = JsonRiskJudge(client)
    gate = Gate(engine, judge=judge)

    assert gate.check("run_command", {"command": "rm -rf /"}).verdict.decision is B
    assert gate.check("run_command", {"command": "ls"}).verdict.decision is A
    assert client.calls == 0
    assert gate.check("run_command", {"command": "pip install x"}).verdict.decision is A
    assert client.calls == 1


def test_invalid_json_judge_response_fails_closed(engine):
    client = StaticLLMClient("not json")
    record = Gate(engine, judge=JsonRiskJudge(client)).check(
        "run_command", {"command": "pip install x"}
    )
    assert record.verdict.decision is B
    assert record.verdict.source == "judge"
    assert not record.proceed


@pytest.mark.parametrize(
    "judge",
    [StubJudge(error=RuntimeError("down")), StubJudge(verdict(K)), StubJudge(verdict(B))],
)
def test_judge_failure_or_doubt_fails_closed(engine, judge):
    rec = Gate(engine, judge=judge).check("run_command", {"command": "pip install x"})
    assert rec.verdict.decision is B and not rec.proceed


def test_on_decision_receives_record_and_errors_are_contained(engine):
    seen = []
    Gate(engine, on_decision=seen.append).check("run_command", {"command": "ls"}, agent_id="cursor")
    assert seen[0].agent_id == "cursor" and seen[0].tool_name == "run_command"

    def boom(_):
        raise RuntimeError("audit down")

    assert Gate(engine, on_decision=boom).check("run_command", {"command": "ls"}).proceed


def test_audit_failure_blocks_call_and_reports_audit_source(engine):
    def audit_failure(_):
        raise OSError("disk full")

    record = Gate(engine, audit_log=audit_failure).check(
        "run_command", {"command": "ls"}
    )
    assert record.verdict.decision is B
    assert record.verdict.source == "audit"
    assert not record.proceed


BY_NAME = """
default: ask
rules:
  - {id: no-delete-tools, kind: other, action: block, match: {regex: '^tool:delete_'}}
  - {id: shell-ok, kind: shell, action: allow, match: {program_any: [ls]}}
"""


def test_tools_can_be_blocked_by_name_without_default_ask_hitting_everything():
    gate = Gate(RuleEngine(parse_ruleset(BY_NAME)))
    assert not gate.check("delete_user", {"command": "ls"}).proceed
    assert gate.check("list_things", {"command": "ls"}).proceed
