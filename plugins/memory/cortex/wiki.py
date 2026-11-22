"""Wiki maintenance for Cortex: lint, an operation log, a generated catalog,
temporal validity, and the term scan that drives compile-on-ingest.

Why this exists
---------------
Cortex retrieved well and compiled nothing. A new source became one new page;
the pages it touched were never revised. The result, measured on a 1,533-page
store: 67% of pages had no inbound link, a person folder held 93 dated
snapshot pages instead of one current page, the hand-written ``index.md`` was
six months stale, and only 12 files marked any claim as superseded.

Karpathy's LLM-Wiki pattern names the missing half. The knowledge base is a
*compiled artifact*: each source is integrated into every page it affects,
contradictions are flagged as they arrive, good answers are filed back, and a
periodic lint keeps the whole thing consistent. Everything here is the
deterministic scaffolding for that loop. The judgment (what to write, whether
something is a contradiction) stays with the calling agent.

Design rules
------------
* **Markdown stays the source of truth.** Nothing here writes to SQLite except
  through the store's normal reindex path. The log and the catalog are plain
  markdown files a human can read.
* **Never delete to supersede.** A page that stopped being true gets
  ``invalid_at`` / ``superseded_by`` frontmatter and drops out of default
  search; it stays on disk and stays reachable by path.
* **Lint reports, it does not fix.** Every check is SQL or a regex over the
  index; nothing here rewrites a page body.
* **Machine-generated pages are not knowledge.** Orphan and staleness checks
  skip journals, handoff digests and archive snapshots, otherwise their volume
  swamps the ratio and the report recommends hand-linking cron output.
"""

from __future__ import annotations

import datetime as _dt
import posixpath
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

# The operation log and the generated catalog live at the store root. Both are
# excluded from the page index (see store._should_skip_file) so the KB never
# indexes its own bookkeeping.
LOG_FILENAME = "log.md"
CATALOG_FILENAME = "catalog.md"

# One parseable header per entry: `## [2026-11-22] ingest | Title`.
# The fixed shape is what makes the log greppable with plain unix tools.
LOG_ENTRY_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] ([a-z][a-z0-9_-]*) \| (.+)$")
LOG_OPS = ("ingest", "query", "lint", "curate", "supersede", "note")

# Categories whose pages are generated or append-only by design. They are real
# content for retrieval but not curated knowledge, so structural checks that
# expect a page to be linked or kept current would flag every one of them.
GENERATED_CATEGORIES = frozenset({"daily", "handoff", "audit", "imports", "_root"})

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
_MDLINK_RE = re.compile(r"\]\(([^)\s]+?\.md)\)")
_CURRENT_WORDS_RE = re.compile(
    r"\b(currently|current state|as of now|right now|latest|is now|at present)\b", re.I
)
_ISO_RE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")

STALE_AFTER_DAYS = 120
# A name has to be referenced this many times before its missing page is worth
# reporting; below that it is ordinary prose, not a concept.
MISSING_CONCEPT_MIN_REFS = 3


# ----------------------------------------------------------------- helpers


def today() -> _dt.date:
    return _dt.date.today()


