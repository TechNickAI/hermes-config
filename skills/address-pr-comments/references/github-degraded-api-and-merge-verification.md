# GitHub degraded-API and merge-verification patterns

Use these patterns when cleaning up PRs while GitHub intermittently returns 503s or a
compound `gh` command exits non-zero.

## Review comments: REST 503 fallback

Do not interpret a failed `/pulls/<N>/comments` request as "no comments." Query GraphQL
review threads instead:

Use the paginated query in the next section; it is the only form to run.

This exposes unresolved threads, replies, and reactions. Preserve the root comment's
`databaseId`; once REST recovers, use it with the normal reply and reaction endpoints.

### Passing the GraphQL query correctly

**Do NOT inline the query as a JSON string in `-f query=...`.** GraphQL field
definitions contain colons (`owner: String!`), and `gh api graphql -f query='...'`
chokes on them with:

```
Expected VAR_SIGN, actual: COLON (":") at [1, 7]
```

Write the query to a `.graphql` file and use shell expansion:

```bash
cat > /tmp/prthreads.graphql << 'EOF'
query($owner: String!, $name: String!, $n: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $n) {
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          comments(first: 100) {
            totalCount
            nodes {
              databaseId
              author { login }
              body
              path
              line
              reactions(first: 10) { nodes { content user { login } } }
            }
          }
        }
      }
    }
  }
}
EOF

gh api graphql --paginate -f query="$(< /tmp/prthreads.graphql)" \
  -F owner=<owner> -F name=<repo> -F n=<pr_number> > /tmp/prthreads.json \
  || { echo "FETCH FAILED: thread counts UNKNOWN, not zero"; exit 1; }
jq -s '[.[].data.repository.pullRequest.reviewThreads.nodes[]]
    | "threads=\(length) unresolved=\(map(select(.isResolved|not))|length)",
      (.[] | select(.comments.totalCount > (.comments.nodes|length))
        | "TRUNCATED thread \(.comments.nodes[0].databaseId): read it on the web"),
      (.[] | select(.isResolved|not) | {
        id:.comments.nodes[0].databaseId,
        author:.comments.nodes[0].author.login,
        path:.comments.nodes[0].path,
        line:.comments.nodes[0].line,
        replies: (.comments.totalCount-1),
        reactions: (.comments.nodes[0].reactions.nodes|length),
        body:.comments.nodes[0].body[0:400]
      })' /tmp/prthreads.json
```

Fetch to a file first and check `gh`'s exit status: piped straight into `jq -s`, a
failed or half-finished fetch prints `threads=0 unresolved=0` and exits 0, which is the
false all-clear this fallback exists to prevent. `--paginate` follows `$endCursor`
across every thread page and `jq -s` joins the pages (works on any `gh`; `--slurp` needs
a recent one), so the counts cover the whole PR. A thread longer than 100 comments
prints `TRUNCATED`; read that one on the web rather than trusting the partial replies.

The `-F` flag passes variables as typed GraphQL fields (String/Int), avoiding all shell
quoting issues.

## Actions API 503: check-runs fallback

When `gh run list --branch=<branch>` or `gh run view <run_id>` returns HTTP 503 (GitHub
Actions API degradation), poll CI status via the check-runs API on the commit SHA
instead:

```bash
SHA=$(gh pr view <N> --repo <owner>/<repo> --json headRefOid --jq '.headRefOid')
gh api repos/<owner>/<repo>/commits/$SHA/check-runs \
  --jq '.check_runs[] | [.name,.status,.conclusion] | @tsv'
```

This endpoint is often available when the actions/runs endpoint is degraded. Check-runs
also show `queued` status when GitHub is experiencing runner-queue delays — this is not
a code problem; wait and re-poll.

## Compound merge command: verify the side effect

`gh pr merge N --squash --delete-branch` performs two independent operations. The merge
may succeed while branch deletion fails with a transient API error, yielding a non-zero
exit.

Before retrying:

```bash
gh api repos/OWNER/REPO/pulls/N \
  --jq '{state, merged, merged_at, head:.head.ref}'
```

If `merged: true`, do not rerun the merge. Delete the branch separately:

```bash
git push origin --delete HEAD_BRANCH
```

Then re-list open PRs to verify the intended backlog is clear.

## Test runner discovery trap

A Django test-label command can return success while reporting `Ran 0 tests` for
pytest-style test modules. Treat zero tests as failed verification, not success. Run the
affected module explicitly with pytest:

```bash
python -m pytest path/to/test_module.py -q --maxfail=1 --ds=<settings>
```

After reviewer-driven edits, run pre-commit on the exact changed files, allow formatter
hooks to modify them, rerun pre-commit, and rerun the targeted tests before pushing.
