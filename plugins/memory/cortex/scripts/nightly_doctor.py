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
from store import SKIP_DIRS, CortexStore, _should_skip_file  # noqa: E402

DEFAULT_QUERY = "memory"
# A reindex may legitimately drop pages, but losing most of the corpus means the
# tree is missing rather than edited. Abort instead of letting the store's
# constructor delete the index.
MIN_CORPUS_RATIO = 0.5
BACKUP_PREFIX = ".plugin.db.bak-doctor-"
# Retention must cover EVERY Cortex DB backup, not just the doctor's own. Manual
# repairs, memory cleanups and one-off scripts write sibling copies under other
# suffixes (bak-memory-cleanup-, bak-embed-, bak-search-repair-, ...). Pruning
# only BACKUP_PREFIX left those growing forever, which defeated the stated
# "must not accumulate DB copies forever" policy. Match the whole family.
BACKUP_GLOB = ".plugin.db.bak-*"
EXIT_OK, EXIT_UNHEALTHY, EXIT_SETUP = 0, 1, 2


def _config_bool(config: dict[str, Any], key: str, default: bool = False) -> bool:
    """Mirror the provider's handling of boolean config strings."""
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _semantic_configured(plugin_config: dict[str, Any]) -> bool:
    """Return whether semantic embedding is enabled — mirror provider precedence.

    The provider's ``_build_embedder`` returns None (lexical-only) when
    ``semantic: false`` or both config and env embed URLs are empty. The doctor
    must apply the same gate so a lexical-only deployment does not alarm.
    """
    if not _config_bool(plugin_config, "semantic", default=True):
        return False
    return bool(plugin_config.get("embed_url") or os.environ.get("CORTEX_EMBED_URL"))


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
    """Count markdown files the INDEXER would actually see.

    A raw rglob overcounts: ``CortexStore._reindex_changed`` prunes SKIP_DIRS
    (``.git/``, ``node_modules/``, ...) and skips files via ``_should_skip_file``.
    Counting files it ignores lets the corpus gate pass on decoys while the
    constructor deletes every real page.
    """
    total = 0
    for dirpath, dirnames, filenames in os.walk(store_path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if fname.endswith(".md") and not _should_skip_file(fname):
                total += 1
    return total


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


def load_plugin_config(profile_home: Path) -> dict[str, Any]:
    """Read ``plugins.cortex`` from the profile's config.yaml.

    The provider honours store_path, db_path, and embed_* settings from here.
    A doctor that reads only .env probes a DIFFERENT database than the live
    agent uses -- creating and "repairing" a fresh one while the real store
    stays broken.
    """
    config_path = profile_home / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        loaded = yaml.safe_load(config_path.read_text()) or {}
    except Exception:
        return {}
    return ((loaded.get("plugins") or {}).get("cortex") or {}) if isinstance(loaded, dict) else {}


def resolve_paths(
    profile_home: Path,
    plugin_config: dict[str, Any],
    store_override: str | None,
) -> tuple[Path, Path]:
    """Resolve (store_path, db_path) the same way the provider does.

    A --store override deliberately ALSO discards a configured db_path. Pairing
    an overridden markdown root with the configured database would open the live
    index against a different corpus, and the reindex would then delete every
    page it could not find under the override.
    """

    def expand(value: str) -> Path:
        # Match the provider: both $HERMES_HOME and ${HERMES_HOME} are accepted.
        text = str(value).replace("${HERMES_HOME}", str(profile_home))
        text = text.replace("$HERMES_HOME", str(profile_home))
        return Path(text).expanduser().resolve()

    if store_override:
        store = Path(store_override).expanduser().resolve()
        return store, store / ".plugin.db"

    store = expand(plugin_config.get("store_path") or str(profile_home / "cortex"))
    raw_db = plugin_config.get("db_path") or ""
    db = expand(raw_db) if raw_db else store / ".plugin.db"
    return store, db


def build_embedder(plugin_config: dict[str, Any]) -> OpenAIEmbeddingClient:
    """Construct the embedding client from config.yaml, falling back to env."""
    dim = plugin_config.get("embed_dim")
    return OpenAIEmbeddingClient(
        url=plugin_config.get("embed_url") or None,
        model=plugin_config.get("embed_model") or None,
        api_key=plugin_config.get("embed_key") or None,
        dimensions=int(dim) if dim else None,
        timeout=30,
        batch_size=16,
    )


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


def prune_backups(store_path: Path, keep_days: int, db_dir: Path | None = None) -> list[str]:
    """Bound disk usage: nightly repairs must not accumulate DB copies forever.

    Backups are written beside db_path (via backup_database), which may live
    outside store_path when db_path is customised. db_dir covers that case.

    Prunes the whole ``.plugin.db.bak-*`` family, not only this script's own
    ``BACKUP_PREFIX``. Ad-hoc copies left by manual repairs and cleanup scripts
    are the same class of artifact and were previously never reclaimed.
    """
    if keep_days <= 0:
        return []
    cutoff = dt.datetime.now().timestamp() - keep_days * 86400
    removed = []
    search_dirs: set[Path] = {store_path}
    if db_dir and db_dir != store_path:
        search_dirs.add(db_dir)
    for search_dir in search_dirs:
        for path in search_dir.glob(BACKUP_GLOB):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed.append(path.name)
            except OSError:
                continue
    return removed


def fts_probe(conn: sqlite3.Connection, query: str) -> dict[str, Any]:
    """Check FTS integrity, distinguishing corruption from a sparse or malformed query.

    Zero matching rows is NOT corruption: it means the term is absent. A
    DatabaseError from the integrity-check indicates a damaged index. An
    OperationalError from the MATCH probe is a bad --query argument, not
    corruption — conflating them triggers rebuilds on a healthy index.
    """
    result: dict[str, Any] = {"ok": False, "corrupt": False, "rows": 0, "error": None}
    # Step 1: FTS5 integrity-check — a DatabaseError here is actual corruption.
    # The `rank` argument (verify against external content) needs SQLite >= 3.42.
    try:
        try:
            conn.execute("INSERT INTO pages_fts(pages_fts, rank) VALUES('integrity-check', 1)")
        except sqlite3.OperationalError:
            conn.execute("INSERT INTO pages_fts(pages_fts) VALUES('integrity-check')")
    except sqlite3.DatabaseError as exc:
        result.update({"corrupt": True, "error": str(exc)})
        return result
    # Step 2: MATCH probe — an OperationalError here is a syntax error in the
    # --query string, not index damage. Report it without setting corrupt=True.
    try:
        rows = conn.execute(
            "SELECT pages.rel_path FROM pages_fts "
            "JOIN pages ON pages.rel_path = pages_fts.rel_path "
            "WHERE pages_fts MATCH ? ORDER BY bm25(pages_fts) LIMIT 3",
            (query,),
        ).fetchall()
        result.update({"ok": True, "rows": len(rows)})
    except sqlite3.OperationalError as exc:
        result.update({"ok": False, "error": f"query syntax error: {exc}"})
    return result


def embedding_probe(store: CortexStore) -> dict[str, Any]:
    """Coverage plus model AND dimension consistency.

    Counting alone reports healthy when every row was computed by a since-changed
    model, or at a different vector width. Dimension drift is the sneakier case:
    an endpoint can change width without changing its name, leaving rows that
    match on model but are unusable at query time.
    """
    stats = store.embedding_stats()
    pages = int(stats.get("pages", 0))
    embedded = int(stats.get("embedded", 0))
    by_model = stats.get("by_model", []) or []
    configured = getattr(store.embedder, "model", None) if store.embedder else None
    configured_dim = getattr(store.embedder, "dimensions", None) if store.embedder else None
    foreign = [m for m in by_model if configured and m.get("model") != configured]
    drifted = []
    if configured_dim:
        for m in by_model:
            lo, hi = m.get("min_dim"), m.get("max_dim")
            if (lo and lo != configured_dim) or (hi and hi != configured_dim):
                drifted.append({"model": m.get("model"), "min_dim": lo, "max_dim": hi})
    return {
        # An empty store is legitimately empty, not unhealthy: a new agent that
        # has not written any pages yet must not alarm every night. Coverage is
        # about whether the pages that EXIST are embedded.
        "ok": embedded == pages and not foreign and not drifted,
        "pages": pages,
        "embedded": embedded,
        "missing": max(0, pages - embedded),
        "configured_model": configured,
        "configured_dim": configured_dim,
        "foreign_model_rows": [m.get("model") for m in foreign],
        "dimension_drift": drifted,
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
    plugin_config: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    plugin_config = plugin_config if plugin_config is not None else {}
    result: dict[str, Any] = {
        "store": str(store_path),
        "query": query,
        "repair_authorized": repair,
        "repairs": [],
        "backups": [],
        "pruned_backups": [],
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    db_path = db_path or (store_path / ".plugin.db")
    result["db_path"] = str(db_path)

    result.update(preflight(store_path, db_path))

    # Backup BEFORE opening: the constructor reindexes, deletes rows for missing
    # files, and can rebuild FTS. That is a mutation, so the recovery point must
    # precede it -- including on check-only runs.
    if db_path.exists():
        try:
            result["backups"].append(str(backup_database(db_path, "preopen")))
        except sqlite3.Error as exc:
            raise SetupError(f"pre-open backup failed, refusing to continue: {exc}") from exc

    embedder = build_embedder(plugin_config)
    embed_health = embedder.health()
    result["embedding_endpoint_healthy"] = embed_health
    result["embed_model"] = getattr(embedder, "model", None)

    try:
        store = CortexStore(
            store_path,
            db_path=db_path,
            embedder=embedder if embed_health else None,
        )
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
        # Semantic checks apply when the provider would build an embedder, OR
        # when semantic remains enabled and the store contains embeddings from a
        # prior deployment — so endpoint-loss degradation is still caught.
        semantic_enabled = _config_bool(plugin_config, "semantic", default=True)
        semantic_in_use = _semantic_configured(plugin_config) or (
            semantic_enabled and coverage["embedded"] > 0
        )
        if not coverage["ok"] and repair and embed_health and coverage["pages"] > 0:
            # backfill_embeddings tracks staleness by content hash PLUS
            # model/dimension, so drifted rows are recomputed without force.
            # force=True is only needed when the model name itself changed and
            # we want the whole corpus rewritten rather than row-by-row.
            changed = store.backfill_embeddings(force=bool(coverage["foreign_model_rows"]))
            result["repairs"].append(f"embeddings_backfilled:{changed}")
        result["embeddings_after"] = embedding_probe(store)

        retriever = CortexRetriever(store)
        rows = retriever.search(query, limit=3)
        sources = [str(row.get("source", "")) for row in rows]
        semantic = any(source.startswith(("hybrid", "vector")) for source in sources)
        result["retrieval"] = {
            # Semantic deployments require hybrid/vector sourcing when rows exist.
            # Lexical-only deployments accept FTS results — they are healthy by design.
            "ok": (semantic if rows else coverage["pages"] == 0) if semantic_in_use
                  else (len(rows) > 0 or coverage["pages"] == 0),
            "count": len(rows),
            "sources": sources,
        }

        # Re-check integrity AFTER repairs, so exit 0 means healthy now.
        result["sqlite_integrity_after"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        result["ok"] = bool(
            result["sqlite_integrity_after"] == "ok"
            and result["fts_after"]["ok"]
            and (not semantic_in_use or result["embeddings_after"]["ok"])
            and result["retrieval"]["ok"]
        )
    finally:
        store.close()
        result["pruned_backups"] = prune_backups(store_path, keep_backup_days, db_dir=db_path.parent)
        result["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", help="Override the store path from config.yaml")
    parser.add_argument("--profile-home", required=True)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--keep-backup-days", type=int, default=14)
    parser.add_argument("--json-out")
    args = parser.parse_args()

    profile_home = Path(args.profile_home).expanduser().resolve()
    load_dotenv(profile_home / ".env")
    plugin_config = load_plugin_config(profile_home)
    store, db_path = resolve_paths(profile_home, plugin_config, args.store)

    try:
        result = run(
            store,
            profile_home,
            args.query,
            args.repair,
            args.keep_backup_days,
            plugin_config=plugin_config,
            db_path=db_path,
        )
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
