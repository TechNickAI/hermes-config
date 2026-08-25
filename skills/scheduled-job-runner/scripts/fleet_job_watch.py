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
for d in c.iter_dialogs(limit=60):
    e = d.entity
    if not (getattr(e, "megagroup", False) or getattr(e, "broadcast", False)):
        continue
    try:
        for m in c.iter_messages(e, limit=150):
            if not m.date or m.date < since: break
            body = m.message or ""
            if not body and getattr(m, "rich_message", None):
                try:
                    body = " ".join(b.get("text","") for b in m.rich_message.blocks)
                except Exception:
                    body = ""
            if not body: continue
            counts[d.name] += 1
            if len(samples[d.name]) < 5:
                samples[d.name].append(body[:260].replace("\n", " | "))
    except Exception:
        continue
c.disconnect()
print(json.dumps({"per_chat": dict(counts.most_common(20)),
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
