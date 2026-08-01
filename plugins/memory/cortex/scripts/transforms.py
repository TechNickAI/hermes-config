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


def safe_path(store: Path, rel: str) -> Path | None:
    """Resolve ``rel`` inside ``store``, or return None if it escapes.

    Plan entries are data. A malformed or hostile entry containing ``..`` or an
    absolute path would otherwise let a transform rewrite files outside the
    knowledge store. Symlinked components are refused for the same reason.
    """
    if not rel or Path(rel).is_absolute():
        return None
    root = store.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    # Refuse to follow symlinks out of the store.
    probe = root
    for part in Path(rel).parts:
        probe = probe / part
        if probe.is_symlink():
            return None
    return candidate


# ----------------------------------------------------------------- frontmatter


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_block_without_fences, body).

    Delimiters must be exact ``---`` lines. A looser check treats an ordinary
    Markdown horizontal rule as a frontmatter fence, which silently swallows the
    prose between two rules when the page is rewritten.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return "", text
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            block = "".join(lines[1:index]).strip("\n")
            body = "".join(lines[index + 1:]).lstrip("\n")
            # Frontmatter must be a YAML mapping. A document that merely opens
            # with a horizontal rule ("---\n\nprose\n\n---") would otherwise
            # have its prose captured as metadata and dropped on rewrite.
            if not _looks_like_mapping(block):
                return "", text
            return block, body
    # Unterminated fence: treat the whole document as body rather than
    # consuming it as metadata.
    return "", text


def _looks_like_mapping(block: str) -> bool:
    if not block.strip():
        return False
    try:
        import yaml

        return isinstance(yaml.safe_load(block), dict)
    except ImportError:
        first = block.strip().splitlines()[0]
        return ":" in first
    except Exception:
        # Malformed YAML in a real frontmatter block still counts as
        # frontmatter; the parser degrades separately.
        return ":" in block.strip().splitlines()[0]


def parse_fm(block: str) -> dict:
    """Parse a frontmatter block into a dict.

    Uses PyYAML when available (the Cortex store already depends on it) so that
    block scalars, nested maps and lists-of-dicts survive a round trip. The
    hand-rolled fallback below only runs if PyYAML is missing, and is documented
    as lossy for those shapes.
    """
    if not block.strip():
        return {}
    try:
        import yaml

        data = yaml.safe_load(block)
        if isinstance(data, dict):
            return data
        # A non-mapping frontmatter block is malformed; fall through rather than
        # silently returning something the callers cannot use.
    except ImportError:
        pass
    except Exception:
        # Malformed YAML must not take down a whole curation run.
        return _parse_fm_fallback(block)
    return _parse_fm_fallback(block)


def _parse_fm_fallback(block: str) -> dict:
    """Minimal YAML subset parser: scalars and simple lists only.

    Lossy for block scalars (``|``/``>``), nested maps, and lists of dicts.
    Only used when PyYAML is unavailable.
    """
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
    """Render frontmatter with a stable key order (Obsidian-friendly).

    Values that the simple renderer cannot represent losslessly (nested maps,
    lists of dicts, multi-line strings) are delegated to PyYAML so no content is
    dropped on rewrite.
    """
    order = ["title", "type", "subtype", "status", "date", "created", "updated",
             "tags", "aliases", "related", "sources", "confidence"]

    def scalar(value) -> str:
        """Render one scalar, quoting anything YAML would reinterpret."""
        sv = str(value)
        needs_quote = (
            "#" in sv
            or re.match(r"^\d{4}-\d{2}-\d{2}", sv)
            # `[[wikilink]]` is the important case: unquoted, YAML reads it as a
            # nested sequence and the link is destroyed on the next read.
            or sv[:1] in "!&*?|>%@`[]{},"
            or sv.strip() != sv
            or sv == ""
        )
        return "'%s'" % sv.replace("'", "''") if needs_quote else sv

    def emit(key: str, value) -> list[str]:
        # Drop keys with no value rather than emitting a bare `key:` that
        # re-parses as None and then stringifies to "None" on the next pass.
        if value is None or value == [] or value == {}:
            return []
        # Simple scalars and flat string lists keep the readable inline form.
        if isinstance(value, list):
            if all(isinstance(v, (str, int, float)) for v in value):
                return ["%s:" % key] + ["  - %s" % scalar(v) for v in value]
        elif not isinstance(value, (dict, list)) and "\n" not in str(value):
            return ["%s: %s" % (key, scalar(value))]
        # Anything structured or multi-line: let YAML handle it losslessly.
        try:
            import yaml

            dumped = yaml.safe_dump({key: value}, default_flow_style=False,
                                    allow_unicode=True, sort_keys=False)
            return dumped.rstrip("\n").splitlines()
        except ImportError:
            return ["%s: %s" % (key, value)]

    lines = ["---"]
    for k in order:
        if k in fm:
            lines.extend(emit(k, fm[k]))
    for k, v in fm.items():
        if k not in order:
            lines.extend(emit(k, v))
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
    skipped = 0
    for item in plan:
        src = safe_path(store, item["from"])
        dst = safe_path(store, item["to"])
        if src is None or dst is None:
            skipped += 1
            continue
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
    return {"renamed": renamed, "skipped_unsafe": skipped}


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
        p = safe_path(store, item["page"])
        if p is None or not p.exists():
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
        p = safe_path(store, prop["page"])
        if p is None or not p.exists():
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
    """Oversized pages that should become a folder of sections.

    A page with an existing sibling folder (``note.md`` alongside ``note/``) is
    skipped: that is a valid layout, and splitting would overwrite whatever the
    folder already contains.
    """
    out = []
    for p in iter_pages(store):
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        if len(text) <= limit_bytes:
            continue
        target_dir = p.with_suffix("")
        if target_dir.exists():
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
    """Split an oversized page into `<name>/index.md` + one file per section.

    Inbound ``[[stem]]`` links are repointed at the new index so splitting never
    leaves dangling references.
    """
    split_count = 0
    files_created = 0
    split_pages: list[str] = []
    for item in plan[:max_pages]:
        p = safe_path(store, item["page"])
        if p is None or not p.exists():
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
        if folder.exists():
            # Defence in depth: plan_split already skips these, but apply must
            # never clobber an existing folder's index.
            continue
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
                "parent": "[[%s/index]]" % (item["page"][:-3] if item["page"].endswith(".md")
                                            else item["page"]),
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
        split_pages.append(item["page"])

    # Repoint inbound links at the new index pages.
    relinked = 0
    if split_pages:
        remaining = list(iter_pages(store))
        # A bare `[[stem]]` is only safe to rewrite when that stem is unique in
        # the store; otherwise a link meant for a different same-named page
        # would be hijacked to the split index.
        stem_counts: collections.Counter = collections.Counter()
        for page in remaining:
            stem_counts[page.stem] += 1
        for rel in split_pages:
            stem_counts[Path(rel).stem] += 1  # the removed source still counts

        for page in remaining:
            try:
                text = page.read_text(errors="replace")
            except OSError:
                continue
            updated = text
            for rel in split_pages:
                stem = Path(rel).stem
                base = rel[:-3] if rel.endswith(".md") else rel
                # Path-qualified links always resolve unambiguously.
                for target in (base, stem if stem_counts[stem] == 1 else None):
                    if not target:
                        continue
                    updated = re.sub(r"\[\[%s\]\]" % re.escape(target),
                                     "[[%s/index]]" % base, updated)
                    updated = re.sub(r"\[\[%s\|" % re.escape(target),
                                     "[[%s/index|" % base, updated)
            if updated != text:
                page.write_text(updated)
                relinked += 1

    return {"pages_split": split_count, "files_created": files_created,
            "inbound_links_repointed": relinked}


