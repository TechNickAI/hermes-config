#!/usr/bin/env python3
"""Curation transforms: the part that actually changes files.

The audit/triage layer measures problems. This module fixes them. Every
transform is (a) reversible via the pre-run snapshot, (b) reported as a concrete
diff before anything is written, and (c) individually toggleable.

Transforms
----------
``derename``      strip date prefixes from filenames; preserve the date in
                  frontmatter (``date:``/``created:``) so nothing is lost.
                  Filenames should describe content; dates are metadata.
``enrich``        add/repair YAML frontmatter: title, type, created, updated,
                  tags, aliases -- an Obsidian-compatible property block.
``link``          build wiki-style ``[[links]]`` between pages by matching
                  titles/aliases in body prose, reducing the orphan rate.
``split``         break oversized pages into a folder of dated sections with an
                  index page that links to each part.
``temporal``      detect temporal conflicts (same subject, contradictory claims
                  at different dates) and resolve by recency, annotating the
                  superseded claim rather than deleting it.
"""
from __future__ import annotations

import collections
import datetime
import re
from pathlib import Path

DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})[-_]+")
TS_PREFIX = re.compile(r"^(\d{8})T(\d{6})[-_]+")
DATE_ANY = re.compile(r"(\d{4}-\d{2}-\d{2})")

# Pages whose date IS the identity -- journals are legitimately date-named.
DATE_IDENTITY_DIRS = {"daily", "journal"}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "artifacts"}


