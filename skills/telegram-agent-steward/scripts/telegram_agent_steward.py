#!/usr/bin/env python3
"""telegram-agent-steward — keeps an agent's own Telegram rooms readable.

Reads history via the owner's telethon session (bots cannot read history).
Acts via the agent's OWN bot token (bots may only delete their own messages,
and only within 48h).

DESIGN INVERSION (from multi-review): repetition is treated as a SEVERITY
SIGNAL, not as noise. A message repeated many times over many hours is how an
ignored alarm looks. Collapsing such a cluster silently is the failure mode
this tool exists to prevent, so clusters escalate before they collapse.

Safety model:
  - DELETE-ELIGIBLE requires positive proof of no information: an exact match
    against known information-free templates. Unrecognized output is HELD.
  - ARCHIVE BEFORE DELETE, fsynced and content-verified, media included.
  - Escalation state persists OUTSIDE the 47h scan window, so an alarm keeps
    escalating long after it stops being delete-eligible.
  - DRY-RUN by default; --apply required to mutate.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import hashlib
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------- constants

# UI ephemera: no information content by construction. Matched as a WHOLE
# MESSAGE shape, not just a first character, so body content still matters.
EPHEMERAL_PATTERNS = [
    re.compile(r"^💻\s*terminal\b", re.S),
    re.compile(r"^🐍\s*Running code\b", re.S),
    # Variation selector U+FE0F may or may not follow the emoji, and the
    # gateway truncates these with a trailing ellipsis. Both forms are UI.
    re.compile("^(?:\U0001f527|\u270f|\u270d|\U0001f4dd|\U0001f4d6|\U0001f50d|"
               "\U0001f310|\U0001f5c2|\u2699)\ufe0f?\\s*"
               r"(?:Editing|Writing|Patching|Reading|Searching|Fetching|Browsing)\b", re.S),
    re.compile(r"^(?:🔍|📖|🌐|🗂|⚙)\ufe0f?\s+\S", re.S),
    re.compile(r"^⏳\s*(?:Working|Queued)\b", re.S),
    re.compile(r"^⏩\s*Steered\b", re.S),
    re.compile(r"^💾\s*Self-improvement review:", re.S),
]

# Reason codes meaning money, execution, or monitoring blindness.
# Anchored to MACHINE-EMITTED alarm shapes, not prose: an earlier version
# matched any message merely CONTAINING "halt"/"escalate" and flagged 385
# ordinary conversational messages in a live room as critical.
NEVER_TOUCH = re.compile(
    r"(?:^|\n)\s*(?:⚠️|🔴|❌)?\s*"
    r"(?:SEV-[012]\b|[A-Z][A-Z ]*HALTED|[A-Z][A-Z ]*BROKEN|MONITOR BLIND|"
    r"HALT \(|CRITICAL:|FATAL:)"
    r"|\bSEV-[012]\b"
    r"|\b(?:MARGIN CALL|LIQUIDATION|EXPOSURE BREACH|DRAWDOWN LIMIT)\b"
    r"|(?:^|\n)\s*(?:⚠️\s*)?Cron '[^']+' failed"
    r"|\b(?:401|403)\s+(?:Unauthorized|Forbidden)\b"
    r"|\b(?:order|exchange)\s+reject(?:ed|ion)\b"
    r"|\bstale market data\b|\bclock skew\b|\bdisk (?:full|capacity)\b",
    re.IGNORECASE | re.MULTILINE,
)

# Bot API accepts exactly ONE reaction per message, from a fixed set.
# Verified live 2026-08-21: ✅ and 👀 are REJECTED (REACTION_INVALID).
REACT_UNACKED = "🔥"  # unacknowledged and stale
REACT_ACKED = "👍"  # owner replied
REACT_ROLLUP = "💯"  # survivor of a collapsed cluster

TG_API = "https://api.telegram.org/bot{token}/{method}"

# A cluster this large/old is escalated, never collapsed — the measured case
# (54 copies over 95h, never acknowledged) must trip this.
ESCALATE_COUNT = 5
ESCALATE_HOURS = 6


def canonical(text: str) -> str:
    """Exact canonical form for clustering. Whitespace-normalized ONLY.

    Deliberately does NOT normalize digits: an earlier version mapped every
    number to 'N', which collides distinct order IDs, prices, and symbols.
    Duplicate deletion now requires genuinely identical content.
    """
    return re.sub(r"[ \t]+", " ", (text or "").strip())


def is_ephemeral(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and any(p.match(t) for p in EPHEMERAL_PATTERNS)


# ---------------------------------------------------------------- state


class State:
    """Escalation + cursor state that OUTLIVES the 47h scan window.

    Deletion expires at 48h but escalation must not: the real incident ran
    95 hours. Without this, the tool would stop escalating at hour 47.

    Also stores a per-TOPIC cursor so each sweep only walks messages newer
    than the last one it processed, instead of rescanning every topic on
    every run.
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS alarm(
                 chat INTEGER, sig TEXT, first_seen TEXT, last_seen TEXT,
                 count INTEGER, acked INTEGER DEFAULT 0, escalated INTEGER DEFAULT 0,
                 sample TEXT, PRIMARY KEY(chat, sig))"""
        )
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS cursor(
                 chat INTEGER, topic INTEGER, last_id INTEGER,
                 last_run TEXT, PRIMARY KEY(chat, topic))"""
        )
        self.db.commit()

    # -- per-topic cursor ------------------------------------------------
    def cursor_for(self, chat: int, topic: int) -> int:
        row = self.db.execute(
            "SELECT last_id FROM cursor WHERE chat=? AND topic=?", (chat, topic)
        ).fetchone()
        return row[0] if row else 0

    def set_cursor(self, chat: int, topic: int, last_id: int, when: str):
        self.db.execute(
            "INSERT INTO cursor(chat,topic,last_id,last_run) VALUES(?,?,?,?) "
            "ON CONFLICT(chat,topic) DO UPDATE SET last_id=max(last_id,excluded.last_id), "
            "last_run=excluded.last_run",
            (chat, topic, last_id, when),
        )
        self.db.commit()

    def observe(self, chat: int, sig: str, when: str, sample: str, n: int = 1):
        """Record n NEW sightings, ACCUMULATING across runs.

        Counts must accumulate, not take a max of per-batch sizes: with an
        incremental cursor each sweep normally sees a single new copy, so
        max() would leave the count pinned at 1 forever and repetition-based
        escalation could never fire.
        """
        row = self.db.execute(
            "SELECT count, first_seen FROM alarm WHERE chat=? AND sig=?", (chat, sig)
        ).fetchone()
        if row:
            self.db.execute(
                "UPDATE alarm SET count=count+?, last_seen=? WHERE chat=? AND sig=?",
                (n, when, chat, sig),
            )
        else:
            self.db.execute(
                "INSERT INTO alarm(chat,sig,first_seen,last_seen,count,sample) VALUES(?,?,?,?,?,?)",
                (chat, sig, when, when, n, sample[:400]),
            )
        self.db.commit()

    def ack(self, chat: int, sig: str):
        self.db.execute("UPDATE alarm SET acked=1 WHERE chat=? AND sig=?", (chat, sig))
        self.db.commit()

    def get(self, chat: int, sig: str):
        cur = self.db.execute(
            "SELECT first_seen,last_seen,count,acked,escalated FROM alarm WHERE chat=? AND sig=?",
            (chat, sig),
        )
        return cur.fetchone()

    def open_alarms(self, chat: int):
        return self.db.execute(
            "SELECT sig,first_seen,last_seen,count,sample FROM alarm "
            "WHERE chat=? AND acked=0 ORDER BY count DESC",
            (chat,),
        ).fetchall()


