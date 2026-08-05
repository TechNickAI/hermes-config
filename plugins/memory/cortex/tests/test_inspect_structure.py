"""Tests for the structural audit that gates a live rollout.

This tool exists to answer one question before curated pages replace real ones:
did the reorganization damage anything? Coverage percentages cannot answer it --
they were all green on a run that wrote 142 unparseable pages. So the failure
mode that matters here is a *quiet* audit: one that under-reports damage, or
cries wolf until nobody reads it. Both are tested below.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from inspect_structure import (  # noqa: E402
    frontmatter_audit,
    link_health,
    pages,
    rename_analysis,
    split_analysis,
)


def write(store: Path, rel: str, text: str) -> Path:
    path = store / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def fm(title: str, body: str = "body", **keys) -> str:
    extra = "".join("%s: '%s'\n" % (k, v) for k, v in keys.items())
    return "---\ntitle: %s\n%s---\n\n%s\n" % (title, extra, body)


class TestInvalidFrontmatterCount:
    """The count gates the rollout; capping it hides the failure it guards."""

    def test_every_invalid_page_is_counted(self, tmp_path):
        for n in range(20):
            # An unquoted `: ` makes the block invalid -- the 142-page bug.
            write(tmp_path, "bad%d.md" % n, "---\ntitle: Session: %d\n---\n\nbody\n" % n)
        assert frontmatter_audit(pages(tmp_path))["invalid_yaml"] == 20

    def test_examples_stay_bounded(self, tmp_path):
        for n in range(20):
            write(tmp_path, "bad%d.md" % n, "---\ntitle: Session: %d\n---\n\nbody\n" % n)
        assert len(frontmatter_audit(pages(tmp_path))["invalid_examples"]) == 8

    def test_valid_store_reports_zero(self, tmp_path):
        write(tmp_path, "ok.md", fm("'Session: 12:26'"))
        assert frontmatter_audit(pages(tmp_path))["invalid_yaml"] == 0


class TestSplitContentPreservation:
    def test_promoted_subsection_is_not_phantom_loss(self, tmp_path):
        """Splitting on `###` consumes the marker; that is not lost prose."""
        before, after = tmp_path / "b", tmp_path / "a"
        write(before, "p/big.md",
              fm("Big", "## S\n\n### One\n\nalpha\n\n### Two\n\nbeta"))
        write(after, "p/big/index.md", fm("Big", "## Sections\n\n- [[p/big/one]]"))
        write(after, "p/big/one.md", fm("One", "## S - One\n\nalpha"))
        write(after, "p/big/two.md", fm("Two", "## S - Two\n\nbeta"))

        assert split_analysis(pages(before), pages(after))["with_loss"] == 0

    def test_genuine_prose_loss_is_reported(self, tmp_path):
        """Guards the test above: silencing the alarm must not silence all alarms."""
        before, after = tmp_path / "b", tmp_path / "a"
        write(before, "p/big.md", fm("Big", "## S\n\nkeepme dropme"))
        write(after, "p/big/index.md", fm("Big", "## Sections\n\n- [[p/big/s]]"))
        write(after, "p/big/s.md", fm("S", "## S\n\nkeepme"))

        result = split_analysis(pages(before), pages(after))
        assert result["with_loss"] == 1
        assert "dropme" in result["detail"][0]["lost_sample"]

    def test_unreferenced_index_is_flagged(self, tmp_path):
        before, after = tmp_path / "b", tmp_path / "a"
        write(before, "p/big.md", fm("Big", "## S\n\nalpha"))
        write(after, "p/big/index.md", fm("Big", "## Sections"))
        write(after, "p/big/s.md", fm("S", "## S\n\nalpha"))

        assert split_analysis(pages(before), pages(after))["orphaned_indexes"] == 1


class TestRenameAnalysis:
    def test_colliding_slugs_pair_with_the_page_claiming_each_date(self, tmp_path):
        """Two dated sources collapse onto one slug; one keeps a date suffix.

        Pairing by first-existing candidate matched a source to the *other*
        source's page and reported a date loss that never happened.
        """
        before, after = tmp_path / "b", tmp_path / "a"
        write(before, "inbox/2026-04-28-scan.md", fm("A"))
        write(before, "inbox/2026-04-29-scan.md", fm("B"))
        # Enough related-links to push `date:` past any fixed byte prefix.
        pad = "related:\n" + "".join("  - '[[d%d]]'\n" % n for n in range(40))
        write(after, "inbox/scan.md",
              "---\ntitle: A\n%sdate: '2026-04-28'\n---\n\nbody\n" % pad)
        write(after, "inbox/scan-2026-04-29.md", fm("B", date="2026-04-29"))

        result = rename_analysis(pages(before), pages(after))
        assert result["date_preserved"] == 2
        assert result["date_lost"] == []

    def test_dropped_date_is_reported(self, tmp_path):
        before, after = tmp_path / "b", tmp_path / "a"
        write(before, "inbox/2026-04-28-scan.md", fm("A"))
        write(after, "inbox/scan.md", fm("A"))

        assert rename_analysis(pages(before), pages(after))["date_lost"] == ["inbox/scan.md"]


class TestLinkHealth:
    def test_broken_ambiguous_and_resolved_are_distinguished(self, tmp_path):
        write(tmp_path, "one/dup.md", fm("D1"))
        write(tmp_path, "two/dup.md", fm("D2"))
        write(tmp_path, "ref.md", fm("R", "[[dup]] and [[nowhere]] and [[one/dup]]"))

        health = link_health(pages(tmp_path))
        assert health["ambiguous"] == 1   # bare stem hits two pages
        assert health["broken"] == 1      # [[nowhere]] resolves to nothing
        assert health["links_total"] == 3

    def test_self_link_is_not_counted_as_broken(self, tmp_path):
        write(tmp_path, "solo.md", fm("Solo", "see [[solo]]"))

        health = link_health(pages(tmp_path))
        assert health["self_links"] == 1
        assert health["broken"] == 0


class TestRewritableTargetsAreNotProseLoss:
    def test_path_citation_rewritten_by_derename_is_not_loss(self, tmp_path):
        before, after = tmp_path / "b", tmp_path / "a"
        write(before, "p/big.md", fm("Big", "## S\n\nanalysis `2026-04-16-old-name.md`; stays"))
        write(after, "p/big/index.md", fm("Big", "## Sections\n\n- [[p/big/s]]"))
        write(after, "p/big/s.md", fm("S", "## S\n\nanalysis `new-name.md`; stays"))
        result = split_analysis(pages(before), pages(after))
        assert result["with_loss"] == 0

    def test_real_word_next_to_rewritten_path_is_still_caught(self, tmp_path):
        before, after = tmp_path / "b", tmp_path / "a"
        write(before, "p/big.md", fm("Big", "## S\n\nanalysis irreplaceable `old.md`"))
        write(after, "p/big/index.md", fm("Big", "## Sections\n\n- [[p/big/s]]"))
        write(after, "p/big/s.md", fm("S", "## S\n\nanalysis `new.md`"))
        result = split_analysis(pages(before), pages(after))
        assert result["with_loss"] == 1
        assert "irreplaceable" in result["detail"][0]["lost_sample"]
