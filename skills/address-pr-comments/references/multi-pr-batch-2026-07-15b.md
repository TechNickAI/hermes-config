# Multi-PR Batch Session — one occasion (Trading Bots + Config)

## Session shape

5 PRs across 3 repos (2 trading bots, 1 MCP hub, 1 config repo). User authorized
merge-if-clean with squash+delete-branch. Each PR had 2-4 bot comments, some already
handled by TechNickAI replies.

| PR   | Repo                         | Comments      | Bots          | Notes                                  |
| ---- | ---------------------------- | ------------- | ------------- | -------------------------------------- |
| #4   | carmentacollective/antevorta | 2             | gemini, codex | Trading bot — money-path code          |
| #9   | carmentacollective/antevorta | 2 (1 replied) | gemini        | WONTFIX reply already present          |
| #9   | carmentacollective/<agent-f> | 2             | gemini        | Both had -1 reactions                  |
| #154 | carmentacollective/mcp-hubby | 4 (2 replied) | gemini, codex | TechNickAI replies already present     |
| #128 | TechNickAI/openclaw-config   | 3             | cursor, codex | Tests failing (not just claude-review) |

## Key lessons

### Branch names in user request didn't match actual branches

User provided: `fix/safety-monitor-scope`, `review-sweep/7-sqlite-conn-reuse`,
`fix/review-sweep-pr2`, `fix/shutdown-mode-bugs`, `fix/cortex-knowledge-categories`

Actual branches: `fix-copytrade-safety-monitor-scope`, `sweep/pr7-review-fixes`,
`review-sweep-pr2-followup`, `fix/review-sweep-153`, `fix/cortex-schema-categories`

Always fetch real branch name from
`gh pr view <N> --json headRefName --jq '.headRefName'`.

### "Only claude-review failures" was wrong for PR 5

User said all 5 PRs had "only claude-review CI failures (no lint/test failures)." PR 5
(openclaw-config #128) actually had failing `Python tests` — 3 tests failed because test
fixtures created `entities/` dirs but the code now expects `people/` dirs. The user's
mental model of CI state was stale.

### Already-handled comments pattern

PR 4 (mcp-hubby #154): Both bot comments had TechNickAI replies confirming fixes (commit
cf856f1), plus reactions (gemini: heart, codex: +1). These were fully closed — just
needed to verify CI and merge.

PR 2 (antevorta #9): Gemini comment had a TechNickAI WONTFIX reply with 1 reaction. Just
needed to add a 👍 to the original gemini comment and merge.

PR 3 (<agent-f> #9): Both gemini comments had -1 reactions (disagreement). Needed
WONTFIX replies explaining why, plus 👍 reactions to close the loop.

### Test that asserts buggy behavior (antevorta #4)

Codex correctly identified that
`test_non_copy_position_excluded_when_copy_scope_explicit` would fail after removing the
guard. The test was asserting the OLD behavior (empty `copy_tickers` suppresses
copy-tagged resting sells). Fix: rename to
`test_copy_tagged_resting_sell_always_in_scope` and assert the finding IS produced.

The test used `_order("T", action="sell", remaining="9", client_order_id="copy-a")` with
`copy_tickers=frozenset()`. With the guard removed, the ticker "T" gets added via the
resting-sell union loop and `resting_sell=9 > position=3` produces a CRITICAL finding.
Updated assertion: `assert len(findings) == 1` and
`assert findings[0].kind == InvariantKind.LATENT_SHORT`.

### Schema/template synchronization (openclaw-config #128)

PR changed `KNOWLEDGE_CATEGORIES` from
`entities/concepts/summaries/synthesis/decisions/how-to` to
`people/ventures/topics/synthesis/decisions/learning/research`. But:

- `schema-template.md` still referenced old dirs (entities/, concepts/, summaries/,
  how-to/)
- Test fixtures created `entities/` and `concepts/` dirs
- `rebuild-index` command looked for `people/index.md` but tests created `entities/`
- 3 tests failed: `test_rebuild_empty`, `test_rebuild_with_pages`,
  `test_rebuild_warns_on_bad_frontmatter`

Cursor and codex both correctly flagged the schema-template mismatch. Fix required
updating: schema-template.md (store layout, page types table, frontmatter examples,
MEMORY.md routing template, linking section), test fixtures (entity→people,
concept→topic dir references), and the `_setup_symlink` MEMORY.md template in the cortex
CLI itself.

### Parallel workflow optimization

All 5 PR view + comment fetch calls were batched into a single tool-call block (10
parallel `gh api` calls). This gave the full triage surface in one round-trip. Then all
5 full comment body fetches + diff fetches were batched in the next round. This is the
fastest pattern for multi-PR batches.

## Bot comment patterns

### gemini-code-assist[bot] — already seen, confirmed

- `![high](...)` / `![medium](...)` badges
- Often provides suggestion blocks with concrete code
- High: real bugs (scope gaps, midnight-wrapping logic gaps)
- Medium: code style (encoding="utf-8", loop simplification)

### chatgpt-codex-connector[bot] — already seen, confirmed

- P1/P2 badge images
- P2: test/contract issues (test asserting old behavior after code change)
- P1: schema/template synchronization mismatches
- "Useful? React with 👍 / 👎." footer

### cursor[bot] — already seen, confirmed

- `### Title` headers with severity badges
- `<!-- BUGBOT_BUG_ID: <uuid> -->` markers
- Medium: schema-template mismatch with category list
- Low: ops files inflating page count

## Commands used

```bash
# Fetch all comments with metadata (parallel batch)
gh api repos/$R/pulls/$N/comments --jq '.[] | {id, user:.user.login, body:.body[:500], path, line, original_line, in_reply_to_id, reactions:.reactions.total_count}'

# Fetch full comment body
gh api repos/$R/pulls/$N/comments --jq '.[] | select(.id == <ID>) |.body'

# Get PR diff
gh pr diff $N --repo $R

# Get test file from PR branch
gh api repos/$R/contents/<path>?ref=<branch> --jq '.content' | base64 -d

# Check CI failures
gh pr checks $N --repo $R
gh run view <RUN_ID> --repo $R --log-failed

# Reply to a line comment
gh api -X POST repos/$R/pulls/$N/comments/$COMMENT_ID/replies -f body="..."

# React to a line comment
gh api -X POST repos/$R/pulls/comments/$COMMENT_ID/reactions -f content="+1" --silent

# Clone with specific branch
gh repo clone $R ~/dev/<repo>-pr<N> -- --branch "$BRANCH"

# Merge with squash + delete branch
gh pr merge $N --repo $R --squash --delete-branch
```

## Final state

| PR                   | Outcome                             | Detail                                     |
| -------------------- | ----------------------------------- | ------------------------------------------ |
| antevorta #4         | Pushed fix, replied, merged pending | Test updated, bot comments replied+reacted |
| antevorta #9         | Merged                              | WONTFIX reply already present, added 👍    |
| <agent-f> #9         | Merged                              | WONTFIX replies posted, 👍 added           |
| mcp-hubby #154       | Merged                              | Already fully handled by TechNickAI        |
| openclaw-config #128 | In progress                         | Schema-template + tests need fixing        |
