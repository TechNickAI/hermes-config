"""Tests for chunk-level semantic retrieval.

The defect being fixed, measured on a live 1,206-page store: the embedding
client truncates input at 8,000 chars, 217 pages (18%) exceeded that, and a
globally-unique-token scan showed 111 of 114 large pages carried content that
existed ONLY past the cutoff. That text was unreachable by semantic search.

The tests that matter here are the ones that would catch a silent regression:
chunking must lose no text, the fast and slow vector scans must rank
identically, and small pages must NOT gain chunk rows (every extra vector is
per-turn latency).
"""

from __future__ import annotations

import sqlite3
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plugin_loader import load_cortex_plugin  # noqa: E402

_mod = load_cortex_plugin()
CortexStore = _mod.CortexStore

_plugin_dir = Path(str(_mod.__file__)).resolve().parent
sys.path.insert(0, str(_plugin_dir))
import chunking  # noqa: E402
import embeddings as emb  # noqa: E402


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------

def test_empty_body_yields_no_chunks() -> None:
    assert chunking.split_markdown("") == []
    assert chunking.split_markdown("   \n\n  ") == []


def test_splits_on_headings_and_keeps_heading_path() -> None:
    body = (
        "# Daily Log\n\nIntro text.\n\n"
        "## Morning\n\nDid the morning thing.\n\n"
        "## Evening\n\nDid the evening thing.\n"
    )
    chunks = chunking.split_markdown(body, target_chars=40)
    trails = [t for t, _ in chunks]

    assert any("Morning" in t for t in trails)
    assert any("Evening" in t for t in trails)
    assert any("Daily Log" in t for t in trails)


def test_heading_path_nests_subsections() -> None:
    body = "# Top\n\nA.\n\n## Middle\n\nB.\n\n### Leaf\n\nC.\n"
    chunks = chunking.split_markdown(body, target_chars=10)
    trails = [t for t, _ in chunks]

    assert any(t == "Top > Middle > Leaf" for t in trails), trails


def test_sibling_heading_pops_the_stack() -> None:
    body = "# Top\n\nA.\n\n## One\n\nB.\n\n## Two\n\nC.\n"
    chunks = chunking.split_markdown(body, target_chars=10)
    trails = [t for t, _ in chunks]

    assert "Top > One > Two" not in trails, "sibling headings must not nest"
    assert any(t == "Top > Two" for t in trails), trails


def test_chunking_is_lossless() -> None:
    """The whole point is that no text is discarded. Verified on real pages too."""
    body = "# A\n\n" + ("alpha " * 900) + "\n\n## B\n\n" + ("beta " * 900) + "\n"
    chunks = chunking.split_markdown(body)

    joined = "".join(text for _, text in chunks)
    assert "".join(joined.split()) == "".join(body.split())


def test_oversized_single_paragraph_is_split_not_dropped() -> None:
    body = "# Big\n\n" + ("x" * 20000) + "\n"
    chunks = chunking.split_markdown(body, target_chars=3000, max_chars=6000)

    joined = "".join(text for _, text in chunks)
    assert "".join(joined.split()) == "".join(body.split())
    assert len(chunks) > 1


def test_small_sections_are_packed_together() -> None:
    """Many tiny headings must not become one vector each."""
    body = "".join(f"## H{i}\n\nshort body {i}\n\n" for i in range(40))
    chunks = chunking.split_markdown(body, target_chars=3000)

    assert len(chunks) < 10, f"expected packing, got {len(chunks)} chunks"


def test_chunk_embedding_text_carries_context() -> None:
    out = chunking.chunk_embedding_text("Daily Log", "ops, fleet", "Daily Log > Lesson", "he agreed")

    assert "Daily Log > Lesson" in out
    assert "Tags: ops, fleet" in out
    assert "he agreed" in out


# --------------------------------------------------------------------------
# Vector scan
# --------------------------------------------------------------------------

def _packed(vals: list[float]) -> bytes:
    return struct.pack(f"<{len(vals)}f", *vals)


def test_top_matches_ranks_by_dot_product() -> None:
    cands = [
        ("far", _packed([0.0, 1.0])),
        ("near", _packed([1.0, 0.0])),
        ("mid", _packed([0.7, 0.7])),
    ]
    out = emb.top_matches([1.0, 0.0], cands, dim=2, limit=3)

    assert [k for k, _ in out] == ["near", "mid", "far"]


def test_top_matches_skips_wrong_dimension_blobs() -> None:
    """A mixed-dimension index is real during an embedder migration."""
    cands = [("good", _packed([1.0, 0.0])), ("bad", _packed([1.0, 0.0, 0.0]))]
    out = emb.top_matches([1.0, 0.0], cands, dim=2, limit=5)

    assert [k for k, _ in out] == ["good"]


