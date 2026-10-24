"""The gate: one decision per tool call. Maps, evaluates rules, consults the judge.

Pure logic with no I/O, so it is easy to test and reuse from other front-ends
(MCP proxy today, agent hooks later).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from circuitbreaker.models import Decision, ToolCall, Verdict
from circuitbreaker.proxy.mapping import ToolMapper
from circuitbreaker.rules import RuleEngine

log = logging.getLogger("circuitbreaker.gate")

Mode = Literal["enforce", "monitor"]

_APPROVAL_FEEDBACK = (
    "This action needs human approval, which is not available. Try a safer, more specific "
    "alternative, or ask the user to run it themselves."
)


class Judge(Protocol):
    """Resolves actions the deterministic rules could not decide (the AI risk judge, M5)."""

    def judge(self, call: ToolCall, rule_verdict: Verdict) -> Verdict: ...


@dataclass(frozen=True)
class DecisionRecord:
    """Everything the audit log (M4) needs about one decision."""

    timestamp: float
    agent_id: str
    tool_name: str
    calls: tuple[ToolCall, ...]
    verdict: Verdict  # final decision: ALLOW or BLOCK (ASK is resolved before this point)
    proceed: bool  # False means the call must not reach the upstream server
    mode: str


def _combine(verdicts: list[Verdict]) -> Verdict:
    for v in verdicts:
        if v.decision is Decision.BLOCK:
            return v
    for v in verdicts:
        if v.decision is Decision.ASK:
            return v
    sources = "+".join(dict.fromkeys(v.source for v in verdicts))
    reason = verdicts[0].reason if len(verdicts) == 1 else "All parts were allowed."
    return Verdict(decision=Decision.ALLOW, reason=reason, source=sources)


class Gate:
    def __init__(
        self,
        engine: RuleEngine,
        mapper: ToolMapper | None = None,
        judge: Judge | None = None,
        mode: Mode = "enforce",
        on_decision: Callable[[DecisionRecord], None] | None = None,
        audit_log: Callable[[DecisionRecord], None] | None = None,
    ) -> None:
        self.engine = engine
        self.mapper = mapper or ToolMapper()
        self.judge = judge
        self.mode = mode
        self.on_decision = on_decision
        self.audit_log = audit_log

    def check(self, tool_name: str, arguments: Any, agent_id: str = "unknown") -> DecisionRecord:
        mapped = self.mapper.map(tool_name, arguments, agent_id)
        verdicts: list[Verdict] = []
        for item in mapped:
            verdict = self.engine.evaluate(item.call)
            if item.advisory and verdict.source == "default":
                continue  # no rule spoke about this tool name
            if verdict.decision is Decision.ASK:
                verdict = self._ask_judge(item.call, verdict)
            verdicts.append(verdict)

        if not verdicts:  # cannot happen (mapper always yields a real call), but fail closed
            final = Verdict(decision=Decision.BLOCK, reason="Nothing to evaluate.", source="gate")
        else:
            final = _combine(verdicts)
        if final.decision is Decision.ASK:
            final = Verdict(
                decision=Decision.BLOCK,
                reason=f"Needs approval before it can run: {final.reason}",
                feedback=final.feedback or _APPROVAL_FEEDBACK,
                source=final.source,
            )

        record = DecisionRecord(
            timestamp=time.time(),
            agent_id=agent_id,
            tool_name=tool_name,
            calls=tuple(m.call for m in mapped),
            verdict=final,
            proceed=final.decision is Decision.ALLOW or self.mode == "monitor",
            mode=self.mode,
        )
        if self.audit_log is not None:
            try:
                self.audit_log(record)
            except Exception:
                log.exception("audit log append failed; blocking action")
                record = replace(
                    record,
                    verdict=Verdict(
                        decision=Decision.BLOCK,
                        reason="The audit log could not record this action, so it was blocked.",
                        feedback="Restore audit-log access before retrying this action.",
                        source="audit",
                    ),
                    proceed=False,
                )
        self._log(record)
        if self.on_decision is not None:
            try:
                self.on_decision(record)
            except Exception:
                log.exception("on_decision callback failed")
        return record

    def _ask_judge(self, call: ToolCall, verdict: Verdict) -> Verdict:
        if self.judge is None:
            return verdict
        try:
            judged = self.judge.judge(call, verdict)
        except Exception:
            log.exception("risk judge failed; failing closed")
            return Verdict(
                decision=Decision.BLOCK,
                reason="The risk judge was unavailable, so the action was blocked.",
                feedback=_APPROVAL_FEEDBACK,
                source="judge",
            )
        if judged.decision is Decision.ASK:
            return Verdict(
                decision=Decision.BLOCK,
                reason="The risk judge could not decide, so the action was blocked.",
                feedback=judged.feedback or _APPROVAL_FEEDBACK,
                source="judge",
            )
        return judged

    @staticmethod
    def _log(record: DecisionRecord) -> None:
        kinds = ",".join(dict.fromkeys(c.kind.value for c in record.calls))
        allowed = record.verdict.decision is Decision.ALLOW
        suffix = "" if record.proceed == allowed else " [monitor: forwarded anyway]"
        log.info(
            "%s tool=%s kinds=%s source=%s%s",
            record.verdict.decision.value.upper(),
            record.tool_name,
            kinds,
            record.verdict.source,
            suffix,
        )
        for call in record.calls:
            log.debug("  %s: %s", call.kind.value, call.target[:300])
