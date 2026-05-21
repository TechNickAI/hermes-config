# Parallel Work — Running Multiple Claude Code Sessions on this Repo

This repo is structured so multiple Claude Code sessions (or human + Claude sessions)
can make progress at once without stepping on each other.

## The unit of work is a GitHub issue

Every meaningful piece of work — a knowledge doc, a sample plugin, a migration runbook,
a tool — gets a GitHub issue with:

- **Scope** — one PR's worth of work
- **Acceptance criteria** — what "done" looks like
- **References** — pointers into `knowledge/`, transcripts, source code
- **Label** — `parallel-ready` if it's pickup-able by any session

See open work:
[`is:open label:parallel-ready`](https://github.com/TechNickAI/hermes-config/issues?q=is%3Aissue%20is%3Aopen%20label%3Aparallel-ready).

## The unit of delivery is a PR

One PR per issue. Coherent, focused, scoped to a single concept. This isn't ceremony —
it's so the `claude-code-review` action produces useful feedback and
`/address-pr-comments` has something concrete to work on.

PR title format: `<area>: <imperative>` (e.g. `knowledge: add memory deep-dive`,
`plugins: add Honcho sample plugin`, `docs: add migration runbook`).

## Claiming an issue

```bash
gh issue edit <NUM> --add-assignee @me
gh issue comment <NUM> --body "Starting on this — branch: <area>/<slug>"
```

If a session goes silent for >24h without a PR, the issue is free to grab.

## The worktree pattern (avoid stepping on each other)

When multiple sessions touch the same repo, use git worktrees so each session has its
own filesystem:

```bash
# from <repo-root>
git worktree add ../hermes-config-<slug> -b <area>/<slug>
cd ../hermes-config-<slug>
# do the work, commit, push
gh pr create
# when merged
cd <repo-root>
git worktree remove ../hermes-config-<slug>
```

The `Agent` tool in Claude Code accepts `isolation: "worktree"` for the same effect when
dispatching sub-agents.

## ⚠️ Sub-agent dispatch checklist

Before invoking the Agent/Task tool, **copy the PII rule from the root `AGENTS.md` into
your sub-agent prompt verbatim**. Sub-agents do not inherit this repo's context. The
failure mode is that sub-agents write detailed research with real paths, fleet member
names, and personal context, then save it to a public branch.

This is the most important checklist item in this doc. Skip it at your peril.

## When to dispatch a sub-agent vs do it inline

Use a sub-agent when:

- The work needs deep research (web, source-reading) that would flood the parent context
- Multiple independent pieces can run in parallel
- The work touches files the parent doesn't otherwise need in context

Do it inline when:

- The work needs the parent's accumulated context to make good decisions
- It's a small, focused edit
- You're already in flow on a related topic

## PR conventions

- Title: `<area>: <imperative>`
- Body: use `.github/pull_request_template.md` (auto-applied)
- Always cross-reference the issue it closes: `Closes #<n>`
- Always include `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` in commits
  when the AI did substantive work
- Push the branch, open the PR, let the review action run
- Use `/address-pr-comments <PR#>` in any session to triage the review

## Coordination rules

- **Don't dual-claim.** Comment first, then start.
- **Don't reach into another branch.** If your work needs something on another branch,
  wait for it to merge.
- **Don't merge for someone else.** The maintainer handles all merges to `main`.
- **Don't push to `main` directly.** All changes go via PR (the bootstrap was the one
  exception).
- **Don't force-push shared branches.** Force-push only your own branch before opening
  the PR.
- **Surface conflicts early.** If you discover that what you're doing contradicts an
  open PR, comment on both with the conflict and a proposed resolution.

## When the work is bigger than one PR

Decompose. If an issue needs >1 PR, close it with a new "tracking" issue that lists the
sub-issues. Each sub-issue is its own PR. The tracking issue closes when all children
close.

## Pre-commit checklist (run this before every push)

1. **PII scrub** — `git diff --cached | rg -i 'users/[a-z]+|wife|kids'` should be empty
2. **Prettier** — `npx prettier --write <files-you-touched>`
3. **Pre-commit hooks** — install once with `pre-commit install` so they run
   automatically

## When you finish

- Mark the task completed in your session's task list.
- Close the issue when the PR merges (`Closes #<n>` in the PR body handles this
  automatically).
- If your work produced follow-up ideas, open new issues for them. Don't pile additions
  into your own PR.
