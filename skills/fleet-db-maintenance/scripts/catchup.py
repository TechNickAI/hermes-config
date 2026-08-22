"""Catch-up prune for one profile, with before/after safety verification.

Prints a compact record so each run can be checked rather than trusted.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

profile = sys.argv[1]
root = os.path.expanduser("~/.hermes")
base = root if profile == "_root" else os.path.join(root, "profiles", profile)
db = os.path.join(base, "state.db")

py = os.path.join(root, "hermes-agent", "venv", "bin", "python")
if not os.path.exists(py):
    py = sys.executable
script = os.path.join(base, "scripts", "dbmaint.py")


def snap():
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.execute("PRAGMA busy_timeout=60000")
    out = {
        "mb": os.path.getsize(db) // 1048576,
        "human": c.execute(
            "select count(*) from sessions "
            "where coalesce(source,'') not in ('cron','subagent')"
        ).fetchone()[0],
        "by_source": dict(
            c.execute(
                "select coalesce(source,'?'), count(*) from sessions group by 1"
            ).fetchall()
        ),
    }
    # Recall probe against human sessions only -- machine-session hits are
    # expected to fall, human ones must not.
    try:
        out["human_fts"] = c.execute(
            "select count(*) from messages_fts f "
            "join messages m on m.id=f.rowid "
            "join sessions s on s.id=m.session_id "
            "where messages_fts match 'the' "
            "and coalesce(s.source,'') not in ('cron','subagent')"
        ).fetchone()[0]
    except Exception:
        out["human_fts"] = None
    c.close()
    return out


before = snap()
started = time.time()

proc = subprocess.run(
    [py, script, "--profile", profile, "--days", "10", "--apply",
     "--no-vacuum", "--chunk-days", "14", "--pause", "3", "--json"],
    capture_output=True, text=True, timeout=5400,
)

after = snap()
elapsed = round(time.time() - started)

ok = proc.returncode == 0
report = {}
try:
    report = json.loads(proc.stdout)
except Exception:
    pass

lost_human = before["human"] - after["human"]
verdict = "OK"
if not ok:
    verdict = "FAILED"
elif lost_human > 0:
    verdict = "HUMAN LOSS"

print(json.dumps({
    "profile": profile,
    "verdict": verdict,
    "rc": proc.returncode,
    "elapsed_s": elapsed,
    "mb": [before["mb"], after["mb"]],
    "human": [before["human"], after["human"]],
    "human_fts": [before["human_fts"], after["human_fts"]],
    "pruned": report.get("pruned"),
    "cron": [before["by_source"].get("cron", 0), after["by_source"].get("cron", 0)],
    "subagent": [before["by_source"].get("subagent", 0),
                 after["by_source"].get("subagent", 0)],
    "other_sources_changed": {
        k: [v, after["by_source"].get(k, 0)]
        for k, v in before["by_source"].items()
        if k not in ("cron", "subagent") and after["by_source"].get(k, 0) != v
    },
    "stderr_tail": (proc.stderr or "")[-300:] if not ok else "",
}, indent=1))
