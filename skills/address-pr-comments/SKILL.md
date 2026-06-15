---
name: address-pr-comments
description: >
  Use when a pull request has feedback from code-review bots (Cursor Bugbot, Codex,
  Claude Code Review, Greptile, CodeRabbit) or humans and you need to triage it, fix
  what is valid, push back on what is wrong, react and reply to every comment, and drive
  the PR to a clean, mergeable state. Trigger phrases: "address the PR comments",
  "handle the bot feedback", "get the review comments addressed".
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [github, code-review, pull-requests, bots, triage, automation]
    related_skills: [pr-review-sweep, multi-review]
---

# Address PR Comments

## Overview

You are the last line of defense before code ships. One or more review bots have
analyzed this PR; humans may have too. Your job is to process that feedback
intelligently: fix what genuinely improves the code, push back on what is wrong, and
leave a clean audit trail so the PR is ready to merge.

You have context the bots lack — the full codebase, project conventions, and
architectural intent. Use that advantage. A bot can be confidently wrong; verify each
claim against the actual source before acting on it.

This skill is the per-PR engine. To _find_ PRs with unaddressed feedback across an org
or a date range, see `pr-review-sweep`, which dispatches this workflow per PR. For a
proactive multi-lens review of an artifact before it becomes a PR, see `multi-review`.

## When to Use

- The user says "address the PR comments", "handle the bot feedback", "get the bots
  happy", "process the review on PR #N", or names a PR that has review comments.
- You opened a PR and want to clear bot findings before asking for merge.
- A `pr-review-sweep` run handed you a specific PR number to work.

Do **not** use for:

- Opening a PR or routine commit/push (that is `github-pr-workflow` territory).
- Merging — this skill drives a PR to _ready_, it does not merge. Merge stays behind the
  user's explicit approval unless they said otherwise.
- Resolving feedback on someone else's PR you lack push access to.

## Core Mandate

Fix every valid issue — not "triage what's blocking." Each comment ends in exactly one
of four states:

1. **Fixed** — the suggestion improves the code, so you implemented it.
2. **Incorrect** — the bot's analysis is wrong given context it lacks; explain why and
   react 👎.
3. **WONTFIX** — technically correct but explicitly unwanted for this project (e.g.
   "ARIA is out of scope here"); decline with 👎 and a one-line reason.
4. **Tracked** — valid but scope exceeds this PR; create a GitHub issue and link it.

"Defer" and "not a blocker" are not outcomes. If a change would genuinely improve the
code and we want it, make it now. The goal is code quality, not just clearing a gate.

## Authentication & Environment

This repo's review checks run on GitHub. Drive everything through the `gh` CLI.

