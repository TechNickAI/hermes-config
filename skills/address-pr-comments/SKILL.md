---
name: address-pr-comments
description: >
  Use when a pull request has review comments from bots (Cursor Bugbot, Codex, Claude
  Code Review, Gemini Code Assist, Greptile, CodeRabbit) or humans to triage and
  resolve. Fixes the real defects the PR introduced, declines the rest with a one-line
  reason, answers every bot comment, and drives the PR to a converged, mergeable state.
version: 2.0.2
license: MIT
metadata:
  hermes:
    requires:
      - gh CLI, authenticated
    tags: [github, code-review, pull-requests, bots, triage, automation]
    related_skills: [pr-review-sweep, multi-review]
---

# Address PR Comments

## Overview

One or more review bots have analyzed this PR; humans may have too. Process that
feedback with the context the bots lack (the codebase, conventions, intent, the inputs
the code really receives): fix what is a real defect, push back on what is wrong, leave
a clean audit trail, and get the PR to merge.

The goal is simple, correct code that ships, not zero comments. The failure this skill
prevents is the loop: a bot raises an edge case, you add complexity for it, the next
round finds an edge case in the new code, and hours later the PR is a complex mess built
for things that never happen. Pushes usually re-trigger the configured reviewers, so an
agent pays for each commit with a review round that a human author would have spent
seconds waving off.

This skill is the per-PR engine. To _find_ PRs with unaddressed feedback, see
`pr-review-sweep`. For a proactive review before a PR exists, see `multi-review`.

## When to Use

- "Address the PR comments", "handle the bot feedback", or a PR number with review
  comments to resolve.
- You opened a PR and want its bot findings cleared before merge.
- A `pr-review-sweep` run handed you a PR number.

Not for: opening a PR or routine commits (`github-pr-workflow`), summarizing comments
without acting on them, or PRs you lack push access to. Merging is in scope only when
the user authorized it.

## Core Mandate

Fix every valid defect the PR introduced, whatever label the bot gave it. Defects the PR
did not introduce are tracked, not fixed here (step 4). Each bot comment ends in exactly
one state, with a reason:

1. **Fixed** — a real defect in the merged code, or a cheap improvement you are already
   pushing for. Implement the simplest version.
2. **Incorrect** — the bot's analysis is wrong given context it lacks. Reply why, 👎.
3. **WONTFIX** — correct but not worth it: an edge case the merged code cannot reach in
   normal operation whose fix adds complexity, a nit not worth a review round, or style
   the project doesn't want. 👎 and one line.
4. **Tracked** — a real defect this PR did not introduce (step 6). File it if it passes
   the filing gate and link it. 👍, not 👎: the bot was right.

"Defer" and "not a blocker" are not outcomes. Never decline a real defect in the merged
code because fixing it is inconvenient; decline an edge case only when you can say why
it cannot happen and its fix costs more complexity than the failure it prevents.

Human comments are not bot comments: never react on a human's behalf, decline, or
WONTFIX a human request (step 11).

## Setup

```bash
gh auth status   # in a sandbox with a rewritten $HOME, set GH_CONFIG_DIR to the user's real ~/.config/gh first
R=$(gh repo view --json nameWithOwner --jq .nameWithOwner)   # or owner/repo from the PR URL; watch for forks
BASE=$(gh pr view <N> --repo $R --json baseRefName --jq .baseRefName)
git fetch origin "$BASE"
```

Pass `--repo $R` on every `gh pr` command. An explicit PR number wins; only fall back to
`gh pr view --repo $R --json number` when checked out on the PR's branch. With neither,
stop.

Repo rules outrank bot labels. Read the repo's `AGENTS.md` / `CLAUDE.md` (a
`## Code Review Rules` section or similar), `REVIEW.md` and `CODING_STANDARDS.md` before
triaging: they define severity and what reviewers should not raise.

## Workflow

### 1. Preflight: will reviewers even run?

```bash
gh pr view <N> --repo $R --json mergeable,mergeStateStatus,isDraft,headRefOid,reviewDecision,statusCheckRollup
```

