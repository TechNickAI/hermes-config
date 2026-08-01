#!/usr/bin/env python3
"""Detect files that do not belong in a Cortex knowledge store.

Motivating case: a fleet member's store grew to **8.2GB / 46,699 files** because a
project dumped multiple fully-unzipped copies of a macOS ``.app`` bundle into
a ``projects/<project>/artifacts/`` tree -- ~25k ``.strings`` files,
~7k TIFFs, ~2.7k ``.pyc``, ~900 ``.nib``. A knowledge base should be markdown
measured in megabytes.

A knowledge store should contain prose (``.md``) plus a modest number of
supporting assets. Anything else is either build output, a vendored
application, a downloaded archive, or a scratch artifact -- and it degrades
retrieval, bloats backups, and hides real content.

This reports; it does NOT delete. Cleanup is a human decision, surfaced through
the review queue.

Usage:
    cortex_junk_detector.py --store PATH [--json] [--min-cluster 25]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
from pathlib import Path

# Extensions that legitimately belong in a knowledge store.
KNOWLEDGE_EXT = {".md", ".markdown", ".txt", ".csv", ".json", ".yaml", ".yml"}
# Modest supporting assets: fine in small numbers, suspicious in bulk.
ASSET_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf", ".webp"}

# Extensions that are almost never legitimate knowledge content.
JUNK_EXT = {
    # compiled / build output
    ".pyc", ".pyo", ".o", ".so", ".dylib", ".dll", ".class", ".jar", ".wasm",
    # app bundles / platform resources
    ".nib", ".xib", ".strings", ".plist", ".icns", ".tiff", ".car", ".lproj",
    # archives
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".dmg", ".pkg", ".iso",
    # binaries / media at scale
    ".exe", ".bin", ".app", ".deb", ".rpm", ".mp4", ".mov", ".avi",
    # lockfiles / caches
    ".lock", ".cache", ".log",
}

# Directory names that signal a non-knowledge tree.
JUNK_DIRS = {
    "node_modules", "__pycache__", ".git", "venv", ".venv", "env",
    "dist", "build", "target", "vendor", "artifacts", "unzipped",
    "site-packages", ".pytest_cache", ".mypy_cache", "Contents",
}

# A single directory holding more than this many files is a dumping ground.
DEFAULT_CLUSTER = 25
# Any single file larger than this is suspicious in a prose store.
LARGE_FILE_BYTES = 5 * 1024 * 1024


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f%s" % (n, unit)
        n /= 1024.0
    return "%dB" % n


def scan(store: Path, min_cluster: int = DEFAULT_CLUSTER) -> dict:
    ext_counts: collections.Counter = collections.Counter()
    ext_bytes: collections.Counter = collections.Counter()
    dir_counts: collections.Counter = collections.Counter()
    dir_bytes: collections.Counter = collections.Counter()
    dir_non_md: collections.Counter = collections.Counter()
    large_files: list[tuple[str, int]] = []
    junk_dir_hits: collections.Counter = collections.Counter()

    total_files = 0
    total_bytes = 0
    md_files = 0

    for dirpath, dirnames, filenames in os.walk(store):
        rel_dir = os.path.relpath(dirpath, store)
        parts = set(Path(rel_dir).parts)

        # Record which junk-signalling directory this tree sits under.
        hit = parts & JUNK_DIRS
        for h in hit:
            junk_dir_hits[h] += len(filenames)

        for fn in filenames:
            if fn.startswith("."):
                continue
            p = Path(dirpath) / fn
            try:
                size = p.stat().st_size
            except OSError:
                continue
            ext = p.suffix.lower()
            total_files += 1
            total_bytes += size
            ext_counts[ext] += 1
            ext_bytes[ext] += size
            dir_counts[rel_dir] += 1
            dir_bytes[rel_dir] += size
            if ext in (".md", ".markdown"):
                md_files += 1
            else:
                dir_non_md[rel_dir] += 1
            if size > LARGE_FILE_BYTES:
                large_files.append((str(p.relative_to(store)), size))

    junk_files = sum(n for e, n in ext_counts.items() if e in JUNK_EXT)
    junk_bytes = sum(b for e, b in ext_bytes.items() if e in JUNK_EXT)

    # Directories that are clearly dumping grounds.
    clusters = [
        {"dir": d, "files": n, "bytes": dir_bytes[d], "non_md": dir_non_md[d],
         "human": human(dir_bytes[d])}
        for d, n in dir_counts.most_common(40)
        if n >= min_cluster
    ]

    # Roll clusters up to their shallowest offending ancestor so the report
    # says "artifacts/" once instead of listing 200 subdirectories.
    roots: dict[str, dict] = {}
    for c in clusters:
        parts = Path(c["dir"]).parts
        root = None
        for i, part in enumerate(parts):
            if part in JUNK_DIRS:
                root = str(Path(*parts[: i + 1]))
                break
        root = root or c["dir"]
        agg = roots.setdefault(root, {"dir": root, "files": 0, "bytes": 0, "non_md": 0})
        agg["files"] += c["files"]
        agg["bytes"] += c["bytes"]
        agg["non_md"] += c.get("non_md", 0)
    offenders = sorted(roots.values(), key=lambda r: -r["bytes"])
    for o in offenders:
        o["human"] = human(o["bytes"])
    # A directory full of legitimate markdown pages (e.g. `daily/` with 273
    # notes) is a healthy category, not a dumping ground. Only flag clusters
    # that are substantially non-markdown.
    offenders = [o for o in offenders if o["non_md"] > o["files"] * 0.5]

    findings = []
    if junk_files:
        findings.append({
            "severity": "needs_human",
            "kind": "junk_files",
            "title": "%d non-knowledge files (%s) in the store" % (junk_files, human(junk_bytes)),
            "detail": "Extensions that are never knowledge content: %s"
                      % ", ".join("%s=%d" % (e, n) for e, n in ext_counts.most_common()
                                  if e in JUNK_EXT and n > 5),
        })
    for o in offenders[:5]:
        if o["files"] >= min_cluster * 2:
            findings.append({
                "severity": "needs_human",
                "kind": "dump_directory",
                "title": "`%s` holds %d files (%s) — looks like a dumping ground"
                         % (o["dir"], o["files"], o["human"]),
                "detail": "Knowledge stores should hold prose, not build/vendor trees. "
                          "Consider moving this outside the store and linking to it.",
            })
    if large_files:
        big = sorted(large_files, key=lambda x: -x[1])[:5]
        findings.append({
            "severity": "agent",
            "kind": "large_files",
            "title": "%d files over %s" % (len(large_files), human(LARGE_FILE_BYTES)),
            "detail": "; ".join("%s (%s)" % (f, human(s)) for f, s in big),
        })

    noise_ratio = round(100 * (total_files - md_files) / total_files) if total_files else 0
    return {
        "store": str(store),
        "totals": {
            "files": total_files,
            "bytes": total_bytes,
            "human": human(total_bytes),
            "markdown": md_files,
            "non_markdown": total_files - md_files,
            "noise_pct": noise_ratio,
        },
        "junk": {"files": junk_files, "bytes": junk_bytes, "human": human(junk_bytes)},
        "top_extensions": ext_counts.most_common(12),
        "offending_dirs": offenders[:10],
        "junk_dir_signals": junk_dir_hits.most_common(8),
        "large_files": sorted(large_files, key=lambda x: -x[1])[:10],
        "findings": findings,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-cluster", type=int, default=DEFAULT_CLUSTER)
    args = ap.parse_args()

    store = Path(args.store)
    if not store.exists():
        print("store not found: %s" % store)
        return 2

    r = scan(store, args.min_cluster)
    if args.json:
        print(json.dumps(r, indent=2))
        return 0

    t = r["totals"]
    print("JUNK SCAN — %s" % store)
    print("  files=%d  size=%s  markdown=%d  non-markdown=%d (%d%% noise)"
          % (t["files"], t["human"], t["markdown"], t["non_markdown"], t["noise_pct"]))
    print("  junk-extension files: %d (%s)" % (r["junk"]["files"], r["junk"]["human"]))
    if r["top_extensions"]:
        print("\n  top extensions:")
        for e, n in r["top_extensions"]:
            mark = "  <-- junk" if e in JUNK_EXT else ""
            print("    %-10s %6d%s" % (e or "(none)", n, mark))
    if r["offending_dirs"]:
        print("\n  offending directories:")
        for o in r["offending_dirs"][:6]:
            print("    %6d files  %8s  %s" % (o["files"], o["human"], o["dir"]))
    if r["findings"]:
        print("\n  FINDINGS:")
        for f in r["findings"]:
            print("    [%s] %s" % (f["severity"], f["title"]))
    else:
        print("\n  clean — no junk detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
