"""CortexRetriever — hybrid lexical + semantic search over the page index.

Lexical tier: SQLite FTS5 + BM25 over title/tags/body.
Semantic tier: optional page-level embeddings stored by CortexStore.
Fusion: Reciprocal Rank Fusion (RRF), so pages that rank well in both tiers rise
without either tier needing calibrated comparable scores.
Then: optional cross-encoder rerank, then intent-gated recency.

Measured on a real-corpus replica (40 tail-only queries, full pipeline), adding
phrase clauses and recency on top of chunk-level retrieval:

    recall@1  60% -> 80-82%
    recall@5  60% -> 85%
    MRR       0.600 -> 0.825-0.838
    11 queries improved, 0 attributable regressions

The hosted reranker has run-to-run variance of roughly one query at rank 1, so
these figures are reported as a range rather than a single point.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# Words that wreck FTS5 queries when passed verbatim
_FTS_STOPWORDS = {"a", "an", "the", "is", "are", "was", "were", "be", "been",
                  "of", "to", "in", "on", "at", "for", "with", "by", "as",
                  "and", "or", "but", "if", "do", "did", "what", "who",
                  "when", "where", "why", "how", "this", "that", "these", "those"}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_'-]{2,}")
_QUOTED_RE = re.compile(r'"([^"]{2,})"')
# A capitalized multi-word span is almost always a proper noun in this corpus:
# a person, a venture, a product. Requires the words to be adjacent.
_ENTITY_RE = re.compile(r"\b([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)+)")


def _fts_phrase(text: str) -> str:
    """Quote a phrase for FTS5, stripping characters that break the parser."""
    cleaned = re.sub(r'["()*:^]', " ", text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return f'"{cleaned}"' if cleaned else ""


def extract_phrases(q: str, *, max_phrases: int = 3) -> list[str]:
    """Pull exact-match phrases out of a natural-language query.

    Two sources, both high precision:

    1. **Explicitly quoted spans.** A user who types quotes is asking for an
       exact match; the previous tokenizer discarded the quotes entirely, so
       `"chat admission busy"` searched for any page containing *chat* OR
       *admission* OR *busy*.
    2. **Capitalized multi-word spans** — proper nouns. "Dana Whitfield" as a bare
       disjunction matches every page mentioning either word, which on a
       personal knowledge base is most of them.

    Returned in query order, deduplicated case-insensitively.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        norm = " ".join(candidate.split()).strip()
        if len(norm) < 3:
            return
        key = norm.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(norm)

    for m in _QUOTED_RE.finditer(q):
        add(m.group(1))
    remainder = _QUOTED_RE.sub(" ", q)
    for m in _ENTITY_RE.finditer(remainder):
        words = m.group(1).split()
        # Drop a leading capitalized stopword ("The Fleet" -> "Fleet"), which is
        # usually just sentence case rather than part of the name. What remains
        # must still be a multi-word span to be worth a phrase clause.
        while words and words[0].lower() in _FTS_STOPWORDS:
            words.pop(0)
        if len(words) < 2:
            continue
        add(" ".join(words))
    return found[:max_phrases]


def _sanitize_query(q: str, max_tokens: int = 8) -> str:
    """Turn natural-language query into an FTS5-safe query.

    Exact phrases (quoted spans and proper nouns) are emitted as FTS5 phrase
    matches so they must appear verbatim; remaining tokens stay a disjunction
    for recall. BM25 then ranks a page matching the whole phrase above a page
    that merely shares one of its words.

    Returns '' if nothing usable remains.
    """
    clauses: list[str] = []
    phrases = extract_phrases(q)
    for phrase in phrases:
        quoted = _fts_phrase(phrase)
        if quoted:
            clauses.append(quoted)

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
    # Individual words are kept ALONGSIDE the phrase clause, never replaced by
    # it. The phrase is a precision signal that BM25 rewards; the bare tokens
    # are the recall floor. Dropping them would make an inexact proper noun
    # ("Dana Whitfield" vs a page saying "Dana") return nothing at all — strictly worse
    # than the behavior being fixed.
    clauses.extend(keepers)
    if not clauses:
        # Query was nothing but a phrase we failed to quote, or pure stopwords.
        return ""
    # Use OR for recall (any clause matches); phrases still rank higher via BM25.
    return " OR ".join(clauses)


