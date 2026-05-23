---
name: cron-healthcheck
description:
  Detect broken Hermes cron jobs and escalate to a remediation sub-agent. Triage cheap,
  fix expensive.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [cron, monitoring, self-healing, fleet, observability]
    related_skills: [fleet-management, kanban-orchestrator]
---

# Cron Healthcheck

A two-tier monitor for Hermes cron jobs. The triage layer runs hourly on a cheap model,
notices when a job has been failing repeatedly, and delegates remediation to an
expensive sub-agent that can actually diagnose and fix.

When everything is healthy, output is **silent** — no notifications, no chat noise.

## When to load

- A scheduled tick from a cron job named `cron-healthcheck` (or similar)
- The user asks "are my crons healthy?" / "any broken jobs?"
- Investigating why a periodic task hasn't run lately

## Prerequisites

- The `cron` toolset is enabled (`hermes tools enable cron`)
- Sub-agent spawning available (`delegation` toolset)
- A notification channel for escalations (set `CRON_HEALTH_ADMIN_TARGET` env var to a
  `send_message` target like `telegram:<chat_id>` — defaults to the home channel if
  unset)

## How it thinks

Every cycle is the same three-step loop:

1. **Survey** — `cronjob(action="list")` to enumerate every job, including disabled
2. **Detect** — Flag any enabled job with `consecutive_errors >= 3`
3. **Branch** —
   - All healthy → reply `HEARTBEAT_OK` and stop (silent success)
   - Anything broken → spawn a remediation sub-agent (see below)

The triage layer does **not** diagnose, **not** remediate, **not** post status updates.

## Detection

In a cron-run session, the `cronjob` toolset is **not** auto-loaded (cron jobs run with
a restricted toolset to keep the runtime cheap). Try the tool first; fall back to
reading `~/.hermes/cron/jobs.json` directly if it's unavailable:

```python
try:
    jobs = cronjob(action="list")["jobs"]
except (NameError, AttributeError):
    import json, pathlib
    jobs = json.loads(pathlib.Path("~/.hermes/cron/jobs.json").expanduser().read_text())
    # jobs.json shape: list of {id, name, enabled, last_status, last_error,
    # consecutive_errors (older builds), schedule, ...}

broken = [j for j in jobs
          if j.get("enabled", True)
          and j.get("consecutive_errors", 0) >= 3]
```

If `consecutive_errors` is missing entirely (newer Hermes versions track failure runs
differently), inspect each job's recent runs under `~/.hermes/cron/output/<job_id>/` and
count how many of the last 3 are FAILED.

If `broken` is empty, return exactly:

```
HEARTBEAT_OK
```

This produces zero output to messaging channels.

## Escalation to sub-agent

When `broken` is non-empty, delegate via `delegate_task`:

```python
delegate_task(
    goal="Diagnose and remediate failing Hermes cron jobs",
    context=f"""
The following cron jobs have consecutive_errors >= 3:

{render_broken_jobs(broken)}

For each failing job, follow this playbook:

1. DIAGNOSE — Inspect the last run output via cronjob(action="list") and any
   referenced scripts. Common causes:
   - Timeout: job exceeds its `timeout_seconds`
   - Crash: the prompt/skill has a bug or hits a tool limit
   - API failure: upstream service is down (rate-limit, 5xx, auth)
   - Config drift: a referenced file moved or a credential expired

2. REMEDIATE — Based on diagnosis:
   - Timeout → cronjob(action="update", schedule=..., timeout=min(current*2, 3600))
   - Config drift → fix the referenced file or skill
   - API failure → record for the report, no auto-fix
   - Crash → record for human escalation, do not guess at code changes

3. VERIFY — cronjob(action="run", job_id=...) to force a test run. Wait for
   completion. Confirm consecutive_errors resets to 0.

4. REPORT — Send a single summary message to the admin channel (env var
   CRON_HEALTH_ADMIN_TARGET, default home channel) covering:
   - Each broken job + root cause
   - Remediation taken (or skipped, with reason)
   - Test-run outcome
   - Jobs that need human investigation

If a remediation attempt fails (test still errors after fix), send an explicit
"needs human" message — include job id, error, and what was tried.
""",
    toolsets=["cron", "terminal", "file", "delegation"]
)
```

## What you do NOT do

- **No remediation** at the triage layer — every fix happens in the sub-agent
- **No status notifications when healthy** — silent success is the contract
- **No fixes on disabled jobs** — disabled means someone disabled it
- **No threshold tuning** — `consecutive_errors >= 3` is the bright line

## State

This skill is stateless. The sub-agent may write a markdown log under
`~/.hermes/cron/runs/cron-healthcheck/YYYY-MM-DD.md` with the remediation history;
delete files older than 30 days on each run.

## Cron setup

Suggested install:

```bash
hermes cron add \
  --name "cron-healthcheck" \
  --schedule "5 * * * *" \
  --tz "<your-timezone>" \
  --skill cron-healthcheck \
  --model "<cheap-triage-model>" \
  --timeout 120
```

Hourly at `:05` — offset from `:00` so it doesn't collide with jobs that fire on the
hour. Use the cheapest model that can reliably follow the detection logic (Gemini Flash,
Haiku, etc.); the expensive model only runs when something is actually broken.

## Budget

| Path                  | Turns       | Model     |
| --------------------- | ----------- | --------- |
| Healthy (heartbeat)   | 2-3         | cheap     |
| Broken → spawn        | 3-5         | cheap     |
| Sub-agent remediation | 10-20 / job | expensive |

## Pitfalls

- **Naming `consecutive_errors`** — field name varies between Hermes versions; check
  with `cronjob(action="list")` first. Fall back to inspecting recent run records if the
  field is absent.
- **HEARTBEAT_OK suppression** — Hermes' gateway treats short single-line responses as
  no-ops only when delivery is configured `local`. Make sure the cron job is created
  with `deliver="local"` (or omit deliver entirely so it doesn't broadcast).
- **Sub-agent loop guard** — if the spawned remediator itself fails, do **not**
  re-spawn. Send the failure straight to the admin target.

## Origin

This was an OpenClaw `workflows/cron-healthcheck/AGENT.md` recipe ported to a Hermes
skill. The original folder-based workflow pattern (one directory per workflow with
sibling state files) doesn't map cleanly to Hermes — skills are the right home for agent
procedures, cron jobs are the right home for scheduling, and `~/.hermes/cron/` is the
right home for run state.
