#!/usr/bin/env python3
"""
jobrun repair dispatcher (v2) — bounded, auditable LLM self-repair.

WHAT THIS IS FOR
----------------
The operator was tired of finding failing cron jobs and then having to ask for a
fix manually. The catch: "if I have a five-minute job or a one-minute job ...
suddenly we're doing an LLM call every minute."

So this module exists to answer exactly one question, safely and repeatedly:

    A job just failed. Do we send a model at it, and if so, under what leash?

THE MEASUREMENT THAT SHAPED IT
-------------------------------
A watchdog on a short schedule produced dozens of timeout failures in one day.
A naive "failed, dispatch a fixer" design would have opened a model session for
every one of them, against a job that was healthy the whole time and failing
only because an upstream dependency was down.

The tests go further and simulate a 1-minute job, because the worry worth
designing against is a job on a 1/2/5-minute schedule, and the tightest cadence
is the one that has to hold.

Under this dispatcher that job gets **zero** dispatches, because timeouts are
not code defects (see ``jobrun_severity.repair_eligible``). The replay across
the real fleet ledgers is: 43 raw failures -> 12 distinct conditions -> 7
repair-eligible -> capped by budget. That ratio, roughly 6:1 before budgets and
far higher after, is the whole point.

STATE LIVES IN SQLITE, NOT JSON
--------------------------------
Never use JSON for agent-facing state. Concurrent cron ticks
would race a JSON file, and the ledger already proved that lesson once.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
------------------------------------------
It does not merge, deploy, restart anything, or edit a live script in place.
It requests a PR. Prior art is unanimous here: Sentry Seer, Copilot Autofix,
Dependabot, and Renovate all stop at a reviewable PR, and ITBench measured the
best agents resolving 13.8% of real SRE scenarios (arXiv:2502.05352). A 13.8%
solve rate is useful triage and an unacceptable auto-merge.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jobrun_severity import (  # noqa: E402
    CRITICAL,
    MONEY_LIVE,
    repair_eligible,
)

# --------------------------------------------------------------------------
# Budgets. Every one of these is a leash the operator asked for.
# --------------------------------------------------------------------------
# Per-condition: how many times we will ever try to fix THIS bug.
MAX_ATTEMPTS_PER_FINGERPRINT = 2
FINGERPRINT_WINDOW_DAYS = 7

# Per-job: stops one pathological job from eating the whole fleet budget.
MAX_ATTEMPTS_PER_JOB = 3
JOB_WINDOW_DAYS = 30

# Fleet-wide rate limits. Modeled on Renovate's prHourlyLimit /
# prConcurrentLimit and Dependabot's default of five open PRs — the same
# problem (bounding machine-generated patches a human must review).
MAX_CONCURRENT = 2
MAX_STARTS_PER_HOUR = 4
MAX_STARTS_PER_DAY = 12
MAX_OPEN_PRS = 10

# Confirmation: how many matching failures before we believe it is real.
# One failure is noise. This is the anti-flap gate.
CONFIRM_CONSECUTIVE = 2

# Backoff between attempts on the same condition, with FULL JITTER.
# Full jitter (sleep = random(0, base)) rather than plain exponential, because
# a fleet-wide dependency break would otherwise synchronize every job's retry
# into one thundering herd. AWS's jitter study is the source.
BACKOFF_BASE_MINUTES = 15
BACKOFF_CAP_HOURS = 24

# Per-attempt ceilings handed to the agent itself.
ATTEMPT_MAX_MINUTES = 45

# Escalation ladder, in hours since the condition was first seen.
ESCALATE_AT_HOURS = (1, 4, 24)
QUARANTINE_AFTER_HOURS = 72

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    fingerprint      TEXT PRIMARY KEY,
    job_id           TEXT NOT NULL,
    host             TEXT NOT NULL,
    reason_code      TEXT,
    severity         TEXT,
    money            TEXT,
    phase            TEXT NOT NULL DEFAULT 'observing',
    occurrence_count INTEGER NOT NULL DEFAULT 0,
    consecutive      INTEGER NOT NULL DEFAULT 0,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    repair_attempts  INTEGER NOT NULL DEFAULT 0,
    last_attempt_at  TEXT,
    next_attempt_at  TEXT,
    lease_until      TEXT,
    pr_url           TEXT,
    acknowledged_at  TEXT,
    notified_at      TEXT,
    notify_status    TEXT,
    announced_escalation TEXT,
    last_error       TEXT,
    deployed_sha     TEXT,
    quarantined_at   TEXT
);
CREATE TABLE IF NOT EXISTS dispatches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint  TEXT NOT NULL,
    job_id       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    outcome      TEXT,
    detail       TEXT,
    cron_job_id  TEXT
);
CREATE INDEX IF NOT EXISTS ix_disp_started ON dispatches(started_at);
CREATE INDEX IF NOT EXISTS ix_inc_job ON incidents(job_id);
"""


