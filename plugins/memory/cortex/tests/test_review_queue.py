"""Tests for the structured review queue.

The queue this replaces was append-only markdown: it grew to ~70 items, 80% of
them stale, and 68 of those were the same automated lint summary re-appended
every night. Nothing ever drained it, so the handful of real judgment calls were
buried. These tests pin the properties that failure mode violated.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review_queue import ReviewQueue  # noqa: E402


def _age(queue: ReviewQueue, item_id: str, days: int) -> None:
    """Backdate an item so TTL/cooldown logic can be exercised deterministically."""
    past = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    for item in queue.items:
        if item["id"] == item_id:
            item["created"] = past
            item["last_seen"] = past
            if item.get("escalated_at"):
                item["escalated_at"] = past


class TestDedupe:
    def test_repeated_raises_collapse_to_one_item(self, tmp_path):
        queue = ReviewQueue(tmp_path)
        for _ in range(10):
            queue.add("lint", "orphans", "300 orphan pages", severity="info")
        assert len(queue.items) == 1
        assert queue.items[0]["seen_count"] == 10

    def test_distinct_keys_stay_separate(self, tmp_path):
        queue = ReviewQueue(tmp_path)
        queue.add("lint", "orphans", "orphans", severity="info")
        queue.add("lint", "frontmatter", "frontmatter", severity="info")
        assert len(queue.items) == 2


class TestSeverityRouting:
    def test_only_needs_human_escalates(self, tmp_path):
        queue = ReviewQueue(tmp_path)
        queue.add("lint", "orphans", "300 orphans", severity="info")
        queue.add("dupe", "topics/a.md", "duplicate page", severity="agent")
        queue.add("contradiction", "people/x.md", "conflict", severity="needs_human")

        pending = queue.pending_escalations()
        assert len(pending) == 1
        assert pending[0]["severity"] == "needs_human"

    def test_lint_never_reaches_a_human(self, tmp_path):
        queue = ReviewQueue(tmp_path)
        for i in range(20):
            queue.add("lint", "metric-%d" % i, "count", severity="info")
        assert queue.pending_escalations() == []


class TestLifecycle:
    def test_escalation_cooldown_then_reescalate(self, tmp_path):
        queue = ReviewQueue(tmp_path)
        item = queue.add("contradiction", "people/x.md", "conflict", severity="needs_human")
        queue.mark_escalated(item["id"])
        assert queue.pending_escalations() == []

        _age(queue, item["id"], 8)
        assert len(queue.pending_escalations()) == 1

    def test_resolution_is_recorded_and_stops_escalation(self, tmp_path):
        queue = ReviewQueue(tmp_path)
        item = queue.add("contradiction", "people/x.md", "conflict", severity="needs_human")
        assert queue.resolve(item["id"], "confirmed by later record", resolver="maintainer")

        stored = queue.items[0]
        assert stored["status"] == "resolved"
        assert stored["resolution"]["by"] == "maintainer"
        assert queue.pending_escalations() == []

    def test_escalations_are_capped_and_oldest_first(self, tmp_path):
        queue = ReviewQueue(tmp_path)
        for i in range(12):
            queue.add("contradiction", "people/p%d.md" % i, "conflict %d" % i,
                      severity="needs_human")
        pending = queue.pending_escalations(limit=5)
        assert len(pending) == 5
        assert pending[0]["key"] == "people/p0.md"


class TestExpiry:
    def test_info_and_agent_expire_but_needs_human_does_not(self, tmp_path):
        queue = ReviewQueue(tmp_path)
        queue.add("lint", "frontmatter", "missing frontmatter", severity="info")
        queue.add("dupe", "topics/a.md", "duplicate", severity="agent")
        queue.add("contradiction", "people/y.md", "conflict", severity="needs_human")
        for item in list(queue.items):
            _age(queue, item["id"], 40)

        assert queue.expire_stale() == 2
        survivor = [i for i in queue.items if i["severity"] == "needs_human"][0]
        assert survivor["status"] == "open"

    def test_recurrence_reopens_an_expired_item(self, tmp_path):
        queue = ReviewQueue(tmp_path)
        queue.add("lint", "frontmatter", "missing frontmatter", severity="info")
        _age(queue, queue.items[0]["id"], 40)
        queue.expire_stale()

        queue.add("lint", "frontmatter", "missing frontmatter", severity="info")
        assert queue.items[0]["status"] == "open"

    def test_prune_drops_long_closed_items(self, tmp_path):
        queue = ReviewQueue(tmp_path)
        for i in range(5):
            queue.add("lint", "k%d" % i, "t%d" % i, severity="info")
            queue.resolve(queue.items[-1]["id"], "done")
        for item in queue.items:
            item["resolution"]["at"] = (
                datetime.date.today() - datetime.timedelta(days=99)
            ).isoformat()

        assert queue.prune_resolved(keep_days=30) == 5
        assert queue.items == []


class TestPersistence:
    def test_round_trips_and_renders_markdown(self, tmp_path):
        queue = ReviewQueue(tmp_path)
        queue.add("contradiction", "people/x.md", "conflict", severity="needs_human")
        queue.save()

        assert (tmp_path / ".review-queue.json").exists()
        rendered = (tmp_path / "review-queue.md").read_text()
        assert "Needs human decision" in rendered

        assert len(ReviewQueue(tmp_path).items) == 1

    def test_corrupt_queue_file_does_not_crash(self, tmp_path):
        (tmp_path / ".review-queue.json").write_text("{not valid json")

        queue = ReviewQueue(tmp_path)
        assert queue.items == []

        queue.add("lint", "x", "y", severity="info")
        queue.save()
        assert json.loads((tmp_path / ".review-queue.json").read_text())["items"]


class TestCorruptionSafety:
    def test_corrupt_file_is_quarantined_not_silently_replaced(self, tmp_path):
        """A corrupt queue may hold unresolved human decisions."""
        queue = ReviewQueue(tmp_path)
        queue.add("contradiction", "people/x.md", "human decision", severity="needs_human")
        queue.save()

        (tmp_path / ".review-queue.json").write_text('{"items": [ {"id": "trunc"')
        reopened = ReviewQueue(tmp_path)

        assert reopened.load_error
        assert (tmp_path / ".review-queue.json.corrupt").exists()


class TestSeverityRatchet:
    def test_severity_upgrades_when_an_issue_proves_serious(self, tmp_path):
        """An item first logged as info must not stay trapped below needs_human."""
        queue = ReviewQueue(tmp_path)
        queue.add("conflict", "people/x.md", "maybe an issue", severity="info")
        queue.add("conflict", "people/x.md", "actually needs a human", severity="needs_human")

        assert queue.items[0]["severity"] == "needs_human"
        assert len(queue.pending_escalations()) == 1

    def test_severity_never_downgrades(self, tmp_path):
        queue = ReviewQueue(tmp_path)
        queue.add("conflict", "people/x.md", "human decision", severity="needs_human")
        queue.add("conflict", "people/x.md", "routine", severity="info")

        assert queue.items[0]["severity"] == "needs_human"


class TestReopenEscalation:
    def test_reopened_item_is_immediately_eligible_again(self, tmp_path):
        """A recurrence is new news; a stale stamp must not silence it."""
        queue = ReviewQueue(tmp_path)
        item = queue.add("c", "k", "t", severity="needs_human")
        queue.mark_escalated(item["id"])
        queue.resolve(item["id"], "handled")

        queue.add("c", "k", "t", severity="needs_human")
        assert queue.items[0]["status"] == "open"
        assert queue.items[0]["escalated_at"] is None
        assert len(queue.pending_escalations()) == 1
