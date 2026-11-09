# Multiple PRs, and comments that land after merge

## Batches of PRs

For several PRs at once, possibly across repositories:

1. Fetch every PR's comments and its real head branch
   (`gh pr view <N> --repo <R> --json headRefName`) in parallel before cloning. Never
   trust a branch name the user typed.
2. Give each PR its own clone, so branches and indexes never collide.
3. Work PRs one at a time but overlap CI waits: push one, triage the next, come back
   when its checks land.
4. Keep a ledger (`multi-pr-review-ledger.md`) and update it after every commit and
   push.
5. After merging one PR, re-check `mergeable` on every other open PR in the same
   repository; a merge can flip a sibling to `CONFLICTING`.
6. Report per PR: fixed, declined, merged or not.

### Delegating to subagents

When the batch is large enough to delegate:

- One full independent clone per subagent. Subagents that share your working tree share
  your index, and two of them committing at once corrupt each other's work.
- Hand each subagent the exact comment text (id, `file:line`, the finding) instead of
  making it re-fetch from GitHub.
- Subagents fix and push; they do not merge. Verify their pushed heads yourself before
  replying or merging.
- Record each clone path in the ledger and remove it, from a different working
  directory, only after its work is verified on the remote.

## Comments that land after merge

The PR can't take a push. Find the unanswered ones: top-level bot comments with no reply
from the PR author or from you (read `in_reply_to_id` defensively, since top-level
comments omit it). A threaded reply from the bot itself or another reviewer doesn't
answer a finding. Then:

1. Branch a follow-up from the current base branch, fix, test, and open a PR.
2. Merge it only if merging was authorized for this work.
3. Reply on the ORIGINAL PR's threads citing the follow-up PR and SHA, and add the
   reaction.
4. Report totals across both PRs.