def _home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _db_path() -> Path:
    # An explicit override exists so a self-test or fixture run can redirect the
    # incident store the same way it redirects the ledger. Without it, tests
    # whose fixtures deliberately fail write permanent "open incidents" into the
    # production database of whatever profile happened to run them.
    override = os.environ.get("JOBRUN_INCIDENT_DB")
    if override:
        p = Path(override)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    d = _home() / "jobstate"
    d.mkdir(parents=True, exist_ok=True)
    return d / "incidents.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or _db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL so a long-running dispatch never blocks a cron tick recording a
    # failure. Concurrent ticks are the normal case, not the exception.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    # CREATE TABLE IF NOT EXISTS does not add columns to an existing incident
    # database. The escalation announcement watermark was added after the first
    # rollout, so migrate in place rather than silently treating every reached
    # milestone as new forever.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(incidents)")}
    if "announced_escalation" not in cols:
        conn.execute("ALTER TABLE incidents ADD COLUMN announced_escalation TEXT")
        conn.commit()
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def backoff_delay(attempt: int) -> timedelta:
    """Full jitter: random(0, min(cap, base * 4**attempt))."""
    base = BACKOFF_BASE_MINUTES * (4 ** max(0, attempt))
    capped = min(base, BACKOFF_CAP_HOURS * 60)
    return timedelta(minutes=random.uniform(0, capped))


@dataclass
class Decision:
    """Why we did or did not send a model at this failure."""

    dispatch: bool
    reason: str
    phase: str
    occurrence_count: int = 0
    attempts_used: int = 0
    next_attempt_at: str | None = None

    def as_note(self) -> str:
        """One line for the incident card. Never silent."""
        if self.dispatch:
            return f"repair dispatched (attempt {self.attempts_used})"
        return f"no repair — {self.reason}"


