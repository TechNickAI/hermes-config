#!/usr/bin/env python3
"""Weekly session-store maintenance launcher for one profile.

Wired to Hermes cron as a ``no_agent`` script job, which means:

  * stdout is delivered to the owner VERBATIM
  * EMPTY stdout is SILENT -- nothing is sent at all

So this prints NOTHING on a healthy run. A weekly "pruned 240 sessions, all
good" message is noise: it needs no decision and trains you to ignore the
channel. It speaks only when a human has something to do -- a failed run, a
lost human session, or a database that has grown past the point where it can
be compacted inside the write-lock budget and needs a supervised window.

Retention only. VACUUM is deliberately NOT run unattended: it takes an
exclusive write lock for ~14.4s per GB, and Hermes fails a user's turn after
60s of blocked transcript writes. Compaction is a supervised, manual step.

Cron requires ``script`` to be a bare FILENAME, not a command line, so the
arguments live here rather than in the job spec.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

PROFILE = os.environ.get("DBMAINT_PROFILE", "kenbot")

# Retention floor. Machine sessions idle longer than this are deleted.
DAYS = 10

# Gentle by design: small age slices, each its own transaction, with a pause
# between them so the live gateway can drain its own queued writes. One huge
# delete would hold the write lock and burst the WAL; forty small ones do not.
CHUNK_DAYS = 7
PAUSE = 5.0

# Stop cleanly after 15 minutes and leave the remainder for next week. A
# catch-up that must be interrupted should still bank its progress.
MAX_SECONDS = 900.0

# Speak up if the store is big enough to need a supervised compaction.
NEEDS_VACUUM_ABOVE_MB = 3000


def main() -> int:
    home = Path.home() / ".hermes"
    script = home / "profiles" / PROFILE / "scripts" / "dbmaint.py"
    if not script.is_file():
        script = home / "scripts" / "dbmaint.py"
    if not script.is_file():
        print(f"[db-maintenance/{PROFILE}] dbmaint.py not found; job is a no-op.")
        return 1

    python = home / "hermes-agent" / "venv" / "bin" / "python"
    cmd = [
        str(python) if python.exists() else sys.executable,
        str(script),
        "--profile", PROFILE,
        "--days", str(DAYS),
        "--apply",
        "--no-vacuum",
        "--chunk-days", str(CHUNK_DAYS),
        "--pause", str(PAUSE),
        "--max-seconds", str(MAX_SECONDS),
        "--json",
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=MAX_SECONDS + 600
        )
    except subprocess.TimeoutExpired:
        print(
            f"[db-maintenance/{PROFILE}] FAILED: wrapper timed out. "
            f"The database may still be locked; check the gateway."
        )
        return 1

    # A concurrent run is expected occasionally (manual work overlapping the
    # schedule). It is not a fault and needs no message.
    if "another maintenance run" in (proc.stdout + proc.stderr):
        return 0

    if proc.returncode != 0:
        tail = (proc.stdout or proc.stderr or "").strip()[-600:]
        print(f"[db-maintenance/{PROFILE}] FAILED (exit {proc.returncode}).\n{tail}")
        return 1

    try:
        report = json.loads(proc.stdout)
    except (ValueError, TypeError):
        print(
            f"[db-maintenance/{PROFILE}] FAILED: unreadable report.\n"
            f"{(proc.stdout or '')[-400:]}"
        )
        return 1

    if not report.get("probe_ok"):
        print(
            f"[db-maintenance/{PROFILE}] FAILED: {report.get('error', 'unknown')}"
        )
        return 1

    # The one data-safety condition worth waking someone for.
    before = report.get("human_sessions_before")
    after = report.get("human_sessions_after")
    if before is not None and after is not None and after < before:
        print(
            f"[db-maintenance/{PROFILE}] STOP: human sessions dropped "
            f"{before} -> {after}. A backup was preserved. Do not run again "
            f"until this is understood."
        )
        return 1

    # Actionable because it requires scheduling a supervised window.
    size_mb = (report.get("after") or {}).get("db_mb", 0)
    if size_mb >= NEEDS_VACUUM_ABOVE_MB:
        pruned = report.get("pruned") or {}
        total = sum(v for v in pruned.values() if isinstance(v, int))
        print(
            f"[db-maintenance/{PROFILE}] Retention ran ({total} sessions), but "
            f"state.db is still {size_mb} MB. Reclaiming that space needs a "
            f"VACUUM, which takes an exclusive write lock (~14.4s/GB) and is "
            f"not safe to run unattended against a live gateway. Schedule a "
            f"supervised window."
        )
        return 0

    # Healthy. Say nothing.
    return 0


if __name__ == "__main__":
    sys.exit(main())
