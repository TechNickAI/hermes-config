# Account adapter: gog Gmail API

Copy this file to `~/.hermes/email-steward/<account-name>/account.md` and replace every
placeholder. Keep secrets out of this file.

## Identity

- account: `<email-address>`
- access method: `gog` through `<absolute-wrapper-path>`
- state directory: `~/.hermes/email-steward/<account-name>/`

Set this shell abbreviation mentally for the commands below:

```bash
GOG="<absolute-wrapper-path>"
ACCOUNT="<email-address>"
```

The wrapper should set the real user home when Hermes runs under a profile-specific
home. It must not print OAuth tokens.

## Operations

### health_check

```bash
$GOG gmail labels list --account "$ACCOUNT" --json --no-input
```

Expected: valid JSON containing the account's labels. Any authentication error is fatal
for the run.

### list_unprocessed(n)

```bash
$GOG gmail search \
  'in:inbox -label:Agent-Starred -label:Agent-Reviewed -label:Agent-Archived -label:Agent-Quarantine' \
  --max <n> --account "$ACCOUNT" --json --no-input
```

Use each result's stable thread ID. Do not use read/unread state as deduplication.

### get_headers(thread_id)

Fetch the thread as JSON, then pipe it directly into the deterministic classifier. Raw
headers and bodies must not be printed into the orchestrator's context.

```bash
$GOG gmail thread get <thread-id> --account "$ACCOUNT" --json --no-input |
python ~/.hermes/skills/email-steward/scripts/header_heuristics.py \
  --format gog-thread-json \
  --vip-domain <company.example> \
  --vip-sender <person@example.com>
```

The classifier reads only the latest message's headers and outputs one small JSON
object.

### get_body(message_id)

Sub-agent only:

```bash
$GOG gmail get <message-id> --account "$ACCOUNT" --json --no-input
```

The child extracts plaintext from exactly one message and returns only the structured
classification contract from the skill. The orchestrator never runs this command.

### apply_action(thread_id, action)

Create these Gmail labels first: `Agent-Archived`, `Agent-Quarantine`, `Agent-Reviewed`,
`Agent-Starred`.

```bash
# archive
$GOG gmail thread modify <thread-id> \
  --add Agent-Archived --remove INBOX --account "$ACCOUNT" --force --no-input

# quarantine
$GOG gmail thread modify <thread-id> \
  --add Agent-Quarantine --remove INBOX --account "$ACCOUNT" --force --no-input

# flag
$GOG gmail thread modify <thread-id> \
  --add Agent-Starred --account "$ACCOUNT" --force --no-input

# keep
$GOG gmail thread modify <thread-id> \
  --add Agent-Reviewed --account "$ACCOUNT" --force --no-input
```

When multiple labels are passed to one `--add` or `--remove`, use the comma-separated
syntax supported by the installed `gog` version. Do not repeat the same flag and assume
values accumulate.

After every mutation, call `gmail thread get` and verify the expected label IDs and
inbox state. Command success text is not verification.

### check_sent(thread_id)

Use `gmail thread get`. If the latest message was authored by the managed account, the
loop is handled. If thread output is unavailable, search sent mail using a narrow sender
and subject query, then verify the returned thread ID.

### check_calendar(clue)

Use the configured calendar CLI only when a message's action depends on a dated event.
Verify the actual event date and status. Do not infer liveness from relative words such
as "tomorrow" in an old email.
