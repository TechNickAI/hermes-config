"""Regression tests for cortex read on YAML date-valued frontmatter.

Bare YAML scalars such as ``created: 2026-06-01`` are parsed by
``yaml.safe_load`` as ``datetime.date`` objects. The cortex read tool must return
JSON-serializable data, so those values need to surface as deterministic strings
rather than raw Python date objects.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Import the plugin as a package so the provider exercises the real tool-call
# read path in __init__.py instead of only the lower-level store API.
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from plugins.memory.cortex import CortexMemoryProvider  # noqa: E402


def test_read_date_valued_frontmatter_returns_json_serializable_strings(tmp_path: Path) -> None:
    store_path = tmp_path / "cortex"
    provider = CortexMemoryProvider(config={"store_path": str(store_path), "semantic": False})
    provider.initialize("test-session", hermes_home=str(tmp_path / "hermes-home"))

    page_path = store_path / "topics" / "date-frontmatter.md"
    page_path.write_text(
        """---
title: Date Frontmatter Regression
created: 2026-06-01
date: 2026-05-10
last_compiled: 2026-05-31
metadata:
  compiled_on: 2026-05-31
  review:
    due: 2026-06-15
---

This page intentionally uses bare YAML date scalars.
""",
        encoding="utf-8",
    )

    try:
        raw = provider.handle_tool_call(
            "cortex",
            {"action": "read", "rel_path": "topics/date-frontmatter.md"},
        )
        payload = json.loads(raw)

        assert "error" not in payload
        frontmatter = payload["frontmatter"]
        assert frontmatter["created"] == "2026-06-01"
        assert frontmatter["date"] == "2026-05-10"
        assert frontmatter["last_compiled"] == "2026-05-31"
        assert frontmatter["metadata"]["compiled_on"] == "2026-05-31"
        assert frontmatter["metadata"]["review"]["due"] == "2026-06-15"

        # Explicitly prove the successful response can be serialized again by any
        # caller without a custom JSON encoder.
        assert json.loads(json.dumps(payload))["frontmatter"]["created"] == "2026-06-01"
    finally:
        provider.shutdown()
