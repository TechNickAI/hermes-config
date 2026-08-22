"""Tests for the scheduled-job-runner skill's jobrun.py execution adapter.

These exercise the real script against real subprocesses — no mocks — because the
whole point of the runner is that its terminal states are trustworthy.
"""

import json
import os
import subprocess
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

SKILL = Path(__file__).parent.parent / "skills" / "scheduled-job-runner"
JOBRUN = SKILL / "scripts" / "jobrun.py"

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_CHILD = 3
EXIT_TIMEOUT = 4


def test_skill_files_exist():
    assert (SKILL / "SKILL.md").is_file()
    assert JOBRUN.is_file()


def test_jobrun_compiles():
    subprocess.run(
        [sys.executable, "-m", "py_compile", str(JOBRUN)], check=True
    )


@pytest.fixture
def home(tmp_path):
    """An isolated HERMES_HOME so tests never touch real job state."""
    (tmp_path / "jobs.d").mkdir()
    (tmp_path / "scripts").mkdir()
    return tmp_path


def _run(home, *args, timeout=180):
    env = dict(os.environ, HERMES_HOME=str(home))
    return subprocess.run(
        [sys.executable, str(JOBRUN), *args],
        capture_output=True, text=True, env=env, timeout=timeout,
    )


def _spec(home, name, body):
    (home / "jobs.d" / f"{name}.toml").write_text(body)


