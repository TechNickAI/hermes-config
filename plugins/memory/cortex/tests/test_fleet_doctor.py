from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fleet_doctor.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fleet_doctor", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return load_module()


def make_target(mod, label="alpha"):
    return mod.Target(label=label, python="py", doctor="doc.py", home="/home", host=None)


def write_targets(tmp_path: Path, entries) -> Path:
    path = tmp_path / "targets.json"
    path.write_text(json.dumps({"targets": entries}))
    return path


# ------------------------------------------------------------------ targets


def test_targets_load_from_file(mod, tmp_path):
    """Targets come from config so no host names live in this repo."""
    path = write_targets(tmp_path, [
        {"label": "alpha", "python": "/py", "doctor": "/doc.py", "home": "/home/a"},
        {"label": "beta", "python": "/py", "doctor": "/doc.py", "home": "/home/b",
         "host": "remote"},
    ])

    targets = mod.load_targets(path)

    assert [t.label for t in targets] == ["alpha", "beta"]
    assert targets[0].host is None, "omitted host means local"
    assert targets[1].host == "remote"


def test_bare_list_of_targets_is_accepted(mod, tmp_path):
    path = tmp_path / "targets.json"
    path.write_text(json.dumps([
        {"label": "solo", "python": "/py", "doctor": "/doc.py", "home": "/home"},
    ]))

    assert [t.label for t in mod.load_targets(path)] == ["solo"]


def test_object_without_targets_key_is_a_clean_error(mod, tmp_path):
    """A misspelled key must not raise KeyError and break the exit-zero contract."""
    path = tmp_path / "targets.json"
    path.write_text(json.dumps({"target": [{"label": "a"}]}))  # note: singular

    with pytest.raises(ValueError) as excinfo:
        mod.load_targets(path)

    assert "targets" in str(excinfo.value)


def test_any_bad_targets_file_reports_and_exits_zero(mod, tmp_path, capsys):
    """Malformed JSON, wrong key, or a missing file must all speak up, never traceback."""
    cases = {
        "missing": tmp_path / "nope.json",
        "malformed": tmp_path / "bad.json",
        "wrong-key": tmp_path / "wrong.json",
    }
    cases["malformed"].write_text("{not json")
    cases["wrong-key"].write_text(json.dumps({"target": []}))

    for name, path in cases.items():
        code = mod.main(["--targets", str(path)])
        out = capsys.readouterr().out
        assert code == 0, name
        assert "🔴" in out, name
        assert "could not load targets" in out, name


def test_local_paths_expand_tilde(mod, tmp_path):
    path = write_targets(tmp_path, [
        {"label": "a", "python": "~/py", "doctor": "~/doc.py", "home": "~/hermes"},
    ])

    target = mod.load_targets(path)[0]

    assert not target.home.startswith("~"), "local ~ must expand to a real path"
    assert target.home.endswith("/hermes")


def test_remote_paths_keep_tilde_for_the_remote_shell(mod, tmp_path):
    """A remote ~ belongs to THAT machine, so expanding it locally is wrong."""
    path = write_targets(tmp_path, [
        {"label": "r", "host": "box", "python": "~/py", "doctor": "~/doc.py",
         "home": "~/hermes"},
    ])

    target = mod.load_targets(path)[0]

    assert target.home == "~/hermes"


def test_remote_arguments_survive_spaces_and_tilde(mod, monkeypatch):
    """ssh runs a shell: spaces must be quoted, but ~ must stay expandable.

    shlex.quote('~/x') gives "'~/x'", which a remote shell reads as a literal
    directory named ~ -- confirmed over real ssh. Both properties must hold.
    """
    seen = {}

    class Done:
        returncode = 0
        stdout = json.dumps({"ok": True, "repairs": []})
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: (seen.update(cmd=cmd), Done())[1])
    target = mod.Target.from_dict({
        "label": "r", "host": "box",
        "python": "~/venv/bin/python", "doctor": "~/d.py", "home": "~/.hermes/my agent",
    })

    mod.probe(target, "long term memory", 10)

    remote = seen["cmd"][-1]
    assert remote.startswith("~/venv/bin/python"), "tilde must stay bare to expand remotely"
    assert "'.hermes/my agent'" in remote, "the space must be quoted"
    assert "'long term memory'" in remote, "a multi-word query must stay one argument"
    assert "'~/" not in remote, "quoting the tilde would make it a literal directory"


def test_incomplete_target_is_rejected_by_name(mod, tmp_path):
    """A typo'd key must fail loudly, naming the target, not check the wrong path."""
    path = write_targets(tmp_path, [
        {"label": "alpha", "python": "/py", "doctor": "/doc.py"},  # no home
    ])

    with pytest.raises(ValueError) as excinfo:
        mod.load_targets(path)

    assert "alpha" in str(excinfo.value)
    assert "home" in str(excinfo.value)


