#!/usr/bin/env python3
"""
Regression tests for the seven P1 review findings on the severity/repair work.

Each test names the defect it locks down. Several of these were shipped bugs
that made the feature's headline claim false, so they get a test apiece rather
than a shared smoke test.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "scheduled-job-runner" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import jobrun_repair as R  # noqa: E402
from jobrun_severity import (  # noqa: E402
    DEGRADED,
    MONEY_LIVE,
    MONEY_NONE,
    MONEY_PAPER,
    SENTINEL_PREFIX,
    MoneyMismatch,
    classify,
    parse_sentinel,
    reconcile_money,
)

PY = sys.executable


def _home():
    h = Path(tempfile.mkdtemp(prefix="jobrun-p1-"))
    (h / "jobs.d").mkdir()
    (h / "scripts").mkdir()
    (h / "jobstate").mkdir()
    return h


def _run(home, *args):
    return subprocess.run(
        [PY, str(SCRIPTS / "jobrun.py"), *args],
        capture_output=True, text=True,
        env=dict(os.environ, HERMES_HOME=str(home)),
        cwd=str(SCRIPTS),
    )


# --------------------------------------------------------------------------
# P1: dedup counted occurrences but notified on every single tick
# --------------------------------------------------------------------------
def test_repeated_condition_is_delivered_once_not_every_tick():
    """The headline claim. Counting is not suppressing."""
    home = _home()
    sink = home / "sent.txt"
    fake = home / "scripts" / "send.sh"
    fake.write_text(f'#!/bin/bash\ncat >> {sink}\necho "---SEND---" >> {sink}\necho sent\n')
    fake.chmod(0o755)
    (home / "scripts" / "boom.py").write_text("import sys\nsys.exit(20)\n")
    (home / "jobs.d" / "noisy.toml").write_text(
        'job_id = "noisy"\nscript = "boom.py"\nruntime = "python"\n'
        f'notify_target = "telegram:-100:7"\nnotify_command = "{fake}"\n'
    )

    cards = 0
    for _ in range(10):
        r = _run(home, "--spec", "noisy")
        if "DEGRADED" in r.stdout:
            cards += 1

    assert cards == 1, f"expected 1 card from 10 identical failures, got {cards}"
    # Count DELIVERIES, not occurrences of the job name: the job id appears
    # both in the card headline and in the log path inside it, so a naive
    # substring count double-counts a single notification.
    sends = sink.read_text().count("---SEND---") if sink.exists() else 0
    assert sends == 1, f"expected 1 notification, got {sends}"


def test_suppressed_runs_say_so_on_stdout():
    """Silent must not mean invisible to someone reading logs by hand."""
    home = _home()
    (home / "scripts" / "boom.py").write_text("import sys\nsys.exit(20)\n")
    (home / "jobs.d" / "q.toml").write_text(
        'job_id = "q"\nscript = "boom.py"\nruntime = "python"\n')
    _run(home, "--spec", "q")
    r = _run(home, "--spec", "q")
    assert "suppressed" in r.stdout, r.stdout


def test_critical_always_speaks_even_when_repeating():
    """A live-money guard must never be summarized into silence."""
    from jobrun import should_speak  # noqa: E402

    inc = {"occurrence_count": 99, "escalation": None, "dispatched": False}
    speak, why = should_speak(inc, "critical")
    assert speak, why


def test_escalation_milestone_breaks_the_silence():
    from jobrun import should_speak  # noqa: E402

    inc = {"occurrence_count": 50, "escalation": "escalate_4h"}
    speak, _ = should_speak(inc, DEGRADED)
    assert speak


# --------------------------------------------------------------------------
# P1: quarantine claimed a pause it never performed
# --------------------------------------------------------------------------
def test_quarantine_does_not_claim_a_pause_it_could_not_perform(monkeypatch):
    """Telling an operator a job stopped while it keeps running is the worst
    of the three possible states."""
    conn = R.connect(Path(tempfile.mkdtemp()) / "i.db")
    row = R.record_failure(
        conn, fingerprint="qf", job_id="jobx", host="h",
        reason_code="failed_unknown", severity=DEGRADED, money=MONEY_NONE,
        error_text="boom", deployed_sha="s")
    monkeypatch.setattr(R, "_pause_scheduled_job", lambda *a, **k: (False, "no CLI"))
    did, msg = R.quarantine(conn, row)
    assert not did
    assert "STILL" in msg and "RUNNING" in msg, msg
    after = conn.execute(
        "SELECT phase FROM incidents WHERE fingerprint='qf'").fetchone()
    assert after["phase"] != "quarantined", (
        "phase must not be quarantined when the pause failed, or repair "
        "decisions are suppressed for a job still running")


def test_quarantine_marks_phase_only_after_a_real_pause(monkeypatch):
    conn = R.connect(Path(tempfile.mkdtemp()) / "i.db")
    row = R.record_failure(
        conn, fingerprint="qg", job_id="joby", host="h",
        reason_code="failed_unknown", severity=DEGRADED, money=MONEY_NONE,
        error_text="boom", deployed_sha="s")
    calls = []
    monkeypatch.setattr(
        R, "_pause_scheduled_job",
        lambda j, r: (calls.append(j), (True, "paused"))[1])
    did, msg = R.quarantine(conn, row)
    assert did and calls == ["joby"]
    after = conn.execute(
        "SELECT phase FROM incidents WHERE fingerprint='qg'").fetchone()
    assert after["phase"] == "quarantined"


# --------------------------------------------------------------------------
# P1: record_success was never called, so `consecutive` never reset
# --------------------------------------------------------------------------
def test_a_successful_run_closes_the_open_condition():
    """Two failures separated by healthy runs must not look consecutive."""
    home = _home()
    flip = home / "flip.txt"
    (home / "scripts" / "flaky.py").write_text(
        "import sys, pathlib\n"
        f"p = pathlib.Path({str(flip)!r})\n"
        "n = int(p.read_text()) if p.exists() else 0\n"
        "p.write_text(str(n + 1))\n"
        "sys.exit(20 if n % 2 == 0 else 0)\n"
    )
    (home / "jobs.d" / "flaky.toml").write_text(
        'job_id = "flaky"\nscript = "flaky.py"\nruntime = "python"\n')

    for _ in range(6):          # fail, ok, fail, ok, fail, ok
        _run(home, "--spec", "flaky")

    conn = R.connect(home / "jobstate" / "incidents.db")
    rows = conn.execute(
        "SELECT consecutive, phase FROM incidents WHERE job_id='flaky'"
    ).fetchall()
    assert rows, "no incident recorded at all"
    for r in rows:
        assert r["consecutive"] <= 1, (
            f"consecutive={r['consecutive']}: a success between failures must "
            f"reset the counter, or a healthy flaky job dispatches a repair")
    disp = conn.execute("SELECT COUNT(*) c FROM dispatches").fetchone()["c"]
    assert disp == 0, f"alternating fail/success dispatched {disp} repairs"


# --------------------------------------------------------------------------
# P1: money mismatch was caught AFTER the child ran, and only on failure
# --------------------------------------------------------------------------
def test_money_mismatch_refuses_to_run_the_child_at_all():
    """A live script declared as paper must never execute."""
    home = _home()
    marker = home / "ran.txt"
    (home / "scripts" / "live.py").write_text(
        "import pathlib\n"
        "TRADING_BASE = 'https://api.alpaca.markets'\n"
        f"pathlib.Path({str(marker)!r}).write_text('executed')\n"
    )
    (home / "jobs.d" / "mis.toml").write_text(
        'job_id = "mis"\nscript = "live.py"\nruntime = "python"\n'
        'money = "paper"\n')

    r = _run(home, "--spec", "mis")
    assert not marker.exists(), (
        "the child EXECUTED despite a live/paper mismatch — the check must "
        "happen before launch, not on the failure path")
    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert "money" in combined.lower(), combined[:400]


def test_a_successful_mismatched_run_is_still_caught():
    """The original bug: only failures were checked, so a mismatched script
    that SUCCEEDED was never examined."""
    home = _home()
    (home / "scripts" / "live_ok.py").write_text(
        "TRADING_BASE = 'https://api.alpaca.markets'\n"
        "print('all good')\n")
    (home / "jobs.d" / "ok.toml").write_text(
        'job_id = "ok"\nscript = "live_ok.py"\nruntime = "python"\n'
        'money = "none"\n')
    r = _run(home, "--spec", "ok")
    assert r.returncode != 0, "a mismatched but SUCCESSFUL job was accepted"


def test_over_declaring_live_is_still_allowed():
    """Louder than warranted is safe; quieter is not."""
    assert reconcile_money(MONEY_LIVE, MONEY_NONE) == MONEY_LIVE


def test_declaring_paper_on_a_live_script_raises():
    try:
        reconcile_money(MONEY_PAPER, MONEY_LIVE)
    except MoneyMismatch:
        return
    raise AssertionError("expected MoneyMismatch")


# --------------------------------------------------------------------------
# P1: money scan read a different path than the runner executed
# --------------------------------------------------------------------------
def test_money_detection_reads_the_script_that_actually_runs():
    """With cwd set, the scanner used <cwd>/scripts/<name> while execution
    used HERMES_HOME/scripts/<name>, so live markers were missed."""
    home = _home()
    other = Path(tempfile.mkdtemp(prefix="othercwd-"))
    (other / "scripts").mkdir()
    # A decoy at <cwd>/scripts/ with NO live markers.
    (other / "scripts" / "t.py").write_text("print('decoy')\n")
    # The real one, which is what actually executes.
    (home / "scripts" / "t.py").write_text(
        "TRADING_BASE = 'https://api.alpaca.markets'\nprint('real')\n")
    (home / "jobs.d" / "c.toml").write_text(
        'job_id = "c"\nscript = "t.py"\nruntime = "python"\n'
        f'cwd = "{other}"\nmoney = "none"\n')
    r = _run(home, "--spec", "c")
    assert r.returncode != 0, (
        "the decoy was scanned instead of the executed script, so a live job "
        "was classified as none")


# --------------------------------------------------------------------------
# P1: sentinel free text bypassed redact()
# --------------------------------------------------------------------------
def test_sentinel_summary_is_sanitized():
    line = (SENTINEL_PREFIX + ' {"schema":"jobrun.result/v1",'
            '"outcome":"degraded","summary":"token=SUPERSECRETVALUE"}')
    seen = {}

    def fake_redact(s):
        seen["called"] = True
        return s.replace("SUPERSECRETVALUE", "[REDACTED]")

    sent = parse_sentinel(line, fake_redact)
    assert seen.get("called"), "sanitize was never invoked"
    assert "SUPERSECRETVALUE" not in (sent.summary or "")
    assert "[REDACTED]" in sent.summary


def test_sentinel_summary_is_bounded():
    huge = "A" * 5000
    line = (SENTINEL_PREFIX + ' {"schema":"jobrun.result/v1",'
            f'"outcome":"degraded","summary":"{huge}"}}')
    sent = parse_sentinel(line)
    assert len(sent.summary) <= 300


def test_sentinel_metrics_free_text_is_sanitized():
    line = (SENTINEL_PREFIX + ' {"schema":"jobrun.result/v1",'
            '"outcome":"degraded","metrics":{"note":"pw=HUNTER2","n":5}}')
    sent = parse_sentinel(line, lambda s: s.replace("HUNTER2", "[X]"))
    assert sent.metrics["n"] == 5
    assert "HUNTER2" not in str(sent.metrics["note"])


def test_leak_does_not_reach_the_card_end_to_end():
    """The whole point: a credential in a summary must not reach a chat."""
    home = _home()
    sink = home / "sent.txt"
    fake = home / "scripts" / "send.sh"
    fake.write_text(f'#!/bin/bash\ncat >> {sink}\necho sent\n')
    fake.chmod(0o755)
    (home / "scripts" / "leak.py").write_text(
        "import sys\n"
        "print('" + SENTINEL_PREFIX + " {\"schema\":\"jobrun.result/v1\","
        "\"outcome\":\"degraded\","
        "\"summary\":\"failed with api_key=sk-livesecret123456\"}')\n"
        "sys.exit(20)\n"
    )
    (home / "jobs.d" / "leak.toml").write_text(
        'job_id = "leak"\nscript = "leak.py"\nruntime = "python"\n'
        f'notify_target = "telegram:-100:7"\nnotify_command = "{fake}"\n')
    r = _run(home, "--spec", "leak")
    body = (sink.read_text() if sink.exists() else "") + r.stdout
    assert "sk-livesecret123456" not in body, (
        "a credential in a sentinel summary reached the card/notification")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
