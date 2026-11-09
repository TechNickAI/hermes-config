# Multi-PR Batch Session — one occasion

## Session shape

5 PRs across 3 repos, each with 1-4 unresolved bot comments. User authorized
merge-if-clean with squash+delete-branch.

| PR  | Repo            | Comments | Bots                                                |
| --- | --------------- | -------- | --------------------------------------------------- |
| #4  | hangl-dashboard | 1        | gemini-code-assist                                  |
| #8  | hangl-dashboard | 3        | gemini-code-assist, chatgpt-codex-connector         |
| #1  | OmniRoute       | 1        | chatgpt-codex-connector                             |
| #26 | <agent-f>       | 3        | cursor, gemini-code-assist                          |
| #28 | <agent-f>       | 4        | gemini-code-assist, cursor, chatgpt-codex-connector |

## Workflow that worked

1. **Parallel comment fetch** — all 5 `gh api` calls in one tool block. JQ filter:
   `.[]|select(.in_reply_to_id==null)|"\(.id)|\(.user.login)|\(.path):\(.line //.original_line)|\(.body[0:400])"'`
2. **Clone per PR** — `gh repo clone` to `~/dev/<repo>-pr<N>`, fetch+checkout PR branch.
3. **Process sequentially, push each fix** — start CI on one PR while validating the
   next.
4. **Return for CI check + merge** — after all pushes, loop back to check CI and merge.

## Bot comment patterns encountered

### gemini-code-assist[bot]

- Uses `![medium](...)` / `![high](...)` / `![security-high](...)` badges.
- Often provides ````suggestion` blocks with concrete code.
- Medium-priority comments can be minor optimizations (hoist a variable, deduplicate
  lookups).
- High-priority comments are usually real bugs (falsy-zero `or` chains, security
  bypasses).

### chatgpt-codex-connector[bot]

- Uses `P1`/`P2` badge images.
- P1: test-seam issues (patching wrong function), correctness bugs.
- P2: security hardening (backslash in redirect validation).
- Often provides "Useful? React with 👍 / 👎." footer.

### cursor[bot]

- Uses `### Title` headers with severity badges.
- Embeds `<!-- DESCRIPTION START -->` / `<!-- DESCRIPTION END -->` markers.
- High-severity findings on trading code (falsy-zero bugs, IoC cover logic).

## Fix patterns

### Backslash open-redirect bypass (hangl-dashboard #8)

Browsers normalize `\` to `/`, so `X-Forwarded-Prefix: /\evil.com` passes a
`startswith("/")` and `not startswith("//")` check but redirects to `//evil.com`. Fix:
add `"\\" in raw` to the guard predicate.

### Test patching wrong function (hangl-dashboard #8)

Test monkeypatches `import_crawdad.main` but code calls `import_crawdad.import_trades`.
Fix: update the test to patch `import_trades` with matching keyword arguments.

### Shared module-level constant with env override (OmniRoute #1)

`CLAUDE_CLI_USER_AGENT = claudeCliUserAgent(CLAUDE_CLI_VERSION)` evaluated at import
time with `getClaudeEntrypoint()`, so `<NEW_FLAG>=sdk-cli` affects ALL consumers
including API-key providers. Fix: make the constant always use `"cli"`, and only call
the function in OAuth-specific paths.

## Key commands

```bash
# Get PR branch name
gh api repos/$R/pulls/$N --jq '.head.ref'

# Reply to a line comment (dedicated endpoint)
gh api -X POST repos/$R/pulls/$N/comments/$COMMENT_ID/replies -f body="..."

# React to a line comment
gh api -X POST repos/$R/pulls/comments/$COMMENT_ID/reactions -f content="+1" --silent

# Check CI
gh pr checks $N --repo $R --json name,state,conclusion

# Merge with squash + delete branch
gh pr merge $N --repo $R --squash --delete-branch
```

## Notes on live trading bot PRs (<agent-f>)

- Extra care with money-path logic: `remaining_count or` falsy-zero bugs can cause
  overcounting of cover.
- `Decimal("0")` corruption in order price calculations is a real concern.
- IoC cover counting logic needs validation against actual execution paths.
- Always validate the specific line/function the bot flags against the actual code — the
  bot may be reviewing an earlier revision and the fix may already be present.
