"""stdio plumbing: client <-> CircuitBreaker <-> upstream MCP server.

MCP's stdio transport is newline-delimited JSON. stdout is the protocol channel,
so nothing but JSON-RPC may ever be written to it. Logs go to stderr.
Threads (not asyncio) keep this identical on Windows, where console stdin cannot
be used with asyncio.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
from typing import BinaryIO

from circuitbreaker.proxy.interceptor import Forward, Interceptor, Reply

log = logging.getLogger("circuitbreaker.proxy")


class ProxyError(RuntimeError):
    """The proxy cannot start (for example, the upstream command does not exist)."""


def _with_newline(data: bytes) -> bytes:
    return data if data.endswith(b"\n") else data + b"\n"


class Proxy:
    def __init__(
        self,
        interceptor: Interceptor,
        command: list[str],
        client_in: BinaryIO | None = None,
        client_out: BinaryIO | None = None,
    ) -> None:
        if not command:
            raise ProxyError("no upstream command given")
        self.interceptor = interceptor
        self.command = command
        self.client_in = client_in or sys.stdin.buffer
        self.client_out = client_out or sys.stdout.buffer
        self._out_lock = threading.Lock()

    def run(self) -> int:
        proc = self._spawn()
        threading.Thread(
            target=self._client_to_upstream, args=(proc,), daemon=True, name="client->upstream"
        ).start()
        try:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, b""):
                self._write_client(_with_newline(line))
        except KeyboardInterrupt:
            pass
        finally:
            code = self._shutdown(proc)
        return code

    # ------------------------------------------------------------------

    def _spawn(self) -> subprocess.Popen[bytes]:
        # which() resolves Windows shims such as npx -> npx.cmd that Popen cannot find.
        exe = shutil.which(self.command[0])
        if exe is None:
            raise ProxyError(f"cannot find upstream command: {self.command[0]!r}")
        try:
            return subprocess.Popen(
                [exe, *self.command[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE
            )
        except OSError as exc:
            raise ProxyError(f"cannot start upstream server: {exc}") from exc

    def _client_to_upstream(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdin is not None
        try:
            for line in iter(self.client_in.readline, b""):
                action = self.interceptor.process(line)
                if isinstance(action, Forward):
                    proc.stdin.write(_with_newline(action.data))
                    proc.stdin.flush()
                elif isinstance(action, Reply):
                    self._write_client(action.data)
        except (BrokenPipeError, OSError, ValueError):
            pass  # the other side went away
        finally:
            try:
                proc.stdin.close()  # client closed: tell the upstream server to finish
            except OSError:
                pass

    def _write_client(self, data: bytes) -> None:
        with self._out_lock:
            try:
                self.client_out.write(data)
                self.client_out.flush()
            except (BrokenPipeError, OSError, ValueError):
                log.warning("client closed the connection")

    @staticmethod
    def _shutdown(proc: subprocess.Popen[bytes]) -> int:
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        return proc.returncode or 0