- **`CONFLICTING`/`DIRTY` suppresses `pull_request` workflows**, so pushes and re-review
  requests may start nothing. Merge the base branch in. If the conflict is
  architectural, or the base branch has since made the opposite design choice, flag it
  to the user instead of silently picking a side. If `git merge-base HEAD origin/$BASE`
  returns nothing, the branch has no shared history; report it.
- **Draft PRs often get no AI review.** Do not mark a draft ready unless the user asked
  to publish it; say it is a draft and stop.
- **A failing build often blocks downstream checks**; fix it first. `UNKNOWN` right
  after a push means GitHub is still computing; re-poll.
- **A red check may be infrastructure.** Read its log before treating it as a finding.
  Auth failures from an empty secret (often red on every open PR at once), runner OOM,
  rate limits, container-pull timeouts and bot actor-policy refusals are infra: rerun
  with `gh run rerun <run_id> --repo $R --failed`, note the cause on the PR, and surface
  a broken secret to the user. A review check that crashed may have posted partial
  findings, so still read what it posted. Signatures:
  `references/ci-infra-flake-patterns.md`.
- **CI that runs pre-commit `--all-files`** fails on files the PR never touched when the
  branch is behind; merging the base branch usually fixes it
  (`references/pre-commit-ci-all-files.md`).

### 2. Fetch every comment, in full

Feedback lives in three places. Keep ids, parent ids and timestamps; read full bodies
before triage.

```bash
BOT='(.user.type == "Bot") or (.user.login | endswith("[bot]"))'
# PR-level (issue) comments
gh api repos/$R/issues/<N>/comments --paginate \
  --jq ".[] | select($BOT) | {id, user: .user.login, created_at, reactions: .reactions.total_count, body}"
# Inline review comments
gh api repos/$R/pulls/<N>/comments --paginate \
  --jq ".[] | select($BOT) | {id, in_reply_to_id, user: .user.login, path, line: (.line // .original_line), commit_id, created_at, reactions: .reactions.total_count, body}"
# Review bodies (no reactions endpoint; findings can hide here)
gh api repos/$R/pulls/<N>/reviews --paginate \
  --jq ".[] | select($BOT) | {id, user: .user.login, state, commit_id, submitted_at, body}"
```

Run the same three queries with `select(($BOT) | not)` for human feedback, and read
`reviewDecision` from step 1. If you post through the user's own account, your replies
appear in the human results too. Skip only the comments you posted this session (keep
their ids); anything else from that account is the user's own feedback, so read it.

- **A 503 or error is never "no comments."** Retry, or fall back to GraphQL
  `reviewThreads` (`references/github-degraded-api-and-merge-verification.md`).
- Read each reviewer's state per head. Some post as a check (`claude-review`); some run
  once per PR rather than per push, so a green check can be the OLD head's verdict; read
  the workflow trigger and count only a verdict created after your last push. If Codex
  posts a summary carrying `codex-review-state` JSON, its `commit_sha` must equal the
  current head. Cursor Bugbot `NEUTRAL` can hide findings, a cancelled run or an error;
  `skipping` means it did not review that push.
- **A clean summary on the newest head does not close older threads.** Incremental
  reviewers review only the new diff and don't re-post open findings, so an unanswered
  finding from round one stays open under a later "no issues found."
- Your own threaded reply can create a `COMMENTED` review record; check the author
  before treating a new review id as findings.
- A bot comment is closed only when you (or the PR author) replied with its outcome and
  reacted. A reply from the bot itself or another reviewer doesn't close it.

### 3. Separate stale findings from live ones

GitHub re-anchors old inline comments onto the new head, and Bugbot may re-post the same
`BUGBOT_BUG_ID` at the fix commit's new lines. Neither is a new finding. Find what is
new by unseen ids (or `created_at` after your last push, `in_reply_to_id` null), then
apply the only real test: **does the current code still exhibit the described defect?**

- Already fixed: reply citing the fix SHA; don't re-fix.
- About a mechanism a later commit removed: prove it with `git grep <symbol> <head-sha>`
  and reply citing the removing SHA. When you replace a mechanism mid-PR, update the PR
  body and the component docs in the same push; stale prose draws these findings.

### 4. Tag each finding Introduced or Pre-existing

Re-review rounds mostly surface old code the reviewer did not reach last pass. Fixing
those in the PR turns a converging PR into an endless one, so tag first:

