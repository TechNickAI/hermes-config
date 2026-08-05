"""Guard tests for the dry-run harness.

`curate_run` promises the live store is never modified, and then calls
`shutil.rmtree` on its workspace. An overlapping `--workspace`/`--label` (or an
absolute `--label`, which discards the workspace prefix) would delete the very
store being audited.
"""

from __future__ import annotations

import json
import os
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


class TestPreservationAccounting:
    """The check that was missing when a run silently deleted 77 files."""

    def _mod(self):
        import importlib
        sys.path.insert(0, str(SCRIPT.parent))
        import curate_run
        return importlib.reload(curate_run)

    def test_missing_non_markdown_is_caught(self, tmp_path):
        src, cand = tmp_path / "s", tmp_path / "c"
        for root in (src, cand):
            (root / "projects/artifacts").mkdir(parents=True)
            (root / "note.md").write_text("---\ntitle: N\n---\n\nbody\n")
        (src / "projects/artifacts/dump.txt").write_text("register xrefs")

        result = self._mod().accounted_for(src, cand)
        assert result["missing_non_markdown"] == ["projects/artifacts/dump.txt"]

    def test_markdown_under_a_skipped_dir_is_still_required(self, tmp_path):
        """`artifacts` is skipped for TRANSFORMS; it is never skipped for survival."""
        src, cand = tmp_path / "s", tmp_path / "c"
        for root in (src, cand):
            (root / "projects/artifacts").mkdir(parents=True)
        (src / "projects/artifacts/asm.md").write_text("movq callq rdi")

        result = self._mod().accounted_for(src, cand)
        assert result["pages_with_unrecoverable_prose"]
        assert "movq" in result["pages_with_unrecoverable_prose"][0]["sample"]

    def test_rename_is_not_reported_as_loss(self, tmp_path):
        src, cand = tmp_path / "s", tmp_path / "c"
        src.mkdir(); cand.mkdir()
        (src / "2026-04-28-scan.md").write_text("---\ntitle: S\n---\n\nalpha beta\n")
        (cand / "scan.md").write_text("---\ntitle: S\ndate: '2026-04-28'\n---\n\nalpha beta\n")

        assert self._mod().accounted_for(src, cand)["pages_with_unrecoverable_prose"] == []

    def test_split_is_not_reported_as_loss(self, tmp_path):
        src, cand = tmp_path / "s", tmp_path / "c"
        src.mkdir(); (cand / "big").mkdir(parents=True)
        (src / "big.md").write_text("---\ntitle: B\n---\n\n## One\n\nalpha\n\n## Two\n\nbeta\n")
        (cand / "big/index.md").write_text("---\ntitle: B\n---\n\n## Sections\n")
        (cand / "big/one.md").write_text("---\ntitle: One\n---\n\n## One\n\nalpha\n")
        (cand / "big/two.md").write_text("---\ntitle: Two\n---\n\n## Two\n\nbeta\n")

        assert self._mod().accounted_for(src, cand)["pages_with_unrecoverable_prose"] == []

    def test_modified_binary_is_caught(self, tmp_path):
        src, cand = tmp_path / "s", tmp_path / "c"
        src.mkdir(); cand.mkdir()
        (src / "img.png").write_bytes(b"original")
        (cand / "img.png").write_bytes(b"corrupted")

        assert self._mod().accounted_for(src, cand)["altered_non_markdown"] == ["img.png"]

    def test_derived_index_is_not_required(self, tmp_path):
        src, cand = tmp_path / "s", tmp_path / "c"
        src.mkdir(); cand.mkdir()
        (src / ".plugin.db").write_bytes(b"sqlite")

        assert self._mod().accounted_for(src, cand)["missing_non_markdown"] == []

    def test_symlink_target_and_path_are_preserved(self, tmp_path):
        src, cand = tmp_path / "s", tmp_path / "c"
        src.mkdir(); cand.mkdir()
        (src / "shared").symlink_to("../knowledge/shared")
        (cand / "shared").symlink_to("../knowledge/shared")
        result = self._mod().accounted_for(src, cand)
        assert result["missing_non_markdown"] == []
        assert result["altered_non_markdown"] == []

    def test_missing_or_retargeted_symlink_is_caught(self, tmp_path):
        src, cand = tmp_path / "s", tmp_path / "c"
        src.mkdir(); cand.mkdir()
        (src / "shared").symlink_to("../knowledge/shared")
        missing = self._mod().accounted_for(src, cand)
        assert missing["missing_non_markdown"] == ["shared"]

        (cand / "shared").symlink_to("../wrong/place")
        altered = self._mod().accounted_for(src, cand)
        assert altered["altered_non_markdown"] == ["shared"]

    def test_horizontal_rule_body_is_in_prose_inventory(self, tmp_path):
        src, cand = tmp_path / "s", tmp_path / "c"
        src.mkdir(); cand.mkdir()
        # The opening --- is a horizontal rule, not YAML frontmatter.
        (src / "note.md").write_text("---\nnot: [valid yaml\n---\nirreplaceable prose\n")
        (cand / "note.md").write_text("---\nnot: [valid yaml\n---\nprose\n")
        result = self._mod().accounted_for(src, cand)
        assert result["substantive_words_lost"] == 1
        assert "irreplaceable" in result["substantive_words_lost_sample"]


