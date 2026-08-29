#!/usr/bin/env python3
"""
Fleet job-health collector.

Gathers FACTS about what the scheduled jobs actually did, across every profile
on every reachable host, plus what those jobs actually SAID in Telegram. Emits
one compact report. It draws no conclusions: severity and "does this matter" are
judgements, and a judgement made by a regex over alarm words grades a resolved
incident as CRITICAL and a quietly broken job as fine. The agent reading this
output decides; this script only measures.

Two independent sources on purpose:
  * the LEDGER says what the runner believes happened
  * TELEGRAM says what the human actually received
Disagreement between them is the interesting signal, and neither alone can show
it. A job that reports success while sending nothing, or sends a flood while its
ledger looks clean, is invisible to a single-source check.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

HOURS = int(os.environ.get("FLEET_WATCH_HOURS", "4"))
NOW = datetime.now(timezone.utc)
SINCE = NOW - timedelta(hours=HOURS)

HOSTS = ["hex", "ali", "gil", "julianna", "sous", "trading"]

REMOTE = r'''
import json, os, sys
from collections import Counter
from datetime import datetime, timedelta, timezone
since = datetime.now(timezone.utc) - timedelta(hours=%d)
home = os.path.expanduser("~")
out = {"host": os.uname().nodename, "profiles": []}
bases = []
r = os.path.join(home, ".hermes")
if os.path.isdir(os.path.join(r, "jobstate")): bases.append(("_root", r))
pd = os.path.join(r, "profiles")
if os.path.isdir(pd):
    for n in sorted(os.listdir(pd)):
        p = os.path.join(pd, n)
        if os.path.isdir(os.path.join(p, "jobstate")): bases.append((n, p))
for name, base in bases:
    led = os.path.join(base, "jobstate", "runs.jsonl")
    rec = {"profile": name, "runs": 0, "states": {}, "failures": [],
           "noteworthy": 0, "runner": "absent", "quiet": True}

    # COVERAGE. The ledger only knows jobs jobrun executes. On one host that is
    # 31 of 54 enabled cron jobs, so a ledger-only read reported "0 failures"
    # while that host's deploy-drift watchdog failed five times in a row and
    # paged the owner. The scheduler's own jobs.json is the real denominator;
    # anything in it without a ledger entry is a job this monitor CANNOT see,
    # and that has to be stated rather than silently omitted.
    rec["cron_enabled"] = 0
    rec["uncovered"] = []
    cj = os.path.join(base, "cron", "jobs.json")
    if os.path.isfile(cj):
        try:
            raw = json.load(open(cj, errors="replace"))
            jobs = raw if isinstance(raw, list) else raw.get("jobs", [])
        except Exception:
            jobs = []
        covered = set()
        if os.path.isfile(led):
            for line in open(led, errors="replace"):
                line = line.strip()
                if not line: continue
                try: row = json.loads(line)
                except Exception: continue
                if row.get("event") == "job.finished":
                    covered.add(row.get("job_id"))

        def _slug(s):
            o = "".join(ch if ch.isalnum() else "-" for ch in (s or "").lower())
            while "--" in o: o = o.replace("--", "-")
            return o.strip("-")

        for j in jobs:
            if not j.get("enabled"): continue
            rec["cron_enabled"] += 1
            if _slug(j.get("name")) not in covered:
                rec["uncovered"].append({
                    "name": j.get("name"),
                    "script": j.get("script") or "(agent prompt)",
                })
    jr = os.path.join(base, "scripts", "jobrun.py")
    if os.path.isfile(jr):
        t = open(jr, errors="replace").read()
        rec["runner"] = ("v2+exitmap" if "exit_map" in t
                         else "v2" if "_v2_classify" in t else "v1")
    if os.path.isfile(led):
        st = Counter()
        for line in open(led, errors="replace"):
            line = line.strip()
            if not line: continue
            try: row = json.loads(line)
            except Exception: continue
            ts = row.get("finished_at") or row.get("ts") or ""
            try:
                when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            if when < since: continue
            ev = row.get("event")
            if ev == "job.noteworthy":
                rec["noteworthy"] += 1
                continue
            if ev != "job.finished": continue
            rec["runs"] += 1
            s = row.get("state") or "?"
            st[s] += 1
            if s != "success":
                rec["failures"].append({
                    "job": row.get("job_id"), "state": s,
                    "exit": row.get("exit_code"),
                    "dur_s": round((row.get("duration_ms") or 0)/1000, 1),
                    "at": ts, "critical": row.get("critical"),
                })
        rec["states"] = dict(st)
        rec["quiet"] = rec["runs"] == 0
    # open incidents, if the v2 db exists
    inc = os.path.join(base, "jobstate", "incidents.db")
    if os.path.isfile(inc):
        try:
            import sqlite3
            c = sqlite3.connect("file:%%s?mode=ro" %% inc, uri=True)
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT job_id,phase,occurrence_count,repair_attempts,"
                "reason_code,severity FROM incidents "
                "WHERE phase NOT IN ('resolved') "
                "ORDER BY occurrence_count DESC LIMIT 12").fetchall()
            rec["open_incidents"] = [dict(x) for x in rows]
            c.close()
        except Exception as e:
            rec["open_incidents_error"] = str(e)[:120]
    out["profiles"].append(rec)
print(json.dumps(out))
''' % HOURS


def local() -> dict:
    p = subprocess.run([sys.executable, "-c", REMOTE],
                       capture_output=True, text=True, timeout=120)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"host": "studio", "error": (p.stderr or p.stdout)[-300:]}


def remote(h: str) -> dict:
    try:
        p = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes", h,
             "python3 -c " + json_quote(REMOTE)],
            capture_output=True, text=True, timeout=150)
        if p.returncode != 0:
            return {"host": h, "unreachable": (p.stderr or "")[-160:]}
        return json.loads(p.stdout)
    except subprocess.TimeoutExpired:
        return {"host": h, "unreachable": "ssh timeout"}
    except Exception as exc:
        return {"host": h, "unreachable": str(exc)[:160]}


def json_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)


def telegram_traffic() -> dict:
    """What the jobs actually SENT, read through the user session.

    Runs in a SUBPROCESS under the tgcli venv rather than importing telethon
    here: this script runs under the agent venv, which does not have it, and a
    glob in sys.path does not expand (the first attempt silently reported
    'telethon unavailable' on a host where telethon was installed and fine).
    """
    tg_py = os.path.expanduser("~/.tgcli/venv/bin/python")
    if not os.path.isfile(tg_py):
        return {"error": "tgcli venv not found"}
    code = TG_PROBE % HOURS
    try:
        p = subprocess.run([tg_py, "-c", code],
                           capture_output=True, text=True, timeout=240)
        if p.returncode != 0:
            return {"error": (p.stderr or p.stdout)[-300:]}
        return json.loads(p.stdout)
    except Exception as exc:
        return {"error": str(exc)[:200]}


TG_PROBE = r'''
import json, os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from telethon.sync import TelegramClient
from telethon.tl import functions
since = datetime.now(timezone.utc) - timedelta(hours=%d)
cfg = json.load(open(os.path.expanduser("~/.tgcli/config.json")))

# Several .session files exist side by side and only some are authorized.
# Picking one by name silently reported "not authorized" while a working
# session sat next to it, so probe them and use the first that is live.
c = None
for name in ("telethon-session", "tgcli", "session", "telethon"):
    p = os.path.expanduser("~/.tgcli/" + name)
    if not os.path.isfile(p + ".session"):
        continue
    t = TelegramClient(p, cfg["app_id"], cfg["app_hash"])
    try:
        t.connect()
        if t.is_user_authorized():
            c = t
            break
        t.disconnect()
    except Exception:
        try: t.disconnect()
        except Exception: pass
if c is None:
    print(json.dumps({"error": "no authorized tgcli session found"}))
    raise SystemExit

counts = Counter(); samples = defaultdict(list)
alerts = []
per_topic = Counter()
topic_names = {}

# ALERT SHAPES. Scheduled-job traffic is identified STRUCTURALLY from the
# scheduler's own envelope, never from scary words. Keyword-only matching hid
# routine success cards, which made it impossible to judge noisy narration or
# a quietly broken job. Direct bot alerts do not have that envelope, so keep a
# narrow second lane for their explicit alert shapes.
CRON_MARKERS = ("cronjob response:", "(job_id:",
                "to stop or manage this job", "cron '")
DIRECT_ALERT_MARKS = ("failed: script exited", "has failed", "runs in a row",
                      "\U0001f534", "\U0001f6d1", "critical", "drift",
                      "not in the repo", "stale code", "wedged", "halt")

for d in c.iter_dialogs(limit=200):
    e = d.entity
    if not (getattr(e, "megagroup", False) or getattr(e, "broadcast", False)):
        continue
    # FORUM TOPICS. These groups are forums; a flat iter_messages mixes every
    # topic together and, worse, `limit` is consumed by whichever topic is
    # chattiest. One forum ran ~190 messages/48h in a single busy topic, so a
    # limit of 150 never reached the ops topic at all -- its alerts were
    # counted into the forum total and then discarded, which is exactly how a
    # monitor reports 144 messages and still sees nothing.
    if getattr(e, "forum", False):
        try:
            r = c(functions.messages.GetForumTopicsRequest(
                peer=e, offset_date=None, offset_id=0, offset_topic=0, limit=100))
            for t_ in r.topics:
                tid = getattr(t_, "id", None)
                if tid is not None:
                    topic_names[(d.name, tid)] = getattr(t_, "title", "?")
        except Exception:
            pass
    try:
        for m in c.iter_messages(e, limit=600):
            if not m.date or m.date < since: break
            body = m.message or ""
            if not body and getattr(m, "rich_message", None):
                try:
                    body = " ".join(b.get("text","") for b in m.rich_message.blocks)
                except Exception:
                    body = ""
            if not body: continue
            tid = None
            rt = getattr(m, "reply_to", None)
            if rt is not None:
                tid = getattr(rt, "reply_to_top_id", None) or getattr(
                    rt, "reply_to_msg_id", None)
            topic = topic_names.get((d.name, tid))
            counts[d.name] += 1
            per_topic[d.name + " / " + str(topic or tid or "-")] += 1
            low = body.lower()
            is_bot = False
            try:
                is_bot = bool(getattr(m.sender, "bot", False))
            except Exception:
                pass
            # Carry every scheduled-job card. Judgement needs the full body to
            # catch both real pages and success narration that should be silent.
            # Direct bot alerts outside the scheduler envelope stay a narrow,
            # explicit lane rather than turning scary-word matching into a
            # severity classifier.
            is_cron = any(k in low for k in CRON_MARKERS)
            is_direct_alert = any(k in low for k in DIRECT_ALERT_MARKS)
            if is_bot and (is_cron or is_direct_alert):
                alerts.append({
                    "chat": d.name, "topic": topic, "topic_id": tid,
                    "at": m.date.isoformat(), "text": body[:12000],
                })
            if len(samples[d.name]) < 5:
                samples[d.name].append(body[:260].replace("\n", " | "))
    except Exception:
        continue
c.disconnect()
alerts.sort(key=lambda a: a["at"])
print(json.dumps({"per_chat": dict(counts.most_common(30)),
                  "per_topic": dict(per_topic.most_common(60)),
                  "alerts": alerts[-60:],
                  "alert_count": len(alerts),
                  "samples": {k: v for k, v in list(samples.items())[:10]}}))
'''


def main() -> int:
    hosts = [local()] + [remote(h) for h in HOSTS]
    report = {
        "window_hours": HOURS,
        "generated_at": NOW.isoformat(),
        "hosts": hosts,
        "telegram": telegram_traffic(),
    }
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
