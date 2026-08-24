#!/usr/bin/env python3
"""
Contract tests for the jobrun repair dispatcher.

The claim under test is the operator's exact worry: "if I have a five-minute job or a
one-minute job ... suddenly we're doing an LLM call every minute." Every leash
gets exercised against real SQLite state, including a simulated 90-second job
failing for hours.

Run: python3 test_jobrun_repair.py
"""

import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'skills' / 'scheduled-job-runner' / 'scripts'))

import jobrun_repair as R  # noqa: E402
from jobrun_severity import (  # noqa: E402
    CRITICAL,
    DEGRADED,
    MONEY_LIVE,
    MONEY_NONE,
    MONEY_PAPER,
)

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def fresh():
    tmp = Path(tempfile.mkdtemp(prefix="jobrun-repair-"))
    return R.connect(tmp / "incidents.db")


def fail_once(conn, fp="fp1", job="j1", reason="failed_unknown",
              sev=DEGRADED, money=MONEY_NONE, err="TypeError: boom"):
    return R.record_failure(
        conn, fingerprint=fp, job_id=job, host="h", reason_code=reason,
        severity=sev, money=money, error_text=err, deployed_sha="abc123",
    )


print("\n== THE HEADLINE: a 90-second job failing for hours ==")
# A fast-cadence trading watchdog: every 90s, 120s timeout.
conn = fresh()
dispatches = 0
for i in range(240):  # 240 ticks at 90s = 6 hours of continuous failure
    row = fail_once(conn, fp="timeout-fp", job="fast-cadence-watchdog",
                    reason="timeout", err="timed out after 120s")
    d = R.decide(conn, row, error_text="timed out after 120s")
    if d.dispatch:
        dispatches += 1
check("6h of a 90s job timing out -> ZERO dispatches", dispatches == 0,
      f"{dispatches} dispatches")
row = conn.execute("SELECT * FROM incidents WHERE fingerprint='timeout-fp'").fetchone()
check("but all 240 occurrences ARE recorded", row["occurrence_count"] == 240,
      row["occurrence_count"])
d = R.decide(conn, row, error_text="timed out")
check("and the reason names the spec, not a bug",
      "timeout" in d.reason.lower() or "cadence" in d.reason.lower(), d.reason)

print("\n== Same job, but a REAL code defect ==")
conn = fresh()
dispatches = 0
for i in range(240):
    row = fail_once(conn, fp="bug-fp", job="fast-job",
                    err="TypeError: unsupported operand")
    d = R.decide(conn, row, error_text="TypeError: unsupported operand")
    if d.dispatch:
        dispatches += 1
        R.dispatch(conn, row, error_text="TypeError", dry_run=True)
check("6h of a real bug on a 90s job -> at most 2 dispatches",
      dispatches <= R.MAX_ATTEMPTS_PER_FINGERPRINT, f"{dispatches}")
# Only ONE lands in a tight loop: after the first dispatch, full-jitter backoff
# holds the condition for 15-60 minutes, and the simulated ticks all occur
# within the same instant. That is the leash working, not a missing dispatch —
# in wall-clock time the second attempt arrives after the backoff expires.
check("240 failures -> exactly 1 LLM call in the first backoff window",
      dispatches == 1, f"{dispatches}")
_r = conn.execute("SELECT * FROM incidents WHERE fingerprint='bug-fp'").fetchone()
check("backoff is what held the rest", _r["next_attempt_at"] is not None)
check("all 240 occurrences still recorded", _r["occurrence_count"] == 240,
      _r["occurrence_count"])

print("\n== Gate: confirmation (one failure is noise) ==")
conn = fresh()
row = fail_once(conn)
d = R.decide(conn, row, error_text="TypeError")
check("first failure does NOT dispatch", not d.dispatch, d.reason)
check("reason says unconfirmed", "unconfirmed" in d.reason)
row = fail_once(conn)
d = R.decide(conn, row, error_text="TypeError")
check("second consecutive failure DOES dispatch", d.dispatch, d.reason)

print("\n== Gate: classification (non-repairable classes) ==")
for reason, err, label in [
    ("timeout", "timed out", "timeout"),
    ("signal", "killed", "signal"),
    ("failed_unknown", "HTTP 429 rate limit exceeded", "rate limit"),
    ("failed_unknown", "401 Unauthorized", "auth"),
    ("failed_unknown", "Connection refused", "network"),
    ("failed_unknown", "missing credential for API", "missing secret"),
]:
    conn = fresh()
    for _ in range(5):
        row = fail_once(conn, reason=reason, err=err)
    d = R.decide(conn, row, error_text=err)
    check(f"{label} never dispatches", not d.dispatch, d.reason)

