#!/usr/bin/env python3
"""
fitlog.py — model-task-fit knowledge base that accumulates over MOA runs.

Answers the ask: "store results, indicating what kind of tasks score well for which
models... building up knowledge over time for which models to use for what."

We do NOT score by #sources. Dimensions (matching the SKILL.md rubric):
  completeness, soundness, actionability, usable_novelty, testability  (0-5 each)
Plus a free-text `won_on` = what this seat was BEST at in the run (the best-of-breed note).

Schema:
  runs(run_id, when, problem_kind, brief_label, notes)
  scores(run_id, seat, model, backend, role, completeness, soundness, actionability,
         usable_novelty, testability, won_on, used_in_synthesis)

CLI:
  fitlog.py init
  fitlog.py add-run  --run-id R --kind KIND --label L [--notes N]
  fitlog.py score    --run-id R --seat S --model M --backend B --role RO \
                     --completeness 4 --soundness 5 --actionability 3 \
                     --usable-novelty 2 --testability 4 \
                     --won-on "architectural purity" --used 1
  fitlog.py report   [--kind KIND]     # aggregates: which model wins what
"""
import sqlite3, argparse, os, sys
from pathlib import Path
from datetime import datetime

# Portable: override with MOA_FITLOG_DB. Default lives in the Hermes profile.
DB = Path(os.environ.get("MOA_FITLOG_DB",
    str(Path.home() / ".hermes/moa_fitlog.db")))

def conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    migrate_legacy_scores(c)
    return c

def migrate_legacy_scores(c):
    """Rebuild scores if it still has the pre-rubric columns.

    Early versions used quality/creativity/correctness. CREATE TABLE IF NOT
    EXISTS leaves that layout in place, and the new rubric columns then fail
    with an OperationalError. Legacy rows aren't translatable to the new
    rubric, so archive the old table and immediately recreate with the new schema.
    """
    cols = {row[1] for row in c.execute("PRAGMA table_info(scores)")}
    if cols and "soundness" not in cols:
        c.execute("ALTER TABLE scores RENAME TO scores_legacy_v1")
        c.execute("""CREATE TABLE scores(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT, seat TEXT, model TEXT, backend TEXT, role TEXT,
          completeness INT, soundness INT, actionability INT,
          usable_novelty INT, testability INT,
          won_on TEXT, used_in_synthesis INT,
          UNIQUE(run_id, seat))""")
        c.commit()
        print("fitlog: archived old scores table as scores_legacy_v1 (pre-rubric schema), new table ready")

def init():
    c = conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS runs(
      run_id TEXT PRIMARY KEY, when_ts TEXT, problem_kind TEXT,
      brief_label TEXT, notes TEXT);
    CREATE TABLE IF NOT EXISTS scores(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT, seat TEXT, model TEXT, backend TEXT, role TEXT,
      completeness INT, soundness INT, actionability INT,
      usable_novelty INT, testability INT,
      won_on TEXT, used_in_synthesis INT,
      UNIQUE(run_id, seat));
    """)
    c.commit(); c.close(); print("fitlog initialized at", DB)

def add_run(a):
    c = conn()
    c.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?)",
              (a.run_id, datetime.now().isoformat(), a.kind, a.label, a.notes or ""))
    c.commit(); c.close(); print("run", a.run_id, "recorded")

def score(a):
    c = conn()
    c.execute("""INSERT OR REPLACE INTO scores
      (run_id,seat,model,backend,role,completeness,soundness,actionability,
       usable_novelty,testability,won_on,used_in_synthesis)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
      (a.run_id,a.seat,a.model,a.backend,a.role,a.completeness,a.soundness,
       a.actionability,a.usable_novelty,a.testability,a.won_on,a.used))
    c.commit(); c.close(); print("scored", a.seat, a.model)

def report(a):
    c = conn()
    q = """SELECT model, role,
      COUNT(*) n,
      ROUND(AVG(completeness),1) comp, ROUND(AVG(soundness),1) snd,
      ROUND(AVG(actionability),1) act, ROUND(AVG(usable_novelty),1) nov,
      ROUND(AVG(testability),1) tst,
      SUM(used_in_synthesis) used,
      GROUP_CONCAT(won_on, ' | ') won
      FROM scores s LEFT JOIN runs r USING(run_id)"""
    params = ()
    if a.kind:
        q += " WHERE r.problem_kind=?"; params = (a.kind,)
    q += " GROUP BY model, role ORDER BY used DESC, snd DESC"
    rows = c.execute(q, params).fetchall()
    c.close()
    if not rows:
        print("(no scores yet)"); return
    print(f"{'model':32}{'role':16}{'n':>3}{'comp':>5}{'snd':>5}{'act':>5}{'nov':>5}{'tst':>5}{'used':>5}  won_on")
    for m,role,n,comp,snd,act,nov,tst,used,won in rows:
        print(f"{str(m)[:31]:32}{str(role)[:15]:16}{n:>3}{comp:>5}{snd:>5}{act:>5}{nov:>5}{tst:>5}{used or 0:>5}  {won or ''}")

def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    r = sub.add_parser("add-run"); r.add_argument("--run-id",required=True,dest="run_id")
    r.add_argument("--kind",required=True); r.add_argument("--label",required=True); r.add_argument("--notes")
    s = sub.add_parser("score")
    for f in ("run-id","seat","model","backend","role","won-on"): s.add_argument("--"+f, dest=f.replace("-","_"), required=True)
    for f in ("completeness","soundness","actionability","usable-novelty","testability","used"):
        s.add_argument("--"+f, dest=f.replace("-","_"), type=int, required=True)
    rp = sub.add_parser("report"); rp.add_argument("--kind")
    a = ap.parse_args()
    {"init": lambda a: init(), "add-run": add_run, "score": score, "report": report}[a.cmd](a) \
        if a.cmd != "init" else init()

if __name__ == "__main__":
    main()
