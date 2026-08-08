---
name: robustify-doctor
description:
  Use when you need to know whether a Hermes agent is actually healthy — during a
  scheduled health check, after an outage or reboot, when someone asks "is X working?",
  or when an agent has gone quiet. Runs a deterministic collector that gathers facts
  across twelve subsystems, then reads those facts as an LLM to correlate symptoms into
  incidents, classify severity, and separate real breakage from things that merely look
  alarming. The script collects; the judgment is yours.
version: 0.1.0
license: MIT
metadata:
  hermes:
    requires:
      - Python 3.9+ (stdlib only, no third-party packages)
      - Read access to the target agent's HERMES_HOME
    tags: [monitoring, diagnostics, reliability, health-check, fleet, observability]
    related_skills: [cron-healthcheck]
---

# Robustify Doctor

## Overview

Most agent monitoring answers "did the process exit 0?" That question misses the failure
class that actually hurts: the job that ran, exited clean, and did nothing. The backup
that failed 573 consecutive times while its launchd unit reported success. The seven
scheduled jobs that had never executed once, two of which were health checks.

This skill is deliberately split in two:

1. **`scripts/robustify_collect.py`** — deterministic, stdlib-only, no LLM, no network.
   It gathers facts and refuses to interpret them. Every collector is fail-soft: a
   broken collector reports its own failure rather than killing the run.
2. **You** — you read the fact sheet and do the part a script cannot: correlate across
   subsystems, decide what is a real incident versus an artifact, and choose what to fix
   versus escalate.

The split matters. A pure-threshold monitor produces alerts nobody trusts, because the
interesting failures are the ones nobody wrote a threshold for. Judgment is the product;
the script is an instrument.

## When to Use

- A scheduled health check (see cadence below)
- After a host reboot, an upgrade, or a service migration
- Someone asks "is my agent working?" / "why did it go quiet?"
- An agent has stopped producing output and you don't yet know why
- Before and after any risky change, as a before/after comparison

**Don't use for:** diagnosing a single known-broken job (read its log directly), or
anything requiring live probes of third-party integrations — this collector is
deliberately passive and read-only.

## Running it

```bash
python3 scripts/robustify_collect.py              # ~1-2s, all collectors
python3 scripts/robustify_collect.py --deep       # adds sqlite PRAGMA quick_check
```

Against another agent's home, or another host:

```bash
HERMES_HOME=/path/to/other/.hermes python3 scripts/robustify_collect.py
ssh <host> 'python3 -' < scripts/robustify_collect.py
```

Pipe the script over stdin rather than quoting it into `ssh ... python3 -c`. Multiline
inline shell over SSH silently returns a single blank line often enough to waste an
hour.

`--deep` runs `PRAGMA quick_check`, which costs ~40s on a multi-GB state database. Fine
for a daily pass, too slow for a frequent one. The default skips it and says so in the
output rather than implying the check passed.

## What it collects

| Section                 | Answers                                                      |
| ----------------------- | ------------------------------------------------------------ |
| `MACHINE`               | disk, memory, thermal, clock drift, timezone                 |
| `DISK_TRAJECTORY`       | is disk trending toward full, and how many hours out         |
| `SCHEDULED_JOBS`        | what never ran, what is stalled, what is overdue             |
| `JOB_OUTPUT_FRESHNESS`  | which jobs "succeed" while producing nothing                 |
| `LOGS`                  | log volume and whether rotation is actually happening        |
| `PROCESSES`             | gateways per profile, RSS per profile, failing launchd units |
| `DATABASES`             | size and integrity of state / cortex / executions / kanban   |
| `CORTEX`                | page counts, journal freshness, FTS index queryability       |
| `INTEGRATIONS_PASSIVE`  | last activity per channel, credential presence, file mode    |
| `USER_SURFACE`          | listening sockets, PM2 process health and restart counts     |
| `CONFIG`                | version, install SHA, model block, timezone, skill count     |
| `BACKUPS`               | success/failure counts and **consecutive trailing failures** |
| `COLLECTOR_SELF_HEALTH` | which collectors failed — the monitor monitoring itself      |

## Reading the output

Prompt yourself (or a sub-agent) with the collector report and these instructions:

```
You are a systems diagnostic agent. Below is deterministic collector output —
facts only, no interpretation.

1. CORRELATE across domains. Group related facts into single incidents rather
   than listing symptoms separately.
2. Classify each finding: BROKEN NOW / WILL BREAK SOON / COSMETIC / NOT A PROBLEM.
3. For each real finding: state the likely cause, the evidence line(s) it rests
   on, and whether it is safe to auto-fix (restart/rotate/clear-cache class) or
   needs a human.
4. Explicitly call out anything that looks alarming but is actually FINE —
   false-positive suppression matters as much as detection.
5. Note what you CANNOT determine from this data and what you'd collect next.

Rules:
- Every claim must cite a specific line from the collector output.
- Do not recommend deleting user data.
- Be concise and concrete. No preamble.
```

Point 4 is not decoration. Roughly half of what looks alarming in a first report is
benign, and a monitor that cries wolf gets ignored within a week.

