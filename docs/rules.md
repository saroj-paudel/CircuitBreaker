# Writing rules

A rule file is YAML. Load the built-in set with `RuleEngine.from_default()` or your own
with `RuleEngine.from_file("my_rules.yaml")`. An invalid file is rejected at load time
with every problem listed; the proxy must refuse to start rather than run unprotected.

```yaml
version: 1
default: ask            # what happens when no rule matches: allow | ask | block

rules:
  - id: no-prod-db                 # unique, lowercase
    description: Never touch the production database.
    kind: shell                    # shell | file_read | file_write | http | sql | other (or a list)
    action: block                  # block | ask | allow
    match:                         # every condition you set must hold (AND)
      program_any: [psql, mysql]
      regex: 'prod'
    feedback: Use the staging database instead.   # shown to the agent when blocked
```

## Precedence

`block` beats `ask` beats `allow` beats `default`, regardless of rule order.
A compound command (`a && b`, pipes, `$(...)`, `bash -c '...'`) is allowed only if every
part is allowed. Anything the engine cannot analyse safely (unbalanced quotes, `$VAR` as
program, `sudo`/`env` wrappers) can never be auto-allowed: it becomes `ask`.

## Match fields

| Field | Kinds | Meaning |
|---|---|---|
| `program_any` | shell | Program name globs after normalisation (`/bin/rm`, `\rm`, `RM` all become `rm`) |
| `flags_all` / `flags_any` | shell | Flags after splitting clusters: `-rf` is `r` and `f`. For `rm`, `-R` and `--recursive` are `r` |
| `subcommand_any` | shell | First positional argument, e.g. `git` + `[push]` |
| `args_any` | shell | Path globs on positional arguments (paths are normalised first) |
| `tokens_any` | shell | Globs on every raw token, for odd flags like `find -delete` |
| `path_any` | file_read, file_write | Path globs |
| `host_any`, `method_any` | http | Host globs (IPs canonicalised) and HTTP methods |
| `regex` | shell, http, sql, other | Case-insensitive regex. SQL is matched per statement, comments stripped |

## Globs

`*` one path segment, `**` any depth, `?` one character, `!pattern` excludes, `\*` a literal star.
`**/.env` also matches `.env`; `dir/**` also matches `dir`.
Exclusions apply per item, so `cat .env .env.example` is still caught:

```yaml
path_any: ["**/.env.*", "!**/.env.example"]
```

## Reuse with anchors

Top-level keys starting with `x-` are ignored by the loader, so you can share lists:

```yaml
x-secrets: &secrets ["**/.env", "**/.ssh/**"]
rules:
  - {id: a, kind: file_read, action: block, match: {path_any: *secrets}}
```

## Known limits (by design, for now)

- POSIX shell syntax only. PowerShell/cmd are not parsed yet.
- Symlinks are not resolved, and shell variables other than `$HOME` are not expanded
  (such commands are never auto-allowed).
- Regex rules are written by you: avoid patterns with catastrophic backtracking.
- SQL rules are text-based. `'drop table x'` inside a string literal can trigger a false block.
