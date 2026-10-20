"""Cortex must work on a runtime that ships ruamel.yaml but NOT PyYAML.

Hermes' managed runtime depends on ruamel.yaml only. Cortex imported PyYAML
directly, so on that runtime writes raised "No module named 'yaml'" and reads
degraded silently: frontmatter parsing swallowed the ImportError and indexed
every page with empty metadata.

Each test runs in a REAL subprocess with PyYAML made unimportable. An
in-process test cannot hide an already-imported module reliably, and the
failure being guarded only exists when the import genuinely fails.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]

pytest.importorskip("ruamel.yaml")

# Block PyYAML for the child: a meta-path finder that refuses `yaml`.
_BLOCK = textwrap.dedent(
    """
    import sys
    class _NoPyYAML:
        def find_spec(self, name, path=None, target=None):
            if name == "yaml" or name.startswith("yaml."):
                raise ModuleNotFoundError("No module named 'yaml'", name="yaml")
            return None
    sys.meta_path.insert(0, _NoPyYAML())
    sys.modules.pop("yaml", None)
    """
)


def _run(body: str, tmp_path: Path) -> dict:
    script = _BLOCK + textwrap.dedent(
        f"""
        sys.path.insert(0, {str(PLUGIN_DIR)!r})
        import json
        try:
            import yaml  # noqa: F401
            raise SystemExit("PyYAML was not blocked; test would prove nothing")
        except ModuleNotFoundError:
            pass
        """
    ) + textwrap.dedent(body)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_write_then_read_keeps_frontmatter_without_pyyaml(tmp_path):
    store = tmp_path / "cortex"
    out = _run(
        f"""
        import yaml_compat
        from store import CortexStore
        st = CortexStore(store_path={str(store)!r})
        st.write_page("topics", "garden-notes", "Body text.", tags=["plants", "season"], title="Garden notes: spring")
        page = next(store_path for store_path in __import__("pathlib").Path({str(store)!r}).rglob("*.md"))
        text = page.read_text()
        row = st._get_conn().execute("SELECT title, tags FROM pages").fetchone()
        print(json.dumps({{"backend": yaml_compat.BACKEND, "text": text,
                           "title": row["title"], "tags": row["tags"]}}))
        """,
        tmp_path,
    )
    assert out["backend"] == "ruamel"
    assert out["text"].startswith("---\n")
    assert "%YAML" not in out["text"], "ruamel's directive header leaked into the page"
    assert out["title"] == "Garden notes: spring"
    assert "plants" in out["tags"] and "season" in out["tags"]


def test_existing_pyyaml_style_frontmatter_parses_the_same(tmp_path):
    """Pages already on disk were written by PyYAML (YAML 1.1 rules)."""
    out = _run(
        """
        import datetime
        from store import _parse_frontmatter
        fm, body = _parse_frontmatter(
            "---\\ntitle: X\\ndate: 2026-01-02\\npublished: yes\\ntags: [a, b]\\n---\\n\\nbody\\n")
        print(json.dumps({"title": fm.get("title"),
                          "date_is_date": isinstance(fm.get("date"), datetime.date),
                          "published": fm.get("published"), "tags": fm.get("tags")}))
        """,
        tmp_path,
    )
    assert out == {"title": "X", "date_is_date": True, "published": True, "tags": ["a", "b"]}


def test_plugin_config_loads_without_pyyaml(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("plugins:\n  cortex:\n    prefetch_limit: 7\n")
    out = _run(
        f"""
        import os
        os.environ["HERMES_HOME"] = {str(home)!r}
        sys.path.insert(0, {str(PLUGIN_DIR / "tests")!r})
        from plugin_loader import load_cortex_plugin
        mod = load_cortex_plugin()
        print(json.dumps(mod._load_plugin_config()))
        """,
        tmp_path,
    )
    assert out == {"prefetch_limit": 7}
