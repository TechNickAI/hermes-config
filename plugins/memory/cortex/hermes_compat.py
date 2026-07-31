"""Hermes runtime imports, with test-time fallbacks.

At runtime inside a Hermes install, `agent`, `tools`, and `hermes_cli` are all
importable and this module is a thin re-export — the plugin gets the real
`MemoryProvider` base class, the real `tool_error`, and the real `cfg_get`.

Outside a Hermes install (a fresh `git clone` + `pytest`, or CI), those modules
do not exist. Rather than making the whole package unimportable — which would
mean nobody can run the test suite without installing the agent first — we fall
back to behaviour-equivalent local definitions. They are deliberately tiny and
mirror the upstream contract:

  * ``MemoryProvider`` — abstract base with the lifecycle hooks the plugin
    overrides. The fallback is a plain ABC; the plugin never calls ``super()``
    for behaviour, only for the interface.
  * ``tool_error`` — returns the same ``{"error": ...}`` JSON string shape.
  * ``cfg_get`` — safe nested-dict traversal returning ``default`` on any miss.
  * ``get_hermes_home`` / ``display_hermes_home`` — Hermes home resolution,
    honouring ``$HERMES_HOME`` and defaulting to ``~/.hermes``.

``HERMES_RUNTIME`` records which path was taken so tests (and anyone debugging)
can assert on it.
"""

from __future__ import annotations

import json
import os
from abc import ABC
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - exercised implicitly by whichever env runs the tests
    from agent.memory_provider import MemoryProvider  # type: ignore
    from tools.registry import tool_error  # type: ignore
    from hermes_cli.config import cfg_get  # type: ignore
    from hermes_constants import get_hermes_home, display_hermes_home  # type: ignore

    HERMES_RUNTIME = True

except ImportError:  # Hermes not installed — standalone / CI / test path
    HERMES_RUNTIME = False

    class MemoryProvider(ABC):  # type: ignore[no-redef]
        """Minimal stand-in for ``agent.memory_provider.MemoryProvider``.

        Mirrors the lifecycle surface the cortex plugin implements. Only used
        when the real Hermes runtime is absent; the plugin overrides every
        method it depends on.
        """

        @property
        def name(self) -> str:
            return "unknown"

        def is_available(self) -> bool:
            return False

        def initialize(self, session_id: str, **kwargs) -> None:
            return None

        def system_prompt_block(self) -> str:
            return ""

        def prefetch(self, query: str, *, session_id: str = "") -> str:
            return ""

        def sync_turn(
            self,
            user_content: str,
            assistant_content: str,
            *,
            session_id: str = "",
            messages: Optional[List[Dict[str, Any]]] = None,
        ) -> None:
            return None

        def get_tool_schemas(self) -> List[Dict[str, Any]]:
            return []

        def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
            return ""

        def shutdown(self) -> None:
            return None

    def tool_error(message: str, **extra: Any) -> str:  # type: ignore[misc]
        """Return a JSON error string for tool handlers.

        >>> tool_error("file not found")
        '{"error": "file not found"}'
        """
        payload: Dict[str, Any] = {"error": message}
        payload.update(extra)
        return json.dumps(payload)

    def cfg_get(cfg: Optional[Dict[str, Any]], *keys: str, default: Any = None) -> Any:  # type: ignore[misc]
        """Traverse nested dict keys safely, returning ``default`` on any miss."""
        current: Any = cfg
        for key in keys:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
        return default if current is None else current

    def get_hermes_home() -> Path:  # type: ignore[misc]
        """Return the Hermes home directory (``$HERMES_HOME`` or ``~/.hermes``)."""
        env = os.environ.get("HERMES_HOME", "").strip()
        return Path(env).expanduser() if env else Path.home() / ".hermes"

    def display_hermes_home() -> str:  # type: ignore[misc]
        """User-friendly display string for the Hermes home, with ``~/`` shorthand."""
        home = get_hermes_home()
        try:
            return f"~/{home.relative_to(Path.home())}"
        except ValueError:
            return str(home)


__all__ = [
    "MemoryProvider",
    "tool_error",
    "cfg_get",
    "get_hermes_home",
    "display_hermes_home",
    "HERMES_RUNTIME",
]
