# CircuitBreaker

Local-first security proxy for AI coding agents. Deterministic YAML rules run first; an
optional risk judge handles ambiguous actions. The proxy and agent hooks share the same
decision gate and record decisions to a tamper-evident audit log.

> **MVP:** CircuitBreaker is an early-stage security aid, not a sandbox. Review its
> [known limitations](docs/proxy.md#limits-to-know-about) before relying on it.

## Quickstart

See the [clone-to-first-run guide](docs/quickstart.md) for setup, a local smoke test, MCP
configuration, agent hooks, and audit verification.

Clone and install the repository into the Python environment used by your agent:

```bash
git clone https://github.com/saroj-paudel/CircuitBreaker.git
cd CircuitBreaker
python -m venv .venv
# PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e .
circuitbreaker --help
```

Start with monitor mode, then enforce once you have reviewed the rules:

```bash
circuitbreaker run --mode monitor -- npx -y @modelcontextprotocol/server-filesystem .
circuitbreaker run -- npx -y @modelcontextprotocol/server-filesystem .
```

The second command uses the built-in rules. If you pass `--rules`, the supplied YAML is a
complete replacement policy, not an overlay; start from
[examples/rules.yaml](examples/rules.yaml) and review it carefully. See
[docs/rules.md](docs/rules.md) for rule semantics.

## Commands

- `circuitbreaker run -- <server command>` — proxy an MCP stdio server.
- `circuitbreaker hook --agent claude_code|codex` — protect supported built-in agent tools.
- `circuitbreaker rules <kind> <target>` — inspect one rule-engine decision.
- `circuitbreaker audit verify` — verify the local hash-chained audit log.

The OpenAI-compatible risk judge is optional and disabled unless configured. When enabled,
action targets and mapped arguments are sent to the configured endpoint. Read
[docs/proxy.md](docs/proxy.md#optional-risk-judge) before enabling it.

## Development

From the repository root:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m pytest -q
ruff check .
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. On macOS/Linux, use
`source .venv/bin/activate`.
