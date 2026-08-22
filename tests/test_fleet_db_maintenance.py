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