def record_failure(
    conn: sqlite3.Connection,
    *,
    fingerprint: str,
    job_id: str,
    host: str,
    reason_code: str,
    severity: str,
    money: str,
    error_text: str = "",
    deployed_sha: str | None = None,
) -> sqlite3.Row:
    """
    Upsert the incident for this CONDITION and return its current state.

    ``occurrence_count`` counts how many times this condition happened;
    ``consecutive`` counts unbroken repeats and is what the confirmation gate
    reads. They differ after a recovery, which is the point: a flapping job and
    a persistently broken one are different animals.
    """
    now = _iso(_now())
    cur = conn.execute(
        "SELECT * FROM incidents WHERE fingerprint = ?", (fingerprint,)
    )
    row = cur.fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO incidents (fingerprint, job_id, host, reason_code, "
            "severity, money, phase, occurrence_count, consecutive, "
            "first_seen_at, last_seen_at, last_error, deployed_sha) "
            "VALUES (?,?,?,?,?,?,'observing',1,1,?,?,?,?)",
            (fingerprint, job_id, host, reason_code, severity, money,
             now, now, error_text[:2000], deployed_sha),
        )
    else:
        conn.execute(
            "UPDATE incidents SET occurrence_count = occurrence_count + 1, "
            "consecutive = consecutive + 1, last_seen_at = ?, severity = ?, "
            "last_error = ?, deployed_sha = ? WHERE fingerprint = ?",
            (now, severity, error_text[:2000], deployed_sha, fingerprint),
        )
    conn.commit()
    return conn.execute(
        "SELECT * FROM incidents WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()


def record_success(conn: sqlite3.Connection, *, job_id: str) -> list:
    """
    A clean run closes every open condition for that job.

    Returns the fingerprints that just recovered, so the caller can report
    "this fixed itself" rather than leaving a stale incident open forever.
    Half-open in circuit-breaker terms: the scheduled run IS the probe.

    THE SUBTLE BUG: an earlier version only reset rows whose phase was not
    already 'resolved'. A job alternating fail/success therefore kept its
    `consecutive` counter from a previous cycle — the row was already
    'resolved', so the success was a no-op — and two failures separated by a
    healthy run eventually satisfied the two-consecutive gate and dispatched a
    repair agent against a job that works half the time. `consecutive` must be
    zeroed on EVERY success regardless of phase; only the phase transition is
    conditional.
    """
    rows = conn.execute(
        "SELECT fingerprint FROM incidents WHERE job_id = ? "
        "AND phase NOT IN ('resolved','quarantined')",
        (job_id,),
    ).fetchall()
    # Always reset the streak, even for rows already marked resolved.
    conn.execute(
        "UPDATE incidents SET consecutive = 0 WHERE job_id = ? "
        "AND phase != 'quarantined'",
        (job_id,),
    )
    if rows:
        conn.execute(
            "UPDATE incidents SET phase='resolved' "
            "WHERE job_id = ? AND phase NOT IN ('resolved','quarantined')",
            (job_id,),
        )
    conn.commit()
    return [r["fingerprint"] for r in rows]


def _count_since(conn: sqlite3.Connection, hours: float) -> int:
    since = _iso(_now() - timedelta(hours=hours))
    return conn.execute(
        "SELECT COUNT(*) c FROM dispatches WHERE started_at >= ?", (since,)
    ).fetchone()["c"]


def _concurrent(conn: sqlite3.Connection) -> int:
    """
    Dispatches that started but never finished, minus expired leases.

    A crashed dispatcher must not permanently consume a concurrency slot; the
    lease expiry is what makes this self-healing rather than self-wedging.
    """
    cutoff = _iso(_now() - timedelta(minutes=ATTEMPT_MAX_MINUTES))
    return conn.execute(
        "SELECT COUNT(*) c FROM dispatches WHERE finished_at IS NULL "
        "AND started_at >= ?", (cutoff,)
    ).fetchone()["c"]


def _open_prs(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM incidents WHERE pr_url IS NOT NULL "
        "AND phase = 'review_pending'"
    ).fetchone()["c"]


def decide(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    error_text: str = "",
    now: datetime | None = None,
) -> Decision:
    """
    The full gate. Every early return is a leash, in cheapest-check-first order.

    Order matters: classification is free, budget queries hit the db. And the
    classification gate is deliberately FIRST because it is the one that
    eliminated nearly every failure of the fast-cadence job.
    """
    now = now or _now()
    phase = row["phase"]
    occ = row["occurrence_count"]

    if phase == "quarantined":
        return Decision(False, "job is quarantined", phase, occ)
    if phase == "review_pending":
        return Decision(
            False, f"a fix is already awaiting review ({row['pr_url'] or 'PR open'})",
            phase, occ, row["repair_attempts"])

    # STALE LEASE RECOVERY. A dispatcher killed mid-flight (host reboot, OOM,
    # SIGKILL) leaves phase='repairing' with a lease that never clears. Without
    # this, the incident is wedged forever: it never dispatches again AND never
    # escalates, so the job silently stops being repaired and nobody is told.
    # That is the worst outcome available here — quieter than a flood and far
    # harder to notice. Settle it to 'escalated' so the human ladder resumes.
    if phase == "repairing":
        lease = _parse(row["lease_until"])
        if lease is None or now >= lease:
            conn.execute(
                "UPDATE incidents SET phase='escalated', lease_until=NULL "
                "WHERE fingerprint=?", (row["fingerprint"],),
            )
            conn.commit()
            phase = "escalated"
        else:
            return Decision(
                False,
                f"a repair is already in flight (lease until {row['lease_until']})",
                phase, occ, row["repair_attempts"])

    # --- Gate 1: is this even a code defect? ------------------------------
    ok, why = repair_eligible(
        reason_code=row["reason_code"] or "",
        error_text=error_text,
        money=row["money"] or "",
        severity=row["severity"] or "",
    )
    if not ok:
        return Decision(False, why, phase, occ)

    # --- Gate 2: confirmation. One failure is noise. ----------------------
    if row["consecutive"] < CONFIRM_CONSECUTIVE:
        return Decision(
            False,
            f"unconfirmed ({row['consecutive']}/{CONFIRM_CONSECUTIVE} "
            f"consecutive) — a single failure is not an incident",
            phase, occ)

    # --- Gate 3: per-condition attempt budget ----------------------------
    if row["repair_attempts"] >= MAX_ATTEMPTS_PER_FINGERPRINT:
        return Decision(
            False,
            f"repair budget exhausted for this condition "
            f"({row['repair_attempts']}/{MAX_ATTEMPTS_PER_FINGERPRINT}) — "
            f"needs a human",
            "escalated", occ, row["repair_attempts"])

    # --- Gate 4: backoff --------------------------------------------------
    nxt = _parse(row["next_attempt_at"])
    if nxt and now < nxt:
        return Decision(
            False, f"backing off until {row['next_attempt_at']}",
            phase, occ, row["repair_attempts"], row["next_attempt_at"])

    # --- Gate 5: per-JOB budget ------------------------------------------
    since = _iso(now - timedelta(days=JOB_WINDOW_DAYS))
    used = conn.execute(
        "SELECT COUNT(*) c FROM dispatches WHERE job_id = ? AND started_at >= ?",
        (row["job_id"], since),
    ).fetchone()["c"]
    if used >= MAX_ATTEMPTS_PER_JOB:
        return Decision(
            False,
            f"job-level budget exhausted ({used}/{MAX_ATTEMPTS_PER_JOB} in "
            f"{JOB_WINDOW_DAYS}d) — this job needs redesign, not another patch",
            "escalated", occ, row["repair_attempts"])

    # --- Gate 6: fleet budgets. Queue, never borrow forward. -------------
    if _concurrent(conn) >= MAX_CONCURRENT:
        return Decision(False, "fleet concurrency limit reached, queued",
                        phase, occ, row["repair_attempts"])
    if _count_since(conn, 1) >= MAX_STARTS_PER_HOUR:
        return Decision(False, "fleet hourly repair budget reached, queued",
                        phase, occ, row["repair_attempts"])
    if _count_since(conn, 24) >= MAX_STARTS_PER_DAY:
        return Decision(False, "fleet daily repair budget reached, queued",
                        phase, occ, row["repair_attempts"])
    if _open_prs(conn) >= MAX_OPEN_PRS:
        return Decision(
            False,
            f"{MAX_OPEN_PRS} auto-repair PRs already await review — "
            f"clearing the queue matters more than opening another",
            phase, occ, row["repair_attempts"])

    return Decision(True, "confirmed code defect within budget", "repairing",
                    occ, row["repair_attempts"] + 1)


REPAIR_PROMPT = """\
A scheduled job on this machine is failing. Investigate and propose a fix.

    job_id      {job_id}
    host        {host}
    condition   {reason_code}
    occurrences {occurrence_count} since {first_seen_at}
    severity    {severity}
    money       {money}
    commit      {deployed_sha}
    spec        {spec_path}
    script      {script_path}
    log         {log_path}

Recent error output:
{error_text}

RULES, in priority order:

1. REPRODUCE FIRST. Run the job's script yourself and observe the failure
   before changing anything. If you cannot reproduce it, say so and stop —
   an unreproduced fix is a guess.
2. DO NOT edit the live script in place. Make your change on a branch and open
   a PR. Never merge it. Never deploy. Never restart a service.
3. DO NOT modify the job's schedule, its cron entry, or any other job.
4. If the real defect is in the SPEC rather than the code (a timeout shorter
   than the job's own runtime, a cadence faster than its duration, a wrong
   working directory), say that plainly and propose the spec change. Do not
   rewrite working code to fit a broken spec.
5. If the failure is environmental (a missing credential, an upstream outage,
   a full disk), report it and stop. You cannot patch those.
6. Add or update a test that FAILS before your change and PASSES after. If the
   project has no test harness for this, say so rather than inventing one.
7. Keep the diff small. If the fix needs more than ~20 files or a redesign,
   stop and write up what you found instead.
8. You have {max_minutes} minutes. If you are not converging, stop and report.

DECLARE YOUR OUTCOME. End your report with EXACTLY ONE of these lines, alone on
the last line. This is parsed, and it is the difference between "an agent ran"
and "something was actually fixed" — a run that investigates carefully and
changes nothing is a legitimate result, but it must never be recorded as a
repair:

    REPAIR-OUTCOME: patched <pr-url-or-branch>   you changed code and opened a PR
    REPAIR-OUTCOME: spec-defect <one line>       the spec is wrong, not the code
    REPAIR-OUTCOME: environmental <one line>     credential/outage/disk, unpatchable
    REPAIR-OUTCOME: not-reproducible <one line>  it did not fail when you ran it
    REPAIR-OUTCOME: declined <one line>          you deliberately made no change

Finish with a short report: what broke, why, what you changed, the PR URL, and
what you could not verify.
"""


def build_prompt(row: sqlite3.Row, *, spec_path="", script_path="",
                 log_path="", error_text="") -> str:
    return REPAIR_PROMPT.format(
        job_id=row["job_id"], host=row["host"],
        reason_code=row["reason_code"] or "unknown",
        occurrence_count=row["occurrence_count"],
        first_seen_at=row["first_seen_at"],
        severity=row["severity"], money=row["money"] or "none",
        deployed_sha=row["deployed_sha"] or "unknown",
        spec_path=spec_path or "(unknown)",
        script_path=script_path or "(unknown)",
        log_path=log_path or "(none)",
        error_text=(error_text or "(none captured)")[:3000],
        max_minutes=ATTEMPT_MAX_MINUTES,
    )


def _classify_agent_report(out: str) -> str:
    """
    What did the repair agent actually DO?

    A finished process is not a fix. The first live dispatch proved it: the
    agent investigated correctly, found the failing script had no repository to
    open a PR against, and deliberately changed nothing — and that was recorded
    as `completed`, which reads as "repaired" to every consumer downstream. An
    honest refusal and a landed patch must not share a label.

    Only an explicit `patched` declaration counts as a repair attempt that
    produced a change. Everything else — including a missing declaration — is
    recorded as what it is, and none of them route to `review_pending`, because
    there is no change to review.
    """
    declared = None
    for line in reversed((out or "").strip().splitlines()):
        line = line.strip()
        if line.upper().startswith("REPAIR-OUTCOME:"):
            declared = line.split(":", 1)[1].strip().split()[0].lower()
            break
    if declared in ("patched", "spec-defect", "environmental",
                    "not-reproducible", "declined"):
        return "patched" if declared == "patched" else declared
    # No declaration. The agent ran and told us nothing parseable, so we must
    # not assume it fixed anything.
    return "no_declaration"


def _hermes_cli() -> str:
    cli = _home().parent / "hermes-agent" / "hermes"
    if cli.exists():
        return str(cli)
    return shutil.which("hermes") or "hermes"


def dispatch(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    spec_path="", script_path="", log_path="", error_text="",
    profile: str | None = None,
    dry_run: bool = False,
) -> tuple[str, str]:
    """
    Send exactly one repair agent at this condition.

    Returns (outcome, detail). Records the dispatch BEFORE launching so a crash
    mid-flight still consumes its budget slot — failing closed, because the
    alternative is an unbounded retry loop, which is the exact thing the operator asked
    to prevent.

    The one-shot agent runs via ``hermes -z``, which does not touch the cron
    schedule at all. That matters: creating a cron job to fix a cron job is how
    you get a repair loop that outlives the bug.
    """
    now = _iso(_now())
    cur = conn.execute(
        "INSERT INTO dispatches (fingerprint, job_id, started_at) VALUES (?,?,?)",
        (row["fingerprint"], row["job_id"], now),
    )
    disp_id = cur.lastrowid
    attempts = row["repair_attempts"] + 1
    conn.execute(
        "UPDATE incidents SET phase='repairing', repair_attempts=?, "
        "last_attempt_at=?, next_attempt_at=?, lease_until=? WHERE fingerprint=?",
        (attempts, now,
         _iso(_now() + backoff_delay(attempts)),
         _iso(_now() + timedelta(minutes=ATTEMPT_MAX_MINUTES)),
         row["fingerprint"]),
    )
    conn.commit()

    prompt = build_prompt(row, spec_path=spec_path, script_path=script_path,
                          log_path=log_path, error_text=error_text)

    if dry_run:
        conn.execute(
            "UPDATE dispatches SET finished_at=?, outcome='dry_run', detail=? "
            "WHERE id=?",
            (_iso(_now()), f"would run {len(prompt)} char prompt", disp_id),
        )
        # SETTLE THE PHASE even in shadow mode. Found by the wall-clock soak
        # test during a recent incident: the early return skipped the terminal phase update,
        # so a shadow-mode incident sat in 'repairing' forever — showing an
        # operator an in-flight repair that was not running and never would.
        # A stuck phase is precisely the "getting stuck" failure the operator asked
        # about, and it appeared in the SHADOW path, the one we ship first.
        conn.execute(
            "UPDATE incidents SET phase='escalated', lease_until=NULL "
            "WHERE fingerprint=?", (row["fingerprint"],),
        )
        conn.commit()
        return "dry_run", prompt

    argv = [_hermes_cli()]
    if profile:
        # -p is REQUIRED. HERMES_PROFILE is silently ignored, and a run without
        # it reads and rewrites the CALLING profile's files while reporting the
        # target's name.
        argv += ["-p", profile]
    argv += ["-z", prompt]

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=ATTEMPT_MAX_MINUTES * 60,
        )
        out = (proc.stdout or "")[-4000:]
        # A one-shot that prints argparse usage did not run the prompt.
        if out.lstrip().startswith("usage:"):
            outcome, detail = "invocation_error", out[:500]
        elif proc.returncode != 0:
            outcome, detail = "agent_failed", (proc.stderr or out)[-500:]
        else:
            outcome, detail = _classify_agent_report(out), out[-1500:]
    except subprocess.TimeoutExpired:
        outcome, detail = "timeout", f"exceeded {ATTEMPT_MAX_MINUTES}m"
    except (OSError, ValueError) as exc:
        outcome, detail = "dispatch_error", str(exc)[:500]

    conn.execute(
        "UPDATE dispatches SET finished_at=?, outcome=?, detail=? WHERE id=?",
        (_iso(_now()), outcome, detail, disp_id),
    )
    # A completed agent does NOT mean a fixed job. Only a later clean run
    # proves that. Park it for review rather than declaring victory.
    #
    # And only a run that actually PATCHED something has anything to review.
    # An agent that correctly declined, or found the defect in the spec, or hit
    # an environmental cause, produced no change — routing those to
    # review_pending invents a pending review that does not exist and hides a
    # condition that still needs a human.
    conn.execute(
        "UPDATE incidents SET phase=?, lease_until=NULL WHERE fingerprint=?",
        ("review_pending" if outcome == "patched" else "escalated",
         row["fingerprint"]),
    )
    conn.commit()
    return outcome, detail


