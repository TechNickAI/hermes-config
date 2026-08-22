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
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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

# Measured cost of the full compaction sequence on real fleet databases:
# 1.7 GB -> 22s, 3.5 GB -> 49.3s. Used to predict the exclusive-lock window
# before taking it.
VACUUM_SECONDS_PER_GB = 14.4

# Hermes gives up on a routine session write after _WRITE_PATIENCE_S = 20s and
# on a transcript-critical write after _TRANSCRIPT_WRITE_PATIENCE_S = 60s
# (hermes_state.py:2719-2720). Past that the user's turn fails with a
# session-storage error and must be resent. So an unattended VACUUM must keep
# its lock comfortably under 60s; 45s leaves margin for a loaded host.
MAX_UNATTENDED_LOCK_SECONDS = 45.0

# After VACUUM, a live reader holding a WAL snapshot can make the trailing
# checkpoint a partial no-op, leaving the rebuilt db in the WAL and the main
# file reading at its OLD size. Retry briefly so reported sizes are truthful.
WAL_SETTLE_ATTEMPTS = 6
WAL_SETTLE_PAUSE = 2.0
WAL_SETTLE_BYTES = 64 * 1024 * 1024


def predicted_lock_seconds(db_bytes: int) -> float:
    """Estimated exclusive-lock duration for the compaction sequence."""
    return (db_bytes / (1024 ** 3)) * VACUUM_SECONDS_PER_GB


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


