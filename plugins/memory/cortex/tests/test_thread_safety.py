"""Regression test for the cortex store thread-safety bug.

Before the fix, ``CortexStore`` opened a single sqlite3 connection on whatever
thread first constructed it. Any subsequent access from a different thread
(notably the Hermes agent tool-call worker, which lives on a separate thread
from the gateway that pre-warms the store) raised::

    sqlite3.ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread.

The fix stores per-thread connections in ``threading.local`` and serialises
writes with a process-wide lock. This test spawns two worker threads against a
store constructed on the main thread, has each thread mix reads (``search``,
``list_pages``, ``count``) and writes (``write_page``, ``append_daily``), and
asserts no ``ProgrammingError`` (or any other exception) leaks out.

Run from the repo root::

    pytest plugins/memory/cortex/tests/test_thread_safety.py -v
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

import pytest

# Make the plugin importable without installing the repo as a package.
PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from store import CortexStore  # noqa: E402
from retrieval import CortexRetriever  # noqa: E402


def test_store_is_thread_safe(tmp_path: Path) -> None:
    """Two worker threads hit the store concurrently — neither should see
    ``sqlite3.ProgrammingError`` from the cross-thread connection guard."""
    store = CortexStore(store_path=tmp_path / "cortex")
    retriever = CortexRetriever(store)

    # Seed one page so search has something to find.
    store.write_page(
        category="topics",
        slug_or_title="seed",
        body="cortex thread safety regression seed page",
        tags=["seed"],
    )

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker(label: str) -> None:
        try:
            # Sync threads so calls genuinely race rather than interleave.
            barrier.wait(timeout=5)
            for i in range(20):
                # Mix of reads and writes — both paths touch the SQLite conn.
                retriever.search("cortex", limit=3)
                store.list_pages(limit=10)
                store.count()
                store.write_page(
                    category="topics",
                    slug_or_title=f"thread-{label}-{i}",
                    body=f"body from {label} iteration {i}",
                    tags=[label],
                )
                store.append_daily(f"{label} iter {i}")
        except BaseException as exc:  # noqa: BLE001 — capture for the assert
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("a",), name="cortex-worker-a")
    t2 = threading.Thread(target=worker, args=("b",), name="cortex-worker-b")
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not t1.is_alive() and not t2.is_alive(), "worker threads hung"

    programming_errors = [e for e in errors if isinstance(e, sqlite3.ProgrammingError)]
    assert not programming_errors, (
        f"cross-thread SQLite use leaked through: {programming_errors!r}"
    )
    assert not errors, f"worker(s) raised unexpected exceptions: {errors!r}"

    # Sanity: both workers actually wrote pages.
    pages = store.list_pages(category="topics", limit=100)
    slugs = {p["rel_path"] for p in pages}
    assert any("thread-a-" in s for s in slugs), "worker A did not write"
    assert any("thread-b-" in s for s in slugs), "worker B did not write"

    store.close()


def test_search_works_from_worker_thread(tmp_path: Path) -> None:
    """Smoke test: build the store on the main thread (mirroring the gateway
    pre-warm), then call ``.search()`` from a worker thread (mirroring the
    agent tool-call dispatcher). Pre-fix this raised ProgrammingError."""
    store = CortexStore(store_path=tmp_path / "cortex")
    retriever = CortexRetriever(store)
    store.write_page(
        category="topics",
        slug_or_title="hello",
        body="the quick brown fox jumps over the lazy dog",
    )

    captured: dict[str, object] = {}

    def worker() -> None:
        try:
            captured["results"] = retriever.search("fox")
        except BaseException as exc:  # noqa: BLE001
            captured["error"] = exc

    t = threading.Thread(target=worker, name="cortex-search-worker")
    t.start()
    t.join(timeout=10)

    assert "error" not in captured, f"search from worker thread raised: {captured.get('error')!r}"
    results = captured.get("results")
    assert isinstance(results, list) and results, "expected at least one search hit"

    store.close()
