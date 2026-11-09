# Pre-commit CI: `--all-files` behavior and auto-fix workflow

When CI uses `pre-commit/action@v3.0.1` (the standard GitHub Action for pre-commit), it
runs `pre-commit run --show-diff-on-failure --all-files`. This checks **every file in
the repo**, not just the PR's changed files. This has major implications for the
fix-CI-and-merge workflow.

## How to tell if CI runs --all-files

```bash
# Read the workflow YAML — look for the pre-commit action usage
grep -A2 "pre-commit".github/workflows/build.yml
# If you see: uses: pre-commit/action@v3.0.1  (with no extra args)
# → it runs --all-files by default
```

## The --all-files trap

When you run `pre-commit run --all-files` locally to reproduce a CI lint failure,
auto-fix hooks (ruff, ruff-format, prettier, smart-quotes, file-contents-sorter) will
modify **every file in the repo** that needs formatting — not just the files the PR
changed. This can produce a massive diff (50+ files) of unrelated formatting churn.

### Correct workflow

1. **Reproduce locally**: `pre-commit run --all-files` to see what CI sees.
2. **Verify pre-existing failures**: run the same command on the base branch in a
   throwaway clone, never by switching your PR checkout. Auto-fix hooks edit files on
   `main`; if any of them differ on the PR branch, `git checkout <pr-branch>` aborts and
   a following `git stash pop` lands on `main`. If main fails the same hooks with the
   same errors, those failures are pre-existing.
   ```bash
   # Subshell: SKILL.md uses $BASE for the base branch name; don't clobber it.
   # Check out what the fetch just returned (FETCH_HEAD): a local base branch
   # may be missing or stale, and single-branch clones get no origin/<base>.
   (
     b="${BASE:-main}"
     git fetch -q origin "$b" || exit 1
     tmp=$(mktemp -d)
     git worktree add -q --detach "$tmp/base" FETCH_HEAD || exit 1
     (cd "$tmp/base" && pre-commit run --all-files 2>&1 | grep -E "Passed|Failed")
     git worktree remove --force "$tmp/base"; rm -rf "$tmp"
   )
   ```
3. **Commit all auto-fixes anyway**: because CI runs `--all-files`, you must push ALL
   auto-fixes (even for files the PR didn't touch) to make the lint check pass. There's
   no way to make CI only check the PR's files.
4. **Non-auto-fixable errors remain**: ruff with `--fix` can auto-fix some issues
   (import ordering, unused imports) but NOT others (E501 line too long, F401 unused
   imports in some cases, PLC0415, EXE001, etc.). These remaining errors are
   pre-existing on main and will continue to fail CI. They are NOT fixable from this PR
   without manually editing every offending line.

### When to merge despite lint failures

If the repo has **no branch protection** (no required status checks), a PR can be merged
even with failing CI checks:

```bash
# Check for required status checks
gh api repos/$R/branches/main/protection \
  --jq '{required_status_checks:.required_status_checks, enforce_admins:.enforce_admins}'
# If required_status_checks is null/empty → no gates, mergeable despite red checks
```

But only do this when:

- The lint failures are confirmed pre-existing on main (not introduced by the PR).
- The user explicitly authorized merging despite known failures.
- You document which checks are failing and why in the merge summary.

## The fix-then-format iteration trap

When you manually edit code (e.g., to address a review bot comment), the edit may
introduce a formatting violation that ruff-format will reject.

**Symptom:** You fix a review comment, push, and CI lint fails again — this time
`ruff-format` says "files were modified by this hook" even though your ruff check
passed.

**Concrete example:** Replacing a single-line `if x not in (A | B):` with a chained
`if x not in A and x not in B:` creates a line longer than 88 chars. ruff-format wants
to wrap it in parentheses. The ruff _check_ hook passes (no lint error), but the ruff
_format_ hook fails.

**Fix:** Always run `pre-commit run --files <your-files>` (or at minimum
`ruff format <your-files>`) after every manual edit, before pushing:

```bash
# After any manual edit, before committing:
pip install ruff  # if not already installed
ruff format <changed-files>
pre-commit run --files <changed-files>
# Then commit and push
```

## Useful gh commands for CI debugging

```bash
# Get specific job logs (when run --log shows "still in progress")
JOB_ID=$(gh api repos/$R/actions/runs/$RUN_ID/jobs \
  --jq '.jobs[] | select(.name=="✨ Lint code") |.id')
gh api repos/$R/actions/jobs/$JOB_ID/logs 2>&1 \
  | grep -i "fail\|error\|ruff\|format"

# Check PR files (to know what the PR actually changed vs what pre-commit touched)
gh pr view <N> --repo <R> --json files --jq '.files[].path'
```

## Parallel PR workflow (multiple repos simultaneously)

When processing multiple PRs across different repos:

1. Clone each repo to a separate directory:
   `gh repo clone <owner>/<repo> ~/dev/<repo>-pr<N>`
2. Set git identity in each clone:
   `git config user.email <email> && git config user.name "<name>"`
3. Install pre-commit once: `pip install pre-commit` (shared across clones)
4. Run pre-commit in both repos simultaneously (parallel terminal calls)
5. Address comments/fixes in parallel, but serialize the push-then-check-CI loop
6. Clean up clone directories after merge: `rm -rf ~/dev/<repo>-pr<N>`

## Session notes (one occasion)

- One repo: ruff auto-fixed 2 import ordering issues; manual edit to address
  gemini-code-assist comment (set union → chained `and`) created a line >88 chars,
  ruff-format wrapped it in parentheses. Required two pushes: first for auto-fixes +
  comment fix, second for format wrap.
- Another repo: `pre-commit run --all-files` modified 59 files (formatting churn across
  the entire repo). Main branch also had 129 pre-existing ruff errors. No branch
  protection → mergeable despite failing lint. User authorized merge despite
  pre-existing CI failures.
