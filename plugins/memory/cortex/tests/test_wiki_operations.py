"""Tests for the compiled-wiki operations: supersede, scan, file, lint, log.

Cortex used to retrieve well and compile nothing: a new source became one new
page, stale pages stayed in search forever, and the hand-written index rotted.
These tests pin the behaviours that fix that, and the boundaries that keep the
fixes from doing damage:

* superseding hides a page from search but never deletes or orphans it;
* a scheduled ``invalid_at`` in the future does not take effect early;
* the log keeps its parseable shape whatever the title contains;
* lint never counts journals or cron digests as orphans;
* the new bookkeeping files are never indexed as knowledge.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plugin_loader import load_cortex_plugin  # noqa: E402

_mod = load_cortex_plugin()
CortexStore = _mod.CortexStore
CortexRetriever = _mod.CortexRetriever
CortexMemoryProvider = _mod.CortexMemoryProvider

_plugin_dir = Path(str(_mod.__file__)).resolve().parent
sys.path.insert(0, str(_plugin_dir))
import wiki  # noqa: E402


def _page(root: Path, rel: str, title: str, body: str, **fm) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    extra = "".join(f"{k}: {v}\n" for k, v in fm.items())
    p.write_text(f"---\ntitle: {title}\ntags: []\n{extra}---\n\n{body}\n", encoding="utf-8")


def _rels(rows) -> list[str]:
    return [r["rel_path"] for r in rows]


# ------------------------------------------------------------- supersede


def test_supersede_hides_page_from_search_but_keeps_it_readable(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    store = CortexStore(store_path=root)
    store.write_page("people", "dana-old", "Dana works at Acme as a gardenwrangler.")
    store.write_page("people", "dana", "Dana now works at Globex as a gardenwrangler.")
    ret = CortexRetriever(store)
    assert "people/dana-old.md" in _rels(ret.search("gardenwrangler", limit=5))

    out = store.supersede("people/dana-old.md", superseded_by="people/dana.md",
                          reason="Moved employers")
    assert out["superseded_by"] == "people/dana.md"

    hits = _rels(ret.search("gardenwrangler", limit=5))
    assert "people/dana-old.md" not in hits
    assert "people/dana.md" in hits
    # Still on disk, still readable, and carries a visible banner + frontmatter.
    page = store.get_page("people/dana-old.md")
    assert page is not None
    assert page["frontmatter"]["superseded_by"] == "people/dana.md"
    assert page["body"].lstrip().startswith("> **Status: Superseded**")
    # Re-superseding replaces the banner instead of stacking a second one.
    store.supersede("people/dana-old.md", superseded_by="people/dana.md", reason="again")
    assert store.get_page("people/dana-old.md")["body"].count("Status: Superseded") == 1


def test_supersede_rejects_missing_or_self_successor(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    store.write_page("topics", "a", "alpha")
    for bad in ("topics/nope.md", "topics/a.md"):
        try:
            store.supersede("topics/a.md", superseded_by=bad)
        except (FileNotFoundError, ValueError):
            continue
        raise AssertionError(f"accepted bad successor {bad}")
    try:
        store.supersede("../escape.md")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal accepted")


def test_future_invalid_at_is_a_scheduled_expiry_not_immediate(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    future = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    past = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    _page(root, "topics/lease.md", "Lease", "The zephyrine lease is active.", invalid_at=future)
    _page(root, "topics/old-lease.md", "Old lease", "The zephyrine lease was signed.", invalid_at=past)
    store = CortexStore(store_path=root)
    hits = _rels(CortexRetriever(store).search("zephyrine lease", limit=5))
    assert "topics/lease.md" in hits
    assert "topics/old-lease.md" not in hits


def test_superseded_pages_leave_the_knowledge_map(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    _page(root, "people/kept.md", "Kept Person", "current")
    _page(root, "people/gone.md", "Gone Snapshot", "old", superseded_by="people/kept.md")
    store = CortexStore(store_path=root)
    kmap = store.knowledge_map()
    assert "Kept Person" in kmap
    assert "Gone Snapshot" not in kmap
    assert store.category_counts()["people"] == 1


def test_validity_migration_on_existing_store_preserves_fts(tmp_path: Path) -> None:
    """An older index without the validity columns migrates in place.

    The migration must not reassign rowids (that desyncs the external-content
    FTS table), so a real BM25 MATCH has to keep working afterwards.
    """
    root = tmp_path / "cortex"
    _page(root, "topics/keep.md", "Keep", "quokkaborne current fact")
    _page(root, "topics/old.md", "Old", "quokkaborne stale fact", superseded_by="topics/keep.md")
    store = CortexStore(store_path=root)
    store.close() if hasattr(store, "close") else None
    db = root / ".plugin.db"
    conn = sqlite3.connect(db)
    # Simulate a pre-migration index: drop the two columns.
    conn.executescript(
        """
        CREATE TABLE pages_old AS SELECT rel_path, category, title, tags, body, mtime, size,
            content_date FROM pages;
        """
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pages)")]
    assert "invalid_at" in cols
    conn.execute("ALTER TABLE pages DROP COLUMN superseded_by")
    conn.execute("ALTER TABLE pages DROP COLUMN invalid_at")
    conn.execute("DROP TABLE pages_old")
    conn.commit()
    conn.close()

    store = CortexStore(store_path=root)
    conn = store._conn
    conn.execute("insert into pages_fts(pages_fts) values('integrity-check')")
    assert conn.execute(
        "select count(*) from pages_fts where pages_fts match 'quokkaborne'"
    ).fetchone()[0] == 2
    assert conn.execute(
        "select invalid_at from pages where rel_path='topics/old.md'"
    ).fetchone()[0] == wiki.ALWAYS_INVALID
    assert _rels(CortexRetriever(store).search("quokkaborne", limit=5)) == ["topics/keep.md"]


# ------------------------------------------------------------------ scan


def test_scan_finds_every_page_mentioning_a_term_whole_word(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    _page(root, "people/ali.md", "Ali", "Ali runs the studio.")
    _page(root, "projects/studio.md", "Studio", "Ali and Bo share the studio.")
    _page(root, "topics/misc.md", "Misc", "We also talked about alignment.")
    _page(root, "people/old-ali.md", "Old Ali", "Ali used to run the shop.",
          superseded_by="people/ali.md")
    store = CortexStore(store_path=root)
    out = store.scan_terms(["Ali", "Nonexistent Person"])
    ali = _rels(out["matches"]["Ali"])
    assert ali[0] == "people/ali.md"  # title hit ranks first
    assert set(ali) == {"people/ali.md", "projects/studio.md"}  # whole word, live only
    assert out["matches"]["Nonexistent Person"] == []
    assert out["pages_touched"] == 2


def test_scan_caps_and_dedupes_terms(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    out = store.scan_terms(["x"] + [f"Term{i}" for i in range(40)] + ["term1", "TERM1"])
    assert len(out["terms"]) == 20
    assert "x" not in out["terms"]  # single characters are noise
    assert sum(t.lower() == "term1" for t in out["terms"]) == 1


# ------------------------------------------------------------------ file


def test_file_answer_writes_analysis_with_sources_and_logs(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    store = CortexStore(store_path=root)
    store.write_page("topics", "a", "alpha")
    store.write_page("topics", "b", "beta")
    rel = store.file_answer("Alpha vs Beta", "Alpha beats beta.",
                            ["topics/a.md", "topics/b.md"], question="which wins?")
    assert rel.startswith("analyses/")
    fm = store.get_page(rel)["frontmatter"]
    assert fm["sources"] == ["topics/a.md", "topics/b.md"]
    assert "analysis" in fm["tags"]
    assert fm["question"] == "which wins?"
    entry = store.recent_log(1)[0]
    assert entry["op"] == "query" and entry["title"] == "Alpha vs Beta"


def test_file_answer_requires_real_sources(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    for sources in ([], ["topics/ghost.md"]):
        try:
            store.file_answer("t", "b", sources)
        except (ValueError, FileNotFoundError):
            continue
        raise AssertionError(f"accepted sources={sources}")


# ------------------------------------------------------------------- log


def test_log_header_stays_parseable_and_rejects_bad_ops(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    store = CortexStore(store_path=root)
    store.log("ingest", "Title with\nnewline | and pipe", ["Updated: people/x.md", ""])
    text = (root / "log.md").read_text()
    headers = [ln for ln in text.splitlines() if ln.startswith("## [")]
    assert len(headers) == 1 and wiki.LOG_ENTRY_RE.match(headers[0])
    entry = store.recent_log(5)[0]
    assert entry["lines"] == ["Updated: people/x.md"]
    try:
        store.log("DROP TABLE", "x")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown op accepted")


def test_bookkeeping_files_are_never_indexed(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    store = CortexStore(store_path=root)
    store.write_page("topics", "real", "real page")
    store.log("note", "something")
    store.lint()
    assert (root / "log.md").exists() and (root / "catalog.md").exists()
    reopened = CortexStore(store_path=root)
    rels = {r["rel_path"] for r in reopened.list_pages(limit=100)}
    assert rels == {"topics/real.md"}


# ------------------------------------------------------------------ lint


def test_lint_counts_curated_orphans_and_ignores_generated_pages(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    _page(root, "topics/hub.md", "Hub", "See [[spoke]] and [[ghost]] and [[ghost]] and [[ghost]].")
    _page(root, "topics/spoke.md", "Spoke", "linked")
    _page(root, "topics/lonely.md", "Lonely", "nobody links here")
    _page(root, "daily/2026-01-01.md", "2026-01-01", "journal")
    _page(root, "handoff/h1.md", "Handoff", "cron digest")
    store = CortexStore(store_path=root)
    rep = store.lint()
    assert rep["broken_links"]["count"] == 3
    assert rep["missing_pages"]["examples"][0] == {"target": "ghost", "refs": 3}
    orphans = set(rep["orphans"]["examples"])
    assert "topics/lonely.md" in orphans and "topics/hub.md" in orphans
    assert "topics/spoke.md" not in orphans
    assert not any(o.startswith(("daily/", "handoff/")) for o in orphans)
    assert rep["catalog"]["pages"] == 5
    catalog = (root / "catalog.md").read_text()
    assert "[Spoke](topics/spoke.md)" in catalog
    assert store.recent_log(1)[0]["op"] == "lint"


def test_lint_flags_old_pages_that_claim_to_be_current(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    old = (dt.date.today() - dt.timedelta(days=400)).isoformat()
    new = dt.date.today().isoformat()
    _page(root, "topics/stale.md", "Stale", "The router is currently on v1.", updated=old)
    _page(root, "topics/fresh.md", "Fresh", "The router is currently on v2.", updated=new)
    _page(root, "topics/evergreen.md", "Evergreen", "Routers forward packets.", updated=old)
    _page(root, "daily/2020-01-01.md", "Old day", "currently tired")
    rep = CortexStore(store_path=root).lint(write_catalog=False, log=False)
    assert [e["page"] for e in rep["stale_current"]["examples"]] == ["topics/stale.md"]


def test_lint_reports_slug_collisions_and_dangling_successors(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    _page(root, "people/mercury.md", "Mercury", "a person")
    _page(root, "topics/mercury.md", "Mercury", "a planet")
    _page(root, "topics/old.md", "Old", "x", invalid_at="2020-01-01", superseded_by="topics/gone.md")
    rep = CortexStore(store_path=root).lint(write_catalog=False, log=False)
    assert rep["slug_collisions"]["examples"][0]["stem"] == "mercury"
    assert rep["dangling_successors"]["examples"][0]["page"] == "topics/old.md"


# ------------------------------------------------------------ tool surface


def _provider(tmp_path: Path):
    prov = CortexMemoryProvider(config={"store_path": str(tmp_path / "cortex"),
                                        "semantic": False, "rerank": False})
    prov.initialize("t")
    return prov


def test_tool_surface_exposes_new_actions(tmp_path: Path) -> None:
    prov = _provider(tmp_path)
    enum = _mod.CORTEX_TOOL_SCHEMA["parameters"]["properties"]["action"]["enum"]
    for action in ("scan", "supersede", "file", "lint", "log"):
        assert action in enum
    call = lambda **a: json.loads(prov.handle_tool_call("cortex", a))  # noqa: E731
    call(action="write", category="people", title="Zed", body="Zed builds boats.")
    call(action="write", category="people", title="Zed Old", body="Zed built carts.")
    scan = call(action="scan", terms=["Zed"])
    assert scan["pages_touched"] == 2
    sup = call(action="supersede", rel_path="people/zed-old.md", superseded_by="people/zed.md")
    assert sup["invalid_at"]
    filed = call(action="file", title="Zed summary", body="Boats.", sources=["people/zed.md"])
    assert filed["status"] == "filed"
    lint = call(action="lint")
    assert lint["superseded"]["count"] == 1
    log = call(action="log", limit=10)
    assert [e["op"] for e in log["entries"]][:3] == ["lint", "query", "supersede"]
    assert "error" in call(action="scan")
    assert "error" in call(action="file", title="x", body="y", sources=[])


def test_lint_resolves_unindexed_folder_index_and_skips_directives(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    _page(root, "people/kim/index.md", "Kim", "folder page, not indexed")
    _page(root, "topics/a.md", "A", "See [[people/kim]], [[people/kim/index]], [[index]], "
          "[[reply_to_current]], [[reply_to:42]] and [[truly-missing]].")
    rep = CortexStore(store_path=root).lint(write_catalog=False, log=False)
    assert [b["target"] for b in rep["broken_links"]["examples"]] == ["truly-missing"]


def test_scan_reports_truncation_and_counts_every_page(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    for i in range(12):
        _page(root, f"topics/t{i}.md", f"T{i}", "Mentions Wobbleton here.")
    store = CortexStore(store_path=root)
    out = store.scan_terms(["Wobbleton"])
    assert len(out["matches"]["Wobbleton"]) == 8
    assert out["totals"]["Wobbleton"] == 12
    assert out["pages_touched"] == 12
    assert out["truncated"] == ["Wobbleton"]
    full = store.scan_terms(["Wobbleton"], per_term=50)
    assert len(full["matches"]["Wobbleton"]) == 12 and full["truncated"] == []


def test_curation_brief_ignores_bookkeeping_files(tmp_path: Path) -> None:
    sys.path.insert(0, str(_plugin_dir / "scripts"))
    import weekly_curation  # noqa: PLC0415

    root = tmp_path / "cortex"
    store = CortexStore(store_path=root)
    store.write_page("topics", "real", "real page")
    store.lint()
    store.log("note", "x")
    rels = {str(p.relative_to(root)) for p in weekly_curation.iter_pages(root)}
    assert rels == {"topics/real.md"}
    brief = weekly_curation.build_brief(root, days=7)
    assert brief["work_set"] == ["topics/real.md"]


# ------------------------------------------------- review regressions (PR #155)


def test_nested_log_and_catalog_pages_are_real_pages(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    _page(root, "projects/log.md", "Project log", "nested log page")
    _page(root, "topics/catalog.md", "Catalog topic", "nested catalog page")
    store = CortexStore(store_path=root)
    store.log("note", "root bookkeeping")
    store.lint()
    rels = {r["rel_path"] for r in CortexStore(store_path=root).list_pages(limit=50)}
    assert rels == {"projects/log.md", "topics/catalog.md"}


def test_concurrent_migration_loser_does_not_crash(tmp_path: Path) -> None:
    """Gateway and cron open a pre-migration store at the same moment.

    The loser reads the column list (no invalid_at), then the winner migrates,
    then the loser decides. Without a re-check under the write lock the loser
    runs ALTER again and dies with "duplicate column name".
    """
    root = tmp_path / "cortex"
    _page(root, "topics/a.md", "A", "alpha", superseded_by="topics/b.md")
    _page(root, "topics/b.md", "B", "beta")
    CortexStore(store_path=root)
    db = root / ".plugin.db"
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE pages DROP COLUMN superseded_by")
    conn.execute("ALTER TABLE pages DROP COLUMN invalid_at")
    conn.commit()
    conn.close()

    class Interleaved:
        """Serves the stale column list to unlocked reads; winner runs between."""

        def __init__(self, real):
            self._real = real
            self._stale = None

        def execute(self, sql, *args):
            if "table_info(pages)" in sql and not self._real.in_transaction:
                if self._stale is None:
                    self._stale = self._real.execute(sql).fetchall()
                    CortexStore(store_path=root)  # the winner migrates now
                return iter(self._stale)
            return self._real.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._real, name)

    real = sqlite3.connect(db)
    real.row_factory = sqlite3.Row
    loser = Interleaved(real)
    stub = CortexStore.__new__(CortexStore)
    stub.store_path = root
    stub._init_schema(loser)
    assert real.execute(
        "select invalid_at from pages where rel_path='topics/a.md'"
    ).fetchone()[0] == wiki.ALWAYS_INVALID


def test_resupersede_without_args_keeps_banner_consistent(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    store.write_page("topics", "a", "alpha")
    store.write_page("topics", "b", "beta")
    store.supersede("topics/a.md", superseded_by="topics/b.md", reason="replaced")
    store.supersede("topics/a.md")
    body = store.get_page("topics/a.md")["body"]
    assert "Current version: [topics/b.md]" in body and "replaced" in body


def test_rewriting_superseded_page_does_not_resurrect_it(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    store.write_page("topics", "a", "zorblat alpha")
    store.write_page("topics", "b", "beta")
    store.supersede("topics/a.md", superseded_by="topics/b.md")
    store.write_page("topics", "a", "zorblat alpha edited")
    assert store.get_page("topics/a.md")["frontmatter"]["superseded_by"] == "topics/b.md"
    assert "topics/a.md" not in _rels(CortexRetriever(store).search("zorblat", limit=5))


def test_file_answer_never_overwrites(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    store.write_page("topics", "a", "alpha")
    r1 = store.file_answer("Same Q", "first", ["topics/a.md"])
    r2 = store.file_answer("Same Q", "second", ["topics/a.md"])
    assert r1 != r2
    assert "first" in store.get_page(r1)["body"] and "second" in store.get_page(r2)["body"]


def test_supersede_refuses_non_pages(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    store = CortexStore(store_path=root)
    store.log("note", "x")
    (root / "notes.txt").write_text("plain")
    for bad in (".plugin.db", "notes.txt", "log.md"):
        try:
            store.supersede(bad)
        except ValueError:
            continue
        raise AssertionError(f"superseded non-page {bad}")
    assert (root / "notes.txt").read_text() == "plain"


def test_list_hides_superseded_pages(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    store.write_page("topics", "a", "alpha")
    store.write_page("topics", "b", "beta")
    store.supersede("topics/a.md", superseded_by="topics/b.md")
    assert _rels(store.list_pages("topics")) == ["topics/b.md"]
    assert _rels(store.list_pages()) == ["topics/b.md"]


def test_curation_keeps_nested_log_pages_and_sees_folder_indexes(tmp_path: Path) -> None:
    sys.path.insert(0, str(_plugin_dir / "scripts"))
    import weekly_curation  # noqa: PLC0415

    root = tmp_path / "cortex"
    _page(root, "projects/log.md", "Project log", "See [[topics/index]].")
    _page(root, "topics/index.md", "Topics", "folder index")
    store = CortexStore(store_path=root)
    store.log("note", "x")
    store.lint()
    rels = {str(p.relative_to(root)) for p in weekly_curation.iter_pages(root)}
    assert rels == {"projects/log.md"}
    assert weekly_curation._lint_summary(root)["broken_links"]["count"] == 0


def test_lint_resolves_markdown_links_relative_to_page(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    _page(root, "topics/a.md", "A", "See [B](sub/b.md) and [C](../people/c.md).")
    _page(root, "topics/sub/b.md", "B", "b")
    _page(root, "people/c.md", "C", "c")
    rep = CortexStore(store_path=root).lint(write_catalog=False, log=False)
    assert rep["broken_links"]["count"] == 0
    assert "topics/sub/b.md" not in rep["orphans"]["examples"]
    assert "people/c.md" not in rep["orphans"]["examples"]


def test_hyphenated_validity_survives_rewrite(tmp_path: Path) -> None:
    root = tmp_path / "cortex"
    _page(root, "topics/b.md", "B", "beta")
    (root / "topics/a.md").write_text(
        "---\ntitle: A\nsuperseded-by: topics/b.md\n---\n\nqwibble alpha\n")
    store = CortexStore(store_path=root)
    store.write_page("topics", "a", "qwibble alpha edited")
    assert "topics/a.md" not in _rels(CortexRetriever(store).search("qwibble", limit=5))


def test_write_keeps_filed_answer_provenance(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    store.write_page("topics", "a", "alpha")
    rel = store.file_answer("Q", "v1", ["topics/a.md"], question="why?")
    store.write_page("analyses", Path(rel).stem, "v2")
    fm = store.get_page(rel)["frontmatter"]
    assert fm["sources"] == ["topics/a.md"] and fm["question"] == "why?"


def test_log_action_can_append_an_ingest_entry(tmp_path: Path) -> None:
    prov = _provider(tmp_path)
    call = lambda **a: json.loads(prov.handle_tool_call("cortex", a))  # noqa: E731
    out = call(action="log", title="Imported meeting notes",
               body="Updated: people/zed.md\nUpdated: projects/boat.md")
    assert out["logged"].endswith("ingest | Imported meeting notes")
    entry = call(action="log", limit=1)["entries"][0]
    assert entry["lines"] == ["Updated: people/zed.md", "Updated: projects/boat.md"]
    assert "error" in call(action="log", title="x", op="bogus")
