#!/usr/bin/env python3
"""Nightly Cortex database doctor: verify, repair, and prove retrieval.

The Markdown tree is authoritative; ``.plugin.db`` is derived local state. This
script never edits Markdown. It verifies SQLite, FTS5, embedding coverage, and
real retrieval, then repairs derived state when authorized.

Safety model
------------
Opening ``CortexStore`` is itself a mutating act: its constructor reindexes
changed pages, DELETEs rows for files it cannot see, and may rebuild FTS. So the
doctor takes a backup and runs a corpus sanity gate BEFORE opening the store,
never after. If the Markdown tree looks unavailable or catastrophically smaller
than the index, the run aborts without opening the store, because a reindex
against a missing tree silently empties a healthy index.

Exit codes
----------
0  verified healthy (after repair, if any)
1  store still not fully operational
2  setup/invocation failure (bad path, unusable store, aborted precondition)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

from embeddings import OpenAIEmbeddingClient  # noqa: E402
from retrieval import CortexRetriever  # noqa: E402
from store import CortexStore  # noqa: E402

DEFAULT_QUERY = "memory"
# A reindex may legitimately drop pages, but losing most of the corpus means the
# tree is missing rather than edited. Abort instead of letting the store's
# constructor delete the index.
MIN_CORPUS_RATIO = 0.5
BACKUP_PREFIX = ".plugin.db.bak-doctor-"
EXIT_OK, EXIT_UNHEALTHY, EXIT_SETUP = 0, 1, 2


class SetupError(RuntimeError):
    """Invocation/precondition failure: do not touch the store."""


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without executing shell content."""
    if not path.exists():
        return
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # Strip ONE matched pair of surrounding quotes; leave inner content alone.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def count_markdown(store_path: Path) -> int:
    return sum(1 for _ in store_path.rglob("*.md"))


def count_indexed_pages(db_path: Path) -> int:
    """Read the existing page count WITHOUT opening CortexStore (which mutates)."""
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT count(*) FROM pages").fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def backup_database(db_path: Path, reason: str) -> Path:
    """Take a consistent SQLite backup (not a raw file copy).

    ``shutil.copy2`` can capture a torn image and ignores WAL sidecars. The
    online backup API copies a transactionally consistent snapshot even while
    another process writes.
    """
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = db_path.with_name(f"{BACKUP_PREFIX}{reason}-{stamp}")
    source = sqlite3.connect(str(db_path))
    try:
        dest = sqlite3.connect(str(backup))
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    return backup


