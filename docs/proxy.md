# The MCP proxy

CircuitBreaker sits between your agent and an MCP server. The agent talks to CircuitBreaker
exactly as it would talk to the server; every `tools/call` is checked first.

```
agent  <--stdio-->  circuitbreaker-proxy  <--stdio-->  real MCP server
                         |
                  rules (deterministic) -> [AI judge, M5] -> allow / block
```

## Wire it into an agent

Any MCP client that launches servers from a JSON config works the same way. Replace the
server's `command` with the proxy and put the original command after `--`:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "circuitbreaker-proxy",
      "args": ["--rules", "my_rules.yaml", "--", "npx", "-y", "@modelcontextprotocol/server-filesystem", "."]
    }
  }
}
```

The agent launches the proxy, so `circuitbreaker-proxy` must be findable from *its*
environment. If it is not on PATH, use the full path to the script or to your venv's Python:

```json
"command": "C:\\Users\\you\\Projects\\CircuitBreaker\\.venv\\Scripts\\python.exe",
"args": ["-m", "circuitbreaker.proxy", "--", "npx", "-y", "..."]
```

Run it by hand to see it work: `python -m circuitbreaker.proxy -- <server command>`
(then type JSON-RPC lines), or just rely on the tests.

## Options

| Option | Meaning |
|---|---|
| `--rules FILE` | Your rules YAML. Default: built-in rules. An invalid file stops startup. |
| `--audit-log FILE` | Hash-chained JSONL decision log. Default: `~/.circuitbreaker/audit.jsonl`. |
| `--judge-url URL` + `--judge-model NAME` | Opt in to an OpenAI-compatible JSON risk judge for ambiguous (`ask`) actions. |
| `--judge-api-key-env ENV` | Environment variable for the judge API key. Default: `CIRCUITBREAKER_JUDGE_API_KEY`. |
| `--judge-timeout SECONDS` | Judge request timeout. Default: 20 seconds. |
| `--mode enforce` | Default. Actions denied by the rules or judge never reach the server. |
| `--mode monitor` | Decisions are logged to stderr and forwarded unless the audit log fails. Use it to tune rules before enforcing. |
| `-v` | Also log call targets (commands, paths). Off by default because they can contain secrets. |

Logs go to **stderr**. stdout is the protocol channel and carries only JSON-RPC.

## What happens to a call

1. The tool name and arguments are mapped to one or more actions: `command` becomes a shell
   action, `path` a file read or write (decided by the tool name; unknown verbs count as
   writes), `url` an HTTP request, `query` or `sql` a SQL statement.
2. Each action goes through the rule engine from M2. One blocked part blocks the whole call.
3. **allow** forwards the original bytes untouched. **block** answers the agent with a tool
   error (`isError: true`) that explains why and suggests an alternative, so the model can
   adapt. The server never sees the request.
4. **ask** (no rule decided) is denied with a "needs approval" message unless an optional
   judge is configured. The judge can allow or block ambiguous actions; deterministic
   `block` rules always take precedence.
5. Every decision is appended to the audit log before the call is forwarded or blocked. If
   the log cannot be written, the action is blocked.

## Audit log

The proxy and agent hooks append each decision to `~/.circuitbreaker/audit.jsonl` by default.
Override the location with `--audit-log FILE`. Records include tool targets and decision
reasons, which may contain sensitive command text or paths; protect the file accordingly.
Appends are serialized across concurrent processes, flushed to disk, and linked using
SHA-256 hashes. A write failure blocks the action, including in monitor mode. Verify the
full chain with:

```bash
circuitbreaker-audit verify
circuitbreaker-audit verify path/to/audit.jsonl
```

Verification reports the first malformed or modified line. The hash chain is tamper-evident,
not a signature: anyone who can rewrite the entire log can recompute its hashes.

## Optional risk judge

The judge is disabled by default. To opt in, provide an OpenAI-compatible chat-completions
endpoint and model to either `circuitbreaker-proxy` or `circuitbreaker-hook`:

```bash
circuitbreaker-proxy --judge-url http://127.0.0.1:8000/v1/chat/completions \
  --judge-model local-model -- <server command>
```

For remote endpoints, use HTTPS and set `CIRCUITBREAKER_JUDGE_API_KEY`; choose another
environment variable with `--judge-api-key-env NAME`, or set it to an empty string for an
endpoint that does not require a key. The `--judge-timeout` option bounds each request.
The configured endpoint receives the action target and mapped arguments, so only enable it
when that data may be sent to the endpoint. The judge must return a JSON object with
`decision` (`allow` or `block`), `reason`, and optional `feedback`; malformed responses,
request failures, and timeouts fail closed. This is a provider-neutral transport contract,
not a guarantee that model judgments are correct; the specific model can be selected later.

Tools the mapper cannot classify become `kind: other` and fall to the ruleset default (ask);
they are denied unless a configured judge resolves the action.

## Allowing or blocking a tool by name

Targets for `other` actions look like `tool:<name> <json arguments>`. Match them with a regex:

```yaml
rules:
  - id: allow-list-allowed-directories
    kind: other
    action: allow
    match: { regex: '^tool:list_allowed_directories\b' }

  - id: no-delete-tools
    kind: other
    action: block
    match: { regex: '^tool:delete_' }
    feedback: Deleting through tools is disabled. Ask the user to do it.
```

Name rules also apply to tools whose arguments were classified (`delete_user` with a
`command` argument is still blocked by the rule above), but only when a rule actually
matches; the ruleset default never applies to the name alone.

## Fail-closed behaviour

- Unparseable JSON lines are rejected with a parse error and **never forwarded**.
- A `tools/call` without a string `name`, sent as a notification, or inside a batch is
  rejected or dropped.
- Any internal error answers the client with an error instead of forwarding.
- A crashing or timing-out judge blocks the action.
- Invalid rules, or an upstream command that cannot be found, stop the proxy at startup
  (exit code 2).

## Limits to know about

- **Only MCP tool calls pass through the proxy.** An agent's *built-in* tools (its own shell,
  file editor, web fetch) do not use MCP and are not covered. They need the agent's
  pre-tool hook mechanism, which is a separate front-end onto the same `Gate`.
- `resources/read` and other MCP methods are forwarded without checks.
- JSON-RPC batches containing `tools/call` are rejected (the current MCP spec has no batching).
- Only the stdio transport is supported, not HTTP/SSE servers.
- Decisions are persisted by default to a local JSONL hash chain.
