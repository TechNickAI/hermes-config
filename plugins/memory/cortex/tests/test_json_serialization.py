"""Regression coverage for JSON-safe cortex tool responses."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the plugin importable without installing the repo as a package.
PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from serialization import dumps_response  # noqa: E402
from store import CortexStore  # noqa: E402


def test_dumps_response_converts_frontmatter_dates_to_iso_strings(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    try:
        page_path = tmp_path / "cortex" / "topics" / "date-page.md"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(
            "---\n"
            "title: Date Page\n"
            "created: 2026-05-10\n"
            "last_compiled: 2026-05-10T12:34:56\n"
            "tags: [regression]\n"
            "---\n"
            "Body text\n",
            encoding="utf-8",
        )

        page = store.get_page("topics/date-page.md")
        assert page is not None

        # yaml.safe_load parses bare dates into date/datetime objects, which the
        # standard JSON encoder cannot serialize directly. Cortex responses must
        # normalize those values at the tool response boundary.
        with pytest.raises(TypeError):
            json.dumps(page)

        payload = json.loads(dumps_response(page))
        assert payload["frontmatter"]["created"] == "2026-05-10"
        assert payload["frontmatter"]["last_compiled"] == "2026-05-10T12:34:56"
        assert payload["rel_path"] == "topics/date-page.md"
        assert payload["body"] == "Body text\n"
    finally:
        store.close()
