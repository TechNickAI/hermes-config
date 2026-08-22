#!/usr/bin/env python3
"""Fleet session-store maintenance for Hermes ``state.db``.

Two phases, deliberately separated (see the "Separate retention from
compaction" rule in the sqlite-live-maintenance skill):

  RETENTION   source-scoped deletion of machine-generated sessions via the
              supported ``hermes sessions prune`` CLI. Runs online, cheap,
              every time.

  COMPACTION  ``wal_checkpoint(TRUNCATE)`` -> ``VACUUM`` -> checkpoint again
              -> ``PRAGMA optimize``. Expensive, rewrites the whole file, and
              only runs when it is actually worth it (``--vacuum-min-mb``).

Design constraints this encodes, each learned the hard way:

* **Never prune human conversation.** ``PRUNABLE_SOURCES`` is an allowlist of
  machine-generated sources. Anything else is refused loudly, even if asked
  for explicitly. Deleting a Telegram thread is not recoverable.
* **The VACUUM sequence needs the trailing checkpoint.** In WAL mode VACUUM
  writes the rebuilt database into the WAL, so without a final
  ``wal_checkpoint(TRUNCATE)`` the file does not shrink -- you have only moved
  the bloat. (photostructure/test-sqlite-vacuum-wal measured this.)
* **Back up before mutating, verify the backup, and abort if it is bad.** The
  backup is taken with SQLite's online backup API while the service still
  serves traffic, then independently reopened and integrity-checked.
* **Disk preflight is not "free >= db size".** VACUUM needs room for the
  original, a full temporary rebuild, and the WAL. We require a real multiple.
* **Report machine-readable state even on failure.** A silent success and a
  crash must not look the same to the caller.

Exit codes: 0 ok/nothing-to-do, 2 config/usage error, 3 maintenance failure.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_FAILURE = 3

# Machine-generated sources only. Human conversation (telegram, slack, cli,
# discord, imessage, webhook...) is never eligible -- webhook is deliberately
# excluded because it can carry durable operational history, not just
# transient event invocations.
PRUNABLE_SOURCES = ("cron", "subagent")

# VACUUM needs the original file, a full temporary rebuild, and WAL headroom.
DISK_SAFETY_MULTIPLE = 2.5


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mb(n: int) -> int:
    return int(n) // (1024 * 1024)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _db_sizes(db: Path) -> dict:
    return {
        "db_mb": _mb(_size(db)),
        "wal_mb": _mb(_size(Path(str(db) + "-wal"))),
        "shm_mb": _mb(_size(Path(str(db) + "-shm"))),
    }


def _connect(db: Path, *, timeout: float = 60.0) -> sqlite3.Connection:
    """Open a normal connection.

    Deliberately NOT a ``file:...?mode=ro`` URI: on a live WAL database that
    form fails *lazily* (connect succeeds, the first query raises), which
    defeats any try/except fallback and fabricates corruption reports.
    """
    conn = sqlite3.connect(str(db), timeout=timeout, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    return conn


def _counts(db: Path) -> dict:
    """Session/message counts, retried once -- a torn read on a live,
    continuously-written database is a transient race, not corruption."""
    for attempt in (1, 2):
        try:
            conn = _connect(db, timeout=30)
            try:
                out = {}
                for src in PRUNABLE_SOURCES:
                    out[src] = conn.execute(
                        "SELECT COUNT(*) FROM sessions WHERE source = ?", (src,)
                    ).fetchone()[0]
                out["sessions_total"] = conn.execute(
                    "SELECT COUNT(*) FROM sessions"
                ).fetchone()[0]
                out["messages_total"] = conn.execute(
                    "SELECT COUNT(*) FROM messages"
                ).fetchone()[0]
                return out
            finally:
                conn.close()
        except sqlite3.DatabaseError:
            if attempt == 2:
                raise
            time.sleep(1.0)
    return {}


def _human_session_count(db: Path) -> int:
    """Sessions that must survive maintenance. The safety invariant."""
    conn = _connect(db, timeout=30)
    try:
        placeholders = ",".join("?" for _ in PRUNABLE_SOURCES)
        return conn.execute(
            f"SELECT COUNT(*) FROM sessions "
            f"WHERE COALESCE(source,'') NOT IN ({placeholders})",
            PRUNABLE_SOURCES,
        ).fetchone()[0]
    finally:
        conn.close()


def _hermes_bin() -> str | None:
    for candidate in (
        Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes",
        Path.home() / ".local" / "bin" / "hermes",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("hermes")


def prune(profile: str, days: int, sources, apply: bool, log) -> dict:
    """Source-scoped retention via the supported CLI.

    We shell out to ``hermes sessions prune`` rather than issuing DELETEs
    ourselves so that lineage handling and FTS index maintenance stay the
    responsibility of the code that owns the schema.
    """
    binary = _hermes_bin()
    if not binary:
        raise RuntimeError("hermes binary not found")

    result = {}
    for src in sources:
        cmd = [
            binary, "-p", profile, "sessions", "prune",
            "--source", src, "--older-than", str(days),
        ]
        cmd.append("--yes" if apply else "--dry-run")

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        out = (proc.stdout or "") + (proc.stderr or "")

        if proc.returncode != 0:
            raise RuntimeError(f"prune {src} exited {proc.returncode}: {out[-400:]}")

        # Count candidate lines rather than trusting a summary string: a
        # parser anchored to one phrasing silently reports zero when the CLI
        # wording changes, which reads exactly like "retention is working".
        matched = sum(
            1 for line in out.splitlines() if line.strip().startswith(f"{src}_")
        )
        result[src] = matched
        log(f"  {src}: {matched} session(s) {'deleted' if apply else 'matched (dry-run)'}")

    return result


def backup(db: Path, dest: Path, log) -> Path:
    """Online backup + independent verification. Raises if unusable."""
    if dest.exists():
        dest.unlink()

    src = _connect(db, timeout=120)
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()

    if not dest.is_file() or _size(dest) == 0:
        raise RuntimeError("backup missing or empty")

    check = sqlite3.connect(str(dest))
    try:
        status = check.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        check.close()
    if status != "ok":
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"backup failed integrity check: {status}")

    log(f"  backup verified: {dest.name} ({_mb(_size(dest))} MB)")
    return dest


def compact(db: Path, log) -> dict:
    """checkpoint -> VACUUM -> checkpoint -> optimize.

    The trailing checkpoint is not optional. VACUUM in WAL mode writes the
    entire rebuilt database through the WAL; skip the final truncate and the
    main file never shrinks.
    """
    before = _db_sizes(db)
    started = time.time()

    conn = _connect(db, timeout=300)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA optimize")
    finally:
        conn.close()

    elapsed = round(time.time() - started, 1)
    after = _db_sizes(db)
    log(
        f"  vacuum: {before['db_mb']} MB -> {after['db_mb']} MB "
        f"(reclaimed {before['db_mb'] - after['db_mb']} MB) in {elapsed}s"
    )
    return {"before": before, "after": after, "seconds": elapsed}


def integrity(db: Path) -> str:
    conn = _connect(db, timeout=120)
    try:
        return conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()


def resolve_db(profile: str) -> Path:
    """Named profiles live under profiles/<name>/; the root profile does not."""
    home = Path.home() / ".hermes"
    if profile in ("_root", "root", ""):
        return home / "state.db"
    return home / "profiles" / profile / "state.db"


def run(args, log) -> dict:
    # Validate the source allowlist FIRST, before touching the filesystem.
    # If this ran after the db-exists check, asking to prune "telegram" on a
    # missing profile would surface as a generic failure and hide the fact
    # that the request itself was unsafe.
    bad = [s for s in args.sources if s not in PRUNABLE_SOURCES]
    if bad:
        raise ValueError(
            f"refusing to prune non-machine source(s): {','.join(bad)}. "
            f"Allowed: {','.join(PRUNABLE_SOURCES)}"
        )

    db = resolve_db(args.profile)
    if not db.is_file():
        raise RuntimeError(f"state.db not found for profile {args.profile}: {db}")

    report = {
        "profile": args.profile,
        "host": os.uname().nodename,
        "started_at": _utc(),
        "db_path": str(db),
        "apply": bool(args.apply),
        "retention_days": args.days,
        "probe_ok": False,
    }

    report["before"] = _db_sizes(db)
    report["counts_before"] = _counts(db)
    humans_before = _human_session_count(db)
    report["human_sessions_before"] = humans_before
    log(f"{args.profile}: {report['before']['db_mb']} MB, "
        f"{report['counts_before'].get('sessions_total', 0)} sessions "
        f"({humans_before} human)")

    log("retention:")
    report["pruned"] = prune(args.profile, args.days, args.sources, args.apply, log)

    # Safety invariant: retention must never reduce the human-session count.
    humans_after = _human_session_count(db)
    report["human_sessions_after"] = humans_after
    if humans_after != humans_before:
        raise RuntimeError(
            f"ABORT: human session count changed {humans_before} -> {humans_after}"
        )

    after_prune = _db_sizes(db)
    should_vacuum = (
        args.apply
        and not args.no_vacuum
        and after_prune["db_mb"] >= args.vacuum_min_mb
    )

    if should_vacuum:
        log("compaction:")
        free = shutil.disk_usage(db.parent).free
        needed = int(_size(db) * DISK_SAFETY_MULTIPLE)
        if free < needed:
            raise RuntimeError(
                f"insufficient disk: {_mb(free)} MB free, "
                f"need ~{_mb(needed)} MB for backup + rebuild"
            )

        bkp = db.with_suffix(f".db.premaint-{int(time.time())}")
        try:
            backup(db, bkp, log)
            report["backup"] = str(bkp)
            report["compaction"] = compact(db, log)
            report["integrity"] = integrity(db)
            if report["integrity"] != "ok":
                raise RuntimeError(f"post-vacuum integrity: {report['integrity']}")
        finally:
            # The backup exists to cover the rewrite. Once integrity is proven
            # it is dead weight -- a weekly job that keeps them adds ~18 GB/yr
            # to the volume we are trying to keep healthy.
            if not args.keep_backup and bkp.exists():
                bkp.unlink()
                report["backup_removed"] = True
    else:
        reason = (
            "dry-run" if not args.apply
            else "disabled" if args.no_vacuum
            else f"below {args.vacuum_min_mb} MB threshold"
        )
        log(f"compaction: skipped ({reason})")
        report["compaction"] = {"skipped": reason}

    report["after"] = _db_sizes(db)
    report["counts_after"] = _counts(db)
    report["reclaimed_mb"] = report["before"]["db_mb"] - report["after"]["db_mb"]
    report["finished_at"] = _utc()
    report["probe_ok"] = True
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Hermes session-store maintenance")
    p.add_argument("--profile", required=True, help="profile name, or _root")
    p.add_argument("--days", type=int, default=10,
                   help="delete machine sessions idle longer than this (default 10)")
    p.add_argument("--sources", default=",".join(PRUNABLE_SOURCES),
                   help=f"comma-separated; allowed: {','.join(PRUNABLE_SOURCES)}")
    p.add_argument("--apply", action="store_true",
                   help="actually delete. Without this, dry-run only.")
    p.add_argument("--no-vacuum", action="store_true", help="retention only")
    p.add_argument("--vacuum-min-mb", type=int, default=500,
                   help="skip VACUUM below this size (default 500)")
    p.add_argument("--keep-backup", action="store_true",
                   help="retain the pre-maintenance backup after success")
    p.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = p.parse_args(argv)

    args.sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    if not args.sources:
        print("error: no sources given", file=sys.stderr)
        return EXIT_CONFIG

    lines: list[str] = []

    def log(msg: str) -> None:
        lines.append(msg)
        if not args.json:
            print(msg, flush=True)

    try:
        report = run(args, log)
    except (ValueError, argparse.ArgumentError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception as exc:
        failure = {
            "profile": args.profile, "probe_ok": False,
            "error": str(exc), "finished_at": _utc(),
        }
        if args.json:
            print(json.dumps(failure, indent=2))
        else:
            print(f"FAILED: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"done: reclaimed {report['reclaimed_mb']} MB")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
