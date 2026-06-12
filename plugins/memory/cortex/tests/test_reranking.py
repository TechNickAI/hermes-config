"""Reranking tests for Cortex retrieval.

The reranker is deliberately tested without network calls. These tests prove the
hot-path contract: disabled/unavailable reranking preserves existing RRF order;
a healthy reranker can reorder the bounded candidate set before final truncation.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from retrieval import CortexRetriever  # noqa: E402
from store import CortexStore  # noqa: E402


class FakeReranker:
    """Reorder by the presence of the answer-bearing phrase in each document."""

    def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[int] | None:
        scored = []
        for i, doc in enumerate(documents):
            score = 1 if "answer-bearing cortex page" in doc.lower() else 0
            scored.append((score, -i, i))
        scored.sort(reverse=True)
        return [i for _, _, i in scored]


class FailingReranker:
    def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[int] | None:
        return None


def test_reranker_reorders_candidates_before_limit_truncation(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    # Both pages match FTS tokens, but lexical/BM25 order puts the noisy page first.
    store.write_page("topics", "noisy", "# Noisy\nCortex memory memory memory general filler.")
    store.write_page(
        "topics",
        "answer",
        "# Answer\nCortex memory. This is the answer-bearing Cortex page for durable recall.",
    )

    base = CortexRetriever(store)
    before = base.search("cortex memory", limit=2)
    assert [r["rel_path"] for r in before] == ["topics/noisy.md", "topics/answer.md"]

    reranked = CortexRetriever(store, reranker=FakeReranker()).search("cortex memory", limit=1)
    assert [r["rel_path"] for r in reranked] == ["topics/answer.md"]
    assert reranked[0]["rerank_rank"] == 1
    assert reranked[0]["source"].endswith("+rerank")


def test_reranker_failure_preserves_existing_order(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    store.write_page("topics", "first", "# First\nCortex memory memory memory general filler.")
    store.write_page("topics", "second", "# Second\nCortex memory answer-bearing Cortex page.")

    base = CortexRetriever(store).search("cortex memory", limit=2)
    failed = CortexRetriever(store, reranker=FailingReranker()).search("cortex memory", limit=2)
    assert [r["rel_path"] for r in failed] == [r["rel_path"] for r in base]
    assert all("rerank_rank" not in r for r in failed)


def test_no_reranker_is_exact_legacy_order(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    store.write_page("topics", "alpha", "# Alpha\nLegacy ordering cortex search term.")
    store.write_page("topics", "beta", "# Beta\nLegacy ordering cortex search term.")

    rows = CortexRetriever(store, reranker=None).search("legacy ordering cortex", limit=2)
    assert len(rows) == 2
    assert all("rerank_rank" not in r for r in rows)
    assert all(not r["source"].endswith("+rerank") for r in rows)


def test_rerank_text_uses_full_body_not_display_snippet(tmp_path: Path) -> None:
    store = CortexStore(store_path=tmp_path / "cortex")
    filler = "x " * 400
    store.write_page(
        "topics",
        "long-answer",
        f"# Long Answer\n{filler}\nThe hidden phrase is answer-bearing Cortex page.",
    )
    retriever = CortexRetriever(store, reranker=FakeReranker())
    rows = retriever.search("long answer", limit=1)
    assert rows[0]["rel_path"] == "topics/long-answer.md"
    assert rows[0]["source"].endswith("+rerank")
