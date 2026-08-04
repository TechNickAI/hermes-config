#!/usr/bin/env python3
"""Inspect what curation actually did to a store's structure.

Coverage percentages can all improve while the reorganization is nonsense: links
pointing at the wrong page, split sections that lost their context, frontmatter
asserting a type the body contradicts. This tool compares a before/after pair and
reports the *shape* of the change, plus correctness checks that a percentage
cannot express.

Usage:
    inspect_structure.py --before PATH --after PATH [--json] [--samples N]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv"}
LINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def pages(root: Path) -> dict[str, str]:
    out = {}
    for p in root.rglob("*.md"):
        if any(part in SKIP for part in p.parts):
            continue
        try:
            out[str(p.relative_to(root))] = p.read_text(errors="replace")
        except OSError:
            continue
    return out


def body_of(text: str) -> str:
    if not text.startswith("---"):
        return text
    lines = text.splitlines(keepends=True)
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            return "".join(lines[i + 1:])
    return text


def norm_words(text: str) -> collections.Counter:
    """Body prose only.

    Markdown structure markers are excluded: promoting a ``###`` subsection to
    its own page legitimately consumes the marker, and counting it as lost
    content produces false alarms that mask real loss.
    """
    return collections.Counter(
        w for w in body_of(text).split() if not re.fullmatch(r"#{1,6}", w))


# ----------------------------------------------------------------- link health


def link_health(store: dict[str, str]) -> dict:
    by_rel = {k[:-3] if k.endswith(".md") else k: k for k in store}
    stems = collections.defaultdict(list)
    for rel in store:
        stems[Path(rel).stem].append(rel)

    total = broken = ambiguous = self_links = 0
    broken_examples, ambiguous_examples = [], []
    for rel, text in store.items():
        for target in LINK.findall(body_of(text)):
            t = target.strip()
            total += 1
            if "/" in t:
                hit = by_rel.get(t)
                if hit is None:
                    broken += 1
                    if len(broken_examples) < 8:
                        broken_examples.append({"in": rel, "target": t})
                elif hit == rel:
                    self_links += 1
            else:
                candidates = stems.get(t, [])
                if not candidates:
                    broken += 1
                    if len(broken_examples) < 8:
                        broken_examples.append({"in": rel, "target": t})
                elif len(candidates) > 1:
                    ambiguous += 1
                    if len(ambiguous_examples) < 8:
                        ambiguous_examples.append(
                            {"in": rel, "target": t, "resolves_to": candidates})
                elif candidates[0] == rel:
                    self_links += 1
    return {
        "links_total": total,
        "broken": broken,
        "broken_pct": round(100.0 * broken / max(total, 1), 2),
        "ambiguous": ambiguous,
        "self_links": self_links,
        "broken_examples": broken_examples,
        "ambiguous_examples": ambiguous_examples,
    }


# ------------------------------------------------------------------- hierarchy


def hierarchy(store: dict[str, str]) -> dict:
    depth = collections.Counter()
    per_dir = collections.Counter()
    for rel in store:
        parts = Path(rel).parts
        depth[len(parts) - 1] += 1
        per_dir[str(Path(rel).parent)] += 1
    sizes = sorted((len(body_of(t)), rel) for rel, t in store.items())
    return {
        "depth_histogram": dict(sorted(depth.items())),
        "max_depth": max(depth) if depth else 0,
        "directories": len(per_dir),
        "largest_dirs": per_dir.most_common(8),
        "singleton_dirs": sum(1 for n in per_dir.values() if n == 1),
        "largest_pages": [{"page": r, "body_bytes": s} for s, r in sizes[-5:][::-1]],
        "tiny_pages": sum(1 for s, _ in sizes if s < 200),
    }


# ---------------------------------------------------------------------- splits


def split_analysis(before: dict[str, str], after: dict[str, str]) -> dict:
    """Verify every split preserved its source content and is reachable."""
    results = []
    for rel in before:
        if rel in after:
            continue
        folder = rel[:-3] if rel.endswith(".md") else rel
        index = folder + "/index.md"
        if index not in after:
            continue
        children = [k for k in after if k.startswith(folder + "/") and k != index]
        src = norm_words(before[rel])
        dst = collections.Counter()
        for k in [index] + children:
            dst.update(norm_words(after[k]))
        missing = src - dst
        # The index adds a heading/link scaffold, so only check for LOSS.
        inbound = sum(1 for t in after.values()
                      if ("[[%s/index]]" % folder) in t or ("[[%s/index|" % folder) in t)
        results.append({
            "source": rel,
            "children": len(children),
            "source_words": sum(src.values()),
            "words_lost": sum(missing.values()),
            "lost_sample": [w for w, _ in missing.most_common(5)],
            "index_inbound_links": inbound,
            "orphaned_index": inbound == 0,
        })
    return {
        "splits": len(results),
        "with_loss": sum(1 for r in results if r["words_lost"] > 0),
        "orphaned_indexes": sum(1 for r in results if r["orphaned_index"]),
        "detail": results,
    }


# ------------------------------------------------------------------- renames


def rename_analysis(before: dict[str, str], after: dict[str, str]) -> dict:
    """A rename must preserve the date in frontmatter and keep links resolvable."""
    gone = set(before) - set(after)
    added = set(after) - set(before)
    date_re = re.compile(r"^(?:.*/)?(\d{4}-\d{2}-\d{2})-(.+)\.md$")
    renames, lost_date, unresolved = [], [], []
    for rel in sorted(gone):
        m = date_re.match(rel)
        if not m:
            continue
        date, slug = m.groups()
        parent = str(Path(rel).parent)

        def join(name: str) -> str:
            return ("%s/%s" % (parent, name)) if parent != "." else name

        # A slug collision is resolved by re-appending the date as a suffix, so
        # both the bare slug and the suffixed form are legitimate targets.
        candidates = [join("%s.md" % slug), join("%s-%s.md" % (slug, date))]
        target = next((c for c in candidates if c in added), None)
        if target is None:
            unresolved.append(rel)
            continue
        head = after[target][:400]
        keeps_date = date in head
        renames.append({"from": rel, "to": target, "date_in_frontmatter": keeps_date})
        if not keeps_date:
            lost_date.append(target)
    return {
        "renames_detected": len(renames),
        "date_preserved": sum(1 for r in renames if r["date_in_frontmatter"]),
        "date_lost": lost_date,
        "unresolved": unresolved,
        "sample": renames[:8],
    }


# ------------------------------------------------------------------ frontmatter


def frontmatter_audit(store: dict[str, str]) -> dict:
    try:
        import yaml
    except ImportError:
        return {"skipped": "PyYAML unavailable"}

    invalid, types, missing_title = [], collections.Counter(), 0
    invalid_total = 0
    for rel, text in store.items():
        if not text.startswith("---"):
            continue
        lines = text.splitlines()
        end = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
        if end is None:
            continue
        try:
            data = yaml.safe_load("\n".join(lines[1:end]))
        except Exception as exc:
            # Count every failure; keep only a few examples. Reporting the
            # example count as the total would have understated the 142-page
            # rollout failure this audit exists to catch.
            invalid_total += 1
            if len(invalid) < 8:
                invalid.append({"page": rel, "error": str(exc).splitlines()[0]})
            continue
        if isinstance(data, dict):
            types[data.get("type", "<none>")] += 1
            if not data.get("title"):
                missing_title += 1
    return {
        "invalid_yaml": invalid_total,
        "invalid_examples": invalid,
        "missing_title": missing_title,
        "type_distribution": types.most_common(12),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--json")
    args = ap.parse_args()

    before = pages(Path(args.before))
    after = pages(Path(args.after))

    bw = sum(sum(norm_words(t).values()) for t in before.values())
    aw = sum(sum(norm_words(t).values()) for t in after.values())

    report = {
        "pages": {"before": len(before), "after": len(after)},
        "body_words": {"before": bw, "after": aw,
                       "delta_pct": round(100.0 * (aw - bw) / max(bw, 1), 3)},
        "link_health": {"before": link_health(before), "after": link_health(after)},
        "hierarchy": {"before": hierarchy(before), "after": hierarchy(after)},
        "splits": split_analysis(before, after),
        "renames": rename_analysis(before, after),
        "frontmatter": {"before": frontmatter_audit(before),
                        "after": frontmatter_audit(after)},
    }

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2))

    lb, la = report["link_health"]["before"], report["link_health"]["after"]
    print("=" * 72)
    print("STRUCTURE INSPECTION")
    print("=" * 72)
    print("pages            %6d -> %6d" % (len(before), len(after)))
    print("body words       %6d -> %6d  (%+.3f%%)"
          % (bw, aw, report["body_words"]["delta_pct"]))
    print()
    print("LINK HEALTH                     before     after")
    print("  total links               %10d %9d" % (lb["links_total"], la["links_total"]))
    print("  broken                    %10d %9d" % (lb["broken"], la["broken"]))
    print("  broken %%                  %10.2f %9.2f" % (lb["broken_pct"], la["broken_pct"]))
    print("  ambiguous (stem clash)    %10d %9d" % (lb["ambiguous"], la["ambiguous"]))
    print("  self-links                %10d %9d" % (lb["self_links"], la["self_links"]))
    for ex in la["broken_examples"][:5]:
        print("    broken: %s -> [[%s]]" % (ex["in"], ex["target"]))
    for ex in la["ambiguous_examples"][:3]:
        print("    ambiguous: %s -> [[%s]] hits %d pages"
              % (ex["in"], ex["target"], len(ex["resolves_to"])))
    print()
    hb, ha = report["hierarchy"]["before"], report["hierarchy"]["after"]
    print("HIERARCHY")
    print("  max depth        %d -> %d" % (hb["max_depth"], ha["max_depth"]))
    print("  directories      %d -> %d" % (hb["directories"], ha["directories"]))
    print("  singleton dirs   %d -> %d" % (hb["singleton_dirs"], ha["singleton_dirs"]))
    print("  tiny pages(<200b) %d -> %d" % (hb["tiny_pages"], ha["tiny_pages"]))
    print("  largest dirs after: %s" % ha["largest_dirs"][:5])
    print()
    sp = report["splits"]
    print("SPLITS")
    print("  pages split           %d" % sp["splits"])
    print("  with content loss     %d" % sp["with_loss"])
    print("  orphaned indexes      %d" % sp["orphaned_indexes"])
    for d in sp["detail"][:6]:
        print("    %-42s -> %2d children, lost %d words, inbound %d"
              % (d["source"], d["children"], d["words_lost"], d["index_inbound_links"]))
    print()
    rn = report["renames"]
    print("RENAMES")
    print("  detected              %d" % rn["renames_detected"])
    print("  date kept in frontmatter %d" % rn["date_preserved"])
    if rn["date_lost"]:
        print("  DATE LOST: %s" % rn["date_lost"][:5])
    print()
    fb, fa = report["frontmatter"]["before"], report["frontmatter"]["after"]
    print("FRONTMATTER")
    print("  invalid YAML     %s -> %s" % (fb.get("invalid_yaml"), fa.get("invalid_yaml")))
    print("  missing title    %s -> %s" % (fb.get("missing_title"), fa.get("missing_title")))
    print("  types after: %s" % fa.get("type_distribution", [])[:8])
    for ex in fa.get("invalid_examples", [])[:5]:
        print("    invalid: %s (%s)" % (ex["page"], ex["error"]))
    print()

    problems = (la["broken"] > lb["broken"], sp["with_loss"], sp["orphaned_indexes"],
                bool(rn["date_lost"]), fa.get("invalid_yaml", 0))
    print("VERDICT: %s" % ("PROBLEMS FOUND" if any(problems) else "structurally clean"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
