"""Tests for embedding-model resolution in vector search.

Semantic search compares a query vector against stored document vectors. Those
comparisons are only meaningful when both sides came from the *same* embedding
model — cosine similarity between vectors from two different models is noise.
So the store filters stored embeddings by model name.

Strict name equality is too brittle in practice: the same underlying model gets
recorded under different names when a provider prefix changes
(``google/gemini-embedding-001`` → ``gemini/gemini-embedding-001``) or a router
route is swapped. A strict match silently disables the semantic tier and
degrades retrieval with no error.

``CortexStore._resolve_vector_model`` threads that needle: self-heal the
unambiguous rename cases, refuse to guess when models genuinely differ. These
tests pin each branch of that decision.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from store import CortexStore  # noqa: E402


class StubEmbedder:
    """Deterministic embedder with a configurable model name and dimension."""

    def __init__(self, model: str = "test-model", dimensions: int = 3) -> None:
        self.model = model
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


def _store(tmp_path: Path, model: str = "test-model") -> CortexStore:
    return CortexStore(
        store_path=str(tmp_path / "cortex"),
        db_path=str(tmp_path / "cortex.db"),
        embedder=StubEmbedder(model=model),
    )


def _insert_embedding(store: CortexStore, model: str, dimensions: int = 3) -> None:
    """Insert one embedding row directly, bypassing the embed/backfill pipeline.

    Mirrors the exact insert `backfill_embeddings` performs (same columns, same
    packed vector encoding) so these tests exercise real stored rows rather than
    a shape the production code would never write.
    """
    from datetime import datetime, timezone

    from embeddings import pack_vector

    rel_path = f"topics/{model.replace('/', '-')}.md"
    # page_embeddings.rel_path is a FK to pages, so the page must exist first.
    store.write_page(category="topics", slug_or_title=rel_path, body=f"Page for {model}.")
    vector = [1.0] + [0.0] * (dimensions - 1)
    store._conn.execute(
        """
        INSERT OR REPLACE INTO page_embeddings
        (rel_path, model, dimensions, content_hash, embedding, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            rel_path,
            model,
            dimensions,
            f"hash-{model}",
            pack_vector(vector),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    store._conn.commit()


def test_exact_model_match_is_used(tmp_path: Path) -> None:
    """The normal path: stored name equals the active embedder's name."""
    store = _store(tmp_path, model="test-model")
    _insert_embedding(store, "test-model")
    assert store._resolve_vector_model("test-model", 3) == "test-model"


def test_provider_prefix_change_resolves_by_model_id(tmp_path: Path) -> None:
    """Same model, different provider prefix — match on the id after the slash."""
    store = _store(tmp_path)
    _insert_embedding(store, "google/gemini-embedding-001")
    resolved = store._resolve_vector_model("gemini/gemini-embedding-001", 3)
    assert resolved == "google/gemini-embedding-001"


def test_lone_stored_model_is_not_adopted_on_rename(tmp_path: Path) -> None:
    """A single stored model is NOT positive evidence of a rename.

    "Exactly one model at this dimension" is indistinguishable from a genuine
    embedder swap: change `embed_model` in config, restart before re-running
    backfill, and the store looks exactly like this. Adopting the stored model
    would compare new-model query vectors against old-model document vectors and
    feed confident nonsense into the agent's context.

    Falling back to lexical FTS5 is strictly better — degraded recall beats
    wrong recall. Flagged by two independent review bots on PR #76.
    """
    store = _store(tmp_path)
    _insert_embedding(store, "old-name-entirely")
    assert store._resolve_vector_model("brand-new-name", 3) is None


def test_ambiguous_multi_model_store_refuses_to_guess(tmp_path: Path) -> None:
    """Several genuinely different models and no match — return None, not a guess.

    This is the case that protects correctness: silently comparing against the
    wrong model's vectors would return confident nonsense.
    """
    store = _store(tmp_path)
    _insert_embedding(store, "model-alpha")
    _insert_embedding(store, "model-beta")
    assert store._resolve_vector_model("model-gamma", 3) is None


def test_ambiguous_store_still_honours_an_exact_match(tmp_path: Path) -> None:
    """Multiple models present, but one matches exactly — use it."""
    store = _store(tmp_path)
    _insert_embedding(store, "model-alpha")
    _insert_embedding(store, "model-beta")
    assert store._resolve_vector_model("model-beta", 3) == "model-beta"


def test_ambiguous_suffix_match_refuses_to_guess(tmp_path: Path) -> None:
    """Two providers exposing the same model id is not an unambiguous rename."""
    store = _store(tmp_path)
    _insert_embedding(store, "google/embed-001")
    _insert_embedding(store, "azure/embed-001")
    assert store._resolve_vector_model("openai/embed-001", 3) is None


def test_no_embeddings_at_dimension_returns_none(tmp_path: Path) -> None:
    """Dimension mismatch means there is nothing safe to compare against."""
    store = _store(tmp_path)
    _insert_embedding(store, "test-model", dimensions=3)
    assert store._resolve_vector_model("test-model", 1536) is None


def test_empty_store_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store._resolve_vector_model("test-model", 3) is None


def test_vector_search_falls_back_to_lexical_when_unresolvable(tmp_path: Path) -> None:
    """The integration contract: unresolvable model ⇒ empty vector results.

    The retriever then keeps the lexical FTS5 results, so search degrades to
    "good keyword search" rather than breaking.
    """
    from retrieval import CortexRetriever

    store = _store(tmp_path, model="model-gamma")
    store.write_page(category="topics", slug_or_title="Estate Planning", body="Trusts and wills.")
    _insert_embedding(store, "model-alpha")
    _insert_embedding(store, "model-beta")

    assert store.vector_search("estate", limit=5) == []
    # Lexical retrieval still finds the page — the "degrades, not breaks" guarantee.
    results = CortexRetriever(store).search("estate", limit=5)
    assert any("estate" in r["rel_path"].lower() for r in results)
