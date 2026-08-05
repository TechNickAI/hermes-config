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
import os
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


def accounted_for(source: Path, candidate: Path) -> dict:
    """Every input file must survive, or be explained by a rename/split.

    Structural quality checks all passed on a run that silently deleted 77
    files, because they only ever inspected markdown that was still present.
    This asks the complementary question: did anything simply vanish?

    Non-markdown is never transformed, so it must be preserved byte for byte.
    Markdown may legitimately disappear from a path when a page is de-renamed
    or split, so it counts as accounted for when its content is recoverable
    elsewhere in the candidate.
    """
    def rel_files(root: Path) -> dict:
        out = {}
        for p in root.rglob("*"):
            if (p.is_file() or p.is_symlink()) and not p.name.startswith(".plugin.db"):
                out[str(p.relative_to(root))] = p
        return out

    src, dst = rel_files(source), rel_files(candidate)
    missing_binary, missing_pages, altered_binary = [], [], []

    dst_words = collections.Counter()
    for rel, p in dst.items():
        if rel.endswith(".md"):
            dst_words.update(_prose(p))

    for rel, p in src.items():
        if rel in dst:
            if p.is_symlink():
                if not dst[rel].is_symlink() or os.readlink(p) != os.readlink(dst[rel]):
                    altered_binary.append(rel)
                continue
            if not rel.endswith(".md") and p.read_bytes() != dst[rel].read_bytes():
                altered_binary.append(rel)
            continue
        if not rel.endswith(".md"):
            missing_binary.append(rel)
            continue
        # A moved page is fine as long as its prose still exists somewhere.
        lost = _prose(p) - dst_words
        if lost:
            missing_pages.append({"page": rel, "words_lost": sum(lost.values()),
                                  "sample": [w for w, _ in lost.most_common(5)]})

    # Also compare the whole corpus. Per-missing-page accounting cannot see a
    # transform that truncates a page but leaves it at the same path.
    global_lost = prose_inventory(source) - prose_inventory(candidate)

    return {
        "source_files": len(src),
        "candidate_files": len(dst),
        "missing_non_markdown": missing_binary,
        "altered_non_markdown": altered_binary,
        "pages_with_unrecoverable_prose": missing_pages,
        "substantive_words_lost": sum(global_lost.values()),
        "substantive_words_lost_sample": [w for w, _ in global_lost.most_common(12)],
    }


def _prose(path: Path) -> collections.Counter:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return collections.Counter()
    # Use the same parser as every transform. A body can legitimately begin
    # with horizontal rules; stripping the first ---...--- pair unconditionally
    # would hide real prose from the preservation inventory.
    block, body = split_frontmatter(text)
    if block and isinstance(parse_fm(block), dict):
        text = body
    def substantive(word: str) -> bool:
        if re.fullmatch(r"#{1,6}", word):
            return False
        # Link targets and path-only citations are expected to change when a
        # page is de-renamed or split. Link preservation has its own stronger
        # checks (broken/ambiguous/inbound); counting an old target token as
        # prose loss produces false blocks such as
        # `` `decisions/2026-05-28-old-name.md`, `` -> the new canonical path.
        stripped = word.strip("`.,;:()[]{}<>")
        if stripped.endswith(".md"):
            return False
        if word.lstrip("`.,;:(){}").startswith("[[") or "]]" in word:
            return False
        return True

    return collections.Counter(w for w in text.split() if substantive(w))


def prose_inventory(root: Path) -> collections.Counter:
    """Global substantive prose/code inventory, excluding rewritable targets."""
    total = collections.Counter()
    for p in root.rglob("*.md"):
        if p.is_file() and not p.is_symlink():
            total.update(_prose(p))
    return total


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

    # Copy the ENTIRE store, not just markdown. The transforms only rewrite
    # prose, but the copy is a candidate *store* -- a caller that swaps it in
    # would silently destroy every attachment, artifact and index beside the
    # pages. (This shipped once and deleted 77 files under `artifacts/`.)
    # SKIP still governs what gets TRANSFORMED, never what gets preserved.
    n = 0
    carried = 0
    for p in live.rglob("*"):
        if p.is_dir() and not p.is_symlink():
            continue
        rel = p.relative_to(live)
        # The search index is derived data and is rebuilt from the pages; a
        # stale index copied beside rewritten pages is worse than none.
        if p.name.startswith(".plugin.db"):
            continue
        dest = ws / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if p.is_symlink():
                dest.symlink_to(os.readlink(p))
            else:
                shutil.copy2(p, dest)
        except OSError:
            continue
        if p.suffix == ".md" and not any(d in SKIP for d in p.parts):
            n += 1
        else:
            carried += 1
    print("\n[copied] %d markdown pages to transform, %d other files preserved"
          % (n, carried))

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

    # Preservation is a precondition, not a metric. Compare against the live
    # store: quality checks only inspect pages that are still there, so a
    # deletion is invisible to them.
    accounting = accounted_for(live, ws)
    report["accounting"] = accounting
    lost_files = accounting["missing_non_markdown"]
    altered = accounting["altered_non_markdown"]
    lost_prose = accounting["pages_with_unrecoverable_prose"]
    lost_words = accounting["substantive_words_lost"]
    preserved = not (lost_files or altered or lost_prose or lost_words)
    report["preserved"] = preserved

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

    print("\n  %-24s %6d   ->  %6d"
          % ("files in store", accounting["source_files"], accounting["candidate_files"]))
    if preserved:
        print("  PRESERVATION: every source file accounted for")
    else:
        print("  PRESERVATION FAILED — candidate is NOT safe to apply")
        if lost_files:
            print("    %d non-markdown file(s) missing, e.g. %s"
                  % (len(lost_files), lost_files[:3]))
        if altered:
            print("    %d non-markdown file(s) modified, e.g. %s"
                  % (len(altered), altered[:3]))
        for item in lost_prose[:3]:
            print("    %s lost %d words %s"
                  % (item["page"], item["words_lost"], item["sample"]))
        if lost_words:
            print("    corpus lost %d substantive words, e.g. %s"
                  % (lost_words, accounting["substantive_words_lost_sample"][:8]))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, default=str))
        print("\n[report] %s" % args.json_out)
    print("\n[live store untouched] %s" % live)
    return 0 if preserved else 1


if __name__ == "__main__":
    raise SystemExit(main())