def test_selftest_passes(home):
    """The runner's own self-test must be green; it is the rollout gate."""
    r = _run(home, "--selftest", timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "passed" in r.stdout


def test_success_is_verbatim(home):
    """stdout must survive byte-for-byte, including a trailing control line."""
    script = home / "scripts" / "gate.py"
    script.write_text('print("diagnostic")\nprint(\'{"wakeAgent": false}\')\n')
    _spec(home, "gate", f'job_id = "gate"\nscript = "{script}"\nruntime = "python"\n')
    r = _run(home, "--spec", "gate")
    assert r.returncode == EXIT_OK
    assert r.stdout.strip().splitlines()[-1] == '{"wakeAgent": false}'


def test_silent_policy_suppresses_successful_output(home):
    """A noisy job is silenced in the SPEC, without editing the script."""
    script = home / "scripts" / "noisy.py"
    script.write_text('print("chatter nobody asked for")\n')
    _spec(home, "noisy",
          f'job_id = "noisy"\nscript = "{script}"\nruntime = "python"\n'
          'output_policy = "silent"\n')
    r = _run(home, "--spec", "noisy")
    assert r.returncode == EXIT_OK
    assert r.stdout == ""


def test_child_failure_exit_code_and_incident_card(home):
    script = home / "scripts" / "boom.py"
    script.write_text('import sys\nsys.stderr.write("kaboom\\n")\nsys.exit(7)\n')
    _spec(home, "boom", f'job_id = "boom"\nscript = "{script}"\nruntime = "python"\n')
    r = _run(home, "--spec", "boom")
    assert r.returncode == EXIT_CHILD
    assert "boom" in r.stdout and "exited 7" in r.stdout


def test_timeout_is_distinct_from_failure(home):
    script = home / "scripts" / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n")
    _spec(home, "slow",
          f'job_id = "slow"\nscript = "{script}"\nruntime = "python"\ntimeout = 2\n')
    r = _run(home, "--spec", "slow")
    assert r.returncode == EXIT_TIMEOUT


def test_missing_script_is_config_error_not_child_failure(home):
    _spec(home, "gone",
          f'job_id = "gone"\nscript = "{home}/scripts/nope.py"\nruntime = "python"\n')
    r = _run(home, "--spec", "gone")
    assert r.returncode == EXIT_CONFIG


def test_unknown_spec_field_is_rejected(home):
    """A misspelled control must fail loudly, never silently do nothing."""
    script = home / "scripts" / "ok.py"
    script.write_text("pass\n")
    _spec(home, "typo",
          f'job_id = "typo"\nscript = "{script}"\ntimeoutt = 5\n')
    r = _run(home, "--spec", "typo")
    assert r.returncode == EXIT_CONFIG
    assert "unknown spec field" in r.stdout.lower()


def test_args_are_passed_through(home):
    script = home / "scripts" / "args.py"
    script.write_text('import sys\nprint(" ".join(sys.argv[1:]))\n')
    _spec(home, "args",
          f'job_id = "args"\nscript = "{script}"\nruntime = "python"\n'
          'args = ["--mode", "backfill"]\n')
    r = _run(home, "--spec", "args")
    assert r.returncode == EXIT_OK
    assert "--mode backfill" in r.stdout


def test_ledger_records_exit_code_and_duration(home):
    script = home / "scripts" / "ok.py"
    script.write_text('print("done")\n')
    _spec(home, "ok", f'job_id = "ok"\nscript = "{script}"\nruntime = "python"\n')
    _run(home, "--spec", "ok")
    ledger = home / "jobstate" / "runs.jsonl"
    assert ledger.is_file()
    rows = [json.loads(x) for x in ledger.read_text().splitlines() if x.strip()]
    fin = [r for r in rows if r.get("event") == "job.finished"]
    assert fin, "no terminal event recorded"
    assert fin[-1]["state"] == "success"
    assert fin[-1]["exit_code"] == 0
    assert isinstance(fin[-1]["duration_ms"], int)


def test_dry_run_reveals_resolved_interpreter(home):
    """--dry-run must show which interpreter will run: the whole point."""
    script = home / "scripts" / "ok.py"
    script.write_text("pass\n")
    _spec(home, "dry", f'job_id = "dry"\nscript = "{script}"\nruntime = "python"\n')
    r = _run(home, "--dry-run", "--spec", "dry")
    assert r.returncode == EXIT_OK
    payload = json.loads(r.stdout)
    assert payload["job_id"] == "dry"
    assert payload["preflight"] == "ok"
    assert payload["argv"][0] == sys.executable


def test_failures_command_is_silent_when_clean(home):
    """Safe to schedule: prints nothing when nothing is wrong."""
    r = _run(home, "--failures", "24")
    assert r.returncode == EXIT_OK
    assert r.stdout.strip() == ""


def test_redaction_masks_secrets_without_mangling_normal_output(home):
    script = home / "scripts" / "leak.py"
    script.write_text(
        'import sys\n'
        'sys.stderr.write("api_key=supersecretvalue\\n")\n'
        'sys.stderr.write("session started and processed 42 rows\\n")\n'
        'sys.exit(1)\n'
    )
    _spec(home, "leak", f'job_id = "leak"\nscript = "{script}"\nruntime = "python"\n')
    r = _run(home, "--spec", "leak")
    assert r.returncode == EXIT_CHILD
    log = next((home / "jobstate" / "logs").glob("leak-*.log")).read_text()
    assert "supersecretvalue" not in log
    assert "[REDACTED]" in log
    # a normal line containing a broad word must survive intact
    assert "session started and processed 42 rows" in log


def test_ledger_does_not_record_secret_arguments(home):
    """argv lands on disk, so a `--token VALUE` pair must be masked."""
    script = home / "scripts" / "ok.py"
    script.write_text("pass\n")
    _spec(home, "sec",
          f'job_id = "sec"\nscript = "{script}"\nruntime = "python"\n'
          'args = ["--token", "hunter2secret", "--verbose"]\n')
    r = _run(home, "--spec", "sec")
    assert r.returncode == EXIT_OK
    ledger = (home / "jobstate" / "runs.jsonl").read_text()
    assert "hunter2secret" not in ledger
    assert "[REDACTED]" in ledger
    # non-secret arguments stay readable for debugging
    assert "--verbose" in ledger


def test_timezone_is_not_forced_when_unset(home):
    """Forcing a TZ would shift the day boundary for date-computing jobs."""
    script = home / "scripts" / "tz.py"
    script.write_text('import os\nprint("TZ=" + os.environ.get("TZ", "<unset>"))\n')
    _spec(home, "tz", f'job_id = "tz"\nscript = "{script}"\nruntime = "python"\n')
    r = _run(home, "--spec", "tz")
    assert "TZ=<unset>" in r.stdout, r.stdout

    _spec(home, "tz2",
          f'job_id = "tz2"\nscript = "{script}"\nruntime = "python"\n'
          'timezone = "UTC"\n')
    r2 = _run(home, "--spec", "tz2")
    assert "TZ=UTC" in r2.stdout


def test_passthrough_survives_a_redaction_pattern(home):
    """Verbatim delivery must not be rewritten by the log-sanitizing pass."""
    script = home / "scripts" / "tok.py"
    script.write_text('print("token=abc123 is my literal output")\n')
    _spec(home, "tok", f'job_id = "tok"\nscript = "{script}"\nruntime = "python"\n')
    r = _run(home, "--spec", "tok")
    assert r.returncode == EXIT_OK
    # delivered stdout is the original
    assert "token=abc123 is my literal output" in r.stdout
    # but the on-disk log is sanitized
    log = next((home / "jobstate" / "logs").glob("tok-*.log")).read_text()
    assert "abc123" not in log


def test_capture_is_bounded_by_bytes_not_lines(home):
    """A single enormous newline-free line must not exhaust memory."""
    script = home / "scripts" / "onebigline.py"
    script.write_text("import sys\nsys.stdout.write('z' * 40_000_000)\n")
    _spec(home, "big",
          f'job_id = "big"\nscript = "{script}"\nruntime = "python"\n'
          'output_policy = "silent"\ntimeout = 120\n')
    r = _run(home, "--spec", "big")
    assert r.returncode == EXIT_OK
    log = next((home / "jobstate" / "logs").glob("big-*.log"))
    # 40MB of output must not produce a 40MB log
    assert log.stat().st_size < 2_000_000, log.stat().st_size


def test_selftest_does_not_pollute_the_real_ledger(home):
    """Self-test fixtures fail on purpose; they must not look like incidents.

    Without isolation, `--failures` reports st-fail/st-timeout as production
    failures forever, which is exactly the false-alarm noise the runner exists
    to remove.
    """
    _run(home, "--selftest", timeout=600)
    ledger = home / "jobstate" / "runs.jsonl"
    if ledger.exists():
        assert "st-fail" not in ledger.read_text()
        assert "st-timeout" not in ledger.read_text()
    logs = home / "jobstate" / "logs"
    if logs.is_dir():
        assert not list(logs.glob("st-*.log"))
    # and the operator view stays quiet
    r = _run(home, "--failures", "24")
    assert r.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Capabilities required before a consequential (money / order-placing) job can
# migrate: a labelled failure, a stated timeout, a recorded code version, and a
# shared identity with any inner wrapper.
# ---------------------------------------------------------------------------


def test_critical_job_failure_is_labelled(home):
    """A failed money job must not read like a failed report generator."""
    script = home / "scripts" / "boom.py"
    script.write_text('import sys\nsys.exit(9)\n')
    _spec(home, "money",
          f'job_id = "money"\nscript = "{script}"\nruntime = "python"\n'
          "critical = true\ntimeout = 60\n")
    r = _run(home, "--spec", "money")
    assert r.returncode == EXIT_CHILD
    assert "CRITICAL" in r.stdout, r.stdout
    ledger = (home / "jobstate" / "runs.jsonl").read_text()
    assert '"critical": true' in ledger.lower()


def test_noncritical_failure_is_not_labelled_critical(home):
    """Control: the label must mean something, so it can't be on everything."""
    script = home / "scripts" / "boom2.py"
    script.write_text('import sys\nsys.exit(3)\n')
    _spec(home, "report",
          f'job_id = "report"\nscript = "{script}"\nruntime = "python"\n')
    r = _run(home, "--spec", "report")
    assert r.returncode == EXIT_CHILD
    assert "CRITICAL" not in r.stdout


def test_deployed_sha_is_recorded_when_job_runs_from_a_git_tree(home):
    """Ties an outcome to the exact code that produced it.

    Without this, the run history and a deploy-drift watchdog can disagree
    about what actually ran.
    """
    import subprocess as sp
    repo = home / "release"
    repo.mkdir()
    sp.run(["git", "init", "-q"], cwd=repo, check=True)
    sp.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    sp.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "job.py").write_text('print("ran")\n')
    sp.run(["git", "add", "-A"], cwd=repo, check=True)
    sp.run(["git", "commit", "-qm", "x"], cwd=repo, check=True)
    sha = sp.run(["git", "rev-parse", "HEAD"], cwd=repo,
                 capture_output=True, text=True).stdout.strip()

    _spec(home, "shaj",
          f'job_id = "shaj"\nscript = "{repo}/job.py"\nruntime = "python"\n'
          f'cwd = "{repo}"\n')
    r = _run(home, "--spec", "shaj")
    assert r.returncode == EXIT_OK
    ledger = (home / "jobstate" / "runs.jsonl").read_text()
    assert sha[:12] in ledger, "deployed sha not recorded"