def test_empty_target_file_is_rejected(mod, tmp_path):
    """Zero targets must not read as 'fleet healthy'."""
    with pytest.raises(ValueError):
        mod.load_targets(write_targets(tmp_path, []))


def test_unloadable_target_file_reports_instead_of_going_silent(mod, tmp_path, capsys):
    """Silence means all-clear, so a broken target file must still speak up."""
    missing = tmp_path / "nope.json"

    code = mod.main(["--targets", str(missing)])

    out = capsys.readouterr().out
    assert code == 0
    assert "🔴" in out
    assert "could not load targets" in out


# ---------------------------------------------------------------- reporting


def test_healthy_fleet_prints_nothing(mod):
    """Silence is the healthy signal."""
    results = [{"label": "a", "state": "healthy"}, {"label": "b", "state": "healthy"}]

    assert mod.format_report(results) == ""


def test_repair_is_silent_after_restoring_health(mod):
    results = [
        {"label": "a", "state": "healthy"},
        {"label": "b", "state": "repaired", "detail": "fts_rebuilt (2/2 embedded)"},
    ]

    assert mod.format_report(results) == ""


def test_broken_alarms_and_names_the_reason(mod):
    results = [
        {"label": "a", "state": "healthy"},
        {"label": "b", "state": "broken", "detail": "FTS still corrupt"},
    ]

    report = mod.format_report(results)

    assert "🔴" in report
    assert "FTS still corrupt" in report


def test_alarm_carries_blast_radius(mod):
    """An alarm must say what is still fine, not only what broke."""
    results = [
        {"label": "a", "state": "healthy"},
        {"label": "b", "state": "healthy"},
        {"label": "c", "state": "broken", "detail": "boom"},
    ]

    assert "still healthy: 2 of 3" in mod.format_report(results)


def test_indeterminate_warns_rather_than_declaring_down(mod):
    """An unparseable probe is not evidence of failure."""
    results = [
        {"label": "a", "state": "healthy"},
        {"label": "b", "state": "indeterminate", "detail": "no output"},
    ]

    report = mod.format_report(results)

    assert "⚠️" in report
    assert "🔴" not in report
    assert "indeterminate" in report


def test_setup_failure_is_treated_as_broken(mod):
    results = [{"label": "a", "state": "setup", "detail": "python missing"}]

    report = mod.format_report(results)

    assert "🔴" in report
    assert "python missing" in report


def test_main_prints_absolutely_nothing_when_fleet_is_healthy(mod, tmp_path, monkeypatch, capsys):
    """End-to-end silence, not just an empty report string.

    Cron runs this with no_agent, so anything on stdout becomes a message. A
    blank line would deliver an empty notification every single night.
    """
    path = write_targets(tmp_path, [
        {"label": "a", "python": "/py", "doctor": "/doc.py", "home": "/home"},
        {"label": "b", "python": "/py", "doctor": "/doc.py", "home": "/home"},
    ])
    monkeypatch.setattr(mod, "check", lambda t, *a, **k: {"label": t.label, "state": "healthy"})

    code = mod.main(["--targets", str(path)])

    assert code == 0
    assert capsys.readouterr().out == ""


def test_main_exits_zero_even_when_alarming(mod, tmp_path, monkeypatch, capsys):
    """A non-zero exit would make the scheduler emit a second, redundant alert."""
    path = write_targets(tmp_path, [
        {"label": "a", "python": "/py", "doctor": "/doc.py", "home": "/home"},
    ])
    monkeypatch.setattr(mod, "check", lambda *a, **k: {
        "label": "a", "state": "broken", "detail": "boom", "retryable": False,
    })

    code = mod.main(["--targets", str(path)])

    assert code == 0
    assert "🔴" in capsys.readouterr().out


# -------------------------------------------------------------------- retry


def test_transient_failure_is_retried_and_recovery_is_silent(mod, monkeypatch):
    calls = []
    outcomes = [
        {"label": "alpha", "state": "broken", "detail": "retrieval degraded", "retryable": True},
        {"label": "alpha", "state": "healthy"},
    ]

    def fake_probe(target, query, timeout):
        calls.append(target.label)
        return outcomes[len(calls) - 1]

    monkeypatch.setattr(mod, "probe", fake_probe)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    result = mod.check(make_target(mod), "memory", 10, 0)

    assert len(calls) == 2, "a soft failure must get a second look"
    assert result["state"] == "healthy"
    assert mod.format_report([result]) == ""


