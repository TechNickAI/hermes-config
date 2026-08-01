"""Tests for the knowledge-store junk detector.

The discrimination that matters: a category holding hundreds of legitimate
markdown notes is healthy and must not be flagged, while a vendored application
bundle or build tree inside the store must be.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from junk_detector import scan  # noqa: E402


def kinds(result: dict) -> set[str]:
    return {finding["kind"] for finding in result["findings"]}


class TestHealthyStore:
    def _build(self, root: Path) -> Path:
        store = root / "clean"
        for index in range(60):
            path = store / "daily" / ("2026-01-%02d.md" % (index % 28 + 1))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Note %d\n\nprose\n" % index)
        for index in range(40):
            path = store / "people" / ("person%02d.md" % index)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Person %d\n\nprose\n" % index)
        return store

    def test_prose_store_reports_no_findings(self, tmp_path):
        result = scan(self._build(tmp_path))
        assert result["findings"] == []
        assert result["totals"]["noise_pct"] == 0

    def test_large_markdown_category_is_not_a_dump(self, tmp_path):
        assert "dump_directory" not in kinds(scan(self._build(tmp_path)))


class TestVendoredBundle:
    def _build(self, root: Path) -> Path:
        store = root / "dirty"
        bundle = store / "projects" / "thing" / "artifacts" / "unzipped" / "App.app" / "Contents"
        bundle.mkdir(parents=True)
        (store / "notes.md").write_text("# Real note\n\nprose\n")
        for index in range(120):
            (bundle / ("Localizable%03d.strings" % index)).write_text("x" * 200)
        for index in range(30):
            (bundle / ("image%02d.tiff" % index)).write_bytes(b"\x00" * 500)
        return store

    def test_junk_extensions_are_counted(self, tmp_path):
        result = scan(self._build(tmp_path))
        assert result["junk"]["files"] >= 150
        assert result["totals"]["noise_pct"] > 90

    def test_bundle_is_reported_for_human_attention(self, tmp_path):
        result = scan(self._build(tmp_path))
        assert {"junk_files", "dump_directory"} <= kinds(result)
        assert any(f["severity"] == "needs_human" for f in result["findings"])


class TestBuildArtifacts:
    def test_compiled_output_is_flagged(self, tmp_path):
        store = tmp_path / "build"
        (store / "__pycache__").mkdir(parents=True)
        (store / "notes.md").write_text("# note\n")
        for index in range(60):
            (store / "__pycache__" / ("mod%02d.pyc" % index)).write_bytes(b"\x00" * 300)

        assert "junk_files" in kinds(scan(store))


class TestLargeFiles:
    def test_large_page_is_agent_severity_not_human(self, tmp_path):
        store = tmp_path / "large"
        store.mkdir()
        (store / "note.md").write_text("# n\n")
        (store / "dump.md").write_text("x" * (6 * 1024 * 1024))

        findings = [f for f in scan(store)["findings"] if f["kind"] == "large_files"]
        assert findings and all(f["severity"] == "agent" for f in findings)


class TestEdgeCases:
    def test_empty_store_is_safe(self, tmp_path):
        store = tmp_path / "empty"
        store.mkdir()

        result = scan(store)
        assert result["totals"]["files"] == 0
        assert result["findings"] == []
