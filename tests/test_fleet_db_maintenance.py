"""Tests for the fleet-db-maintenance dbmaint.py script.

Real subprocesses against real SQLite files. The whole value of this script is
that it deletes production data correctly, so mocking the database would test
nothing that matters.
"""

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).parent.parent / "skills" / "fleet-db-maintenance"
DBMAINT = SKILL / "scripts" / "dbmaint.py"

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_FAILURE = 3


def test_skill_files_exist():
    assert (SKILL / "SKILL.md").is_file()
    assert DBMAINT.is_file()


def test_dbmaint_compiles():
    subprocess.run([sys.executable, "-m", "py_compile", str(DBMAINT)], check=True)


def _load(name="dbmaint"):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, DBMAINT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_db(path: Path, *, cron=5, telegram=3, age_days=30):
    """A state.db shaped like Hermes': sessions + messages, WAL mode."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, "
        "started_at REAL, last_activity_at REAL, end_reason TEXT, "
        "archived INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, "
        "role TEXT, content TEXT, timestamp REAL, active INTEGER DEFAULT 1, "
        "compacted INTEGER DEFAULT 0)"
    )
    old = time.time() - age_days * 86400
    for i in range(cron):
        sid = f"cron_abc_{i}"
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,0,0)",
            (sid, "cron", old, old, "completed"),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
            (sid, "user", "x" * 500, old),
        )
    for i in range(telegram):
        sid = f"tg_{i}"
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,0,0)",
            (sid, "telegram", old, old, "completed"),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
            (sid, "user", "important human words", old),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "state.db"
    _make_db(p)
    return p


# --- the safety invariant -------------------------------------------------

def test_refuses_human_sources(db):
    """Asking to prune telegram must be refused, not honoured."""
    mod = _load()
    args = type("A", (), {
        "profile": "x", "days": 10, "sources": ["telegram"], "apply": True,
        "no_vacuum": True, "vacuum_min_mb": 500, "keep_backup": False,
        "max_lock_seconds": 45.0, "force_vacuum": False,
    })()
    mod.resolve_db = lambda p: db
    with pytest.raises(ValueError, match="refusing to prune non-machine"):
        mod.run(args, lambda m: None)


def test_prunable_sources_excludes_human_channels():
    mod = _load()
    for human in ("telegram", "slack", "cli", "discord", "imessage", "webhook"):
        assert human not in mod.PRUNABLE_SOURCES


def test_human_session_count_ignores_machine_sources(db):
    mod = _load()
    assert mod._human_session_count(db) == 3  # telegram only


# --- compaction mechanics -------------------------------------------------

def test_vacuum_shrinks_file_after_delete(db):
    """The full sequence must actually return bytes to the OS.

    This is the regression guard for the WAL trap, and it holds a SECOND
    connection open for the duration -- which is what makes it a real test.

    Measured behaviour: when maintenance is the only connection, closing it
    triggers an implicit checkpoint and the file shrinks whether or not we
    checkpoint explicitly, so a naive version of this test passes even with
    the fix removed. With another connection open (a live gateway, i.e. every
    real run) the implicit checkpoint cannot complete, and omitting the
    trailing wal_checkpoint(TRUNCATE) leaves the whole rebuilt database in
    the WAL: 19 MB vs 0 MB in a direct A/B.
    """
    mod = _load()
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE bulk (id INTEGER PRIMARY KEY, blob TEXT)")
    conn.executemany(
        "INSERT INTO bulk (blob) VALUES (?)", [("y" * 4000,) for _ in range(4000)]
    )
    conn.commit()
    conn.execute("DELETE FROM bulk")
    conn.commit()
    conn.close()

    big = db.stat().st_size

    # Stand in for the live gateway: a concurrent reader that prevents the
    # implicit close-time checkpoint from masking a missing explicit one.
    gateway = sqlite3.connect(str(db))
    gateway.execute("SELECT COUNT(*) FROM sessions").fetchone()
    try:
        result = mod.compact(db, lambda m: None)
        after = db.stat().st_size
    finally:
        gateway.close()

    assert after < big, (
        f"VACUUM did not reclaim space ({big} -> {after}); "
        "the trailing wal_checkpoint(TRUNCATE) is missing"
    )
    assert result["after"]["db_mb"] <= result["before"]["db_mb"]
    wal = Path(str(db) + "-wal")
    assert not wal.exists() or wal.stat().st_size < 1_000_000


def test_backup_is_verified_and_readable(db, tmp_path):
    mod = _load()
    dest = tmp_path / "backup.db"
    mod.backup(db, dest, lambda m: None)
    assert dest.is_file() and dest.stat().st_size > 0
    conn = sqlite3.connect(str(dest))
    assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 8
    conn.close()


def test_backup_aborts_when_output_unusable(db, tmp_path, monkeypatch):
    """An unusable backup must abort loudly, never be silently accepted.

    The contract is "raise rather than proceed" — compaction must not run
    behind a backup we cannot restore from.
    """
    import sqlite3 as _sq

    mod = _load()
    dest = tmp_path / "bad.db"

    real_connect = mod.sqlite3.connect

    def fake_connect(path, *a, **k):
        if str(path) == str(dest):
            # A file that exists and is non-empty but is not a database.
            dest.write_bytes(b"SQLite format 3\x00" + os.urandom(4096))
        return real_connect(path, *a, **k)

    monkeypatch.setattr(mod.sqlite3, "connect", fake_connect)
    with pytest.raises((RuntimeError, _sq.DatabaseError)):
        mod.backup(db, dest, lambda m: None)


def test_backup_rejects_empty_output(db, tmp_path, monkeypatch):
    """A zero-byte backup must be caught by the explicit size guard."""
    mod = _load()
    dest = tmp_path / "empty.db"

    class NoopBackup:
        def backup(self, out):
            dest.write_bytes(b"")

        def close(self):
            pass

    monkeypatch.setattr(mod, "_connect", lambda *a, **k: NoopBackup())
    monkeypatch.setattr(
        mod.sqlite3, "connect", lambda *a, **k: type(
            "C", (), {"close": lambda self: None}
        )()
    )
    with pytest.raises(RuntimeError, match="missing or empty"):
        mod.backup(db, dest, lambda m: None)


def test_integrity_ok(db):
    assert _load().integrity(db) == "ok"


# --- output parsing (the silent-failure class) ----------------------------

def _fake_hermes(tmp_path, stdout, rc=0):
    """Install a fake `hermes` binary so prune() can be driven end-to-end."""
    fake = tmp_path / "hermes"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.exit({rc})\n"
    )
    fake.chmod(0o755)
    return fake


def test_parses_count_for_subagent_ids(tmp_path, monkeypatch):
    """Subagent session ids are NOT prefixed with the source name.

    Regression guard for a measured bug: cron ids look like
    `cron_<hash>_<stamp>` but subagent ids look like `20260701_103305_1d2614`.
    A parser matching on an f"{src}_" prefix reported 0 while the CLI was
    really deleting 124 sessions -- a silent retention failure that looks
    identical to a healthy no-op.
    """
    mod = _load()
    real = (
        "124 session(s) match (last active before 2026-08-12 09:03, "
        "source 'subagent'; oldest activity 2026-07-01 10:35):\n"
        "  20260701_103305_1d2614  2026-07-01 10:35  subagent   work   48 msgs\n"
        "  20260701_103547_58e5e2  2026-07-01 10:37  subagent   work   42 msgs\n"
    )
    fake = _fake_hermes(tmp_path, real)
    monkeypatch.setattr(mod, "_hermes_bin", lambda: str(fake))
    out = mod.prune("p", 10, ["subagent"], False, lambda m: None)
    assert out["subagent"] == 124


def test_parses_count_for_cron_ids(tmp_path, monkeypatch):
    mod = _load()
    real = (
        "8323 session(s) match (last active before 2026-08-12 09:03, "
        "source 'cron'):\n"
        "  cron_047e1a5f26f3_20260623_000057  2026-06-23 00:03  cron  14 msgs\n"
    )
    fake = _fake_hermes(tmp_path, real)
    monkeypatch.setattr(mod, "_hermes_bin", lambda: str(fake))
    out = mod.prune("p", 10, ["cron"], False, lambda m: None)
    assert out["cron"] == 8323


def test_no_matches_parses_as_zero(tmp_path, monkeypatch):
    mod = _load()
    fake = _fake_hermes(tmp_path, "No sessions match (source 'cron').\n")
    monkeypatch.setattr(mod, "_hermes_bin", lambda: str(fake))
    assert mod.prune("p", 10, ["cron"], False, lambda m: None)["cron"] == 0


def test_unparseable_output_raises_rather_than_reporting_zero(tmp_path, monkeypatch):
    """An unrecognised format must fail loudly.

    Reporting 0 for output we could not parse is the exact failure mode that
    makes a broken pruner look healthy for months.
    """
    mod = _load()
    fake = _fake_hermes(tmp_path, "Wubba lubba dub dub\n")
    monkeypatch.setattr(mod, "_hermes_bin", lambda: str(fake))
    with pytest.raises(RuntimeError, match="could not parse"):
        mod.prune("p", 10, ["cron"], False, lambda m: None)


def test_nonzero_exit_raises(tmp_path, monkeypatch):
    mod = _load()
    fake = _fake_hermes(tmp_path, "boom\n", rc=1)
    monkeypatch.setattr(mod, "_hermes_bin", lambda: str(fake))
    with pytest.raises(RuntimeError, match="exited 1"):
        mod.prune("p", 10, ["cron"], False, lambda m: None)


def test_apply_flag_passed_through(tmp_path, monkeypatch):
    """--yes when applying, --dry-run otherwise. Getting this backwards
    would either delete during a dry run or silently never delete."""
    mod = _load()
    captured = {}
    real_run = mod.subprocess.run

    def spy(cmd, **kw):
        captured["cmd"] = cmd
        return real_run(
            [sys.executable, "-c", "print('0 session(s) match')"], **kw
        )

    monkeypatch.setattr(mod, "_hermes_bin", lambda: "/bin/true")
    monkeypatch.setattr(mod.subprocess, "run", spy)

    mod.prune("p", 10, ["cron"], False, lambda m: None)
    assert "--dry-run" in captured["cmd"] and "--yes" not in captured["cmd"]

    mod.prune("p", 10, ["cron"], True, lambda m: None)
    assert "--yes" in captured["cmd"] and "--dry-run" not in captured["cmd"]


# --- run() end-to-end failure paths ---------------------------------------

def _args(**over):
    base = dict(
        profile="p", days=10, sources=["cron"], apply=True,
        no_vacuum=True, vacuum_min_mb=500, keep_backup=False,
        max_lock_seconds=45.0, force_vacuum=False,
    )
    base.update(over)
    return type("A", (), base)()


def test_human_count_abort_actually_triggers(db, monkeypatch):
    """The invariant must fire when human sessions disappear.

    Proven by simulating a prune that deletes a telegram session -- the exact
    catastrophe the allowlist is meant to prevent. Without this test the abort
    could be deleted entirely and the suite would stay green.
    """
    mod = _load()
    monkeypatch.setattr(mod, "resolve_db", lambda p: db)

    def rogue(profile, days, sources, apply, log):
        conn = sqlite3.connect(str(db))
        conn.execute(
            "DELETE FROM sessions WHERE id = "
            "(SELECT id FROM sessions WHERE source = 'telegram' LIMIT 1)"
        )
        conn.commit()
        conn.close()
        return {"cron": 1}

    monkeypatch.setattr(mod, "prune", rogue)
    with pytest.raises(RuntimeError, match="human session count changed"):
        mod.run(_args(), lambda m: None)


def test_invariant_checked_even_when_prune_raises(db, monkeypatch):
    """A prune that deletes human rows and THEN fails must still be caught.

    Skipping the invariant on the error path is exactly when it matters most:
    a subprocess can delete rows and then time out or be killed.
    """
    mod = _load()
    monkeypatch.setattr(mod, "resolve_db", lambda p: db)

    def rogue_then_fail(profile, days, sources, apply, log):
        conn = sqlite3.connect(str(db))
        conn.execute(
            "DELETE FROM sessions WHERE id = "
            "(SELECT id FROM sessions WHERE source = 'telegram' LIMIT 1)"
        )
        conn.commit()
        conn.close()
        raise RuntimeError("prune timed out")

    monkeypatch.setattr(mod, "prune", rogue_then_fail)
    with pytest.raises(RuntimeError, match="human session count changed"):
        mod.run(_args(), lambda m: None)


def test_prune_failure_propagates(db, monkeypatch):
    """A failed prune that harmed nothing must still fail the run, not pass."""
    mod = _load()
    monkeypatch.setattr(mod, "resolve_db", lambda p: db)

    def boom(profile, days, sources, apply, log):
        raise RuntimeError("cli exploded")

    monkeypatch.setattr(mod, "prune", boom)
    with pytest.raises(RuntimeError, match="retention failed"):
        mod.run(_args(), lambda m: None)


def test_backup_taken_before_retention(db, monkeypatch):
    """Backup must precede the first destructive call, not follow it.

    If prune ran first, a prune that deleted rows and then died would leave
    nothing to restore from.
    """
    mod = _load()
    monkeypatch.setattr(mod, "resolve_db", lambda p: db)
    order = []

    real_backup = mod.backup
    monkeypatch.setattr(
        mod, "backup",
        lambda d, dest, log: (order.append("backup"), real_backup(d, dest, log))[1],
    )
    monkeypatch.setattr(
        mod, "prune",
        lambda *a, **k: (order.append("prune"), {"cron": 0})[1],
    )
    mod.run(_args(), lambda m: None)
    assert order == ["backup", "prune"]


def test_backup_survives_compaction_failure(db, monkeypatch):
    """On compaction failure the backup must NOT be deleted."""
    mod = _load()
    monkeypatch.setattr(mod, "resolve_db", lambda p: db)
    monkeypatch.setattr(mod, "prune", lambda *a, **k: {"cron": 0})
    monkeypatch.setattr(
        mod, "compact",
        lambda d, log: (_ for _ in ()).throw(RuntimeError("vacuum died")),
    )
    with pytest.raises(RuntimeError, match="vacuum died"):
        mod.run(_args(no_vacuum=False, vacuum_min_mb=0), lambda m: None)

    leftovers = list(db.parent.glob("*.premaint-*"))
    assert leftovers, "backup was deleted despite compaction failure"


def test_backup_removed_on_success(db, monkeypatch):
    """Housekeeping: a clean run must not leave backups accumulating."""
    mod = _load()
    monkeypatch.setattr(mod, "resolve_db", lambda p: db)
    monkeypatch.setattr(mod, "prune", lambda *a, **k: {"cron": 0})
    report = mod.run(_args(), lambda m: None)
    assert report["probe_ok"] is True
    assert report.get("backup_removed") is True
    assert not list(db.parent.glob("*.premaint-*"))


def test_dry_run_takes_no_backup_and_does_not_mutate(db, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "resolve_db", lambda p: db)
    monkeypatch.setattr(mod, "prune", lambda *a, **k: {"cron": 3})
    before = db.stat().st_size
    report = mod.run(_args(apply=False), lambda m: None)
    assert "backup" not in report
    assert not list(db.parent.glob("*.premaint-*"))
    assert db.stat().st_size == before


def test_insufficient_disk_aborts_before_mutating(db, monkeypatch):
    """Disk preflight must stop the run before anything destructive."""
    mod = _load()
    monkeypatch.setattr(mod, "resolve_db", lambda p: db)
    called = []
    monkeypatch.setattr(mod, "prune", lambda *a, **k: called.append(1) or {})
    monkeypatch.setattr(
        mod.shutil, "disk_usage",
        lambda p: type("D", (), {"free": 1, "total": 2, "used": 1})(),
    )
    with pytest.raises(RuntimeError, match="insufficient disk"):
        mod.run(_args(), lambda m: None)
    assert not called, "prune ran despite failed disk preflight"


# --- the lock-budget guard ------------------------------------------------

def test_predicted_lock_matches_measured_rate():
    """Sanity-check the model against the real benchmarks it came from."""
    mod = _load()
    # cora: 3505 MB measured at 49.3s
    assert 40 <= mod.predicted_lock_seconds(3505 * 1024 ** 2) <= 60
    # sterling: 1685 MB measured at 22s
    assert 17 <= mod.predicted_lock_seconds(1685 * 1024 ** 2) <= 30


def test_vacuum_refused_when_lock_would_exceed_budget(db, monkeypatch):
    """A predicted lock over budget must SKIP compaction, not take it.

    Past 60s Hermes fails the user's turn with a session-storage error
    (_TRANSCRIPT_WRITE_PATIENCE_S), so an unattended run must never get there.
    """
    mod = _load()
    monkeypatch.setattr(mod, "resolve_db", lambda p: db)
    monkeypatch.setattr(mod, "prune", lambda *a, **k: {"cron": 0})
    monkeypatch.setattr(mod, "predicted_lock_seconds", lambda b: 93.0)
    compacted = []
    monkeypatch.setattr(
        mod, "compact", lambda d, log: compacted.append(1) or {}
    )
    report = mod.run(_args(no_vacuum=False, vacuum_min_mb=0), lambda m: None)
    assert not compacted, "took a 93s lock on a live gateway"
    assert report["compaction"]["skipped"] == "predicted_lock_exceeds_budget"
    assert report["compaction"]["needs_supervised_window"] is True


def test_force_vacuum_overrides_the_budget(db, monkeypatch):
    """The operator escape hatch must actually work for supervised windows."""
    mod = _load()
    monkeypatch.setattr(mod, "resolve_db", lambda p: db)
    monkeypatch.setattr(mod, "prune", lambda *a, **k: {"cron": 0})
    monkeypatch.setattr(mod, "predicted_lock_seconds", lambda b: 93.0)
    compacted = []
    monkeypatch.setattr(
        mod, "compact",
        lambda d, log: (compacted.append(1), {"seconds": 93})[1],
    )
    monkeypatch.setattr(mod, "integrity", lambda d: "ok")
    mod.run(
        _args(no_vacuum=False, vacuum_min_mb=0, force_vacuum=True),
        lambda m: None,
    )
    assert compacted, "--force-vacuum did not override the budget"


def test_vacuum_proceeds_within_budget(db, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "resolve_db", lambda p: db)
    monkeypatch.setattr(mod, "prune", lambda *a, **k: {"cron": 0})
    monkeypatch.setattr(mod, "predicted_lock_seconds", lambda b: 22.0)
    compacted = []
    monkeypatch.setattr(
        mod, "compact",
        lambda d, log: (compacted.append(1), {"seconds": 22})[1],
    )
    monkeypatch.setattr(mod, "integrity", lambda d: "ok")
    mod.run(_args(no_vacuum=False, vacuum_min_mb=0), lambda m: None)
    assert compacted, "refused a safe 22s compaction"


# --- CLI contract ---------------------------------------------------------

def _cli(*args):
    return subprocess.run(
        [sys.executable, str(DBMAINT), *args],
        capture_output=True, text=True, timeout=120,
    )


def test_cli_rejects_human_source():
    r = _cli("--profile", "nope", "--sources", "telegram")
    assert r.returncode == EXIT_CONFIG
    assert "refusing" in (r.stdout + r.stderr).lower()


def test_cli_missing_profile_is_failure():
    r = _cli("--profile", "definitely-not-a-real-profile-xyz")
    assert r.returncode == EXIT_FAILURE


def test_cli_json_failure_has_probe_ok_false():
    """A crash and a silent success must not look the same."""
    import json

    r = _cli("--profile", "definitely-not-a-real-profile-xyz", "--json")
    assert r.returncode == EXIT_FAILURE
    payload = json.loads(r.stdout)
    assert payload["probe_ok"] is False
    assert payload["error"]


def test_default_is_dry_run():
    """--apply must be required to delete anything."""
    src = DBMAINT.read_text()
    assert '"--apply", action="store_true"' in src
    assert "Without this, dry-run only" in src


def test_resolve_db_root_vs_named(monkeypatch, tmp_path):
    mod = _load()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert mod.resolve_db("_root") == tmp_path / ".hermes" / "state.db"
    assert mod.resolve_db("bosun") == (
        tmp_path / ".hermes" / "profiles" / "bosun" / "state.db"
    )
