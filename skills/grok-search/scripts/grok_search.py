#!/usr/bin/env python3
"""grok_search.py — real-time web + X (Twitter) search via xAI's Grok.

Stdlib-only. Calls xAI's Responses API (https://api.x.ai/v1/responses) with
Grok's server-side agentic tools enabled:

  - web_search : real-time web search + page browsing
  - x_search   : search X (Twitter) posts, users, and threads

Grok runs the searching server-side and returns results with citations. This
helper asks Grok to emit a compact JSON block so callers get structured rows,
and falls back to the response's citation annotations if Grok narrates instead.

Auth (first hit wins):
  1. --api-key flag
  2. env XAI_API_KEY

Why a skill script and not the built-in `xai` web backend?
  The built-in backend makes Grok *the* web_search provider for a profile.
  This script is the opposite: an on-demand tool you reach for by name when
  you specifically want real-time or X/Twitter-native results, while your
  default web search stays on a general-purpose provider.

Trust note: Grok *generates* the result URLs itself (it is an LLM, not a
search index), so a query built from untrusted input can in principle steer
the URLs it returns. Validate any URL before fetching it downstream.

Commands:
  web  "<query>"  [--limit N] [--recency-days D] [--allowed-domains a,b]
                  [--excluded-domains a,b] [--model M]
  x    "<query>"  [--limit N] [--handles a,b] [--from-date YYYY-MM-DD]
                  [--to-date YYYY-MM-DD] [--model M]

Output: JSON object {"query", "mode", "results": [{title, url, description,
source}], "citations": [...]} to stdout. Errors print JSON to stderr and exit
non-zero.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api.x.ai/v1/responses"
DEFAULT_MODEL = "grok-4.3"  # stable reasoning flagship; agentic tools require a reasoning model
DEFAULT_TIMEOUT = 90
_MAX_DOMAINS = 5  # xAI cap on allowed/excluded domain filters
_MAX_HANDLES = 20  # xAI cap on allowed_x_handles filters
_JSON_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _die(msg: str, code: int = 1) -> None:
    json.dump({"error": msg}, sys.stderr)
    sys.stderr.write("\n")
    sys.exit(code)


def _valid_url(url: str) -> bool:
    """Accept only well-formed http(s) URLs. Grok generates result URLs, so we
    reject non-web schemes (javascript:, data:, file:, …) before emitting them."""
    try:
        parsed = urllib.parse.urlparse(url)
    except (ValueError, AttributeError):
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _api_key(args) -> str:
    key = (args.api_key or os.environ.get("XAI_API_KEY", "")).strip()
    if not key:
        _die("No xAI credentials. Pass --api-key or set XAI_API_KEY.")
    return key


def _csv(value: str | None, label: str = "value", cap: int = _MAX_DOMAINS) -> list[str]:
    if not value:
        return []
    out: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if item:
            out.append(item)
    if len(out) > cap:
        _die(f"Too many {label} ({len(out)}); xAI allows at most {cap}.")
    return out


def _build_prompt(query: str, limit: int, mode: str) -> str:
    where = "the web" if mode == "web" else "X (Twitter)"
    return (
        f"Use the {'web_search' if mode == 'web' else 'x_search'} tool to find "
        f"current information on {where} for the query below, then respond with "
        "ONLY a single JSON object — no prose, no markdown fences — matching:\n\n"
        '{"results": [{"title": "string", "url": "string", '
        '"description": "1-2 sentence summary"}]}\n\n'
        f"Return at most {limit} results ordered by relevance, absolute https:// "
        'URLs. If none exist, return {"results": []}.\n\n'
        f"Query: {query}"
    )


def _post(payload: dict, key: str, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "hermes-grok-search/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            _die(f"xAI returned a non-JSON response: {raw[:200]}".rstrip(), code=2)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            pass
        detail = body[:300]
        if body:
            try:
                err = json.loads(body).get("error")
                if isinstance(err, dict):
                    detail = err.get("message") or err.get("code") or detail
                elif isinstance(err, str):
                    detail = err
            except (json.JSONDecodeError, ValueError, AttributeError):
                pass
        _die(f"xAI HTTP {exc.code}: {detail}".rstrip(), code=2)
    except urllib.error.URLError as exc:
        _die(f"Could not reach xAI: {exc.reason}", code=2)
    except (TimeoutError, OSError) as exc:
        _die(f"xAI request failed: {exc}", code=2)
    return {}  # unreachable


def _collect_text_and_annotations(data: dict) -> tuple[list[str], list[dict]]:
    texts: list[str] = []
    annotations: list[dict] = []
    for item in data.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for chunk in item.get("content", []) or []:
            if not isinstance(chunk, dict) or chunk.get("type") != "output_text":
                continue
            text = chunk.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
            for ann in chunk.get("annotations", []) or []:
                if isinstance(ann, dict):
                    annotations.append(ann)
    return texts, annotations


def _parse_json_results(text: str, limit: int) -> list[dict] | None:
    candidates = [text]
    match = _JSON_RE.search(text)
    if match and match.group(0) != text:
        candidates.append(match.group(0))
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        rows = parsed.get("results")
        if not isinstance(rows, list):
            continue
        out: list[dict] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url", "")).strip()
            if not _valid_url(url):
                continue
            out.append(
                {
                    "title": str(row.get("title", "")).strip(),
                    "url": url,
                    "description": str(row.get("description", "")).strip(),
                    "source": "grok",
                }
            )
        # A well-formed object with a `results` list is authoritative — return it
        # even when empty, so "Grok found nothing" is not misread as a parse
        # failure that falls through to citation scraping.
        return out
    return None


def _results_from_citations(data: dict, annotations: list[dict], limit: int) -> list[dict]:
    urls: list[str] = []
    for ann in annotations:
        url = str(ann.get("url", "")).strip()
        if url:
            urls.append(url)
    if not urls:
        cites = data.get("citations")
        if isinstance(cites, list):
            urls = [str(u).strip() for u in cites if isinstance(u, str) and u.strip()]
    seen: set[str] = set()
    out: list[dict] = []
    for url in urls:
        if url in seen or not _valid_url(url):
            continue
        seen.add(url)
        out.append({"title": "", "url": url, "description": "", "source": "grok"})
        if len(out) >= limit:
            break
    return out


def _search(mode: str, args) -> None:
    key = _api_key(args)
    limit = max(1, min(int(args.limit), 100))
    tool: dict = {"type": "web_search" if mode == "web" else "x_search"}

    if mode == "web":
        allowed = _csv(getattr(args, "allowed_domains", None), "allowed-domains")
        excluded = _csv(getattr(args, "excluded_domains", None), "excluded-domains")
        if allowed and excluded:
            _die("allowed-domains and excluded-domains are mutually exclusive (xAI rule).")
        if allowed:
            tool["filters"] = {"allowed_domains": allowed}
        elif excluded:
            tool["filters"] = {"excluded_domains": excluded}
        if getattr(args, "recency_days", None):
            # xAI's web_search recency control is from_date/to_date, not a
            # recency_days filter field (unknown fields are silently ignored),
            # so translate the day count into a concrete from_date.
            days = max(1, int(args.recency_days))
            since = datetime.date.today() - datetime.timedelta(days=days)
            tool["from_date"] = since.isoformat()
    else:  # x_search
        handles = _csv(getattr(args, "handles", None), "handles", cap=_MAX_HANDLES)
        if handles:
            tool["allowed_x_handles"] = handles
        if getattr(args, "from_date", None):
            tool["from_date"] = args.from_date
        if getattr(args, "to_date", None):
            tool["to_date"] = args.to_date

    payload = {
        "model": args.model,
        "input": [{"role": "user", "content": _build_prompt(args.query, limit, mode)}],
        "tools": [tool],
        "include": ["no_inline_citations"],
    }

    data = _post(payload, key, DEFAULT_TIMEOUT)

    api_error = data.get("error") if isinstance(data, dict) else None
    if isinstance(api_error, dict):
        _die(f"xAI returned an error: {api_error.get('message') or api_error.get('code') or 'unknown'}", code=2)
    elif isinstance(api_error, str) and api_error.strip():
        _die(f"xAI returned an error: {api_error.strip()}", code=2)

    texts, annotations = _collect_text_and_annotations(data)
    # `_parse_json_results` returns None on parse failure and a list (possibly
    # empty) when Grok emitted a well-formed results object. An authoritative
    # empty list stops the search; only None falls through to citation scraping.
    results: list[dict] | None = None
    for block in texts:
        parsed = _parse_json_results(block, limit)
        if parsed is not None:
            results = parsed
            break
    if results is None:
        results = _results_from_citations(data, annotations, limit)

    # Merge citation URLs from BOTH sources: per-chunk annotations and the
    # Responses API's top-level `citations` field (which is where sources land
    # when no_inline_citations suppresses inline annotations).
    citations: list[str] = []
    citation_sources: list[str] = [str(a.get("url", "")).strip() for a in annotations]
    top_level = data.get("citations") if isinstance(data, dict) else None
    if isinstance(top_level, list):
        citation_sources.extend(str(u).strip() for u in top_level if isinstance(u, str))
    for url in citation_sources:
        if url and url not in citations and _valid_url(url):
            citations.append(url)

    json.dump(
        {"query": args.query, "mode": mode, "results": results, "citations": citations},
        sys.stdout,
        indent=2,
    )
    sys.stdout.write("\n")


def main() -> None:
    # Shared options live on a parent parser so global flags like --api-key work
    # whether they appear before OR after the subcommand (argparse otherwise
    # rejects a root-only flag placed after the subcommand name).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--api-key", help="xAI API key (else env XAI_API_KEY)")

    parser = argparse.ArgumentParser(
        description="Grok real-time web + X search", parents=[common]
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    web = sub.add_parser("web", help="Real-time web search via Grok", parents=[common])
    web.add_argument("query")
    web.add_argument("--limit", type=int, default=5)
    web.add_argument("--recency-days", type=int)
    web.add_argument("--allowed-domains", help="comma-separated, max 5")
    web.add_argument("--excluded-domains", help="comma-separated, max 5")
    web.add_argument("--model", default=DEFAULT_MODEL)

    xp = sub.add_parser("x", help="Search X (Twitter) posts via Grok", parents=[common])
    xp.add_argument("query")
    xp.add_argument("--limit", type=int, default=5)
    xp.add_argument("--handles", help="comma-separated X handles, max 20")
    xp.add_argument("--from-date", help="YYYY-MM-DD")
    xp.add_argument("--to-date", help="YYYY-MM-DD")
    xp.add_argument("--model", default=DEFAULT_MODEL)

    args = parser.parse_args()
    _search(args.mode, args)


if __name__ == "__main__":
    main()
