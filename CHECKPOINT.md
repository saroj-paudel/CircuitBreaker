# Build checkpoints

- [x] **M1 Foundation**: packaging, CI, core models (`ToolCall`, `Verdict`, `Decision`), `BlockedError`
- [ ] **M2 YAML rule engine**: load rules, match shell/file/http/sql, normalise commands
- [ ] **M3 Proxy skeleton**: intercept a tool call, route to engine, forward or block
- [ ] **M4 Audit log**: append-only JSONL, hash-chained, `verify` command
- [ ] **M5 AI risk judge**: pluggable LLM client, JSON verdict, fail-closed
- [ ] **M6 CLI + docs**: `circuitbreaker run`, quickstart, example rules, v0.1.0