print("\n== Gate: money and severity ==")
conn = fresh()
for _ in range(5):
    row = fail_once(conn, money=MONEY_LIVE)
d = R.decide(conn, row, error_text="TypeError")
check("live money never auto-repairs", not d.dispatch, d.reason)

conn = fresh()
for _ in range(5):
    row = fail_once(conn, sev=CRITICAL)
d = R.decide(conn, row, error_text="TypeError")
check("critical never auto-repairs", not d.dispatch, d.reason)

conn = fresh()
for _ in range(5):
    row = fail_once(conn, money=MONEY_PAPER)
d = R.decide(conn, row, error_text="TypeError")
check("paper money CAN auto-repair", d.dispatch, d.reason)

print("\n== Gate: per-job budget across DIFFERENT conditions ==")
# A job with many distinct bugs must not bypass the leash via new fingerprints.
conn = fresh()
total = 0
for bug in range(6):
    for _ in range(3):
        row = fail_once(conn, fp=f"bug-{bug}", job="samejob",
                        err=f"TypeError variant {bug}")
    d = R.decide(conn, row, error_text=f"TypeError variant {bug}")
    if d.dispatch:
        R.dispatch(conn, row, error_text="x", dry_run=True)
        total += 1
check("6 distinct bugs on one job capped by job budget",
      total <= R.MAX_ATTEMPTS_PER_JOB, f"{total} dispatched")
check("job budget is what stopped it", total == R.MAX_ATTEMPTS_PER_JOB, f"{total}")

print("\n== Gate: fleet-wide budgets ==")
conn = fresh()
sent = 0
for j in range(20):
    for _ in range(3):
        row = fail_once(conn, fp=f"f{j}", job=f"job{j}", err="TypeError")
    d = R.decide(conn, row, error_text="TypeError")
    if d.dispatch:
        R.dispatch(conn, row, error_text="x", dry_run=True)
        sent += 1
check("20 jobs failing at once respects the hourly cap",
      sent <= R.MAX_STARTS_PER_HOUR, f"{sent} dispatched")
d = R.decide(conn, row, error_text="TypeError")
check("further requests are QUEUED, not dropped silently",
      "queue" in d.reason.lower() or "budget" in d.reason.lower(), d.reason)

print("\n== Gate: backoff with full jitter ==")
conn = fresh()
for _ in range(3):
    row = fail_once(conn)
R.dispatch(conn, row, error_text="x", dry_run=True)
row = conn.execute("SELECT * FROM incidents WHERE fingerprint='fp1'").fetchone()
check("next_attempt_at is set after a dispatch", row["next_attempt_at"])
d = R.decide(conn, row, error_text="TypeError")
check("immediate retry is blocked by backoff",
      not d.dispatch and "backing off" in d.reason, d.reason)
delays = [R.backoff_delay(1).total_seconds() for _ in range(50)]
check("jitter produces a spread, not a constant", len(set(delays)) > 40)
check("jitter never exceeds the cap",
      max(delays) <= R.BACKOFF_CAP_HOURS * 3600)

print("\n== Recovery closes the incident (half-open probe) ==")
conn = fresh()
for _ in range(3):
    fail_once(conn, fp="rfp", job="recovers")
closed = R.record_success(conn, job_id="recovers")
check("a clean run resolves the open condition", closed == ["rfp"], closed)
row = conn.execute("SELECT * FROM incidents WHERE fingerprint='rfp'").fetchone()
check("phase becomes resolved", row["phase"] == "resolved", row["phase"])

print("\n== review_pending blocks re-dispatch ==")
conn = fresh()
for _ in range(3):
    row = fail_once(conn, fp="pr-fp", job="hasapr")
R.dispatch(conn, row, error_text="x", dry_run=True)
conn.execute("UPDATE incidents SET phase='review_pending', pr_url='http://pr/1' "
             "WHERE fingerprint='pr-fp'")
conn.commit()
row = conn.execute("SELECT * FROM incidents WHERE fingerprint='pr-fp'").fetchone()
d = R.decide(conn, row, error_text="TypeError")
check("an open PR stops further dispatches", not d.dispatch, d.reason)
check("reason points at the PR", "review" in d.reason.lower(), d.reason)