def prune_backups(store_path: Path, keep_days: int) -> list[str]:
    """Bound disk usage: nightly repairs must not accumulate DB copies forever."""
    if keep_days <= 0:
        return []
    cutoff = dt.datetime.now().timestamp() - keep_days * 86400
    removed = []
    for path in store_path.glob(f"{BACKUP_PREFIX}*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path.name)
        except OSError:
            continue
    return removed


def fts_probe(conn: sqlite3.Connection, query: str) -> dict[str, Any]:
    """Check FTS integrity, distinguishing corruption from a sparse query.

    Zero matching rows is NOT corruption: it means the term is absent. Only a
    raised error indicates a damaged index. Conflating them makes the doctor
    rebuild a healthy index every night and still report failure.
    """
    result: dict[str, Any] = {"ok": False, "corrupt": False, "rows": 0, "error": None}
    try:
        # FTS5's own integrity-check catches orphaned rowids even when a MATCH
        # probe happens not to touch the damaged row. The `rank` argument
        # (verify against external content) needs SQLite >= 3.42; fall back.
        try:
            conn.execute("INSERT INTO pages_fts(pages_fts, rank) VALUES('integrity-check', 1)")
        except sqlite3.OperationalError:
            conn.execute("INSERT INTO pages_fts(pages_fts) VALUES('integrity-check')")
        rows = conn.execute(
            "SELECT pages.rel_path FROM pages_fts "
            "JOIN pages ON pages.rel_path = pages_fts.rel_path "
            "WHERE pages_fts MATCH ? ORDER BY bm25(pages_fts) LIMIT 3",
            (query,),
        ).fetchall()
        result.update({"ok": True, "rows": len(rows)})
    except sqlite3.DatabaseError as exc:
        result.update({"corrupt": True, "error": str(exc)})
    return result


def embedding_probe(store: CortexStore) -> dict[str, Any]:
    """Coverage plus model consistency.

    Counting alone reports healthy when every row was computed by a since-changed
    model, which is a silent retrieval-quality failure.
    """
    stats = store.embedding_stats()
    pages = int(stats.get("pages", 0))
    embedded = int(stats.get("embedded", 0))
    by_model = stats.get("by_model", []) or []
    configured = getattr(store.embedder, "model", None) if store.embedder else None
    foreign = [m for m in by_model if configured and m.get("model") != configured]
    return {
        "ok": pages > 0 and embedded == pages and not foreign,
        "pages": pages,
        "embedded": embedded,
        "missing": max(0, pages - embedded),
        "configured_model": configured,
        "foreign_model_rows": [m.get("model") for m in foreign],
        "by_model": by_model,
    }


def preflight(store_path: Path, db_path: Path) -> dict[str, Any]:
    """Refuse to open the store when doing so would destroy a good index."""
    if not store_path.is_dir():
        raise SetupError(f"store directory does not exist: {store_path}")
    markdown = count_markdown(store_path)
    indexed = count_indexed_pages(db_path)
    info = {"markdown_files": markdown, "indexed_pages_before_open": indexed}
    if indexed > 0 and markdown < indexed * MIN_CORPUS_RATIO:
        raise SetupError(
            f"corpus sanity check failed: {markdown} markdown files on disk vs "
            f"{indexed} indexed pages. Refusing to open the store, because "
            f"reindexing against a missing tree would delete the index. "
            f"Check the --store path and that the volume is mounted."
        )
    return info


def run(
    store_path: Path,
    profile_home: Path,
    query: str,
    repair: bool,
    keep_backup_days: int = 14,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "store": str(store_path),
        "query": query,
        "repair_authorized": repair,
        "repairs": [],
        "backups": [],
        "pruned_backups": [],
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    db_path = store_path / ".plugin.db"

    result.update(preflight(store_path, db_path))

    # Backup BEFORE opening: the constructor reindexes, deletes rows for missing
    # files, and can rebuild FTS. That is a mutation, so the recovery point must
    # precede it -- including on check-only runs.
    if db_path.exists():
        try:
            result["backups"].append(str(backup_database(db_path, "preopen")))
        except sqlite3.Error as exc:
            raise SetupError(f"pre-open backup failed, refusing to continue: {exc}") from exc

    embedder = OpenAIEmbeddingClient(timeout=30, batch_size=16)
    embed_health = embedder.health()
    result["embedding_endpoint_healthy"] = embed_health

    try:
        store = CortexStore(store_path, embedder=embedder if embed_health else None)
    except Exception as exc:
        raise SetupError(f"store open failed: {exc}") from exc

    try:
        conn = store._conn
        # Give the live gateway room to finish a write instead of reading a lock
        # error as corruption.
        conn.execute("PRAGMA busy_timeout = 30000")
        result["sqlite_integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]

        fts = fts_probe(conn, query)
        result["fts_before"] = fts
        if fts["corrupt"] and repair:
            with store._write_lock:
                conn.execute("INSERT INTO pages_fts(pages_fts) VALUES('rebuild')")
                conn.commit()
            result["repairs"].append("fts_rebuilt")
        result["fts_after"] = fts_probe(conn, query)

        coverage = embedding_probe(store)
        result["embeddings_before"] = coverage
        if not coverage["ok"] and repair and embed_health and coverage["pages"] > 0:
            # force=True when a foreign model is present, so stale vectors are
            # recomputed rather than left in place by a count-only backfill.
            changed = store.backfill_embeddings(force=bool(coverage["foreign_model_rows"]))
            result["repairs"].append(f"embeddings_backfilled:{changed}")
        result["embeddings_after"] = embedding_probe(store)

        retriever = CortexRetriever(store)
        rows = retriever.search(query, limit=3)
        sources = [str(row.get("source", "")) for row in rows]
        semantic = any(source.startswith(("hybrid", "vector")) for source in sources)
        result["retrieval"] = {
            # An empty store or an absent term returns no rows legitimately;
            # only demand semantic sourcing when there is something to retrieve.
            "ok": semantic if rows else coverage["pages"] == 0,
            "count": len(rows),
            "sources": sources,
        }

        # Re-check integrity AFTER repairs, so exit 0 means healthy now.
        result["sqlite_integrity_after"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        result["ok"] = bool(
            result["sqlite_integrity_after"] == "ok"
            and result["fts_after"]["ok"]
            and result["embeddings_after"]["ok"]
            and result["retrieval"]["ok"]
        )
    finally:
        store.close()
        result["pruned_backups"] = prune_backups(store_path, keep_backup_days)
        result["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", required=True)
    parser.add_argument("--profile-home", required=True)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--keep-backup-days", type=int, default=14)
    parser.add_argument("--json-out")
    args = parser.parse_args()

    store = Path(args.store).expanduser().resolve()
    profile_home = Path(args.profile_home).expanduser().resolve()
    load_dotenv(profile_home / ".env")

    try:
        result = run(store, profile_home, args.query, args.repair, args.keep_backup_days)
        code = EXIT_OK if result.get("ok") else EXIT_UNHEALTHY
    except SetupError as exc:
        result = {"ok": False, "setup_error": str(exc), "store": str(store)}
        code = EXIT_SETUP

    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.json_out:
        # Write atomically so a crash cannot leave truncated state behind.
        out = Path(args.json_out)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(rendered + "\n")
        tmp.replace(out)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
