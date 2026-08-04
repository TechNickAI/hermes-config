"""Tests for the curation transforms that actually rewrite pages.

These transforms mutate a knowledge store, so the tests focus on the ways that
can go wrong: destroying distinct records while deduping, reintroducing dates
into filenames, corrupting prose while adding links, and losing content while
splitting an oversized page.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from transforms import (  # noqa: E402
    apply_derename,
    apply_enrich,
    apply_links,
    apply_split,
    apply_temporal,
    parse_fm,
    plan_derename,
    plan_enrich,
    plan_links,
    plan_split,
    plan_temporal,
    render_fm,
    rewrite_references,
    split_frontmatter,
)


def write(store: Path, rel: str, text: str) -> Path:
    path = store / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class TestFrontmatterRoundTrip:
    def test_parse_and_render_preserve_scalars_and_lists(self):
        block, body = split_frontmatter(
            "---\ntitle: Example\ntags:\n  - alpha\n  - beta\n---\n\n# Example\n\nprose\n"
        )
        data = parse_fm(block)
        assert data["title"] == "Example"
        assert data["tags"] == ["alpha", "beta"]
        assert "prose" in body

        rendered = render_fm(data)
        assert parse_fm(split_frontmatter(rendered + "\n\nbody\n")[0]) == data

    def test_body_without_frontmatter_is_untouched(self):
        block, body = split_frontmatter("# Heading\n\nprose\n")
        assert block == ""
        assert body.startswith("# Heading")

    def test_wikilinks_survive_a_round_trip(self):
        """Unquoted `[[link]]` parses as a nested YAML sequence and is destroyed."""
        data = {"title": "X", "related": ["[[some-page]]", "[[other-page]]"]}
        reparsed = parse_fm(split_frontmatter(render_fm(data) + "\n\nbody\n")[0])
        assert reparsed["related"] == ["[[some-page]]", "[[other-page]]"]

    def test_block_scalar_content_is_not_lost(self):
        block = "title: X\nsummary: |\n  line one\n  line two\n"
        data = parse_fm(block)
        assert "line one" in data["summary"] and "line two" in data["summary"]

        reparsed = parse_fm(split_frontmatter(render_fm(data) + "\n\nbody\n")[0])
        assert reparsed["summary"] == data["summary"]

    def test_nested_structures_survive(self):
        block = "title: X\nmeta:\n  author: jane\n  version: 2\nrefs:\n  - name: a\n    url: b\n"
        data = parse_fm(block)
        assert data["meta"] == {"author": "jane", "version": 2}
        assert data["refs"] == [{"name": "a", "url": "b"}]

        reparsed = parse_fm(split_frontmatter(render_fm(data) + "\n\nbody\n")[0])
        assert reparsed["meta"] == data["meta"]
        assert reparsed["refs"] == data["refs"]

    def test_hash_in_value_is_not_truncated_as_a_comment(self):
        data = {"title": "C# and #hashtag"}
        reparsed = parse_fm(split_frontmatter(render_fm(data) + "\n\nbody\n")[0])
        assert reparsed["title"] == "C# and #hashtag"

    def test_colon_space_in_title_is_quoted(self):
        """Session-style titles otherwise make the entire frontmatter invalid YAML."""
        data = {"title": "Session: 2026-05-24 12:26:40 CDT"}
        rendered = render_fm(data)
        reparsed = parse_fm(split_frontmatter(rendered + "\n\nbody\n")[0])
        assert reparsed["title"] == data["title"]

    def test_string_scalars_do_not_change_yaml_type(self):
        data = {"title": "true", "status": "null", "confidence": "3.14"}
        reparsed = parse_fm(split_frontmatter(render_fm(data) + "\n\nbody\n")[0])
        assert reparsed == data

    def test_malformed_yaml_does_not_raise(self):
        assert isinstance(parse_fm("title: [unclosed\n  bad: : :\n"), dict)

    def test_horizontal_rule_is_not_mistaken_for_frontmatter(self):
        """`---\\n\\nprose\\n\\n---` is Markdown, not metadata; prose must survive."""
        doc = "---\n\nSome intro prose.\n\n---\n\nMore prose.\n"
        block, body = split_frontmatter(doc)
        assert block == ""
        assert "Some intro prose." in body

    def test_unterminated_fence_keeps_the_document_as_body(self):
        doc = "---\ntitle: X\n\nnever closed\n"
        block, body = split_frontmatter(doc)
        assert block == ""
        assert "never closed" in body


class TestPathSafety:
    def test_plan_entry_cannot_escape_the_store(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        outside = tmp_path / "outside.md"
        outside.write_text("---\ntitle: Outside\n---\n\nmust not be touched\n")

        apply_derename(store, [
            {"from": "../outside.md", "to": "captured.md", "date": "2026-01-01"},
        ])
        assert outside.exists()
        assert "must not be touched" in outside.read_text()

    def test_absolute_paths_are_refused(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        outside = tmp_path / "outside.md"
        outside.write_text("keep\n")

        result = apply_derename(store, [
            {"from": str(outside), "to": "captured.md", "date": "2026-01-01"},
        ])
        assert result["renamed"] == 0
        assert outside.exists()


class TestDerename:
    def test_date_prefix_moves_from_filename_into_frontmatter(self, tmp_path):
        write(tmp_path, "decisions/2026-07-19-backend-switch.md", "# Backend switch\n\nprose\n")

        plan = plan_derename(tmp_path)
        assert plan == [{
            "from": "decisions/2026-07-19-backend-switch.md",
            "to": "decisions/backend-switch.md",
            "date": "2026-07-19",
        }]

        apply_derename(tmp_path, plan)
        renamed = tmp_path / "decisions/backend-switch.md"
        assert renamed.exists()
        assert not (tmp_path / "decisions/2026-07-19-backend-switch.md").exists()

        data = parse_fm(split_frontmatter(renamed.read_text())[0])
        assert data["date"] == "2026-07-19"

    def test_journal_entries_keep_their_dated_names(self, tmp_path):
        write(tmp_path, "daily/2026-07-19.md", "# Daily\n\nprose\n")
        assert plan_derename(tmp_path) == []

    def test_timestamp_prefix_is_also_stripped(self, tmp_path):
        write(tmp_path, "inbox/20260520T033430-probe-failure.md", "# Probe\n\nprose\n")
        plan = plan_derename(tmp_path)
        assert plan[0]["to"] == "inbox/probe-failure.md"
        assert plan[0]["date"] == "2026-05-20"

    def test_collision_keeps_both_pages(self, tmp_path):
        write(tmp_path, "notes/report.md", "# Report\n\noriginal\n")
        write(tmp_path, "notes/2026-01-02-report.md", "# Report\n\ndated\n")

        plan = plan_derename(tmp_path)
        apply_derename(tmp_path, plan)

        assert (tmp_path / "notes/report.md").read_text().endswith("original\n")
        assert len(list((tmp_path / "notes").glob("*.md"))) == 2

    def test_references_are_rewritten(self, tmp_path):
        write(tmp_path, "decisions/2026-07-19-backend-switch.md", "# Switch\n\nprose\n")
        write(tmp_path, "topics/index.md", "See [[2026-07-19-backend-switch]] for detail.\n")

        plan = plan_derename(tmp_path)
        apply_derename(tmp_path, plan)
        rewrite_references(tmp_path, plan)

        assert "[[backend-switch]]" in (tmp_path / "topics/index.md").read_text()


class TestEnrich:
    def test_missing_frontmatter_is_added(self, tmp_path):
        write(tmp_path, "people/example.md", "# Example Person\n\nprose\n")

        plan = plan_enrich(tmp_path)
        assert plan[0]["page"] == "people/example.md"

        apply_enrich(tmp_path, plan)
        data = parse_fm(split_frontmatter((tmp_path / "people/example.md").read_text())[0])
        assert data["title"] == "Example Person"  # taken from the H1
        assert data["type"] == "person"           # inferred from the category
        assert data["tags"] == ["people"]
        assert data["created"] and data["updated"]

    def test_existing_values_are_not_overwritten(self, tmp_path):
        write(tmp_path, "topics/a.md", "---\ntitle: Kept\ntype: custom\n---\n\nprose\n")

        apply_enrich(tmp_path, plan_enrich(tmp_path))
        data = parse_fm(split_frontmatter((tmp_path / "topics/a.md").read_text())[0])
        assert data["title"] == "Kept"
        assert data["type"] == "custom"

    def test_body_prose_survives_enrichment(self, tmp_path):
        write(tmp_path, "topics/a.md", "# A\n\nimportant sentence\n")
        apply_enrich(tmp_path, plan_enrich(tmp_path))
        assert "important sentence" in (tmp_path / "topics/a.md").read_text()


class TestLinks:
    def test_links_are_added_to_related_frontmatter(self, tmp_path):
        write(tmp_path, "people/jane-maintainer.md", "---\ntitle: Jane Maintainer\n---\n\nbio\n")
        write(tmp_path, "projects/thing.md",
              "---\ntitle: Thing\n---\n\nJane Maintainer owns this project.\n")

        proposals = plan_links(tmp_path)
        applied = apply_links(tmp_path, proposals)
        assert applied["links_added"] >= 1

        data = parse_fm(split_frontmatter((tmp_path / "projects/thing.md").read_text())[0])
        assert "[[jane-maintainer]]" in data["related"]

    def test_prose_is_never_modified(self, tmp_path):
        write(tmp_path, "people/jane-maintainer.md", "---\ntitle: Jane Maintainer\n---\n\nbio\n")
        body = "Jane Maintainer owns this project.\n"
        write(tmp_path, "projects/thing.md", "---\ntitle: Thing\n---\n\n" + body)

        apply_links(tmp_path, plan_links(tmp_path))
        assert body in (tmp_path / "projects/thing.md").read_text()

    def test_page_does_not_link_to_itself(self, tmp_path):
        write(tmp_path, "topics/self-reference.md",
              "---\ntitle: Self Reference\n---\n\nSelf Reference is discussed here.\n")

        for proposal in plan_links(tmp_path):
            assert all(link["target"] != "self-reference" for link in proposal["links"])

    def test_link_count_is_capped_per_page(self, tmp_path):
        for i in range(12):
            write(tmp_path, "people/person-number-%02d.md" % i,
                  "---\ntitle: Person Number %02d\n---\n\nbio\n" % i)
        mentions = " ".join("Person Number %02d" % i for i in range(12))
        write(tmp_path, "topics/hub.md", "---\ntitle: Hub\n---\n\n" + mentions + "\n")

        hub = [p for p in plan_links(tmp_path, max_per_page=5) if p["page"] == "topics/hub.md"]
        assert hub and len(hub[0]["links"]) <= 5


class TestSplit:
    def _oversized(self) -> str:
        # plan_split only fires above 40KB and needs 4+ sections; make the
        # fixture comfortably clear both thresholds.
        sections = "".join(
            "## 2026-05-%02d — Section %d\n\n%s\n\n" % (day, day, "content " * 900)
            for day in range(1, 9)
        )
        return "---\ntitle: Big Page\ntype: person\n---\n\nintro\n\n" + sections

    def test_oversized_page_becomes_folder_with_index(self, tmp_path):
        write(tmp_path, "people/big-page.md", self._oversized())

        plan = plan_split(tmp_path)
        assert plan and plan[0]["page"] == "people/big-page.md"

        result = apply_split(tmp_path, plan)
        assert result["pages_split"] == 1
        assert (tmp_path / "people/big-page/index.md").exists()
        assert not (tmp_path / "people/big-page.md").exists()

    def test_section_filenames_contain_no_dates(self, tmp_path):
        write(tmp_path, "people/big-page.md", self._oversized())
        apply_split(tmp_path, plan_split(tmp_path))

        for child in (tmp_path / "people/big-page").glob("*.md"):
            assert not re.match(r"^\d{4}-\d{2}-\d{2}", child.name), child.name

    def test_section_dates_are_preserved_as_metadata(self, tmp_path):
        write(tmp_path, "people/big-page.md", self._oversized())
        apply_split(tmp_path, plan_split(tmp_path))

        children = [c for c in (tmp_path / "people/big-page").glob("*.md") if c.stem != "index"]
        dates = [parse_fm(split_frontmatter(c.read_text())[0]).get("date") for c in children]
        assert all(d and d.startswith("2026-05") for d in dates)

    def test_content_is_preserved_across_the_split(self, tmp_path):
        original = self._oversized()
        write(tmp_path, "people/big-page.md", original)
        apply_split(tmp_path, plan_split(tmp_path))

        combined = "".join(
            split_frontmatter(c.read_text())[1]
            for c in (tmp_path / "people/big-page").rglob("*.md")
        )
        original_words = len(split_frontmatter(original)[1].split())
        assert len(combined.split()) >= original_words

    def test_small_pages_are_left_alone(self, tmp_path):
        write(tmp_path, "topics/small.md", "---\ntitle: Small\n---\n\n## One\n\nshort\n")
        assert plan_split(tmp_path) == []


class TestTemporal:
    def test_conflicting_dated_claims_are_detected_and_annotated(self, tmp_path):
        write(
            tmp_path,
            "projects/service.md",
            "---\ntitle: Service\n---\n\n"
            "## 2026-01-01 — Initial\n\nThe host is alpha.example.com today.\n\n"
            "## 2026-06-01 — Migration\n\nThe host is beta.example.com now.\n",
        )

        findings = plan_temporal(tmp_path)
        assert findings and findings[0]["conflicts"][0]["claim_type"] == "endpoint"

        apply_temporal(tmp_path, findings)
        text = (tmp_path / "projects/service.md").read_text()
        assert "## Current state" in text
        assert "beta.example.com" in text.split("## 2026-01-01")[0]

    def test_prose_fragments_do_not_produce_false_conflicts(self, tmp_path):
        write(
            tmp_path,
            "daily/2026-04-03.md",
            "---\ntitle: Daily\n---\n\n"
            "## 2026-04-03 — Morning\n\nIt is on track and running well.\n\n"
            "## 2026-04-04 — Evening\n\nEverything is now running smoothly.\n",
        )
        assert plan_temporal(tmp_path) == []

    def test_single_dated_section_is_not_a_conflict(self, tmp_path):
        write(tmp_path, "projects/one.md",
              "---\ntitle: One\n---\n\n## 2026-01-01 — Only\n\nThe host is alpha.example.com.\n")
        assert plan_temporal(tmp_path) == []

    def test_annotation_is_idempotent(self, tmp_path):
        write(
            tmp_path,
            "projects/service.md",
            "---\ntitle: Service\n---\n\n"
            "## 2026-01-01 — Initial\n\nThe host is alpha.example.com today.\n\n"
            "## 2026-06-01 — Migration\n\nThe host is beta.example.com now.\n",
        )
        apply_temporal(tmp_path, plan_temporal(tmp_path))
        first = (tmp_path / "projects/service.md").read_text()

        apply_temporal(tmp_path, plan_temporal(tmp_path))
        assert (tmp_path / "projects/service.md").read_text().count("## Current state") == 1
        assert (tmp_path / "projects/service.md").read_text() == first


class TestSplitRelinking:
    def _oversized(self) -> str:
        sections = "".join(
            "## 2026-05-%02d — Section %d\n\n%s\n\n" % (day, day, "word " * 2000)
            for day in range(1, 9)
        )
        return "---\ntitle: Big Page\ntype: person\n---\n\nintro\n\n" + sections

    def test_inbound_links_are_repointed_at_the_new_index(self, tmp_path):
        """Splitting a page must not leave [[stem]] links dangling."""
        write(tmp_path, "people/big-page.md", self._oversized())
        write(tmp_path, "topics/ref.md", "---\ntitle: Ref\n---\n\nSee [[big-page]] for detail.\n")

        result = apply_split(tmp_path, plan_split(tmp_path))
        assert result["pages_split"] == 1
        assert result["inbound_links_repointed"] == 1

        text = (tmp_path / "topics/ref.md").read_text()
        assert "[[people/big-page/index]]" in text
        assert "[[big-page]]" not in text

    def test_path_qualified_links_are_repointed(self, tmp_path):
        write(tmp_path, "people/big-page.md", self._oversized())
        write(tmp_path, "topics/ref.md",
              "---\ntitle: Ref\n---\n\nSee [[people/big-page]] for detail.\n")

        apply_split(tmp_path, plan_split(tmp_path))
        text = (tmp_path / "topics/ref.md").read_text()
        assert "[[people/big-page/index]]" in text

    def test_bare_link_is_left_alone_when_the_stem_is_ambiguous(self, tmp_path):
        """A same-named page elsewhere means [[stem]] must not be hijacked."""
        write(tmp_path, "people/big-page.md", self._oversized())
        write(tmp_path, "topics/big-page.md", "---\ntitle: Other\n---\n\nunrelated\n")
        write(tmp_path, "notes/ref.md", "---\ntitle: Ref\n---\n\nSee [[big-page]].\n")

        apply_split(tmp_path, plan_split(tmp_path))
        assert "[[big-page]]" in (tmp_path / "notes/ref.md").read_text()

    def test_aliased_links_are_also_repointed(self, tmp_path):
        write(tmp_path, "people/big-page.md", self._oversized())
        write(tmp_path, "topics/ref.md",
              "---\ntitle: Ref\n---\n\nSee [[big-page|the big page]].\n")

        apply_split(tmp_path, plan_split(tmp_path))
        assert "[[people/big-page/index|the big page]]" in (tmp_path / "topics/ref.md").read_text()


class TestSplitSafety:
    def _oversized(self) -> str:
        sections = "".join(
            "## 2026-05-%02d — Section %d\n\n%s\n\n" % (day, day, "word " * 2000)
            for day in range(1, 9)
        )
        return "---\ntitle: Big Page\ntype: person\n---\n\nintro\n\n" + sections

    def test_existing_sibling_folder_is_never_clobbered(self, tmp_path):
        """`note.md` alongside `note/` is a valid layout, not a split target."""
        write(tmp_path, "people/big-page.md", self._oversized())
        write(tmp_path, "people/big-page/index.md", "existing index content\n")

        assert plan_split(tmp_path) == []
        assert "existing index content" in (tmp_path / "people/big-page/index.md").read_text()
        assert (tmp_path / "people/big-page.md").exists()


class TestLinkPathSafety:
    def test_link_proposal_cannot_escape_the_store(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        outside = tmp_path / "outside.md"
        outside.write_text("---\ntitle: Outside\n---\n\nmust not change\n")

        apply_links(store, [{"page": "../outside.md",
                             "links": [{"name": "x", "target": "victim"}]}])
        assert "must not change" in outside.read_text()


class TestTemporalSubjects:
    def test_distinct_subjects_are_not_a_conflict(self, tmp_path):
        """An api host and a db host on both dates is stable, not drift."""
        write(
            tmp_path,
            "projects/svc.md",
            "---\ntitle: Svc\n---\n\n"
            "## 2026-01-01 — Initial\n\n"
            "The api host is alpha.example.com. The db host is db.example.com.\n\n"
            "## 2026-06-01 — Later\n\n"
            "The api host is alpha.example.com. The db host is db.example.com.\n",
        )
        assert plan_temporal(tmp_path) == []

    def test_same_subject_change_is_still_detected(self, tmp_path):
        write(
            tmp_path,
            "projects/svc.md",
            "---\ntitle: Svc\n---\n\n"
            "## 2026-01-01 — Initial\n\nThe api host is alpha.example.com.\n\n"
            "## 2026-06-01 — Migration\n\nThe api host is beta.example.com.\n",
        )
        findings = plan_temporal(tmp_path)
        assert findings
        assert findings[0]["conflicts"][0]["newest"]["value"] == "beta.example.com"


class TestFallbackQuoting:
    """The fallback parser must reverse exactly what the renderer emits."""

    def _fallback_round_trip(self, data: dict) -> dict:
        from transforms import _parse_fm_fallback
        return _parse_fm_fallback(render_fm(data))

    def test_apostrophes_survive_the_fallback_parser(self):
        data = {"title": "Today's plan"}
        assert self._fallback_round_trip(data)["title"] == "Today's plan"

    def test_apostrophes_do_not_multiply_across_passes(self):
        """Curation re-renders what it parses, so drift compounds every run."""
        data = {"title": "Today's plan for Sam's review"}
        for _ in range(5):
            data = self._fallback_round_trip(data)
        assert data["title"] == "Today's plan for Sam's review"

    def test_list_items_with_apostrophes_survive(self):
        data = {"title": "T", "tags": ["sam's", "today's"]}
        assert self._fallback_round_trip(data)["tags"] == ["sam's", "today's"]

    def test_wikilinks_survive_the_fallback_parser(self):
        data = {"title": "T", "related": ["[[alpha]]", "[[beta]]"]}
        assert self._fallback_round_trip(data)["related"] == ["[[alpha]]", "[[beta]]"]


class TestRenderIdempotence:
    """A second curation pass must be a no-op, or stores drift every week."""

    def test_frontmatter_is_stable_across_repeated_renders(self):
        data = {
            "title": "Session: 2026-05-24 12:26:40 CDT — Sam's review",
            "type": "note",
            "tags": ["c#", "today's"],
            "related": ["[[alpha]]", "[[beta/index]]"],
            "confidence": "medium",
        }
        first = render_fm(data)
        second = render_fm(parse_fm(split_frontmatter(first + "\n\nbody\n")[0]))
        third = render_fm(parse_fm(split_frontmatter(second + "\n\nbody\n")[0]))
        assert first == second == third

    def test_every_rendered_block_is_valid_yaml(self):
        import yaml
        data = {
            "title": "Session: 12:26 — a: b",
            "tags": ["a#b", "it's"],
            "related": ["[[x]]"],
        }
        block, _ = split_frontmatter(render_fm(data) + "\n\nbody\n")
        parsed = yaml.safe_load(block)
        assert isinstance(parsed, dict)
        assert parsed == data


class TestSplitIndexLinks:
    def _oversized(self, label: str) -> str:
        sections = "".join(
            "## 2026-05-%02d — %s update %d\n\n%s\n\n" % (day, label, day, "word " * 2000)
            for day in range(1, 9)
        )
        return "---\ntitle: %s\ntype: person\n---\n\nintro\n\n" % label + sections

    def test_index_links_are_path_qualified(self, tmp_path):
        """A bare stem collides across split folders and resolves arbitrarily."""
        write(tmp_path, "people/alice.md", self._oversized("Alice"))
        apply_split(tmp_path, plan_split(tmp_path))

        index = (tmp_path / "people/alice/index.md").read_text()
        assert "[[people/alice/" in index
        # No bare-stem section links.
        for line in index.splitlines():
            if line.startswith("- [["):
                assert "/" in line.split("[[")[1].split("]]")[0]

    def test_two_split_people_do_not_share_ambiguous_targets(self, tmp_path):
        write(tmp_path, "people/alice.md", self._oversized("Alice"))
        write(tmp_path, "people/bob.md", self._oversized("Bob"))
        apply_split(tmp_path, plan_split(tmp_path))

        alice = (tmp_path / "people/alice/index.md").read_text()
        bob = (tmp_path / "people/bob/index.md").read_text()
        assert "[[people/bob/" not in alice
        assert "[[people/alice/" not in bob


class TestSlugify:
    def test_transcript_ids_are_dropped(self):
        from transforms import slugify
        assert slugify("Ace model fallback issue Limitless hnem5smxoylk") == \
            "ace-model-fallback-issue"

    def test_fireflies_id_form_is_dropped(self):
        from transforms import slugify
        assert slugify("Apr 7 2026 planning call Fireflies ID 01kmv4wgn3zs2h") == \
            "apr-7-2026-planning-call"

    def test_truncation_never_leaves_a_partial_word(self):
        from transforms import slugify
        slug = slugify("AI reliability tool frustration is a recurring pattern "
                       "across many different sessions and contexts")
        assert not slug.endswith("-")
        assert len(slug) <= 50
        # Every retained token must be a whole word from the source.
        source = "ai reliability tool frustration is a recurring pattern across many " \
                 "different sessions and contexts".split()
        assert all(part in source for part in slug.split("-"))

    def test_all_identifier_input_still_yields_a_name(self):
        from transforms import slugify
        assert slugify("limitless hnem5smxoylk")


class TestDiscriminatingTags:
    def test_irrelevant_parent_tags_are_not_inherited(self):
        from transforms import discriminating_tags
        tags = discriminating_tags(
            ["romantic-partner", "austin", "house-music", "comedy"],
            "Heath Schechinger therapy session",
        )
        assert "house-music" not in tags
        assert "comedy" not in tags

    def test_relevant_parent_tag_is_kept(self):
        from transforms import discriminating_tags
        assert "comedy" in discriminating_tags(
            ["austin", "comedy"], "Comedy show in October")

    def test_tags_are_not_identical_across_sections(self):
        """A tag on every child discriminates nothing."""
        from transforms import discriminating_tags
        parent = ["romantic-partner", "austin", "house-music"]
        a = discriminating_tags(parent, "Therapy session with Heath")
        b = discriminating_tags(parent, "Costa Rica trip planning")
        assert a != b


class TestSecondarySplit:
    def test_oversized_section_is_split_on_subheadings(self, tmp_path):
        """A 36KB child means the page was chopped, not reorganized."""
        big_section = "".join(
            "### 2026-05-%02d — Entry %d\n\n%s\n\n" % (d, d, "word " * 1200)
            for d in range(1, 7)
        )
        page = ("---\ntitle: Subject\ntype: person\n---\n\nintro\n\n"
                "## Current State\n\n" + big_section +
                "".join("## Section %d\n\n%s\n\n" % (i, "word " * 200) for i in range(4)))
        write(tmp_path, "people/subject.md", page)

        apply_split(tmp_path, plan_split(tmp_path))
        children = list((tmp_path / "people/subject").glob("*.md"))
        oversized = [c for c in children if len(c.read_text()) > 20000]
        assert not oversized, [c.name for c in oversized]
        assert any("current-state" in c.name for c in children)


class TestTagPhrases:
    def test_multiword_names_stay_intact(self):
        """`costa` and `rica` are meaningless alone; `costa-rica` is a concept."""
        from transforms import discriminating_tags
        tags = discriminating_tags([], "Holly session in Costa Rica")
        assert "costa-rica" in tags
        assert "costa" not in tags and "rica" not in tags

    def test_temporal_filler_is_not_a_tag(self):
        from transforms import discriminating_tags
        tags = discriminating_tags([], "Recent updates late night")
        assert not {"recent", "late", "updates"} & set(tags)