## Interpretation traps

These are wrong readings that were made against real data. The collector emits the
disambiguating fact in each case — use it.

1. **A stale `running` row is not a stalled job.** On one host, 3 of 4 long-running rows
   belonged to jobs that had since completed 26-85 times: orphan rows from a crash. The
   collector separates `jobs_stalled_no_later_completion` (real) from
   `jobs_orphan_running_rows_benign` (noise). Age alone is the wrong discriminator; the
   presence of a _later terminal run for the same job_ is the right one.

2. **"No row in executions.db" does not mean "never ran."** That database retains ~20
   hours on a busy host. The collector distinguishes `jobs_no_evidence_of_ever_running`
   (no execution row AND no output file — real) from
   `jobs_last_ran_before_retention_window` (ran, just outside the window — expected).
   Always read `execution_retention_hours` before drawing conclusions about job history.

3. **A nonzero launchd `last_exit` may predate a manual fix.** It is the last run's
   code, not current state. Corroborate with the unit's own log age, which the collector
   emits as `last_run_log_age_h`.

4. **High restart counts are not an outage.** `restarts=262` alongside
   `current_uptime=7.3h` means it crashed a lot historically and is stable now. Both
   facts matter; neither alone tells the story.

5. **`integrity=readable(deep-check-skipped)` is not `integrity=ok`.** It means the
   database opened and answered a query. Run `--deep` before claiming integrity.

6. **Backup success counts are meaningless without the consecutive-failure count.** 4
   successes and 573 failures is a broken backup, not a working one.

## Pitfalls found building this

1. **`pgrep` excludes itself and all its ancestors.** When this collector runs inside an
   agent, that agent's own gateway is invisible to `pgrep`. Verified: a host's own
   gateway was silently missing from every report while four siblings appeared normally.
   A monitor blind to the agent running it is worse than none. The collector uses
   `ps -axo` for this reason — do not "simplify" it back to `pgrep`.

2. **Unanchored process patterns match unrelated processes.** `hermes.*gateway` matched
   a node test runner in a checkout whose path contained "hermes", inflating the gateway
   count and reporting a 14MB RSS that belonged to the wrong process entirely.

3. **Unanchored launchd label matching is worse.** A filter of `/hermes|ace/` matched
   `com.apple.facetimemessagestored` and `com.apple.appplaceholdersyncd` — the substring
   "ace". Two Apple system daemons were reported as failing Hermes services. Anchor on
   `^ai\.hermes\.`.

4. **`sqlite3.connect(file:X?mode=ro)` is lazy.** It succeeds at connect time and only
   raises on the first query, because a WAL database needs a `-shm` sidecar that
   read-only mode forbids. The collector's `ro_connect()` validates with a real query
   before returning and falls back to `immutable=1`.

5. **FTS5 `integrity-check` is a WRITE.** `INSERT INTO t(t) VALUES('integrity-check')`
   on a read-only handle raises "readonly database", which is _not_ corruption.
   Reporting it as corruption is a false alarm on a scary-sounding subject.

6. **Sampling only the first gateway PID hides the bloated one.** Report RSS per
   profile.

## Cadence

Layer the checks; each catches what the one above cannot see.

| Frequency | Scope                                       | LLM?                                          |
| --------- | ------------------------------------------- | --------------------------------------------- |
| 5 min     | disk and memory hard limits                 | no — script only, silent when healthy         |
| 30 min    | gateway presence, database integrity        | no                                            |
| Hourly    | full collector + LLM read, auto-fix in-lane | yes, cheap model                              |
| Daily     | `--deep`, backup verification, digest       | yes, escalate to a stronger model on findings |

**Run it from outside the agent it monitors as well as inside.** A wedged agent cannot
report that it is wedged; every self-check runs inside the process that would be down.
External observation is the only way to detect total failure, and disagreement between
the internal and external view is itself a signal.

## Escalation

1. **Log** — within variance, or a first occurrence that self-resolved
2. **Auto-fix** — config-only, reversible, non-sensitive. Never user data, prompts, or
   memory.
3. **Surface, don't fix** — anything touching user-visible data, credentials, or state
   that a wrong fix would corrupt. A silently-mangling job auto-repaired is worse than
   one left broken.
4. **Wake a human** — blast radius is fleet-wide, or repair attempts are exhausted.
   Always with cause and blast radius attached, never a raw alert.

**Verify after fixing.** Force-run the job and confirm the symptom is gone. Exit 0 is
not verification — that is the same mistake this skill exists to catch.

## Verification Checklist

- [ ] `collectors_failed: 0` in `COLLECTOR_SELF_HEALTH` — a partial report read as a
      complete one is how you conclude "healthy" about a subsystem that never reported
- [ ] Every gateway you expect appears in `PROCESSES`, including the agent's own
- [ ] `execution_retention_hours` read before any claim about job history
- [ ] `--deep` run before any claim about database integrity
- [ ] Every finding cites a specific line from the report
- [ ] Benign-but-alarming items explicitly called out as benign
