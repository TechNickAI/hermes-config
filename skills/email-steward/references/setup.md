# Email steward setup

## Design

One account maps to one state directory and one Hermes cron job. The job attaches the
`email-steward` skill and supplies only the account state path. Scheduling and delivery
belong to Hermes, not to the skill.

## 1. Install the skill

From a clone of `hermes-config`:

```bash
mkdir -p ~/.hermes/skills/email-steward
cp -R skills/email-steward/. ~/.hermes/skills/email-steward/
```

A direct URL can install `SKILL.md`, but this skill also needs `scripts/`, `templates/`,
and `references/`, so copy the directory or use a skill source that preserves linked
files.

## 2. Create one account state directory

```bash
ACCOUNT_NAME="<account-name>"
STATE="$HOME/.hermes/email-steward/$ACCOUNT_NAME"
mkdir -p "$STATE/logs"
cp ~/.hermes/skills/email-steward/templates/rules.md "$STATE/rules.md"
cp ~/.hermes/skills/email-steward/templates/learned-senders.md "$STATE/learned-senders.md"
cp ~/.hermes/skills/email-steward/templates/agent-notes.md "$STATE/agent-notes.md"
cp ~/.hermes/skills/email-steward/templates/account-gog.md "$STATE/account.md"
# Use account-himalaya.md instead for IMAP.
```

Edit the placeholders. Store secrets in a secret manager, `.env`, or a mode-700 helper,
never in these Markdown files.

Create the audit labels or folders named by the adapter before enabling actions.

## 3. Test the adapter before scheduling

Run these manually:

1. `health_check` and parse its output;
2. `list_unprocessed(3)` and verify stable IDs;
3. `get_headers(id)` for one bulk message and one ordinary message;
4. run one reversible `keep` action on a test message;
5. re-query and prove the audit label appeared;
6. undo the test label if desired.

Do not test by permanently deleting or sending mail.

## 4. Create the Hermes cron job

Current Hermes CLI syntax:

```bash
hermes cron create "every 30m" \
  "Triage exactly one email account using the attached email-steward skill. Account state: $HOME/.hermes/email-steward/<account-name>. Read account.md, rules.md, learned-senders.md, and agent-notes.md before acting. Process at most the configured batch size. If the verified inbox batch is empty or nothing needs attention and all actions succeeded, return exactly [SILENT]." \
  --name "email-steward-<account-name>" \
  --deliver origin \
  --skill email-steward
```

Choose a delivery destination that the user actually monitors. `origin` returns to the
conversation that created the job. Use an explicit `platform:chat_id` only when the user
requests another destination.

The CLI currently has no `--toolset` flag. Jobs created through Hermes' cron API can
restrict the **parent job** with `enabled_toolsets`; otherwise use a dedicated profile
whose enabled tools are already minimal. For this workflow, the parent normally needs:

- terminal or file tools for the email CLI and Markdown state;
- delegation for context-isolated body reads;
- cron only for job management, not every run.

This is not a per-child sandbox. `delegate_task` children inherit the parent's available
toolsets, so narrowing the parent job or profile is the security boundary. Delegation
provides a fresh conversation and keeps raw body text out of the orchestrator's context;
it does not remove tools from the child.

## 5. Prove the scheduled path

A job is not installed merely because it exists.

1. Force one run with `hermes cron run <job-id>` or the cron tool.
2. Inspect the execution status and output.
3. Verify at least one real, reversible test disposition on the mail system.
4. Confirm a successful no-op is suppressed by exact `[SILENT]`.
5. Break the adapter command temporarily and confirm the job reports an operational
   failure instead of silence, then restore it.

## Migrating an older workflow

Port these concepts:

- user rules and VIPs;
- confirmed sender patterns;
- failure corrections;
- reversible labels and action vocabulary;
- actionability gates;
- prompt-injection isolation.

Do not port these mechanics:

- a standalone workflow runner;
- channel-specific sending code;
- a separate scheduler;
- hidden JSON state;
- per-run status messages;
- raw email bodies in logs.

Review imported rules before use. Old state often contains personal data and stale
one-off exceptions, so it does not belong in the public skill and should not be copied
blindly into a new account.
