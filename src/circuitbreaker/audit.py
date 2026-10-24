"""Append-only, hash-chained JSONL audit log."""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from circuitbreaker.proxy.gate import DecisionRecord

GENESIS_HASH = "0" * 64
_HASH_FIELD = "hash"
_PREVIOUS_HASH_FIELD = "previous_hash"


class AuditLogError(OSError):
    """The audit log could not be safely appended to."""


class AuditVerificationError(ValueError):
    """The audit log is malformed or its hash chain is invalid."""


@dataclass(frozen=True)
class VerificationResult:
    entries: int
    head_hash: str


def default_audit_path() -> Path:
    """Return the default per-user audit-log location."""
    return Path.home() / ".circuitbreaker" / "audit.jsonl"


def _canonical_json(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AuditLogError(f"audit event is not valid JSON: {exc}") from exc


def _digest(event: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in event.items() if key != _HASH_FIELD}
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def _decode_event(raw: bytes, line_number: int) -> dict[str, Any]:
    try:
        event = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise AuditVerificationError(f"line {line_number}: invalid JSON: {exc}") from exc
    if not isinstance(event, dict):
        raise AuditVerificationError(f"line {line_number}: event must be a JSON object")
    stored_hash = event.get(_HASH_FIELD)
    previous_hash = event.get(_PREVIOUS_HASH_FIELD)
    if not isinstance(stored_hash, str) or len(stored_hash) != 64:
        raise AuditVerificationError(f"line {line_number}: missing or invalid hash")
    if not isinstance(previous_hash, str) or len(previous_hash) != 64:
        raise AuditVerificationError(f"line {line_number}: missing or invalid previous_hash")
    try:
        bytes.fromhex(stored_hash)
        bytes.fromhex(previous_hash)
    except ValueError as exc:
        raise AuditVerificationError(f"line {line_number}: hash must be hexadecimal") from exc
    return event


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialize appends across threads and processes on Windows and POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            while True:
                lock_file.seek(0)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _last_event(path: Path) -> dict[str, Any] | None:
    """Read and validate the final complete record without loading the full log."""
    with path.open("rb") as log_file:
        log_file.seek(0, os.SEEK_END)
        end = log_file.tell()
        if end == 0:
            return None

        log_file.seek(end - 1)
        if log_file.read(1) != b"\n":
            raise AuditLogError("audit log ends with an incomplete record")

        position = end - 2
        chunks: list[bytes] = []
        while position >= 0:
            start = max(0, position - 4095)
            log_file.seek(start)
            chunk = log_file.read(position - start + 1)
            newline = chunk.rfind(b"\n")
            if newline >= 0:
                chunks.append(chunk[newline + 1 :])
                break
            chunks.append(chunk)
            position = start - 1
        raw = b"".join(reversed(chunks))
        try:
            event = _decode_event(raw, 1)
            if not hmac.compare_digest(event[_HASH_FIELD], _digest(event)):
                raise AuditLogError("audit log's final record has an invalid hash")
        except AuditVerificationError as exc:
            raise AuditLogError(str(exc)) from exc
        return event


class AuditLog:
    """Callable audit sink used by the shared tool-call gate."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_audit_path()
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        self._thread_lock = threading.Lock()
        try:
            parent_existed = self.path.parent.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not parent_existed and os.name != "nt":
                self.path.parent.chmod(0o700)
            descriptor = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            os.close(descriptor)
            if os.name != "nt":
                self.path.chmod(0o600)
        except OSError as exc:
            raise AuditLogError(f"cannot initialize audit log {self.path}: {exc}") from exc

    def __call__(self, record: DecisionRecord) -> None:
        """Append one decision and synchronously flush it to disk."""
        event: dict[str, Any] = {
            "timestamp": record.timestamp,
            "agent_id": record.agent_id,
            "tool_name": record.tool_name,
            "calls": [
                {"kind": call.kind.value, "target": call.target} for call in record.calls
            ],
            "verdict": {
                "decision": record.verdict.decision.value,
                "reason": record.verdict.reason,
                "feedback": record.verdict.feedback,
                "source": record.verdict.source,
            },
            "proceed": record.proceed,
            "mode": record.mode,
        }

        try:
            with self._thread_lock, _exclusive_file_lock(self._lock_path):
                previous = _last_event(self.path)
                event[_PREVIOUS_HASH_FIELD] = (
                    previous[_HASH_FIELD] if previous is not None else GENESIS_HASH
                )
                event[_HASH_FIELD] = _digest(event)
                line = _canonical_json(event) + b"\n"
                descriptor = os.open(self.path, os.O_APPEND | os.O_WRONLY)
                with os.fdopen(descriptor, "ab", closefd=True) as log_file:
                    log_file.write(line)
                    log_file.flush()
                    os.fsync(log_file.fileno())
        except (OSError, ValueError) as exc:
            raise AuditLogError(f"cannot append audit log {self.path}: {exc}") from exc


def verify_audit_log(path: str | Path | None = None) -> VerificationResult:
    """Verify every JSONL event and its link to the preceding event."""
    log_path = Path(path) if path is not None else default_audit_path()
    try:
        with log_path.open("rb") as log_file:
            previous_hash = GENESIS_HASH
            entries = 0
            for line_number, raw in enumerate(log_file, 1):
                if not raw.endswith(b"\n"):
                    raise AuditVerificationError(
                        f"line {line_number}: record is not newline-terminated"
                    )
                event = _decode_event(raw[:-1], line_number)
                if not hmac.compare_digest(event[_PREVIOUS_HASH_FIELD], previous_hash):
                    raise AuditVerificationError(
                        f"line {line_number}: previous_hash does not match the chain"
                    )
                if not hmac.compare_digest(event[_HASH_FIELD], _digest(event)):
                    raise AuditVerificationError(f"line {line_number}: hash mismatch")
                previous_hash = event[_HASH_FIELD]
                entries += 1
    except AuditVerificationError:
        raise
    except OSError as exc:
        raise AuditVerificationError(f"cannot read audit log {log_path}: {exc}") from exc
    return VerificationResult(entries=entries, head_hash=previous_hash)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="circuitbreaker-audit",
        description="Inspect CircuitBreaker's tamper-evident audit log.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser("verify", help="verify a JSONL audit log")
    verify_parser.add_argument(
        "path",
        nargs="?",
        default=str(default_audit_path()),
        help=f"audit log path (default: {default_audit_path()})",
    )
    args = parser.parse_args(argv)
    try:
        result = verify_audit_log(args.path)
    except AuditVerificationError as exc:
        print(f"Audit verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Audit log verified: {result.entries} entries; head {result.head_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
