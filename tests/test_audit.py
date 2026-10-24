import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from circuitbreaker.audit import (
    GENESIS_HASH,
    AuditLog,
    AuditLogError,
    AuditVerificationError,
    verify_audit_log,
)
from circuitbreaker.models import Decision, ToolCall, ToolKind, Verdict
from circuitbreaker.proxy import DecisionRecord


def record(index):
    return DecisionRecord(
        timestamp=float(index),
        agent_id="test-agent",
        tool_name="run_command",
        calls=(ToolCall(kind=ToolKind.SHELL, target=f"echo {index}"),),
        verdict=Verdict(decision=Decision.ALLOW, reason="test", source="test"),
        proceed=True,
        mode="enforce",
    )


def test_audit_log_appends_hash_chained_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(path)
    audit(record(1))
    audit(record(2))

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert events[0]["previous_hash"] == GENESIS_HASH
    assert events[1]["previous_hash"] == events[0]["hash"]
    assert events[0]["calls"] == [{"kind": "shell", "target": "echo 1"}]
    assert verify_audit_log(path).entries == 2


def test_empty_audit_log_is_valid(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.touch()
    result = verify_audit_log(path)
    assert result.entries == 0 and result.head_hash == GENESIS_HASH


def test_verification_detects_modified_event(tmp_path):
    path = tmp_path / "audit.jsonl"
    audit = AuditLog(path)
    audit(record(1))
    audit(record(2))
    first, second = path.read_text(encoding="utf-8").splitlines()
    changed = json.loads(first)
    changed["calls"][0]["target"] = "echo changed"
    path.write_text(json.dumps(changed) + "\n" + second + "\n", encoding="utf-8")

    with pytest.raises(AuditVerificationError, match="line 1: hash mismatch"):
        verify_audit_log(path)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ('{"hash":"bad"}', "not newline-terminated"),
        ("not json\n", "invalid JSON"),
        ('{"hash":"' + "a" * 64 + '","previous_hash":"' + "0" * 64 + '"}\n', "hash mismatch"),
    ],
)
def test_verification_rejects_invalid_logs(tmp_path, contents, message):
    path = tmp_path / "bad.jsonl"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(AuditVerificationError, match=message):
        verify_audit_log(path)


def test_append_refuses_an_incomplete_tail(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_bytes(b'{"partial":true}')
    with pytest.raises(AuditLogError, match="incomplete record"):
        AuditLog(path)(record(1))
    assert path.read_bytes() == b'{"partial":true}'


def test_concurrent_writers_preserve_a_valid_chain(tmp_path):
    path = tmp_path / "audit.jsonl"

    def append(index):
        AuditLog(path)(record(index))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(40)))

    result = verify_audit_log(path)
    assert result.entries == 40
    targets = {
        json.loads(line)["calls"][0]["target"] for line in path.read_text().splitlines()
    }
    assert targets == {f"echo {index}" for index in range(40)}
