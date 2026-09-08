#!/usr/bin/env python3
"""
Regression tests for the six changes merged back from a downstream fork.

Two independent lineages of `jobrun.py` diverged from a common ancestor: the
fleet copy in this repo, and a trading host's copy that grew real fixes under
production pressure. Neither was a superset of the other, so a blind overwrite
in either direction would have destroyed tested work -- which is exactly what
these tests exist to prevent from being quietly undone later.

Each test names the defect it locks down and asserts the BEHAVIOUR, not the
presence of a symbol. Several of these were shipped bugs whose symptom was
silence, and silence is what a weak test also looks like.
"""

import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "scheduled-job-runner" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import jobrun as J  # noqa: E402
import jobrun_severity as S  # noqa: E402


@pytest.fixture
def home(monkeypatch, tmp_path):
    """An isolated HERMES_HOME with the dirs jobrun expects."""
    for sub in ("jobs.d", "scripts", "jobstate"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(J, "HERMES_HOME", tmp_path)
    monkeypatch.setattr(J, "SPEC_DIR", tmp_path / "jobs.d")
    monkeypatch.setattr(J, "STATE_DIR", tmp_path / "jobstate")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


def _spec(home, **kw):
    body = ['job_id = "t"', 'script = "t.py"']
    for k, v in kw.items():
        if isinstance(v, str):
            body.append(f'{k} = "{v}"')
        elif isinstance(v, dict):
            inner = ", ".join(f'{ik} = "{iv}"' for ik, iv in v.items())
            body.append(f"{k} = {{{inner}}}")
        else:
            body.append(f"{k} = {v}")
    (home / "jobs.d" / "t.toml").write_text("\n".join(body), encoding="utf-8")
    (home / "scripts" / "t.py").write_text("print('ok')\n", encoding="utf-8")
    return J.Spec.load("t")


# --------------------------------------------------------------------------
# 1. on_change actually gates, and is not a config value that does nothing
# --------------------------------------------------------------------------


def test_on_change_suppresses_only_a_repeated_message(home):
    """The whole point: identical output stays quiet, changed output speaks.

    This gate previously delegated to a module that exists only on one host.
    Everywhere else the import failed, the gate failed OPEN, and `on_change`
    silently behaved as `passthrough` -- a spec field that validated and did
    nothing. A config value that is accepted and inert is worse than one that
    is rejected, because the operator believes it took effect.
    """
    first, why_first = J._speech_gate("job-a", "nothing changed")
    repeat, _ = J._speech_gate("job-a", "nothing changed")
    changed, why_changed = J._speech_gate("job-a", "something else")

    assert first is True, "the first message must always be delivered"
    assert repeat is False, "an identical repeat must be suppressed"
    assert changed is True, "changed content must always break through"
    assert "changed" in why_first and "changed" in why_changed


def test_the_gate_is_keyed_per_job_not_globally(home):
    """Two jobs emitting the same text must not silence each other."""
    J._speech_gate("job-a", "same words")
    speak, _ = J._speech_gate("job-b", "same words")
    assert speak is True


def test_a_liveness_heartbeat_breaks_a_long_silence(home):
    """Suppression is keyed on content, but a quiet channel must still prove life.

    Without this, a job repeating itself for weeks is indistinguishable from a
    job that died -- the exact ambiguity the ledger exists to remove.
    """
    J._speech_gate("job-c", "steady state")
    suppressed, _ = J._speech_gate("job-c", "steady state")
    assert suppressed is False
    # Same content, but the heartbeat window has effectively elapsed.
    speak, why = J._speech_gate("job-c", "steady state", heartbeat_h=0.0)
    assert speak is True
    assert "heartbeat" in why


def test_the_gate_fails_open_on_unusable_state(home, monkeypatch):
    """A broken gate must degrade to CHATTY, never to silent.

    Failing closed here would silence the entire fleet from one bug, which is
    strictly worse than the noise this exists to reduce.
    """
    monkeypatch.setattr(J, "STATE_DIR", Path("/proc/definitely/not/writable"))
    speak, why = J._speech_gate("job-d", "anything")
    assert speak is True
    assert "unavailable" in why


def test_on_change_is_a_valid_policy_and_junk_is_still_rejected(home):
    assert _spec(home, output_policy="on_change").output_policy == "on_change"
    (home / "jobs.d" / "t.toml").write_text(
        'job_id = "t"\nscript = "t.py"\noutput_policy = "sometimes"\n', encoding="utf-8"
    )
    with pytest.raises(J.ConfigError):
        J.Spec.load("t")


# --------------------------------------------------------------------------
# 2. the failure card must name the failure, not the last log line
# --------------------------------------------------------------------------


def test_a_success_line_is_never_reported_as_the_error():
    """`stderr.splitlines()[-1]` reported whatever happened LAST.

    For any job that writes an audit trail to stderr that is usually a SUCCESS,
    so the owner got a DEGRADED banner over a sentence describing something
    that went right, with the real cause invisible several lines earlier.
    """
    out = "starting\nFAILED (venue rejected the order)"
    err = "item 3 processed\nitem 4 -> completed"
    detail = J._failure_detail(out, err)
    assert "FAILED (" in detail
    assert "-> completed" not in detail


def test_a_healthy_tally_is_not_mistaken_for_the_error():
    """A bare "FAILED" also matches "checked 26, kept 21, failed 1".

    That line is a STATISTIC. Reporting it tells the owner a failure happened
    without saying what it was -- the same defect one step smaller.
    """
    out = "checked 26, kept 21, failed 1"
    err = "Traceback (most recent call last): ValueError"
    assert "Traceback" in J._failure_detail(out, err)


def test_markers_extend_from_the_environment_without_forking_the_file(monkeypatch):
    """A domain fleet adds its own vocabulary through config, not a patched runner.

    The original markers were literal strings from one trading host's log
    format. Hard-coding them upstream would have made every other fleet carry
    another fleet's vocabulary, which is how a shared file becomes a fork.

    Deliberately NOT tested with importlib.reload: reloading rebinds the module
    object while other test modules still hold `from ... import Name`
    references, so an exception class imported elsewhere stops comparing equal
    to the reloaded one and an unrelated `pytest.raises` fails. Found the hard
    way -- this suite passed alone and broke a neighbour.
    """
    monkeypatch.setenv("JOBRUN_FAILURE_MARKERS", "KRAKEN EXPLODED|reactor scrammed")
    extended = J._marker_env("JOBRUN_FAILURE_MARKERS", J._FAILURE_MARKERS)
    assert "KRAKEN EXPLODED" in extended
    assert "reactor scrammed" in extended
    assert "Traceback" in extended, "defaults must survive"

    monkeypatch.setattr(J, "_FAILURE_MARKERS", extended)
    assert "KRAKEN EXPLODED" in J._failure_detail("all good\nKRAKEN EXPLODED at pier 9", "")


def test_marker_extension_never_drops_or_duplicates_defaults(monkeypatch):
    monkeypatch.setenv("X_MARKERS", "Traceback|  |brand new")
    got = J._marker_env("X_MARKERS", ("Traceback", "ERROR"))
    assert got == ("Traceback", "ERROR", "brand new"), "no dupes, no blanks, defaults kept"
    monkeypatch.delenv("X_MARKERS")
    assert J._marker_env("X_MARKERS", ("Traceback",)) == ("Traceback",)


# --------------------------------------------------------------------------
# 3. a growing body must not mint a fresh incident every run
# --------------------------------------------------------------------------


def test_a_drifting_body_keeps_one_stable_identity():
    """The condition is "production is behind". The commit list is churn.

    Hashing the body minted a brand-new incident every time the list grew, so
    dedup was bypassed, the alarm looked new forever, and it could never be
    acknowledged.
    """
    a = S.normalize_error("🔴 DEPLOY DRIFT: 14 commits behind, 3h old, sha abc123")
    b = S.normalize_error("🔴 DEPLOY DRIFT: 27 commits behind, 9h old, sha def456")
    assert a == b


def test_independent_findings_do_not_collapse_into_one_bucket():
    """Collapsing everything would hide a second real problem behind the first."""
    a = S.normalize_error("🔴 PRODUCTION CHECKOUT DIRTY: alpha.py")
    b = S.normalize_error("🔴 PRODUCTION CHECKOUT DIRTY: beta.py")
    assert a != b


def test_ordinary_errors_still_get_ordinary_normalisation():
    """The new branch must not swallow the normal path."""
    got = S.normalize_error("ValueError at 0xdeadbeef run_id=abc123")
    assert "run_id=RID" in got


def test_condition_prefixes_extend_from_the_environment(monkeypatch):
    """Same reload hazard as the marker test; assert the helper directly."""
    monkeypatch.setenv("JOBRUN_COLLAPSING_PREFIXES", "🔴 TIDE DRIFT:")
    extended = S._prefix_env("JOBRUN_COLLAPSING_PREFIXES", S._CONDITION_PREFIXES)
    assert "🔴 TIDE DRIFT:" in extended
    assert "🔴 DEPLOY DRIFT:" in extended, "defaults must survive"

    monkeypatch.setattr(S, "_CONDITION_PREFIXES", extended)
    monkeypatch.setattr(S, "_CONDITION_LINES", extended + S._CONDITION_LINES)
    a = S.normalize_error("🔴 TIDE DRIFT: 3 charts stale")
    b = S.normalize_error("🔴 TIDE DRIFT: 9 charts stale")
    assert a == b


# --------------------------------------------------------------------------
# 4. a timed-out job must be able to say WHERE it hung
# --------------------------------------------------------------------------


def test_children_run_with_faulthandler_so_a_timeout_leaves_a_stack(home):
    """Two live stalls produced `stderr_bytes: 0`.

    With nothing captured, "raise the ceiling" was the only available guess --
    which is not diagnosis, it is superstition.
    """
    assert J.build_env(_spec(home)).get("PYTHONFAULTHANDLER") == "1"


def test_a_job_may_still_choose_its_own_faulthandler_setting(home):
    """setdefault, not overwrite: an explicit spec value wins."""
    spec = _spec(home, env={"PYTHONFAULTHANDLER": "0"})
    assert J.build_env(spec).get("PYTHONFAULTHANDLER") == "0"


def test_our_own_process_group_is_never_reported_as_a_survivor():
    """Probing our own group would be jobrun asking to kill itself."""
    own = os.getpgrp()
    assert J._group_is_empty(own, own) is True
    assert J._group_is_empty(0, own) is True


def test_a_finished_group_is_recognised_as_empty():
    """A pgid that no longer exists must read as empty, not as a survivor.

    Reporting a dead group as alive turns a clean timeout into a spurious
    `wrapper_error`, which is a non-retryable state.
    """
    import subprocess

    p = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    pgid = os.getpgid(p.pid)
    p.wait()
    assert J._group_is_empty(pgid, os.getpgrp(), settle_s=2.0) is True


# --------------------------------------------------------------------------
# 5. a missing sidecar must fail LOUDLY, not disable the money guard
# --------------------------------------------------------------------------


def test_both_sidecars_import_and_are_never_silently_absent():
    """This used to catch ImportError and return (None, None).

    That disabled the preflight money-mismatch guard for EVERY job -- silently,
    and precisely while the install was broken. The guard's whole value is
    being present when things are wrong.
    """
    sev, rep = J._v2_mods()
    assert sev is not None
    assert rep is not None


# --------------------------------------------------------------------------
# 6. self-test fixtures must never touch live state
# --------------------------------------------------------------------------


def test_selftest_redirects_both_the_incident_db_and_hermes_home():
    """Two independent fixes for one bug; both are kept deliberately.

    The explicit override redirects the one path we knew about. Redirecting
    HERMES_HOME closes the CLASS, so a helper not yet written cannot resolve
    state into a live profile. `st-fail` and `st-timeout` were found sitting in
    a production incident table because only part of this was in place.
    """
    src = (SCRIPTS / "jobrun.py").read_text(encoding="utf-8")
    body = src[src.index("def selftest(") :]
    assert 'os.environ["JOBRUN_INCIDENT_DB"]' in body
    assert 'os.environ["HERMES_HOME"] = str(tmp)' in body
    assert "_saved_home" in body, "HERMES_HOME must be restored on exit"


def test_the_runner_carries_no_single_host_paths():
    """This file is shared. One fleet's absolute paths must not ride along.

    A hard-coded `/srv/<something>` is how a shared runner quietly becomes one
    host's fork that everyone else carries.
    """
    for name in ("jobrun.py", "jobrun_severity.py", "jobrun_repair.py"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        assert "/srv/" not in code, f"{name} carries a host-specific absolute path"


# --------------------------------------------------------------------------
# 7. a monitor that exits 0 while reporting an emergency must still page
#    (recovered from a divergent host copy during the same reconciliation)
# --------------------------------------------------------------------------


def _run(home, spec_body, script_body, env=None):
    """Execute jobrun end-to-end as a real subprocess and capture its output."""
    import subprocess

    (home / "jobs.d" / "m.toml").write_text(spec_body, encoding="utf-8")
    (home / "scripts" / "m.py").write_text(script_body, encoding="utf-8")
    e = dict(os.environ, HERMES_HOME=str(home))
    e.update(env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "jobrun.py"), "--spec", "m"],
        capture_output=True,
        text=True,
        env=e,
        timeout=120,
    )