_ESCALATION_RANK = {
    None: 0,
    "escalate_1h": 1,
    "escalate_4h": 2,
    "escalate_24h": 3,
    "quarantine": 4,
}


def escalation_due(row: sqlite3.Row, now: datetime | None = None) -> str | None:
    """
    What to tell the human, based on the AGE of the condition, not run count.

    Age rather than occurrences is deliberate: a 90-second job would otherwise
    hit "escalate at 3 occurrences" four minutes in, which is how a system
    trains its owner to ignore it.
    """
    now = now or _now()
    if row["acknowledged_at"]:
        return None
    first = _parse(row["first_seen_at"])
    if not first:
        return None
    age_h = (now - first).total_seconds() / 3600.0

    due = None
    if age_h >= QUARANTINE_AFTER_HOURS and row["phase"] != "quarantined":
        due = "quarantine"
    else:
        for h in reversed(ESCALATE_AT_HOURS):
            if age_h >= h:
                due = f"escalate_{h}h"
                break

    # A threshold is an edge, not a level. Returning `escalate_1h` on every
    # five-minute run from hour 1 through hour 4 recreates the flood with a
    # different headline. It remains pending until a notification is confirmed
    # sent, then stays quiet until the next threshold advances.
    announced = row["announced_escalation"]
    if _ESCALATION_RANK.get(due, 0) <= _ESCALATION_RANK.get(announced, 0):
        return None
    return due


