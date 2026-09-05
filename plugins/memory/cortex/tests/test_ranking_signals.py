"""Tests for exact-phrase recall and intent-gated recency ranking.

Two ranking signals, each fixing a measured defect:

1. **Phrases.** ``_sanitize_query`` OR'd the top eight tokens and silently threw
   quotes away, so ``"chat admission busy"`` searched for any page containing
   *chat* OR *admission* OR *busy*, and a proper noun like ``Dana Whitfield`` matched
   every page mentioning either word. On a personal knowledge base that is most
   of them.
2. **Recency.** Only 0.2% of pages on the live store carry a frontmatter date,
   yet person and project folders accumulate dated snapshots, so semantic
   similarity happily ranks a confident months-old page above the page that
   replaced it.

The danger in both is over-correction: a phrase requirement that removes the
recall floor, or a recency prior that buries evergreen pages. These tests pin
those boundaries.
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plugin_loader import load_cortex_plugin  # noqa: E402

_mod = load_cortex_plugin()
CortexStore = _mod.CortexStore
CortexRetriever = _mod.CortexRetriever

_plugin_dir = Path(str(_mod.__file__)).resolve().parent
sys.path.insert(0, str(_plugin_dir))
import retrieval as rt  # noqa: E402
import store as store_mod  # noqa: E402


# --------------------------------------------------------------------------
# Phrase extraction
# --------------------------------------------------------------------------

def test_quoted_span_becomes_a_phrase() -> None:
    assert rt.extract_phrases('"chat admission busy" error') == ["chat admission busy"]


def test_capitalized_proper_noun_becomes_a_phrase() -> None:
    assert rt.extract_phrases("who is Dana Whitfield") == ["Dana Whitfield"]


def test_single_capitalized_word_is_not_a_phrase() -> None:
    """One word needs no phrase treatment; tokens already handle it."""
    assert rt.extract_phrases("tell me about Cortex") == []


def test_sentence_initial_stopwords_are_not_phrases() -> None:
    assert rt.extract_phrases("The Fleet is fine") != ["The Fleet"]


def test_phrases_are_deduplicated_case_insensitively() -> None:
    out = rt.extract_phrases('"Dana Whitfield" and Dana Whitfield again')
    assert out == ["Dana Whitfield"]


def test_phrase_count_is_capped() -> None:
    q = "Alpha Beta and Gamma Delta and Epsilon Zeta and Eta Theta"
    assert len(rt.extract_phrases(q, max_phrases=2)) == 2


def test_sanitize_emits_phrase_and_keeps_token_floor() -> None:
    """The phrase is a precision signal; bare tokens remain the recall floor."""
    out = rt._sanitize_query("who is Dana Whitfield")

    assert '"Dana Whitfield"' in out
    assert "dana" in out and "whitfield" in out, "tokens must survive alongside the phrase"


def test_sanitize_strips_fts_metacharacters_from_phrases() -> None:
    out = rt._sanitize_query('"broken: thing (here)" now')

    assert "(" not in out and ")" not in out and ":" not in out
    # Still a usable query rather than empty.
    assert out


def test_sanitize_handles_unbalanced_quote() -> None:
    out = rt._sanitize_query('a "dangling quote here')
    assert out  # must not raise or return empty


def test_sanitize_empty_and_stopword_only() -> None:
    assert rt._sanitize_query("") == ""
    assert rt._sanitize_query("the and of") == ""


# --------------------------------------------------------------------------
# Content dates
# --------------------------------------------------------------------------

def test_content_date_prefers_frontmatter() -> None:
    got = store_mod.content_date("daily/2026-01-01.md", {"date": "2026-05-05"})
    assert got == "2026-05-05"


def test_content_date_falls_back_to_path() -> None:
    assert store_mod.content_date("daily/2026-01-02.md", {}) == "2026-01-02"


def test_content_date_none_when_undated() -> None:
    assert store_mod.content_date("topics/preferences.md", {}) is None


def test_content_date_ignores_unparseable_frontmatter() -> None:
    assert store_mod.content_date("topics/x.md", {"date": "someday"}) is None


def test_content_date_is_indexed(tmp_path: Path) -> None:
    store_path = tmp_path / "cortex"
    (store_path / "daily").mkdir(parents=True)
    (store_path / "daily" / "2026-03-04.md").write_text("# Log\n\nbody\n", encoding="utf-8")
    (store_path / "topics").mkdir(parents=True)
    (store_path / "topics" / "evergreen.md").write_text("# Ever\n\nbody\n", encoding="utf-8")
    store = CortexStore(store_path=str(store_path))

    rows = dict(
        store._conn.execute("SELECT rel_path, content_date FROM pages").fetchall()
    )

    assert rows["daily/2026-03-04.md"] == "2026-03-04"
    assert rows["topics/evergreen.md"] is None


def test_migration_backfills_content_date_on_existing_store(tmp_path: Path) -> None:
    """An existing store must gain populated dates, not a NULL column."""
    store_path = tmp_path / "cortex"
    (store_path / "daily").mkdir(parents=True)
    (store_path / "daily" / "2026-06-07.md").write_text("# Log\n\nbody\n", encoding="utf-8")
    store = CortexStore(store_path=str(store_path))
    db_path = store.db_path
    # Simulate the pre-migration schema.
    store._conn.execute("DROP INDEX IF EXISTS pages_content_date_idx")
    store._conn.commit()
    store.close()

    reopened = CortexStore(store_path=str(store_path), db_path=str(db_path))
    got = reopened._conn.execute(
        "SELECT content_date FROM pages WHERE rel_path = ?", ("daily/2026-06-07.md",)
    ).fetchone()[0]

    assert got == "2026-06-07"


def test_migration_does_not_corrupt_the_fts_index(tmp_path: Path) -> None:
    """Regression: the first migration broke lexical search on every page.

    pages_fts is an FTS5 external-content table keyed by rowid, and the reindex
    path uses INSERT OR REPLACE, which REASSIGNS rowids. Forcing a whole-store
    reindex to populate the new column therefore desynced the index, and every
    BM25 query began raising "missing row N from content table". Measured on a
    real-corpus replica: 24 of 40 eval queries went from rank 1 to no result.
    """
    store_path = tmp_path / "cortex"
    (store_path / "topics").mkdir(parents=True)
    for i in range(25):
        (store_path / "topics" / f"p{i:02d}.md").write_text(
            f"---\ntitle: Page {i}\n---\n\nzebra content number {i}\n", encoding="utf-8"
        )
    store = CortexStore(store_path=str(store_path))
    db_path = store.db_path

    # Drop back to the pre-migration schema, preserving rowids.
    store._conn.execute("ALTER TABLE pages RENAME TO pages_old")
    store._conn.execute(
        "CREATE TABLE pages (rel_path TEXT PRIMARY KEY, category TEXT NOT NULL,"
        " title TEXT, tags TEXT, body TEXT, mtime REAL, size INTEGER)"
    )
    store._conn.execute(
        "INSERT INTO pages (rowid, rel_path, category, title, tags, body, mtime, size)"
        " SELECT rowid, rel_path, category, title, tags, body, mtime, size FROM pages_old"
    )
    store._conn.execute("DROP TABLE pages_old")
    store._conn.commit()
    store.close()

    migrated = CortexStore(store_path=str(store_path), db_path=str(db_path))

    # The FTS index must still be queryable AND self-consistent.
    migrated._conn.execute("INSERT INTO pages_fts(pages_fts) VALUES('integrity-check')")
    rows = migrated._conn.execute(
        "SELECT pages.rel_path, bm25(pages_fts) FROM pages_fts"
        " JOIN pages ON pages.rel_path = pages_fts.rel_path"
        " WHERE pages_fts MATCH ? ORDER BY 2 LIMIT 5",
        ("zebra",),
    ).fetchall()

    assert len(rows) == 5, "BM25 lexical search must still work after migration"


# --------------------------------------------------------------------------
# Recency intent + multiplier
# --------------------------------------------------------------------------

def test_recency_intent_detection() -> None:
    for q in [
        "what is the current model",
        "latest fleet status",
        "where do things stand with backups",
        "what's the status of the migration",
        "is that still true",
    ]:
        assert rt.wants_recency(q), q


def test_non_temporal_queries_do_not_trigger_recency() -> None:
    for q in [
        "how does the reranker work",
        "Dana Whitfield background",
        "explain the chunking design",
    ]:
        assert not rt.wants_recency(q), q


def test_multiplier_is_neutral_for_undated_pages() -> None:
    """Most of the corpus is undated; penalizing it would demote the majority."""
    assert rt._recency_multiplier(None) == 1.0
    assert rt._recency_multiplier("") == 1.0
    assert rt._recency_multiplier("not-a-date") == 1.0


def test_multiplier_decays_with_age() -> None:
    today = date(2026, 9, 4)
    fresh = rt._recency_multiplier("2026-09-04", today=today)
    half = rt._recency_multiplier("2026-03-08", today=today)  # ~180 days
    old = rt._recency_multiplier("2024-09-04", today=today)

    assert fresh > half > old
    assert fresh <= 1.0 + rt._RECENCY_MAX_BOOST + 1e-9
    assert old >= 1.0, "decay must never penalize below neutral"
    assert abs(half - (1.0 + rt._RECENCY_MAX_BOOST * 0.5)) < 0.02


def test_future_dates_do_not_exceed_the_cap() -> None:
    today = date(2026, 9, 4)
    future = rt._recency_multiplier("2027-01-01", today=today)
    assert future <= 1.0 + rt._RECENCY_MAX_BOOST + 1e-9


# --------------------------------------------------------------------------
# Ranking integration
# --------------------------------------------------------------------------

def _dated_store(tmp_path: Path) -> CortexStore:
    store_path = tmp_path / "cortex"
    (store_path / "people").mkdir(parents=True)
    today = date.today()
    for name, delta in (("old", 400), ("new", 2)):
        d = (today - timedelta(days=delta)).isoformat()
        (store_path / "people" / f"state-{d}.md").write_text(
            f"---\ntitle: State {name}\ndate: {d}\n---\n\nzebra project status is {name}.\n",
            encoding="utf-8",
        )
    (store_path / "topics").mkdir(parents=True)
    (store_path / "topics" / "evergreen.md").write_text(
        "---\ntitle: Zebra Principles\n---\n\nzebra project guiding principles.\n",
        encoding="utf-8",
    )
    return CortexStore(store_path=str(store_path))


def test_boost_is_strong_enough_to_actually_reorder() -> None:
    """Regression: the first implementation was a silent no-op.

    Scoring rank as 1/(1+position) puts adjacent ranks 2x apart, so a
    multiplier capped at 1.30 could never move a row and the whole feature did
    nothing while looking correct. The rank gap must stay comparable to the
    boost.
    """
    gap = (1.0 / 60.0) / (1.0 / 61.0)
    assert gap < 1.0 + rt._RECENCY_MAX_BOOST, (
        "adjacent-rank gap must be smaller than the max boost, or recency is inert"
    )


def test_a_much_better_match_still_outranks_a_fresh_page() -> None:
    """Recency breaks ties near the top; it must not haul a page up the list.

    A capped multiplier bounds the size of the boost but NOT the distance a row
    travels, because rank-derived scores are near-uniform. A fresh page ranked
    tenth once overtook a far stronger match ranked first. Only the head window
    is eligible.
    """
    today = date.today()
    rows = [{"rel_path": f"p{i}.md"} for i in range(10)]
    fresh = "p9.md"  # deliberately outside the eligible window

    class _Store:
        class _Cursor:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        class _Conn:
            @classmethod
            def execute(cls, _sql, rels):
                return _Store._Cursor(
                    [
                        {
                            "rel_path": r,
                            "content_date": today.isoformat() if r == fresh else None,
                        }
                        for r in rels
                    ]
                )

        _conn = _Conn()

    ret = CortexRetriever(_Store())
    out = ret._apply_recency("current status", rows)

    assert out[0]["rel_path"] == "p0.md", "a 9-place relevance gap must not be erased"
    assert [r["rel_path"] for r in out] == [r["rel_path"] for r in rows], (
        "a page outside the recency window must not move at all"
    )


def test_recency_reorders_a_fresh_page_inside_the_window() -> None:
    """The window bounds reach without making the feature inert."""
    today = date.today()
    rows = [{"rel_path": f"p{i}.md"} for i in range(10)]
    fresh = "p2.md"  # inside the window, adjacent-ish to the top

    class _Store:
        class _Cursor:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        class _Conn:
            @classmethod
            def execute(cls, _sql, rels):
                return _Store._Cursor(
                    [
                        {
                            "rel_path": r,
                            "content_date": today.isoformat() if r == fresh else None,
                        }
                        for r in rels
                    ]
                )

        _conn = _Conn()

    ret = CortexRetriever(_Store())
    out = ret._apply_recency("current status", rows)

    assert any("recency_boost" in r for r in out), "the date lookup must actually succeed"
    assert out[0]["rel_path"] == fresh, "a fresh page inside the window should win a near-tie"
    assert [r["rel_path"] for r in out[len(out) - 6 :]] == [f"p{i}.md" for i in range(4, 10)], (
        "rows past the window keep their original order"
    )


def test_recency_reorders_only_on_temporal_intent(tmp_path: Path) -> None:
    store = _dated_store(tmp_path)
    ret = CortexRetriever(store)

    neutral = ret.search("zebra project", limit=5)
    assert not any("recency_boost" in r for r in neutral), "no boost without intent"

    temporal = ret.search("current zebra project status", limit=5)
    assert any("recency_boost" in r for r in temporal), "expected a boost on intent"


def test_newer_page_wins_a_tie_on_temporal_query(tmp_path: Path) -> None:
    store = _dated_store(tmp_path)
    ret = CortexRetriever(store)

    rows = ret.search("current zebra project status", limit=5)
    paths = [r["rel_path"] for r in rows]
    newer = [p for p in paths if "state-" in p]

    assert newer, paths
    # Among the two dated snapshots, the recent one must come first.
    dated = [p for p in paths if "state-" in p]
    assert dated == sorted(dated, reverse=True), f"newer snapshot should lead: {dated}"


def test_undated_evergreen_page_is_not_buried(tmp_path: Path) -> None:
    """A global decay would sink undated reference pages. It must not."""
    store = _dated_store(tmp_path)
    ret = CortexRetriever(store)

    rows = ret.search("current zebra project status", limit=5)
    paths = [r["rel_path"] for r in rows]

    assert "topics/evergreen.md" in paths, paths


def test_recency_is_inert_without_the_column(tmp_path: Path) -> None:
    """An older store predating content_date must keep working."""
    store = _dated_store(tmp_path)
    store._conn.execute("ALTER TABLE pages RENAME TO pages_backup")
    store._conn.execute(
        "CREATE TABLE pages (rel_path TEXT PRIMARY KEY, category TEXT NOT NULL,"
        " title TEXT, tags TEXT, body TEXT, mtime REAL, size INTEGER)"
    )
    store._conn.execute(
        "INSERT INTO pages SELECT rel_path, category, title, tags, body, mtime, size FROM pages_backup"
    )
    store._conn.commit()
    ret = CortexRetriever(store)

    rows = ret._apply_recency("current status", [{"rel_path": "people/x.md"}])

    assert rows == [{"rel_path": "people/x.md"}]


def test_migration_survives_a_non_utf8_page(tmp_path: Path) -> None:
    """One unreadable file must not abort the migration and brick the store.

    UnicodeDecodeError is not an OSError, so a narrow ``except OSError`` here
    would escape and leave the store unopenable — all retrieval lost, not just
    the recency signal.
    """
    store_path = tmp_path / "cortex"
    (store_path / "daily").mkdir(parents=True)
    good = store_path / "daily" / "2026-03-04.md"
    good.write_text("# Good page\n\nreadable\n", encoding="utf-8")

    store = CortexStore(store_path=str(store_path))
    db_path = store.db_path
    store._conn.execute("DROP INDEX IF EXISTS pages_content_date_idx")
    store._conn.execute("ALTER TABLE pages DROP COLUMN content_date")
    store._conn.commit()
    store.close()

    # The indexed file is now unreadable as UTF-8.
    good.write_bytes(b"\xff\xfe binary \x00 garbage")

    reopened = CortexStore(store_path=str(store_path), db_path=str(db_path))
    cols = {r["name"] for r in reopened._conn.execute("PRAGMA table_info(pages)")}
    assert "content_date" in cols, "migration must complete despite the unreadable file"
    got = reopened._conn.execute(
        "SELECT content_date FROM pages WHERE rel_path = ?", ("daily/2026-03-04.md",)
    ).fetchone()[0]
    assert got == "2026-03-04", "must fall back to the date in the path"


def test_editing_a_page_does_not_break_lexical_search(tmp_path: Path) -> None:
    """Editing any page must not desync the external-content FTS index.

    `pages_fts` is keyed by rowid. `INSERT OR REPLACE INTO pages` deletes and
    reinserts, assigning a NEW rowid, after which every BM25-ordered MATCH
    raises "missing row N from content table" — total lexical-search failure
    from a single edit, silently degrading search to vector-only.
    """
    store_path = tmp_path / "cortex"
    (store_path / "daily").mkdir(parents=True)
    for i in range(5):
        (store_path / "daily" / f"2026-03-0{i + 1}.md").write_text(
            f"# Page {i}\n\nalpha bravo {i}\n", encoding="utf-8"
        )

    store = CortexStore(store_path=str(store_path))
    db_path = store.db_path
    before = [r[0] for r in store._conn.execute("SELECT rowid FROM pages ORDER BY rowid")]
    store.close()

    time.sleep(0.01)  # ensure a distinct mtime so the page reindexes
    (store_path / "daily" / "2026-03-01.md").write_text(
        "# Page 0 edited\n\nalpha delta echo\n", encoding="utf-8"
    )

    reopened = CortexStore(store_path=str(store_path), db_path=str(db_path))
    after = [r[0] for r in reopened._conn.execute("SELECT rowid FROM pages ORDER BY rowid")]
    assert before == after, "rowids must be preserved or the FTS index desyncs"

    rows = reopened._conn.execute(
        "SELECT rel_path FROM pages_fts WHERE pages_fts MATCH ? ORDER BY bm25(pages_fts) LIMIT 5",
        ("alpha",),
    ).fetchall()
    assert len(rows) == 5, "BM25 lexical search must survive a page edit"

    hits = reopened._conn.execute(
        "SELECT rel_path FROM pages_fts WHERE pages_fts MATCH ? LIMIT 5", ("echo",)
    ).fetchall()
    assert [r["rel_path"] for r in hits] == ["daily/2026-03-01.md"], "edited text must be indexed"