def test_a_successful_run_reporting_a_critical_condition_still_pages(tmp_path):
    """CRITICAL was unreachable for the normal shape of a monitor.

    A monitor RAN FINE; what it FOUND is the emergency. Returning EXIT_OK
    without classifying meant a correct critical sentinel on a real money-path
    breach rendered as an ordinary passthrough alert, indistinguishable from a
    routine notice.
    """
    for sub in ("jobs.d", "scripts", "jobstate"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    spec = 'job_id = "m"\nscript = "m.py"\nmoney = "live"\ncritical = true\ntimeout = 60\n'
    script = (
        "import json\n"
        "print('gross leverage 4.1x over the 3.0x cap')\n"
        "print('@@JOBRUN_RESULT@@ ' + json.dumps("
        "{'schema':'jobrun.result/v1','outcome':'critical','summary':'leverage breach'}))\n"
    )
    r = _run(tmp_path, spec, script)
    assert r.returncode == 0, "the job itself succeeded and must not be marked failed"
    combined = r.stdout + r.stderr
    assert "CRITICAL" in combined, f"critical condition did not page:\n{combined}"
    assert "exited 0" not in combined, "'exited 0' beside a red stop sign contradicts itself"


def test_an_ordinary_successful_run_is_not_escalated(tmp_path):
    """The guard above must not turn every healthy job into a page.

    Only an explicit sentinel may escalate. If routine output could trip this,
    the fix would be worse than the bug it closes.
    """
    for sub in ("jobs.d", "scripts", "jobstate"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    spec = 'job_id = "m"\nscript = "m.py"\nmoney = "live"\ncritical = true\ntimeout = 60\n'
    r = _run(tmp_path, spec, "print('checked 12 positions, all within limits')\n")
    assert r.returncode == 0
    assert "CRITICAL" not in (r.stdout + r.stderr)
    assert "all within limits" in r.stdout, "normal output must still pass through verbatim"


def test_the_machine_sentinel_never_reaches_a_human(tmp_path):
    """`@@JOBRUN_RESULT@@ {...}` is addressed to the RUNNER, not the owner.

    It was appearing at the top of owner-facing messages. Stripped centrally so
    every future emitter inherits the fix instead of each wrapper repeating it.
    """
    for sub in ("jobs.d", "scripts", "jobstate"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    spec = 'job_id = "m"\nscript = "m.py"\ntimeout = 60\n'
    script = (
        "import json\n"
        "print('12 markets scanned, 2 flagged')\n"
        "print('@@JOBRUN_RESULT@@ ' + json.dumps({'schema':'jobrun.result/v1','outcome':'healthy'}))\n"
    )
    r = _run(tmp_path, spec, script)
    assert r.returncode == 0
    assert "@@JOBRUN_RESULT@@" not in r.stdout, "machine line leaked into owner output"
    assert "12 markets scanned" in r.stdout, "the real message must survive"


# --------------------------------------------------------------------------
# 8. upstream review findings on this very PR
# --------------------------------------------------------------------------


def test_a_watchdogs_own_defect_is_not_deduped_against_the_drift_it_reports():
    """Collapsing the headline must not swallow an unrelated failure.

    Returning the headline alone discarded every unrecognised line, so a run
    carrying a drift headline AND a fresh traceback fingerprinted IDENTICALLY
    to ordinary drift. The watchdog developing its own bug was silently deduped
    against the condition it exists to report -- a monitor going blind while
    still appearing to work, which is worse than the noise the collapsing
    removes.
    """
    drift = S.normalize_error("🔴 DEPLOY DRIFT: 14 commits behind, 3h old")
    mixed = S.normalize_error(
        "🔴 DEPLOY DRIFT: 14 commits behind, 3h old\n"
        "Traceback (most recent call last):\n"
        "ValueError: the watchdog itself broke"
    )
    assert mixed != drift, "an unrelated failure must not fingerprint as routine drift"
    assert "ValueError" in mixed, "the real failure must survive into the identity"
    assert mixed.startswith("🔴 DEPLOY DRIFT:"), "the stable condition is still the prefix"


def test_two_different_defects_alongside_the_same_drift_stay_distinct():
    a = S.normalize_error("🔴 DEPLOY DRIFT: 14 behind\nValueError: alpha exploded")
    b = S.normalize_error("🔴 DEPLOY DRIFT: 14 behind\nKeyError: beta missing")
    assert a != b


def test_pure_drift_still_collapses_as_the_count_grows():
    """The original fix must survive the correction to it."""
    a = S.normalize_error("🔴 DEPLOY DRIFT: 14 commits behind, 3h old, sha aaa")
    b = S.normalize_error("🔴 DEPLOY DRIFT: 96 commits behind, 30h old, sha bbb")
    assert a == b


def test_concurrent_runs_cannot_both_decide_the_message_changed(home):
    """overlap="allow" + on_change: two finishers racing the same state file.

    Unlocked, both read the previous digest before either writes, so both
    decide "changed" and both speak -- exactly the duplicate the gate exists to
    prevent. They also shared one `.json.tmp`, so one replace() could fail on a
    file the other had already moved.

    PROCESSES PLUS A BARRIER, not threads. Two weaker versions of this test
    passed even with the lock REMOVED and therefore proved nothing: threads
    serialized under the GIL on a fast path, and bare processes start ~10ms
    apart, so the first finished before the second began. The barrier releases
    every worker into the gate at the same instant, which is the only way the
    unlocked read-decide-write window is actually open. Verified by deleting
    the flock and watching this go RED.
    """
    import multiprocessing as mp

    N = 8

    def child(state_dir, barrier, q):
        import importlib

        sys.path.insert(0, str(SCRIPTS))
        m = importlib.import_module("jobrun")
        m.STATE_DIR = state_dir
        barrier.wait(timeout=30)  # everyone enters the gate together
        q.put(m._speech_gate("racer", "identical body"))

    ctx = mp.get_context("fork")
    q = ctx.Queue()
    barrier = ctx.Barrier(N)
    procs = [ctx.Process(target=child, args=(J.STATE_DIR, barrier, q)) for _ in range(N)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)

    results = [q.get(timeout=30) for _ in range(N)]
    spoke = [r for r in results if r[0]]
    assert len(spoke) == 1, f"exactly one concurrent run may speak, got {len(spoke)}: {results}"


def test_the_public_file_carries_no_dated_incident_forensics():
    """This repo is public. Internal dates, PR numbers and SHAs do not belong.

    The reasoning is what generalises; the incident log is what leaks.
    """
    for name in ("jobrun.py", "jobrun_severity.py", "jobrun_repair.py"):
        text = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "PR #" not in text, f"{name} names an internal pull request"
        assert not re.search(r"\b\d{2}-\d{2} \d{2}:\d{2}Z", text), f"{name} has incident stamps"