def test_run_id_can_be_shared_with_an_inner_wrapper(home):
    """Nested wrappers must not double-count one run.

    The agent already runs its own domain wrapper inside some jobs. If both
    ledgers invent their own id, one execution appears as two records and
    failure counts inflate. jobrun exports its run id so the inner recorder
    can adopt it.
    """
    script = home / "scripts" / "echo_run.py"
    script.write_text(
        'import os\nprint(os.environ.get("JOBRUN_RUN_ID", "<missing>"))\n'
    )
    _spec(home, "nested",
          f'job_id = "nested"\nscript = "{script}"\nruntime = "python"\n')
    r = _run(home, "--spec", "nested")
    assert r.returncode == EXIT_OK
    emitted = r.stdout.strip()
    assert emitted != "<missing>", "JOBRUN_RUN_ID not exported to the child"
    ledger = (home / "jobstate" / "runs.jsonl").read_text()
    assert emitted in ledger, "child's run id does not match the ledger row"


def test_timeout_shorter_than_interval_is_enforced_for_critical_jobs(home):
    """A critical job must declare its OWN timeout.

    Inheriting the default silently hands a consequential job a ceiling it
    never asked for, possibly longer than its own schedule interval.
    """
    script = home / "scripts" / "ok.py"
    script.write_text("pass\n")
    _spec(home, "nolimit",
          f'job_id = "nolimit"\nscript = "{script}"\nruntime = "python"\n'
          "critical = true\n")  # no timeout key at all
    r = _run(home, "--spec", "nolimit")
    assert r.returncode == EXIT_CONFIG
    assert "timeout" in r.stdout.lower()