# ----------------------------------------------------------------- frontmatter


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_block_without_fences, body)."""
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    if end == -1:
        return "", text
    return text[3:end].strip("\n"), text[end + 4:].lstrip("\n")


def parse_fm(block: str) -> dict:
    """Minimal YAML subset parser: scalars and simple lists."""
    fm: dict = {}
    key = None
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith(("  - ", "- ")) and key:
            fm.setdefault(key, [])
            if isinstance(fm[key], list):
                fm[key].append(line.split("- ", 1)[1].strip().strip("'\""))
            continue
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            key = k.strip()
            v = v.strip()
            fm[key] = v.strip("'\"") if v else []
    return fm


def render_fm(fm: dict) -> str:
    """Render frontmatter with a stable key order (Obsidian-friendly)."""
    order = ["title", "type", "subtype", "status", "date", "created", "updated",
             "tags", "aliases", "related", "sources", "confidence"]
    lines = ["---"]
    for k in order:
        if k not in fm:
            continue
        v = fm[k]
        if isinstance(v, list):
            if not v:
                continue
            lines.append("%s:" % k)
            lines.extend("  - %s" % item for item in v)
        else:
            sv = str(v)
            if re.match(r"^\d{4}-\d{2}-\d{2}", sv):
                sv = "'%s'" % sv
            lines.append("%s: %s" % (k, sv))
    for k, v in fm.items():
        if k in order:
            continue
        if isinstance(v, list):
            if not v:
                continue
            lines.append("%s:" % k)
            lines.extend("  - %s" % item for item in v)
        else:
            lines.append("%s: %s" % (k, v))
    lines.append("---")
    return "\n".join(lines)


def titleize(stem: str) -> str:
    s = stem.replace("-", " ").replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else stem


def iter_pages(store: Path):
    for p in sorted(store.rglob("*.md")):
        if any(d in SKIP_DIRS for d in p.parts):
            continue
        if p.name.startswith("."):
            continue
        yield p


# -------------------------------------------------------------------- derename


def plan_derename(store: Path) -> list[dict]:
    """Strip date prefixes from filenames outside journal dirs.

    A filename should say what the page is about. The date belongs in
    frontmatter, where it stays sortable and queryable without polluting every
    link and reference to the page.
    """
    plan = []
    taken: set[str] = {str(p.relative_to(store)) for p in iter_pages(store)}
    for p in iter_pages(store):
        rel = p.relative_to(store)
        top = rel.parts[0] if len(rel.parts) > 1 else ""
        if top in DATE_IDENTITY_DIRS:
            continue

        name = p.name
        date = None
        m = DATE_PREFIX.match(name)
        if m:
            date = m.group(1)
            new_name = DATE_PREFIX.sub("", name)
        else:
            m2 = TS_PREFIX.match(name)
            if not m2:
                continue
            raw = m2.group(1)
            date = "%s-%s-%s" % (raw[:4], raw[4:6], raw[6:8])
            new_name = TS_PREFIX.sub("", name)

        if not new_name or new_name == ".md":
            continue

        candidate = str(rel.parent / new_name) if str(rel.parent) != "." else new_name
        # Avoid collisions: keep a disambiguating suffix if the target exists.
        if candidate in taken:
            stem = new_name[:-3]
            candidate = str(rel.parent / ("%s-%s.md" % (stem, date))) if str(rel.parent) != "." \
                else "%s-%s.md" % (stem, date)
            if candidate in taken:
                continue
        taken.add(candidate)
        plan.append({"from": str(rel), "to": candidate, "date": date})
    return plan


def apply_derename(store: Path, plan: list[dict]) -> dict:
    """Rename files and preserve the extracted date in frontmatter."""
    renamed = 0
    for item in plan:
        src, dst = store / item["from"], store / item["to"]
        if not src.exists() or dst.exists():
            continue
        try:
            text = src.read_text(errors="replace")
        except OSError:
            continue
        block, body = split_frontmatter(text)
        fm = parse_fm(block) if block else {}
        # Preserve the date that was previously encoded in the filename.
        fm.setdefault("date", item["date"])
        fm.setdefault("created", item["date"])
        fm.setdefault("title", titleize(Path(item["to"]).stem))
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(render_fm(fm) + "\n\n" + body.lstrip("\n"))
        src.unlink()
        renamed += 1
    return {"renamed": renamed}


def rewrite_references(store: Path, plan: list[dict]) -> dict:
    """Update links that pointed at the old date-prefixed filenames."""
    mapping = {Path(i["from"]).stem: Path(i["to"]).stem for i in plan}
    mapping_files = {i["from"]: i["to"] for i in plan}
    touched = 0
    for p in iter_pages(store):
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        original = text
        for old, new in mapping.items():
            text = text.replace("[[%s]]" % old, "[[%s]]" % new)
            text = text.replace("[[%s|" % old, "[[%s|" % new)
        for old, new in mapping_files.items():
            text = text.replace("(%s)" % old, "(%s)" % new)
            text = text.replace("`%s`" % old, "`%s`" % new)
        if text != original:
            p.write_text(text)
            touched += 1
    return {"files_updated": touched}


# --------------------------------------------------------------------- enrich


def plan_enrich(store: Path) -> list[dict]:
    """Pages needing frontmatter added or repaired."""
    out = []
    for p in iter_pages(store):
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        block, body = split_frontmatter(text)
        fm = parse_fm(block) if block else {}
        missing = [k for k in ("title", "type", "created", "updated", "tags") if not fm.get(k)]
        if missing:
            out.append({"page": str(p.relative_to(store)), "missing": missing,
                        "has_frontmatter": bool(block)})
    return out


def infer_type(rel: str) -> str:
    top = rel.split("/")[0] if "/" in rel else ""
    return {
        "people": "person", "decisions": "decision", "projects": "project",
        "ventures": "venture", "topics": "topic", "research": "research",
        "daily": "journal", "audit": "audit", "health": "health",
        "synthesis": "synthesis", "learning": "learning",
    }.get(top, "note")


def apply_enrich(store: Path, plan: list[dict]) -> dict:
    fixed = 0
    for item in plan:
        p = store / item["page"]
        if not p.exists():
            continue
        try:
            text = p.read_text(errors="replace")
            st = p.stat()
        except OSError:
            continue
        block, body = split_frontmatter(text)
        fm = parse_fm(block) if block else {}
        rel = item["page"]

        if not fm.get("title"):
            # Prefer an H1 in the body over the filename.
            m = re.search(r"^#\s+(.+)$", body, re.M)
            fm["title"] = m.group(1).strip() if m else titleize(Path(rel).stem)
        if not fm.get("type"):
            fm["type"] = infer_type(rel)
        if not fm.get("created"):
            d = DATE_ANY.search(Path(rel).name)
            fm["created"] = d.group(1) if d else \
                datetime.date.fromtimestamp(st.st_ctime).isoformat()
        if not fm.get("updated"):
            fm["updated"] = datetime.date.fromtimestamp(st.st_mtime).isoformat()
        if not fm.get("tags"):
            top = rel.split("/")[0] if "/" in rel else "note"
            fm["tags"] = [top]
        p.write_text(render_fm(fm) + "\n\n" + body.lstrip("\n"))
        fixed += 1
    return {"enriched": fixed}


# ----------------------------------------------------------------------- link


def build_title_index(store: Path) -> dict[str, str]:
    """Map lowercase title/alias -> page stem, for link matching."""
    idx: dict[str, str] = {}
    for p in iter_pages(store):
        try:
            block, body = split_frontmatter(p.read_text(errors="replace"))
        except OSError:
            continue
        fm = parse_fm(block) if block else {}
        stem = p.stem
        names = [fm.get("title") or titleize(stem)]
        aliases = fm.get("aliases")
        if isinstance(aliases, list):
            names.extend(aliases)
        for n in names:
            n = str(n).strip().lower()
            # Skip very short or overly generic names to avoid false links.
            if len(n) >= 6 and n not in ("untitled", "review queue"):
                idx.setdefault(n, stem)
    return idx


def plan_links(store: Path, max_per_page: int = 5) -> list[dict]:
    """Propose [[wikilinks]] where a page's title appears in another's prose."""
    idx = build_title_index(store)
    proposals = []
    for p in iter_pages(store):
        try:
            block, body = split_frontmatter(p.read_text(errors="replace"))
        except OSError:
            continue
        stem = p.stem
        low = body.lower()
        found = []
        for name, target in idx.items():
            if target == stem or len(found) >= max_per_page:
                continue
            if "[[%s]]" % target in body:
                continue
            # Whole-phrase match only, and not already inside a link.
            if re.search(r"(?<!\[\[)\b%s\b" % re.escape(name), low):
                found.append({"name": name, "target": target})
        if found:
            proposals.append({"page": str(p.relative_to(store)), "links": found})
    return proposals


def apply_links(store: Path, proposals: list[dict]) -> dict:
    """Add proposed links to each page's `related:` frontmatter list.

    Frontmatter is used rather than inline body edits: it is non-destructive to
    prose, Obsidian renders it as properties, and it cannot corrupt sentences.
    """
    linked_pages = 0
    links_added = 0
    for prop in proposals:
        p = store / prop["page"]
        if not p.exists():
            continue
        try:
            block, body = split_frontmatter(p.read_text(errors="replace"))
        except OSError:
            continue
        fm = parse_fm(block) if block else {}
        related = fm.get("related")
        related = list(related) if isinstance(related, list) else ([related] if related else [])
        existing = {str(r).strip() for r in related}
        added = 0
        for link in prop["links"]:
            ref = "[[%s]]" % link["target"]
            if ref not in existing:
                related.append(ref)
                existing.add(ref)
                added += 1
        if added:
            fm["related"] = related
            p.write_text(render_fm(fm) + "\n\n" + body.lstrip("\n"))
            linked_pages += 1
            links_added += added
    return {"pages_linked": linked_pages, "links_added": links_added}


# ---------------------------------------------------------------------- split


def plan_split(store: Path, limit_bytes: int = 40000) -> list[dict]:
    """Oversized pages that should become a folder of sections."""
    out = []
    for p in iter_pages(store):
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        if len(text) <= limit_bytes:
            continue
        block, body = split_frontmatter(text)
        sections = re.findall(r"^##\s+(.+)$", body, re.M)
        if len(sections) < 4:
            continue  # not enough structure to split safely
        out.append({
            "page": str(p.relative_to(store)),
            "bytes": len(text),
            "sections": len(sections),
            "target_dir": str(p.relative_to(store)).replace(".md", "/"),
        })
    return sorted(out, key=lambda x: -x["bytes"])


def apply_split(store: Path, plan: list[dict], max_pages: int = 3) -> dict:
    """Split an oversized page into `<name>/index.md` + one file per section."""
    split_count = 0
    files_created = 0
    for item in plan[:max_pages]:
        p = store / item["page"]
        if not p.exists():
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        block, body = split_frontmatter(text)
        fm = parse_fm(block) if block else {}

        parts = re.split(r"^(##\s+.+)$", body, flags=re.M)
        preamble = parts[0].strip()
        chunks = []
        for i in range(1, len(parts) - 1, 2):
            heading = parts[i].lstrip("# ").strip()
            content = parts[i + 1]
            chunks.append((heading, content))
        if len(chunks) < 4:
            continue

        folder = store / item["page"].replace(".md", "")
        folder.mkdir(parents=True, exist_ok=True)
        index_links = []
        for heading, content in chunks:
            # Strip any leading date from the heading before slugging: dates
            # belong in frontmatter, never in filenames.
            dm = DATE_ANY.search(heading)
            heading_nodate = DATE_ANY.sub("", heading)
            heading_nodate = re.sub(r"^[\s\-\u2014:]+", "", heading_nodate).strip()
            slug_src = heading_nodate or heading
            slug = re.sub(r"[^a-z0-9]+", "-", slug_src.lower()).strip("-")[:60] or "section"
            child_fm = {
                "title": (heading_nodate or heading)[:120],
                "type": fm.get("type", "note"),
                "parent": "[[%s]]" % Path(item["page"]).stem,
                "tags": fm.get("tags") or [],
            }
            if dm:
                # Date preserved as metadata, so chronological sorting still works.
                child_fm["date"] = dm.group(1)
            target = folder / ("%s.md" % slug)
            n = 2
            while target.exists():
                target = folder / ("%s-%d.md" % (slug, n))
                n += 1
            target.write_text(render_fm(child_fm) + "\n\n## " + heading + "\n" + content.rstrip() + "\n")
            index_links.append("- [[%s]]%s" % (target.stem, (" — %s" % dm.group(1)) if dm else ""))
            files_created += 1

        fm["title"] = fm.get("title") or titleize(p.stem)
        fm["type"] = fm.get("type", "index")
        index_body = (preamble + "\n\n" if preamble else "") + \
            "## Sections\n\n" + "\n".join(index_links) + "\n"
        (folder / "index.md").write_text(render_fm(fm) + "\n\n" + index_body)
        files_created += 1
        p.unlink()
        split_count += 1
    return {"pages_split": split_count, "files_created": files_created}


# ------------------------------------------------------------------- temporal


CLAIM_PATTERNS = [
    # Deliberately narrow. An earlier looser version matched prose fragments and
    # produced junk like "state: its" / "state: llama" -- worse than useless,
    # because a bogus "current state" banner is actively misleading.
    (re.compile(r"\b(?:host|server|endpoint|url)\s*(?:is|=|:)\s*"
                r"(https?://[\w./:-]+|[\w.-]+\.[\w.-]+|\d{1,3}(?:\.\d{1,3}){3})", re.I), "endpoint"),
    (re.compile(r"\b(?:lives?|located|based)\s+in\s+([A-Z][\w .,-]{2,38})"), "location"),
    (re.compile(r"\b(?:works?|employed)\s+(?:at|for)\s+([A-Z][\w .,&-]{2,38})"), "employer"),
    (re.compile(r"\b(?:model|provider)\s*(?:is|=|:)\s*([\w./-]{3,50})", re.I), "model"),
    (re.compile(r"\b(?:port)\s*(?:is|=|:)\s*(\d{2,5})\b", re.I), "port"),
]

# A claim value must look like a real identifier, not a stray word.
VALUE_OK = re.compile(r"^[\w][\w./:@ -]{2,}$")
NOISE_VALUES = {"its", "it", "the", "a", "an", "this", "that", "true", "false", "none"}

SUPERSEDE_HINTS = re.compile(
    r"\b(no longer|used to|previously|formerly|as of|superseded|changed to|moved to|"
    r"switched to|deprecated|instead of|correction)\b", re.I)


def plan_temporal(store: Path) -> list[dict]:
    """Find pages carrying multiple dated claims about the same subject.

    Temporal conflicts are the highest-value memory defect: an agent confidently
    citing a fact that a later entry already overturned. Resolution is by
    recency, but the superseded claim is annotated rather than deleted so the
    history stays auditable.
    """
    findings = []
    for p in iter_pages(store):
        try:
            block, body = split_frontmatter(p.read_text(errors="replace"))
        except OSError:
            continue
        # Collect dated sections.
        sections = re.split(r"^(##\s+.+)$", body, flags=re.M)
        dated = []
        for i in range(1, len(sections) - 1, 2):
            heading = sections[i]
            content = sections[i + 1]
            dm = DATE_ANY.search(heading)
            if dm:
                dated.append((dm.group(1), heading.strip(), content))
        if len(dated) < 2:
            continue

        by_claim: dict[str, list] = collections.defaultdict(list)
        for date, heading, content in dated:
            for pattern, label in CLAIM_PATTERNS:
                for m in pattern.finditer(content):
                    val = m.group(1).strip().rstrip(".,;:").strip()[:60]
                    if val.lower() in NOISE_VALUES or not VALUE_OK.match(val):
                        continue
                    by_claim[label].append((date, val, heading))

        conflicts = []
        for label, entries in by_claim.items():
            values = {v for _, v, _ in entries}
            # Require conflicting values on DIFFERENT dates: the same page
            # listing two values on one day is usually enumeration, not drift.
            dates = {d for d, _, _ in entries}
            if len(values) > 1 and len(dates) > 1:
                entries.sort()
                conflicts.append({
                    "claim_type": label,
                    "oldest": {"date": entries[0][0], "value": entries[0][1]},
                    "newest": {"date": entries[-1][0], "value": entries[-1][1]},
                    "distinct_values": len(values),
                })
        has_hint = bool(SUPERSEDE_HINTS.search(body))
        if conflicts:
            findings.append({
                "page": str(p.relative_to(store)),
                "dated_sections": len(dated),
                "conflicts": conflicts,
                "has_supersede_language": has_hint,
                "resolution": "annotate older claims as superseded by the %s entry"
                              % max(d for d, _, _ in dated),
            })
    return findings


def apply_temporal(store: Path, findings: list[dict], max_pages: int = 20) -> dict:
    """Annotate pages that carry unresolved temporal conflicts.

    Adds a `## Current state` block at the top summarizing the most recent
    claim, so a reader (or a retrieval snippet) sees the current fact first
    instead of whichever dated section happens to rank.
    """
    annotated = 0
    for f in findings[:max_pages]:
        p = store / f["page"]
        if not p.exists():
            continue
        try:
            block, body = split_frontmatter(p.read_text(errors="replace"))
        except OSError:
            continue
        if "## Current state" in body:
            continue
        fm = parse_fm(block) if block else {}
        lines = ["## Current state", ""]
        for c in f["conflicts"]:
            lines.append("- **%s:** %s _(as of %s; earlier entry said %s on %s)_"
                         % (c["claim_type"], c["newest"]["value"], c["newest"]["date"],
                            c["oldest"]["value"], c["oldest"]["date"]))
        lines.append("")
        lines.append("_Auto-derived by recency from dated sections below; "
                     "verify before relying on it._")
        fm["has_temporal_conflict"] = "true"
        p.write_text(render_fm(fm) + "\n\n" + "\n".join(lines) + "\n\n" + body.lstrip("\n"))
        annotated += 1
    return {"pages_annotated": annotated}


__all__ = [
    "plan_derename", "apply_derename", "rewrite_references",
    "plan_enrich", "apply_enrich",
    "plan_links", "apply_links",
    "plan_split", "apply_split",
    "plan_temporal", "apply_temporal",
    "split_frontmatter", "parse_fm", "render_fm",
]
