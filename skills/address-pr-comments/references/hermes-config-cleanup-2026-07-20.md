# hermes-config PR cleanup — one occasion

5 open PRs across TechNickAI/hermes-config, all CI-green. Addressed bot comments and
merged in dependency order.

## PRs processed

| PR  | Title                                                              | Comments                                         | Result               |
| --- | ------------------------------------------------------------------ | ------------------------------------------------ | -------------------- |
| #64 | fix(cortex): survive embedding model prefix changes                | 0 open                                           | Merged (clean)       |
| #63 | fix(grok-search): handle --recency-days 0                          | 0 open                                           | Merged (clean)       |
| #59 | fix(multi-review): slow reviewers on router path, parallel default | 1 (codex P1: incident dates)                     | Fixed + merged       |
| #62 | fix(moa-solve): review-sweep issues from #61                       | 4 (cursor+codex: fitlog schema + omni fail-fast) | Fixed all 4 + merged |
| #57 | docs(knowledge): discovery-harvest pattern                         | —                                                | Skipped (draft)      |

## Comment patterns addressed

### #59: Incident-specific dates in public skill (codex P1)

Codex flagged exact dates ("Confirmed one occasion; parallel default reaffirmed one
occasion") in `skills/multi-review/SKILL.md`, citing the repo's AGENTS.md guidance to
generalize incident dates. Fix: replaced with "Confirmed in practice; parallel default
reaffirmed after a later recurrence."

### #62: Fitlog schema migration (cursor + codex, 2 threads)

**Problem:** `fitlog.py` renamed score columns from `quality/creativity/correctness` to
`completeness/soundness/actionability/usable_novelty/testability`, but
`CREATE TABLE IF NOT EXISTS` leaves old-schema tables in place. `score()` and `report()`
then fail with `OperationalError` referencing columns that don't exist.

**Fix:** Added `migrate_legacy_scores(c)` that checks `PRAGMA table_info(scores)` for
the `soundness` column; if absent, archives the old table as `scores_legacy_v1` and lets
`CREATE TABLE IF NOT EXISTS` create the fresh schema. Called from `conn()` so EVERY
entry point auto-migrates, not just `init()`.

**Test:** Created a DB with old schema, ran `init`, confirmed old table archived + new
schema created + legacy rows preserved.

### #62: OmniRoute fail-fast (cursor + codex, 2 threads)

**Problem:** `panel.py`'s `run_seat()` catches all exceptions in a broad
`except Exception`, including `RuntimeError("MOA_OMNI_BASE_URL env var is required")`.
This means missing config produces exit code zero with only a per-seat `error` field —
easy to miss.

**Fix:** Moved the `OMNI_BASE` check BEFORE the `try/except` block, so missing config
raises immediately and propagates as a real failure instead of being swallowed.

## Techniques used

- **GraphQL review-thread queries via `.graphql` file:** `gh api graphql -f query=...`
  fails with "Expected VAR_SIGN, actual: COLON" on inline GraphQL. Write to a file and
  use `$(< file)` expansion with `-F` variable flags.
- **Check-runs API as 503 fallback:** When `gh run list` returns 503 during GitHub
  degradation, `gh api repos/<repo>/commits/<sha>/check-runs` is often available and
  provides the same status/conclusion data.
- **Draft PR detection:** Always include `isDraft` in batch PR status polls.
  `gh pr merge` on a draft fails with "Pull Request is still a draft
  (mergePullRequest)".
- **Pre-commit after every edit:** `ruff-format` will reformat manually-edited code
  (e.g. `bulk_create` refactors). Run `pre-commit run --files <changed>` after every
  edit and before pushing to catch formatting changes before CI does.

## Merge order

Independent PRs (no shared files): merged #64 + #63 first (zero comments, clean), then
#59 and #62 in any order (both rebased clean on latest main). #57 skipped as draft.
