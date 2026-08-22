#!/usr/bin/env python3
"""Locate corruption in a Hermes state.db.

``PRAGMA integrity_check`` tells you a database is malformed. It does not tell
you WHICH table, and that distinction decides the whole response: recreating
one rebuildable queue table is routine, losing a conversation history is not.

This full-scans every table so the damage can be localized before anyone
reaches for a repair. Read-only -- it never writes to the database.

Found kenbot's real corruption in 2026-08: 21 tables, exactly one
(`delivery_obligations`) raised, while 3,629 sessions, 452,451 messages and
both FTS indexes read clean.

Usage: locate_corruption.py [path/to/state.db]
"""
import os
import sqlite3
import sys

db = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/.hermes/state.db")
print(f"database: {db}")
print(f"size: {os.path.getsize(db) // 1048576} MB")
print()

c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
c.execute("PRAGMA busy_timeout=30000")

print("=== whole-database verdict ===")
for pragma in ("quick_check", "integrity_check"):
    try:
        print(f"  {pragma}: {c.execute(f'pragma {pragma}').fetchall()[:3]}")
    except Exception as e:
        print(f"  {pragma}: RAISED {type(e).__name__}: {str(e)[:60]}")

print()
print("=== full-scan each table (this is what localizes it) ===")
tables = [r[0] for r in c.execute(
    "select name from sqlite_master where type='table' order by name"
).fetchall()]
bad = []
for t in tables:
    try:
        c.execute(f'select count(*) from "{t}"').fetchone()
        print(f"  ok    {t}")
    except Exception as e:
        bad.append(t)
        print(f"  BAD   {t}: {str(e)[:60]}")

print()
print("=== FTS MATCH probe (a count can pass while MATCH throws) ===")
for t in tables:
    if not t.endswith(("_fts", "_trigram")):
        continue
    try:
        c.execute(f"select rowid from {t} where {t} match 'the' limit 1").fetchone()
        print(f"  ok    {t} MATCH")
    except Exception as e:
        print(f"  BAD   {t} MATCH: {str(e)[:60]}")

print()
print("=== verdict ===")
if not bad:
    print("  no table raised on scan")
else:
    print(f"  damaged: {bad}")
    rebuildable = [t for t in bad
                   if t.endswith(("_fts", "_data", "_idx", "_docsize", "_config"))
                   or t == "delivery_obligations"]
    precious = [t for t in bad if t not in rebuildable]
    print(f"  rebuildable (queue/index): {rebuildable or 'none'}")
    print(f"  PRECIOUS (do not drop):    {precious or 'none'}")
    print()
    print("  Do NOT vacuum a corrupt database -- rewriting a file with a bad")
    print("  page turns localized damage into total loss, and the backup")
    print("  would be a copy of the corruption. Byte-copy the file first.")
c.close()
