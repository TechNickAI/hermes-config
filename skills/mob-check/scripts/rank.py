#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""mob-check ranker - engagement-weighted, recency-scoped cross-source ranking.

Reads a JSON corpus on stdin, writes a ranked JSON brief on stdout. Pure stdlib,
so it runs as `uv run rank.py` or plain `python3 rank.py`. No network, no API keys.

This is the deterministic core of the `mob-check` skill: the agent fetches items with
Hermes' native tools (web_search, x_search, youtube-content, polymarket, ...), drops
them into the input schema, and this script does the math the original last30days
engine did - per-source engagement normalization, weighted reciprocal rank fusion,
per-author capping, source diversification, and relevance pruning - so ranking is
reproducible instead of vibes.

Input  (stdin JSON):
  {
    "query": "kanye west",
    "freshness_mode": "balanced_recent",   # strict_recent | balanced_recent | evergreen_ok
    "now": "2026-06-14T00:00:00Z",          # optional; defaults to current UTC
    "items": [
      {
        "source": "reddit",                 # reddit|x|youtube|hackernews|polymarket|...
        "id": "abc123",                     # stable per-source id (falls back to url)
        "title": "...",
        "url": "https://...",
        "snippet": "...",                   # body/comment text used for relevance
        "author": "u/someone",              # optional; used for per-author cap
        "published_at": "2026-06-10T12:00:00Z",
        "engagement": {"score": 1500, "num_comments": 320, "upvote_ratio": 0.95}
      }
    ],
    "subqueries": [                          # optional; defaults to one weight-1 stream
      {"label": "primary", "weight": 1.0, "sources": ["reddit", "x"]}
    ]
  }

Output (stdout JSON):
  {
    "query": "...",
    "ranked": [ {rank, score, title, url, source, author, local_relevance,
                 freshness, engagement, sources, why} ],
    "coverage": {"per_source": {...}, "total_in": N, "total_ranked": M,
                 "thin_evidence": bool},
    "notes": [ ... ]
  }

Flags:
  --top N            limit ranked output (default 25)
  --freshness-mode   override input freshness_mode
  --self-test        run built-in assertions (golden ordering) and exit
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Source quality: editorial signal-to-noise. Web/grounding is 1.0 baseline;    #
# social platforms discounted for noise. Ported from last30days signals.py.    #
# --------------------------------------------------------------------------- #
SOURCE_QUALITY = {
    "web": 1.0,
    "youtube": 0.85,
    "digg": 0.85,
    "hackernews": 0.8,
    "xiaohongshu": 0.7,
    "x": 0.68,
    "bluesky": 0.66,
    "reddit": 0.6,
    "truthsocial": 0.6,
    "instagram": 0.58,
    "tiktok": 0.58,
    "polymarket": 0.5,
}
DEFAULT_SOURCE_QUALITY = 0.6

# Per-source engagement field weights. Reddit/YouTube/TikTok have bespoke
# functions (top-comment slot); the rest use these (field, weight) tuples.
ENGAGEMENT_WEIGHTS = {
    "x": [("likes", 0.55), ("reposts", 0.25), ("replies", 0.15), ("quotes", 0.05)],
    "instagram": [("views", 0.50), ("likes", 0.30), ("comments", 0.20)],
    "hackernews": [("points", 0.55), ("comments", 0.45)],
    "bluesky": [("likes", 0.40), ("reposts", 0.30), ("replies", 0.20), ("quotes", 0.10)],
    "truthsocial": [("likes", 0.45), ("reposts", 0.30), ("replies", 0.25)],
    "polymarket": [("volume", 0.60), ("liquidity", 0.40)],
    "digg": [("postCount", 0.40), ("uniqueAuthors", 0.30), ("rank_score", 0.30)],
}

