import json
import subprocess
import sys

from circuitbreaker import __version__


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "circuitbreaker", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_unified_cli_version_matches_package_version():
    result = run_cli("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == f"circuitbreaker {__version__}"
    assert __version__ == "0.1.0"


def test_unified_cli_help_lists_subcommands():
    result = run_cli("--help")
    assert result.returncode == 0
    for command in ("run", "hook", "audit", "rules"):
        assert command in result.stdout


def test_unified_cli_rules_subcommand():
    result = run_cli("rules", "shell", "git status")
    assert result.returncode == 0
    assert json.loads(result.stdout)["decision"] == "allow"


def test_unified_cli_rejects_unknown_command():
    result = run_cli("unknown")
    assert result.returncode == 2
    assert "unknown command" in result.stderr


def test_unified_cli_routes_hook_help():
    result = run_cli("hook", "--help")
    assert result.returncode == 0
    assert "--agent" in result.stdout


def test_example_rules_are_valid():
    from pathlib import Path

    from circuitbreaker.rules import RuleEngine

    RuleEngine.from_file(Path(__file__).parents[1] / "examples" / "rules.yaml")