# ---------------------------------------------------------------- bot side


class ForumLookupError(RuntimeError):
    """Topic enumeration failed; we cannot prove what the owner has read."""


class Bot:
    def __init__(self, token: str, dry_run: bool = True):
        self.token = token
        self.dry_run = dry_run
        self.failures: list[str] = []

    def _call(self, method: str, **kw):
        for k, v in list(kw.items()):
            if isinstance(v, (list, dict)):
                kw[k] = json.dumps(v)
        data = urllib.parse.urlencode(kw).encode()
        url = TG_API.format(token=self.token, method=method)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, data=data, timeout=25) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                try:
                    body = json.load(e)
                except Exception:
                    body = {"ok": False, "description": str(e)}
                # Honour Telegram flood control instead of silently dropping.
                if e.code == 429:
                    wait = int(body.get("parameters", {}).get("retry_after", 2))
                    time.sleep(min(wait, 30))
                    continue
                self.failures.append(f"{method}: {body.get('description')}")
                return body
            except Exception as e:  # transient network
                if attempt == 3:
                    self.failures.append(f"{method}: {e}")
                    return {"ok": False, "description": str(e)}
                time.sleep(1 + attempt)
        return {"ok": False, "description": "retries exhausted"}

    def delete(self, chat_id: int, message_id: int):
        if self.dry_run:
            return {"ok": True, "dry_run": True}
        return self._call("deleteMessage", chat_id=chat_id, message_id=message_id)

    def react(self, chat_id: int, message_id: int, emoji: str):
        if self.dry_run:
            return {"ok": True, "dry_run": True}
        return self._call(
            "setMessageReaction",
            chat_id=chat_id,
            message_id=message_id,
            reaction=[{"type": "emoji", "emoji": emoji}],
        )

    def send(self, chat_id: int, text: str, thread_id=None, silent=True):
        if self.dry_run:
            return {"ok": True, "dry_run": True, "result": {"message_id": 0}}
        kw = dict(chat_id=chat_id, text=text, disable_notification=silent, parse_mode="HTML")
        if thread_id:
            kw["message_thread_id"] = thread_id
        return self._call("sendMessage", **kw)

    def edit(self, chat_id: int, message_id: int, text: str):
        if self.dry_run:
            return {"ok": True, "dry_run": True}
        return self._call(
            "editMessageText", chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML"
        )

    def pin(self, chat_id: int, message_id: int):
        if self.dry_run:
            return {"ok": True, "dry_run": True}
        return self._call(
            "pinChatMessage", chat_id=chat_id, message_id=message_id, disable_notification=True
        )


