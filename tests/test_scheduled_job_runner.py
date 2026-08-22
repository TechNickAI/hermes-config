"""Tests for the scheduled-job-runner skill's jobrun.py execution adapter.

These exercise the real script against real subprocesses — no mocks — because the
whole point of the runner is that its terminal states are trustworthy.
"""

import json
import os
import subprocess
import sys
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
# Capabilities required before a live-money job can migrate.
# Written against a real trading agent whose 48 scheduled jobs all forward into
# a release tree, and whose entry job was measured running 946s on a 900s
# schedule with no lock.
# ---------------------------------------------------------------------------


def test_critical_job_failure_is_labelled(home):
    """A failed money job must not read like a failed report generator."""
    script = home / "scripts" / "boom.py"
    script.write_text('import sys\nsys.exit(9)\n')
    _spec(home, "money",
          f'job_id = "money"\nscript = "{script}"\nruntime = "python"\n'
          "critical = true\n")
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
    """The measured near-miss: a 946s run on a 900s schedule.

    A critical job must declare a timeout; refusing to start is safer than
    letting an unbounded money job overrun its own schedule.
    """
    script = home / "scripts" / "ok.py"
    script.write_text("pass\n")
    _spec(home, "nolimit",
          f'job_id = "nolimit"\nscript = "{script}"\nruntime = "python"\n'
          "critical = true\ntimeout = 0\n")
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
