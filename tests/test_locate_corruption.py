"""Prove locate_corruption.py actually detects damage.

A detector that only ever reports "clean" is worthless. This builds a real
SQLite file, corrupts a specific table's pages, and checks the tool names that
table -- the same shape as kenbot's delivery_obligations failure.
"""
import os
import subprocess
import sqlite3
import sys
import tempfile

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "fleet-db-maintenance", "scripts", "locate_corruption.py")
PY = os.path.expanduser("~/.hermes/hermes-agent/venv/bin/python")

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "state.db")

c = sqlite3.connect(db)
c.execute("PRAGMA page_size=4096")
c.execute("PRAGMA journal_mode=DELETE")  # single file, easy to corrupt
c.execute("create table sessions (id text primary key, source text)")
c.execute("create table victim (id integer primary key, blob text)")
for i in range(400):
    c.execute("insert into sessions values (?,?)", (f"s{i}", "telegram"))
    c.execute("insert into victim values (?,?)", (i, "x" * 900))
c.commit()

# Find a page belonging to `victim` and scribble on it.
pages = c.execute(
    "select pageno from dbstat where name='victim' order by pageno limit 5"
).fetchall()
target = pages[-1][0]
c.close()

with open(db, "r+b") as f:
    f.seek((target - 1) * 4096)
    f.write(b"\xde\xad\xbe\xef" * 512)

print(f"corrupted page {target} (belongs to 'victim')")
print()
out = subprocess.run([PY, TOOL, db], capture_output=True, text=True).stdout
print(out)

ok = "BAD   victim" in out and "ok    sessions" in out
print("RESULT:", "PASS - named the damaged table and cleared the healthy one"
      if ok else "FAIL - did not localize correctly")
sys.exit(0 if ok else 1)