# ---------------------------------------------------------------- archive


def archive_and_verify(path: Path, records: list[dict]) -> bool:
    """Durably persist records, then PROVE they are readable and complete.

    Returns True only if every record round-trips. Callers must refuse to
    delete when this returns False — readability of the file is not enough,
    each line must parse and the count must match.
    """
    if not records:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    before = 0
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            before = sum(1 for _ in f)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    # Verify: every line parses, and we gained exactly len(records) lines.
    ids = set()
    with path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) - before != len(records):
        return False
    for line in lines[before:]:
        try:
            ids.add(json.loads(line)["id"])
        except Exception:
            return False
    return ids == {r["id"] for r in records}


# ---------------------------------------------------------------- sweep


async def list_topics(client, ent):
    """Enumerate forum topics with the owner's per-topic READ WATERMARK.

    read_inbox_max_id is the highest message id the owner has actually read
    in that topic — Telegram tracks this per topic, per device-synced. It is
    the only trustworthy 'has the owner seen this' signal: presence (UserStatus)
    is useless here because the reading session IS the owner's account and
    therefore always reports Online.
    """
    from telethon.tl import functions, types

    out = []
    try:
        r = await client(
            functions.messages.GetForumTopicsRequest(
                peer=ent, offset_date=None, offset_id=0, offset_topic=0, limit=100
            )
        )
    except Exception as e:
        # Distinguish LOOKUP FAILURE from 'this is not a forum'. Returning []
        # for both would fabricate read_max=0/unread=0 and let a transient API
        # error delete messages the owner never saw.
        raise ForumLookupError(str(e)) from e
    for t in r.topics:
        if isinstance(t, types.ForumTopic):
            out.append(t)
    return out