def test_top_matches_handles_empty_inputs() -> None:
    assert emb.top_matches([], [("a", _packed([1.0]))], dim=1, limit=3) == []
    assert emb.top_matches([1.0], [], dim=1, limit=3) == []


def test_fast_and_slow_scans_agree(monkeypatch) -> None:
    """A fast path that silently reorders results is worse than no fast path.

    numpy is optional (the repo must test from a bare clone), so both branches
    must exist and must produce identical rankings.
    """
    import random

    random.seed(1234)
    dim = 16
    cands = [
        (f"v{i}", _packed([random.uniform(-1, 1) for _ in range(dim)]))
        for i in range(50)
    ]
    query = [random.uniform(-1, 1) for _ in range(dim)]

    fast = emb.top_matches(query, cands, dim=dim, limit=10)
    monkeypatch.setattr(emb, "_np", None)
    slow = emb.top_matches(query, cands, dim=dim, limit=10)

    assert [k for k, _ in fast] == [k for k, _ in slow]
    for (_, a), (_, b) in zip(fast, slow):
        assert abs(a - b) < 1e-5


# --------------------------------------------------------------------------
# Store integration
# --------------------------------------------------------------------------

class _StubEmbedder:
    """Deterministic bag-of-words embedder; no network, stable rankings."""

    model = "stub-model"
    dimensions = 26

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out = []
        for t in texts:
            vec = [0.0] * 26
            for ch in t.lower():
                if "a" <= ch <= "z":
                    vec[ord(ch) - 97] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out

    def health(self) -> bool:
        return True


def _store_with(tmp_path: Path, pages: dict[str, str]) -> CortexStore:
    store_path = tmp_path / "cortex"
    for rel, body in pages.items():
        p = store_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return CortexStore(store_path=str(store_path), embedder=_StubEmbedder())


def test_small_pages_get_no_chunk_rows(tmp_path: Path) -> None:
    """Every extra vector is per-turn latency; small pages must stay 1:1."""
    store = _store_with(tmp_path, {"topics/small.md": "# Small\n\nshort body\n"})
    store.backfill_embeddings()

    assert store.backfill_chunk_embeddings() == 0
    assert store.embedding_stats()["chunks"] == 0


def test_large_pages_get_chunk_rows(tmp_path: Path) -> None:
    body = "# Big\n\n" + ("alpha " * 1200) + "\n\n## Tail\n\n" + ("omega " * 1200) + "\n"
    store = _store_with(tmp_path, {"topics/big.md": body})
    store.backfill_embeddings()

    written = store.backfill_chunk_embeddings()

    assert written > 1
    stats = store.embedding_stats()
    assert stats["chunks"] == written
    assert stats["chunked_pages"] == 1


def test_chunk_backfill_is_idempotent(tmp_path: Path) -> None:
    body = "# Big\n\n" + ("alpha " * 1200) + "\n\n## Tail\n\n" + ("omega " * 1200) + "\n"
    store = _store_with(tmp_path, {"topics/big.md": body})
    store.backfill_embeddings()
    first = store.backfill_chunk_embeddings()

    assert first > 0
    assert store.backfill_chunk_embeddings() == 0, "second run must be a no-op"


def test_tail_only_content_becomes_retrievable(tmp_path: Path) -> None:
    """The core regression: text past the embed cutoff must be findable.

    The needle sits far beyond CHUNK_THRESHOLD_CHARS, so the page-level vector
    cannot represent it.
    """
    filler = "alpha bravo charlie delta " * 400
    body = f"# Journal\n\n{filler}\n\n## Zebra Notes\n\nzzz zebra zebra zebra xylophone\n"
    store = _store_with(tmp_path, {"daily/journal.md": body})
    store.backfill_embeddings()

    before = store.vector_search("zebra xylophone", limit=3)
    assert not any(r["source"] == "vector-chunk" for r in before)

    store.backfill_chunk_embeddings()
    after = store.vector_search("zebra xylophone", limit=3)

    assert after, "expected a hit after chunk backfill"
    assert after[0]["rel_path"] == "daily/journal.md"
    assert after[0]["source"] == "vector-chunk"
    assert "zebra" in after[0]["snippet"].lower(), "snippet must show the matching text"


def test_chunk_hits_do_not_duplicate_a_page(tmp_path: Path) -> None:
    """Several chunks of one page must collapse to a single result row."""
    body = "# Doc\n\n" + "".join(
        f"## Section {i}\n\nzebra content {i} " + ("pad " * 300) + "\n\n" for i in range(6)
    )
    store = _store_with(tmp_path, {"topics/doc.md": body})
    store.backfill_embeddings()
    store.backfill_chunk_embeddings()

    rows = store.vector_search("zebra content", limit=5)
    paths = [r["rel_path"] for r in rows]

    assert len(paths) == len(set(paths)), f"duplicate pages in results: {paths}"


