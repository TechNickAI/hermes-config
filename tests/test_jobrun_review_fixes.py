#!/usr/bin/env python3
"""
Regression tests for the seven P1 review findings on the severity/repair work.

Each test names the defect it locks down. Several of these were shipped bugs
that made the feature's headline claim false, so they get a test apiece rather
than a shared smoke test.
"""

import os
import pathlib
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


# --------------------------------------------------------------------------
# Found on a live profile: a directory named like a job id shadowed the spec
# --------------------------------------------------------------------------
def test_bare_job_id_is_not_shadowed_by_a_same_named_directory(tmp_path,
                                                               monkeypatch):
    """Cron passes a bare job id with the profile dir as cwd. A state
    directory named exactly like the job id made Spec.load() try to parse the
    DIRECTORY and fail with 'Is a directory' — the job silently never ran.
    Two real jobs on a live profile hit this."""
    home = _home()
    (home / "scripts" / "s.py").write_text("print('ok')\n")
    (home / "jobs.d" / "watchy.toml").write_text(
        'job_id = "watchy"\nscript = "s.py"\nruntime = "python"\n')
    # The trap: a directory with the same name as the job id, in the cwd.
    (home / "watchy").mkdir()
    r = subprocess.run(
        [PY, str(SCRIPTS / "jobrun.py"), "--spec", "watchy"],
        capture_output=True, text=True,
        env=dict(os.environ, HERMES_HOME=str(home)),
        cwd=str(home),          # cwd is the profile dir, as cron does it
    )
    assert r.returncode == 0, (r.stdout + r.stderr)[:400]
    assert "Is a directory" not in (r.stdout + r.stderr)


def test_explicit_path_to_a_spec_still_works(tmp_path):
    """The path form must keep working; only bare-name resolution changed."""
    home = _home()
    (home / "scripts" / "s.py").write_text("print('ok')\n")
    spec = home / "jobs.d" / "pathy.toml"
    spec.write_text('job_id = "pathy"\nscript = "s.py"\nruntime = "python"\n')
    r = subprocess.run(
        [PY, str(SCRIPTS / "jobrun.py"), "--spec", str(spec)],
        capture_output=True, text=True,
        env=dict(os.environ, HERMES_HOME=str(home)), cwd=str(SCRIPTS))
    assert r.returncode == 0, (r.stdout + r.stderr)[:300]


# --------------------------------------------------------------------------
# exit_map: legacy scripts declare their own exit convention
# --------------------------------------------------------------------------
def _exitmap_home(rc, extra=""):
    home = _home()
    sh = home / "scripts" / "tw.sh"
    sh.write_text("#!/bin/bash\necho 'TRANCHE 2 FILLED: position is now 1.25'\n"
                  f"exit {rc}\n")
    sh.chmod(0o755)
    (home / "jobs.d" / "tw.toml").write_text(
        'job_id = "tw"\nscript = "tw.sh"\nruntime = "bash"\ntimeout = 60\n'
        + extra)
    return home


def test_a_fired_tripwire_is_news_not_a_failure():
    """A script whose contract is '1 = tripwire fired' worked when it fired."""
    home = _exitmap_home(1, '\n[exit_map]\n1 = "noteworthy"\n2 = "broken"\n')
    r = _run(home, "--spec", "tw")
    assert r.returncode == 0, (
        "a noteworthy outcome must exit 0, or the scheduler stacks its own "
        f"failure banner on top: {r.stdout}")
    assert "TRANCHE 2 FILLED" in r.stdout
    # The content is the message: no failure furniture.
    assert "DEGRADED" not in r.stdout
    assert "Error:" not in r.stdout
    assert "Repair:" not in r.stdout


def test_the_same_script_still_alarms_when_genuinely_broken():
    """Control: exit_map must not mute the code that means 'I am broken'."""
    home = _exitmap_home(2, '\n[exit_map]\n1 = "noteworthy"\n2 = "broken"\n')
    r = _run(home, "--spec", "tw")
    assert r.returncode != 0
    assert "BROKEN" in r.stdout


