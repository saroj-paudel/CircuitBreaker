# Quickstart

## Install

CircuitBreaker requires Python 3.10 or newer. Clone the repository and install this checkout
into the same Python environment that launches your agent:

```bash
git clone https://github.com/saroj-paudel/CircuitBreaker.git
cd CircuitBreaker
python -m venv .venv
# PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e .
circuitbreaker --help
```

Choose the activation command for your shell from the comments above. Activation is
optional if you use the environment's full paths. This installs the cloned source in
editable mode; it does not assume a PyPI release exists.

For development and tests, install the additional tools and run the checks:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check .
```

## Verify the clone locally

This smoke test uses the repository's fake MCP server and does not need Node.js, an external
server package, API key, or network service. From the repository root, start:

```bash
circuitbreaker run --mode monitor -- python tests/fake_mcp_server.py
```

Paste this JSON-RPC request into the running process and press Enter:

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"run_command","arguments":{"command":"rm -rf /"}}}
```

The fake server responds with `executed run_command`, demonstrating that monitor mode
forwards the call. Restart without `--mode monitor` and repeat the request; the proxy should
return a blocked tool result. **The fake server never runs the provided shell command**—it
only records whether a tool call reached it. Use Ctrl+C to stop the proxy.

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

For an actual filesystem MCP server, install Node.js first; this example uses `npx` to fetch
`@modelcontextprotocol/server-filesystem`. CircuitBreaker does not install or manage the
upstream server.

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
