"""Hybrid semantic retrieval tests for Cortex.

These tests keep the embedding client deterministic: no network calls, no model
downloads. They prove the storage/retrieval contract that a live OpenAI-compatible
embedding service later satisfies.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from retrieval import CortexRetriever  # noqa: E402
from store import CortexStore  # noqa: E402


class FakeEmbedder:
    """Tiny deterministic embedder keyed by semantic concepts.

    The vectors are intentionally simple unit-ish axes. This makes the expected
    ranking obvious: "inheritance" query should retrieve the estate-planning page
    even though the page lacks the query's literal token.
    """

    def __init__(self) -> None:
        self.dimensions = 3
        self.model = "fake-semantic"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            t = text.lower()
            if any(w in t for w in ["inheritance", "estate", "trust", "legacy", "wealth"]):
                out.append([1.0, 0.0, 0.0])
            elif any(w in t for w in ["reels", "caption", "composer", "instagram", "facebook"]):
                out.append([0.0, 1.0, 0.0])
            else:
                out.append([0.0, 0.0, 1.0])
        return out


def test_vector_backfill_indexes_pages_and_records_metadata(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex", embedder=FakeEmbedder())
    store.write_page("topics", "estate-blueprint", "# Estate Blueprint\nLegacy trust design for family wealth.")

    count = store.backfill_embeddings(force=True)
    assert count == 1

    row = store._conn.execute(
        "SELECT rel_path, model, dimensions, embedding FROM page_embeddings"
    ).fetchone()
    assert row["rel_path"] == "topics/estate-blueprint.md"
    assert row["model"] == "fake-semantic"
    assert row["dimensions"] == 3
    assert isinstance(row["embedding"], bytes)


def test_hybrid_search_finds_semantic_match_without_literal_overlap(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex", embedder=FakeEmbedder())
    retriever = CortexRetriever(store)
    store.write_page("topics", "estate-blueprint", "# Estate Blueprint\nLegacy trust design for family wealth.")
    store.write_page("daily", "2026-05-14", "# Social Automation\nMBS reels composer caption fragility notes.")
    store.backfill_embeddings(force=True)

    # "inheritance" appears in no page body. FTS alone would return nothing.
    rows = retriever.search("inheritance planning for heirs", limit=3)
    assert rows
    assert rows[0]["rel_path"] == "topics/estate-blueprint.md"
    assert rows[0]["source"] in {"hybrid", "vector"}
    assert rows[0]["vector_score"] > 0.9


def test_retriever_falls_back_to_fts_when_no_embedder(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    retriever = CortexRetriever(store)
    store.write_page("daily", "2026-05-14", "# Social Automation\nMBS reels composer caption fragility notes.")

    rows = retriever.search("reels composer", limit=2)
    assert rows
    assert rows[0]["rel_path"] == "daily/2026-05-14.md"
    assert rows[0]["source"] == "fts"


def test_changed_page_gets_reembedded_on_reindex(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex", embedder=FakeEmbedder())
    path = store.write_page("topics", "mutable", "# Mutable\nLegacy wealth planning.")
    store.backfill_embeddings(force=True)
    before = store._conn.execute(
        "SELECT content_hash, embedding FROM page_embeddings WHERE rel_path=?", (path,)
    ).fetchone()

    store.write_page("topics", "mutable", "# Mutable\nInstagram reels composer notes.")
    changed = store.backfill_embeddings()
    after = store._conn.execute(
        "SELECT content_hash, embedding FROM page_embeddings WHERE rel_path=?", (path,)
    ).fetchone()

    assert changed == 1
    assert before["content_hash"] != after["content_hash"]
    assert before["embedding"] != after["embedding"]


def test_deleted_page_removes_embedding(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex", embedder=FakeEmbedder())
    path = store.write_page("topics", "to-delete", "# To Delete\nLegacy wealth planning.")
    store.backfill_embeddings(force=True)
    assert store._conn.execute(
        "SELECT COUNT(*) FROM page_embeddings WHERE rel_path=?", (path,)
    ).fetchone()[0] == 1

    (tmp_path / "cortex" / path).unlink()
    store._reindex_changed()

    assert store._conn.execute(
        "SELECT COUNT(*) FROM page_embeddings WHERE rel_path=?", (path,)
    ).fetchone()[0] == 0


def test_fts_self_heal_is_safe_noop_on_healthy_store(tmp_path: Path) -> None:
    """_heal_fts_if_needed must not disturb a healthy index, and an explicit
    rebuild must leave lexical search fully working.

    The real-world desync (external-content FTS rows pointing at moved content
    rowids) was observed on the live DB and is fixed by the 'rebuild' command;
    that fix is exercised here by forcing a rebuild and re-querying.
    """
    store = CortexStore(store_path=tmp_path / "cortex")
    store.write_page("daily", "2026-05-14", "# Social\nMBS reels composer caption notes about memory.")
    retriever = CortexRetriever(store)
    assert retriever.search("reels composer", limit=2)

    # Healthy store: heal detects nothing to do.
    assert store._heal_fts_if_needed() is False

    # Force the same rebuild path the heal uses; search must still work.
    store._conn.execute("INSERT INTO pages_fts(pages_fts) VALUES('rebuild')")
    store._conn.commit()
    rows = retriever.search("reels composer", limit=2)
    assert rows and rows[0]["rel_path"] == "daily/2026-05-14.md"


