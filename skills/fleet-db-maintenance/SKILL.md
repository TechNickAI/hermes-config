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

The run snapshots the **identity set** of every non-machine session before
retention and requires it to remain a **subset** afterwards. Identities, not a
count: the gateway is live, so it can create a session while a buggy prune
deletes one, leaving the count identical and the loss invisible. New sessions
arriving mid-run are fine; a missing one aborts the run.

`COALESCE(source,'')` matters here — a NULL source would otherwise fall
through `NOT IN (...)` under SQL's NULL semantics and be treated as prunable.

## Target pinning (two silent no-ops)

Both verified against the live CLI:

* **`hermes -p _root` is REJECTED.** argparse prints a usage banner and exits
  **0**, so a wrapper that only checks the return code reports success while
  deleting nothing. The root profile is selected by pointing `HERMES_HOME` at
  `~/.hermes` with **no** `-p` flag. The script treats a usage banner as a
  failure.
* **`HERMES_PROFILE` is silently ignored** by the CLI. `HERMES_HOME` is what
  actually decides which database is opened, so the script sets it explicitly
  rather than inheriting whatever the scheduler exported. Without this it can
  COUNT one database while the subprocess DELETES from another.

## Counts are reconciled, not parsed

On an `--apply` run the CLI prints only `Pruned N session(s).` and lists
nothing, so any listing-based parser reports 0 for a successful deletion. The
script diffs the real per-source row count in the database and reports that.

For dry runs it parses the header count and **refuses to report an unverified
number** — returning 0 for output it did not understand is exactly what makes
a broken pruner look healthy for months.

## Concurrency

Each run takes an exclusive `flock` on `<db>.maint.lock` for the whole
protocol. A duplicate scheduler dispatch or a manual run overlapping the weekly
job would otherwise interleave two multi-step protocols on one file — racing
each other's snapshots, contending during VACUUM, and deleting a backup the
other run still needs. SQLite serializes transactions, not this protocol.

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

## The lock budget (the constraint that shapes everything)

VACUUM takes an **exclusive write lock** for the entire rebuild. Measured on
real fleet databases: **~14.4 seconds per GB** (1.7 GB -> 22s, 3.5 GB -> 49.3s).

That matters because Hermes does not wait forever. From
`hermes_state.py:2719-2720`:

```python
_WRITE_PATIENCE_S = 20.0             # routine session writes
_TRANSCRIPT_WRITE_PATIENCE_S = 60.0  # transcript-critical writes
```

Past those budgets a live user's turn **fails with a session-storage error and
must be resent**. So "VACUUM is just a stall, not an outage" is wrong above
about 60 seconds of lock. Projected at the measured rate:

| profile | size | predicted lock | unattended? |
|---|---|---|---|
| julianna (Ace) | 6.7 GB | ~93s | **refused** |
| cora | 3.5 GB | ~49s | borderline |
| kenbot | 3.0 GB | ~42s | ok, but trading |
| bosun | 2.2 GB | ~32s | ok |
| sterling | 1.7 GB | ~23s | ok |

`dbmaint.py` predicts the lock before taking it and **refuses** to compact when
it exceeds `--max-lock-seconds` (default 45s, leaving margin under the 60s
cliff). The run still prunes; it just reports
`needs_supervised_window: true` instead of freezing a live agent. Use
`--force-vacuum` only during a supervised window.

Retention alone is safe at any size — deletes are short transactions, not a
whole-file rewrite.

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

## Gentle by default: chunked retention

A single `prune --older-than 10` on a backlogged profile is one enormous
DELETE: a long write lock, a huge WAL burst, and an FTS index churning through
thousands of rows at once. `--chunk-days` splits it into age slices walked
**oldest-first**, each its own transaction, with `--pause` between them so the
live gateway can drain its own queued writes.

```bash
# Catch-up on a backlogged profile: 30-day slices, 2s apart
dbmaint.py --profile kenbot --days 10 --apply --no-vacuum \
           --chunk-days 30 --pause 2

# The weekly steady state: smaller slices, longer pauses, hard deadline
dbmaint.py --profile kenbot --days 10 --apply --no-vacuum \
           --chunk-days 7 --pause 5 --max-seconds 900
```

`--max-seconds` stops cleanly at a deadline and leaves the rest for next week.
An interrupted catch-up should still bank its progress rather than roll back.
Oldest-first ordering means the least valuable data goes first, so a partial
run is still a useful run.

## The weekly job says nothing when healthy

`weekly_db_maintenance.py` is wired as a `no_agent` cron script: stdout is
delivered verbatim, and **empty stdout is silent**. A healthy run prints
nothing at all. A weekly "pruned 240 sessions, all good" needs no decision and
only trains the owner to ignore the channel.

It speaks for exactly three things:

| condition | why it is actionable |
|---|---|
| human sessions dropped | data loss; stop and investigate |
| run failed / unreadable report | retention is not happening |
| db still above ~3 GB after retention | needs a supervised VACUUM window |

Retention only — it never VACUUMs unattended.

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

## Measured canary result

First real run, on Bosun's own 2.25 GB database (2026-08-22):

```
before:  2252 MB, 12,076 sessions (2,057 human)
pruned:  8,323 cron + 124 subagent
vacuum:  2252 MB -> 1774 MB in 10.1s
after:   1774 MB, 3,630 sessions (2,057 human)  <- human count unchanged
integrity: ok   reclaimed: 478 MB   total runtime: 2m0s
```

FTS recall for four sampled terms was identical before and after (200 hits
each, capped), and the gateway kept serving throughout.

Note the shape: **8,447 sessions deleted freed 478 MB**, while the file is
still 1.77 GB. Most of what remains is FTS index, not conversation. Retention
stops the growth; it does not shrink a store back to nothing.

## Pitfalls

- **Never stop the gateway to do this.** Hermes' lifecycle guard blocks a
  gateway from restarting itself (anti-respawn-loop), and a script that dies
  between stop and start leaves the service down with nobody home. This script
  never stops anything; it works against the live database and keeps its lock
  inside the write-patience budget instead.
- **Do not run two profiles on one host concurrently.** Studio hosts five.
  Their databases are separate files, so they do not block each other at the
  SQLite level, but two simultaneous VACUUMs contend for the same disk and each
  one's lock stretches past its prediction. Stagger them.
- **Do not run this during an active incident.** If an agent is already wedged
  or its disk is filling, adding an exclusive lock and a 2.5x temporary rewrite
  makes it worse. Retention-only (`--no-vacuum`) is the safe move mid-incident.
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