print("\n== Escalation is by AGE, not run count ==")
conn = fresh()
for _ in range(500):
    row = fail_once(conn, fp="agefp", job="fast")
check("500 occurrences in the same instant does NOT escalate",
      R.escalation_due(row) is None, R.escalation_due(row))
now = R._now()
check("escalates at 1h", R.escalation_due(row, now + timedelta(hours=1.1)) == "escalate_1h")
check("escalates at 4h", R.escalation_due(row, now + timedelta(hours=4.1)) == "escalate_4h")
check("quarantine due at 72h",
      R.escalation_due(row, now + timedelta(hours=73)) == "quarantine")
R.acknowledge(conn, "agefp")
row = conn.execute("SELECT * FROM incidents WHERE fingerprint='agefp'").fetchone()
check("acknowledgement stops escalation",
      R.escalation_due(row, now + timedelta(hours=99)) is None)

print("\n== Quarantine refuses critical / live money ==")
conn = fresh()
row = fail_once(conn, fp="critfp", job="guard", sev=CRITICAL, money=MONEY_LIVE)
did, msg = R.quarantine(conn, row)
check("critical job is NOT auto-disabled", not did, msg)
check("refusal explains why", "never auto-disabled" in msg)

conn = fresh()
row = fail_once(conn, fp="livefp", job="livejob", money=MONEY_LIVE)
did, _ = R.quarantine(conn, row)
check("live-money job is NOT auto-disabled", not did)

conn = fresh()
row = fail_once(conn, fp="okfp", job="reportgen", money=MONEY_NONE)
did, msg = R.quarantine(conn, row)
check("a report generator CAN be quarantined", did)
check("quarantine message says it stays visible",
      "remind daily" in msg or "stays OPEN" in msg, msg)
row = conn.execute("SELECT * FROM incidents WHERE fingerprint='okfp'").fetchone()
check("quarantined incident is NOT closed", row["phase"] == "quarantined")

print("\n== Dispatch bookkeeping ==")
conn = fresh()
for _ in range(3):
    row = fail_once(conn, fp="dfp", job="dj")
outcome, prompt = R.dispatch(conn, row, spec_path="/s.toml",
                             script_path="/s.py", error_text="TypeError: x",
                             dry_run=True)
check("dry run does not launch anything", outcome == "dry_run")
check("prompt forbids merging", "Never merge" in prompt)
check("prompt forbids editing live scripts", "DO NOT edit the live script" in prompt)
check("prompt demands reproduction first", "REPRODUCE FIRST" in prompt)
check("prompt allows blaming the SPEC", "defect is in the SPEC" in prompt)
check("prompt carries the occurrence count", "occurrences" in prompt)
d = conn.execute("SELECT * FROM dispatches WHERE fingerprint='dfp'").fetchone()
check("dispatch is recorded", d is not None)
check("dispatch consumed a budget slot", d["outcome"] == "dry_run")

print("\n== handle_failure end-to-end, shadow by default ==")
conn = fresh()
res = None
for _ in range(3):
    res = R.handle_failure(
        conn, fingerprint="e2e", job_id="ejob", host="h",
        reason_code="failed_unknown", severity=DEGRADED, money=MONEY_NONE,
        error_text="TypeError: bad", deployed_sha="sha1",
    )
# The FIRST confirmed call dispatches; later calls in the same instant are held
# by backoff. Assert on the dispatch record rather than the last return value.
_d = conn.execute("SELECT * FROM dispatches WHERE fingerprint='e2e'").fetchall()
check("end-to-end dispatched exactly once", len(_d) == 1, f"{len(_d)}")
check("defaults to dry run (shadow mode)", _d and _d[0]["outcome"] == "dry_run")
check("later calls report the backoff, not a dispatch",
      not res["dispatched"] and "backing off" in res["decision"], res["decision"])
check("note is human readable", res["note"].startswith("no repair —"), res["note"])

res2 = R.handle_failure(
    conn, fingerprint="e2e-t", job_id="ejob2", host="h",
    reason_code="timeout", severity=DEGRADED, money=MONEY_NONE,
    error_text="timed out",
)
check("suppression is never silent", res2["note"].startswith("no repair —"), res2["note"])

failed = [(n, d) for n, ok, d in _results if not ok]
print(f"\n{len(_results) - len(failed)}/{len(_results)} passed")
if failed:
    print("\nFAILURES:")
    for n, d in failed:
        print(f"  - {n} {d}")
    sys.exit(1)
print("All repair dispatcher tests passed.")
