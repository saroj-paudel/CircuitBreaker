"""Normalisation: turn messy agent input into canonical forms that rules can match.

This is where "easily bypassed regex" is defeated. `rm -rf /`, `rm  -fr  //`,
`/bin/rm -r -f /`, `sudo rm --recursive --force /` and `bash -c 'rm -rf /'`
all reduce to the same canonical segment: program=rm, flags={r, f}, args=("/",).
"""

from __future__ import annotations

import ipaddress
import os
import posixpath
import re
import shlex
import socket
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlsplit

# --------------------------------------------------------------------------- globs


@lru_cache(maxsize=2048)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """Translate a path-style glob to a regex.

    `*` matches within one path segment, `**` across segments. `**/x` also
    matches `x`, and `dir/**` also matches `dir` itself. `\\*` is a literal star.
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n:  # escaped character: match literally
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if c == "*":
            j = i
            while j < n and pattern[j] == "*":
                j += 1
            if j - i >= 2:
                at_start = i == 0 or pattern[i - 1] == "/"
                if at_start and j < n and pattern[j] == "/":
                    out.append("(?:.*/)?")
                    i = j + 1
                    continue
                if i > 0 and pattern[i - 1] == "/" and j == n:
                    out.pop()  # drop the literal "/" so the directory itself matches
                    out.append("(?:/.*)?")
                    i = j
                    continue
                out.append(".*")
                i = j
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE | re.DOTALL)


def glob_match(value: str, pattern: str) -> bool:
    return _glob_regex(pattern).match(value) is not None


def matches_patterns(value: str, patterns: list[str]) -> bool:
    """True if value matches any positive pattern and no `!negative` pattern."""
    positives = [p for p in patterns if not p.startswith("!")]
    negatives = [p[1:] for p in patterns if p.startswith("!")]
    if not any(glob_match(value, p) for p in positives):
        return False
    return not any(glob_match(value, p) for p in negatives)


# --------------------------------------------------------------------------- paths

_HOME_VAR = re.compile(r"\$(?:\{HOME\}|HOME(?![A-Za-z0-9_]))")


def normalize_path(raw: str) -> str:
    """Canonical path: `$HOME`/home dir -> `~`, `//` and `..` collapsed, `\\` -> `/`."""
    p = raw.strip().replace("\\", "/")
    p = _HOME_VAR.sub("~", p)
    home = os.path.expanduser("~").replace("\\", "/").rstrip("/")
    if home and (p == home or p.startswith(home + "/")):
        p = "~" + p[len(home) :]
    p = re.sub(r"^/{2,}", "/", p)
    return posixpath.normpath(p) if p else p


# --------------------------------------------------------------------------- urls


def normalize_host(url: str) -> str | None:
    """Hostname of a URL, lower-cased, with numeric/hex/IPv6-mapped IPs canonicalised."""
    try:
        host = urlsplit(url if "://" in url else "//" + url).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".").lower()
    try:
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(host)
    except ValueError:
        try:  # decimal / hex / octal IPv4 forms such as 2852039166 or 0xA9FEA9FE
            ip = ipaddress.ip_address(socket.inet_aton(host))
        except (OSError, ValueError):
            return host
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return str(ip)


# --------------------------------------------------------------------------- sql


def normalize_sql(text: str) -> list[str]:
    """Strip comments, collapse whitespace, lower-case, and split into statements."""
    text = re.sub(r"/\*!\d*", " ", text)  # MySQL executable comments: keep the contents
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = text.replace("/*", " ").replace("*/", " ")
    text = re.sub(r"--[^\n]*", " ", text)
    statements = (" ".join(s.split()).lower() for s in text.split(";"))
    return [s for s in statements if s]


# --------------------------------------------------------------------------- shell

_PUNCT = ";()<>|&\n"
_MAX_DEPTH = 5
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_NUMERIC_FLAG = re.compile(r"^-\d+$")
_DYNAMIC_HOME = re.compile(r"\$(?:\{HOME\}|HOME(?![A-Za-z0-9_]))")

# Programs that run another program: stripped so the real command is judged.
_WRAPPERS = {"sudo", "doas", "nohup", "time", "command", "exec", "nice", "env", "stdbuf", "setsid"}
_VALUE_FLAGS = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U"},
    "doas": {"-u", "-C"},
    "nice": {"-n"},
    "env": {"-u", "-C"},
    "time": {"-f", "-o"},
    "stdbuf": {"-i", "-o", "-e"},
}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
# Per-program flag aliases so `--recursive` and `-R` both become `r` for rm.
_ALIASES = {"rm": {"R": "r", "recursive": "r", "force": "f"}}


@dataclass(frozen=True)
class Segment:
    """One simple command, e.g. the `rm -rf /` in `ls && rm -rf /`."""

    program: str
    flags: frozenset[str]
    args: tuple[str, ...]  # positional arguments (flags removed)
    raw_args: tuple[str, ...]  # every token after the program, flags included
    text: str  # program + raw args, for regex rules
    wrappers: tuple[str, ...] = ()  # sudo, env, ... that were stripped
    dynamic: bool = False  # contains an unresolved $VAR or backtick


@dataclass
class ParsedShell:
    segments: list[Segment] = field(default_factory=list)
    redirect_writes: list[str] = field(default_factory=list)
    redirect_reads: list[str] = field(default_factory=list)
    ok: bool = True
    problem: str = ""

    def fail(self, problem: str) -> None:
        if self.ok:
            self.ok, self.problem = False, problem

    def merge(self, other: ParsedShell) -> None:
        self.segments += other.segments
        self.redirect_writes += other.redirect_writes
        self.redirect_reads += other.redirect_reads
        if not other.ok:
            self.fail(other.problem)


def _is_dynamic(token: str) -> bool:
    return "$" in _DYNAMIC_HOME.sub("", token) or "`" in token


def _program_name(token: str) -> str:
    name = posixpath.basename(token.replace("\\", "/")).lower() or token.lower()
    return name[:-4] if name.endswith(".exe") else name


def _lex(text: str) -> list[str]:
    lex = shlex.shlex(text, posix=True, punctuation_chars=_PUNCT)
    lex.whitespace = " \t\r"  # newline is an operator, not whitespace
    lex.whitespace_split = True
    lex.commenters = ""  # treat "#..." as real text: stricter than a real shell
    return list(lex)


def _find_substitutions(text: str) -> list[str]:
    """Inner text of $(...), <(...), >(...) and `...` (even inside quotes)."""
    found: list[str] = []
    for opener in ("$(", "<(", ">("):
        start = 0
        while (i := text.find(opener, start)) != -1:
            start = i + 2
            if text[i + 2 : i + 3] == "(":  # $(( arithmetic ))
                continue
            depth, j = 1, i + 2
            while j < len(text) and depth:
                depth += (text[j] == "(") - (text[j] == ")")
                j += 1
            found.append(text[i + 2 : j - 1 if depth == 0 else j])
    found += re.findall(r"`([^`]*)`", text)
    return found


def _shell_payload(raw_args: list[str]) -> str | None:
    """The string after `-c` (also `-lc`, `-ec`) for sh/bash/zsh invocations."""
    for idx, tok in enumerate(raw_args):
        if tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:]:
            return raw_args[idx + 1] if idx + 1 < len(raw_args) else None
    return None


def _build_segment(tokens: list[str], result: ParsedShell, depth: int) -> None:
    wrappers: list[str] = []
    i, n = 0, len(tokens)
    while i < n:
        if _ENV_ASSIGN.match(tokens[i]):
            wrappers.append("env-assign")
            i += 1
            continue
        name = _program_name(tokens[i])
        if name not in _WRAPPERS:
            break
        wrappers.append(name)
        i += 1
        value_flags = _VALUE_FLAGS.get(name, set())
        while i < n and (
            tokens[i].startswith("-") or (name == "env" and _ENV_ASSIGN.match(tokens[i]))
        ):
            i += 2 if tokens[i] in value_flags and i + 1 < n else 1
    rest = tokens[i:]
    if not rest:
        return

    program = _program_name(rest[0])
    raw_args = rest[1:]
    aliases = _ALIASES.get(program, {})
    flags: set[str] = set()
    args: list[str] = []
    end_of_flags = False
    for tok in raw_args:
        if end_of_flags:
            args.append(tok)
        elif tok == "--":
            end_of_flags = True
        elif tok.startswith("--"):
            name = tok[2:].split("=", 1)[0]
            flags.add(aliases.get(name, name))
        elif tok.startswith("-") and len(tok) > 1 and not _NUMERIC_FLAG.match(tok):
            for ch in tok[1:]:
                if ch == "=":
                    break
                flags.add(aliases.get(ch, ch))
        else:
            args.append(tok)

    result.segments.append(
        Segment(
            program=program,
            flags=frozenset(flags),
            args=tuple(args),
            raw_args=tuple(raw_args),
            text=" ".join([program, *raw_args]),
            wrappers=tuple(wrappers),
            dynamic=any(_is_dynamic(t) for t in rest),
        )
    )

    # Look inside `bash -c "..."` and `eval "..."`.
    payload: str | None = None
    if program in _SHELLS:
        payload = _shell_payload(raw_args)
    elif program == "eval":
        payload = " ".join(raw_args)
    if payload:
        result.merge(parse_shell(payload, depth + 1))


def _record_redirect(op: str, target: str, result: ParsedShell) -> None:
    if op.endswith("&") or op in ("<<", "<<<"):  # fd duplication / heredoc
        return
    if _is_dynamic(target):
        result.fail("redirect target uses a shell variable")
        return
    if ">" in op:
        result.redirect_writes.append(target)
    else:
        result.redirect_reads.append(target)


def parse_shell(text: str, _depth: int = 0) -> ParsedShell:
    """Split a command line into canonical segments, redirects and substitutions."""
    result = ParsedShell()
    if _depth > _MAX_DEPTH:
        result.fail("shell nesting too deep to analyse")
        return result
    try:
        tokens = _lex(text)
    except ValueError as exc:
        result.fail(f"could not parse command: {exc}")
        return result

    for inner in _find_substitutions(text):
        result.merge(parse_shell(inner, _depth + 1))

    current: list[str] = []
    pending: str | None = None
    for tok in [*tokens, "\n"]:  # sentinel flushes the last segment
        if set(tok) <= set(_PUNCT):
            if "<" in tok or ">" in tok:
                pending = tok
                continue
            pending = None
            if current:
                _build_segment(current, result, _depth)
                current = []
        elif pending is not None:
            _record_redirect(pending, tok, result)
            pending = None
        else:
            current.append(tok)
    return result
