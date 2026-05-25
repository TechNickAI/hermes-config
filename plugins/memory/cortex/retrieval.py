"""CortexRetriever — search the page index using FTS5 with sensible ranking."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Words that wreck FTS5 queries when passed verbatim
_FTS_STOPWORDS = {"a", "an", "the", "is", "are", "was", "were", "be", "been",
                  "of", "to", "in", "on", "at", "for", "with", "by", "as",
                  "and", "or", "but", "if", "do", "did", "what", "who",
                  "when", "where", "why", "how", "this", "that", "these", "those"}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_'-]{2,}")


def _sanitize_query(q: str, max_tokens: int = 8) -> str:
    """Turn natural-language query into an FTS5-safe disjunction.

    Strips punctuation, drops stopwords, picks the most informative tokens.
    Returns '' if nothing usable remains.
    """
    tokens = _TOKEN_RE.findall(q.lower())
    keepers: list[str] = []
    for t in tokens:
        if t in _FTS_STOPWORDS:
            continue
        # FTS5 special chars
        clean = t.replace('"', '').replace("'", "")
        if not clean:
            continue
        keepers.append(clean)
        if len(keepers) >= max_tokens:
            break
    if not keepers:
        return ""
    # Use OR for recall (any token matches)
    return " OR ".join(keepers)


class CortexRetriever:
    """Search Cortex pages via FTS5 ranked by BM25 with title/tag boosts."""

    def __init__(self, store):
        self.store = store
        # NB: do NOT cache `store._conn` here. SQLite connections are
        # thread-affine, and CortexStore now hands out per-thread connections
        # via a property. Resolve fresh on every call so search() works from
        # whatever thread the agent's tool worker dispatches us on.

    def search(self, query: str, *, limit: int = 5, category: str | None = None, snippet_chars: int = 240) -> list[dict]:
        if not query:
            return []
        fts_q = _sanitize_query(query)
        if not fts_q:
            return []
        # bm25() lower = better. Boost title (0.5x) and tags (0.7x) by giving them lower weight.
        sql = """
            SELECT pages.rel_path,
                   pages.category,
                   pages.title,
                   pages.tags,
                   snippet(pages_fts, 3, '**', '**', ' … ', 32) AS snippet,
                   bm25(pages_fts, 1.0, 0.5, 0.7, 1.0) AS score
            FROM pages_fts
            JOIN pages ON pages.rel_path = pages_fts.rel_path
            WHERE pages_fts MATCH ?
        """
        params: list[Any] = [fts_q]
        if category:
            sql += " AND pages.category = ?"
            params.append(category)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        try:
            cur = self.store._conn.execute(sql, params)
        except Exception as e:
            logger.debug("CortexRetriever: FTS search failed (%s) for query=%r", e, fts_q)
            return []
        rows: list[dict] = []
        for row in cur.fetchall():
            d = dict(row)
            if snippet_chars and d.get("snippet") and len(d["snippet"]) > snippet_chars:
                d["snippet"] = d["snippet"][:snippet_chars] + "…"
            rows.append(d)
        return rows
