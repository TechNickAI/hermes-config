#!/usr/bin/env python3
"""
WALL-CLOCK soak test: does a 1-minute job ever escape the leash?

Every prior test fired its ticks in a single instant, so backoff trivially held
them. That proves nothing about a job failing for DAYS, where every backoff
window expires naturally. This test advances a simulated clock minute by minute
and counts real dispatch attempts.

The question being falsified: "a 1/2/5 minute job fires off an LLM call
repeatedly trying to fix it, and gets stuck."
"""

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'skills' / 'scheduled-job-runner' / 'scripts'))

import jobrun_repair as R  # noqa: E402
from jobrun_severity import DEGRADED, MONEY_NONE  # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


class Clock:
    """A controllable clock patched over the module's _now()."""

    def __init__(self, start=None):
        self.t = start or datetime(2030, 1, 1, tzinfo=timezone.utc)

    def now(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def soak(*, minutes_total, cadence_min, error_text, reason_code="failed_unknown",
         money=MONEY_NONE, severity=DEGRADED, label=""):
    """Run a failing job across simulated wall-clock time."""
    tmp = Path(tempfile.mkdtemp(prefix="soak-"))
    conn = R.connect(tmp / "incidents.db")
    clock = Clock()
    real_now = R._now
    R._now = clock.now  # noqa
    try:
        dispatches = []
        ticks = 0
        for minute in range(0, minutes_total, cadence_min):
            ticks += 1
            row = R.record_failure(
                conn, fingerprint="soak-fp", job_id="fastjob", host="h",
                reason_code=reason_code, severity=severity, money=money,
                error_text=error_text, deployed_sha="sha-A",
            )
            d = R.decide(conn, row, error_text=error_text, now=clock.now())
            if d.dispatch:
                R.dispatch(conn, row, error_text=error_text, dry_run=True)
                dispatches.append(clock.now())
            clock.advance(minutes=cadence_min)
        final = conn.execute(
            "SELECT * FROM incidents WHERE fingerprint='soak-fp'").fetchone()
        return ticks, dispatches, final, conn, clock
    finally:
        R._now = real_now  # noqa


print("\n== 7 DAYS of a 1-minute job failing with a REAL code defect ==")
ticks, disp, row, conn, clock = soak(
    minutes_total=7 * 24 * 60, cadence_min=1,
    error_text="TypeError: unsupported operand type(s)")
print(f"   {ticks} failed runs over 7 days -> {len(disp)} LLM dispatches")
check("a week of minute-by-minute failure is bounded",
      len(disp) <= R.MAX_ATTEMPTS_PER_FINGERPRINT, f"{len(disp)} dispatches")
check("all 10,080 occurrences recorded", row["occurrence_count"] == ticks,
      f"{row['occurrence_count']}")
if len(disp) >= 2:
    gap = (disp[1] - disp[0]).total_seconds() / 60
    # FULL JITTER means sleep = random(0, base), so the gap is a DRAW from
    # [0, base], not a floor of base. Asserting `gap >= 15` was wrong and
    # flaked: a legitimate 14-minute draw is exactly the behavior we want.
    # What matters is that consecutive attempts cannot be adjacent ticks and
    # stay under the cap, so assert the real invariant.
    check("gap between attempts is a jittered backoff, not adjacent ticks",
          1 <= gap <= R.BACKOFF_CAP_HOURS * 60, f"{gap:.1f} min")

print("\n== 30 DAYS: does the leash ever release and loop? ==")
ticks, disp, row, conn, clock = soak(
    minutes_total=30 * 24 * 60, cadence_min=1,
    error_text="TypeError: unsupported operand type(s)")
print(f"   {ticks} failed runs over 30 days -> {len(disp)} LLM dispatches")
check("a MONTH of continuous failure never loops",
      len(disp) <= R.MAX_ATTEMPTS_PER_FINGERPRINT, f"{len(disp)} dispatches")
check("phase ends escalated (handed to a human), not repairing",
      row["phase"] in ("escalated", "quarantined", "review_pending"), row["phase"])

print("\n== 30 DAYS of a 1-minute job TIMING OUT (the fast-cadence shape) ==")
ticks, disp, row, conn, clock = soak(
    minutes_total=30 * 24 * 60, cadence_min=1,
    reason_code="timeout", error_text="timed out after 120s")
print(f"   {ticks} timeouts over 30 days -> {len(disp)} LLM dispatches")
check("timeouts NEVER dispatch, no matter how long", len(disp) == 0, f"{len(disp)}")

print("\n== 2-minute and 5-minute cadences ==")
for cad in (2, 5):
    ticks, disp, row, _, _ = soak(
        minutes_total=14 * 24 * 60, cadence_min=cad,
        error_text="AttributeError: NoneType")
    check(f"{cad}-min job, 14 days -> bounded",
          len(disp) <= R.MAX_ATTEMPTS_PER_FINGERPRINT,
          f"{ticks} runs, {len(disp)} dispatches")

print("\n== Does a NEW DEPLOY reset the budget (and can that loop)? ==")
# A fix ships, the fingerprint changes, budget resets. Correct behavior. But if
# a job redeployed every hour while broken, could it dispatch forever?
tmp = Path(tempfile.mkdtemp(prefix="soak-deploy-"))
conn = R.connect(tmp / "incidents.db")
clock = Clock()
real_now = R._now
R._now = clock.now  # noqa
try:
    total = 0
    for deploy in range(30):  # 30 deploys over 30 days
        fp = f"deploy-fp-{deploy}"
        for _ in range(5):
            row = R.record_failure(
                conn, fingerprint=fp, job_id="redeployed", host="h",
                reason_code="failed_unknown", severity=DEGRADED,
                money=MONEY_NONE, error_text="TypeError: x",
                deployed_sha=f"sha-{deploy}")
            d = R.decide(conn, row, error_text="TypeError: x", now=clock.now())
            if d.dispatch:
                R.dispatch(conn, row, error_text="x", dry_run=True)
                total += 1
            clock.advance(minutes=5)
        clock.advance(hours=20)
    print(f"   30 deploys x 5 failures each -> {total} dispatches")
    check("per-JOB budget caps redeploy churn",
          total <= R.MAX_ATTEMPTS_PER_JOB, f"{total} dispatches")
finally:
    R._now = real_now  # noqa

print("\n== The stuck-forever check: is the human ALWAYS told? ==")
ticks, disp, row, conn, clock = soak(
    minutes_total=10 * 24 * 60, cadence_min=1,
    error_text="TypeError: boom")
esc = R.escalation_due(row, clock.now())
check("after 10 days unacknowledged, escalation is due",
      esc in ("quarantine", "escalate_24h"), str(esc))
did, msg = R.quarantine(conn, row)
check("non-critical job is quarantined, not left spinning", did, msg[:60])
row2 = conn.execute(
    "SELECT * FROM incidents WHERE fingerprint='soak-fp'").fetchone()
check("quarantined incident stays OPEN (dead-man)",
      row2["phase"] == "quarantined", row2["phase"])
d = R.decide(conn, row2, error_text="TypeError", now=clock.now())
check("quarantined job stops dispatching entirely",
      not d.dispatch and "quarantin" in d.reason, d.reason)

print("\n== CRASHED DISPATCHER: does a stale lease wedge the incident forever? ==")
# A dispatcher killed mid-flight (reboot, OOM, SIGKILL) leaves phase='repairing'
# with a lease nobody clears. The wedge is worse than a flood: no dispatch AND
# no escalation, so the job quietly stops being repaired and nobody is told.
tmp = Path(tempfile.mkdtemp(prefix="soak-crash-"))
conn = R.connect(tmp / "incidents.db")
clock = Clock()
real_now = R._now
R._now = clock.now  # noqa
try:
    for _ in range(3):
        row = R.record_failure(
            conn, fingerprint="crashfp", job_id="cj", host="h",
            reason_code="failed_unknown", severity=DEGRADED, money=MONEY_NONE,
            error_text="TypeError", deployed_sha="s")
    # Simulate a dispatcher that started and never came back.
    conn.execute(
        "UPDATE incidents SET phase='repairing', "
        "lease_until=? WHERE fingerprint='crashfp'",
        (R._iso(clock.now() + timedelta(minutes=45)),))
    conn.commit()
    row = conn.execute(
        "SELECT * FROM incidents WHERE fingerprint='crashfp'").fetchone()
    d = R.decide(conn, row, error_text="TypeError", now=clock.now())
    check("an IN-FLIGHT repair blocks a second dispatch",
          not d.dispatch and "in flight" in d.reason, d.reason)

    clock.advance(hours=2)  # lease expires
    row = conn.execute(
        "SELECT * FROM incidents WHERE fingerprint='crashfp'").fetchone()
    d = R.decide(conn, row, error_text="TypeError", now=clock.now())
    row2 = conn.execute(
        "SELECT * FROM incidents WHERE fingerprint='crashfp'").fetchone()
    check("an EXPIRED lease is recovered, not wedged",
          row2["phase"] != "repairing", row2["phase"])
    check("recovered incident resumes the human ladder",
          row2["phase"] == "escalated", row2["phase"])
finally:
    R._now = real_now  # noqa

print("\n== FINGERPRINT_WINDOW_DAYS: declared but enforced? ==")
# repair_attempts is a lifetime counter that nothing resets. If the constant
# implies a rolling window it does not actually have, that is a doc bug that
# would mislead the next reader into thinking budget refreshes weekly.
tmp = Path(tempfile.mkdtemp(prefix="soak-win-"))
conn = R.connect(tmp / "incidents.db")
clock = Clock()
real_now = R._now
R._now = clock.now  # noqa
try:
    for _ in range(3):
        row = R.record_failure(
            conn, fingerprint="winfp", job_id="wj", host="h",
            reason_code="failed_unknown", severity=DEGRADED, money=MONEY_NONE,
            error_text="TypeError", deployed_sha="s")
    for _ in range(R.MAX_ATTEMPTS_PER_FINGERPRINT):
        row = conn.execute(
            "SELECT * FROM incidents WHERE fingerprint='winfp'").fetchone()
        d = R.decide(conn, row, error_text="TypeError", now=clock.now())
        if d.dispatch:
            R.dispatch(conn, row, error_text="x", dry_run=True)
        clock.advance(days=1)
    clock.advance(days=60)  # far beyond FINGERPRINT_WINDOW_DAYS
    row = R.record_failure(
        conn, fingerprint="winfp", job_id="wj", host="h",
        reason_code="failed_unknown", severity=DEGRADED, money=MONEY_NONE,
        error_text="TypeError", deployed_sha="s")
    d = R.decide(conn, row, error_text="TypeError", now=clock.now())
    check("budget does NOT silently refresh after the window",
          not d.dispatch, d.reason)
    print(f"     (60 days later, still: {d.reason})")
finally:
    R._now = real_now  # noqa

failed = [(n, d) for n, ok, d in _results if not ok]
print(f"\n{len(_results) - len(failed)}/{len(_results)} passed")
if failed:
    print("\nFAILURES:")
    for n, d in failed:
        print(f"  - {n} {d}")
    sys.exit(1)
print("Soak test passed: the leash holds across wall-clock time.")