def record_notification(
    conn: sqlite3.Connection,
    *,
    fingerprint: str,
    status: str,
    escalation: str | None = None,
) -> sqlite3.Row | None:
    """Record whether the human notification actually left this process.

    A failed send must not consume either the incident's first page or an
    escalation milestone. Only `sent` advances the announcement watermark;
    every failure stays eligible for retry on the next scheduled run.
    """
    now = _iso(_now())
    if status == "sent":
        conn.execute(
            "UPDATE incidents SET notify_status=?, notified_at=?, "
            "announced_escalation=COALESCE(?, announced_escalation) "
            "WHERE fingerprint=?",
            (status, now, escalation, fingerprint),
        )
    else:
        conn.execute(
            "UPDATE incidents SET notify_status=? WHERE fingerprint=?",
            (status, fingerprint),
        )
    conn.commit()
    return conn.execute(
        "SELECT * FROM incidents WHERE fingerprint=?", (fingerprint,)
    ).fetchone()


def _pause_scheduled_job(job_id: str, reason: str) -> tuple[bool, str]:
    """
    Actually stop the scheduled job via the CLI. Returns (stopped, detail).

    THE BUG THIS FIXES: the first cut flipped a row in SQLite and returned a
    message saying the job was paused. It was not. The scheduler kept firing it
    at full cadence while repair decisions were permanently suppressed as
    "quarantined" — the operator was told a job had stopped while it kept
    running, which is a worse state than either honest outcome.
    """
    cli = shutil.which("hermes")
    if not cli:
        home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
        cand = home.parent / "hermes-agent" / "hermes"
        cli = str(cand) if cand.exists() else None
    if not cli:
        return False, "hermes CLI not found; cannot pause"
    try:
        proc = subprocess.run(
            [cli, "cronjob", "pause", job_id, "--reason", reason],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode == 0:
            return True, "paused via hermes cronjob pause"
        return False, (proc.stderr or proc.stdout or "pause failed")[-200:]
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)[:200]


