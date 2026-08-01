#!/usr/bin/env python3
"""Structured review queue for Cortex memory curation.

Replaces the append-only ``review-queue.md`` log (which grew to 70 items, 80%
stale, oldest ~3.5 months, 68 of them automated lint spam) with a deduped,
resolvable queue that has an actual lifecycle.

Design rules:
  * Stable content-hashed IDs -> re-raising the same issue bumps ``last_seen``
    and ``seen_count`` instead of creating a duplicate entry.
  * Severity decides routing:
      - ``needs_human``   escalate to the human's Memory Management channel
      - ``agent``         the weekly LLM curation pass should fix it
      - ``info``          metrics only; NEVER escalated, auto-expires
  * Every item ends: resolved / escalated / expired. Nothing sits forever.
  * Escalations are capped per run so a bad week cannot spam the channel.

Storage: ``<store>/.review-queue.json`` (source of truth) plus a rendered
``review-queue.md`` for humans reading the store directly.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

SEVERITIES = ("needs_human", "agent", "info")
STATUSES = ("open", "resolved", "escalated", "expired")

# Informational items are metrics, not tasks: expire them aggressively.
INFO_TTL_DAYS = 7
# Agent-actionable items get longer, but still finite, life.
AGENT_TTL_DAYS = 30
# needs_human items never auto-expire; a human must decide. They do get
# re-escalated on a cooldown so they are not silently forgotten.
ESCALATION_COOLDOWN_DAYS = 7


def _today() -> str:
    return datetime.date.today().isoformat()


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _days_since(iso_date: str | None) -> int:
    if not iso_date:
        return 0
    try:
        d = datetime.date.fromisoformat(iso_date[:10])
    except ValueError:
        return 0
    return (datetime.date.today() - d).days


class ReviewQueue:
    """Deduped, lifecycle-managed review queue for one Cortex store."""

    def __init__(self, store_path: str | Path):
        self.store = Path(store_path)
        self.json_path = self.store / ".review-queue.json"
        self.md_path = self.store / "review-queue.md"
        self.items: list[dict[str, Any]] = []
        self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        self.load_error: str | None = None
        if self.json_path.exists():
            try:
                data = json.loads(self.json_path.read_text())
                self.items = data.get("items", [])
                return
            except (json.JSONDecodeError, OSError) as exc:
                # A corrupt queue must not be silently replaced: it may hold
                # unresolved human decisions. Quarantine the file so the next
                # save cannot overwrite the only copy, and surface the error.
                self.load_error = str(exc)
                try:
                    quarantine = self.json_path.with_suffix(".json.corrupt")
                    self.json_path.replace(quarantine)
                except OSError:
                    pass
                self.items = []
        self.items = []

    def save(self) -> None:
        self.store.mkdir(parents=True, exist_ok=True)
        payload = {"updated": _now(), "items": self.items}
        tmp = self.json_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.json_path)
        self._render_markdown()

    # -- core API ----------------------------------------------------------

    @staticmethod
    def make_id(kind: str, key: str) -> str:
        """Stable ID from issue kind + a natural key (e.g. the page path)."""
        return hashlib.sha256(("%s::%s" % (kind, key)).encode()).hexdigest()[:12]

    def add(
        self,
        kind: str,
        key: str,
        title: str,
        severity: str = "agent",
        detail: str = "",
        sources: Iterable[str] = (),
        recommendation: str = "",
    ) -> dict[str, Any]:
        """Raise an issue. Idempotent: re-raising bumps last_seen, no duplicate."""
        if severity not in SEVERITIES:
            raise ValueError("bad severity %r" % severity)
        item_id = self.make_id(kind, key)
        for it in self.items:
            if it["id"] == item_id:
                it["last_seen"] = _today()
                it["seen_count"] = it.get("seen_count", 1) + 1
                # Severity may only ratchet UP. A later pass that recognizes an
                # item as needing a human must not leave it at `info`, where it
                # would expire silently without ever being escalated.
                if SEVERITIES.index(severity) < SEVERITIES.index(it["severity"]):
                    it["severity"] = severity
                # A previously expired/resolved issue that recurs re-opens.
                if it["status"] in ("expired", "resolved"):
                    it["status"] = "open"
                    it["reopened"] = _today()
                # Refresh mutable context.
                it["detail"] = detail or it.get("detail", "")
                it["recommendation"] = recommendation or it.get("recommendation", "")
                return it
        item = {
            "id": item_id,
            "kind": kind,
            "key": key,
            "title": title,
            "severity": severity,
            "status": "open",
            "detail": detail,
            "sources": list(sources),
            "recommendation": recommendation,
            "created": _today(),
            "last_seen": _today(),
            "seen_count": 1,
            "escalated_at": None,
            "resolution": None,
        }
        self.items.append(item)
        return item

    def resolve(self, item_id: str, note: str, resolver: str = "curation-pass") -> bool:
        for it in self.items:
            if it["id"] == item_id and it["status"] != "resolved":
                it["status"] = "resolved"
                it["resolution"] = {"note": note, "by": resolver, "at": _now()}
                return True
        return False

    def mark_escalated(self, item_id: str) -> None:
        for it in self.items:
            if it["id"] == item_id:
                it["status"] = "escalated"
                it["escalated_at"] = _today()

    # -- selection ---------------------------------------------------------

    def open_items(self, severity: str | None = None) -> list[dict]:
        out = [i for i in self.items if i["status"] in ("open", "escalated")]
        if severity:
            out = [i for i in out if i["severity"] == severity]
        return out

    def pending_escalations(self, limit: int = 5) -> list[dict]:
        """needs_human items due for escalation (new, or past cooldown)."""
        due = []
        for it in self.items:
            if it["severity"] != "needs_human" or it["status"] not in ("open", "escalated"):
                continue
            if it["status"] == "open" and not it["escalated_at"]:
                due.append(it)
            elif it["escalated_at"] and _days_since(it["escalated_at"]) >= ESCALATION_COOLDOWN_DAYS:
                due.append(it)
        # Oldest first: age is the signal that something is being ignored.
        due.sort(key=lambda i: i["created"])
        return due[:limit]

    def expire_stale(self) -> int:
        """Expire informational/agent items past their TTL. Never needs_human."""
        n = 0
        for it in self.items:
            if it["status"] != "open":
                continue
            ttl = {"info": INFO_TTL_DAYS, "agent": AGENT_TTL_DAYS}.get(it["severity"])
            if ttl and _days_since(it["last_seen"]) > ttl:
                it["status"] = "expired"
                it["resolution"] = {
                    "note": "auto-expired after %d days without recurrence" % ttl,
                    "by": "queue-ttl",
                    "at": _now(),
                }
                n += 1
        return n

    def prune_resolved(self, keep_days: int = 30) -> int:
        """Drop long-closed items so the file cannot grow without bound."""
        before = len(self.items)
        self.items = [
            i for i in self.items
            if i["status"] in ("open", "escalated")
            or _days_since((i.get("resolution") or {}).get("at", i.get("last_seen"))) <= keep_days
        ]
        return before - len(self.items)

    def stats(self) -> dict:
        by_status: dict[str, int] = {}
        by_sev: dict[str, int] = {}
        for it in self.items:
            by_status[it["status"]] = by_status.get(it["status"], 0) + 1
            if it["status"] in ("open", "escalated"):
                by_sev[it["severity"]] = by_sev.get(it["severity"], 0) + 1
        openish = [i for i in self.items if i["status"] in ("open", "escalated")]
        oldest = min((i["created"] for i in openish), default=None)
        return {
            "total": len(self.items),
            "open": len(openish),
            "by_status": by_status,
            "open_by_severity": by_sev,
            "oldest_open": oldest,
            "oldest_open_age_days": _days_since(oldest) if oldest else 0,
        }

    # -- human-readable view ----------------------------------------------

    def _render_markdown(self) -> None:
        s = self.stats()
        lines = [
            "---",
            "title: Review Queue",
            "type: learning",
            "tags: [review-queue, memory-curation]",
            "updated: '%s'" % _today(),
            "---",
            "",
            "# Review Queue",
            "",
            "_Generated view — source of truth is `.review-queue.json`._",
            "_Managed by the weekly memory curation pass. Items are deduped and",
            "have a lifecycle: open -> resolved / escalated / expired._",
            "",
            "**Open: %d** (needs_human=%d, agent=%d, info=%d) · total tracked: %d"
            % (
                s["open"],
                s["open_by_severity"].get("needs_human", 0),
                s["open_by_severity"].get("agent", 0),
                s["open_by_severity"].get("info", 0),
                s["total"],
            ),
            "",
        ]
        for sev, heading in (
            ("needs_human", "## Needs human decision"),
            ("agent", "## Agent-actionable"),
            ("info", "## Informational"),
        ):
            group = [i for i in self.items if i["severity"] == sev and i["status"] in ("open", "escalated")]
            if not group:
                continue
            lines += [heading, ""]
            for it in sorted(group, key=lambda i: i["created"]):
                age = _days_since(it["created"])
                flag = " · escalated %s" % it["escalated_at"] if it["escalated_at"] else ""
                lines.append("### [%s] %s" % (it["id"], it["title"]))
                lines.append("")
                lines.append("- **kind:** %s · **age:** %dd · **seen:** %dx%s"
                             % (it["kind"], age, it.get("seen_count", 1), flag))
                if it.get("detail"):
                    lines.append("- **detail:** %s" % it["detail"])
                for src in it.get("sources", []):
                    lines.append("  - source: `%s`" % src)
                if it.get("recommendation"):
                    lines.append("- **recommended:** %s" % it["recommendation"])
                lines.append("")
        try:
            self.md_path.write_text("\n".join(lines))
        except OSError:
            pass


__all__ = ["ReviewQueue", "SEVERITIES", "STATUSES"]