- **Introduced** if any of these hold: the PR wrote or changed the cited lines (a
  verbatim move does not count: check with `git diff --color-moved` or
  `git blame -M -C`, which are heuristics); the PR changed a caller, contract, config or
  invariant that makes old code wrong or newly reachable; or the cited code is what the
  PR exists to change (its title, body or the user's ask).
- **Pre-existing** only when the defect exists unchanged on the base branch and the PR
  neither touches nor depends on it. `git blame` (with `-M -C` to follow moves, which is
  heuristic) shows line history; confirm by reading the code on `origin/$BASE`.

Pre-existing goes to the filing gate (step 6) and is not fixed here. Say so in the
reply: "Predates this PR (present on `<base>` at `<sha>`); tracked in #n."

### 5. Triage Introduced findings

**Is it a real defect in the merged code?** Judge against the inputs and configuration
the merged code is built to handle, not against what production has seen so far (new
code has never run). A reproducible failure on an ordinary sequence is a defect.

The burden is on the decline. If you can construct an ordinary sequence that triggers
it, it is a defect. Decline only with one line naming the concrete reason the case can't
arise, and only for cases like these, unless the fix is trivial and leaves the code no
harder to read:

- a same-instant or narrow-window interleaving that the merged code's invariants rule
  out (name the invariant: the serializing queue, transaction or single writer);
- two or more independent failures at once;
- volume or data shapes the code is not built for;
- absurd values in a hand-edited config, or validating the code's own outputs.

A plausible race or check-then-act gap on a path the PR adds is a defect: fix it.
Complexity looks like locks, revision counters, retry loops, re-read-and-compare, or new
state added only to close a theoretical window. A new config knob tends to draw a chain
of boundary variants (rounding, overflow, collisions): judge each on reachability, and
once you have declined one as unreachable, answer its later variants by citing that
reply instead of starting a fix chain.

**Then decide whether it earns a push.** A defect earns one, whatever the bot labelled
it. A nit (naming, wording, style, polish, an optional refactor) gets fixed only when
you are already pushing for a defect and the change is cheap; otherwise WONTFIX with
"nit, not worth a review round." **Never push a commit whose only content is nits.** A
missing test for behaviour the PR adds is not a nit; it earns a push.

**Fix the class, not the line.** A valid finding is usually one instance of a pattern.
Find its siblings in the code this PR adds or changes, and fix them in the same push:

- new state (a latch, flag, kill switch) some caller ignores: grep every entry point
  that acts, give each the check and its own test, and confirm the detector that sets
  the state is reachable on that path;
- one counter-example to a classifier: scan the real population the code sees and fix
  every value that slips the same way, with a positive control that must still pass;
- one hot-path walk: switch every site in that pass;
- copied code with one inherited constant flagged: check the copy's other constants.

Classify each sibling with step 4: one the PR's change broke is Introduced and gets
fixed; one that merely shares the pattern is Pre-existing, listed in the reply and the
report and sent through the filing gate.

**Watch for the spiral.** A finding in code you added to answer an earlier finding is a
cue to stop adding mechanism. Fix the original defect the simplest way, and revert or
simplify your own hardening rather than hardening it further, but never remove a control
the finding itself is about. Patterns that break it (single-writer redesign, shipping an
unrequested sub-feature off by default, fallbacks that already fail loudly):
`references/breaking-the-spiral.md`.

**Classify before you code, every round.** For "a path can reach X" findings, write the
failing test first. If no realistic input fails, drop the test and decline with that
reasoning; that cost one local run, not a review round.

**Decline as Incorrect** when you can say why: a single-use value flagged as a magic
string; a race on operations a queue, transaction or event loop already serializes (an
`await` between read and write breaks that); a null check the types already guarantee;
an optimization with no profile. Grep the repo's rules for the topic first; a finding
that contradicts a written repo rule is answered by citing it.

**Verify factual API claims live.** When a bot asserts what an external API accepts or
returns, probe it both ways and paste the result. HTTP 200 does not mean a field was
honored; confirm the effect. Correct a stale doc in the same PR, scoped to the endpoint
you probed.

**When the bot is right that new code lacks a test, write it.** Drive the real function
on a small fixture (fresh temp dirs, the empty case included). If a fix breaks an
existing test that pinned the old buggy behaviour, update and rename that test; don't
delete it.

### 6. The filing gate (Pre-existing and out-of-scope findings)

File an issue only when all five hold; otherwise reply "Not filing: <reason>":

1. **Normal operation:** reachable without a crash window, compound failure, or volume
   the code isn't built for.
2. **Reachable:** you can name the evidence (log, row, CI run) or an ordinary sequence
   that reaches it.
3. **Behaviour, not test plumbing:** "add coverage for X" with no defect in X is not an
   issue.
4. **Live code:** on the base branch and enabled, not a retired path.
5. **Worth the complexity:** the fix would not make the code harder to read.

Search open issues first and comment on a duplicate instead. One defect per issue,
titled by the user-visible symptom (never "review follow-ups on #N"), with the sequence,
evidence at `file:line` on the base SHA, impact, simplest fix and labels. Omnibus
follow-up issues can't be closed and rot. If the repo has issues disabled, tell the user
where it should be tracked; a Tracked item with nowhere tracked is a silent decline.

The PR that fixes an issue names each one `Fixes #<n>` (one keyword per issue;
`Closes #a, #b` closes only `#a`, and only on a merge into the default branch). Read the
issue state back after merge.

### 7. Push once per round

- **Wait for every configured reviewer and CI to land on the current head**, fix all
  real findings in local commits, then push once. Pushing per finding or per reviewer
  re-runs the reviewers each time. Poll CI in a separate step from the push.
- **Run the focused tests before committing and read the pass line**: zero tests
  collected is not a pass. Run the repo's formatter on the files you changed only, and
  check `git diff --stat` for unrelated churn and formatter-mangled conflict markers
  (`> > > > > > >`).
- **Prove each new test guards the fix:** copy the file aside, break the changed line,
  run the one test, copy it back and `cmp`. Never use `git checkout -- <file>` for this;
  it discards the uncommitted fix.
- **Gate the push on the commit's exit status** (`git commit ... && git push`, or check
  it explicitly). A hook-rejected commit leaves HEAD on the old commit; a push joined
  with `;` ships old code under your "fixed" reply.