async def chat_read_state(client, ent):
    """Chat-level (read_max, unread) for a NON-forum group.

    Never guess zero: zero means 'nothing read', and treating it as 'all read'
    is the unsafe direction.
    """
    try:
        d = await client.get_dialogs(limit=200)
        for dlg in d:
            if dlg.entity and getattr(dlg.entity, "id", None) == getattr(ent, "id", None):
                return (dlg.dialog.read_inbox_max_id or 0), (dlg.unread_count or 0)
    except Exception:
        pass
    return None, None


async def sweep(cfg, apply: bool) -> int:
    from telethon import TelegramClient

    tg_cfg = json.load(open(os.path.expanduser(cfg["telethon_config"])))
    client = TelegramClient(
        os.path.expanduser(cfg["telethon_session"]), tg_cfg["app_id"], tg_cfg["app_hash"]
    )
    await client.connect()
    if not await client.is_user_authorized():
        print("FATAL: telethon session not authorized", file=sys.stderr)
        return 2

    bot = Bot(cfg["bot_token"], dry_run=not apply)
    owner_id = int(cfg["owner_id"])
    bot_id = int(cfg["bot_token"].split(":")[0])
    now = dt.datetime.now(dt.timezone.utc)
    horizon = now - dt.timedelta(hours=47)  # bots cannot delete past 48h
    root = Path(os.path.expanduser(cfg["archive_dir"]))
    state = State(root.parent / "steward-state.db")

    report = {
        "deleted": 0,
        "collapsed": 0,
        "held": 0,
        "critical": 0,
        "topics_walked": 0,
        "topics_skipped_unchanged": 0,
        "topics_skipped_unread": 0,
        "escalated": [],
        "archive_failures": 0,
        "api_failures": 0,
    }

    for chat_id in cfg["chats"]:
        try:
            ent = await client.get_entity(chat_id)
            try:
                topics = await list_topics(client, ent)
            except ForumLookupError as e:
                # FAIL CLOSED: without topic state we cannot prove what the
                # owner has read, so we touch nothing in this chat.
                report.setdefault("chat_errors", []).append(
                    f"{chat_id}: topic lookup failed, skipped for safety: {e}"
                )
                continue
            walk = [(t.id, t.top_message, t.read_inbox_max_id, t.unread_count) for t in topics]
            if not walk:
                # Plain (non-forum) group: get REAL chat-level read state.
                read_max, unread = await chat_read_state(client, ent)
                if read_max is None:
                    report.setdefault("chat_errors", []).append(
                        f"{chat_id}: no read state available, skipped for safety"
                    )
                    continue
                walk = [(0, 0, read_max, unread)]

            for topic_id, top_msg, read_max, unread in walk:
                cursor = state.cursor_for(chat_id, topic_id)

                # 1. Nothing new since we last swept this topic -> skip entirely.
                #    This is what stops us reprocessing every topic every run.
                if top_msg and cursor and top_msg <= cursor:
                    report["topics_skipped_unchanged"] += 1
                    continue

                # 2. UNREAD topics are LEFT ALONE. Deleting or collapsing
                #    messages the owner has not read yet would destroy them
                #    before he ever sees them. Wait until he has caught up.
                #    (read_inbox_max_id advances as he reads on any device.)
                if cfg.get("respect_unread", True) and unread and unread > 0:
                    report["topics_skipped_unread"] += 1
                    continue

                report["topics_walked"] += 1
                msgs = []
                # min_id makes the server return only messages after the
                # cursor, so a swept topic is never re-paged.
                kwargs = dict(limit=cfg.get("scan_limit", 3000))
                if topic_id:
                    kwargs["reply_to"] = topic_id
                if cursor:
                    kwargs["min_id"] = cursor
                async for m in client.iter_messages(ent, **kwargs):
                    if m.date < horizon:
                        break
                    msgs.append(m)
                if not msgs:
                    # Topic had nothing inside the 47h delete window. Record
                    # the cursor anyway (at its current head) or this topic is
                    # re-walked on every single run forever — measured: only
                    # 6 of 22 topics were skippable because dormant topics
                    # never earned a cursor.
                    if apply and top_msg:
                        state.set_cursor(chat_id, topic_id, top_msg, now.isoformat())
                    continue

                before_arch = report["archive_failures"]
                before_api = len(bot.failures)
                await process_batch(
                    msgs, chat_id, topic_id, read_max, bot, bot_id, owner_id,
                    client, ent, state, root, now, cfg, apply, report,
                )
                # Only advance past work that actually succeeded. Advancing
                # after a failed archive or API call would make the next run
                # skip that range forever, so the failure could never be
                # retried once the transient cause cleared.
                clean = (
                    report["archive_failures"] == before_arch
                    and len(bot.failures) == before_api
                )
                newest_id = max(m.id for m in msgs)
                if apply and clean:
                    state.set_cursor(chat_id, topic_id, newest_id, now.isoformat())
                elif apply:
                    report.setdefault("cursor_held", []).append(
                        {"chat": chat_id, "topic": topic_id}
                    )
        except Exception as e:  # one bad room must not abort the rest
            report.setdefault("chat_errors", []).append(f"{chat_id}: {e}")

    report["api_failures"] = len(bot.failures)
    if bot.failures:
        report["api_failure_detail"] = bot.failures[:10]

    await client.disconnect()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    # Non-zero when a requested mutation failed, so cron surfaces it.
    return 1 if (report["archive_failures"] or report["api_failures"]) else 0


