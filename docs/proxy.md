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
| `--mode enforce` | Default. Blocked and ambiguous calls never reach the server. |
| `--mode monitor` | Decisions are logged to stderr but everything is forwarded. Use it to tune rules on real traffic before enforcing. |
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
4. **ask** (no rule decided) is denied for now with an "needs approval" message. In M5 the
   AI risk judge resolves these.

Tools the mapper cannot classify become `kind: other` and fall to the ruleset default (ask),
so unknown tools are denied until you say otherwise.

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
- Decisions are not persisted yet. M4 adds the tamper-evident audit log through the
  `on_decision` hook.
