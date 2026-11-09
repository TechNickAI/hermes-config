# CI Infrastructure Flake Patterns

Common CI failures that look like test/code failures but are actually infrastructure
flakes. Recognize them before trying to "fix" code that isn't broken.

## Container-Initialization Timeout (Docker Image Pull Exceeding Job Time Limit)

**Signature in logs:**

```
The job has exceeded the maximum execution time of 5m0s
##[error]The operation was canceled.
```

The failure occurs during the "Initialize containers" step — pulling Docker images for
service containers (MySQL, Redis, etc.) — before any project code runs. The job's
`timeout-minutes` (often 5 minutes) is consumed entirely by image downloads.

**How to identify:**

- `gh run view <RUN_ID> --job <JOB_ID> --log` shows the job stuck in "Initialize
  containers" with Docker pull progress lines, then
  `##[error]The operation was canceled.`
- No test output, no lint output, no build output — the job never reached the steps that
  run project code.
- The timestamp gap between job start and cancellation matches the `timeout-minutes`
  setting (e.g. started 13:39, canceled 13:44 = exactly 5 minutes).

**Fix:** This is an infrastructure flake. Rerun the failed job:

```bash
# Rerun only the failed jobs (faster than rerunning everything)
gh run rerun <RUN_ID> --repo <OWNER>/<REPO> --failed

# Poll until the rerun completes
gh pr checks <PR_NUMBER> --repo <OWNER>/<REPO>
# or
gh pr checks <PR_NUMBER> --repo <OWNER>/<REPO> --watch
```

If the rerun passes (typical — image cache is warm from the first attempt), proceed to
merge. If it fails the same way, the timeout may need to be raised in the workflow YAML
(`timeout-minutes:` on the job) — flag this to the user.

## Review-Bot Auth Failure (401 / Empty API Key)

Already covered in the main SKILL.md (step 2a). Short version: `claude-review` or
similar AI-review checks fail with `401 Invalid authentication credentials` when a
secret (`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`) is missing or expired in the workflow
config. Fails CI-wide across every open PR simultaneously. Fix the secret, don't chase
phantom findings.

## Distinguishing Infra Flakes from Real Failures

| Signal        | Infra Flake                                                               | Real Code Failure                                                 |
| ------------- | ------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Failure phase | Before project code runs (container init, setup, checkout)                | During test/lint/build steps                                      |
| Error message | "operation was canceled", "exceeded maximum execution time", 401/403 auth | AssertionError, lint violations, build errors with file:line refs |
| Scope         | Affects multiple PRs or repeats across reruns                             | Specific to this PR's code changes                                |
| Log content   | Docker pull progress, network errors, auth errors                         | Test tracebacks, compiler output, linter messages                 |

**Decision tree:**

1. Read `gh run view --job <JOB_ID> --log` (or `--log-failed`)
2. If failure is in container/setup/auth phase with no project code output → infra flake
   → rerun
3. If rerun passes → merge
4. If rerun fails same way → real infra issue → flag to user, don't merge
5. If failure is in test/lint/build phase → real code failure → fix the code
