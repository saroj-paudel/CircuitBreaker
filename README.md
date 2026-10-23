# CircuitBreaker

Local-first security proxy that sits between an AI coding agent and your machine.
Deterministic YAML rules first, AI risk judge only for ambiguous actions,
tamper-evident audit log for every decision.

> Status: early development. See CHECKPOINT.md.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest
```