- Confirm auth first: `gh auth status`. If it fails inside a sandboxed/profile shell
  whose `$HOME` is rewritten, point `gh` at its real config dir for the session, e.g.
  `export GH_CONFIG_DIR="$REAL_HOME/.config/gh"` (substitute the operator's real home),
  or prefix the command: `HOME="$REAL_HOME" gh …`. Verify with `gh auth status` again.
- Resolve owner/repo from the remote when you need it raw:
  ```bash
  REMOTE_URL=$(git remote get-url origin)
  R=$(echo "$REMOTE_URL" | sed -E 's#.*github\.com[:/]##; s#\.git$##')   # owner/repo
  ```

## Workflow

### 1. Detect the PR

If the user gave a PR number, use it directly as `<N>` in every command below — do not
re-derive it from the branch. Only fall back to branch detection when no number was
given:

```bash
# Explicit number wins:
N=<pr-number-from-user>

# Otherwise auto-detect from the current branch:
N=$(gh pr view --json number --jq '.number')
```

`gh pr view` with no argument resolves the PR for the _current branch_, so it only works
when you're checked out on the PR's branch. If the user said "process PR #123" while you
sit on `main`, using branch detection would target the wrong PR (or none). When you have
a number, pass `gh pr view <N> --repo $R`. If neither a number nor a branch PR is
available, say so and stop — don't guess.

### 2. Preflight: is the PR even runnable?

Bots won't post (or their checks stay stuck) when the PR can't build or merge. Check
before you wait on feedback that will never arrive:

```bash
gh pr view <N> --repo $R --json mergeable,mergeStateStatus,statusCheckRollup \
  --jq '{mergeable, state: .mergeStateStatus,
         checks: [(.statusCheckRollup // [])[] | {name, status, conclusion}]}'
```

- **Merge conflicts** (`mergeable: CONFLICTING`) block bot checks — resolve first
  (rebase/merge per project convention), push, then bots re-run. Flag architectural
  conflicts for the user instead of auto-resolving.
- **Failing build / required check** often blocks downstream checks — fix the build
  before chasing individual comments.
- **`UNKNOWN`** usually means GitHub is still computing mergeability right after a push;
  re-poll in a few seconds.

### 3. Fetch comments from BOTH endpoints

Review feedback lives at two different API levels. Miss one and you miss half the
findings. Fetch bot and human feedback separately so you never react on a human's behalf
(step 10) and never declare "no actionable feedback" while a maintainer's top-level
comment sits unread.

```bash
# --- BOT feedback ---

# PR-level / issue comments from bots (Claude Code Review, some Greptile/CodeRabbit)
gh api repos/$R/issues/<N>/comments --paginate \
  --jq '.[] | select(.user.login | endswith("[bot]")) | {id, user: .user.login, created_at, body}'

# Line-level / inline review comments from bots (Cursor, Codex, Greptile inline)
gh api repos/$R/pulls/<N>/comments --paginate \
  --jq '.[] | select(.user.login | endswith("[bot]"))
            | "\n=== \(.path):\(.line // .original_line) — \(.user.login) [id \(.id)] ===\n\(.body)"'

# Bot review summaries (Cursor/Codex headers, verdict bodies). --paginate: the reviews
# endpoint defaults to 30 per page and is chronological, so without it you can read the
# OLDEST page on a busy PR and miss the latest verdict.
gh api repos/$R/pulls/<N>/reviews --paginate \
  --jq '[.[] | select(.user.login | endswith("[bot]"))]
            | sort_by(.submitted_at) | reverse
            | .[] | "--- \(.user.login) [\(.state)] \(.submitted_at) ---\n\(.body[0:600])"'

# --- HUMAN feedback (surface separately; do NOT auto-react or auto-decline — step 10) ---

# Human top-level PR conversation comments
gh api repos/$R/issues/<N>/comments --paginate \
  --jq '.[] | select((.user.login | endswith("[bot]")) | not) | "\(.user.login): \(.body[0:300])"'

# Human inline + review-body feedback
gh api repos/$R/pulls/<N>/comments --paginate \
  --jq '.[] | select((.user.login | endswith("[bot]")) | not) | "\(.path):\(.line // .original_line) \(.user.login): \(.body[0:300])"'
gh api repos/$R/pulls/<N>/reviews --paginate \
  --jq '.[] | select((.user.login | endswith("[bot]")) | not) | select(.body != "") | "\(.user.login) [\(.state)]: \(.body[0:300])"'
```

Notes:

- **`claude-review` posts as a CHECK, not a comment** — its pass/fail shows in
  `gh pr checks <N>`, not in the comment endpoints. Read it there.
- **Only act on the most recent Claude/PR-level bot review.** Older ones reflect
  outdated code — that is why the review-summary query sorts by `submitted_at` and
  reverses, so the newest verdict is first; ignore superseded earlier ones.
- Process any login ending in `[bot]` — don't hardcode an allowlist; new reviewers
  appear. Known set: `cursor[bot]`, `chatgpt-codex-connector[bot]`, `claude[bot]`,
  `greptile[bot]`, `coderabbitai[bot]`.
- The human queries above are for triage/surfacing only. Reactions, replies, and
  auto-decline (steps 6–7) apply to **bot** comments; human feedback goes to the user
  per step 10.

### 4. Separate stale-anchored findings from live ones

After any prior fix push, a bot's inline comment stays anchored to the line numbers of
the commit it reviewed — it is **not** necessarily a re-finding. Trust the latest
_check-run_ status over lingering inline text. To isolate findings actually tied to the
current head:

```bash
HEAD=$(gh pr view <N> --repo $R --json headRefOid --jq '.headRefOid')
# gh's --jq does NOT forward jq's --arg; pipe to a real jq with --arg instead.
gh api repos/$R/pulls/<N>/comments --paginate \
  | jq -r --arg h "$HEAD" 'map(select(.commit_id == $h or .original_commit_id == $h))
                           | .[] | "\(.path):\(.line // .original_line) — \(.body[0:140])"'
```

Read each finding's _description_ against the current file state. If your fix already
changed that code, the comment is stale-anchored — reply that it's resolved (cite the
fix SHA) rather than "re-fixing." Cursor embeds `<!-- BUGBOT_BUG_ID: <uuid> -->`; the
same uuid reappearing means the same finding, not a new one.