async def process_batch(
    msgs, chat_id, topic_id, read_max, bot, bot_id, owner_id,
    client, ent, state, root, now, cfg, apply, report,
):
    """Classify and act on one topic's batch of messages."""
    replied_to = {
        m.reply_to.reply_to_msg_id
        for m in msgs
        if m.sender_id == owner_id and getattr(m, "reply_to", None)
    }
    pinned = set()
    try:
        async for pm in client.iter_messages(ent, filter_pinned=True, limit=50):
            pinned.add(pm.id)
    except Exception:
        pinned = {m.id for m in msgs if getattr(m, "pinned", False)}

    # Anything the owner replied to is preserved — otherwise his reply is left
    # pointing at a deleted message. This must exclude from DELETION, not only
    # from cluster escalation.
    protected = pinned | replied_to
    ours = [m for m in msgs if m.sender_id == bot_id and m.id not in protected]

    ephemeral, critical, routine = [], [], []
    for m in ours:
        text = m.raw_text or ""
        # A message the owner has NOT read yet is never touched, even inside
        # an otherwise-caught-up topic. read_max is the per-topic watermark.
        if read_max and m.id > read_max:
            report["held"] += 1
            continue
        # Service messages (pin/join/topic events) are chat structure, not
        # agent output. They report empty text and are not bot-deletable.
        if getattr(m, "action", None) is not None:
            continue
        # Media is never auto-deleted: the archive is text-only, so a
        # chart-only alert would be destroyed with no recoverable copy.
        if getattr(m, "media", None) is not None:
            report["held"] += 1
            continue
        if NEVER_TOUCH.search(text):
            critical.append(m)
        elif not text:
            report["held"] += 1
        elif is_ephemeral(text):
            ephemeral.append(m)
        else:
            routine.append(m)

    report["critical"] += len(critical)

    # ---- 1. ephemeral: archive (verified) then delete
    if ephemeral:
        recs = [
            {
                "chat": chat_id,
                "thread": topic_id,
                "id": m.id,
                "date": m.date.isoformat(),
                "text": m.raw_text or "",
                "class": "ephemeral",
            }
            for m in ephemeral
        ]
        ok = archive_and_verify(root / str(chat_id) / f"{now:%Y-%m-%d}.jsonl", recs) if apply else True
        if ok:
            for m in ephemeral:
                if bot.delete(chat_id, m.id).get("ok"):
                    report["deleted"] += 1
        else:
            report["archive_failures"] += 1

    # ---- 2. clusters: escalate BEFORE collapsing
    clusters: dict[str, list] = defaultdict(list)
    for m in routine + critical:
        clusters[canonical(m.raw_text or "")].append(m)

    for sig, group in clusters.items():
        group.sort(key=lambda m: (m.date, m.id))
        newest = group[-1]
        acked = any(m.id in replied_to for m in group)
        # hashlib, not hash(): Python's hash() is salted per process, so a
        # signature would get a different key on every run and never
        # accumulate.
        short_sig = hashlib.sha256(sig.encode("utf-8")).hexdigest()[:32]
        if apply:
            state.observe(chat_id, short_sig, newest.date.isoformat(), sig[:400], n=len(group))
            if acked:
                state.ack(chat_id, short_sig)

        # Repetition and elapsed time come from PERSISTED state, not this
        # batch. With an incremental cursor a batch usually holds one new
        # copy, so an hourly alarm would show len(group)==1 forever and
        # never trip the threshold.
        row = state.get(chat_id, short_sig)
        if row:
            first_seen, _last, total, acked_db, _esc = row
            acked = acked or bool(acked_db)
            try:
                first_dt = dt.datetime.fromisoformat(first_seen)
            except ValueError:
                first_dt = group[0].date
        else:
            total, first_dt = len(group), group[0].date
        span_h = (now - first_dt).total_seconds() / 3600

        is_critical = bool(NEVER_TOUCH.search(newest.raw_text or ""))
        # Repetition ALONE is a severity signal, independent of wording.
        repeated_alarm = total >= ESCALATE_COUNT and span_h >= ESCALATE_HOURS

        if (is_critical or repeated_alarm) and not acked:
            bot.react(chat_id, newest.id, REACT_UNACKED)
            report["escalated"].append(
                {
                    "chat": chat_id,
                    "thread": topic_id,
                    "count": total,
                    "span_h": round(span_h, 1),
                    "sample": sig[:100],
                }
            )
            continue  # NEVER collapse an unacknowledged alarm
        if acked and is_critical:
            bot.react(chat_id, newest.id, REACT_ACKED)
            continue

        if not cfg.get("collapse_duplicates"):
            continue
        if len(group) < cfg.get("cluster_min", 3):
            continue
        if sig[:120] not in set(cfg.get("collapse_allowlist", [])):
            report["held"] += len(group)
            continue

        older = group[:-1]
        recs = [
            {
                "chat": chat_id,
                "thread": topic_id,
                "id": m.id,
                "date": m.date.isoformat(),
                "text": m.raw_text or "",
                "class": "collapsed",
            }
            for m in older
        ]
        ok = archive_and_verify(root / str(chat_id) / f"{now:%Y-%m-%d}.jsonl", recs) if apply else True
        if not ok:
            report["archive_failures"] += 1
            continue
        for m in older:
            if bot.delete(chat_id, m.id).get("ok"):
                report["collapsed"] += 1
        bot.react(chat_id, newest.id, REACT_ROLLUP)
        bot.send(
            chat_id,
            f"⟳ <b>{len(group)}×</b> identical over {span_h:.0f}h — collapsed, "
            f"newest kept above. First {group[0].date:%m-%d %H:%M}Z.",
            thread_id=topic_id or None,
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--apply", action="store_true", help="actually mutate (default dry-run)")
    a = ap.parse_args()
    cfg = json.load(open(os.path.expanduser(a.config)))
    sys.exit(asyncio.run(sweep(cfg, a.apply)))


if __name__ == "__main__":
    main()
