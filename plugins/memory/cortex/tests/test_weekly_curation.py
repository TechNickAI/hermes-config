"""Tests for the incremental weekly curation pass.

The pass this replaces reloaded the entire corpus on every run with no cursor,
so a timeout meant permanently re-processing the same early pages and never
reaching the rest. The checkpoint tests below are the guard against that.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review_queue import ReviewQueue  # noqa: E402
from weekly_curation import Checkpoint, apply_decisions, build_brief  # noqa: E402

CURATION_SCRIPT = SCRIPTS_DIR / "weekly_curation.py"


def make_store(root: Path, pages: int = 20) -> Path:
    store = root / "cortex"
    for index in range(pages):
        category = "daily" if index % 2 == 0 else "topics"
        path = store / category / ("page%02d.md" % index)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\ntitle: Page %d\n---\n\n# Page %d\n\nDurable content %d.\n" % (index, index, index)
        )
    return store


class TestIncrementalWorkSet:
    def test_first_run_sees_everything_as_uncurated(self, tmp_path):
        store = make_store(tmp_path)
        brief = build_brief(store, days=7)
        assert brief["totals"]["never_curated"] == 20

    def test_work_set_is_bounded(self, tmp_path):
        store = make_store(tmp_path, pages=200)
        assert build_brief(store, days=7)["totals"]["work_set"] <= 60

    def test_interrupted_run_resumes_instead_of_restarting(self, tmp_path):
        store = make_store(tmp_path)
        first = build_brief(store, days=7)

        # Simulate a run that curated part of the work set, then timed out.
        partial = first["work_set"][:8]
        apply_decisions(store, {"curated_pages": partial, "completed": False})

        second = build_brief(store, days=7)
        assert second["totals"]["never_curated"] == 12
        assert not any(page in second["work_set"] for page in partial)

    def test_full_sweep_completes_and_records_the_run(self, tmp_path):
        store = make_store(tmp_path)
        apply_decisions(store, {
            "curated_pages": build_brief(store, days=7)["work_set"],
            "completed": True,
        })

        brief = build_brief(store, days=7)
        assert brief["totals"]["never_curated"] == 0
        assert brief["checkpoint"]["last_completed_run"] is not None

    def test_edited_page_returns_to_the_work_set(self, tmp_path):
        store = make_store(tmp_path)
        apply_decisions(store, {
            "curated_pages": build_brief(store, days=7)["work_set"],
            "completed": True,
        })

        time.sleep(0.01)
        target = store / "topics" / "page01.md"
        target.write_text(target.read_text() + "\nA new fact appeared later.\n")

        brief = build_brief(store, days=7)
        assert brief["work_set"] == ["topics/page01.md"]

    def test_generated_queue_file_is_not_curated_as_content(self, tmp_path):
        """The pass must not treat its own output as a page needing curation."""
        store = make_store(tmp_path)
        queue = ReviewQueue(store)
        queue.add("lint", "orphans", "orphans", severity="info")
        queue.save()  # writes review-queue.md into the store

        apply_decisions(store, {
            "curated_pages": build_brief(store, days=7)["work_set"],
            "completed": True,
        })
        assert build_brief(store, days=7)["totals"]["never_curated"] == 0


class TestCandidateDetection:
    def test_near_duplicate_titles_are_flagged(self, tmp_path):
        store = make_store(tmp_path)
        (store / "topics" / "alpha-report.md").write_text("---\ntitle: Alpha Report\n---\nbody\n")
        (store / "topics" / "alpha-report-2.md").write_text("---\ntitle: Alpha Report 2\n---\nbody\n")

        candidates = build_brief(store, days=7)["duplicate_candidates"]
        assert any("alpha" in c["a"] and "alpha" in c["b"] for c in candidates)

    def test_journal_entries_are_excluded_from_duplicate_checks(self, tmp_path):
        store = make_store(tmp_path)
        candidates = build_brief(store, days=7)["duplicate_candidates"]
        assert not any(c["a"].startswith("daily/") for c in candidates)

    def test_supersede_language_surfaces_a_contradiction_candidate(self, tmp_path):
        store = make_store(tmp_path)
        (store / "topics" / "status.md").write_text(
            "---\ntitle: Status\n---\nThe service previously ran on host A but now moved to host B.\n"
        )
        candidates = build_brief(store, days=7)["contradiction_candidates"]
        assert any(c["page"] == "topics/status.md" for c in candidates)


class TestDecisions:
    def test_decisions_populate_the_review_queue(self, tmp_path):
        store = make_store(tmp_path)
        result = apply_decisions(store, {
            "queue_items": [
                {"kind": "contradiction", "key": "topics/status.md", "title": "Host moved",
                 "severity": "needs_human", "detail": "Two hosts claimed",
                 "sources": ["topics/status.md"], "recommendation": "Confirm current host"},
                {"kind": "lint", "key": "orphans", "title": "orphan count", "severity": "info"},
            ],
            "completed": True,
        })
        assert result["queue_items_added"] == 2

        queue = ReviewQueue(store)
        assert len(queue.open_items("needs_human")) == 1
        assert all(i["severity"] == "needs_human" for i in queue.pending_escalations())

    def test_reapplying_the_same_decision_adds_no_duplicate(self, tmp_path):
        store = make_store(tmp_path)
        decision = {
            "queue_items": [{"kind": "contradiction", "key": "topics/status.md",
                             "title": "Host moved", "severity": "needs_human"}],
            "completed": True,
        }
        apply_decisions(store, decision)
        before = len(ReviewQueue(store).items)

        apply_decisions(store, decision)
        assert len(ReviewQueue(store).items) == before


class TestResilience:
    def test_corrupt_checkpoint_is_recovered(self, tmp_path):
        store = make_store(tmp_path)
        (store / ".curation-checkpoint.json").write_text("{broken")

        assert isinstance(Checkpoint(store).processed, dict)
        assert build_brief(store, days=7)["totals"]["work_set"] > 0


class TestCommandLine:
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CURATION_SCRIPT), *args],
            capture_output=True, text=True,
        )

    def test_status_reports_queue_and_checkpoint(self, tmp_path):
        store = make_store(tmp_path)
        result = self._run("--store", str(store), "--status")
        assert result.returncode == 0

        payload = json.loads(result.stdout)
        assert "queue" in payload and "checkpoint" in payload

    def test_brief_emits_valid_json(self, tmp_path):
        store = make_store(tmp_path)
        result = self._run("--store", str(store), "--brief")
        assert result.returncode == 0
        assert json.loads(result.stdout)["totals"]["pages"] == 20

    def test_missing_store_exits_non_zero(self, tmp_path):
        assert self._run("--store", str(tmp_path / "absent")).returncode != 0


class TestCheckpointHygiene:
    def test_entries_for_deleted_pages_are_pruned(self, tmp_path):
        store = make_store(tmp_path)
        apply_decisions(store, {
            "curated_pages": build_brief(store, days=7)["work_set"],
            "completed": True,
        })
        assert len(Checkpoint(store).processed) == 20

        (store / "topics" / "page01.md").unlink()
        result = apply_decisions(store, {"curated_pages": [], "completed": True})

        assert result["checkpoint_entries_pruned"] == 1
        assert len(Checkpoint(store).processed) == 19


class TestLinkGraph:
    def test_stem_collisions_do_not_inflate_orphan_count(self, tmp_path):
        """topics/foo.md and projects/foo.md must both receive [[foo]] credit."""
        store = tmp_path / "cortex"
        for category in ("topics", "projects"):
            path = store / category / "foo.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\ntitle: Foo\n---\n\nbody\n")
        hub = store / "topics" / "hub.md"
        hub.write_text("---\ntitle: Hub\n---\n\nSee [[foo]].\n")

        structure = build_brief(store, days=7)["structure"]
        # Only the hub itself is an orphan; both foo pages are linked.
        assert structure["orphans"] == 1