def _human_session_ids(db: Path) -> set:
    """The IDENTITIES of sessions that must survive maintenance.

    Identities, not a count. A count is fooled two ways on a live gateway:
    it deletes one human session while the gateway creates another (equal
    counts, real data loss), and it cannot tell you WHICH row vanished. The
    invariant we actually want is that the before-set remains a SUBSET of the
    after-set -- new sessions arriving mid-run are fine, missing ones are not.
    """
    conn = _connect(db, timeout=30)
    try:
        placeholders = ",".join("?" for _ in PRUNABLE_SOURCES)
        rows = conn.execute(
            f"SELECT id FROM sessions "
            f"WHERE COALESCE(source,'') NOT IN ({placeholders})",
            PRUNABLE_SOURCES,
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _human_session_count(db: Path) -> int:
    """Kept for reporting; the load-bearing check is _human_session_ids."""
    return len(_human_session_ids(db))


class MaintenanceLock:
    """Cross-process exclusive lock for one database.

    Without this, a duplicate scheduler dispatch or a manual run overlapping
    the weekly job can interleave two multi-step protocols on the same file:
    racing each other's before/after snapshots, contending during VACUUM, and
    deleting a backup the other run still needs. SQLite serializes individual
    transactions, not this protocol.
    """

    def __init__(self, db: Path):
        self.path = Path(str(db) + ".maint.lock")
        self._fh = None

    def __enter__(self):
        import fcntl

        self._fh = open(self.path, "w")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._fh.close()
            self._fh = None
            raise RuntimeError(
                f"another maintenance run holds {self.path}; refusing to "
                f"run two protocols against the same database"
            )
        self._fh.write(f"{os.getpid()} {_utc()}\n")
        self._fh.flush()
        return self

    def __exit__(self, *exc):
        import fcntl

        if self._fh:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            self._fh.close()
        self.path.unlink(missing_ok=True)
        return False


def _hermes_bin() -> str | None:
    for candidate in (
        Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes",
        Path.home() / ".local" / "bin" / "hermes",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("hermes")


def _source_count(db: Path, src: str) -> int:
    conn = _connect(db, timeout=30)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE source = ?", (src,)
        ).fetchone()[0]
    finally:
        conn.close()


def prune(
    profile: str, days: int, sources, apply: bool, log,
    db: Optional[Path] = None,
    chunk_days: int = 0, pause: float = 0.0, max_seconds: float = 0.0,
) -> dict:
    """Source-scoped retention via the supported CLI.

    We shell out to ``hermes sessions prune`` rather than issuing DELETEs
    ourselves so that lineage handling and FTS index maintenance stay the
    responsibility of the code that owns the schema.

    With ``chunk_days`` the work is split into age slices walked oldest-first,
    each its own transaction with a ``pause`` between them. This is what makes
    a large catch-up gentle: one 8,000-session delete is a long write lock and
    a huge WAL burst, while forty 200-session deletes let the gateway
    interleave its own writes and let SQLite checkpoint as it goes.

    ``max_seconds`` stops cleanly at a deadline, leaving the rest for the next
    run. A catch-up that has to be interrupted should still bank its progress.

    Counts are RECONCILED against the database rather than trusted from
    stdout: on an ``--yes`` run the CLI prints only ``Pruned N session(s).``
    and lists nothing, so any listing-based count reports 0 for a successful
    deletion.
    """
    binary = _hermes_bin()
    if not binary:
        raise RuntimeError("hermes binary not found")

    started = time.time()
    result = {}

    for src in sources:
        # Plan the windows. Without chunking it is one open-ended slice.
        if chunk_days and db and apply:
            oldest = _oldest_age_days(db, [src])
            windows = _age_slices(oldest, days, chunk_days) or [(days, None)]
        else:
            windows = [(days, None)]

        total = 0
        stopped_early = False

        for lo, hi in windows:
            if max_seconds and (time.time() - started) > max_seconds:
                log(f"  {src}: deadline reached, {total} deleted so far "
                    f"(remaining backlog left for the next run)")
                stopped_early = True
                break

            cmd, env = _prune_command(binary, profile, src, lo, apply, newer_than=hi)
            before = _source_count(db, src) if (db and apply) else None

            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=1800, env=env
            )
            out = (proc.stdout or "") + (proc.stderr or "")

            # argparse rejects an unknown -p and exits 0 after printing usage,
            # so a usage banner is a silent no-op, not a success.
            if out.lstrip().startswith("usage:"):
                raise RuntimeError(
                    f"prune {src} rejected by the CLI (usage banner). "
                    f"Command: {' '.join(cmd)}"
                )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"prune {src} exited {proc.returncode}: {out[-400:]}"
                )

            if before is not None:
                deleted = before - _source_count(db, src)
                total += deleted
                if deleted and hi is not None:
                    log(f"    {src} {hi}-{lo}d: {deleted} deleted")
                if pause and deleted:
                    # Yield the write lock so the live gateway can drain its
                    # own queued writes before the next slice.
                    time.sleep(pause)
                continue

            # Dry-run: parse the header count. Never infer from listed row
            # prefixes -- cron ids look like cron_<hash>_<stamp> but subagent
            # ids look like 20260701_103305_1d2614, so prefix matching
            # reports 0 while 124 sessions are really eligible.
            matched = None
            for line in out.splitlines():
                m = re.search(r"(\d+)\s+session\(s\)", line)
                if m:
                    matched = int(m.group(1))
                    break
            if matched is None:
                if re.search(r"No sessions match", out, re.I):
                    matched = 0
                else:
                    raise RuntimeError(
                        f"could not parse prune output for {src}; refusing to "
                        f"report an unverified count. Tail: {out[-300:]}"
                    )
            total += matched

        result[src] = total
        if apply and db:
            log(f"  {src}: {total} session(s) deleted (verified in db)"
                + (" [partial]" if stopped_early else ""))
        else:
            log(f"  {src}: {total} session(s) matched (dry-run)")

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
    main file never shrinks (verified: 19 MB vs 0 MB with a concurrent reader
    open, which is every real run).

    THIS TAKES AN EXCLUSIVE WRITE LOCK for the whole rebuild. Measured on real
    fleet databases: ~14.4 seconds per GB (1.7 GB -> 22s, 3.5 GB -> 49s). A
    live gateway that tries to write during that window waits, and Hermes
    gives up after _WRITE_PATIENCE_S = 20s for routine writes and
    _TRANSCRIPT_WRITE_PATIENCE_S = 60s for transcript-critical ones
    (hermes_state.py:2719-2720) -- past that the user's turn fails with a
    session-storage error and has to be resent. Callers must gate this on a
    size that keeps the lock inside those budgets.
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


def profile_home(profile: str) -> Path:
    """The HERMES_HOME that owns this profile's state.db."""
    home = Path.home() / ".hermes"
    if profile in ("_root", "root", ""):
        return home
    return home / "profiles" / profile


def resolve_db(profile: str) -> Path:
    """Named profiles live under profiles/<name>/; the root profile does not."""
    return profile_home(profile) / "state.db"


def _prune_command(
    binary: str, profile: str, src: str, days: int, apply: bool,
    newer_than: Optional[int] = None,
):
    """Build the CLI invocation and the environment that pins its target.

    ``newer_than`` bounds the window from the old side, so a chunked catch-up
    can walk the backlog in age slices: ``--older-than 120 --newer-than 150``
    deletes only sessions aged 120-150 days. Each slice is its own
    transaction, so no single delete holds the write lock for long.

    Two traps, both verified against the live CLI:

    * ``hermes -p _root`` is REJECTED -- argparse prints usage and the prune
      never runs, while a naive wrapper reports success. The root profile is
      selected by pointing HERMES_HOME at ~/.hermes with NO -p flag.
    * ``HERMES_PROFILE`` is silently ignored, so it cannot be used to steer
      the subprocess. ``HERMES_HOME`` is what actually decides which database
      is opened, so we set it explicitly rather than inheriting whatever the
      scheduler happened to export. Without this the script can COUNT one
      database while the subprocess DELETES from another.
    """
    env = dict(os.environ)
    env["HERMES_HOME"] = str(profile_home(profile))
    cmd = [binary]
    if profile not in ("_root", "root", ""):
        cmd += ["-p", profile]
    cmd += ["sessions", "prune", "--source", src, "--older-than", str(days)]
    if newer_than is not None:
        cmd += ["--newer-than", str(newer_than)]
    cmd.append("--yes" if apply else "--dry-run")
    return cmd, env


