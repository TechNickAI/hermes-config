"""CortexRetriever — hybrid lexical + semantic search over the page index.

Lexical tier: SQLite FTS5 + BM25 over title/tags/body.
Semantic tier: optional page-level embeddings stored by CortexStore.
Fusion: Reciprocal Rank Fusion (RRF), so pages that rank well in both tiers rise
without either tier needing calibrated comparable scores.
"""

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
    """Search Cortex pages via FTS5, optional semantic embeddings, and optional rerank."""

    def __init__(self, store, reranker=None):
        self.store = store
        self.reranker = reranker
        # NB: do NOT cache `store._conn` here. SQLite connections are
        # thread-affine, and CortexStore now hands out per-thread connections
        # via a property. Resolve fresh on every call so search() works from
        # whatever thread the agent's tool worker dispatches us on.

    def _fts_search(
        self,
        query: str,
        *,
        limit: int = 5,
        category: str | None = None,
        snippet_chars: int = 240,
    ) -> list[dict]:
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
            d["fts_score"] = d.get("score")
            d["source"] = "fts"
            rows.append(d)
        return rows

    def _rerank_texts(self, rows: list[dict], query: str = "", max_chars: int = 1000) -> list[str]:
        """Fetch compact, query-focused candidate text for reranking.

        The local cross-encoder has a finite context window. Feeding the first N
        chars of every page is both wasteful and lower quality when the
        answer-bearing sentence sits later in a page. Instead, send stable page
        identity (title/path/tags), the display snippet, and short windows around
        query-token hits in the body. Fall back to the page opening only when no
        query terms are present.
        """
        if not rows:
            return []
        rels = [r.get("rel_path") for r in rows if r.get("rel_path")]
        placeholders = ",".join("?" for _ in rels)
        body_by_rel: dict[str, str] = {}
        if placeholders:
            try:
                cur = self.store._conn.execute(
                    f"SELECT rel_path, body FROM pages WHERE rel_path IN ({placeholders})", rels
                )
                body_by_rel = {str(r["rel_path"]): str(r["body"] or "") for r in cur.fetchall()}
            except Exception as e:
                logger.debug("CortexRetriever: rerank body hydration failed: %s", e)
        query_terms = [t for t in _TOKEN_RE.findall(query.lower()) if t not in _FTS_STOPWORDS][:8]
        docs: list[str] = []
        for row in rows:
            rel = str(row.get("rel_path") or "")
            title = str(row.get("title") or "")
            tags = str(row.get("tags") or "")
            snippet = str(row.get("snippet") or "").replace("\n", " ").strip()
            body = body_by_rel.get(rel) or snippet
            pieces = [f"Title: {title}", f"Path: {rel}", f"Tags: {tags}"]
            if snippet:
                pieces.append(f"Snippet: {snippet}")
            lower = body.lower()
            windows: list[str] = []
            seen_spans: set[tuple[int, int]] = set()
            for term in query_terms:
                pos = lower.find(term)
                if pos < 0:
                    continue
                start = max(0, pos - 180)
                end = min(len(body), pos + 420)
                span = (start, end)
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                windows.append(body[start:end].replace("\n", " ").strip())
                if len(windows) >= 3:
                    break
            if windows:
                pieces.append("Relevant body windows: " + " … ".join(windows))
            else:
                pieces.append(body[:600].replace("\n", " ").strip())
            doc = "\n".join(p for p in pieces if p)
            docs.append(doc[:max_chars])
        return docs

    def _apply_rerank(self, query: str, rows: list[dict]) -> list[dict]:
        """Rerank candidates if configured; otherwise preserve input order."""
        reranker = getattr(self, "reranker", None)
        rerank = getattr(reranker, "rerank", None)
        if not callable(rerank) or not rows:
            return rows
        docs = self._rerank_texts(rows, query=query)
        order = rerank(query, docs, top_n=len(rows))
        if not order:
            return rows
        ranked: list[dict] = []
        for rank, idx in enumerate(order, start=1):
            if 0 <= idx < len(rows):
                row = dict(rows[idx])
                row["rerank_rank"] = rank
                row["source"] = f"{row.get('source', 'unknown')}+rerank"
                ranked.append(row)
        return ranked or rows

    def search(self, query: str, *, limit: int = 5, category: str | None = None, snippet_chars: int = 240) -> list[dict]:
        """Return ranked Cortex pages.

        If semantic embeddings are available, combines FTS5 BM25 and vector
        cosine search via Reciprocal Rank Fusion. If the embedding service/index
        is absent or fails, this remains exactly the old FTS5-only behavior.
        """
        if not query:
            return []

        # Pull a larger candidate set from each tier before fusion.
        candidate_limit = max(limit * 4, 20)
        fts_rows = self._fts_search(query, limit=candidate_limit, category=category, snippet_chars=snippet_chars)
        vector_rows: list[dict] = []
        vector_search = getattr(self.store, "vector_search", None)
        if callable(vector_search):
            vector_rows = vector_search(query, limit=candidate_limit, category=category)

        if not vector_rows:
            return self._finalize(query, fts_rows, limit)
        if not fts_rows:
            return self._finalize(query, vector_rows, limit)

        # Reciprocal Rank Fusion. k=60 is the standard conservative default: it
        # rewards agreement across tiers without letting a single rank-1 result
        # swamp the other list.
        k = 60.0
        merged: dict[str, dict] = {}
        fusion: dict[str, float] = {}

        def add_rows(rows: list[dict], tier: str) -> None:
            for rank, row in enumerate(rows, start=1):
                rel = row["rel_path"]
                if rel not in merged:
                    merged[rel] = dict(row)
                    fusion[rel] = 0.0
                else:
                    # Prefer lexical snippets (highlighted) when available, but
                    # preserve vector score/source metadata from both sides.
                    if tier == "fts" and row.get("snippet"):
                        merged[rel]["snippet"] = row["snippet"]
                    for key in ["fts_score", "vector_score"]:
                        if key in row:
                            merged[rel][key] = row[key]
                fusion[rel] += 1.0 / (k + rank)

        add_rows(fts_rows, "fts")
        add_rows(vector_rows, "vector")

        out: list[dict] = []
        for rel, row in merged.items():
            row["fusion_score"] = fusion[rel]
            has_fts = "fts_score" in row
            has_vec = "vector_score" in row
            row["source"] = "hybrid" if has_fts and has_vec else ("vector" if has_vec else "fts")
            # Keep legacy lower-is-better `score` roughly meaningful for callers
            # that display it, while the actual sort uses fusion_score.
            row["score"] = -row["fusion_score"]
            if snippet_chars and row.get("snippet") and len(row["snippet"]) > snippet_chars:
                row["snippet"] = row["snippet"][:snippet_chars] + "…"
            out.append(row)

        out.sort(key=lambda r: r["fusion_score"], reverse=True)
        return self._finalize(query, out, limit)

    def _finalize(self, query: str, rows: list[dict], limit: int) -> list[dict]:
        """Optionally rerank the fused candidate set, then truncate to `limit`.

        Reranking happens on a bounded candidate window (not the full list) so
        latency stays predictable on large stores. If no reranker is configured
        or it fails, this returns the existing order — exactly the prior behavior.
        """
        reranker = getattr(self, "reranker", None)
        if reranker is None or not rows:
            return rows[:limit]
        # Cap the rerank window: enough candidates to meaningfully reorder the
        # top `limit`, without shipping the whole store to the cross-encoder.
        window = max(limit * 4, 20)
        reranked = self._apply_rerank(query, rows[:window])
        return reranked[:limit]

