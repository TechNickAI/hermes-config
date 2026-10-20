"""One YAML entry point for the plugin, whichever library the runtime ships.

Cortex used PyYAML (``import yaml``) directly. Hermes' managed runtime ships
``ruamel.yaml`` as its YAML dependency and does NOT ship PyYAML, so on any
gateway running that runtime every ``import yaml`` raised ImportError:

* writes failed loudly ("No module named 'yaml'"), and
* reads failed SILENTLY: frontmatter parsing catches the error, so every page
  indexed with empty frontmatter (no title, tags, or date), and the plugin's
  own config load returned ``{}``.

Prefer PyYAML when present (existing behavior, byte-identical output) and fall
back to ruamel.yaml's pure-Python safe loader pinned to YAML 1.1, which matches
PyYAML's scalar resolution (``2026-01-02`` -> date, ``yes`` -> True). Only the
three calls the plugin uses are exposed.
"""

from __future__ import annotations

import io
from typing import Any

try:
    import yaml as _pyyaml
except ImportError:  # managed Hermes runtime: ruamel.yaml only
    _pyyaml = None

BACKEND = "pyyaml" if _pyyaml is not None else "ruamel"

if _pyyaml is None:
    from ruamel.yaml import YAML as _RuamelYAML

    def _ruamel() -> Any:
        y = _RuamelYAML(typ="safe", pure=True)
        y.version = (1, 1)
        y.default_flow_style = False
        y.allow_unicode = True
        return y


def safe_load(stream: Any) -> Any:
    if _pyyaml is not None:
        return _pyyaml.safe_load(stream)
    return _ruamel().load(stream)


def safe_dump(data: Any, stream: Any = None, **kwargs: Any) -> str | None:
    if _pyyaml is not None:
        return _pyyaml.safe_dump(data, stream, **kwargs)
    y = _ruamel()
    if "default_flow_style" in kwargs:
        y.default_flow_style = kwargs["default_flow_style"]
    if stream is not None:
        y.dump(data, stream)
        return None
    buf = io.StringIO()
    y.dump(data, buf)
    text = buf.getvalue()
    # ruamel pinned to YAML 1.1 emits an explicit "%YAML 1.1\n---\n" header.
    # Frontmatter is wrapped in our own "---" fences, so strip it.
    if text.startswith("%YAML"):
        text = text.split("\n---", 1)[1].lstrip("\n").lstrip(" ")
        if text.startswith("\n"):
            text = text[1:]
    return text


def dump(data: Any, stream: Any = None, **kwargs: Any) -> str | None:
    """Config writes only ever contain plain dicts/lists/scalars: safe dump suffices."""
    return safe_dump(data, stream, **kwargs)
