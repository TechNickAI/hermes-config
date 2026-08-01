#!/usr/bin/env python3
"""Run the full curation transform suite on a COPY and report before/after.

Answers the question "what is actually different about the memory?" with
concrete counts and sample diffs -- not metrics about problems, but a record of
changes made.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from transforms import (  # noqa: E402
    apply_derename, apply_enrich, apply_links, apply_split, apply_temporal,
    iter_pages, parse_fm, plan_derename, plan_enrich, plan_links, plan_split,
    plan_temporal, rewrite_references, split_frontmatter,
)

SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "artifacts"}


def measure(store: Path) -> dict:
    pages = list(iter_pages(store))
    total = len(pages) or 1
    fm_count = 0
    linked = 0
    oversized = 0
    dated_names = 0
    total_bytes = 0
    inbound: collections.Counter = collections.Counter()
    tag_count = 0

    # Stems collide across categories; credit every candidate so the orphan rate
    # is not overstated. Path-qualified links resolve exactly.
    stems: dict[str, list[Path]] = collections.defaultdict(list)
    by_relpath: dict[str, Path] = {}
    for p in pages:
        stems[p.stem].append(p)
        by_relpath[str(p.relative_to(store))[:-3]] = p

    for p in pages:
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        total_bytes += len(text)
        block, body = split_frontmatter(text)
        fm = parse_fm(block) if block else {}
        if block:
            fm_count += 1
        if fm.get("tags"):
            tag_count += 1
        rel = str(p.relative_to(store))
        top = rel.split("/")[0] if "/" in rel else ""
        if top not in ("daily", "journal") and re.match(r"^\d{4}-\d{2}-\d{2}|^\d{8}T", p.name):
            dated_names += 1
        if len(text) > 20000:
            oversized += 1
        refs = set(re.findall(r"\[\[([^\]|]+)", text))
        if refs:
            linked += 1
        for r in refs:
            t = r.strip()
            if "/" in t:
                exact = by_relpath.get(t)
                if exact and exact != p:
                    inbound[exact] += 1
                continue
            for candidate in stems.get(t, []):
                if candidate != p:
                    inbound[candidate] += 1

    orphans = sum(1 for p in pages if inbound.get(p, 0) == 0)
    return {
        "pages": len(pages),
        "bytes": total_bytes,
        "frontmatter_pct": round(100 * fm_count / total),
        "tagged_pct": round(100 * tag_count / total),
        "linked_pct": round(100 * linked / total),
        "orphan_pct": round(100 * orphans / total),
        "oversized": oversized,
        "date_named_files": dated_names,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--workspace", default="/tmp/memory-curation")
    ap.add_argument("--json-out")
    ap.add_argument("--samples", type=int, default=4)
    args = ap.parse_args()

    live = Path(args.store).resolve()
    ws = (Path(args.workspace) / args.label).resolve()

    # The whole premise is that the live store is never touched, and the next
    # statement is an rmtree. An overlapping --workspace/--label (or an absolute
    # --label, which discards the workspace prefix) would delete the very store
    # being audited.
    if ws == live or ws in live.parents or live in ws.parents:
        print("refusing to run: workspace %s overlaps the live store %s" % (ws, live))
        return 2

    if ws.exists():
        shutil.rmtree(ws)
    ws.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("CURATION TRANSFORM RUN — %s" % args.label)
    print("  live  : %s   (READ ONLY)" % live)
    print("  copy  : %s" % ws)
    print("=" * 72)

    # Copy markdown only: transforms operate on prose, and bulk binaries would
    # dominate the copy time without affecting the result.
    n = 0
    for p in live.rglob("*.md"):
        if any(d in SKIP for d in p.parts):
            continue
        rel = p.relative_to(live)
        dest = ws / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(p, dest)
            n += 1
        except OSError:
            pass
    print("\n[copied] %d markdown pages" % n)

    before = measure(ws)
    report: dict = {"label": args.label, "before": before, "changes": {}, "samples": {}}

    # 1. de-rename ---------------------------------------------------------
    dr = plan_derename(ws)
    report["samples"]["derename"] = [{"from": d["from"], "to": d["to"]} for d in dr[:args.samples]]
    r1 = apply_derename(ws, dr)
    r1b = rewrite_references(ws, dr)
    report["changes"]["derename"] = {**r1, **r1b, "planned": len(dr)}
    print("[1/5 derename ] %d files renamed, %d referencing files updated"
          % (r1["renamed"], r1b["files_updated"]))

    # 2. enrich frontmatter -----------------------------------------------
    en = plan_enrich(ws)
    report["samples"]["enrich"] = en[:args.samples]
    r2 = apply_enrich(ws, en)
    report["changes"]["enrich"] = {**r2, "planned": len(en)}
    print("[2/5 enrich   ] %d pages given complete YAML frontmatter" % r2["enriched"])

    # 3. temporal conflicts ------------------------------------------------
    tp = plan_temporal(ws)
    report["samples"]["temporal"] = tp[:args.samples]
    r3 = apply_temporal(ws, tp)
    report["changes"]["temporal"] = {**r3, "detected": len(tp)}
    print("[3/5 temporal ] %d conflicts found, %d pages annotated with current state"
          % (len(tp), r3["pages_annotated"]))

    # 4. split oversized ---------------------------------------------------
    sp = plan_split(ws)
    report["samples"]["split"] = sp[:args.samples]
    r4 = apply_split(ws, sp)
    report["changes"]["split"] = {**r4, "candidates": len(sp)}
    print("[4/5 split    ] %d oversized pages split into %d files"
          % (r4["pages_split"], r4["files_created"]))

    # 5. link --------------------------------------------------------------
    lk = plan_links(ws)
    report["samples"]["links"] = lk[:args.samples]
    r5 = apply_links(ws, lk)
    report["changes"]["links"] = {**r5, "pages_with_proposals": len(lk)}
    print("[5/5 link     ] %d links added across %d pages"
          % (r5["links_added"], r5["pages_linked"]))

    after = measure(ws)
    report["after"] = after

    print("\n" + "-" * 72)
    print("BEFORE -> AFTER")
    print("-" * 72)
    rows = [
        ("pages", "pages", ""),
        ("frontmatter_pct", "frontmatter coverage", "%"),
        ("tagged_pct", "pages with tags", "%"),
        ("linked_pct", "pages with links", "%"),
        ("orphan_pct", "orphan rate", "%"),
        ("oversized", "oversized pages", ""),
        ("date_named_files", "date-named files", ""),
    ]
    for key, label, unit in rows:
        b, a = before[key], after[key]
        delta = a - b
        arrow = "" if delta == 0 else ("  (%+d)" % delta)
        print("  %-24s %6s%-1s  ->  %6s%-1s%s" % (label, b, unit, a, unit, arrow))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, default=str))
        print("\n[report] %s" % args.json_out)
    print("\n[live store untouched] %s" % live)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
