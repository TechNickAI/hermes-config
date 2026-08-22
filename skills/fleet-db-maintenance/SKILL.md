---
name: fleet-db-maintenance
description: >
  Keep Hermes session stores (`state.db`) from growing without bound by
  deleting aged machine-generated sessions and compacting the file. Use when a
  `state.db` is large or growing, when a gateway shows SQLite lock contention
  or slow session search, when setting up recurring session-store maintenance
  on one host or a whole fleet, or when someone asks to prune/vacuum/clean up
  cron or subagent session history. Covers the retention-vs-compaction split,
  the WAL VACUUM trap, the human-conversation safety invariant, and the
  per-agent weekly rollout.
version: 1.0.0
author: Bosun
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [hermes, sqlite, state-db, retention, vacuum, fleet, maintenance, cron]
    related_skills:
      - sqlite-live-maintenance
      - fleet-change-propagation
      - scheduled-job-runner
---

# Fleet session-store maintenance

## What this solves

A Hermes `state.db` accumulates every session, message, tool call, and FTS
index entry forever. On an agent that runs scheduled jobs, the store is
dominated by machine chatter: measured across a 14-profile fleet, `cron` +
`subagent` sessions were **50-95% of all messages** on every busy profile, and
about **8-10 GB of a 23 GB fleet total** was machine traffic older than 30
days.

Left alone this ends badly, and has: one fleet member's gateway was saturated
by an 11 GB `state.db` with a 4 GB WAL, logging 171 database-lock failures in a
day, peaking at 5.5 GB RAM, until Telegram stopped consuming updates.

## The two levers, and why they stay separate

| | what it does | cost | cadence |
|---|---|---|---|
| **Retention** | deletes aged machine sessions | cheap, online | every run |
| **Compaction** | `VACUUM` rewrites the whole file | expensive, exclusive lock | only when it pays |

Do not fuse these into one weekly "clean everything" script. Retention should
run often and cheaply; compaction should run only when the file is actually big
enough to justify a full rewrite. `dbmaint.py` gates compaction behind
`--vacuum-min-mb` (default 500) for exactly this reason.

## Why `sessions.auto_prune` is not the answer

The obvious move is the built-in config key. It does not work for this:

```python
maybe_auto_prune_and_vacuum(retention_days, min_interval_hours, vacuum,
                            sessions_dir, min_vacuum_interval_days)
```

**There is no source filter.** It calls `prune_sessions(older_than_days=...)`
and nothing else, so `auto_prune: true` deletes aged human conversations on the
same sweep as junk cron runs. The CLI `hermes sessions prune` *does* support
`--source`, which is why this skill shells out to it.

Second trap: any filter suppresses the implicit 90-day default, so
`prune --source cron` with no age flag matches **all** cron sessions ever.
Always pass `--older-than`.

## The safety invariant

`PRUNABLE_SOURCES = ("cron", "subagent")` is an allowlist, and the script
refuses anything else even when asked explicitly. `webhook` is deliberately
excluded: it can carry durable operational history, not just transient events.

The run additionally counts non-machine sessions before and after retention and
**aborts if that number moved at all**. A retention sweep that touches one
human conversation is a failed run, not a successful one.

## The WAL VACUUM trap

In WAL mode `VACUUM` does not write to the database file. It writes the entire
rebuilt database into the WAL, so without a trailing checkpoint the main file
does not shrink — you have moved the bloat, not removed it. The sequence must
be:

```sql
PRAGMA wal_checkpoint(TRUNCATE);   -- start clean
VACUUM;                            -- rebuild (goes to WAL!)
PRAGMA wal_checkpoint(TRUNCATE);   -- REQUIRED, or the file stays big
PRAGMA optimize;                   -- VACUUM is a schema change; restat
```

`tests/test_fleet_db_maintenance.py::test_vacuum_shrinks_file_after_delete` is
the regression guard: it inflates a database, deletes the rows, compacts, and
asserts the file actually got smaller and the WAL was truncated.

## Disk preflight

"Free space >= database size" is **not** sufficient. VACUUM needs the original,
a complete temporary rebuild, and WAL headroom. The script requires
`2.5x` the database size and aborts before mutating if the host is short.

## Usage

```bash
# Always look first. Dry-run is the default; --apply is required to delete.
python3 dbmaint.py --profile bosun --days 10

# Apply, with compaction if the file is >= 500 MB
python3 dbmaint.py --profile bosun --days 10 --apply

# Retention only, no rewrite
python3 dbmaint.py --profile kenbot --days 10 --apply --no-vacuum

# Machine-readable, for a scheduled job
python3 dbmaint.py --profile _root --days 10 --apply --json
```

`--profile _root` targets `~/.hermes/state.db`; a named profile targets
`~/.hermes/profiles/<name>/state.db`.

Exit codes: `0` ok, `2` config/usage error, `3` maintenance failure. The JSON
report always carries `probe_ok`, so a crash and a silent success never look
alike to the caller.

## Scheduling: per-agent, on its own host

Install a weekly job on **each** profile that maintains **its own** database.
Do not build a central job that SSHes around the fleet — a maintenance runner
that reaches across hosts fails as one unit and hides which member broke.

Stagger the runs so two profiles on a shared host never VACUUM at once; each
one takes an exclusive write lock, and overlapping them turns a short stall
into a long one.

Backups are removed after the integrity check passes by default. A weekly job
retaining a ~350 MB backup adds ~18 GB/year to the volume you were trying to
keep healthy. Use `--keep-backup` only for a one-off manual run.

## Pitfalls

- **Do not run this from the target gateway's own scheduler if it must stop the
  gateway.** Hermes' lifecycle guard blocks a gateway from restarting itself
  (anti-respawn-loop), and a script that dies between stop and start leaves the
  service down. This script deliberately never stops the gateway: `VACUUM`
  takes an exclusive lock and the gateway waits on it, which is a stall, not an
  outage.
- **Never prune `role='tool'` rows.** They pair with assistant tool calls;
  orphaning them returns HTTP 400 and makes those sessions permanently
  un-resumable. This script deletes whole sessions via the supported CLI, which
  sidesteps it.
- **`last_status: ok` from a scheduler means dispatched, not succeeded.** Read
  the report, and reconcile the claimed counts against before/after database
  counts.
- **Do not anchor an output parser to one CLI phrasing.** A parser expecting
  `^[0-9]+ session` returns zero against `Pruned 46 session(s).`, which reads
  exactly like "nothing to do" — a silent retention failure.
- **Check where the bytes actually are before planning row deletion.** On large
  Hermes databases the FTS trigram index frequently rivals or exceeds the
  message text it indexes. Run
  `select name, sum(pgsize) from dbstat group by name order by 2 desc;` first.
  If the index dominates, the lever is an FTS rebuild, not pruning.
- **Archiving reclaims zero bytes.** `sessions.auto_archive` flips one bit and
  hides sessions from `/resume`; it is a listing-layer nicety, not a disk fix.
  It does not affect `session_search` recall either way.

## Verification checklist

- [ ] Dry-run reviewed before the first `--apply` on any profile.
- [ ] Human session count identical before and after.
- [ ] `PRAGMA quick_check` returns `ok` after compaction.
- [ ] Before/after sizes recorded; reclaimed bytes match expectation.
- [ ] Gateway still serving after the run (process present, platform ready).
- [ ] `session_search` still returns hits for a known human conversation.
