---
name: pr-review-sweep
description:
  Nightly sweep of recently-merged PRs to address unhandled review comments via a Claude
  Code sub-agent.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [github, code-review, automation, claude-code, cron]
    related_skills: [claude-code, github-pr-workflow]
---

# PR Review Sweep

Walks the last N days of merged PRs across configured GitHub orgs, finds review comments
that were never addressed (no PR-author reply, no reactions), and dispatches a Claude
Code sub-agent to fix them in a follow-up PR.

Designed to run nightly as a cron job. Reports a summary to the home channel at the end.

## When to load

- Cron tick named `pr-review-sweep`
- Manual ask: "sweep recent PRs for unanswered review comments"
- Investigating whether a specific PR has unhandled feedback

## Configuration

Edit these knobs at the top of your cron-job prompt (or pass via env):

| Knob               | Default                       | Notes                                      |
| ------------------ | ----------------------------- | ------------------------------------------ |
| `LOOKBACK_DAYS`    | `7`                           | How far back to scan merged PRs            |
| `ORGS`             | `<your-orgs-comma-separated>` | Passed to `gh search prs --owner`          |
| `MAX_PRS_PER_RUN`  | `10`                          | Cost ceiling — bump after a few clean runs |
| `TIMEOUT_PER_PR_S` | `1200`                        | Claude Code wall-clock per PR              |
| `EXCLUDE_REPOS`    | `[]`                          | Hard-skip list (archived/abandoned)        |
| `FOLLOWUP_LABELS`  | `["review-sweep"]`            | Labels to add to the follow-up PR          |
| `ADMIN_TARGET`     | home channel                  | Where the summary report goes              |

## Prerequisites

- `gh` CLI authenticated (`gh auth status` must pass)
- Claude Code CLI installed and an ai-coding-config plugin with an `address-pr-comments`
  skill (the agent your sub-process actually runs)
- `terminal` and `delegation` toolsets enabled
- Workspace provisioning script (e.g. `workshop {repo} {branch}` or
  `gh repo clone ... ; cd ...`)

## How it thinks

1.  **Discover merged PRs in the window:**

    ```bash
    gh search prs \
      --owner <org1> --owner <org2> \
      --state closed --merged \
      --merged-at ">$(date -d '${LOOKBACK_DAYS} days ago' +%Y-%m-%d)" \
      --json number,title,repository,author,headRepository \
      --limit 50
    ```

2.  **For each PR, fetch comments from both endpoints and filter out anything the PR
    author has already engaged with:**

    ```bash
    # Line-level review comments. Three filters:
    #   1. Not authored by the PR author
    #   2. Top-level (no in_reply_to_id) — replies are handled via their parent
    #   3. Zero reactions
    # Then second pass: drop any top-level comment that has a child reply from the
    # PR author (they already responded inline).
    gh api repos/{owner}/{repo}/pulls/{pr}/comments > /tmp/line-comments.json

    AUTHOR="{author}"
    jq --arg author "$AUTHOR" '
      # Collect IDs of parent comments that the author has replied to
      (map(select(.user.login == $author and .in_reply_to_id != null) | .in_reply_to_id) | unique) as $replied_to
      | map(select(
          .user.login != $author
          and .in_reply_to_id == null
          and ((.reactions.total_count // 0) == 0)
          and (.id as $id | $replied_to | index($id) | not)
        ))
    ' /tmp/line-comments.json

    # Issue-level (general PR) comments. These are flat (no threading), so use a
    # timestamp heuristic: drop any comment if the PR author posted ANY issue comment
    # AFTER it. That's a reasonable proxy for "the author saw it and moved on."
    gh api repos/{owner}/{repo}/issues/{pr}/comments > /tmp/issue-comments.json

    jq --arg author "$AUTHOR" '
      # Latest timestamp of any author issue comment (null if none)
      (map(select(.user.login == $author) | .created_at) | sort | last // "0") as $author_last
      | map(select(
          .user.login != $author
          and ((.reactions.total_count // 0) == 0)
          and (.created_at > $author_last)
        ))
    ' /tmp/issue-comments.json
    ```

    "Unhandled" = not from PR author, zero reactions, AND no later author engagement
    (inline reply for line comments, later issue comment for issue comments). No bot
    allowlist — catches bot reviewers (CodeRabbit, claude-review) and humans alike.

