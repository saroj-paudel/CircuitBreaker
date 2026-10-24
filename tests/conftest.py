import pytest


@pytest.fixture(autouse=True)
def fake_home(monkeypatch):
    """Deterministic home directory on every OS (Windows ignores $HOME)."""
    monkeypatch.setattr("circuitbreaker.rules.normalize._home_dir", lambda: "/home/tester")