# ------------------------------------------------------------------- temporal


CLAIM_PATTERNS = [
    # Deliberately narrow. An earlier looser version matched prose fragments and
    # produced junk like "state: its" / "state: llama" -- worse than useless,
    # because a bogus "current state" banner is actively misleading.
    # Group 1 is the SUBJECT, group 2 the VALUE: claims are keyed by subject so
    # two different hosts mentioned on the same page are not read as a conflict.
    (re.compile(r"\b(?:the\s+)?(\w[\w -]{0,24}?)\s*(?:host|server|endpoint|url)\s*(?:is|=|:)\s*"
                r"(https?://[\w./:-]+|[\w.-]+\.[\w.-]+|\d{1,3}(?:\.\d{1,3}){3})", re.I), "endpoint"),
    (re.compile(r"\b(?:the\s+)?(\w[\w -]{0,24}?)\s*port\s*(?:is|=|:)\s*(\d{2,5})\b", re.I), "port"),
    (re.compile(r"\b(?:the\s+)?(\w[\w -]{0,24}?)\s*(?:model|provider)\s*(?:is|=|:)\s*"
                r"([\w./-]{3,50})", re.I), "model"),
    # Subject-bearing forms: "<Name> lives in <Place>", "<Name> works at <Org>".
    # Without a real subject every person on a page collapses into one bucket
    # and unrelated facts read as drift.
    (re.compile(r"\b([A-Z][\w.-]+(?:\s+[A-Z][\w.-]+)?)\s+(?:lives?|is\s+based|is\s+located)"
                r"\s+in\s+([A-Z][\w .,-]{2,38})"), "location"),
    (re.compile(r"\b([A-Z][\w.-]+(?:\s+[A-Z][\w.-]+)?)\s+(?:works?|is\s+employed)"
                r"\s+(?:at|for)\s+([A-Z][\w .,&-]{2,38})"), "employer"),
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

        by_claim: dict[tuple[str, str], list] = collections.defaultdict(list)
        for date, heading, content in dated:
            for pattern, label in CLAIM_PATTERNS:
                for m in pattern.finditer(content):
                    subject = (m.group(1) or "").strip().lower()
                    val = m.group(2).strip().rstrip(".,;:").strip()[:60]
                    if val.lower() in NOISE_VALUES or not VALUE_OK.match(val):
                        continue
                    by_claim[(label, subject)].append((date, val, heading))

        conflicts = []
        for (label, subject), entries in by_claim.items():
            values = {v for _, v, _ in entries}
            # Require conflicting values on DIFFERENT dates: the same page
            # listing two values on one day is usually enumeration, not drift.
            dates = {d for d, _, _ in entries}
            if len(values) > 1 and len(dates) > 1:
                entries.sort()
                # An ambiguous newest date (two different values on the same
                # latest date) is not a resolvable conflict -- skip rather than
                # asserting a lexicographically chosen winner.
                newest_date = entries[-1][0]
                newest_values = {v for d, v, _ in entries if d == newest_date}
                if len(newest_values) > 1:
                    continue
                conflicts.append({
                    "claim_type": label,
                    "subject": subject,
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
        p = safe_path(store, f["page"])
        if p is None or not p.exists():
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
            label = ("%s %s" % (c.get("subject", ""), c["claim_type"])).strip()
            lines.append("- **%s:** %s _(as of %s; earlier entry said %s on %s)_"
                         % (label, c["newest"]["value"], c["newest"]["date"],
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
