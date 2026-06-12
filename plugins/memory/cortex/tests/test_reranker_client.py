"""Unit tests for the CortexReranker client (no network).

These exercise the Cohere-shape parsing and the fail-safe contract directly,
without a live endpoint, by monkeypatching the single HTTP method.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from reranker import CortexReranker  # noqa: E402


def test_blank_url_disables_rerank() -> None:
    rr = CortexReranker(url="")
    assert rr.rerank("q", ["a", "b"]) is None
    assert rr.health() is False


def test_parses_cohere_results_into_index_order(monkeypatch) -> None:
    rr = CortexReranker(url="http://localhost:9/v1/rerank")

    def fake_post(query, documents, top_n):
        # Reverse order: best is the last document.
        return [{"index": 2, "relevance_score": -1.0},
                {"index": 0, "relevance_score": -9.0},
                {"index": 1, "relevance_score": -10.0}]

    monkeypatch.setattr(rr, "_post", fake_post)
    order = rr.rerank("q", ["a", "b", "c"])
    assert order == [2, 0, 1]


def test_unmentioned_indices_are_appended_not_dropped(monkeypatch) -> None:
    rr = CortexReranker(url="http://localhost:9/v1/rerank")
    # Reranker only returns top_n=1; the rest must still appear, in original order.
    monkeypatch.setattr(rr, "_post", lambda q, d, n: [{"index": 1, "relevance_score": 0.5}])
    order = rr.rerank("q", ["a", "b", "c"], top_n=1)
    assert order is not None
    assert order[0] == 1
    assert sorted(order) == [0, 1, 2]


def test_malformed_response_fails_safe(monkeypatch) -> None:
    rr = CortexReranker(url="http://localhost:9/v1/rerank")

    def bad_post(query, documents, top_n):
        raise ValueError("rerank response missing 'results' array")

    monkeypatch.setattr(rr, "_post", bad_post)
    assert rr.rerank("q", ["a", "b"]) is None


def test_non_object_result_entries_fail_safe(monkeypatch) -> None:
    rr = CortexReranker(url="http://localhost:9/v1/rerank")
    monkeypatch.setattr(rr, "_post", lambda q, d, n: [1, None, "bad"])
    assert rr.rerank("q", ["a", "b"]) is None


def test_out_of_range_indices_are_ignored(monkeypatch) -> None:
    rr = CortexReranker(url="http://localhost:9/v1/rerank")
    monkeypatch.setattr(
        rr, "_post",
        lambda q, d, n: [{"index": 99, "relevance_score": 1.0}, {"index": 0, "relevance_score": 0.5}],
    )
    order = rr.rerank("q", ["a", "b"])
    # 99 dropped, 0 kept, 1 appended as the unmentioned safety-net index.
    assert order == [0, 1]
