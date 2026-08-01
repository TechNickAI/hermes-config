"""Guard tests for the dry-run harness.

`curate_run` promises the live store is never modified, and then calls
`shutil.rmtree` on its workspace. An overlapping `--workspace`/`--label` (or an
absolute `--label`, which discards the workspace prefix) would delete the very
store being audited.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "curate_run.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=120,
    )


class TestWorkspaceContainment:
    def test_overlapping_workspace_is_refused(self, tmp_path):
        live = tmp_path / "mystore"
        live.mkdir()
        (live / "note.md").write_text("PRECIOUS\n")

        result = _run("--store", str(live), "--label", "mystore",
                      "--workspace", str(tmp_path))

        assert result.returncode == 2
        assert "refusing to run" in result.stdout
        assert (live / "note.md").read_text() == "PRECIOUS\n"

    def test_workspace_equal_to_store_is_refused(self, tmp_path):
        live = tmp_path / "store"
        live.mkdir()
        (live / "note.md").write_text("PRECIOUS\n")

        result = _run("--store", str(live), "--label", ".",
                      "--workspace", str(live))

        assert result.returncode == 2
        assert (live / "note.md").read_text() == "PRECIOUS\n"

    def test_disjoint_workspace_is_allowed(self, tmp_path):
        live = tmp_path / "store"
        live.mkdir()
        (live / "note.md").write_text("# Note\n\nprose\n")

        result = _run("--store", str(live), "--label", "run",
                      "--workspace", str(tmp_path / "work"))

        assert result.returncode == 0
        assert (live / "note.md").exists()
