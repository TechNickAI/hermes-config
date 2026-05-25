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

import logging
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

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


def _safe_slug(s: str) -> str:
    """Turn an arbitrary string into a safe filename slug."""
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\-_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "untitled"


class CortexStore:
    """Filesystem-backed KB with a SQLite FTS5 index over page bodies."""

    def __init__(self, store_path: str | Path, db_path: str | Path | None = None):
        self.store_path = Path(store_path).expanduser()
        self.store_path.mkdir(parents=True, exist_ok=True)

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
        """)
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
                        conn.execute(
                            "INSERT OR REPLACE INTO pages (rel_path, category, title, tags, body, mtime, size) VALUES (?,?,?,?,?,?,?)",
                            (rel, category, title, tags_str, body, mtime, p.stat().st_size),
                        )
                        changed += 1
                    except sqlite3.Error as e:
                        logger.debug("CortexStore: failed to index %s: %s", rel, e)
                        continue

            # Remove pages that no longer exist on disk
            for rel in list(indexed):
                if rel not in seen:
                    conn.execute("DELETE FROM pages WHERE rel_path = ?", (rel,))
                    changed += 1

            if changed:
                conn.commit()
                logger.info("CortexStore: reindexed %d pages", changed)
            return changed

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