def test_without_exit_map_legacy_behavior_is_unchanged():
    """No spec change means no behavior change: migration stays opt-in."""
    home = _exitmap_home(1)
    r = _run(home, "--spec", "tw")
    assert r.returncode != 0
    assert "DEGRADED" in r.stdout


def test_exit_map_cannot_relabel_success():
    home = _exitmap_home(0, '\n[exit_map]\n0 = "broken"\n')
    r = _run(home, "--spec", "tw")
    assert r.returncode != 0
    assert "exit 0" in (r.stdout + r.stderr)


def test_exit_map_rejects_an_unknown_outcome_name():
    home = _exitmap_home(1, '\n[exit_map]\n1 = "sortof_bad"\n')
    r = _run(home, "--spec", "tw")
    assert r.returncode != 0
    assert "exit_map" in (r.stdout + r.stderr)


def test_exit_map_cannot_excuse_a_timeout():
    """Runner-derived termination outranks any spec claim. A job killed by the
    harness is not 'noteworthy' just because the spec says exit 1 is."""
    home = _home()
    sh = home / "scripts" / "slow.sh"
    sh.write_text("#!/bin/bash\nsleep 30\n")
    sh.chmod(0o755)
    (home / "jobs.d" / "slow.toml").write_text(
        'job_id = "slow"\nscript = "slow.sh"\nruntime = "bash"\ntimeout = 1\n'
        '\n[exit_map]\n1 = "noteworthy"\n124 = "noteworthy"\n')
    r = _run(home, "--spec", "slow")
    assert r.returncode != 0, "a timeout must never be downgraded by exit_map"
    assert "noteworthy" not in r.stdout.lower()


# --------------------------------------------------------------------------
# "The agent finished" is not "the job was fixed"
# --------------------------------------------------------------------------
def _cls(text):
    """Load the classifier the way the runner does: with its sibling modules
    importable. Loading jobrun_repair.py in isolation fails inside dataclass
    construction, because it resolves names from the scripts/ directory."""
    scripts = (pathlib.Path(__file__).resolve().parent.parent /
               "skills/scheduled-job-runner/scripts")
    sys.path.insert(0, str(scripts))
    for mod in [m for m in sys.modules if m.startswith("jobrun")]:
        del sys.modules[mod]
    try:
        import jobrun_repair
        return jobrun_repair._classify_agent_report(text)
    finally:
        sys.path.remove(str(scripts))


def test_only_an_explicit_patch_counts_as_a_repair():
    assert _cls("done\nREPAIR-OUTCOME: patched https://x/pull/1") == "patched"


def test_an_agent_that_declined_is_not_recorded_as_a_repair():
    """The first live dispatch: the agent correctly refused to invent a fix for
    a script with no repository, and that was recorded as 'completed' — which
    reads as 'repaired' to everything downstream."""
    out = ("Pushing a branch would mean manufacturing a change. I stopped.\n"
           "REPAIR-OUTCOME: declined synthetic fixture, no repo")
    assert _cls(out) == "declined"


def test_a_report_with_no_declaration_is_never_assumed_fixed():
    out = "I investigated carefully and wrote a nice summary with no marker."
    assert _cls(out) == "no_declaration"


def test_an_invented_outcome_word_is_not_trusted():
    assert _cls("REPAIR-OUTCOME: totally-fixed-trust-me") == "no_declaration"


def test_the_last_declaration_wins_over_one_quoted_mid_report():
    out = ("I considered REPAIR-OUTCOME: patched but rejected it.\n"
           "REPAIR-OUTCOME: declined nothing to change")
    assert _cls(out) == "declined"


def test_spec_and_environmental_causes_are_distinguishable():
    assert _cls("REPAIR-OUTCOME: spec-defect timeout too short") == "spec-defect"
    assert _cls("REPAIR-OUTCOME: environmental missing cred") == "environmental"
    assert _cls("REPAIR-OUTCOME: not-reproducible ran clean") == "not-reproducible"