def test_failures_view_puts_critical_first(home):
    """An operator scanning a failure list must see the money job first."""
    ok = home / "scripts" / "f.py"
    ok.write_text("import sys\nsys.exit(1)\n")
    _spec(home, "aaa-report", f'job_id = "aaa-report"\nscript = "{ok}"\nruntime = "python"\n')
    _spec(home, "zzz-money",
          f'job_id = "zzz-money"\nscript = "{ok}"\nruntime = "python"\n'
          "critical = true\ntimeout = 60\n")
    _run(home, "--spec", "aaa-report")
    _run(home, "--spec", "zzz-money")
    r = _run(home, "--failures", "24")
    assert "CRITICAL" in r.stdout
    lines = [x for x in r.stdout.splitlines() if "-report" in x or "-money" in x]
    assert "zzz-money" in lines[0], f"critical job not listed first: {lines}"


def test_selftest_still_passes_with_new_capabilities(home):
    r = _run(home, "--selftest", timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr


def test_runs_when_the_profile_home_does_not_exist_yet(tmp_path):
    """A fresh profile must not fail every job with FileNotFoundError.

    Defaulting the child's cwd to the profile home is right, but only if that
    directory exists. Otherwise Popen raises FileNotFoundError and the runner
    blames the job for its own bad default.
    """
    missing = tmp_path / "not-created-yet"
    env = dict(os.environ, HERMES_HOME=str(missing))
    r = subprocess.run([sys.executable, str(JOBRUN), "--selftest"],
                       capture_output=True, text=True, env=env, timeout=600)
    assert r.returncode == 0, r.stdout[-2000:]


# ---------------------------------------------------------------------------
# Failure notification.
# The scheduler drops a failure alert entirely when a job is configured
# deliver=local: _resolve_delivery_targets() returns [] and _deliver_result()
# returns None, which is indistinguishable from a successful send. A job that
# guards something important can therefore break in permanent silence.
# The runner already KNOWS the job failed, so it notifies directly.
# ---------------------------------------------------------------------------


def test_failure_notifies_the_configured_target(home):
    """A failing job must reach a human even when cron would drop it."""
    sink = home / "sent.txt"
    fake = home / "scripts" / "fake_send.sh"
    fake.write_text(f'#!/bin/bash\ncat > {sink}\necho sent\n')
    fake.chmod(0o755)

    boom = home / "scripts" / "boom.py"
    boom.write_text("import sys\nsys.exit(4)\n")
    _spec(home, "guard",
          f'job_id = "guard"\nscript = "{boom}"\nruntime = "python"\n'
          f'notify_target = "telegram:-100:7"\nnotify_command = "{fake}"\n')
    r = _run(home, "--spec", "guard")
    assert r.returncode == EXIT_CHILD
    assert sink.exists(), "no notification was sent for a failed job"
    body = sink.read_text()
    assert "guard" in body
    assert "exited 4" in body


def test_success_notifies_nobody(home):
    """Control: quiet success is the whole point. Only failures speak."""
    sink = home / "sent2.txt"
    fake = home / "scripts" / "fake_send2.sh"
    fake.write_text(f'#!/bin/bash\ncat > {sink}\n')
    fake.chmod(0o755)

    ok = home / "scripts" / "ok.py"
    ok.write_text("pass\n")
    _spec(home, "quiet",
          f'job_id = "quiet"\nscript = "{ok}"\nruntime = "python"\n'
          f'notify_target = "telegram:-100:7"\nnotify_command = "{fake}"\n')
    r = _run(home, "--spec", "quiet")
    assert r.returncode == EXIT_OK
    assert not sink.exists(), "a SUCCESSFUL job sent a notification"


def test_notification_failure_does_not_change_the_job_outcome(home):
    """A broken notifier must not turn a passing job into a failing one.

    Bookkeeping must never decide the exit code the scheduler records.
    """
    ok = home / "scripts" / "ok2.py"
    ok.write_text("pass\n")
    _spec(home, "nf",
          f'job_id = "nf"\nscript = "{ok}"\nruntime = "python"\n'
          'notify_target = "telegram:-100:7"\n'
          'notify_command = "/nonexistent/sender"\n')
    r = _run(home, "--spec", "nf")
    assert r.returncode == EXIT_OK


def test_notification_records_its_own_outcome_in_the_ledger(home):
    """A notifier that silently fails recreates the bug being fixed.

    Asserts the real recorded outcome, not merely that the word "notify"
    appears somewhere: the spec fields alone would satisfy a substring check
    even with the notification code removed entirely.
    """
    boom = home / "scripts" / "boom3.py"
    boom.write_text("import sys\nsys.exit(1)\n")
    _spec(home, "nrec",
          f'job_id = "nrec"\nscript = "{boom}"\nruntime = "python"\n'
          'notify_target = "telegram:-100:7"\n'
          'notify_command = "/nonexistent/sender"\n')
    r = _run(home, "--spec", "nrec")
    assert r.returncode == EXIT_CHILD

    rows = [json.loads(x) for x
            in (home / "jobstate" / "runs.jsonl").read_text().splitlines()
            if x.strip()]
    notified = [x for x in rows if x.get("event") == "job.notified"]
    assert notified, "no job.notified event was recorded"
    # The sender does not exist, so the runner must SAY the alert failed
    # rather than reporting a send it never made.
    assert notified[-1]["notify_status"] == "failed_no_sender", notified[-1]
    assert "notification failed_no_sender" in r.stdout


def test_notify_target_is_not_required(home):
    """Jobs without a target keep working exactly as before."""
    boom = home / "scripts" / "boom4.py"
    boom.write_text("import sys\nsys.exit(2)\n")
    _spec(home, "plain",
          f'job_id = "plain"\nscript = "{boom}"\nruntime = "python"\n')
    r = _run(home, "--spec", "plain")
    assert r.returncode == EXIT_CHILD


def test_critical_failure_notification_is_marked_critical(home):
    """The message a human wakes up to must say it guards money."""
    sink = home / "sent3.txt"
    fake = home / "scripts" / "fake_send3.sh"
    fake.write_text(f'#!/bin/bash\ncat > {sink}\n')
    fake.chmod(0o755)

    boom = home / "scripts" / "boom5.py"
    boom.write_text("import sys\nsys.exit(7)\n")
    _spec(home, "money2",
          f'job_id = "money2"\nscript = "{boom}"\nruntime = "python"\n'
          "critical = true\ntimeout = 60\n"
          f'notify_target = "telegram:-100:7"\nnotify_command = "{fake}"\n')
    r = _run(home, "--spec", "money2")
    assert r.returncode == EXIT_CHILD
    assert "CRITICAL" in sink.read_text()


def test_notifier_does_not_depend_on_a_login_shell_path(home):
    """cron has no login shell, so a bare "hermes" on PATH is the assumption
    that makes an alert fail only in production. The runner must resolve the
    CLI explicitly rather than relying on PATH lookup.

    Asserted via the argv the runner BUILDS, not via a send attempt: on a bare
    clone hermes-agent is not installed at all, so requiring a successful
    resolution would make this test fail for reasons unrelated to the bug.
    """
    import importlib.util

    spec_mod = importlib.util.spec_from_file_location("jobrun_mod", JOBRUN)
    jr = importlib.util.module_from_spec(spec_mod)
    spec_mod.loader.exec_module(jr)

    s = jr.Spec({"job_id": "p", "command": "true",
                 "notify_target": "telegram:-100:7"})
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    orig, jr.subprocess.run = jr.subprocess.run, fake_run
    try:
        # Empty PATH: nothing is discoverable by name.
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = ""
        try:
            status = jr.notify_failure(s, "boom")
        finally:
            os.environ["PATH"] = old_path
    finally:
        jr.subprocess.run = orig

    assert status == "sent"
    argv = captured["argv"]
    # The sender must be an absolute path or an explicit fallback — never a
    # bare name resolved from an empty PATH.
    assert argv[1:4] == ["send", "--quiet", "--to"], argv
    assert os.path.isabs(argv[0]) or argv[0] == "hermes", argv


def test_a_job_that_cannot_start_still_notifies(home):
    """A deleted script is one of the commonest ways a job quietly dies.

    Config/preflight failures return before execution, so they originally
    skipped the notifier entirely — a guard job would go silent in exactly the
    situation the alert exists for.
    """
    sink = home / "sent_cfg.txt"
    fake = home / "scripts" / "fake_send_cfg.sh"
    fake.write_text(f'#!/bin/bash\ncat > {sink}\n')
    fake.chmod(0o755)

    _spec(home, "gone",
          'job_id = "gone"\nscript = "/nonexistent/deleted_by_deploy.py"\n'
          'runtime = "python"\ncritical = true\ntimeout = 60\n'
          f'notify_target = "telegram:-100:7"\nnotify_command = "{fake}"\n')
    r = _run(home, "--spec", "gone")
    assert r.returncode == EXIT_CONFIG
    assert sink.exists(), "a job that could not start notified nobody"
    body = sink.read_text()
    assert "gone" in body
    assert "CRITICAL" in body, body


def test_cannot_start_records_the_notification_in_the_ledger(home):
    _spec(home, "gone2",
          'job_id = "gone2"\nscript = "/nonexistent/x.py"\nruntime = "python"\n'
          'notify_target = "telegram:-100:7"\n'
          'notify_command = "/nonexistent/sender"\n')
    r = _run(home, "--spec", "gone2")
    assert r.returncode == EXIT_CONFIG
    rows = [json.loads(x) for x
            in (home / "jobstate" / "runs.jsonl").read_text().splitlines()
            if x.strip()]
    notified = [x for x in rows if x.get("event") == "job.notified"]
    assert notified, "no notification recorded for a start failure"
    assert notified[-1]["notify_status"] == "failed_no_sender"


def test_ledger_timestamps_use_one_format(home):
    """Mixed timestamp formats make the ledger unsortable."""
    boom = home / "scripts" / "b.py"
    boom.write_text("import sys\nsys.exit(1)\n")
    _spec(home, "tsfmt",
          f'job_id = "tsfmt"\nscript = "{boom}"\nruntime = "python"\n'
          'notify_target = "telegram:-100:7"\n'
          'notify_command = "/nonexistent/sender"\n')
    _run(home, "--spec", "tsfmt")
    rows = [json.loads(x) for x
            in (home / "jobstate" / "runs.jsonl").read_text().splitlines()
            if x.strip()]
    stamps = [r[k] for r in rows for k in ("ts", "finished_at") if r.get(k)]
    assert stamps
    # All must parse with the same parser and agree on tz-awareness.
    parsed = [datetime.fromisoformat(s) for s in stamps]
    aware = {p.tzinfo is not None for p in parsed}
    assert len(aware) == 1, f"mixed tz-awareness in ledger: {stamps}"


def test_a_finished_run_is_never_lost_to_a_concurrent_prune(home, monkeypatch):
    """The ledger is the only evidence a scheduled job ran.

    prune REPLACES the ledger path. If an append locks the ledger INODE rather
    than a stable side-lock, an append landing between the prune's snapshot and
    its replace is written to the doomed inode and discarded with it -- the run
    silently vanishes.
    """
    import importlib.util
    import multiprocessing as mp

    os.environ["HERMES_HOME"] = str(home)
    spec_mod = importlib.util.spec_from_file_location("jobrun_conc", JOBRUN)
    jr = importlib.util.module_from_spec(spec_mod)
    spec_mod.loader.exec_module(jr)
    n_append = 300

    def appender(_):
        for i in range(n_append):
            jr.append_ledger({"event": "job.finished", "seq": i})

    def pruner(_):
        for _ in range(40):
            jr.prune_state()

    monkeypatch.setattr(jr, "LEDGER_MAX_LINES", 50, raising=False)
    ctx = mp.get_context("fork")
    ps = [ctx.Process(target=appender, args=(0,)),
          ctx.Process(target=pruner, args=(0,))]
    for p in ps:
        p.start()
    for p in ps:
        p.join(60)

    lines = [x for x in jr.LEDGER.read_text().splitlines() if x.strip()]
    corrupt = []
    seqs = set()
    for x in lines:
        try:
            seqs.add(json.loads(x).get("seq"))
        except json.JSONDecodeError:
            corrupt.append(x)
    assert not corrupt, f"{len(corrupt)} torn ledger line(s)"
    # Retention legitimately drops OLD records; it must never drop records
    # while newer ones survive, which is what inode-swap loss looks like.
    kept = sorted(s for s in seqs if s is not None)
    if kept:
        assert kept == list(range(kept[0], kept[-1] + 1)), (
            f"gap in surviving runs {kept[:5]}..{kept[-5:]}: a finished run "
            "vanished mid-sequence, which retention alone cannot cause")


def test_the_signal_handler_never_blocks_on_the_prune_lock(home):
    """flock is not reentrant across file descriptors.

    A signal arriving while prune holds the ledger lock would make the handler
    block on the same thread forever: graceful shutdown hangs until SIGKILL and
    the signal row is never written. A racy append beats a hung runner.
    """
    import fcntl
    import importlib.util
    import signal as sig

    os.environ["HERMES_HOME"] = str(home)
    spec_mod = importlib.util.spec_from_file_location("jobrun_sig", JOBRUN)
    jr = importlib.util.module_from_spec(spec_mod)
    spec_mod.loader.exec_module(jr)
    jr.STATE_DIR.mkdir(parents=True, exist_ok=True)

    fired = {}

    def bail(signum, frame):
        fired["deadlock"] = True
        raise AssertionError("append_ledger blocked while the prune lock was held")

    old = sig.signal(sig.SIGALRM, bail)
    try:
        with open(jr.LEDGER_LOCK, "a+", encoding="utf-8") as lk:
            fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
            sig.alarm(5)
            jr.append_ledger({"event": "job.finished", "state": "signal"},
                             blocking=False)
            sig.alarm(0)
    finally:
        sig.signal(sig.SIGALRM, old)

    assert not fired.get("deadlock")
    # The record must actually land, not merely not-hang.
    rows = [json.loads(x) for x in jr.LEDGER.read_text().splitlines() if x.strip()]
    assert any(r.get("state") == "signal" for r in rows), (
        "the signal row was not written: a shutdown with no ledger record is "
        "the failure this guards")
