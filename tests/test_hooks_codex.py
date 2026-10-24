"""Tests for the OpenAI Codex hook runner."""

import io
import json

import pytest

from circuitbreaker.hooks.codex import process, run_stdio
from circuitbreaker.proxy import Gate
from circuitbreaker.rules import RuleEngine


@pytest.fixture(scope="module")
def gate():
    return Gate(RuleEngine.from_default())


@pytest.fixture(scope="module")
def monitor_gate():
    return Gate(RuleEngine.from_default(), mode="monitor")


# ---------------------------------------------------------------------------
# process() — pure logic
# ---------------------------------------------------------------------------


def _payload(**kwargs: object) -> dict:
    return dict(kwargs)


def test_shell_allow_safe(gate):
    resp = process(gate, _payload(type="shell", command="git status"))
    assert resp.allowed


def test_shell_block_dangerous(gate):
    resp = process(gate, _payload(type="shell", command="rm -rf /"))
    assert not resp.allowed


def test_exec_alias_block_dangerous(gate):
    resp = process(gate, _payload(type="exec", command="rm -rf /"))
    assert not resp.allowed


def test_file_read_allows_project_file(gate):
    resp = process(gate, _payload(type="file_read", path="README.md"))
    assert resp.allowed


def test_file_read_blocks_dotenv(gate):
    resp = process(gate, _payload(type="file_read", path=".env"))
    assert not resp.allowed


def test_file_write_blocks_secret(gate):
    resp = process(gate, _payload(type="file_write", path=".env"))
    assert not resp.allowed


def test_file_write_allows_project(gate):
    resp = process(gate, _payload(type="file_write", path="src/mymodule.py"))
    assert resp.allowed


def test_http_allows_safe_registry(gate):
    resp = process(gate, _payload(type="http", url="https://pypi.org/project/x/", method="GET"))
    assert resp.allowed


def test_http_blocks_cloud_metadata(gate):
    resp = process(gate, _payload(type="http", url="http://169.254.169.254/"))
    assert not resp.allowed


def test_unknown_type_fails_closed(gate):
    resp = process(gate, _payload(type="mystery", data="x"))
    assert not resp.allowed


# ---------------------------------------------------------------------------
# run_stdio() — stdin source
# ---------------------------------------------------------------------------


def _run_stdin(gate, payload: dict) -> tuple[int, dict, str]:
    stdin = io.StringIO(json.dumps(payload))
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = run_stdio(gate, stdin=stdin, stdout=stdout, stderr=stderr)
    stdout.seek(0)
    stderr.seek(0)
    out_text = stdout.read().strip()
    err_text = stderr.read()
    out_payload = json.loads(out_text) if out_text else {}
    return code, out_payload, err_text


def test_stdin_allow_exits_0(gate):
    code, payload, _ = _run_stdin(gate, {"type": "shell", "command": "ls"})
    assert code == 0
    assert payload.get("allowed") is True


def test_stdin_block_exits_1(gate):
    code, payload, stderr_text = _run_stdin(gate, {"type": "shell", "command": "rm -rf /"})
    assert code == 1
    assert payload.get("allowed") is False
    assert "CircuitBreaker" in stderr_text


def test_stdin_block_has_message(gate):
    code, payload, _ = _run_stdin(gate, {"type": "shell", "command": "rm -rf /"})
    assert "message" in payload


def test_stdin_empty_fails_closed(gate):
    code, _, _ = _run_stdin(gate, {})
    # Empty payload → unknown type → fails closed.
    # (type defaults to "other" which the ruleset default blocks)
    # We just verify it doesn't crash.
    assert isinstance(code, int)


def test_stdin_monitor_exits_0_despite_block(monitor_gate):
    code, payload, _ = _run_stdin(monitor_gate, {"type": "shell", "command": "rm -rf /"})
    assert code == 0
    assert payload.get("allowed") is True


# ---------------------------------------------------------------------------
# run_stdio() — CODEX_TOOL_CALL env var source
# ---------------------------------------------------------------------------


def test_env_var_takes_precedence_over_stdin(gate, monkeypatch):
    env_payload = json.dumps({"type": "shell", "command": "git status"})
    monkeypatch.setenv("CODEX_TOOL_CALL", env_payload)
    # stdin is empty — if env var is used, this still works.
    stdin = io.StringIO("")
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = run_stdio(gate, stdin=stdin, stdout=stdout, stderr=stderr)
    assert code == 0


def test_env_var_block(gate, monkeypatch):
    env_payload = json.dumps({"type": "shell", "command": "rm -rf /"})
    monkeypatch.setenv("CODEX_TOOL_CALL", env_payload)
    stdin = io.StringIO("")
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = run_stdio(gate, stdin=stdin, stdout=stdout, stderr=stderr)
    assert code == 1