### 5. Triage each comment

Ask: **"Is this suggestion correct given context the bot lacks?"** Verify against the
real code before agreeing _or_ pushing back.

**Fix it** when the analysis is correct:

- Bug, security issue, logic error, or a genuine improvement. For a security/production
  bug, react 🚀 (critical) or ❤️ (subtle catch) and fix immediately.
- When the bot diagnoses the right problem but proposes a clumsy fix, solve the
  underlying issue the clean way and credit the diagnosis.

**Decline as Incorrect** when you can articulate why the bot is wrong. Common
false-positive classes (each is a _decline-with-explanation_, not a skip):

- **Single-use values** flagged as "magic strings." Extracting a constant used exactly
  once adds indirection without a DRY benefit. Constants exist to stay DRY across
  multiple uses.
- **Theoretical race conditions** where operations are already serialized by a queue,
  mutex, transaction, or single-threaded event loop the bot can't see. (A real example:
  a bot flagged a "concurrent map overwrite" in a get-or-create block that had no
  `await` between the read and the write — atomic under a single-threaded runtime.)
- **Redundant type/null checks** already guaranteed by the type system or handled by
  runtime validation at another layer.
- **Premature optimization** with no profiling data showing a real problem.

**Decline as WONTFIX** when correct but explicitly unwanted: accessibility when it isn't
a project priority, i18n in an English-only tool, micro-optimizations on cold paths,
style that conflicts with project convention. Check project config/conventions for the
team's stance; if none is declared and the call is non-trivial, ask the user rather than
guessing.

**Create a GitHub issue** when valid but out of scope — e.g. the fix would touch a
shared utility used by ten other files. Open it, link it in your reply, keep the PR
focused:

```bash
gh issue create --repo $R --title "<concise>" --body "<context + link to PR #N>"
```

Never decline merely because fixing is inconvenient.

### 6. React to every comment — and use the right endpoint

Every bot comment gets exactly one reaction. Reactions are training signals:

- 👍 `+1` — helpful, addressed. "More like this."
- ❤️ `heart` — exceptional catch (subtle bug, real security issue).
- 🚀 `rocket` — critical security/production bug you fixed.
- 👎 `-1` — incorrect/irrelevant/wrong analysis. "Less like this."

**The endpoint depends on the comment level — mixing them 404s:**

```bash
# Line-level (inline) comment — lives on the PULLS endpoint
gh api repos/$R/pulls/comments/<comment_id>/reactions -f content="rocket"

# PR-level (issue) comment — lives on the ISSUES endpoint
gh api repos/$R/issues/comments/<comment_id>/reactions -f content="+1"
```

Valid contents: `+1`, `-1`, `heart`, `rocket`, `laugh`, `hooray`, `confused`, `eyes`.

### 7. Reply where it adds training value

A reply is most valuable when it explains _why_ a suggestion is wrong — that is the
training data that improves bots over time. Keep it brief; the reaction is the primary
signal.

```bash
# Threaded reply to a line-level comment (note in_reply_to)
gh api repos/$R/pulls/<N>/comments -f body="Declining: this value appears exactly once; \
extracting a constant adds indirection without a DRY benefit." -F in_reply_to=<comment_id>
```

- When fixing, the commit speaks for itself — mention the fix SHA for traceability.
- For an exceptional catch, a short "Great catch — fixed in `<sha>`" plus ❤️ is welcome.
- Pure pleasantries ("Thanks for the review!") add less than a reaction alone — skip
  them.