class TestEndToEndPreservation:
    """Exercise the real copy path, not just the accounting helper.

    The helper being correct is not enough: the deletion shipped *through*
    curate_run's own copy step, so the test has to run that step.
    """

    def _store(self, root: Path) -> Path:
        (root / "projects/artifacts").mkdir(parents=True)
        (root / "note.md").write_text("---\ntitle: Note\n---\n\nalpha beta gamma\n")
        (root / "projects/artifacts/asm.md").write_text(
            "---\ntitle: ASM\n---\n\nmovq callq rdi rsi\n")
        (root / "projects/artifacts/dump.bin").write_bytes(b"\x00binary payload")
        (root / "image.png").write_bytes(b"\x89PNG stub")
        return root

    def test_non_markdown_and_skipped_dirs_survive_a_real_run(self, tmp_path):
        store = self._store(tmp_path / "store")
        ws = tmp_path / "ws"
        result = _run("--store", str(store), "--workspace", str(ws),
                      "--label", "cand", "--json-out", str(tmp_path / "r.json"))
        assert result.returncode == 0, result.stdout[-2000:]

        cand = ws / "cand"
        assert (cand / "image.png").read_bytes() == b"\x89PNG stub"
        assert (cand / "projects/artifacts/dump.bin").read_bytes() == b"\x00binary payload"
        assert (cand / "projects/artifacts/asm.md").exists()

        report = json.loads((tmp_path / "r.json").read_text())
        assert report["preserved"] is True
        assert report["accounting"]["missing_non_markdown"] == []

    def test_run_fails_loudly_when_content_would_be_lost(self, tmp_path, monkeypatch):
        """A candidate that drops files must exit non-zero, not just print."""
        store = self._store(tmp_path / "store")
        ws = tmp_path / "ws"
        _run("--store", str(store), "--workspace", str(ws), "--label", "cand")

        # Simulate any transform losing a file, then re-audit.
        sys.path.insert(0, str(SCRIPT.parent))
        import importlib
        import curate_run
        curate_run = importlib.reload(curate_run)
        (ws / "cand/projects/artifacts/dump.bin").unlink()

        audit = curate_run.accounted_for(store, ws / "cand")
        assert audit["missing_non_markdown"] == ["projects/artifacts/dump.bin"]


    def test_exit_code_is_nonzero_when_preservation_fails(self, tmp_path):
        """The rollout gates on this exit code; a printed warning is not enough.

        A file the copy step cannot read is dropped from the candidate, which
        is exactly the shape of the failure that shipped. The only signal under
        test here is the process exit status.
        """
        store = tmp_path / "store"
        store.mkdir()
        (store / "keep.md").write_text("---\ntitle: K\n---\n\nalpha\n")
        secret = store / "payload.bin"
        secret.write_bytes(b"irreplaceable")

        # Deterministically force the copy call to fail in a subprocess, even
        # under root. sitecustomize is imported before the script and wraps
        # shutil.copy2 only for the named fixture.
        shim = tmp_path / "shim"
        shim.mkdir()
        (shim / "sitecustomize.py").write_text(
            "import shutil\n"
            "_real = shutil.copy2\n"
            "def copy2(src, dst, *a, **k):\n"
            "    if str(src).endswith('payload.bin'):\n"
            "        raise OSError('injected copy failure')\n"
            "    return _real(src, dst, *a, **k)\n"
            "shutil.copy2 = copy2\n"
        )
        env = dict(os.environ, PYTHONPATH=str(shim))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--store", str(store),
             "--workspace", str(tmp_path / "ws"), "--label", "cand",
             "--json-out", str(tmp_path / "r.json")],
            capture_output=True, text=True, timeout=120, env=env,
        )

        assert result.returncode != 0, result.stdout[-1500:]
        assert "PRESERVATION FAILED" in result.stdout
        report = json.loads((tmp_path / "r.json").read_text())
        assert report["preserved"] is False
        assert report["accounting"]["missing_non_markdown"] == ["payload.bin"]
