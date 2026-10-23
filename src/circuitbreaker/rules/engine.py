"""Deterministic rule engine: ToolCall in, Verdict out. No AI, no network.

Precedence (deny-overrides): BLOCK > ASK > ALLOW > ruleset default.
A compound command is only allowed if *every* part of it is allowed.
Anything the engine cannot understand safely becomes ASK, never ALLOW.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from circuitbreaker.models import Decision, ToolCall, ToolKind, Verdict
from circuitbreaker.rules.loader import load_default_ruleset, load_ruleset
from circuitbreaker.rules.normalize import (
    ParsedShell,
    Segment,
    matches_patterns,
    normalize_host,
    normalize_path,
    normalize_sql,
    parse_shell,
)
from circuitbreaker.rules.schema import Match, Rule, RuleSet

log = logging.getLogger(__name__)

_IGNORED_REDIRECTS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}


def _rx(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def _resolve(rules: list[Rule]) -> Rule | None:
    """Deny-overrides: first BLOCK, else first ASK, else first ALLOW."""
    for wanted in (Decision.BLOCK, Decision.ASK, Decision.ALLOW):
        for rule in rules:
            if rule.action is wanted:
                return rule
    return None


def _shell_match(m: Match, seg: Segment, full_text: str) -> bool:
    if m.program_any is not None and not matches_patterns(
        seg.program, [p.lower() for p in m.program_any]
    ):
        return False
    if m.flags_all is not None and not all(f in seg.flags for f in m.flags_all):
        return False
    if m.flags_any is not None and not any(f in seg.flags for f in m.flags_any):
        return False
    if m.subcommand_any is not None and not (
        seg.args and seg.args[0].lower() in {s.lower() for s in m.subcommand_any}
    ):
        return False
    if m.args_any is not None and not any(
        matches_patterns(normalize_path(a), m.args_any) for a in seg.args
    ):
        return False
    if m.tokens_any is not None and not any(
        matches_patterns(t, m.tokens_any) for t in seg.raw_args
    ):
        return False
    return m.regex is None or _rx(m.regex, seg.text) or _rx(m.regex, full_text)


class RuleEngine:
    def __init__(self, ruleset: RuleSet) -> None:
        self.ruleset = ruleset
        self._by_kind: dict[ToolKind, list[Rule]] = {
            kind: [r for r in ruleset.rules if kind in r.kind] for kind in ToolKind
        }

    @classmethod
    def from_default(cls) -> RuleEngine:
        return cls(load_default_ruleset())

    @classmethod
    def from_file(cls, path: str | Path) -> RuleEngine:
        return cls(load_ruleset(path))

    # ------------------------------------------------------------------ public

    def evaluate(self, call: ToolCall) -> Verdict:
        try:
            if call.kind is ToolKind.SHELL:
                return self._eval_shell(call)
            if call.kind is ToolKind.SQL:
                return self._eval_sql(call)
            if call.kind is ToolKind.HTTP:
                return self._eval_http(call)
            return self._eval_simple(call)
        except Exception:  # fail closed: an engine bug must never become an ALLOW
            log.exception("rule engine error")
            return Verdict(
                decision=Decision.BLOCK,
                reason="Internal error while evaluating rules (failing closed).",
                source="engine",
            )

    # ------------------------------------------------------------------ helpers

    def _default(self) -> Verdict:
        return Verdict(
            decision=self.ruleset.default, reason="No rule matched this action.", source="default"
        )

    @staticmethod
    def _from_rule(rule: Rule) -> Verdict:
        return Verdict(
            decision=rule.action,
            reason=rule.description or f"Rule '{rule.id}' matched.",
            feedback=rule.feedback,
            source=f"rule:{rule.id}",
        )

    def _aggregate(self, verdicts: list[Verdict], ok: bool = True, problem: str = "") -> Verdict:
        for v in verdicts:
            if v.decision is Decision.BLOCK:
                return v
        if not ok:
            return Verdict(
                decision=Decision.ASK,
                reason=f"Action could not be analysed safely: {problem}.",
                feedback="Rewrite the action as plain, explicit commands.",
                source="engine",
            )
        for v in verdicts:
            if v.decision is Decision.ASK:
                return v
        if verdicts and all(v.decision is Decision.ALLOW for v in verdicts):
            sources = "+".join(dict.fromkeys(v.source for v in verdicts))
            reason = verdicts[0].reason if len(verdicts) == 1 else "All parts were allowed."
            return Verdict(decision=Decision.ALLOW, reason=reason, source=sources)
        return self._default()

    # ------------------------------------------------------------------ shell

    def _segment_verdict(self, seg: Segment, full_text: str) -> Verdict:
        matched = [
            r for r in self._by_kind[ToolKind.SHELL] if _shell_match(r.match, seg, full_text)
        ]
        picked = _resolve(matched)
        verdict = self._from_rule(picked) if picked else self._default()
        if verdict.decision is Decision.ALLOW and (seg.wrappers or seg.dynamic):
            return Verdict(
                decision=Decision.ASK,
                reason="Elevated, wrapped or variable-driven commands are never auto-allowed.",
                source="engine",
            )
        return verdict

    def _eval_shell(self, call: ToolCall) -> Verdict:
        full_text = " ".join(call.target.split())
        parsed: ParsedShell = parse_shell(call.target)
        verdicts = [self._segment_verdict(seg, full_text) for seg in parsed.segments]

        # Whole-command regex rules (fork bombs, curl|sh) run even if parsing failed.
        whole = Segment("", frozenset(), (), (), full_text)
        hit = _resolve(
            [r for r in self._by_kind[ToolKind.SHELL] if _shell_match(r.match, whole, full_text)]
        )
        if hit and hit.action is not Decision.ALLOW:
            verdicts.append(self._from_rule(hit))

        # Redirects are file operations in disguise: `echo x > ~/.ssh/authorized_keys`.
        for path in parsed.redirect_writes:
            if path not in _IGNORED_REDIRECTS:
                verdicts.append(
                    self._eval_simple(ToolCall(kind=ToolKind.FILE_WRITE, target=path))
                )
        for path in parsed.redirect_reads:
            if path not in _IGNORED_REDIRECTS:
                verdicts.append(self._eval_simple(ToolCall(kind=ToolKind.FILE_READ, target=path)))

        return self._aggregate(verdicts, parsed.ok, parsed.problem)

    # ------------------------------------------------------------------ file / other

    def _eval_simple(self, call: ToolCall) -> Verdict:
        rules = self._by_kind[call.kind]
        if call.kind in (ToolKind.FILE_READ, ToolKind.FILE_WRITE):
            path = normalize_path(call.target)
            matched = [r for r in rules if matches_patterns(path, r.match.path_any or [])]
        else:
            matched = [r for r in rules if r.match.regex and _rx(r.match.regex, call.target)]
        picked = _resolve(matched)
        return self._from_rule(picked) if picked else self._default()

    # ------------------------------------------------------------------ http

    def _eval_http(self, call: ToolCall) -> Verdict:
        host = normalize_host(call.target)
        method = str(call.args.get("method", "GET")).upper()
        matched: list[Rule] = []
        for rule in self._by_kind[ToolKind.HTTP]:
            m = rule.match
            if m.host_any is not None and not (host and matches_patterns(host, m.host_any)):
                continue
            if m.method_any is not None and method not in {x.upper() for x in m.method_any}:
                continue
            if m.regex is not None and not _rx(m.regex, call.target):
                continue
            matched.append(rule)
        picked = _resolve(matched)
        verdict = self._from_rule(picked) if picked else self._default()
        if host is None and verdict.decision is not Decision.BLOCK:
            return Verdict(
                decision=Decision.ASK, reason="URL has no readable host.", source="engine"
            )
        return verdict

    # ------------------------------------------------------------------ sql

    def _eval_sql(self, call: ToolCall) -> Verdict:
        verdicts: list[Verdict] = []
        for statement in normalize_sql(call.target):
            matched = [
                r
                for r in self._by_kind[ToolKind.SQL]
                if r.match.regex and _rx(r.match.regex, statement)
            ]
            picked = _resolve(matched)
            verdicts.append(self._from_rule(picked) if picked else self._default())
        return self._aggregate(verdicts)