### 8. Push fixes, then re-poll (don't poll-and-sleep)

Work while bots run. Process whatever comments are available from fast reviewers (Claude
Code Review usually finishes first), commit, push, then check the slower ones (Cursor,
Codex, Greptile). After a push, bots re-analyze:

- Skip comment IDs you've already processed; only handle genuinely new feedback.
- Group related fixes into a coherent commit with a conventional message.
- Re-run the preflight (step 2) and check-status loop after each push.

```bash
gh pr checks <N> --repo $R          # one-shot status
# or watch until checks settle:
gh pr checks <N> --repo $R --watch
```

If you've pushed 3+ times and bots keep surfacing genuinely new issues, stop and flag it
— something systematic is off, and the user should weigh in.

### 9. Stall detection

If a specific bot stays `queued`/`in_progress` for more than ~5 minutes with no output,
investigate rather than wait silently. Usual causes: unresolved merge conflict, a build
failure blocking downstream checks, CI runner queue depth, or rate limiting. Report it
plainly: "Cursor has been queued 8 min; the build is failing on type-check, which is
likely blocking it — fixing the build first." Never idle silently; if you have nothing
actionable, say so and say why.

### 10. Human comments are not yours to auto-resolve

Surface human-reviewer comments separately and flag them for the user. Don't react on
the user's behalf or auto-decline a human's request.

## Completion Report

When all bots have settled and no actionable feedback remains, report:

```
## Bot Feedback Addressed
**PR:** #<N> — <title>   (<url>)
**Fixed:** <n>    **Declined:** <n> (<incorrect> incorrect, <wontfix> wontfix)
**Issues created:** <n>
**Checks:** <pre-commit/Cursor/claude-review status>
<one-line summary of key fixes and notable declines>
<any human comments still needing attention>
```

Before declaring done, verify **every** bot comment got a reaction — go back and add any
you missed. State explicitly whether the PR is now clean and mergeable, then hand the
merge decision back to the user.

## Common Pitfalls

1. **Only fetching one comment endpoint.** Inline findings (pulls) and PR-level verdicts
   (issues) are separate APIs; `gh pr view --comments` misses inline ones entirely.
   Always query both, plus `gh pr checks` for check-only reviewers like `claude-review`.
2. **Reacting/replying on the wrong endpoint.** Line-level comment reactions go to
   `pulls/comments/<id>`, PR-level to `issues/comments/<id>`. Cross them and you get
   a 404.
3. **Treating stale-anchored inline comments as new findings.** After a fix push, old
   comments linger at their original line numbers. Check `commit_id` against the head
   SHA and read the description against current code before "re-fixing."
4. **Rubber-stamping the bot.** Bots are confidently wrong often enough that you must
   verify each claim against the source. Equally: don't dismiss a real bug just because
   the proposed fix is awkward — fix the underlying issue.
5. **Merging.** This skill ends at _ready to merge_. Don't merge unless the user
   explicitly authorized it.
6. **Poll-and-sleep.** Don't block on the slowest bot. Process available comments, push,
   and re-poll. Investigate stalls instead of waiting silently.
7. **`gh` auth failing in a rewritten-`$HOME` shell.** Point `GH_CONFIG_DIR` at the real
   config dir or prefix `HOME=<real-home>`; never assume the bare command will find
   auth.
8. **Forgetting a reaction.** Every bot comment gets exactly one. Audit before declaring
   complete.

## Verification Checklist

- [ ] PR detected (explicit number or branch auto-detect); stopped cleanly if none.
- [ ] Preflight done: mergeable + check status read before waiting on bots.
- [ ] Comments fetched from issues endpoint, pulls endpoint, AND `gh pr checks`.
- [ ] Each comment triaged → Fixed / Incorrect / WONTFIX / Tracked.
- [ ] Every bot comment has exactly one reaction, on the correct endpoint.
- [ ] Declines have a one-line "why the bot is wrong / why unwanted" reply.
- [ ] Fixes pushed; bots re-polled until checks settle; stalls investigated, not slept
      on.
- [ ] Human comments surfaced separately for the user.
- [ ] Completion report with Fixed/Declined/Tracked counts and merge-readiness; merge
      left to the user.