def quarantine(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[bool, str]:
    """
    FAIL-VISIBLE quarantine. Stop the work, never the alarm.

    A critical or live-money job is NEVER auto-quarantined. Auto-disabling the
    thing that guards money converts a loud failure into silent non-execution —
    the one condition an internal wrapper cannot detect — and non-acknowledgment
    is weak evidence a job is dispensable (it can mean a routing failure, a
    vacation, or a dead gateway).

    Even when a job IS quarantined, the incident stays open and keeps emitting
    a daily dead-man reminder. A job that is off must prove it is off.

    The SQLite phase is only set to 'quarantined' AFTER the scheduler has
    actually stopped the job. If the pause fails, the phase stays open and the
    caller is told the truth: still running, still failing, needs a human.
    """
    if row["severity"] == CRITICAL or row["money"] == MONEY_LIVE:
        return False, (
            "REFUSED: critical / live-money jobs are never auto-disabled. "
            "Escalating instead — only a human turns this off."
        )

    stopped, detail = _pause_scheduled_job(
        row["job_id"],
        f"auto-quarantined after {QUARANTINE_AFTER_HOURS}h unacknowledged, "
        f"{row['occurrence_count']} occurrences",
    )
    if not stopped:
        # Do NOT claim a pause that did not happen, and do NOT mark the phase
        # quarantined: that would suppress repair decisions for a job still
        # running at full cadence.
        return False, (
            f"QUARANTINE FAILED for {row['job_id']}: {detail}. The job is STILL "
            f"RUNNING and still failing ({row['occurrence_count']} occurrences). "
            f"Pause it by hand: hermes cronjob pause {row['job_id']}"
        )

    conn.execute(
        "UPDATE incidents SET phase='quarantined', quarantined_at=? "
        "WHERE fingerprint=?", (_iso(_now()), row["fingerprint"]),
    )
    conn.commit()
    return True, (
        f"{row['job_id']} PAUSED after {QUARANTINE_AFTER_HOURS}h unacknowledged "
        f"({row['occurrence_count']} occurrences). The incident stays OPEN and "
        f"will remind daily until acknowledged. Resume with: "
        f"hermes cronjob resume {row['job_id']}"
    )


def handle_failure(
    conn: sqlite3.Connection,
    *,
    fingerprint: str, job_id: str, host: str, reason_code: str,
    severity: str, money: str, error_text: str = "",
    deployed_sha: str | None = None, spec_path: str = "",
    script_path: str = "", log_path: str = "", profile: str | None = None,
    dry_run: bool = True,
) -> dict:
    """
    One call per failed run. Returns everything the incident card needs.

    ``dry_run`` defaults to True on purpose. Shadow mode is the default state of
    this system: it records what it WOULD have done until someone deliberately
    turns it loose. The failure mode here is an agent confidently repairing
    something that was not broken, and shadow mode is what makes that visible
    before it is expensive.
    """
    row = record_failure(
        conn, fingerprint=fingerprint, job_id=job_id, host=host,
        reason_code=reason_code, severity=severity, money=money,
        error_text=error_text, deployed_sha=deployed_sha,
    )
    decision = decide(conn, row, error_text=error_text)
    result = {
        "fingerprint": fingerprint,
        "occurrence_count": row["occurrence_count"],
        "phase": decision.phase,
        "dispatched": False,
        # Callers gate delivery on this: a shadow rehearsal must not be
        # reported to a human as though a repair actually happened.
        "shadow": bool(dry_run),
        "decision": decision.reason,
        "note": decision.as_note(),
        "notify_status": row["notify_status"],
        "escalation": escalation_due(row),
        "quarantine": None,
    }
    if decision.dispatch:
        outcome, detail = dispatch(
            conn, row, spec_path=spec_path, script_path=script_path,
            log_path=log_path, error_text=error_text, profile=profile,
            dry_run=dry_run,
        )
        result["dispatched"] = True
        result["outcome"] = outcome
        result["detail"] = detail[:400]

    if result["escalation"] == "quarantine":
        row = conn.execute(
            "SELECT * FROM incidents WHERE fingerprint=?", (fingerprint,)
        ).fetchone()
        did, msg = quarantine(conn, row)
        result["quarantine"] = {"applied": did, "message": msg}
    return result


def acknowledge(conn: sqlite3.Connection, fingerprint: str) -> bool:
    """The operator saw it. Stop escalating; keep the record."""
    cur = conn.execute(
        "UPDATE incidents SET acknowledged_at=? WHERE fingerprint=?",
        (_iso(_now()), fingerprint),
    )
    conn.commit()
    return cur.rowcount > 0


def open_incidents(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT * FROM incidents WHERE phase NOT IN ('resolved') "
        "ORDER BY first_seen_at"
    ).fetchall()


def budget_status(conn: sqlite3.Connection) -> dict:
    return {
        "concurrent": f"{_concurrent(conn)}/{MAX_CONCURRENT}",
        "last_hour": f"{_count_since(conn, 1)}/{MAX_STARTS_PER_HOUR}",
        "last_day": f"{_count_since(conn, 24)}/{MAX_STARTS_PER_DAY}",
        "open_prs": f"{_open_prs(conn)}/{MAX_OPEN_PRS}",
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="jobrun repair dispatcher")
    ap.add_argument("--status", action="store_true", help="open incidents + budgets")
    ap.add_argument("--ack", metavar="FINGERPRINT")
    ap.add_argument("--db")
    args = ap.parse_args()

    conn = connect(Path(args.db) if args.db else None)

    if args.ack:
        print("acknowledged" if acknowledge(conn, args.ack) else "not found")
        return 0

    if args.status:
        b = budget_status(conn)
        print("Repair budgets: " + "  ".join(f"{k}={v}" for k, v in b.items()))
        rows = open_incidents(conn)
        if not rows:
            print("No open incidents.")
            return 0
        print(f"\n{len(rows)} open incident(s):")
        for r in rows:
            ack = " ACK" if r["acknowledged_at"] else ""
            print(f"  [{r['phase']:14s}] {r['job_id']:44s} x{r['occurrence_count']:<4d} "
                  f"attempts={r['repair_attempts']}{ack}")
            print(f"       {r['fingerprint']}  {r['reason_code']}  since {r['first_seen_at']}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