def test_editing_a_page_invalidates_its_chunks(tmp_path: Path) -> None:
    body = "# Big\n\n" + ("alpha " * 1200) + "\n\n## Tail\n\n" + ("omega " * 1200) + "\n"
    store = _store_with(tmp_path, {"topics/big.md": body})
    store.backfill_embeddings()
    store.backfill_chunk_embeddings()
    assert store.embedding_stats()["chunks"] > 0

    (store.store_path / "topics/big.md").write_text("# Big\n\nnow tiny\n", encoding="utf-8")
    store._reindex_changed()

    assert store.embedding_stats()["chunks"] == 0, "stale chunks must not survive an edit"


def test_deleting_a_page_removes_its_chunks(tmp_path: Path) -> None:
    body = "# Big\n\n" + ("alpha " * 1200) + "\n\n## Tail\n\n" + ("omega " * 1200) + "\n"
    store = _store_with(tmp_path, {"topics/big.md": body})
    store.backfill_embeddings()
    store.backfill_chunk_embeddings()

    (store.store_path / "topics/big.md").unlink()
    store._reindex_changed()

    assert store.embedding_stats()["chunks"] == 0


def test_no_embedder_means_no_chunk_work(tmp_path: Path) -> None:
    store_path = tmp_path / "cortex"
    (store_path / "topics").mkdir(parents=True)
    (store_path / "topics" / "p.md").write_text("# P\n\n" + ("x " * 6000), encoding="utf-8")
    store = CortexStore(store_path=str(store_path))

    assert store.backfill_chunk_embeddings() == 0


def test_category_filter_applies_to_chunk_tier(tmp_path: Path) -> None:
    body = "# Doc\n\n" + ("alpha " * 1200) + "\n\n## Zebra\n\nzebra xylophone notes\n"
    store = _store_with(tmp_path, {"daily/d.md": body, "topics/t.md": body})
    store.backfill_embeddings()
    store.backfill_chunk_embeddings()

    rows = store.vector_search("zebra xylophone", limit=5, category="topics")

    assert rows
    assert all(r["category"] == "topics" for r in rows)


def test_vector_scan_does_not_load_every_body(tmp_path: Path) -> None:
    """The scan must carry vectors only; text is hydrated for winners alone.

    Selecting p.body for every embedded page to build 240-char snippets cost
    ~29MB peak per query on a 1,200-page store, on the gateway thread, for text
    that was immediately discarded.
    """
    pages = {f"topics/p{i}.md": f"# Page {i}\n\n" + f"filler body {i} " * 200 for i in range(12)}
    store = _store_with(tmp_path, pages)
    store.backfill_embeddings()

    seen: list[str] = []
    real_conn = store._conn

    class _SpyConn:
        def execute(self, sql, *args):
            seen.append(" ".join(str(sql).split()))
            return real_conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(real_conn, name)

    store._tls.conn = _SpyConn()
    try:
        rows = store.vector_search("filler body 3", limit=3)
    finally:
        store._tls.conn = real_conn

    assert rows, "search should still return results"
    scans = [s for s in seen if "FROM page_embeddings e" in s]
    assert scans, "expected the page-embedding scan"
    for s in scans:
        selected = s.split("FROM")[0]
        assert "body" not in selected, f"the scan must not select page bodies: {s}"


def test_giant_heading_cannot_crowd_out_chunk_text() -> None:
    """Context prefixes are page-derived, so they must be bounded.

    An absurdly long title or heading would otherwise consume the embedder's
    whole input budget and push out the body it was meant to describe.
    """
    text = "the actual chunk body that must survive"
    out = chunking.chunk_embedding_text("T" * 5000, "g" * 5000, "H" * 5000, text)

    assert text in out, "chunk body must survive an oversized context prefix"
    assert len(out) < 1000, f"context prefix must be bounded, got {len(out)} chars"


def test_write_failure_does_not_leave_an_open_transaction(tmp_path: Path) -> None:
    """A mid-batch failure must roll back, not park a transaction on the conn.

    Connections are cached per thread and the write lock is released on exit,
    so an abandoned transaction would block the next writer indefinitely.
    """
    pages = {"topics/big.md": "# Big\n\n" + ("## S%d\n\n" % 0) + "x" * 9000}
    store = _store_with(tmp_path, pages)

    real_conn = store._conn
    calls = {"n": 0}

    class _FailingConn:
        def execute(self, sql, *args):
            if "INSERT OR REPLACE INTO chunk_embeddings" in str(sql):
                calls["n"] += 1
                if calls["n"] == 1:
                    return real_conn.execute(sql, *args)
                raise sqlite3.OperationalError("simulated mid-batch failure")
            return real_conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(real_conn, name)

    store._tls.conn = _FailingConn()
    try:
        written = store.backfill_chunk_embeddings()
    finally:
        store._tls.conn = real_conn

    assert written == 0, "a failed batch must report nothing written"
    assert not real_conn.in_transaction, "transaction must be rolled back, not left open"