def _iso(value: Any) -> str | None:
    """Normalise a frontmatter date (date, datetime or string) to YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.strftime("%Y-%m-%d")
    m = _ISO_RE.search(str(value))
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def validity(fm: dict, *, on: _dt.date | None = None) -> tuple[str | None, str | None]:
    """Return ``(invalid_at, superseded_by)`` when the page is no longer current.

    A page is invalid when ``invalid_at`` is on or before ``on`` (default
    today), or when it names a ``superseded_by`` successor without a date. A
    future ``invalid_at`` is a scheduled expiry, not yet in effect.
    """
    on = on or today()
    raw_successor = fm.get("superseded_by") or fm.get("superseded-by")
    successor = str(raw_successor).strip() if raw_successor else None
    inv = _iso(fm.get("invalid_at") or fm.get("invalid-at"))
    if inv:
        try:
            if _dt.date.fromisoformat(inv) > on:
                return (None, None)
        except ValueError:
            pass
        return (inv, successor)
    if successor:
        return (on.isoformat(), successor)
    return (None, None)


# Stored when a page names a successor but no date: it has been superseded
# since before any date we could compare against.
ALWAYS_INVALID = "1970-01-01"

# SQL predicate for "this page is current". Evaluated at QUERY time so a
# scheduled expiry (a future ``invalid_at``) takes effect on its date without a
# reindex. ISO dates compare correctly as strings.
LIVE_SQL = "({alias}invalid_at IS NULL OR {alias}invalid_at > date('now', 'localtime'))"


def live_sql(alias: str = "") -> str:
    return LIVE_SQL.format(alias=f"{alias}." if alias else "")


def stored_validity(fm: dict) -> tuple[str | None, str | None]:
    """Validity fields as written to the index: raw date, not judged against today."""
    raw_successor = fm.get("superseded_by") or fm.get("superseded-by")
    successor = str(raw_successor).strip() if raw_successor else None
    inv = _iso(fm.get("invalid_at") or fm.get("invalid-at"))
    if inv:
        return (inv, successor)
    if successor:
        return (ALWAYS_INVALID, successor)
    return (None, None)


def _link_targets(body: str) -> list[str]:
    out = [m.strip() for m in _WIKILINK_RE.findall(body)]
    out += [m.strip() for m in _MDLINK_RE.findall(body)]
    return [t for t in out if t]


def _normalise_target(target: str) -> str:
    t = target.strip().lstrip("./")
    if t.endswith(".md"):
        t = t[:-3]
    return t.lower()


# --------------------------------------------------------------------- log


def append_log(store_path: str | Path, op: str, title: str, lines: Iterable[str] = (),
               *, when: _dt.date | None = None) -> str:
    """Append one entry to ``log.md`` and return the header written.

    The header is validated against :data:`LOG_ENTRY_RE` so a malformed title
    (embedded newline, empty) can never break the parseable shape.
    """
    op = (op or "").strip().lower()
    if op not in LOG_OPS:
        raise ValueError(f"unknown log op {op!r}; expected one of {', '.join(LOG_OPS)}")
    clean_title = " ".join(str(title or "").split())
    if not clean_title:
        raise ValueError("log entry needs a title")
    day = (when or today()).isoformat()
    header = f"## [{day}] {op} | {clean_title}"
    if not LOG_ENTRY_RE.match(header):
        raise ValueError(f"log header failed validation: {header!r}")
    body = [f"- {' '.join(str(line).split())}" for line in lines if str(line).strip()]
    path = Path(store_path) / LOG_FILENAME
    if not path.exists():
        path.write_text(
            "# Cortex log\n\n"
            "Append-only record of ingests, filed answers, lint passes and curation.\n"
            "Each entry starts `## [YYYY-MM-DD] op | title`, so "
            "`grep '^## \\[' log.md | tail` lists the latest.\n",
            encoding="utf-8",
        )
    with path.open("a", encoding="utf-8") as f:
        f.write("\n" + header + "\n")
        if body:
            f.write("\n".join(body) + "\n")
    return header


def read_log(store_path: str | Path, limit: int = 20) -> list[dict]:
    """Return the most recent ``limit`` log entries, newest first."""
    path = Path(store_path) / LOG_FILENAME
    if not path.exists():
        return []
    entries: list[dict] = []
    current: dict | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LOG_ENTRY_RE.match(line)
        if m:
            current = {"date": m.group(1), "op": m.group(2), "title": m.group(3), "lines": []}
            entries.append(current)
        elif current is not None and line.startswith("- "):
            current["lines"].append(line[2:])
    return list(reversed(entries))[: max(0, limit)]


# ------------------------------------------------------------------ catalog


def render_catalog(rows: list[dict], *, generated: _dt.date | None = None) -> str:
    """Render the full page catalog: every live page, grouped by category.

    ``rows`` carry ``rel_path``, ``category``, ``title``, ``summary`` and
    ``content_date``. The catalog is generated, never hand-edited; it replaces
    the drifting hand-maintained index Karpathy's pattern relies on.
    """
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    day = (generated or today()).isoformat()
    out = [
        "# Cortex catalog",
        "",
        f"_Generated {day} from the page index. Do not edit by hand; it is rebuilt by "
        "`cortex(action='lint')`._",
        "",
    ]
    for cat in sorted(by_cat, key=lambda c: (-len(by_cat[c]), c)):
        items = sorted(by_cat[cat], key=lambda r: ((r.get("title") or "").lower(), r["rel_path"]))
        out.append(f"## {cat} ({len(items)})")
        out.append("")
        for r in items:
            title = " ".join(str(r.get("title") or r["rel_path"]).split())
            summary = " ".join(str(r.get("summary") or "").split())
            date = r.get("content_date") or ""
            tail = f" — {summary[:140]}" if summary else ""
            stamp = f" ({date})" if date else ""
            out.append(f"- [{title}]({r['rel_path']}){stamp}{tail}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------- lint


# `[[reply_to_current]]` / `[[reply_to:123]]` / `[[audio_as_voice]]` are chat
# delivery directives that leaked into imported transcripts, not wiki links.
_DIRECTIVE_RE = re.compile(r"^(reply_to(_current)?(:.*)?|audio_as_voice)$", re.I)


def lint(pages: list[dict], *, on: _dt.date | None = None, examples: int = 8,
         disk_paths: Iterable[str] = ()) -> dict:
    """Deterministic structural lint over indexed pages.

    ``pages`` carry ``rel_path``, ``category``, ``title``, ``body``,
    ``content_date`` and ``frontmatter``. Returns counts plus a few examples
    per check. Checks, all mechanical:

    * ``broken_links``   — a ``[[link]]`` or ``(x.md)`` that resolves to no page
    * ``orphans``        — a curated page nothing links to (generated
                           categories excluded)
    * ``missing_pages``  — a link target referenced 3+ times with no page
    * ``slug_collisions``— one filename stem living in several categories, so
                           a bare ``[[stem]]`` is ambiguous
    * ``stale_current``  — a curated page older than 120 days whose text still
                           claims to describe the present
    * ``superseded``     — pages carrying validity frontmatter (informational)
    * ``dangling_successors`` — ``superseded_by`` pointing at no page
    * ``no_frontmatter`` — pages without any frontmatter block
    """
    on = on or today()
    by_rel = {p["rel_path"]: p for p in pages}
    by_stem: dict[str, list[str]] = defaultdict(list)
    by_norm: dict[str, str] = {}
    for rel in by_rel:
        stem = Path(rel).stem.lower()
        by_stem[stem].append(rel)
        by_norm[_normalise_target(rel)] = rel

    # Files that exist but are not indexed (folder index.md pages) are valid
    # link targets; they just never count as pages for orphan purposes.
    on_disk = {_normalise_target(d) for d in disk_paths}
    disk_stems = {Path(d).name for d in on_disk}

    def resolve(target: str, base: str = "") -> list[str]:
        # Markdown links are relative to the linking page first (`sub/b.md`
        # from topics/a.md is topics/sub/b.md); store-root resolution is the
        # fallback because wikilinks and most Cortex links are root-relative.
        if base and not target.startswith("/"):
            rel_norm = posixpath.normpath(posixpath.join(base, target.strip()))
            if not rel_norm.startswith(".."):
                rel_norm = _normalise_target(rel_norm)
                for cand in (rel_norm, rel_norm + "/index"):
                    if cand in by_norm:
                        return [by_norm[cand]]
                    if cand in on_disk:
                        return [cand + ".md"]
        norm = _normalise_target(target)
        if "/" in norm:
            for cand in (norm, norm + "/index"):
                if cand in by_norm:
                    return [by_norm[cand]]
                if cand in on_disk:
                    return [cand + ".md"]
            return []
        hits = by_stem.get(Path(norm).name, [])
        if not hits and Path(norm).name in disk_stems:
            return [norm + ".md"]
        return hits

    inbound: Counter = Counter()
    broken: list[dict] = []
    missing: Counter = Counter()
    for rel, p in by_rel.items():
        body = p.get("body") or ""
        page_dir = posixpath.dirname(rel)
        md_targets = {m.strip() for m in _MDLINK_RE.findall(body)}
        for target in _link_targets(body):
            if _DIRECTIVE_RE.match(target) or "://" in target:
                continue
            hits = resolve(target, page_dir if target in md_targets else "")
            if not hits:
                broken.append({"in": rel, "target": target})
                missing[_normalise_target(target)] += 1
                continue
            for h in hits:
                if h != rel:
                    inbound[h] += 1
        related = (p.get("frontmatter") or {}).get("related") or []
        if isinstance(related, str):
            related = [related]
        for target in related:
            t = str(target).strip().strip("[]")
            for h in resolve(t):
                if h != rel:
                    inbound[h] += 1

    curated = [r for r, p in by_rel.items() if p["category"] not in GENERATED_CATEGORIES]
    orphans = sorted(r for r in curated if inbound.get(r, 0) == 0)
    missing_pages = sorted(
        ({"target": t, "refs": n} for t, n in missing.items() if n >= MISSING_CONCEPT_MIN_REFS),
        key=lambda d: (-d["refs"], d["target"]),
    )
    collisions = sorted(
        ({"stem": s, "pages": sorted(rels)} for s, rels in by_stem.items()
         if len({r.split("/")[0] for r in rels}) > 1),
        key=lambda d: d["stem"],
    )

    stale: list[dict] = []
    superseded: list[dict] = []
    dangling: list[dict] = []
    no_fm: list[str] = []
    cutoff = on - _dt.timedelta(days=STALE_AFTER_DAYS)
    for rel, p in by_rel.items():
        fm = p.get("frontmatter") or {}
        if not fm:
            no_fm.append(rel)
        inv, successor = validity(fm, on=on)
        if inv:
            superseded.append({"page": rel, "invalid_at": inv, "superseded_by": successor})
            if successor and not resolve(successor):
                dangling.append({"page": rel, "superseded_by": successor})
            continue
        if p["category"] in GENERATED_CATEGORIES:
            continue
        cd = p.get("content_date")
        if not cd:
            continue
        try:
            if _dt.date.fromisoformat(cd) > cutoff:
                continue
        except ValueError:
            continue
        m = _CURRENT_WORDS_RE.search(p.get("body") or "")
        if m:
            stale.append({"page": rel, "content_date": cd, "says": m.group(0)})

    def head(items: list) -> list:
        return items[:examples]

    return {
        "pages": len(by_rel),
        "curated_pages": len(curated),
        "broken_links": {"count": len(broken), "examples": head(broken)},
        "orphans": {
            "count": len(orphans),
            "pct_of_curated": round(100 * len(orphans) / max(1, len(curated))),
            "examples": head(orphans),
        },
        "missing_pages": {"count": len(missing_pages), "examples": head(missing_pages)},
        "slug_collisions": {"count": len(collisions), "examples": head(collisions)},
        "stale_current": {"count": len(stale), "examples": head(sorted(stale, key=lambda d: d["content_date"]))},
        "superseded": {"count": len(superseded), "examples": head(superseded)},
        "dangling_successors": {"count": len(dangling), "examples": head(dangling)},
        "no_frontmatter": {"count": len(no_fm), "examples": head(sorted(no_fm))},
    }


# -------------------------------------------------------------- term scan


def scan_terms(pages: list[dict], terms: Iterable[str], *, per_term: int = 8,
               max_terms: int = 20) -> dict:
    """Find every page that mentions each term: the fan-out step of ingest.

    Karpathy's ingest updates *every* page a new source touches, not just one
    new page. The agent extracts the source's entities and concepts; this
    returns, per term, the pages that mention it and the line where they do, so
    the agent can decide page by page whether to update, flag a contradiction,
    or leave it. Matching is case-insensitive and whole-word, so ``Ali`` does
    not match ``also``. Superseded pages are skipped: they are history, and
    editing them would rewrite the past.
    """
    clean: list[str] = []
    for t in terms:
        s = " ".join(str(t).split())
        if len(s) >= 2 and s.lower() not in {c.lower() for c in clean}:
            clean.append(s)
        if len(clean) >= max_terms:
            break
    result: dict[str, list[dict]] = {}
    totals: dict[str, int] = {}
    all_rels: set[str] = set()
    live = [p for p in pages if not validity(p.get("frontmatter") or {})[0]]
    for term in clean:
        rx = re.compile(r"(?<![\w])" + re.escape(term) + r"(?![\w])", re.I)
        hits: list[dict] = []
        for p in live:
            text = p.get("body") or ""
            in_title = bool(rx.search(p.get("title") or ""))
            m = rx.search(text)
            if not (m or in_title):
                continue
            line = ""
            if m:
                start = text.rfind("\n", 0, m.start()) + 1
                end = text.find("\n", m.end())
                line = text[start: end if end != -1 else len(text)].strip()[:200]
            hits.append({
                "rel_path": p["rel_path"],
                "title": p.get("title") or "",
                "in_title": in_title,
                "mentions": len(rx.findall(text)),
                "line": line,
            })
        hits.sort(key=lambda h: (not h["in_title"], -h["mentions"], h["rel_path"]))
        totals[term] = len(hits)
        all_rels.update(h["rel_path"] for h in hits)
        result[term] = hits[:per_term]
    # Truncation is explicit: `total` per term and `pages_touched` count every
    # match, so a caller seeing total > len(matches) knows to re-scan that term
    # with a higher limit instead of assuming the list was complete.
    return {"terms": clean, "matches": result, "totals": totals,
            "truncated": sorted(t for t in clean if totals[t] > len(result[t])),
            "pages_touched": len(all_rels)}
