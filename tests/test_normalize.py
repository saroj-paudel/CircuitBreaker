import pytest

from circuitbreaker.rules.normalize import (
    glob_match,
    matches_patterns,
    normalize_host,
    normalize_path,
    normalize_sql,
    parse_shell,
)


@pytest.mark.parametrize(
    ("value", "pattern", "expected"),
    [
        (".env", "**/.env", True),
        ("a/b/.env", "**/.env", True),
        ("/a/.env", "**/.env", True),
        (".env.local", "**/.env", False),
        ("~/.ssh", "**/.ssh/**", True),
        ("~/.ssh/id_rsa", "**/.ssh/**", True),
        ("src/app.py", "*", False),
        ("app.py", "*", True),
        ("/etc", "/etc/**", True),
        ("/etc/passwd", "/etc/**", True),
        ("/etcetera", "/etc/**", False),
        ("mkfs.ext4", "mkfs*", True),
        ("KEY.PEM", "**/*.pem", True),
        ("*", r"\*", True),
        ("build", r"\*", False),
    ],
)
def test_glob_match(value, pattern, expected):
    assert glob_match(value, pattern) is expected


def test_negative_patterns_exclude():
    pats = ["**/.env.*", "!**/.env.example"]
    assert matches_patterns("a/.env.prod", pats)
    assert not matches_patterns("a/.env.example", pats)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("//", "/"),
        ("/./", "/"),
        ("/../..", "/"),
        ("~/", "~"),
        ("$HOME", "~"),
        ("${HOME}/x", "~/x"),
        ("/home/tester/.ssh/id_rsa", "~/.ssh/id_rsa"),
        ("./build", "build"),
        ("src/../.env", ".env"),
        ("C:\\Users\\me\\.ssh\\id_rsa", "C:/Users/me/.ssh/id_rsa"),
    ],
)
def test_normalize_path(raw, expected):
    assert normalize_path(raw) == expected


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://PyPI.org/simple", "pypi.org"),
        ("http://github.com@evil.com/", "evil.com"),
        ("http://2852039166/", "169.254.169.254"),
        ("http://0xA9FEA9FE/", "169.254.169.254"),
        ("http://[::ffff:169.254.169.254]/", "169.254.169.254"),
        ("http://metadata.google.internal./x", "metadata.google.internal"),
        ("example.com/path", "example.com"),
    ],
)
def test_normalize_host(url, host):
    assert normalize_host(url) == host


def test_normalize_sql_strips_comments_and_splits():
    result = normalize_sql("SELECT 1; DROP/**/TABLE  users -- bye")
    assert result == ["select 1", "drop table users"]
    assert normalize_sql("/*!50000 DROP TABLE users */") == ["drop table users"]


def one(cmd):
    parsed = parse_shell(cmd)
    assert parsed.ok, parsed.problem
    return parsed.segments


@pytest.mark.parametrize(
    "cmd",
    ["rm -rf /", "rm  -fr  /", "/bin/rm -r -f /", "rm --recursive --force /", "\\rm -rf /",
     "'rm' -rf '/'", "r''m -rf /", "rm / -rf", "rm -rf -- /", "RM -RF /"],
)  # fmt: skip
def test_rm_variants_normalise_identically(cmd):
    (seg,) = one(cmd)
    assert seg.program == "rm"
    assert {"r"} <= seg.flags
    assert seg.args == ("/",)


def test_wrappers_are_stripped_and_recorded():
    (seg,) = one("sudo -u root env FOO=1 rm -rf /")
    assert seg.program == "rm"
    assert seg.wrappers == ("sudo", "env")


def test_compound_commands_split():
    progs = [s.program for s in one("ls && pwd; echo hi | wc -l\nwhoami")]
    assert progs == ["ls", "pwd", "echo", "wc", "whoami"]


def test_nested_shell_and_substitution_are_parsed():
    assert "rm" in [s.program for s in one("bash -c 'echo ok; rm -rf /'")]
    assert "rm" in [s.program for s in one('echo "$(rm -rf /)"')]
    assert "rm" in [s.program for s in one("echo `rm -rf /`")]
    assert "rm" in [s.program for s in one("eval 'rm -rf /'")]


def test_redirects_are_captured_not_args():
    parsed = parse_shell("echo hi > out.txt 2>&1 < in.txt")
    assert parsed.redirect_writes == ["out.txt"]
    assert parsed.redirect_reads == ["in.txt"]
    assert "out.txt" not in parsed.segments[0].args


def test_unbalanced_quote_is_not_ok():
    assert not parse_shell("echo 'oops").ok


def test_variable_program_is_dynamic():
    (seg,) = one("$CMD -rf /")
    assert seg.dynamic


def test_home_variable_is_not_dynamic():
    (seg,) = one("ls $HOME")
    assert not seg.dynamic
