"""CortexStore — markdown KB on disk + SQLite FTS5 index over page bodies.

The store wraps the existing `~/.hermes/cortex/` (or $HERMES_HOME/cortex/)
filesystem layout. Pages are markdown with YAML frontmatter:

    ---
    title: Some Title
    tags: [tag1, tag2]
    ---

    body...

Categories are subdirectories: people/, ventures/, topics/, synthesis/,
decisions/, learning/, research/. Daily journal lives at daily/YYYY-MM-DD.md.

The FTS5 index lives at <store>/.plugin.db. It is rebuilt incrementally
based on file mtime — only changed/added pages are reindexed each open.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:  # package import (normal Hermes runtime)
    from .embeddings import pack_vector, unpack_vector, top_matches
    from .chunking import split_markdown, chunk_embedding_text
except ImportError:  # flat import (tests add plugin dir to sys.path)
    from embeddings import pack_vector, unpack_vector, top_matches
    from chunking import split_markdown, chunk_embedding_text

logger = logging.getLogger(__name__)


KNOWLEDGE_CATEGORIES = [
    "people", "ventures", "projects", "topics", "synthesis",
    "decisions", "learning", "research",
]
"""Suggested seed categories created on fresh stores. NOT a whitelist — the agent is
free to create any category it wants by writing `category/slug.md`, and the indexer
walks every markdown file under the store root regardless of directory."""

DAILY_DIR = "daily"
DEFAULT_DB_FILENAME = ".plugin.db"

# Directory names skipped during recursive indexing. These are operational noise
# (VCS metadata, package caches, virtualenvs, backups, source-tree dumps) that
# don't belong in a knowledge base index even when they live under the store root.
SKIP_DIRS = {
    ".git", ".hg", ".svn",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", ".env",
    ".DS_Store",
}

# File name patterns skipped during indexing. Same rationale as SKIP_DIRS.
def _should_skip_file(name: str) -> bool:
    if name == "index.md":
        return True
    if name.endswith(".bak"):
        return True
    if name.startswith("."):
        return True
    return False


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown. Returns (frontmatter, body)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return ({}, text)
    fm_text, body = m.group(1), m.group(2)
    try:
        import yaml
        fm = yaml.safe_load(fm_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
    return (fm, body)


def _serialize_frontmatter(fm: dict, body: str) -> str:
    """Re-serialize page with YAML frontmatter."""
    import yaml
    fm_text = yaml.safe_dump(fm, default_flow_style=False, sort_keys=False).strip()
    return f"---\n{fm_text}\n---\n\n{body.lstrip()}"


_TITLE_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _flatten_title(raw: str) -> str:
    """Collapse a page title to one safe inline line.

    Titles are written by the agent itself and land in the SYSTEM PROMPT every
    turn via the knowledge map. Newlines and control characters would let a
    stored title break out of its list item and imitate prompt structure, so
    they are collapsed to spaces before the title is ever rendered.
    """
    return _TITLE_CONTROL_RE.sub(" ", raw).replace("\n", " ").strip()

_DATE_IN_NAME_RE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")
_DATE_FM_KEYS = ("date", "updated", "created")


def content_date(rel_path: str, fm: dict) -> str | None:
    """Best-effort CONTENT date for a page as an ISO string, or None.

    Order: an explicit frontmatter date field, then a YYYY-MM-DD embedded in the
    path (the convention for daily journals and dated snapshots).

    Deliberately NOT mtime. A reindex, a bulk frontmatter migration, or an rsync
    rewrites mtime for every page at once, which would make the whole store look
    freshly authored and destroy any recency signal built on it.
    """
    for key in _DATE_FM_KEYS:
        raw = fm.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        m = _DATE_IN_NAME_RE.search(text)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _DATE_IN_NAME_RE.search(rel_path)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _safe_slug(s: str) -> str:
    """Turn an arbitrary string into a safe filename slug."""
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\-_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "untitled"


class CortexStore:
    """Filesystem-backed KB with a SQLite FTS5 index over page bodies."""

    def __init__(self, store_path: str | Path, db_path: str | Path | None = None, embedder=None):
        self.store_path = Path(store_path).expanduser()
        self.store_path.mkdir(parents=True, exist_ok=True)

        # Optional semantic tier. When set, page bodies are embedded and stored
        # in page_embeddings for hybrid retrieval. None => lexical-only (FTS5),
        # which is the safe default if the embedding service is unreachable.
        self.embedder = embedder

        # Ensure standard subdirs exist
        for cat in KNOWLEDGE_CATEGORIES + [DAILY_DIR, "learning/archive"]:
            (self.store_path / cat).mkdir(parents=True, exist_ok=True)

        self.db_path = Path(db_path).expanduser() if db_path else (self.store_path / DEFAULT_DB_FILENAME)
        # SQLite connections are thread-affine: a connection created on thread A
        # raises ProgrammingError when used from thread B. The Hermes gateway
        # pre-warms the store on its main thread but tool calls dispatch from a
        # worker thread, so we keep one connection per thread in TLS. Writes are
        # additionally serialised with a process-wide lock so the FTS5 reindex
        # path (DELETE + INSERT OR REPLACE) stays consistent across threads.
        self._tls = threading.local()
        self._write_lock = threading.Lock()
        # Open on the constructing thread so _init_schema / _reindex_changed
        # below run against a real connection.
        conn = self._get_conn()
        self._init_schema(conn)
        self._reindex_changed(conn)
        self._heal_fts_if_needed(conn)

    # -- Connection management --------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """Return a sqlite3 connection bound to the calling thread.

        Connections are cached in ``threading.local`` so each thread reuses its
        own handle for the life of the store. ``check_same_thread=True`` (the
        default) is fine because we never share a connection across threads.
        """
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            self._tls.conn = conn
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        """Back-compat shim — older callers (e.g. CortexRetriever) read
        ``store._conn`` directly. Route them through the per-thread getter so
        they pick up a connection bound to the current thread instead of the
        one the store was constructed on."""
        return self._get_conn()

    # -- Schema ------------------------------------------------------------

    def _init_schema(self, conn: sqlite3.Connection | None = None) -> None:
        conn = conn or self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pages (
                rel_path TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                title TEXT,
                tags TEXT,
                body TEXT,
                mtime REAL,
                size INTEGER
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                rel_path UNINDEXED,
                title,
                tags,
                body,
                content='pages',
                content_rowid='rowid',
                tokenize='porter unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
                INSERT INTO pages_fts(rowid, rel_path, title, tags, body)
                VALUES (new.rowid, new.rel_path, new.title, new.tags, new.body);
            END;
            CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
                INSERT INTO pages_fts(pages_fts, rowid, rel_path, title, tags, body)
                VALUES('delete', old.rowid, old.rel_path, old.title, old.tags, old.body);
            END;
            CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
                INSERT INTO pages_fts(pages_fts, rowid, rel_path, title, tags, body)
                VALUES('delete', old.rowid, old.rel_path, old.title, old.tags, old.body);
                INSERT INTO pages_fts(rowid, rel_path, title, tags, body)
                VALUES (new.rowid, new.rel_path, new.title, new.tags, new.body);
            END;
            CREATE TABLE IF NOT EXISTS page_embeddings (
                rel_path TEXT PRIMARY KEY REFERENCES pages(rel_path) ON DELETE CASCADE,
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                embedding BLOB NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS page_embeddings_model_idx ON page_embeddings(model, dimensions);
            -- Sub-page vectors for pages too large to embed whole. Only pages
            -- over the embedder's input cap get rows here, so small pages keep
            -- exactly one page-level vector and the index stays ~2x, not ~6x.
            CREATE TABLE IF NOT EXISTS chunk_embeddings (
                rel_path TEXT NOT NULL REFERENCES pages(rel_path) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                heading_path TEXT,
                text TEXT NOT NULL,
                model TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                embedding BLOB NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (rel_path, chunk_index)
            );
            CREATE INDEX IF NOT EXISTS chunk_embeddings_model_idx ON chunk_embeddings(model, dimensions);
        """)
        # Additive migration for stores created before content_date existed.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(pages)")}
        if "content_date" not in cols:
            # ALTER and backfill must be ATOMIC. The column's existence is the
            # completion marker, so a crash between the two would leave the
            # column present and permanently unpopulated — recency silently
            # dead with no way to detect it.
            conn.execute("BEGIN")
            try:
                conn.execute("ALTER TABLE pages ADD COLUMN content_date TEXT")
                # Backfill in place with UPDATE. Do NOT force a reindex here (e.g.
                # by resetting mtime): rowid reassignment desyncs the external-content
                # pages_fts index and lexical search starts raising "missing row N
                # from content table". UPDATE preserves rowids.
                for row in conn.execute("SELECT rel_path FROM pages").fetchall():
                    rel = str(row["rel_path"])
                    try:
                        text = (self.store_path / rel).read_text(encoding="utf-8")
                        fm, _body = _parse_frontmatter(text)
                        cd = content_date(rel, fm)
                    except Exception:
                        # Unreadable, non-UTF-8, or malformed frontmatter: fall
                        # back to a date in the path. A single bad file must never
                        # abort the migration and leave the store unopenable.
                        cd = content_date(rel, {})
                    conn.execute(
                        "UPDATE pages SET content_date = ? WHERE rel_path = ?", (cd, rel)
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        conn.execute("CREATE INDEX IF NOT EXISTS pages_content_date_idx ON pages(content_date)")
        conn.commit()

    # -- Indexing ----------------------------------------------------------

    def _reindex_changed(self, conn: sqlite3.Connection | None = None) -> int:
        """Walk the store, re-index files whose mtime changed. Returns count reindexed.

        Recursively scans every `*.md` file under the store root. Directory names in
        SKIP_DIRS (`.git/`, `node_modules/`, etc.) and files matching
        `_should_skip_file` are excluded. No category whitelist — any subdirectory
        becomes a category automatically.

        All write paths (INSERT OR REPLACE / DELETE / COMMIT) are wrapped in the
        store-wide write lock so concurrent threads can't interleave updates to
        the FTS5 index.
        """
        conn = conn or self._get_conn()
        with self._write_lock:
            # Snapshot indexed mtimes
            cur = conn.execute("SELECT rel_path, mtime FROM pages")
            indexed = {row["rel_path"]: row["mtime"] for row in cur.fetchall()}

            seen: set[str] = set()
            changed = 0
            for dirpath, dirnames, filenames in os.walk(self.store_path):
                # Prune skip dirs in-place so we don't descend into them
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
                for fname in filenames:
                    if not fname.endswith(".md") or _should_skip_file(fname):
                        continue
                    p = Path(dirpath) / fname
                    try:
                        rel = str(p.relative_to(self.store_path))
                    except ValueError:
                        continue
                    seen.add(rel)
                    try:
                        mtime = p.stat().st_mtime
                    except OSError:
                        continue
                    if rel in indexed and abs(indexed[rel] - mtime) < 1e-6:
                        continue
                    # (Re)index this page
                    try:
                        text = p.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.debug("CortexStore: failed to read %s: %s", rel, e)
                        continue
                    fm, body = _parse_frontmatter(text)
                    title = str(fm.get("title", "")) or p.stem.replace("-", " ").title()
                    tags = fm.get("tags", []) or []
                    if isinstance(tags, str):
                        tags_str = tags
                    else:
                        tags_str = ", ".join(str(t) for t in tags)
                    # Category = top-level dir; top-level loose files get category "_root"
                    parts = rel.split("/", 1)
                    category = parts[0] if len(parts) > 1 else "_root"
                    try:
                        # UPSERT, never INSERT OR REPLACE. `pages_fts` is an
                        # external-content FTS5 table keyed by rowid; REPLACE
                        # deletes and reinserts, which assigns a NEW rowid and
                        # desyncs the index. The symptom is total lexical-search
                        # failure ("missing row N from content table") after any
                        # page edit. ON CONFLICT updates in place, preserving
                        # the rowid and keeping the FTS triggers coherent.
                        conn.execute(
                            "INSERT INTO pages (rel_path, category, title, tags, body, mtime, size, content_date)"
                            " VALUES (?,?,?,?,?,?,?,?)"
                            " ON CONFLICT(rel_path) DO UPDATE SET"
                            " category=excluded.category, title=excluded.title,"
                            " tags=excluded.tags, body=excluded.body,"
                            " mtime=excluded.mtime, size=excluded.size,"
                            " content_date=excluded.content_date",
                            (rel, category, title, tags_str, body, mtime, p.stat().st_size,
                             content_date(rel, fm)),
                        )
                        # Invalidate stale embedding so backfill regenerates it from new content
                        conn.execute("DELETE FROM page_embeddings WHERE rel_path = ?", (rel,))
                        conn.execute("DELETE FROM chunk_embeddings WHERE rel_path = ?", (rel,))
                        changed += 1
                    except sqlite3.Error as e:
                        logger.debug("CortexStore: failed to index %s: %s", rel, e)
                        continue

            # Remove pages that no longer exist on disk
            for rel in list(indexed):
                if rel not in seen:
                    conn.execute("DELETE FROM pages WHERE rel_path = ?", (rel,))
                    conn.execute("DELETE FROM page_embeddings WHERE rel_path = ?", (rel,))
                    conn.execute("DELETE FROM chunk_embeddings WHERE rel_path = ?", (rel,))
                    changed += 1

            if changed:
                conn.commit()
                logger.info("CortexStore: reindexed %d pages", changed)
            return changed

    def _heal_fts_if_needed(self, conn: sqlite3.Connection | None = None) -> bool:
        """Detect and repair a desynced external-content FTS5 index.

        Root cause: ``pages_fts`` is an external-content table keyed by rowid.
        ``INSERT OR REPLACE INTO pages`` reassigns rowids, which can leave the
        FTS index pointing at content rows that have moved ("missing row N from
        content table pages"). When that happens BM25-ordered MATCH joins raise
        and lexical search silently returns nothing. We probe cheaply and, on
        error, rebuild the index from the content table. Returns True if a
        rebuild ran.
        """
        conn = conn or self._get_conn()
        try:
            conn.execute(
                "SELECT pages.rowid FROM pages_fts JOIN pages ON pages.rowid = pages_fts.rowid "
                "WHERE pages_fts MATCH 'the OR a OR memory' ORDER BY bm25(pages_fts) LIMIT 1"
            ).fetchall()
            return False  # join works — index is healthy
        except sqlite3.Error as e:
            logger.warning("CortexStore: FTS index desynced (%s) — rebuilding", e)
            try:
                with self._write_lock:
                    conn.execute("INSERT INTO pages_fts(pages_fts) VALUES('rebuild')")
                    conn.commit()
                logger.info("CortexStore: FTS index rebuilt")
                return True
            except sqlite3.Error as e2:
                logger.error("CortexStore: FTS rebuild failed: %s", e2)
                return False

    # -- Semantic embeddings ----------------------------------------------

    @staticmethod
    def _embedding_text(row: sqlite3.Row | dict) -> str:
        """Text embedded for semantic retrieval: title/tags/body, page-level."""
        title = row["title"] or ""
        tags = row["tags"] or ""
        body = row["body"] or ""
        return f"{title}\nTags: {tags}\n\n{body}".strip()

    @staticmethod
    def _embedding_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()

    def backfill_embeddings(self, *, force: bool = False, limit: int | None = None) -> int:
        """Embed missing/stale pages. Returns the number of rows written.

        The embedding tier is optional. If no embedder is configured, this is a
        no-op and FTS5 remains fully functional. Staleness is tracked by content
        hash plus model/dimension, so changing either model or page text triggers
        a fresh vector on the next backfill.
        """
        if self.embedder is None:
            return 0
        conn = self._conn
        model = getattr(self.embedder, "model", "unknown")
        configured_dim = int(getattr(self.embedder, "dimensions", 0) or 0)
        sql = """
            SELECT p.rel_path, p.category, p.title, p.tags, p.body, e.content_hash, e.model, e.dimensions
            FROM pages p
            LEFT JOIN page_embeddings e ON e.rel_path = p.rel_path
            ORDER BY p.mtime DESC
        """
        candidates: list[tuple[sqlite3.Row, str, str]] = []
        for row in conn.execute(sql).fetchall():
            text = self._embedding_text(row)
            h = self._embedding_hash(text)
            stale = (
                force
                or row["content_hash"] != h
                or row["model"] != model
                or (configured_dim and row["dimensions"] != configured_dim)
            )
            if stale:
                candidates.append((row, text, h))
                if limit is not None and len(candidates) >= limit:
                    break
        if not candidates:
            return 0

        texts = [c[1] for c in candidates]
        try:
            vectors = self.embedder.embed(texts)
        except Exception as e:
            logger.warning("CortexStore: embedding backfill failed: %s", e)
            return 0
        if len(vectors) != len(candidates):
            logger.warning("CortexStore: embedder returned %d vectors for %d texts", len(vectors), len(candidates))
            return 0

        now = datetime.now().isoformat(timespec="seconds")
        written = 0
        with self._write_lock:
            for (row, _text, h), vec in zip(candidates, vectors):
                if not vec:
                    continue
                dim = len(vec)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO page_embeddings
                    (rel_path, model, dimensions, content_hash, embedding, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (row["rel_path"], model, dim, h, pack_vector([float(x) for x in vec]), now),
                )
                written += 1
            conn.commit()
        if written:
            logger.info("CortexStore: embedded %d pages with %s", written, model)
        return written

    # Pages at or under this many characters are embedded whole and get no chunk
    # rows. The embedding client truncates its input at 8,000 chars, so anything
    # above that is silently losing text today.
    CHUNK_THRESHOLD_CHARS = 8000

    def backfill_chunk_embeddings(self, *, force: bool = False, limit: int | None = None) -> int:
        """Embed sub-page chunks for pages too large to embed whole.

        Page-level vectors truncate at the embedder's input cap, so on a live
        store 18% of pages were discarding 27% of all body text — and 111 of 114
        large pages held content that existed ONLY past the cutoff. This indexes
        those pages a second time at chunk granularity, which also fixes topic
        dilution on multi-subject pages.

        Small pages are skipped entirely: they are already fully represented by
        their page vector, and every extra vector is per-turn scan latency.
        Returns the number of chunk rows written.
        """
        if self.embedder is None:
            return 0
        conn = self._conn
        model = getattr(self.embedder, "model", "unknown")
        configured_dim = int(getattr(self.embedder, "dimensions", 0) or 0)

        rows = conn.execute(
            "SELECT rel_path, title, tags, body FROM pages ORDER BY mtime DESC"
        ).fetchall()

        pending: list[tuple[str, int, str, str, str]] = []  # rel, idx, heading, text, hash
        touched: set[str] = set()
        for row in rows:
            body = row["body"] or ""
            whole = self._embedding_text(row)
            if len(whole) <= self.CHUNK_THRESHOLD_CHARS:
                continue
            existing = {
                r["chunk_index"]: r
                for r in conn.execute(
                    "SELECT chunk_index, content_hash, model, dimensions FROM chunk_embeddings WHERE rel_path = ?",
                    (row["rel_path"],),
                ).fetchall()
            }
            chunks = split_markdown(body)
            for idx, (heading, text) in enumerate(chunks):
                etext = chunk_embedding_text(row["title"] or "", row["tags"] or "", heading, text)
                h = self._embedding_hash(etext)
                prior = existing.get(idx)
                stale = (
                    force
                    or prior is None
                    or prior["content_hash"] != h
                    or prior["model"] != model
                    or (configured_dim and prior["dimensions"] != configured_dim)
                )
                if stale:
                    pending.append((row["rel_path"], idx, heading, text, h))
                    touched.add(row["rel_path"])
            # Drop rows for chunks that no longer exist (page shrank).
            if len(existing) > len(chunks):
                with self._write_lock:
                    conn.execute(
                        "DELETE FROM chunk_embeddings WHERE rel_path = ? AND chunk_index >= ?",
                        (row["rel_path"], len(chunks)),
                    )
                    conn.commit()
            if limit is not None and len(pending) >= limit:
                break

        if not pending:
            return 0

        by_rel = {r["rel_path"]: r for r in rows}
        texts = [
            chunk_embedding_text(
                by_rel[rel]["title"] or "", by_rel[rel]["tags"] or "", heading, text
            )
            for rel, _idx, heading, text, _h in pending
        ]
        try:
            vectors = self.embedder.embed(texts)
        except Exception as e:
            logger.warning("CortexStore: chunk embedding backfill failed: %s", e)
            return 0
        if len(vectors) != len(pending):
            logger.warning(
                "CortexStore: embedder returned %d vectors for %d chunks", len(vectors), len(pending)
            )
            return 0

        now = datetime.now().isoformat(timespec="seconds")
        written = 0
        with self._write_lock:
            try:
                for (rel, idx, heading, text, h), vec in zip(pending, vectors):
                    if not vec:
                        continue
                    try:
                        blob = pack_vector([float(x) for x in vec])
                    except (TypeError, ValueError) as e:
                        logger.warning(
                            "CortexStore: bad vector for %s#%d, skipping: %s", rel, idx, e
                        )
                        continue
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO chunk_embeddings
                        (rel_path, chunk_index, heading_path, text, model, dimensions,
                         content_hash, embedding, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rel, idx, heading, text, model, len(vec), h,
                            blob, now,
                        ),
                    )
                    written += 1
                conn.commit()
            except Exception as e:
                # Never leave a write transaction open on a cached, per-thread
                # connection: the lock is released on exit and the next writer
                # would block behind an abandoned transaction.
                conn.rollback()
                logger.warning("CortexStore: chunk embedding write failed, rolled back: %s", e)
                return 0
        if written:
            logger.info(
                "CortexStore: embedded %d chunks across %d pages with %s",
                written, len(touched), model,
            )
        return written

    def _resolve_vector_model(self, active_model: str, qdim: int) -> str | None:
        """Pick the stored embedding model to compare the query vector against.

        Never mixes distinct models (that would produce meaningless cosine
        scores), but self-heals the "same model, different name" case so a
        provider/prefix rename or route swap can't silently disable semantic
        search. Resolution order, all constrained to embeddings of dimension
        ``qdim``:

          1. Exact name match — the fast, normal path.
          2. Suffix match — provider prefixes differ but the model id after the
             last "/" is identical (e.g. ``google/gemini-embedding-001`` vs
             ``gemini/gemini-embedding-001``), and only when unambiguous.

        Anything else returns ``None`` and the caller falls back to lexical
        FTS5. In particular a lone stored model at this dimension is NOT
        adopted: that state is indistinguishable from a genuine embedder swap
        before a backfill, and silently comparing across two different models
        yields confident nonsense. Degraded recall beats wrong recall.
        """
        try:
            rows = self._conn.execute(
                "SELECT model, COUNT(*) AS n FROM page_embeddings WHERE dimensions = ? GROUP BY model",
                (qdim,),
            ).fetchall()
        except Exception as e:
            logger.debug("CortexStore: model resolution query failed: %s", e)
            return active_model  # fall back to strict exact-match behavior
        models = [str(r["model"]) for r in rows]
        if not models:
            return None
        # 1. exact
        if active_model in models:
            return active_model

        def suffix(m: str) -> str:
            return m.rsplit("/", 1)[-1]

        # 2. suffix (provider-prefix change), only if unambiguous
        active_suffix = suffix(active_model)
        suffix_matches = [m for m in models if suffix(m) == active_suffix]
        if len(suffix_matches) == 1:
            logger.warning(
                "CortexStore: query embedder model %r not stored verbatim; matched "
                "%r by model id (provider prefix differs). Re-run backfill to align.",
                active_model, suffix_matches[0],
            )
            return suffix_matches[0]
        # No positive evidence that any stored model is the active one — refuse to guess.
        #
        # Note we deliberately do NOT adopt a lone stored model just because it is the
        # only one at this dimension. "Exactly one stored model" does not distinguish a
        # rename from a genuine embedder swap, and a genuine swap (change embed_model,
        # restart before re-running backfill) produces exactly that state. Adopting it
        # would compare new-model query vectors against old-model document vectors and
        # feed confident nonsense into the agent's context. Falling back to lexical FTS5
        # is strictly better: degraded recall beats wrong recall.
        logger.warning(
            "CortexStore: query embedder model %r has no compatible stored embeddings "
            "at dim %d (stored models: %s); semantic tier off, using lexical only. "
            "Re-run backfill to embed pages with the active model.",
            active_model, qdim, ", ".join(sorted(set(models))),
        )
        return None

    def vector_search(self, query: str, *, limit: int = 5, category: str | None = None) -> list[dict]:
        """Exact semantic search over page-level embeddings.

        Returns rows shaped like CortexRetriever.search(), with vector_score in
        cosine-similarity units (higher is better). If the embedder or index is
        unavailable, returns [] so callers can fall back to lexical FTS5.
        """
        if self.embedder is None or not query.strip():
            return []
        try:
            qvecs = self.embedder.embed([query])
        except Exception as e:
            logger.debug("CortexStore: query embedding failed: %s", e)
            return []
        if not qvecs:
            return []
        q = [float(x) for x in qvecs[0]]
        qdim = len(q)
        active_model = getattr(self.embedder, "model", "unknown")
        # Choose which stored model(s) to compare against. Comparing a query
        # vector against document vectors from a DIFFERENT embedding model yields
        # meaningless cosine scores, so we must never mix models. But we also must
        # not silently return nothing when the same underlying model is merely
        # recorded under a different name (e.g. a provider/prefix change like
        # "google/gemini-embedding-001" -> "gemini/gemini-embedding-001", or a
        # route swap OpenRouter->OmniRoute). Resolve the target model defensively:
        target_model = self._resolve_vector_model(active_model, qdim)
        if target_model is None:
            # No compatible embeddings at this dimension — fall back to lexical.
            return []
        # Scan carries ONLY identity + vector. Page bodies and chunk text are
        # hydrated afterwards for the handful of winners: pulling every body
        # into memory to build 240-char snippets cost ~29MB peak per query on a
        # 1,200-page store, on the gateway thread, for text immediately discarded.
        sql = """
            SELECT e.rel_path, e.embedding
            FROM page_embeddings e
            JOIN pages p ON p.rel_path = e.rel_path
            WHERE e.dimensions = ?
            AND e.model = ?
        """
        params: list[Any] = [qdim, target_model]
        if category:
            sql += " AND p.category = ?"
            params.append(category)
        candidates: list[tuple[str, bytes]] = [
            (row["rel_path"], row["embedding"]) for row in self._conn.execute(sql, params)
        ]

        # Chunk tier: sub-page vectors for pages that were too large to embed
        # whole. A page can win on either tier; the best score for a page wins,
        # and a chunk hit carries its own excerpt so the snippet shows the text
        # that actually matched instead of the page's opening 240 chars.
        csql = """
            SELECT c.rel_path, c.chunk_index, c.embedding
            FROM chunk_embeddings c
            JOIN pages p ON p.rel_path = c.rel_path
            WHERE c.dimensions = ?
            AND c.model = ?
        """
        cparams: list[Any] = [qdim, target_model]
        if category:
            csql += " AND p.category = ?"
            cparams.append(category)
        chunk_candidates: list[tuple[tuple[str, int], bytes]] = []
        try:
            chunk_candidates = [
                ((row["rel_path"], row["chunk_index"]), row["embedding"])
                for row in self._conn.execute(csql, cparams)
            ]
        except sqlite3.Error as e:
            logger.debug("CortexStore: chunk tier unavailable: %s", e)

        # Over-fetch before collapsing: several chunks of one page can occupy the
        # top slots, so taking exactly `limit` chunk hits could yield one page.
        page_hits = top_matches(q, candidates, dim=qdim, limit=limit * 4 or 4)
        chunk_hits = top_matches(q, chunk_candidates, dim=qdim, limit=limit * 8 or 8)
        if not page_hits and not chunk_hits:
            return []

        # Collapse to the best score per page BEFORE hydrating text, so only the
        # surviving rows are ever read off disk.
        winners: dict[str, tuple[float, tuple[str, int] | None]] = {}
        for rel, score in page_hits:
            winners[rel] = (score, None)
        for key, score in chunk_hits:
            rel, _idx = key
            prior = winners.get(rel)
            if prior is None or score > prior[0]:
                winners[rel] = (score, key)

        rels = list(winners)
        placeholders = ",".join("?" for _ in rels)
        meta = {
            row["rel_path"]: row
            for row in self._conn.execute(
                f"SELECT rel_path, category, title, tags, body FROM pages"
                f" WHERE rel_path IN ({placeholders})",
                rels,
            )
        }
        chunk_keys = [k for _score, k in winners.values() if k is not None]
        chunk_meta: dict[tuple[str, int], sqlite3.Row] = {}
        if chunk_keys:
            cond = " OR ".join("(rel_path = ? AND chunk_index = ?)" for _ in chunk_keys)
            flat: list[Any] = [v for k in chunk_keys for v in k]
            try:
                chunk_meta = {
                    (row["rel_path"], row["chunk_index"]): row
                    for row in self._conn.execute(
                        f"SELECT rel_path, chunk_index, heading_path, text"
                        f" FROM chunk_embeddings WHERE {cond}",
                        flat,
                    )
                }
            except sqlite3.Error as e:
                logger.debug("CortexStore: chunk hydration failed: %s", e)

        best: dict[str, dict] = {}
        for rel, (score, key) in winners.items():
            row = meta.get(rel)
            if row is None:
                continue  # page deleted between scan and hydration
            crow = chunk_meta.get(key) if key is not None else None
            if crow is not None:
                text = (crow["text"] or "").strip()
                snippet = text[:240] + ("…" if len(text) > 240 else "")
                best[rel] = {
                    "rel_path": rel,
                    "category": row["category"],
                    "title": row["title"],
                    "tags": row["tags"],
                    "snippet": snippet,
                    "score": -score,
                    "vector_score": score,
                    "source": "vector-chunk",
                    "heading_path": (crow["heading_path"] or "").strip(),
                }
            else:
                body = row["body"] or ""
                best[rel] = {
                    "rel_path": rel,
                    "category": row["category"],
                    "title": row["title"],
                    "tags": row["tags"],
                    "snippet": body[:240] + ("…" if len(body) > 240 else ""),
                    "score": -score,
                    "vector_score": score,
                    "source": "vector",
                }
        rows = sorted(best.values(), key=lambda r: r["vector_score"], reverse=True)
        return rows[:limit]

    def oversized_page_count(self) -> int:
        """Pages large enough to need the chunk tier.

        Mirrors the threshold used by backfill_chunk_embeddings so coverage can
        be checked without re-deriving the rule at the call site.
        """
        return self._conn.execute(
            "SELECT COUNT(*) FROM pages WHERE LENGTH(body) > ?", (self.CHUNK_THRESHOLD_CHARS,)
        ).fetchone()[0]

    def embedding_stats(self) -> dict:
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n, MIN(dimensions) AS min_dim, MAX(dimensions) AS max_dim, model FROM page_embeddings GROUP BY model ORDER BY n DESC"
        )
        by_model = [dict(r) for r in cur.fetchall()]
        total_pages = self.count()
        total_embedded = sum(r["n"] for r in by_model)
        try:
            chunks, chunked_pages = self._conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT rel_path) FROM chunk_embeddings"
            ).fetchone()
        except sqlite3.Error:
            chunks, chunked_pages = 0, 0
        return {
            "pages": total_pages,
            "embedded": total_embedded,
            "by_model": by_model,
            "chunks": chunks or 0,
            "chunked_pages": chunked_pages or 0,
        }

    # -- Page CRUD ---------------------------------------------------------

    def get_page(self, rel_path: str) -> Optional[dict]:
        p = (self.store_path / rel_path).resolve()
        if not str(p).startswith(str(self.store_path.resolve()) + "/"):
            logger.warning("CortexStore: rel_path escapes store root, rejecting: %s", rel_path)
            return None
        if not p.is_file():
            return None
        text = p.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        return {
            "rel_path": rel_path,
            "title": fm.get("title", p.stem),
            "tags": fm.get("tags", []),
            "body": body,
            "frontmatter": fm,
        }

    def write_page(self, category: str, slug_or_title: str, body: str, tags: list[str] | None = None, title: str | None = None) -> str:
        """Create or overwrite a page. Returns the rel_path.

        No category whitelist — `category` can be any directory name (it will be
        created if missing). Path traversal is blocked.
        """
        # Block path traversal in category
        clean_cat = category.strip("/").strip()
        if not clean_cat or ".." in Path(clean_cat).parts or Path(clean_cat).is_absolute():
            raise ValueError(f"Invalid category: {category!r}")
        slug = _safe_slug(slug_or_title)
        if not slug.endswith(".md"):
            slug = slug + ".md"
        path = self.store_path / clean_cat / slug
        path.parent.mkdir(parents=True, exist_ok=True)
        fm = {
            "title": title or slug.removesuffix(".md").replace("-", " ").title(),
            "tags": tags or [],
            "updated": datetime.now().strftime("%Y-%m-%d"),
        }
        path.write_text(_serialize_frontmatter(fm, body), encoding="utf-8")
        self._reindex_changed()
        return f"{clean_cat}/{slug}"

    def append_daily(self, text: str, when: Optional[datetime] = None) -> str:
        """Append a timestamped entry to today's daily journal. Returns rel_path."""
        when = when or datetime.now()
        date_str = when.strftime("%Y-%m-%d")
        time_str = when.strftime("%H:%M")
        path = self.store_path / DAILY_DIR / f"{date_str}.md"
        rel = f"{DAILY_DIR}/{date_str}.md"
        # If file doesn't exist, create with frontmatter
        if not path.exists():
            header = _serialize_frontmatter({"title": date_str, "tags": ["daily"]}, "")
            path.write_text(header + f"\n## {time_str}\n\n{text.strip()}\n", encoding="utf-8")
        else:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n## {time_str}\n\n{text.strip()}\n")
        self._reindex_changed()
        return rel

    def list_pages(self, category: str | None = None, limit: int = 50) -> list[dict]:
        if category:
            cur = self._conn.execute(
                "SELECT rel_path, title, tags FROM pages WHERE category = ? ORDER BY mtime DESC LIMIT ?",
                (category, limit),
            )
        else:
            cur = self._conn.execute(
                "SELECT rel_path, title, tags FROM pages ORDER BY mtime DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in cur.fetchall()]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]

    def category_counts(self) -> dict[str, int]:
        cur = self._conn.execute("SELECT category, COUNT(*) as n FROM pages GROUP BY category")
        return {row["category"]: row["n"] for row in cur.fetchall()}

    def knowledge_map(
        self, *, max_chars: int = 2000, per_category: int = 6, max_title_chars: int = 60
    ) -> str:
        """Render a compact, budget-bounded map of what the store contains.

        Retrieval can only find what the agent thinks to look for. Prefetch
        injects a handful of matched snippets, which answers "what is relevant to
        this turn" but never "what do I know about at all" — so a page nobody
        queries for stays invisible no matter how good the ranker is. This
        renders the shape of the store (categories, their sizes, and a sample of
        page titles) so the agent can navigate deliberately with `list`/`read`
        instead of guessing search terms.

        Breadth beats depth here: knowing that a category *exists* is what
        prevents the "didn't know to look" failure, so long titles are elided to
        ``max_title_chars`` rather than allowed to consume the budget and push
        whole categories off the map.

        Deterministic by construction: categories sorted by descending page
        count then name, titles sorted by recency then rel_path. The output is
        hard-capped at ``max_chars`` because this lands in the system prompt on
        every single turn — an unbounded map would silently tax the whole
        session. Categories that do not fit are summarised as a remainder line
        rather than dropped without trace.
        """
        counts = self.category_counts()
        if not counts:
            return ""
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        # One windowed pass instead of a query per category: this runs on every
        # turn, and `pages` has no index on `category`, so the N+1 form was N
        # full table scans per turn.
        samples: dict[str, list[str]] = {c: [] for c, _ in ordered}
        cur = self._conn.execute(
            """
            SELECT category, title, rel_path FROM (
                SELECT category, title, rel_path,
                       ROW_NUMBER() OVER (
                           PARTITION BY category ORDER BY title ASC, rel_path ASC
                       ) AS rn
                FROM pages
            ) WHERE rn <= ?
            """,
            (per_category,),
        )
        for r in cur.fetchall():
            bucket = samples.get(str(r["category"]))
            if bucket is None:
                continue
            t = _flatten_title(str(r["title"] or r["rel_path"]))
            if max_title_chars and len(t) > max_title_chars:
                t = t[: max_title_chars - 1].rstrip() + "…"
            bucket.append(t)
        # BREADTH BEFORE DEPTH. Spending the budget in size order let the two
        # largest categories consume it and hid 12 of 19 categories on a real
        # store — including the small, high-value ones (learning, research,
        # synthesis). The map's job is to tell the agent what EXISTS, so every
        # category name is guaranteed a slot first, and sample titles are then
        # distributed across categories with whatever budget remains.
        lines: list[str] = []
        bare = {c: f"- **{c}** ({n})" for c, n in ordered}

        def _remainder(idx: int) -> str:
            cats = len(ordered) - idx
            pages = sum(c for _, c in ordered[idx:])
            return f"- …{cats} more categories ({pages} pages) — use `list` to browse"

        # Every category is named. Distribute the remaining budget round-robin
        # so one large category cannot monopolise the title slots. The budget is
        # enforced by MEASURING the rendered output after each added title, not
        # by predicting its cost: the "+N more" suffix and the ": " separator
        # appear and change as titles are added, and estimating them is exactly
        # how a "hard cap" quietly becomes a soft one.
        chosen: dict[str, list[str]] = {c: [] for c, _ in ordered}

        def _render() -> str:
            out: list[str] = []
            for category, n in ordered:
                titles = chosen[category]
                if not titles:
                    # No sample shown: the count already conveys the size, so a
                    # bare "+N more" would be pure noise (and 25 of them cost
                    # more than the entire skeleton).
                    out.append(bare[category])
                    continue
                sample = ", ".join(titles)
                more = n - len(titles)
                if more > 0:
                    sample = f"{sample}, +{more} more"
                out.append(f"- **{category}** ({n}): {sample}")
            return "\n".join(out)

        # Measure the real skeleton, then only add titles while the MEASURED
        # output stays inside the cap.
        if len(_render()) > max_chars:
            used = 0
            lines = []
            for idx, (category, _n) in enumerate(ordered):
                line = bare[category]
                if used + len(line) + 1 + len(_remainder(idx)) + 1 > max_chars:
                    tail = _remainder(idx)
                    # The remainder itself is subject to the cap. With a very
                    # small budget nothing legitimate fits, and emitting an
                    # over-budget line would break the guarantee the caller is
                    # relying on to control prompt size.
                    if used + len(tail) <= max_chars:
                        lines.append(tail)
                    break
                lines.append(line)
                used += len(line) + 1
            out = "\n".join(lines)
            return out if len(out) <= max_chars else ""

        for depth in range(per_category):
            progressed = False
            for category, _n in ordered:
                pool = samples.get(category, [])
                if depth >= len(pool):
                    continue
                chosen[category].append(pool[depth])
                if len(_render()) > max_chars:
                    chosen[category].pop()
                    continue
                progressed = True
            if not progressed:
                break

        return _render()

    def close(self) -> None:
        """Close the calling thread's connection (if any).

        Per-thread connections opened from other threads are released when
        those threads terminate; SQLite cleans up the underlying handle on
        Python finaliser. This matches the typical shutdown sequence where
        the gateway tears the store down from the same thread it constructed
        it on.
        """
        conn = getattr(self._tls, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._tls.conn = None