- **Confirm the PR's `headRefOid` equals local `HEAD`** after the push. Reviewers review
  whatever GitHub thinks the head is.
- **Update the PR body when the approach changed**:
  `gh api -X PATCH repos/$R/pulls/<N> -F body=@<file>`, then grep the returned body.
  `gh pr edit --body-file` can fail without changing anything.

### 8. Reply and react, after the push lands

Post replies citing a fix SHA only after step 7 confirmed that SHA is the PR head. Never
pre-write replies into a script that pushes; trace each claim in the code first.

```bash
gh api -X POST repos/$R/pulls/<N>/comments/<top_level_id>/replies -f body="..."  # inline thread
gh api repos/$R/pulls/comments/<comment_id>/reactions -f content="+1"            # inline
gh api repos/$R/issues/comments/<comment_id>/reactions -f content="+1"           # PR-level
gh pr comment <N> --repo $R --body "Re review <review_id>: <outcome>"            # review body
```

- One reaction per inline or PR-level bot comment (review bodies get the PR comment
  above instead): 👍 addressed or Tracked, ❤️ subtle catch, 🚀 critical defect fixed, 👎
  Incorrect or WONTFIX. Reactions are feedback signals some bots use.
- **Target every reply and reaction by comment id**, never by matching body text: bot
  bodies start with badge HTML, so a text match silently hits nothing. Inline replies go
  to the thread's top-level comment.
- Fixed: cite the SHA. Declined: one plain line of why. Introduced/Pre-existing goes in
  the reply when it decided the outcome.

### 9. Fix repeated false positives at the source

When you decline the same class of finding as **Incorrect** twice on a repo, add it to
that repo's "do not raise" list (`## Code Review Rules`, `REVIEW.md`,
`CODING_STANDARDS.md`), in its own follow-up PR (that is not a nit push on this PR)
unless this PR already edits those rules. If the mistake is mechanical, add a lint rule
or hook instead. A 👎 per comment trains nothing on our side. Convenience declines
(nits, WONTFIX) never become rules.

### 10. Stall detection

