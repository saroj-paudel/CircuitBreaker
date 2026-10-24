# CircuitBreaker

Local-first security proxy that sits between an AI coding agent and your machine.
Deterministic YAML rules first, AI risk judge only for ambiguous actions,
tamper-evident audit log for every decision.

> Status: early development. See CHECKPOINT.md.

## Quickstart

See the [quickstart guide](docs/quickstart.md) for installation, MCP setup, hooks, and audit
verification.

Install CircuitBreaker into the Python environment used by your agent:

```bash
python -m pip install circuitbreaker
circuitbreaker --help
```

To protect an MCP server, run it through the proxy:

```bash
circuitbreaker run --mode monitor -- npx -y @modelcontextprotocol/server-filesystem .
```

Start with `--mode monitor` to review decisions without blocking tool calls. Switch to the
default `enforce` mode once your rules are ready:

```bash
circuitbreaker run --rules examples/rules.yaml -- npx -y @modelcontextprotocol/server-filesystem .
```

The default policy is used unless `--rules` is supplied. A custom rules file replaces (it
does not extend) the built-in policy; see [examples/rules.yaml](examples/rules.yaml) and
[the rule guide](docs/rules.md).

The MCP proxy and agent hooks write a tamper-evident decision log by default. Verify it:

```bash
circuitbreaker-audit verify
```
```

The unified command also supports `circuitbreaker audit verify`, `circuitbreaker hook
--agent claude_code`, and `circuitbreaker rules shell "git status"`. Full setup and data
handling details are in [the proxy guide](docs/proxy.md) and
[the agent hooks guide](docs/hooks.md).

An optional OpenAI-compatible risk judge can resolve ambiguous actions. It is disabled
unless configured and receives action targets/arguments when enabled; see the
[judge setup](docs/proxy.md#optional-risk-judge) before using a remote endpoint.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest
```
