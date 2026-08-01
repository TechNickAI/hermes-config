#!/usr/bin/env python3
"""Weekly memory curation pass — the LLM-driven librarian.

This replaces the degraded nightly lint script. The old pass finished in 4.6s
because it stopped thinking: it counted orphans and appended the same summary
to a review queue nobody read. The original librarian (Feb-Apr daily logs) read
dailies, extracted durable facts, reconciled contradictions, and created linked
pages. This restores that, with the operational properties the old one lacked.

What this script does (deterministic scaffolding):
  * builds an INCREMENTAL work set from a checkpoint (resumes after a timeout
    instead of restarting at page 1 -- the original failure mode)
  * gathers structural metrics for the report
  * detects candidate contradictions/duplicates cheaply to focus LLM attention
  * writes the curation BRIEF the calling agent reasons over
  * applies the agent's decisions back to the queue + checkpoint

The LLM reasoning happens in the cron job that runs this: the script prepares
the work, the agent does the thinking, then calls --apply with its decisions.
This split keeps the expensive part focused and the cheap part testable.

Usage:
    cortex_weekly_curation.py --store PATH [--days 7] [--brief]
    cortex_weekly_curation.py --store PATH --apply decisions.json
    cortex_weekly_curation.py --store PATH --status
"""
from __future__ import annotations

import argparse
import collections
import datetime
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_queue import ReviewQueue  # noqa: E402

CHECKPOINT_NAME = ".curation-checkpoint.json"
# Cap the LLM work set so one run cannot balloon; leftovers roll to next run.
MAX_PAGES_PER_RUN = 60
# Similarity above which two pages are flagged as possible duplicates.
DUPE_RATIO = 0.82
EXCLUDE_DIRS = {".git", "_legacy-root", "artifacts", "node_modules", "__pycache__"}
# Files the curation system itself generates. Excluded so the pass never
# curates its own output (which otherwise shows up as a perpetually-dirty page
# and inflates the "never curated" backlog by one every run).
EXCLUDE_FILES = {"review-queue.md"}


def today() -> datetime.date:
    return datetime.date.today()


def iso(d: datetime.date) -> str:
    return d.isoformat()


# ---------------------------------------------------------------- checkpoint


class Checkpoint:
    """Tracks incremental progress so a timeout resumes instead of restarting.

    Resumption works through ``processed`` (rel_path -> mtime): a page is
    re-curated only if it has never been seen or has changed since. There is no
    positional cursor -- an mtime comparison is more robust, because pages can be
    added, deleted or edited between runs.
    """

    def __init__(self, store: Path):
        self.path = store / CHECKPOINT_NAME
        self.data = {"last_run": None, "last_completed_run": None, "processed": {}}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except (json.JSONDecodeError, OSError):
                pass

    @property
    def processed(self) -> dict:
        return self.data.setdefault("processed", {})

    def needs_work(self, rel: str, mtime: float) -> bool:
        """A page needs curation if never seen, or changed since last curated."""
        seen = self.processed.get(rel)
        return seen is None or mtime > float(seen)

    def mark(self, rel: str, mtime: float) -> None:
        self.processed[rel] = mtime

    def prune(self, existing: set[str]) -> int:
        """Drop entries for pages that no longer exist, so the file stays bounded."""
        stale = [rel for rel in self.processed if rel not in existing]
        for rel in stale:
            del self.processed[rel]
        return len(stale)

    def save(self, completed: bool) -> None:
        self.data["last_run"] = datetime.datetime.now().isoformat(timespec="seconds")
        if completed:
            self.data["last_completed_run"] = self.data["last_run"]
        try:
            self.path.write_text(json.dumps(self.data, indent=2))
        except OSError:
            pass


# ------------------------------------------------------------------ scanning


def iter_pages(store: Path):
    for p in sorted(store.rglob("*.md")):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        if p.name.startswith("."):
            continue
        if p.name in EXCLUDE_FILES:
            continue
        yield p


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_raw, body = text[3:end], text[end + 4:]
    fm = {}
    for line in fm_raw.splitlines():
        if ":" in line and not line.startswith((" ", "-")):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("'\"")
    return fm, body


def load_pages(store: Path) -> dict[str, dict]:
    pages = {}
    for p in iter_pages(store):
        try:
            text = p.read_text(errors="replace")
            st = p.stat()
        except OSError:
            continue
        fm, body = parse_frontmatter(text)
        rel = str(p.relative_to(store))
        pages[rel] = {
            "path": p,
            "rel": rel,
            "category": rel.split("/")[0] if "/" in rel else "_root",
            "title": fm.get("title") or p.stem,
            "frontmatter": fm,
            "body": body,
            "size": len(text),
            "mtime": st.st_mtime,
            "links": set(re.findall(r"\[\[([^\]|]+)", text)),
        }
    return pages


