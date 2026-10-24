# Quickstart

## Install

Install CircuitBreaker into the same Python environment that launches your agent:

```bash
python -m pip install circuitbreaker
circuitbreaker --help
```

For local development, install the checked-out project with `python -m pip install -e .`.

## Protect an MCP server

First run in monitor mode to see decisions without enforcing the rules:

```bash
circuitbreaker run --mode monitor -- npx -y @modelcontextprotocol/server-filesystem .
```

When the policy is ready, remove `--mode monitor` to enforce it:

```bash
circuitbreaker run -- npx -y @modelcontextprotocol/server-filesystem .
```

Use a custom policy with `--rules`. The file is a complete ruleset, not an overlay on the
built-in rules:

```bash
circuitbreaker run --rules examples/rules.yaml -- npx -y @modelcontextprotocol/server-filesystem .
```

This example blocks some credential paths and broad recursive deletes, allows a few
read-only Git operations, and asks about everything else. Review and adapt it to your
environment. See [rules.md](rules.md) for matching and precedence details.

## Configure an agent

Replace the MCP server command in your agent's configuration with `circuitbreaker`, and
pass the original server command after `--`. For example:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "circuitbreaker",
      "args": ["run", "--", "npx", "-y", "@modelcontextprotocol/server-filesystem", "."]
    }
  }
}
```

If `circuitbreaker` is not on the agent's PATH, use the Python executable from the
environment where the package is installed and invoke `-m circuitbreaker`.

## Audit and hooks

Decisions are written to `~/.circuitbreaker/audit.jsonl` by default. Verify the chain with:

```bash
circuitbreaker audit verify
```

Use the built-in agent hook front-end with:

```bash
circuitbreaker hook --agent claude_code
circuitbreaker hook --agent codex
```

Hook installation details are in [hooks.md](hooks.md).

## Optional risk judge

The judge is disabled by default. If enabled, action targets and arguments are sent to the
configured endpoint. Configure the endpoint and model explicitly, use HTTPS for remote
services, and set the API key through an environment variable. See
[proxy.md](proxy.md#optional-risk-judge) for options, privacy considerations, and limits.
