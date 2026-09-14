# Scheduled Job Model Upgrade Review

Session-derived guidance for auditing cron/scheduled jobs for stronger model tiers (for
example an Omniroute `think` combo) and multi-review use.

## Durable pattern

When reviewing scheduled jobs for model upgrades:

1. Inventory jobs across all relevant Hermes homes/profiles, not only the active
   profile.
2. Classify by cadence before recommending model changes:
   - High-frequency polling/watchdogs stay cheap or script-only.
   - Daily flagship synthesis can justify a stronger model when output quality matters.
   - Weekly/nightly synthesis and self-improvement jobs are usually the best upgrade
     targets.
   - `no_agent` script jobs have no model to upgrade.
3. Classify by ownership:
   - If a job exists in the wrong persona/profile, remove the duplicate rather than
     upgrading both.
   - Operational fleet/support jobs should live with <agent-a> unless the user says
     otherwise.
4. Classify by side effects:
   - Read-only synthesis jobs can move to stronger models freely.
   - Jobs that create tasks, send messages, trade, mutate state, or change config need
     explicit guardrails before upgrading.
5. Verify after edits by re-reading the scheduler/job state, not just the file you
   edited.

## Cron cadence pitfall

Do not confuse weekday jobs with weekly jobs.

- `0 17 * * 0` = weekly, Sunday.
- `15 9 * * 1` = weekly, Monday.
- `0 18 * * 3` = weekly, Wednesday.
- `0 7 * * 1-5` = every weekday, not weekly.
- `15 19 * * 1-5` = every weekday, not weekly.

If the user says "weekly stuff," only change true once-per-week schedules, unless they
explicitly include weekday jobs.

## Multi-review fit

Use multi-review for scheduled jobs only at durable decision points:

- Weekly portfolio/business/self-improvement reviews.
- Nightly knowledge-base maintenance that writes durable conclusions.
- Cron fleet/model-upgrade plans before applying broad config changes.
- Task-creation or external-action jobs only immediately before the side effect, with a
  duplicate/false-positive lens.

Avoid multi-review for:

- High-frequency polling jobs.
- Deterministic health checks.
- Silent watchdog wrappers.
- Script-only `no_agent` jobs.

## Model-tier upgrade heuristics

Good `think`/strong-model candidates:

- Weekly reviews and rollups.
- Nightly synthesis/knowledge consolidation.
- Flagship daily briefs where judgment and prioritization matter.

Usually keep on `work`/cheap models:

- High-frequency inbox/email stewards.
- Fleet watchdogs.
- Deterministic health checks.
- Jobs whose main task is refresh/fetch/format rather than judgment.

For high-frequency stewards, prefer a separate nightly/weekly strong-model self-audit
over upgrading every polling pass.