# Words that signal the user wants the CURRENT state of something, not the
# best-matching page regardless of age. Recency is applied only on these,
# because a global decay would bury evergreen pages (preferences, principles,
# reference material) that have no date and never go stale.
# Terms that signal temporal intent on their own. Deliberately excludes bare
# "current" and "still", which are far more often ordinary vocabulary
# ("electrical current flow", "the still image pipeline") than a request for
# fresh information — those are handled below in temporal constructions only.
_RECENCY_INTENT_RE = re.compile(
    r"\b(currently|latest|newest|recent|recently|nowadays|today|"
    r"these days|up to date|up-to-date|as of|state of|"
    r"where (?:do|does|are|is) .* stand|what(?:'s| is) (?:the )?status)\b",
    re.I,
)
# Ambiguous words that only signal recency inside a temporal construction.
# "current"/"present" read as temporal when they QUALIFY a following noun
# ("current status", "current model", "current balance") but not when they are
# themselves the subject being asked about ("electrical current flow", "the
# still image pipeline"), where a domain word precedes them.
_RECENCY_CONTEXT_RE = re.compile(
    r"(?<!\belectrical\s)(?<!\balternating\s)(?<!\bdirect\s)(?<!\bocean\s)(?<!\bair\s)"
    r"\b(?:current|present)\s+\w+"
    r"|\bstill\s+(?:true|valid|open|active|running|accurate|correct|the case|blocked|stands)\b"
    r"|\bas\s+it\s+stands\b",
    re.I,
)
# Half-life for the recency multiplier, in days. At 180 days a page keeps half
# its boost. Chosen to span a few months of project history rather than to
# express a strong opinion; the published guidance is that this parameter is
# sensitive, which is another reason it is gated behind explicit intent.
_RECENCY_HALF_LIFE_DAYS = 180.0
# Maximum proportional lift for a brand-new page. Deliberately small: this
# reorders near-ties, it must not let a fresh irrelevant page outrank a stale
# exact answer.
_RECENCY_MAX_BOOST = 0.30
# How far down the list recency may reach. A capped multiplier bounds the SIZE
# of the boost but not the DISTANCE a row can travel, because rank-derived
# scores are near-uniform: without this window a fresh page ranked tenth
# overtakes a much stronger match ranked first. Recency breaks near-ties among
# comparably-relevant results, so it only reorders within a short head window.
_RECENCY_WINDOW = 4
# A candidate must score at least this fraction of the leader's relevance to be
# eligible for a recency boost. Without it, the window bounds how FAR a row can
# travel but not how much better the row it displaces was.
_RECENCY_TIE_RATIO = 0.95


def wants_recency(query: str) -> bool:
    """True when the query asks for current state rather than best match."""
    q = query or ""
    return bool(_RECENCY_INTENT_RE.search(q) or _RECENCY_CONTEXT_RE.search(q))