A reviewer `queued`/`in_progress` for ~5+ minutes with no output gets investigated:
conflicts, a failing build blocking it, runner queue, rate limits, a draft PR, or a
once-per-PR trigger. Report it plainly; never idle silently. If round 3+ still surfaces
new Introduced defects (not Pre-existing, not your own hardening), tell the user and
name the pattern: the approach may be wrong.

### 11. Human comments

Surface them separately for the user, or implement them when they are clear requests
within the PR's scope. Don't react on a human's behalf, and never decline or WONTFIX a
human request.

### 12. Final sweep

After the last push, once checks settle:

- every top-level bot comment, from every round, has your reply where useful and your
  reaction (`reactions.total_count` of 0 is a miss);
- replying does not resolve a GitHub thread: if branch protection requires resolved
  conversations, resolve the threads you closed (GraphQL `resolveReviewThread`);
- every review body was read and answered;
- every reviewer reported on the current head;
- no human `CHANGES_REQUESTED` stands and no human request is unanswered;
- tests re-ran after your last edit.

A valid new Introduced defect sends you back to step 5. Then the PR is ready.

## Merging (only when authorized)

- **Author check first.** "Merge what's ready" covers the user's PRs, PRs you opened for
  them, and dependency bots if they said so. A PR from anyone else is outside the trust
  boundary: post findings and hand the user the decision.
- Merge **pinned to the reviewed head**, using the repo's allowed method:
  `gh pr merge <N> --repo $R --squash --match-head-commit <full-40-char-sha>` (swap
  `--squash` for `--merge`/`--rebase` if that is what the repo allows).
- Read back `gh pr view <N> --repo $R --json state,mergeCommit`. Queued is not merged; a
  non-zero exit can still have merged (branch deletion failed); a 409 "head branch was
  modified" right after a push or label change is often transient, so re-read the head
  and retry the same pinned merge. Then `git fetch origin $BASE` and confirm with
  `git merge-base --is-ancestor <merge-commit> origin/$BASE`.
- Comments that land after merge, and batches of PRs:
  `references/multi-pr-and-post-merge.md`.

## Completion Report

```
## Bot Feedback Addressed
**PR:** #<N> — <title>   (<url>)
**Rounds:** <n>   **Fixed:** <n>   **Declined:** <n> (<incorrect> incorrect, <wontfix> wontfix)
**Introduced / Pre-existing:** <i> / <p>   **Issues filed:** <n>   **Siblings left:** <list or none>
**Reviewers on head <sha>:** <per reviewer>
<key fixes, notable declines, any "do not raise" rule added>
<human comments still needing the user>
```

Say plainly whether the PR is mergeable, and whether you merged it.

## Common Pitfalls

1. **Fixing Pre-existing findings in the PR**, the main reason review loops don't
   converge. Equally wrong: calling the PR's own change Pre-existing.
2. **Treating the bot's P2 as a nit.** Labels don't decide; defect vs nit does.
3. **A nit-only push.** It buys a review round for nothing.
4. **Fixing the cited line and leaving its siblings.** The next round names the twin.
5. **Hardening your own hardening.**
6. **Trusting a green check or a clean latest summary** while older threads or review
   bodies are unanswered.
7. **Replying "fixed in <sha>" before that SHA is the PR head.**
8. **Replying by text match** instead of comment id.

## Verification Checklist

- [ ] Preflight read: mergeable, draft state, infra-only red checks recognized.
- [ ] Full comment bodies read from issues, pulls and reviews endpoints, plus each
      reviewer's state on the current head; API errors never read as "no comments".
- [ ] Each finding tagged Introduced / Pre-existing; the PR's own purpose never tagged
      Pre-existing.
- [ ] Every valid Introduced defect fixed with its siblings in the change; leftovers
      listed; Tracked items passed the filing gate.
- [ ] No nit-only push; one push per round; commit gated; PR head verified before
      replying.
- [ ] Every bot comment has a reaction and, where useful, a reply by id; review bodies
      answered.
- [ ] A false positive declined as Incorrect twice became a repo rule or check.
- [ ] Humans surfaced and never declined; no standing `CHANGES_REQUESTED`.
- [ ] Report sent; merge pinned and verified on the base branch if authorized.
