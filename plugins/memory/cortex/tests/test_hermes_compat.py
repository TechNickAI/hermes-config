"""Tests for the Hermes runtime compatibility shim.

The cortex plugin must import cleanly in two very different environments:

  * **Inside a Hermes install** — `agent`, `tools`, `hermes_cli`, and
    `hermes_constants` are importable, and the plugin must use the *real*
    implementations so it integrates with the live agent loop.
  * **From a bare clone** — none of those exist. The plugin still has to import
    so `pytest` works without installing the agent first.

`hermes_compat` is the single seam between those worlds. These tests pin the
fallback behaviour to the upstream contract, so a future edit can't silently
drift the stub away from what Hermes actually does.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

import hermes_compat  # noqa: E402
from hermes_compat import (  # noqa: E402
    HERMES_RUNTIME,
    MemoryProvider,
    cfg_get,
    display_hermes_home,
    get_hermes_home,
    tool_error,
)


def test_exports_are_present_in_either_environment() -> None:
    """Every name the plugin imports resolves, with or without Hermes installed."""
    assert isinstance(HERMES_RUNTIME, bool)
    for name in hermes_compat.__all__:
        assert hasattr(hermes_compat, name), f"missing export: {name}"


def test_memory_provider_is_subclassable() -> None:
    """The plugin subclasses MemoryProvider; that must work in both worlds.

    The real Hermes base class declares several abstract methods, so this dummy
    implements the full required surface — the same one the cortex provider
    implements. If upstream adds a new abstract method, this test fails loudly
    rather than the plugin breaking at agent startup.
    """

    class Dummy(MemoryProvider):
        @property
        def name(self) -> str:
            return "dummy"

        def is_available(self) -> bool:
            return True

        def initialize(self, session_id: str = "", **kwargs) -> None:
            return None

        def get_tool_schemas(self) -> list:
            return []

        def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
            return ""

        def shutdown(self) -> None:
            return None

    dummy = Dummy()
    assert dummy.name == "dummy"
    assert dummy.is_available() is True
    assert dummy.get_tool_schemas() == []


def test_tool_error_matches_upstream_json_shape() -> None:
    assert json.loads(tool_error("file not found")) == {"error": "file not found"}
    assert json.loads(tool_error("bad input", success=False)) == {
        "error": "bad input",
        "success": False,
    }


def test_cfg_get_traverses_and_defaults_safely() -> None:
    cfg = {"plugins": {"cortex": {"prefetch_limit": 5}}}
    assert cfg_get(cfg, "plugins", "cortex", "prefetch_limit") == 5
    assert cfg_get(cfg, "plugins", "cortex") == {"prefetch_limit": 5}
    # Missing key at any depth returns the default rather than raising.
    assert cfg_get(cfg, "plugins", "nope", default={}) == {}
    assert cfg_get(cfg, "plugins", "cortex", "nope", default=7) == 7
    # A non-dict where a section was expected must not raise AttributeError.
    assert cfg_get({"plugins": "oops"}, "plugins", "cortex", default={}) == {}
    # None config is a documented input.
    assert cfg_get(None, "plugins", default="fallback") == "fallback"


def test_hermes_home_honours_env_var(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_HOME", "/opt/hermes-custom")
    assert get_hermes_home() == Path("/opt/hermes-custom")
    assert display_hermes_home() == "/opt/hermes-custom"


def test_hermes_home_defaults_under_home(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_HOME", raising=False)
    home = get_hermes_home()
    assert home.is_absolute()
    # Real Hermes uses platform-native paths; both it and the stub land under
    # the user's home directory on Linux/macOS, which is what callers rely on.
    assert str(home).startswith(str(Path.home()))