def _age_slices(oldest_days: int, floor_days: int, step_days: int):
    """Age windows from oldest to newest, e.g. (150,120), (120,90), ...

    Walking oldest-first means the biggest, least-valuable backlog goes first
    and an interrupted catch-up still made real progress.
    """
    slices = []
    hi = oldest_days
    while hi > floor_days:
        lo = max(hi - step_days, floor_days)
        slices.append((lo, hi))
        hi = lo
    return slices


def _oldest_age_days(db: Path, sources) -> int:
    """Age in days of the oldest machine session, for slice planning."""
    conn = _connect(db, timeout=30)
    try:
        placeholders = ",".join("?" for _ in sources)
        row = conn.execute(
            f"SELECT MIN(COALESCE(last_activity_at, started_at)) "
            f"FROM sessions WHERE source IN ({placeholders})",
            tuple(sources),
        ).fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        return 0
    return int((time.time() - float(row[0])) / 86400) + 1


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
    humans_before = _human_session_ids(db)
    report["human_sessions_before"] = len(humans_before)
    log(f"{args.profile}: {report['before']['db_mb']} MB, "
        f"{report['counts_before'].get('sessions_total', 0)} sessions "
        f"({len(humans_before)} human)")

    # Disk preflight and backup happen BEFORE the first destructive operation.
    #
    # Retention is destructive on its own -- it deletes rows via the CLI. If
    # that subprocess deleted human sessions and then exited nonzero, timed
    # out, or was killed mid-flight, a backup taken later (or only in the
    # compaction branch) would be worthless: there would be nothing to restore
    # from. So on any --apply run we take the verified backup first, whether
    # or not we intend to compact afterwards.
    bkp = None
    if args.apply:
        free = shutil.disk_usage(db.parent).free
        needed = int(_size(db) * DISK_SAFETY_MULTIPLE)
        if free < needed:
            raise RuntimeError(
                f"insufficient disk: {_mb(free)} MB free, "
                f"need ~{_mb(needed)} MB for backup + rebuild"
            )
        # Decide the compaction gate BEFORE backing up. A run that is going to
        # skip compaction does not need a full-size copy of the database --
        # taking one anyway wrote a needless 3.5 GB file on cora and left it
        # behind, which is the opposite of what a disk-hygiene tool should do.
        _pred = predicted_lock_seconds(_size(db))
        _will_compact = (
            not args.no_vacuum
            and _mb(_size(db)) >= args.vacuum_min_mb
            and (_pred <= args.max_lock_seconds or args.force_vacuum)
        )
        log("backup:")
        bkp = db.with_suffix(f".db.premaint-{int(time.time())}")
        backup(db, bkp, log)
        report["backup"] = str(bkp)
        report["backup_for_compaction"] = _will_compact

    log("retention:")
    prune_error = None
    try:
        report["pruned"] = prune(
            args.profile, args.days, args.sources, args.apply, log, db=db,
            chunk_days=args.chunk_days, pause=args.pause,
            max_seconds=args.max_seconds,
        )
    except Exception as exc:
        # Do NOT return here. A failed prune may still have deleted rows, so
        # the human-session invariant below must be evaluated either way --
        # that check is the whole safety story and skipping it on the error
        # path is exactly when it matters most.
        prune_error = exc
        report["pruned"] = {"error": str(exc)}
        log(f"  prune FAILED: {exc}")

    # Safety invariant: every human session present before must still be
    # present after. Subset, not count equality -- the gateway may legitimately
    # create new sessions mid-run, but none may disappear.
    # Evaluated on both the success and failure paths.
    humans_after = _human_session_ids(db)
    report["human_sessions_after"] = len(humans_after)
    lost = humans_before - humans_after
    if lost:
        detail = f" Backup preserved at {bkp}." if bkp else ""
        sample = ", ".join(sorted(lost)[:5])
        raise RuntimeError(
            f"ABORT: {len(lost)} human session(s) disappeared during "
            f"retention (e.g. {sample}).{detail}"
        )
    if prune_error is not None:
        raise RuntimeError(f"retention failed: {prune_error}")

    after_prune = _db_sizes(db)
    should_vacuum = (
        args.apply
        and not args.no_vacuum
        and after_prune["db_mb"] >= args.vacuum_min_mb
    )

    # Predicted lock window. An unattended run REFUSES to take a lock long
    # enough to fail a live user's turn; --force-vacuum is the operator's
    # explicit override for a supervised maintenance window.
    predicted = predicted_lock_seconds(_size(db))
    report["predicted_lock_seconds"] = round(predicted, 1)
    lock_budget_exceeded = (
        should_vacuum
        and predicted > args.max_lock_seconds
        and not args.force_vacuum
    )
    if lock_budget_exceeded:
        log(
            f"compaction: SKIPPED -- predicted {predicted:.0f}s exclusive lock "
            f"exceeds the {args.max_lock_seconds:.0f}s budget. A live gateway "
            f"drops writes past 60s. The prediction is a conservative "
            f"worst case; measure the real cost on a COPY first "
            f"(cp state.db /tmp/x.db && sqlite3 /tmp/x.db VACUUM), then "
            f"re-run with --max-lock-seconds <measured+margin>, or "
            f"--force-vacuum during a supervised window."
        )
        report["compaction"] = {
            "skipped": "predicted_lock_exceeds_budget",
            "predicted_lock_seconds": round(predicted, 1),
            "needs_supervised_window": True,
        }
        should_vacuum = False

    if should_vacuum:
        log(f"compaction: (predicted ~{predicted:.0f}s exclusive lock)")
        try:
            report["compaction"] = compact(db, log)
            report["integrity"] = integrity(db)
            if report["integrity"] != "ok":
                raise RuntimeError(f"post-vacuum integrity: {report['integrity']}")
        except Exception:
            # The database may now be damaged. Keep the backup no matter what
            # --keep-backup says: deleting the only good copy on the failure
            # path is the worst outcome this script could produce.
            report["backup_preserved_on_failure"] = str(bkp)
            log(f"  FAILED -- backup preserved at {bkp}")
            raise

        # A live reader (the gateway holding a WAL read snapshot) can make the
        # trailing checkpoint a partial no-op, leaving the rebuilt database in
        # the WAL. The main file then still reads at its OLD size and the run
        # looks like it reclaimed nothing. Measured on cora: db 3510 MB with a
        # 2264 MB WAL immediately after VACUUM, settling to 2251 MB once the
        # reader released. Retry the checkpoint briefly before reporting sizes.
        for attempt in range(WAL_SETTLE_ATTEMPTS):
            wal = _size(Path(str(db) + "-wal"))
            if wal < WAL_SETTLE_BYTES:
                break
            time.sleep(WAL_SETTLE_PAUSE)
            conn = _connect(db, timeout=60)
            try:
                busy, _pages, _ckpt = conn.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()
            finally:
                conn.close()
            if not busy:
                break
        else:
            report["wal_not_settled_mb"] = _mb(_size(Path(str(db) + "-wal")))
            log(f"  note: WAL still {report['wal_not_settled_mb']} MB -- a live "
                f"reader is holding a snapshot; it will settle on release")
    elif not lock_budget_exceeded:
        reason = (
            "dry-run" if not args.apply
            else "disabled" if args.no_vacuum
            else f"below {args.vacuum_min_mb} MB threshold"
        )
        log(f"compaction: skipped ({reason})")
        report["compaction"] = {"skipped": reason}

    # Success path only. The backup exists to cover the destructive work; once
    # every check has passed it is dead weight, and a weekly job that retains
    # one adds ~18 GB/yr to the volume we are trying to keep healthy. On ANY
    # failure above we raise before reaching this and the backup survives.
    if bkp is not None and not args.keep_backup and bkp.exists():
        bkp.unlink()
        report["backup_removed"] = True

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
    p.add_argument("--max-lock-seconds", type=float,
                   default=MAX_UNATTENDED_LOCK_SECONDS,
                   help=(
                       "refuse an unattended VACUUM whose predicted exclusive "
                       f"lock exceeds this (default {MAX_UNATTENDED_LOCK_SECONDS:.0f}s; "
                       "Hermes drops transcript writes past 60s)"
                   ))
    p.add_argument("--force-vacuum", action="store_true",
                   help="override --max-lock-seconds. Supervised windows only.")
    p.add_argument("--chunk-days", type=int, default=30,
                   help=(
                       "delete in age slices of this many days, oldest first, "
                       "each its own transaction (default 30; 0 = one big "
                       "delete). Keeps the write lock short on a live gateway."
                   ))
    p.add_argument("--pause", type=float, default=2.0,
                   help="seconds to yield between slices (default 2.0)")
    p.add_argument("--max-seconds", type=float, default=0.0,
                   help=(
                       "stop cleanly after this long and leave the rest for "
                       "the next run (default 0 = no deadline)"
                   ))
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
        from pathlib import Path as _P
        _db = resolve_db(args.profile)
        if _db.is_file():
            with MaintenanceLock(_db):
                report = run(args, log)
        else:
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
