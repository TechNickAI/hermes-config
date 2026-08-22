---
name: scheduled-job-runner
description: Use for cron/scheduled job work. Ships jobrun.py runner.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [cron, jobs, reliability, uv, pep723, alerting, fleet]
    related_skills: [cron-healthcheck]
    # referenced but not shipped here (another source):
    #   capacity-monitoring-and-alerting, agent-notification-suppression
---

# Scheduled Job Runner

## When to use

Creating, fixing, migrating, or reviewing ANY scheduled job on the fleet — Hermes cron
`script:` jobs, `no_agent` watchdogs, shell wrappers around Python, or a job that is
noisy, silently failing, delivering nowhere, overlapping itself, or missing
dependencies. Trigger phrases: "this job is noisy", "the cron failed silently", "job
output went to the wrong place", "wrap this script for cron", "why did this job not
run", "add a scheduled job", "should this use uv".

## Overview

One execution adapter for every scheduled job. Hermes cron remains the **scheduler**;
`jobrun.py` is the **runner** the job's `script:` field points at. It exists because
scheduled jobs tend to each re-implement the same handful of concerns, badly and
inconsistently, once a setup grows past a few of them.

**Do not build another scheduler.** Do not adopt `cronic` — it exits 0 after a child
failure, which blinds the Hermes failure alert.

Runner lives at `<profile>/scripts/jobrun.py`.

## The seven responsibilities jobrun owns

Each was previously hand-rolled per script:

1. **Interpreter/dependency resolution** — kills the `.sh` wrapper hack
2. **Silence-on-success** — kills hand-rolled `if [ $RC -ne 0 ]` blocks
3. **Overlap prevention** — flock; hand-rolled in only a tiny minority of scripts
4. **Hard timeout + signal handling** — timeout distinguishable from failure
5. **Structured run ledger** — exit code, duration, outcome
6. **Bounded log capture + secret redaction** — quiet ≠ discard evidence
7. **Heartbeat / dead-man's-switch** — the only thing catching "never ran"

## Quick start

```bash
# once per host: install uv, verify the Python floor, create state dirs
jobrun.py --bootstrap

cat > $HERMES_HOME/jobs.d/my-job.toml <<'EOF'
job_id        = "my-job"
script        = "my_script.py"   # relative to $HERMES_HOME/scripts/
runtime       = "auto"           # auto | uv | python | bash
timeout       = 300
overlap       = "skip"           # skip | allow | queue
output_policy = "passthrough"    # passthrough | silent
args          = ["--mode", "daily"]
owner         = "platform-team"
EOF

jobrun.py --spec my-job --dry-run    # validate, run nothing
```

Then point the cron job's `script:` at `jobrun.py --spec my-job`.

Verify the runner itself: `jobrun.py --selftest` (26 checks, real processes, non-zero
exit on any failure).

## Day-2 operations

```bash
jobrun.py --list             # every job on this host + its last outcome
jobrun.py --status my-job    # recent runs, exit codes, durations, log path
jobrun.py --failures 24      # failures in the last N hours; SILENT when clean
jobrun.py --bootstrap        # install/verify uv + python floor
```

`--failures` prints nothing when nothing is wrong, so it is safe to schedule as its own
job.

## Passing arguments

Arguments live in the spec, not in a hand-written wrapper that hardcodes them into an
`exec` line:

```toml
args = ["--symbol", "AAPL", "--lookback", "30"]
```

They are appended to argv for every runtime (`python`, `uv`, `bash`, `command`). Two
schedules of the same script with different arguments are two specs, not two copies of
the script.

## Controlling noise

`output_policy` decides what a SUCCESSFUL run sends to the human:

- `passthrough` (default) — stdout byte-for-byte verbatim. Preserves the scheduler's
  delivery contract and any trailing control payload such as `{"wakeAgent": false}`.
- `silent` — never speak on success, whatever the job printed. **This is the fix for a
  noisy job: set it in the spec, do not edit the script.** Failures still report.

Failures always deliver an incident card regardless of policy.

## Money jobs: `critical = true`

For a job that moves real money, set `critical` in the spec:

```toml
critical = true
timeout  = 600     # REQUIRED for critical jobs, and shorter than the interval
```

What it changes:

- The failure card leads with `🛑 CRITICAL` and says the job moves real money, so it
  does not read like a failed report generator.
- `--failures` lists critical failures FIRST and counts them in the header.
- The spec is REJECTED unless it declares its OWN timeout. A critical job must not
  inherit the default, because a job that can outrun its own schedule needs a stated
  ceiling.

What it deliberately does NOT change: execution. The runner never gates on domain state.
A kill-switch belongs at the single choke point every action funnels through, not in a
wrapper that would become a second, weaker authority.

## Nested wrappers and `deployed_sha`

Some jobs already run their own domain recorder (business counters, application-specific
state). Two ledgers for one run is fine; two IDENTITIES is not, because failures then
double-count. jobrun exports its identity to the child:

| variable          | meaning                                  |
| ----------------- | ---------------------------------------- |
| `JOBRUN_RUN_ID`   | adopt this instead of inventing a run id |
| `JOBRUN_JOB_ID`   | the spec's job_id                        |
| `JOBRUN_CRITICAL` | `1` when the job is critical             |

Every ledger row also records `deployed_sha` — the short git SHA of the tree the job ran
from (via `cwd`). Without it, run history and a deploy-drift watchdog can disagree about
what actually ran.

## Terminal states — never collapse into exit 1

