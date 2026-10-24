# CircuitBreaker

Local-first security proxy that sits between an AI coding agent and your machine.
Deterministic YAML rules first, AI risk judge only for ambiguous actions,
tamper-evident audit log for every decision.

> Status: early development. See CHECKPOINT.md.

## Try the rule engine

```bash
python -m circuitbreaker.rules shell "rm  -fr  /"          # block
python -m circuitbreaker.rules shell "bash -c 'ls; rm -rf /'"  # block
python -m circuitbreaker.rules shell "git status"          # allow
python -m circuitbreaker.rules shell "pip install requests" # ask (AI judge, coming in M5)
python -m circuitbreaker.rules file_read ".env"            # block
python -m circuitbreaker.rules http "http://2852039166/"   # block (cloud metadata IP)
python -m circuitbreaker.rules sql "DELETE FROM users"     # block
```

See [docs/rules.md](docs/rules.md) for the rule format.

## Protect an MCP server

Put the proxy in front of any stdio MCP server (put the original command after `--`):

```bash
circuitbreaker-proxy -- npx -y @modelcontextprotocol/server-filesystem .
circuitbreaker-proxy --mode monitor -- <server command>   # log only, to tune rules first
```

See [docs/proxy.md](docs/proxy.md) for agent configuration, options and limits.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest
```
