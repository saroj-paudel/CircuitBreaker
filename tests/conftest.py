import pytest


@pytest.fixture(autouse=True)
def fake_home(monkeypatch):
    monkeypatch.setenv("HOME", "/home/tester")         # Linux / macOS
    monkeypatch.setenv("USERPROFILE", "/home/tester")  # Windows