RRF_K = 60  # standard RRF smoothing constant (Cormack et al. 2009)
MAX_ITEMS_PER_AUTHOR = 3
DIVERSITY_RELEVANCE_THRESHOLD = 0.25
MIN_PER_SOURCE = 2
# Below this many ranked items OR sources, the corpus is too thin to synthesize
# a confident brief. The skill should say "thin evidence" instead of bluffing.
THIN_EVIDENCE_MIN_ITEMS = 4
THIN_EVIDENCE_MIN_SOURCES = 2

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "what", "how", "why", "who", "vs", "versus",
    "best", "top", "about", "this", "that", "these", "those", "it", "its",
}


# --------------------------------------------------------------------------- #
# Relevance: token-overlap between query and item text. Simplified port of      #
# relevance.py token_overlap_relevance - coverage + precision, 0.0..1.0.        #
# --------------------------------------------------------------------------- #
def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t]


def _informative(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


def local_relevance(query: str, item: dict) -> float:
    q_tokens = _informative(_tokens(query))
    if not q_tokens:
        return 0.5  # neutral fallback for empty/stopword-only queries
    text = "\n".join(
        str(item.get(k, "")) for k in ("title", "snippet", "body") if item.get(k)
    )
    doc_tokens = set(_tokens(text))
    if not doc_tokens:
        base = 0.0
    else:
        q_set = set(q_tokens)
        coverage = sum(1 for t in q_set if t in doc_tokens) / len(q_set)
        matched = sum(1 for t in doc_tokens if t in q_set)
        precision = matched / max(1, len(doc_tokens))
        phrase_bonus = 0.0
        if query.strip().lower() in text.lower() and len(q_tokens) > 1:
            phrase_bonus = 0.12
        base = 0.62 * (coverage ** 1.3) + 0.26 * min(1.0, precision * 4) + phrase_bonus
        base = min(1.0, base)

    # High-engagement YouTube floor: official videos with huge view counts often
    # have titles that don't keyword-match the query but are clearly important.
    eng = item.get("engagement") or {}
    if item.get("source") == "youtube" and _num(eng.get("views")) > 100_000:
        base = max(base, 0.3)
    # Project-mode floor: explicitly requested GitHub repo etc.
    if "project-mode" in (item.get("labels") or []):
        base = max(base, 0.8)
    return round(base, 4)


# --------------------------------------------------------------------------- #
# Freshness: recency score 0..100, shaped by mode. Port of dates.recency_score #
# + signals.freshness. Half-life style decay over ~30 days.                     #
# --------------------------------------------------------------------------- #
def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        s = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def recency_score(published_at: str | None, now: datetime, half_life_days: float = 30.0) -> float:
    dt = _parse_dt(published_at)
    if dt is None:
        # Undated items should not be auto-zeroed: many real sources (search
        # snippets, GitHub, evergreen pages) omit dates. Give a neutral-low floor
        # so a dated-recent item still wins but an undated one is not buried.
        return 35.0
    age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
    # 100 at age 0, 50 at half_life, ~25 at 2x half_life. 30-day default half-life
    # suits "what people are saying" research where 4-8 week old threads still matter.
    return 100.0 * (0.5 ** (age_days / half_life_days))


# Half-life per freshness mode: NEWS wants sharp decay, evergreen wants gentle.
_HALF_LIFE = {"strict_recent": 12.0, "balanced_recent": 30.0, "evergreen_ok": 75.0}


def freshness(published_at: str | None, now: datetime, mode: str) -> int:
    hl = _HALF_LIFE.get(mode, 30.0)
    score = recency_score(published_at, now, hl)
    if mode == "strict_recent":
        return int(score)
    if mode == "evergreen_ok":
        return int((score * 0.6) + 40)
    return int((score * 0.8) + 10)  # balanced_recent (default)


# --------------------------------------------------------------------------- #
# Engagement: per-source weighted log1p, then min-max normalized WITHIN source. #
# --------------------------------------------------------------------------- #
def _num(value) -> float:
    try:
        n = float(value)
        return n if n > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def log1p_safe(value) -> float:
    n = _num(value)
    return math.log1p(n) if n > 0 else 0.0


def _top_comment_score(item: dict) -> float:
    comments = (item.get("engagement") or {}).get("top_comments") or item.get("top_comments") or []
    if comments and isinstance(comments[0], dict):
        return log1p_safe(comments[0].get("score"))
    return 0.0


def engagement_raw(item: dict) -> float | None:
    src = item.get("source") or "unknown"
    eng = item.get("engagement") or {}
    if src == "reddit":
        score = log1p_safe(eng.get("score"))
        comments = log1p_safe(eng.get("num_comments"))
        ratio = _num(eng.get("upvote_ratio"))
        top = _top_comment_score(item)
        if not any([score, comments, ratio, top]):
            return None
        return 0.50 * score + 0.35 * comments + 0.05 * (ratio * 10.0) + 0.10 * top
    if src == "youtube":
        views, likes = log1p_safe(eng.get("views")), log1p_safe(eng.get("likes"))
        comments, top = log1p_safe(eng.get("comments")), _top_comment_score(item)
        if not any([views, likes, comments, top]):
            return None
        return 0.45 * views + 0.32 * likes + 0.13 * comments + 0.10 * top
    if src == "tiktok":
        views, likes = log1p_safe(eng.get("views")), log1p_safe(eng.get("likes"))
        comments, top = log1p_safe(eng.get("comments")), _top_comment_score(item)
        if not any([views, likes, comments, top]):
            return None
        return 0.45 * views + 0.27 * likes + 0.18 * comments + 0.10 * top
    weights = ENGAGEMENT_WEIGHTS.get(src)
    if weights:
        vals = [(log1p_safe(eng.get(f)), w) for f, w in weights]
        if not any(v for v, _ in vals):
            return None
        return sum(v * w for v, w in vals)
    # generic: mean of logged values
    logged = [v for v in (log1p_safe(x) for x in eng.values()) if v > 0]
    return sum(logged) / len(logged) if logged else None


def normalize_0_100(values: list[float | None]) -> list[int | None]:
    valid = [v for v in values if v is not None]
    if not valid:
        return [None] * len(values)
    low, high = min(valid), max(valid)
    if math.isclose(low, high):
        return [50 if v is not None else None for v in values]
    return [None if v is None else int(((v - low) / (high - low)) * 100) for v in values]


# --------------------------------------------------------------------------- #
# URL-based dedup key.                                                          #
# --------------------------------------------------------------------------- #
def _url_ok(url: str) -> bool:
    """True if the url looks like a real, linkable permalink (not a placeholder)."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip().lower()
    if not u.startswith(("http://", "https://")):
        return False
    # Common placeholder/fabrication tells the synthesizer must not link.
    bad = ("example", "placeholder", "...", "<", "your-", "xxx", "id=example",
           "v=example", "watch?v=abc", "/abc123", "todo")
    return not any(b in u for b in bad)


def candidate_key(item: dict) -> str:
    url = (item.get("url") or "").strip().lower()
    if url:
        url = re.sub(r"^https?://", "", url)
        url = re.sub(r"^(www\.|old\.|m\.)", "", url)
        url = url.split("?")[0].rstrip("/")
        return url
    return f"{item.get('source')}:{item.get('id')}"


# --------------------------------------------------------------------------- #
# Main pipeline.                                                                #
# --------------------------------------------------------------------------- #
def annotate(items: list[dict], query: str, now: datetime, mode: str) -> None:
    """Attach local_relevance, freshness, source_quality, and per-source-normalized
    engagement_score to each item in place."""
    by_source: dict[str, list[dict]] = {}
    for it in items:
        by_source.setdefault(it.get("source", "unknown"), []).append(it)
    for src, group in by_source.items():
        eng_norm = normalize_0_100([engagement_raw(it) for it in group])
        for it, e in zip(group, eng_norm):
            it["_relevance"] = local_relevance(query, it)
            it["_freshness"] = freshness(it.get("published_at"), now, mode)
            it["_engagement"] = e
            it["_quality"] = SOURCE_QUALITY.get(src, DEFAULT_SOURCE_QUALITY)
            # Engagement is missing on ~90% of live items (search snippets hide
            # counts). When absent, redistribute its weight to relevance+freshness
            # instead of scoring it zero, which would bury high-signal-but-uncounted
            # items. Source quality breaks ties between equally relevant items so a
            # high-signal source (HN, YouTube) outranks a noisy one.
            if e is None:
                base = 0.72 * it["_relevance"] + 0.28 * (it["_freshness"] / 100.0)
            else:
                base = (
                    0.60 * it["_relevance"]
                    + 0.25 * (it["_freshness"] / 100.0)
                    + 0.15 * (e / 100.0)
                )
            it["_rank_score"] = base * (0.85 + 0.15 * it["_quality"])


def prune_low_relevance(items: list[dict], minimum: float = 0.15) -> list[dict]:
    social = {"reddit", "x", "tiktok", "instagram", "bluesky", "truthsocial"}
    present = {it.get("source") for it in items}

    def passes(it: dict) -> bool:
        rel = it.get("_relevance", 0.0)
        if rel < minimum:
            return False
        if it.get("source") in social and not it.get("_engagement"):
            if rel < minimum * 1.5:
                return False
        return True

    filtered = [it for it in items if passes(it)]
    return filtered or items


def weighted_rrf(items: list[dict], subqueries: list[dict]) -> list[dict]:
    """Fuse per-(subquery, source) ranked streams into one candidate pool."""
    # Build streams: each subquery sees the sources it declares (or all).
    candidates: dict[str, dict] = {}
    for sq in subqueries:
        label = sq.get("label", "primary")
        weight = float(sq.get("weight", 1.0))
        srcs = sq.get("sources")
        sq_query = sq.get("query")
        stream = [it for it in items if (srcs is None or it.get("source") in srcs)]
        # A subquery may carry its own `query` text (used for COMPARISON, where each
        # side gets a labeled subquery). When present, rank this stream by relevance
        # to THAT subquery, not just the global rank score, so each side's most
        # on-topic items earn the strong low-rank RRF contributions. Without this the
        # labels are inert and a single side can monopolize the fused pool.
        if sq_query:
            sqq = str(sq_query)
            stream.sort(
                key=lambda it: 0.65 * local_relevance(sqq, it)
                + 0.35 * (it.get("_rank_score", 0.0)),
                reverse=True,
            )
        else:
            stream.sort(key=lambda it: it.get("_rank_score", 0.0), reverse=True)
        for rank, it in enumerate(stream, start=1):
            key = candidate_key(it)
            contribution = weight / (RRF_K + rank)
            cand = candidates.get(key)
            if cand is None:
                raw_url = it.get("url", "") or ""
                candidates[key] = {
                    "key": key, "title": it.get("title", ""),
                    "url": raw_url if _url_ok(raw_url) else "",
                    "source": it.get("source"), "author": it.get("author"),
                    "snippet": it.get("snippet", ""),
                    "local_relevance": it.get("_relevance", 0.0),
                    "freshness": it.get("_freshness", 0),
                    "engagement": it.get("_engagement"),
                    "quality": it.get("_quality", DEFAULT_SOURCE_QUALITY),
                    "rrf": contribution, "sources": [it.get("source")],
                }
            else:
                cand["rrf"] += contribution
                # If the candidate was first seen with an unlinkable url, adopt a
                # clean one from a later duplicate.
                if not cand.get("url"):
                    iu = it.get("url", "") or ""
                    if _url_ok(iu):
                        cand["url"] = iu
                cand["local_relevance"] = max(cand["local_relevance"], it.get("_relevance", 0.0))
                cand["freshness"] = max(cand["freshness"], it.get("_freshness", 0))
                ie = it.get("_engagement")
                if ie is not None:
                    cand["engagement"] = ie if cand["engagement"] is None else max(cand["engagement"], ie)
                if it.get("source") not in cand["sources"]:
                    cand["sources"].append(it.get("source"))
                # keep the longer snippet
                if len((it.get("snippet") or "").split()) > len((cand["snippet"] or "").split()):
                    cand["snippet"] = it.get("snippet")
    pool = list(candidates.values())
    pool.sort(key=lambda c: (-c["rrf"], -c["local_relevance"], -c["freshness"], c["source"], c["title"]))
    return pool


def apply_author_cap(pool: list[dict], cap: int = MAX_ITEMS_PER_AUTHOR) -> list[dict]:
    counts: dict[str, int] = {}
    out = []
    for c in pool:
        author = (c.get("author") or "").strip().lower()
        if not author:
            out.append(c)
            continue
        if counts.get(author, 0) < cap:
            out.append(c)
            counts[author] = counts.get(author, 0) + 1
    return out


def diversify(pool: list[dict], limit: int) -> list[dict]:
    """Reserve MIN_PER_SOURCE slots for each qualifying source before truncating."""
    max_rel: dict[str, float] = {}
    for c in pool:
        max_rel[c["source"]] = max(max_rel.get(c["source"], 0.0), c["local_relevance"])
    reserved: dict[str, list[dict]] = {}
    remainder: list[dict] = []
    for c in pool:
        qualifies = max_rel.get(c["source"], 0.0) >= DIVERSITY_RELEVANCE_THRESHOLD
        bucket = reserved.setdefault(c["source"], [])
        if qualifies and len(bucket) < MIN_PER_SOURCE:
            bucket.append(c)
        else:
            remainder.append(c)
    result = [c for bucket in reserved.values() for c in bucket]
    seen = {c["key"] for c in result}
    for c in remainder:
        if len(result) >= limit:
            break
        if c["key"] not in seen:
            result.append(c)
            seen.add(c["key"])
    result.sort(key=lambda c: (-c["rrf"], -c["local_relevance"], -c["freshness"], c["source"], c["title"]))
    return result[:limit]


def _why(c: dict) -> str:
    bits = [f"rel {c['local_relevance']:.2f}", f"fresh {c['freshness']}"]
    if c.get("engagement") is not None:
        bits.append(f"eng {c['engagement']}")
    if len(c.get("sources", [])) > 1:
        bits.append(f"{len(c['sources'])} sources")
    return ", ".join(bits)


def rank(payload: dict, top: int, mode_override: str | None) -> dict:
    query = payload.get("query", "")
    items = list(payload.get("items") or [])
    mode = mode_override or payload.get("freshness_mode") or "balanced_recent"
    now = _parse_dt(payload.get("now")) or datetime.now(timezone.utc)
    subqueries = payload.get("subqueries") or [{"label": "primary", "weight": 1.0, "sources": None}]

    total_in = len(items)
    per_source_in: dict[str, int] = {}
    for it in items:
        per_source_in[it.get("source", "unknown")] = per_source_in.get(it.get("source", "unknown"), 0) + 1

    annotate(items, query, now, mode)
    items = prune_low_relevance(items)
    pool = weighted_rrf(items, subqueries)
    pool = apply_author_cap(pool)
    pool = diversify(pool, top)

    ranked = []
    for i, c in enumerate(pool, start=1):
        ranked.append({
            "rank": i,
            "score": round(c["rrf"], 6),
            "title": c["title"],
            "url": c["url"],
            "source": c["source"],
            "author": c.get("author"),
            "local_relevance": round(c["local_relevance"], 3),
            "freshness": c["freshness"],
            "engagement": c.get("engagement"),
            "sources": c["sources"],
            "why": _why(c),
        })

    ranked_sources = {r["source"] for r in ranked}
    thin = len(ranked) < THIN_EVIDENCE_MIN_ITEMS or len(ranked_sources) < THIN_EVIDENCE_MIN_SOURCES
    # Engagement coverage: how many ranked items carry a real engagement number.
    with_eng = sum(1 for r in ranked if r.get("engagement") is not None)
    eng_frac = round(with_eng / len(ranked), 2) if ranked else 0.0
    eng_thin = bool(ranked) and eng_frac < 0.5
    notes = []
    if thin:
        notes.append(
            "THIN EVIDENCE: too few items or sources to synthesize a confident brief. "
            "Report what was found, name the gaps, and do not overstate."
        )
    if eng_thin:
        notes.append(
            f"LOW ENGAGEMENT COVERAGE: only {with_eng}/{len(ranked)} ranked items have a "
            "real engagement number. Engagement is this skill's core signal. Go back and "
            "web_extract the top threads to read actual upvote/point/view counts before "
            "synthesizing. Do NOT invent numbers; use qualitative language for any you "
            "genuinely cannot recover."
        )
    return {
        "query": query,
        "freshness_mode": mode,
        "ranked": ranked,
        "coverage": {
            "per_source_in": per_source_in,
            "total_in": total_in,
            "total_ranked": len(ranked),
            "ranked_sources": sorted(ranked_sources),
            "thin_evidence": thin,
            "engagement_coverage": eng_frac,
            "low_engagement_coverage": eng_thin,
        },
        "notes": notes,
    }


# --------------------------------------------------------------------------- #
# Self-test: golden ordering + invariants. No pytest dependency.               #
# --------------------------------------------------------------------------- #
def self_test() -> int:
    now = "2026-06-14T00:00:00Z"
    # Engagement normalization guard: a 2M-view YouTube item must NOT auto-outrank
    # a strongly on-topic, high-upvote Reddit thread purely on raw counts.
    payload = {
        "query": "acme robot mower",
        "now": now,
        "items": [
            {"source": "reddit", "id": "r1", "title": "Acme robot mower long-term review",
             "snippet": "My Acme robot mower after 6 months, the good and bad",
             "author": "u/lawnnerd", "published_at": "2026-06-12T00:00:00Z",
             "engagement": {"score": 1800, "num_comments": 420, "upvote_ratio": 0.96}},
            {"source": "reddit", "id": "r2", "title": "Acme mower stuck on slopes",
             "snippet": "Anyone else have the Acme robot mower get stuck on slopes?",
             "author": "u/hilly", "published_at": "2026-06-10T00:00:00Z",
             "engagement": {"score": 600, "num_comments": 95, "upvote_ratio": 0.9}},
            {"source": "youtube", "id": "y1", "title": "I bought 10 gadgets from a vending machine",
             "snippet": "vending machine haul unboxing, not really about mowers",
             "author": "BigChannel", "published_at": "2026-06-13T00:00:00Z",
             "engagement": {"views": 2_000_000, "likes": 80_000, "comments": 5000}},
            {"source": "x", "id": "x1", "title": "Acme robot mower just cut my whole yard",
             "snippet": "the Acme robot mower is wild, did my whole yard unattended",
             "author": "@gardengeek", "published_at": "2026-06-13T00:00:00Z",
             "engagement": {"likes": 1200, "reposts": 300, "replies": 80}},
        ],
    }
    out = rank(payload, top=25, mode_override=None)
    ranked = out["ranked"]
    assert ranked, "expected ranked output"
    top_titles = [r["title"] for r in ranked[:2]]
    # The off-topic 2M-view video must not be #1.
    assert ranked[0]["source"] in {"reddit", "x"}, f"off-topic high-view item ranked #1: {ranked[0]}"
    assert any("review" in t.lower() for t in top_titles), f"on-topic review not near top: {top_titles}"

    # Author cap: 4 items from one author collapse to <= 3 in the pool.
    cap_payload = {
        "query": "widget",
        "now": now,
        "items": [
            {"source": "x", "id": f"x{i}", "title": f"widget take {i}",
             "snippet": "widget widget widget", "author": "@spammer",
             "published_at": "2026-06-13T00:00:00Z",
             "engagement": {"likes": 100 + i}}
            for i in range(4)
        ],
    }
    cap_out = rank(cap_payload, top=25, mode_override=None)
    assert len(cap_out["ranked"]) <= 3, f"author cap failed: {len(cap_out['ranked'])}"

    # Thin evidence flag fires on a single-source, single-item corpus.
    thin_out = rank({"query": "x", "now": now, "items": [
        {"source": "web", "id": "w1", "title": "x explained", "snippet": "x is x",
         "published_at": "2026-06-13T00:00:00Z", "engagement": {}}]}, top=25, mode_override=None)
    assert thin_out["coverage"]["thin_evidence"] is True, "thin evidence not flagged"

    # Freshness mode shifts ordering: an old high-engagement item vs a fresh one.
    fm = {"query": "news topic", "now": now, "items": [
        {"source": "x", "id": "old", "title": "news topic big thread",
         "snippet": "news topic happened", "author": "@a",
         "published_at": "2026-05-01T00:00:00Z", "engagement": {"likes": 50000}},
        {"source": "x", "id": "new", "title": "news topic update today",
         "snippet": "news topic just happened", "author": "@b",
         "published_at": "2026-06-13T00:00:00Z", "engagement": {"likes": 200}},
    ]}
    strict = rank({**fm, "freshness_mode": "strict_recent"}, top=25, mode_override=None)
    assert strict["ranked"][0]["title"].endswith("today"), \
        f"strict_recent should favor fresh item: {strict['ranked'][0]['title']}"

    # No broken empty links: every ranked item with no url still has a string url field.
    for r in out["ranked"]:
        assert isinstance(r["url"], str), "url must be a string"

    # Source-quality tiebreak: at equal relevance/freshness and no engagement, a
    # high-signal source (hackernews 0.8) outranks a noisy one (tiktok 0.58).
    tq = rank({"query": "framework laptop", "now": now, "items": [
        {"source": "tiktok", "id": "tt", "title": "framework laptop quick look",
         "snippet": "framework laptop unboxing", "author": "@a",
         "published_at": "2026-06-10T00:00:00Z", "engagement": {}},
        {"source": "hackernews", "id": "hn", "title": "framework laptop teardown",
         "snippet": "framework laptop repairability discussion", "author": "b",
         "published_at": "2026-06-10T00:00:00Z", "engagement": {}},
    ]}, top=25, mode_override=None)
    assert tq["ranked"][0]["source"] == "hackernews", \
        f"source-quality tiebreak failed: {[r['source'] for r in tq['ranked']]}"

    # Undated items are not auto-buried: an undated on-topic item still ranks above
    # an off-topic dated one.
    ud = rank({"query": "acme mower", "now": now, "items": [
        {"source": "web", "id": "u1", "title": "acme mower deep review",
         "snippet": "acme mower long term review thoughts", "author": None,
         "published_at": None, "engagement": {}},
        {"source": "web", "id": "u2", "title": "unrelated gardening tips",
         "snippet": "general lawn care basics nothing about the topic", "author": None,
         "published_at": "2026-06-13T00:00:00Z", "engagement": {}},
    ]}, top=25, mode_override=None)
    assert "review" in ud["ranked"][0]["title"], \
        f"undated on-topic item buried: {ud['ranked'][0]['title']}"

    # Engagement coverage flag: a corpus where <50% of ranked items have engagement
    # numbers should raise low_engagement_coverage so the agent goes back to extract.
    ec = rank({"query": "widget review", "now": now, "items": [
        {"source": "web", "id": f"w{i}", "title": f"widget review {i}",
         "snippet": "widget review details and analysis", "author": None,
         "published_at": "2026-06-10T00:00:00Z", "engagement": {}}
        for i in range(5)
    ]}, top=25, mode_override=None)
    assert ec["coverage"]["low_engagement_coverage"] is True, \
        f"low engagement coverage not flagged: {ec['coverage']}"

    # Placeholder/unlinkable URLs are stripped to "" so they never become broken
    # inline citations downstream. A real url passes through unchanged.
    uf = rank({"query": "acme mower", "now": now, "items": [
        {"source": "web", "id": "p1", "title": "acme mower review",
         "snippet": "acme mower long term review", "author": None,
         "published_at": "2026-06-10T00:00:00Z",
         "url": "https://example.com/placeholder", "engagement": {}},
        {"source": "reddit", "id": "p2", "title": "acme mower real thread",
         "snippet": "acme mower owners discuss real experience", "author": "u/x",
         "published_at": "2026-06-10T00:00:00Z",
         "url": "https://old.reddit.com/r/lawncare/comments/abc/acme/", "engagement": {"score": 200}},
    ]}, top=25, mode_override=None)
    by_title = {r["title"]: r for r in uf["ranked"]}
    assert by_title["acme mower review"]["url"] == "", \
        f"placeholder url not stripped: {by_title['acme mower review']['url']!r}"
    assert by_title["acme mower real thread"]["url"].startswith("https://old.reddit.com"), \
        f"valid url was dropped: {by_title['acme mower real thread']['url']!r}"

    # Comparison balance: with labeled subqueries each carrying its own query, the
    # smaller/lower-engagement side must still surface in the ranked output rather
    # than being buried by the louder side. Side B has one weak item; without
    # per-subquery relevance it would be swamped by side A's three strong items.
    cmp_out = rank({
        "query": "alpha vs beta",
        "now": now,
        "subqueries": [
            {"label": "a", "query": "alpha", "weight": 1.0, "sources": None},
            {"label": "b", "query": "beta", "weight": 1.0, "sources": None},
        ],
        "items": [
            {"source": "reddit", "id": "a1", "title": "alpha is great",
             "snippet": "alpha alpha alpha review", "author": "u/a1",
             "published_at": "2026-06-12T00:00:00Z", "engagement": {"score": 5000}},
            {"source": "reddit", "id": "a2", "title": "alpha deep dive",
             "snippet": "alpha alpha analysis", "author": "u/a2",
             "published_at": "2026-06-12T00:00:00Z", "engagement": {"score": 4000}},
            {"source": "x", "id": "a3", "title": "alpha thread",
             "snippet": "alpha alpha hot take", "author": "@a3",
             "published_at": "2026-06-12T00:00:00Z", "engagement": {"likes": 3000}},
            {"source": "web", "id": "b1", "title": "beta quiet review",
             "snippet": "beta beta beta long term", "author": None,
             "published_at": "2026-06-11T00:00:00Z", "engagement": {}},
        ],
    }, top=4, mode_override=None)
    cmp_titles = [r["title"] for r in cmp_out["ranked"]]
    assert any("beta" in t for t in cmp_titles), \
        f"comparison buried the quiet side entirely: {cmp_titles}"

    print("self-test: PASS (golden ordering, author cap, thin-evidence, freshness mode, "
          "source-quality tiebreak, undated floor, engagement coverage, url safety, "
          "placeholder-url strip, comparison balance)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="mob-check engagement-weighted ranker")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--freshness-mode", choices=["strict_recent", "balanced_recent", "evergreen_ok"])
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    raw = sys.stdin.read()
    if not raw.strip():
        print(json.dumps({"error": "no input on stdin"}), file=sys.stderr)
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}), file=sys.stderr)
        return 2
    result = rank(payload, top=args.top, mode_override=args.freshness_mode)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