| state             | exit | meaning                                     |
| ----------------- | ---- | ------------------------------------------- |
| `success`         | 0    | job completed                               |
| `skipped_overlap` | 0    | previous run still going; **not** a failure |
| `config_error`    | 2    | runner could not START the job              |
| `child_failure`   | 3    | the job itself exited non-zero              |
| `timeout`         | 4    | exceeded its deadline                       |
| `signal`          | 5    | killed by a signal                          |
| `wrapper_error`   | 6    | the runner itself broke                     |

`config_error` vs `child_failure` matters most to a human: "your job is broken" and "I
could not start your job" are different pages. 126/127/128+ are deliberately avoided
(shell-reserved).

## Python dependencies: locked PEP 723 + uv

A scheduled Python script declares its own dependencies inline and locks them. It never
relies on whatever happens to be in the agent's venv.

```python
# /// script
# requires-python = ">=3.13"
# dependencies = ["yfinance"]
# ///
```

```bash
uv lock --script my_script.py     # creates my_script.py.lock — COMMIT IT
```

`runtime = "auto"` detects the PEP 723 block and runs `uv run --locked --script`. No
block → agent venv; `.sh`/`.bash` → bash.

**Why:** the Hermes runner executes non-`.sh` scripts with `sys.executable` (the agent
venv). A script needing a package that venv lacks fails with `ModuleNotFoundError` —
exactly why people write a `.sh` wrapper that execs a different interpreter. Declare the
dep instead.

### Pitfalls

- **A PEP 723 header alone does nothing.** It is a comment to the stock runner.
  Verified: the same script resolved requests 2.33.0 via the agent venv vs 2.34.2 via
  its own lock. Route through jobrun or the pin is fiction.
- **Always `--locked`.** Without it uv may silently rewrite the lock at 3am. jobrun adds
  it automatically when a `.lock` exists.
- **`--offline` only after prewarming** that exact script/lock/python/host combo.
  Default online mode can revalidate index metadata and fail mid-outage.
- **Do not stack many cold uv starts on one minute.** Concurrent cold environment
  creation contends badly. Stagger schedules.
- **uv must exist on the host.** Check `command -v uv` before migrating.
- **macOS `/usr/bin/python3` cannot be upgraded** — Apple-owned, SIP-protected, restored
  by OS updates. Never "fix" a dep problem by pointing at it.
- **Preserve the wake gate.** If a job emits a final `{"wakeAgent": false}` line, stdout
  must pass through byte-for-byte. jobrun does; verify after any change to delivery
  formatting.

## What a job may send a human

Governing test: **does this change what the human would do?** If not, stay silent.

- **Never notify on routine success.** Success goes to the ledger, not the chat.
- Alert on **symptoms** (missing data, stale output, deadline breach), not internal
  causes (one retry, a CPU spike, a transient exception).
- A single transient failure **retries silently**; page on exhausted retries.
- "Ran and found nothing" must be provable: record `outcome=no_change` and
  `records_examined`. **Missing telemetry is UNKNOWN/MISSED, never zero.**
- **Dedup by condition, not execution:** `dedup_key = host/job_id/failure_class`. Keep
  `occurrence_count` + `first_seen_at`; update ONE incident rather than reposting. A
  repeated identical alert is an UNACKNOWLEDGED ALARM — attach a count and escalate on
  age; never delete the newest copy.
- Failure message = **incident card**, not a stdout dump: severity + job + condition,
  impact, evidence (never an invented root cause), attempts and duration, the exact next
  safe action, owner, occurrence count, log path, run id.

## Heartbeat / dead-man's-switch

A wrapper can only report after it starts. It can never report that it **never started**
— host down, gateway dead, scheduler stalled, job deleted. Only an external
schedule-aware receiver catches that.

Model: `SCHEDULED -> STARTED -> SUCCEEDED | FAILED | TIMED_OUT | MISSED`, stable
`run_id`, `scheduled_at` recorded separately from `started_at`. jobrun sends `/start`
before work and exactly one terminal ping after (healthchecks.io wire format: `/start`,
`/<exit-code>`, `/fail`, `?rid=`).

Set grace = normal runtime + realistic jitter, not an arbitrary delay.

**The heartbeat must never change the job's outcome.** 5s timeout, exceptions swallowed,
delivery status recorded. A monitor being down must not take the job down.

## Reading the ledger

`$HERMES_HOME/jobstate/runs.jsonl` — one JSON object per terminal event with `job_id`,
`run_id`, `state`, `exit_code`, `duration_ms`, `attempt`, `argv`, `runtime`, timestamps,
`log_path`, `heartbeat`.

```bash
python3 -c "
import json,collections,os
p=os.path.expandvars('\$HERMES_HOME/jobstate/runs.jsonl')
rows=[json.loads(l) for l in open(p)][-200:]
c=collections.Counter((r['job_id'],r['state']) for r in rows)
for (j,s),n in c.most_common(): print(f'{n:4d}  {j}  {s}')"
```

This is the status API. **Never grep raw stdout for status.**

## Verification checklist

- [ ] `jobrun.py --selftest` passes
- [ ] `--dry-run` shows the expected `argv` — confirm the interpreter is the one you
      intended; this is the whole point
- [ ] Job runs clean and **silent** on the success path
- [ ] Failure path exercised on purpose; message is an incident card
- [ ] If it declares deps: `.lock` exists and is committed
- [ ] Overlap policy deliberate, not the default by accident
- [ ] Timeout shorter than the schedule interval
- [ ] If the job's success is self-evidencing (writes a file/row), its FAILURE path is
      the only path that ever delivers — test that path explicitly
