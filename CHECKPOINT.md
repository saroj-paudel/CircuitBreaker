# Build checkpoints

- [x] **M1 Foundation**: packaging, CI, core models (`ToolCall`, `Verdict`, `Decision`), `BlockedError`
- [x] **M2 YAML rule engine**: load rules, match shell/file/http/sql, normalise commands
- [x] **M3 Proxy skeleton**: MCP stdio proxy, tool-call mapping, forward or block, monitor mode
- [x] **M3b Agent hooks**: pre-tool hook front-end for Claude Code / Antigravity and Codex, reusing `Gate`
- [x] **M4 Audit log**: append-only JSONL, hash-chained, `verify` command
- [x] **M5 AI risk judge**: pluggable LLM client, JSON verdict, fail-closed (OpenAI-compatible transport; model/provider choice deferred)
- [x] **M6 CLI + docs**: `circuitbreaker run`, quickstart, example rules, v0.1.0

## Proposed additions
- [x] **CI on Windows**: add `windows-latest` to the test matrix
- [ ] **M3b Agent hooks**: front-end for agents' built-in tools (shell/file), reusing `Gate`
- [ ] **Windows shells**: PowerShell and cmd parsing, Windows path rules
