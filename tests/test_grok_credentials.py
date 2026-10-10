"""Credential resolution stays local and never requires a network call."""
import argparse
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/grok-search/scripts/grok_search.py"


def load_script(path):
    spec = importlib.util.spec_from_file_location("grok_credentials", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("layout", ["grok-search", "research/grok-search"])
def test_profile_file_fallbacks(tmp_path, monkeypatch, layout):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    profile = tmp_path / "profile"
    installed = profile / "skills" / layout / "scripts/grok_search.py"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(SCRIPT.read_bytes())
    module = load_script(installed)
    args = argparse.Namespace(api_key=None)
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(explicit))
    (explicit / ".env").write_text("export XAI_API_KEY = 'explicit-value'\n")
    (profile / ".env").write_text('XAI_API_KEY="installed-value"\n')
    assert module._api_key(args) == "explicit-value"
    (explicit / ".env").write_text("XAI_API_KEY=''\n")
    assert module._api_key(args) == "installed-value"
    (explicit / ".env").unlink()
    assert module._api_key(args) == "installed-value"
    (profile / ".env").unlink()
    monkeypatch.delenv("HERMES_HOME")
    default = tmp_path / "user/.hermes"
    default.mkdir(parents=True)
    (default / ".env").write_text("XAI_API_KEY=default-value\n")
    assert module._api_key(args) == "default-value"


def test_explicit_credentials_precede_files(tmp_path, monkeypatch):
    module = load_script(SCRIPT)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / ".env").write_text("XAI_API_KEY=file-value\n")
    monkeypatch.setenv("XAI_API_KEY", "env-value")
    assert module._api_key(argparse.Namespace(api_key=None)) == "env-value"
    assert module._api_key(argparse.Namespace(api_key="flag-value")) == "flag-value"
