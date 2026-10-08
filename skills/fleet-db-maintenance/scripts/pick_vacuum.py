import os
import sqlite3
import time

targets = {
    "_root": "~/.hermes/state.db",
    "sterling": "~/.hermes/profiles/sterling/state.db",
    "cora": "~/.hermes/profiles/cora/state.db",
    "bosun": "~/.hermes/profiles/bosun/state.db",
}
now = time.time()
for name, p in targets.items():
    path = os.path.expanduser(p)
    if not os.path.isfile(path):
        continue
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.execute("PRAGMA busy_timeout=15000")
    last = c.execute(
        "select max(coalesce(last_activity_at, started_at)) from sessions"
    ).fetchone()[0]
    # Human traffic in the last hour is the thing that makes a lock risky.
    recent_human = c.execute(
        "select count(*) from sessions "
        "where coalesce(source,'') not in ('cron','subagent') "
        "and coalesce(last_activity_at, started_at) > ?",
        (now - 3600,),
    ).fetchone()[0]
    free_pages, page_size = (
        c.execute("pragma freelist_count").fetchone()[0],
        c.execute("pragma page_size").fetchone()[0],
    )
    c.close()
    mb = os.path.getsize(path) // 1048576
    print(
        f"{name}\t{mb}MB\tidle={round((now - float(last)) / 60, 1)}min\t"
        f"human_1h={recent_human}\tfreelist={free_pages * page_size // 1048576}MB\t"
        f"lock~{round(mb / 1024 * 14.4)}s"
    )