3.  **Filter:**
    - Skip dependabot/renovate PRs unless they have non-bot review comments
    - Skip any repo in `EXCLUDE_REPOS`
    - Stop after `MAX_PRS_PER_RUN`

4.  **For each remaining PR (serial, not parallel):**
    - Provision a fresh workshop: `workshop {repo} pr-sweep-{pr}` (or `git clone` +
      `git checkout -b pr-sweep-{pr}`)
    - Spawn Claude Code in ACP mode:

           ```python
           # Pseudocode — adapt to your ACP wrapper
           run_claude_code(
               agent="claude",
               mode="run",
               runtime="acp",
               cwd=workshop_path,
               timeout_s=TIMEOUT_PER_PR_S,
               task=f"""

      Use the Skill tool to invoke the address-pr-comments skill with argument
      {pr_number}.

This skill is provided by the ai-coding-config plugin (globally installed). It knows the
project's triage conventions and review standards. Do NOT manually triage — you MUST use
the Skill tool to load and execute the address-pr-comments skill.

If the skill creates fixes, they go in a new follow-up PR with labels:
{FOLLOWUP_LABELS}.

After creating any follow-up PR, use the Skill tool to invoke address-pr-comments on the
NEW PR number too (cascading sweep).

Do NOT merge anything. Leave merge for human review. """, ) ```

- **Verify completion:** When the sub-process returns, scan its output. If it did NOT
  use the Skill tool for `address-pr-comments`, send it back with:

  > "You did not use the Skill tool. Invoke the address-pr-comments skill now using the
  > Skill tool."

  Keep doing this until the skill is actually invoked. (Without this, Claude Code in ACP
  mode will silently freelance instead of running the skill — `/slash` prefixes are NOT
  auto-intercepted in ACP/--print mode.)

5. **Cleanup** workshop directory after each PR is done.

6. **Report summary** to `ADMIN_TARGET` covering: PRs scanned, PRs swept, fixes vs
   declined, PRs that need human attention, whether the skill was invoked per PR.

## Pitfalls

- **ACP slash commands are meaningless** — `claude --print "/address-pr-comments"` does
  NOT invoke the skill. Always use the explicit "Use the Skill tool to invoke ..."
  prompt above. Verify in the output.
- **Bot allowlist trap** — early versions hardcoded bot logins to skip. Don't. Use the
  zero-reactions heuristic — it covers any reviewer category.
- **Cost runaway** — keep `MAX_PRS_PER_RUN` low (≤10) until you've watched a few
  successful runs. A bad sweep can spin up 50+ Claude Code processes.
- **Serial, not parallel** — Claude Code workshops grab the same `~/dev/{repo}*` paths;
  parallel runs collide. Serial keeps state clean.
- **Workshop cleanup must succeed** — orphan workshop dirs accumulate fast. Always
  `rm -rf` after each PR, even on failure.

## Cron setup

```bash
hermes cron add \
  --name "pr-review-sweep" \
  --schedule "0 6 * * *" \
  --tz "<your-timezone>" \
  --skill pr-review-sweep \
  --model "<work-tier-model>" \
  --timeout 7200
```

Daily at `06:00` local. Long timeout because Claude Code per-PR runs can be slow
(`MAX_PRS_PER_RUN * TIMEOUT_PER_PR_S` upper bound).

## Origin

Ported from an OpenClaw `workflows/pr-review-sweep/` folder (AGENT.md + config.md) into
a single Hermes skill. The config.md knobs are now the table at the top of this file;
the AGENT.md procedure is the body. Cron scheduling lives separately in `hermes cron`,
where it belongs.
