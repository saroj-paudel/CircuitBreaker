# Agent hooks — built-in tool protection

The MCP proxy intercepts calls that go through the MCP `tools/call` protocol.
But AI agents also have **built-in tools** — their own shell executor, file editor,
web fetch — that never touch MCP.  The hook front-end protects those too, by
running the same deterministic rule engine before each built-in tool invocation.

```
                    ┌─────────────────────────────────┐
                    │         CircuitBreaker           │
 Built-in tools ──► │  circuitbreaker-hook  (M3b)     │
  Bash / Edit /     │    ↓                             │
  WebFetch / …      │  Gate ── engine ── rules (M2)   │
                    │    ↑                             │
 MCP tools/call ──► │  circuitbreaker-proxy  (M3)      │
                    └─────────────────────────────────┘
```

---

## Claude Code / Antigravity (AGY)

### How the hook works

Claude Code calls an executable before every built-in tool invocation.
The hook receives JSON on **stdin** and must write a JSON response to **stdout**
(logs go to **stderr**).

```
stdin  → {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}
stdout ← {"decision": "block", "reason": "...", "feedback": "..."}
```

A non-zero exit code also signals a block.

### Installation

**1 — Clone and install CircuitBreaker** (if not already done):

```bash
git clone https://github.com/saroj-paudel/CircuitBreaker.git
cd CircuitBreaker
python -m pip install -e .
```

For development and tests, install `python -m pip install -e ".[dev]"` instead. The package
is installed into the active Python environment; if the agent cannot find the executable,
use the full path to that environment's `circuitbreaker` command.

**2 — Edit `~/.claude/settings.json`**:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "circuitbreaker hook --agent claude_code"
          }
        ]
      }
    ]
  }
}
```

> **Windows:** use the full path to the venv Python if it is not on PATH:
> ```json
> "command": "C:\\Users\\you\\Projects\\CircuitBreaker\\.venv\\Scripts\\circuitbreaker.exe hook --agent claude_code"
> ```

**3 — (Optional) point at your own rules file:**

```json
"command": "circuitbreaker hook --agent claude_code --rules my_rules.yaml"
```

### Verify it works

Open a new Claude Code session and ask it to run something dangerous:

```
Run: rm -rf /tmp/test_dir
```

You should see Claude receive a "blocked" response and suggest an alternative.

### Monitor mode (tune before you enforce)

```json
"command": "circuitbreaker hook --agent claude_code --mode monitor"
```

In monitor mode every decision is logged to stderr and actions are normally allowed.
Audit-log failures still block actions. Watch the logs, adjust your rules, then switch back
to `enforce`.

---

## OpenAI Codex

### How the hook works

Codex calls a pre-tool hook before shell and file operations.  The hook can
receive the tool payload via:

- The `CODEX_TOOL_CALL` environment variable (newer Codex versions), **or**
- JSON on **stdin** (older versions).

CircuitBreaker handles both automatically.  Exit code `0` = allow, `1` = block.
The block reason is written to **stderr** (which Codex shows the model) and as
JSON to **stdout**.

### Installation

**Edit `codex.yaml`** in your project or home directory:

```yaml
shell_hooks:
  pre:
    - command: circuitbreaker hook --agent codex
```

**Windows:**

```yaml
shell_hooks:
  pre:
    - command: C:\Users\you\Projects\CircuitBreaker\.venv\Scripts\circuitbreaker.exe hook --agent codex
```

### Monitor mode

```yaml
shell_hooks:
  pre:
    - command: circuitbreaker hook --agent codex --mode monitor
```

---

## Options

| Option | Meaning |
|---|---|
| `--agent claude_code\|codex` | Required. Which agent protocol to speak. |
| `--rules FILE` | Your rules YAML. Default: built-in rules. |
| `--audit-log FILE` | Hash-chained JSONL decision log. Default: `~/.circuitbreaker/audit.jsonl`. |
| `--judge-url URL` + `--judge-model NAME` | Opt in to an OpenAI-compatible judge for ambiguous actions. |
| `--judge-api-key-env ENV` | API-key environment variable; default `CIRCUITBREAKER_JUDGE_API_KEY`. |
| `--judge-timeout SECONDS` | Judge request timeout; default 20 seconds. |
| `--mode enforce` | Default. Blocked/ambiguous calls are denied. |
| `--mode monitor` | Log decisions and allow them unless the audit log fails. |
| `-v` / `--verbose` | Log tool targets to stderr (may contain secrets). |

---

## What is protected

| Claude tool | Codex type | Checked as |
|---|---|---|
| `Bash` | `shell` / `exec` | Shell command |
| `Read` / `NotebookRead` | `file_read` | File read |
| `Write` / `Edit` / `MultiEdit` / `NotebookEdit` | `file_write` | File write |
| `WebFetch` | `http` / `fetch` | HTTP request |
| `WebSearch` | — | HTTP (query string) |
| Anything else | Anything else | `other` → ruleset default (`ask`, then judge or block) |

---

## Writing rules for hooks

The hooks reuse the same YAML rule format as the MCP proxy.  See [rules.md](rules.md)
for the full rule schema.  A few hook-specific examples:

```yaml
# Block the agent from running any curl command at all.
- id: hook-no-curl
  kind: shell
  action: block
  match:
    program_any: [curl]
  feedback: Use the WebFetch tool instead of curl.

# Block writes to the migrations directory without asking.
- id: hook-protect-migrations
  kind: file_write
  action: ask
  match:
    path_any: ["**/migrations/**"]
  feedback: Run migrations explicitly, do not auto-generate them.
```

---

## Fail-closed behaviour

- Unparseable hook input → block (CircuitBreaker never lets a parse failure become allow).
- Missing `tool_name` / `type` → block.
- Internal error → block.
- A crashing or timing-out judge → block.
- If the audit log cannot record a decision, the action is blocked.
- Invalid rules file → hook exits with code 2 at startup (Claude Code / Codex will see the
  subprocess fail and refuse to start).

The hook appends decisions to `~/.circuitbreaker/audit.jsonl` by default. Override it with
`--audit-log FILE`; verify the chain with `circuitbreaker-audit verify`. Audit write failures
block actions even in monitor mode.

To resolve ambiguous actions, pass both `--judge-url URL` and `--judge-model NAME`. The
endpoint receives action targets and mapped arguments; only enable it if sending that data
there is acceptable. See [proxy.md](proxy.md#optional-risk-judge) for response format,
credential handling, and fail-closed behavior. Deterministic block rules are never sent to
the judge.
