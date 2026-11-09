# Multi-PR review-comment ledger

Use this when addressing bot/human comments across more than one PR or checkout in a
single run. Keep it as scratch notes in the session (or a temporary file) and update it
immediately after each commit/push.

## Ledger template

| PR  | Title     | Head branch | Checkout | Comment IDs / paths | Fix commit(s)     | Pushed ref verified       | Verification             |
| --- | --------- | ----------- | -------- | ------------------- | ----------------- | ------------------------- | ------------------------ |
| #N  | `<title>` | `<branch>`  | `<path>` | `<id>: path:line`   | `<sha> <subject>` | `HEAD == origin/<branch>` | `<command>` → `<result>` |

## Minimum checks before final report

For each PR branch:

```bash
git -C <checkout> status --short --branch
git -C <checkout> rev-parse HEAD
git -C <checkout> rev-parse origin/<head-branch>
git -C <checkout> show --stat --oneline --no-renames HEAD
```

Also record the exact quality gates that passed, not just "tests passed":

```bash
pre-commit run --files <changed files>
python -m pytest <suite or targeted tests> -q
```

## Merge-main-into-branch pattern (the #1 batch-run CI fix)

When a repo's CI runs `pre-commit --all-files` (common in many repos), a fix that lands
on main (e.g. trailing whitespace in a shared file) makes every other open PR branch
fail CI on files it never touched. After subagents push their fixes, the orchestrator
MUST merge main into each branch before checking CI:

```bash
cd <checkout>
git fetch origin main
git checkout <pr-branch>
git merge origin/main --no-edit
# If conflicts: resolve each hunk by reading both sides (see below), then
# run the tests before committing.
git add <resolved files> && git commit -m "Merge main: resolve conflicts"
git push origin <pr-branch>
```

**Most common conflict:** a shared test file where several PRs each added test functions
at the same spot. Keeping both is usually right, but resolve each hunk by hand; never
strip the markers and concatenate. If both sides edit the same test, or define a test
with the same name, Python keeps only the later definition and silently drops the
other's coverage while pytest stays green. Rename or merge those, then check the
collected test count went up by what both PRs added
(`pytest --collect-only -q | tail -1`).

**Subagent-created checkouts**: subagents may create their own checkouts at unexpected
paths (e.g. `~/src/<repo>-pr33`). Record every clone path in the ledger; for linked
worktrees, `git worktree list` finds them. Use those for follow-up operations rather
than creating new ones.

**`gh pr merge --squash` is silent on success** — always verify:

```bash
gh pr view <n> --json state --jq '.state'  # should be "MERGED"
```

## Orchestrator verification commands

After each subagent returns, verify independently:

```bash
# Push landed?
git fetch origin <branch> && git log --oneline origin/<branch> -2
# CI status (check BOTH matrix runs if the repo uses a Python version matrix)
gh pr checks <n> | grep -E "pass|fail"
# Merge after green (non-money-path PRs only; money-path → leave for human)
gh pr merge <n> --squash
gh pr view <n> --json state --jq '.state'  # verify "MERGED"
```

## Common batch-run mistakes this prevents

- Reporting from memory after several context compactions and losing the exact commit
  SHA.
- Pushing a local batch branch while the PR head branch remains unchanged.
- Mixing verification results between checkouts that share the same repo but not the
  same branch.
- Forgetting that a PR can be a review-sweep PR itself and receive new review comments
  on the review-fix code.
- Forgetting to merge main into each branch after a pre-commit `--all-files` fix landed
  on main — branches diverged from main will fail CI on unrelated files.
- Not finding subagent-created checkouts (they may be at unexpected paths — read the
  ledger's clone paths, or `git worktree list` for linked worktrees).