def link_graph(pages: dict) -> tuple[collections.Counter, list[str]]:
    """Count inbound wikilinks per page and identify orphans.

    Stems can collide across categories (``topics/foo.md`` and
    ``projects/foo.md``). Mapping stem -> single page would silently drop the
    other's inbound links and inflate the orphan count, so collisions credit
    every candidate.
    """
    stems: dict[str, list[str]] = collections.defaultdict(list)
    for rel in pages:
        stems[Path(rel).stem].append(rel)

    inbound: collections.Counter = collections.Counter()
    for rel, pg in pages.items():
        for target in pg["links"]:
            key = Path(target.strip()).stem
            for candidate in stems.get(key, []):
                if candidate != rel:
                    inbound[candidate] += 1
    orphans = [r for r in pages if inbound.get(r, 0) == 0]
    return inbound, orphans


# ------------------------------------------------------------ cheap detectors


def find_duplicate_candidates(pages: dict, limit: int = 12) -> list[dict]:
    """Cheap near-duplicate detection to focus LLM attention.

    Compares within-category titles only; full pairwise on bodies would be
    O(n^2) on ~900 pages.
    """
    by_cat: dict[str, list] = collections.defaultdict(list)
    for rel, pg in pages.items():
        by_cat[pg["category"]].append((rel, pg["title"]))
    out = []
    for cat, items in by_cat.items():
        if len(items) < 2 or cat == "daily":
            continue  # dailies are legitimately similar by nature
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                ratio = difflib.SequenceMatcher(None, a[1].lower(), b[1].lower()).ratio()
                if ratio >= DUPE_RATIO:
                    out.append({"kind": "duplicate_candidate", "a": a[0], "b": b[0],
                                "similarity": round(ratio, 3)})
                    if len(out) >= limit:
                        return out
    return out


CONTRADICTION_HINTS = (
    (r"\b(no longer|used to|previously|formerly)\b", "temporal-shift"),
    (r"\b(actually|correction|instead|not true|incorrect)\b", "correction"),
    (r"\b(but now|however now|changed to|switched to|moved to)\b", "state-change"),
)


def find_contradiction_candidates(pages: dict, recent_rels: list[str], limit: int = 10) -> list[dict]:
    """Surface pages whose language suggests a superseded/conflicting fact.

    Deliberately high-recall and low-precision: the LLM pass decides what is a
    real contradiction. This just narrows ~900 pages to a readable handful.
    """
    out = []
    for rel in recent_rels:
        pg = pages.get(rel)
        if not pg:
            continue
        for pattern, label in CONTRADICTION_HINTS:
            for m in re.finditer(pattern, pg["body"], re.I):
                start = max(0, m.start() - 140)
                snippet = pg["body"][start:m.end() + 140].replace("\n", " ").strip()
                out.append({"kind": "contradiction_candidate", "page": rel,
                            "signal": label, "snippet": snippet})
                break
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------- brief


def build_brief(store: Path, days: int) -> dict:
    pages = load_pages(store)
    cp = Checkpoint(store)
    inbound, orphans = link_graph(pages)

    cutoff = today() - datetime.timedelta(days=days)
    recent, stale_unprocessed = [], []
    for rel, pg in sorted(pages.items(), key=lambda kv: -kv[1]["mtime"]):
        mdate = datetime.date.fromtimestamp(pg["mtime"])
        if mdate >= cutoff:
            recent.append(rel)
        if cp.needs_work(rel, pg["mtime"]):
            stale_unprocessed.append(rel)

    # Incremental work set: recent first, then never-curated backlog.
    work = [r for r in recent if r in stale_unprocessed]
    for r in stale_unprocessed:
        if r not in work:
            work.append(r)
        if len(work) >= MAX_PAGES_PER_RUN:
            break
    work = work[:MAX_PAGES_PER_RUN]

    cats = collections.Counter(p["category"] for p in pages.values())
    total = len(pages) or 1
    top_cat, top_n = (cats.most_common(1) or [("", 0)])[0]

    dailies = [r for r in recent if pages[r]["category"] == "daily"]

    return {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "store": str(store),
        "window_days": days,
        "totals": {
            "pages": len(pages),
            "recent": len(recent),
            "never_curated": len(stale_unprocessed),
            "work_set": len(work),
            "backlog_after_run": max(0, len(stale_unprocessed) - len(work)),
        },
        "structure": {
            "orphans": len(orphans),
            "orphan_pct": round(100 * len(orphans) / total),
            "oversized": sum(1 for p in pages.values() if p["size"] > 20000),
            "no_frontmatter": sum(1 for p in pages.values() if not p["frontmatter"]),
            "top_category": top_cat,
            "top_category_pct": round(100 * top_n / total),
            "categories": dict(cats.most_common(8)),
        },
        "work_set": work,
        "recent_dailies": dailies[:14],
        "duplicate_candidates": find_duplicate_candidates(pages),
        "contradiction_candidates": find_contradiction_candidates(pages, recent),
        "checkpoint": {
            "last_run": cp.data.get("last_run"),
            "last_completed_run": cp.data.get("last_completed_run"),
            "pages_curated_ever": len(cp.processed),
        },
    }