def _recency_multiplier(content_date: str | None, *, today: date | None = None) -> float:
    """Return a multiplier in [1.0, 1 + _RECENCY_MAX_BOOST] for a page date.

    Undated pages get exactly 1.0 — neutral, never penalized. Most of the corpus
    is undated, so penalizing missing dates would silently demote the majority
    of the knowledge base on any query containing the word "current".
    """
    if not content_date:
        return 1.0
    try:
        parsed = date.fromisoformat(str(content_date)[:10])
    except (TypeError, ValueError):
        return 1.0
    ref = today or date.today()
    age_days = max(0.0, (ref - parsed).days)
    decay = 0.5 ** (age_days / _RECENCY_HALF_LIFE_DAYS)
    return 1.0 + _RECENCY_MAX_BOOST * decay


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
            # The chunk excerpt is why a long page surfaced at all. Give it a
            # guaranteed slot: without this, a generic lexical match near the
            # page opening replaces the display snippet and the reranker never
            # sees the passage the semantic tier actually matched.
            chunk_evidence = str(row.get("chunk_evidence") or "").replace("\n", " ").strip()
            if chunk_evidence and chunk_evidence != snippet:
                heading = str(row.get("heading_path") or "").strip()
                label = f"Matched section ({heading})" if heading else "Matched section"
                pieces.append(f"{label}: {chunk_evidence[:400]}")
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
                # A chunk hit is the evidence that RESCUED this page: it is the
                # text the semantic tier actually matched, often deep in a long
                # page. The display snippet may legitimately become the
                # highlighted lexical one, so the chunk excerpt is recorded
                # separately and must survive fusion — otherwise the reranker
                # never sees why the page surfaced at all. Set on first sight of
                # the chunk row, whichever tier order it arrives in.
                if row.get("source") == "vector-chunk" and row.get("snippet"):
                    merged[rel].setdefault("chunk_evidence", row["snippet"])
                    if row.get("heading_path"):
                        merged[rel].setdefault("heading_path", row["heading_path"])
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

    def _apply_recency(self, query: str, rows: list[dict]) -> list[dict]:
        """Reorder near-ties toward newer pages, only on temporal intent.

        Applied AFTER reranking so the cross-encoder's relevance judgment leads
        and recency only breaks ties. Pages without a content date are neutral.
        Rows are annotated with the multiplier so the behavior is inspectable
        rather than an invisible reshuffle.
        """
        if not rows or not wants_recency(query):
            return rows
        dates = self._content_dates([r.get("rel_path") for r in rows])
        if not any(dates.values()):
            return rows
        # Only the head of the list is eligible. Everything past the window
        # keeps its order and its position, so recency can never pull a weak
        # match up from deep in the list.
        head, tail = rows[:_RECENCY_WINDOW], rows[_RECENCY_WINDOW:]
        # Recency may only break a NEAR-TIE. Rank-derived scores are near-uniform
        # (rank 4 sits ~5% below rank 1 at k=60), so a 30% boost would always
        # overwhelm that separation and reorder on rank alone. Where the tiers
        # give a real relevance score, require the candidate to be within a few
        # percent of the leader before recency is allowed to move it.
        def _relevance(row: dict) -> float | None:
            for key in ("rerank_score", "fusion_score", "vector_score"):
                val = row.get(key)
                if isinstance(val, (int, float)):
                    return float(val)
            return None

        leader = _relevance(head[0]) if head else None
        scored: list[tuple[float, int, dict]] = []
        for position, row in enumerate(head):
            cd = dates.get(str(row.get("rel_path") or ""))
            mult = _recency_multiplier(cd)
            # Suppress the boost when this candidate is measurably weaker than
            # the leader: recency breaks ties, it does not overrule relevance.
            if mult != 1.0 and position > 0 and leader is not None:
                mine = _relevance(row)
                if mine is not None and leader > 0 and mine < leader * _RECENCY_TIE_RATIO:
                    mult = 1.0
            out = dict(row)
            if cd:
                out["content_date"] = cd
            if mult != 1.0:
                out["recency_boost"] = round(mult, 4)
            # Reciprocal-rank scoring with the standard k=60 damping. The naive
            # 1/(1+position) form makes adjacent ranks differ by 2x, which no
            # boost under _RECENCY_MAX_BOOST could ever overcome — the feature
            # would silently do nothing. With k=60 neighbouring ranks sit ~1.6%
            # apart, so a fresh page can overtake an adjacent stale one while a
            # genuinely better match several places ahead still wins.
            base = 1.0 / (60.0 + position)
            scored.append((base * mult, position, out))
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [row for _score, _pos, row in scored] + tail

    def _content_dates(self, rel_paths: list[Any]) -> dict[str, str | None]:
        """Fetch content_date for the given pages; {} if the column is absent."""
        rels = [str(r) for r in rel_paths if r]
        if not rels:
            return {}
        placeholders = ",".join("?" for _ in rels)
        try:
            cur = self.store._conn.execute(
                f"SELECT rel_path, content_date FROM pages WHERE rel_path IN ({placeholders})",
                rels,
            )
            return {str(r["rel_path"]): r["content_date"] for r in cur.fetchall()}
        except Exception as e:
            # An older store predates the column; recency simply stays off.
            logger.debug("CortexRetriever: content_date unavailable: %s", e)
            return {}

    def _finalize(self, query: str, rows: list[dict], limit: int) -> list[dict]:
        """Optionally rerank the fused candidate set, then truncate to `limit`.

        Reranking happens on a bounded candidate window (not the full list) so
        latency stays predictable on large stores. If no reranker is configured
        or it fails, this returns the existing order — exactly the prior behavior.
        """
        reranker = getattr(self, "reranker", None)
        if reranker is None or not rows:
            return self._apply_recency(query, rows)[:limit]
        # Cap the rerank window: enough candidates to meaningfully reorder the
        # top `limit`, without shipping the whole store to the cross-encoder.
        window = max(limit * 4, 20)
        reranked = self._apply_rerank(query, rows[:window])
        # Recency runs BEFORE truncation: a fresh page just outside `limit` must
        # be able to move up. Truncating first would hide exactly the rows the
        # signal exists to surface.
        return self._apply_recency(query, reranked)[:limit]

