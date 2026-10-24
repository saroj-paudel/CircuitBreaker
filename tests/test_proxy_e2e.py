"""End to end: the real proxy process in front of a fake MCP server."""

import copy
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

import pytest
import yaml

FAKE_SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


@pytest.fixture(scope="module")
def rules_file(tmp_path_factory):
    """Default rules plus an allow rule for the fake server's `seen` tool."""
    data = yaml.safe_load(files("circuitbreaker.rules").joinpath("default.yaml").read_text("utf-8"))
    data = copy.deepcopy(data)
    data["rules"].append(
        {
            "id": "allow-seen",
            "kind": "other",
            "action": "allow",
            "match": {"regex": r"^tool:seen\b"},
        }
    )
    path = tmp_path_factory.mktemp("rules") / "rules.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(path)


class ProxyProc:
    def __init__(self, *proxy_args, audit_file=None):
        audit_args = ["--audit-log", str(audit_file)] if audit_file else []
        cmd = [sys.executable, "-m", "circuitbreaker.proxy", *proxy_args, *audit_args, "--",
               sys.executable, FAKE_SERVER]  # fmt: skip
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self._next_id = 1
        self._watchdog = threading.Timer(60, self.proc.kill)  # a hang becomes a failure
        self._watchdog.start()

    def send_raw(self, data: bytes):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def recv(self):
        raw = self.proc.stdout.readline()
        assert raw, "proxy closed stdout unexpectedly"
        return json.loads(raw)  # also proves stdout carries only JSON

    def rpc(self, method, params=None):
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self._next_id += 1
        self.send_raw(json.dumps(msg).encode() + b"\n")
        return self.recv()

    def call(self, name, **arguments):
        return self.rpc("tools/call", {"name": name, "arguments": arguments})["result"]

    def text(self, name, **arguments):
        return self.call(name, **arguments)["content"][0]["text"]

    def close(self):
        self.proc.stdin.close()
        code = self.proc.wait(timeout=30)
        self._watchdog.cancel()
        return code


@pytest.fixture
def proxy(rules_file):
    audit_file = Path(rules_file).with_name("proxy-audit.jsonl")
    p = ProxyProc("--rules", rules_file, audit_file=audit_file)
    yield p
    if p.proc.poll() is None:
        p.proc.kill()
    p._watchdog.cancel()


def test_handshake_and_list_pass_through(proxy):
    init = proxy.rpc("initialize", {"clientInfo": {"name": "pytest"}})
    assert init["result"]["serverInfo"]["name"] == "fake"
    assert proxy.rpc("tools/list")["result"]["tools"][0]["name"] == "run_command"


def test_allowed_call_reaches_server(proxy):
    assert proxy.text("run_command", command="ls -la") == "executed run_command"


def test_blocked_call_never_reaches_server(proxy):
    result = proxy.call("run_command", command="rm  -fr  /")
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "Blocked by CircuitBreaker" in text and "inside the project" in text
    assert "rm" not in proxy.text("seen")


def test_file_tools_are_mapped_and_enforced(proxy):
    assert proxy.call("write_file", path=".env", content="KEY=1")["isError"] is True
    assert proxy.call("read_file", path="README.md").get("isError") is None
    seen = proxy.text("seen")
    assert "README.md" in seen and ".env" not in seen


def test_ambiguous_action_is_denied_until_a_judge_exists(proxy):
    result = proxy.call("run_command", command="pip install requests")
    assert result["isError"] is True
    assert "approval" in result["content"][0]["text"].lower()


def test_configured_json_judge_resolves_ask_through_proxy(rules_file):
    class JudgeHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert request["messages"][1]["role"] == "user"
            content = json.dumps(
                {"decision": "allow", "reason": "Accepted for this test", "feedback": None}
            )
            body = json.dumps(
                {"choices": [{"message": {"content": content}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), JudgeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    audit_file = Path(rules_file).with_name("judge-audit.jsonl")
    proxy = ProxyProc(
        "--rules",
        rules_file,
        "--judge-url",
        f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
        "--judge-model",
        "test-model",
        audit_file=audit_file,
    )
    try:
        result = proxy.call("run_command", command="pip install requests")
        assert result.get("isError") is None
        assert "executed run_command" in result["content"][0]["text"]
    finally:
        if proxy.proc.poll() is None:
            proxy.proc.kill()
        proxy._watchdog.cancel()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_bad_json_is_rejected_and_proxy_keeps_working(proxy):
    proxy.send_raw(b"this is not json\n")
    assert proxy.recv()["error"]["code"] == -32700
    assert proxy.text("run_command", command="pwd") == "executed run_command"


def test_monitor_mode_forwards_everything(rules_file):
    audit_file = Path(rules_file).with_name("monitor-audit.jsonl")
    p = ProxyProc("--rules", rules_file, "--mode", "monitor", audit_file=audit_file)
    try:
        assert p.text("run_command", command="rm -rf /") == "executed run_command"
    finally:
        p.proc.kill()
        p._watchdog.cancel()


def test_proxy_exits_cleanly_when_client_disconnects(proxy):
    proxy.call("run_command", command="ls")
    assert proxy.close() == 0


def test_proxy_persists_decisions_and_audit_cli_verifies_them(proxy, rules_file):
    audit_file = Path(rules_file).with_name("proxy-audit.jsonl")
    proxy.call("run_command", command="ls")
    result = subprocess.run(
        [sys.executable, "-m", "circuitbreaker.audit", "verify", str(audit_file)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "entries" in result.stdout


def test_refuses_to_start_with_invalid_rules(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("rules: [{id: a, kind: shell, action: block, match: {nope: 1}}]")
    result = subprocess.run(
        [sys.executable, "-m", "circuitbreaker.proxy", "--rules", str(bad), "--", sys.executable],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert result.returncode == 2 and "refusing to start" in result.stderr


def test_unknown_upstream_command_fails_clearly():
    result = subprocess.run(
        [sys.executable, "-m", "circuitbreaker.proxy", "--", "definitely-not-a-real-command"],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert result.returncode == 2 and "cannot find upstream command" in result.stderr


def test_missing_upstream_command_is_a_usage_error():
    result = subprocess.run(
        [sys.executable, "-m", "circuitbreaker.proxy"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 2 and "after '--'" in result.stderr