def test_durable_failure_is_not_retried(mod, monkeypatch):
    """Corruption is a fact, not a blip: report it the first time."""
    calls = []

    def fake_probe(target, query, timeout):
        calls.append(target.label)
        return {"label": "alpha", "state": "broken",
                "detail": "FTS still corrupt", "retryable": False}

    monkeypatch.setattr(mod, "probe", fake_probe)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    result = mod.check(make_target(mod), "memory", 10, 0)

    assert len(calls) == 1
    assert result["state"] == "broken"


def test_persistent_transient_reports_the_retry_result(mod, monkeypatch):
    """The alarm must carry the SECOND probe's detail, not stale first-run text."""
    outcomes = [
        {"label": "alpha", "state": "broken", "detail": "first-detail", "retryable": True},
        {"label": "alpha", "state": "broken", "detail": "second-detail", "retryable": True},
    ]
    calls = []

    def fake_probe(target, query, timeout):
        calls.append(target.label)
        return outcomes[len(calls) - 1]

    monkeypatch.setattr(mod, "probe", fake_probe)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    result = mod.check(make_target(mod), "memory", 10, 0)

    assert len(calls) == 2
    assert result["detail"] == "second-detail"


# ------------------------------------------------------------- classification


def test_healthy_doctor_output_with_no_repairs_is_healthy(mod, monkeypatch):
    _stub_run(mod, monkeypatch, {"ok": True, "repairs": []})

    assert mod.probe(make_target(mod), "memory", 10)["state"] == "healthy"


def test_repairs_are_surfaced_with_coverage(mod, monkeypatch):
    _stub_run(mod, monkeypatch, {
        "ok": True, "repairs": ["fts_rebuilt"],
        "embeddings_after": {"embedded": 5, "pages": 5},
    })

    result = mod.probe(make_target(mod), "memory", 10)

    assert result["state"] == "repaired"
    assert "fts_rebuilt" in result["detail"]
    assert "5/5" in result["detail"]


def test_mixed_alarm_keeps_repair_context(mod):
    """Silencing success must not erase context from a real alarm."""
    report = mod.format_report([
        {"label": "alpha", "state": "indeterminate",
         "detail": "endpoint timed out"},
        {"label": "beta", "state": "repaired",
         "detail": "fts_rebuilt (5/5 embedded)"},
        {"label": "gamma", "state": "healthy"},
    ])

    assert "inconclusive" in report
    assert "alpha" in report and "endpoint timed out" in report
    assert "beta" in report and "fts_rebuilt" in report


def test_unparseable_output_is_indeterminate_not_broken(mod, monkeypatch):
    """Absence of evidence is not evidence of failure."""
    _stub_run(mod, monkeypatch, raw="Traceback: something exploded")

    result = mod.probe(make_target(mod), "memory", 10)

    assert result["state"] == "indeterminate"
    assert result["retryable"] is True


def test_setup_error_is_not_retryable(mod, monkeypatch):
    _stub_run(mod, monkeypatch, {"setup_error": "interpreter missing"})

    result = mod.probe(make_target(mod), "memory", 10)

    assert result["state"] == "setup"
    assert result["retryable"] is False


def test_corruption_is_durable_but_endpoint_blip_is_retryable(mod, monkeypatch):
    _stub_run(mod, monkeypatch, {
        "ok": False,
        "fts_after": {"ok": False},
        "embeddings_after": {"ok": True},
        "retrieval": {"ok": True},
        "embedding_endpoint_healthy": True,
    })
    corrupt = mod.probe(make_target(mod), "memory", 10)

    _stub_run(mod, monkeypatch, {
        "ok": False,
        "fts_after": {"ok": True},
        "embeddings_after": {"ok": True},
        "retrieval": {"ok": False, "sources": ["fts"]},
        "embedding_endpoint_healthy": False,
    })
    blip = mod.probe(make_target(mod), "memory", 10)

    assert corrupt["retryable"] is False, "corruption must alarm immediately"
    assert blip["retryable"] is True, "an endpoint blip deserves a retry"
    assert "retrieval degraded" in blip["detail"]


def test_remote_target_runs_over_ssh(mod, monkeypatch):
    """Remote profiles must be reached via ssh, not executed locally."""
    seen = {}

    class Done:
        returncode = 0
        stdout = json.dumps({"ok": True, "repairs": []})
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return Done()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    target = mod.Target(label="r", python="/py", doctor="/d.py", home="/h", host="box")

    mod.probe(target, "memory", 10)

    assert seen["cmd"][0] == "ssh"
    assert "box" in seen["cmd"]


def test_timeout_is_unreachable_and_retryable(mod, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise mod.subprocess.TimeoutExpired(cmd, 10)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    result = mod.probe(make_target(mod), "memory", 10)

    assert result["state"] == "unreachable"
    assert result["retryable"] is True


def _stub_run(mod, monkeypatch, payload=None, raw=None):
    class Done:
        returncode = 0
        stdout = raw if raw is not None else json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: Done())