# --------------------------------------------------------------------- apply


def apply_decisions(store: Path, decisions: dict) -> dict:
    """Apply the agent's curation decisions to the queue + checkpoint.

    decisions = {
      "queue_items":   [{kind,key,title,severity,detail,sources,recommendation}],
      "resolved":      [{"id": ..., "note": ...}],
      "curated_pages": ["daily/2026-08-01.md", ...],
      "completed":     true
    }
    """
    q = ReviewQueue(store)
    cp = Checkpoint(store)
    pages = load_pages(store)

    added = 0
    for spec in decisions.get("queue_items", []):
        q.add(
            kind=spec["kind"],
            key=spec["key"],
            title=spec["title"],
            severity=spec.get("severity", "agent"),
            detail=spec.get("detail", ""),
            sources=spec.get("sources", []),
            recommendation=spec.get("recommendation", ""),
        )
        added += 1

    resolved = 0
    for r in decisions.get("resolved", []):
        if q.resolve(r["id"], r.get("note", "resolved by curation pass")):
            resolved += 1

    for rel in decisions.get("curated_pages", []):
        pg = pages.get(rel)
        if pg:
            cp.mark(rel, pg["mtime"])

    expired = q.expire_stale()
    pruned = q.prune_resolved()
    q.save()
    stale_checkpoint = cp.prune(set(pages))
    cp.save(completed=bool(decisions.get("completed")))

    return {
        "queue_items_added": added,
        "resolved": resolved,
        "expired": expired,
        "pruned": pruned,
        "checkpoint_entries_pruned": stale_checkpoint,
        "pages_marked_curated": len(decisions.get("curated_pages", [])),
        "queue_stats": q.stats(),
    }


# ---------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--brief", action="store_true", help="emit curation brief as JSON")
    ap.add_argument("--apply", help="path to decisions JSON")
    ap.add_argument("--status", action="store_true", help="show queue + checkpoint status")
    args = ap.parse_args()

    store = Path(args.store)
    if not store.exists():
        print("store not found: %s" % store, file=sys.stderr)
        return 2

    if args.status:
        q = ReviewQueue(store)
        cp = Checkpoint(store)
        print(json.dumps({"queue": q.stats(), "checkpoint": {
            "last_run": cp.data.get("last_run"),
            "last_completed_run": cp.data.get("last_completed_run"),
            "pages_curated_ever": len(cp.processed),
        }}, indent=2))
        return 0

    if args.apply:
        decisions = json.loads(Path(args.apply).read_text())
        print(json.dumps(apply_decisions(store, decisions), indent=2))
        return 0

    brief = build_brief(store, args.days)
    if args.brief:
        print(json.dumps(brief, indent=2))
        return 0

    t, s = brief["totals"], brief["structure"]
    print("CURATION BRIEF — %s" % store)
    print("  pages=%d  recent(%dd)=%d  never_curated=%d" % (t["pages"], brief["window_days"], t["recent"], t["never_curated"]))
    print("  work set this run: %d  (backlog after: %d)" % (t["work_set"], t["backlog_after_run"]))
    print("  orphans=%d%%  oversized=%d  no_frontmatter=%d" % (s["orphan_pct"], s["oversized"], s["no_frontmatter"]))
    print("  top category: %s (%d%%)" % (s["top_category"], s["top_category_pct"]))
    print("  duplicate candidates: %d" % len(brief["duplicate_candidates"]))
    print("  contradiction candidates: %d" % len(brief["contradiction_candidates"]))
    print("  last completed run: %s" % brief["checkpoint"]["last_completed_run"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
