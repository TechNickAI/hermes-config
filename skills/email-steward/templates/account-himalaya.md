# Account adapter: himalaya IMAP

Copy this file to `~/.hermes/email-steward/<account-name>/account.md` and replace every
placeholder. The Himalaya config must retrieve its password at runtime from a secret
store or mode-700 helper. Never put the password here.

## Identity

- account: `<email-address>`
- access method: `himalaya`
- config path: `<absolute-config-path>`
- state directory: `~/.hermes/email-steward/<account-name>/`

```bash
CFG="<absolute-config-path>"
```

## Operations

### health_check

```bash
himalaya -c "$CFG" -o json folder list
```

Expected: valid JSON. Authentication or transport failure is fatal for the run. Do not
fall back to browser login automation.

### list_unprocessed(n)

```bash
himalaya -c "$CFG" -o json envelope list -f INBOX -s <n>
```

Gmail over IMAP exposes labels as folders. Exclude messages already copied to terminal
`Agent-*` folders. If the installed backend cannot query those labels efficiently, keep
a visible Markdown dedup table under the account state directory with message ID, date,
and disposition. Do not use unread state.

### get_headers(message_id)

Pipe the command directly to the classifier so the body never enters the orchestrator's
context. The classifier stops parsing at the first blank line.

```bash
himalaya -c "$CFG" message read --preview -f INBOX \
  -H From -H Subject -H Content-Type -H List-Unsubscribe -H Precedence \
  -H List-Id -H In-Reply-To -H X-Mailchimp -H X-MC-User -H X-SG-EID \
  -H X-Sendgrid -H X-Mailgun -H X-Postmark -H X-HubSpot -H X-Marketo \
  -H X-SES-Outgoing -H X-Campaign -H X-Microsoft-CDO-Busystatus \
  <message-id> |
python ~/.hermes/skills/email-steward/scripts/header_heuristics.py \
  --format rfc822 \
  --vip-domain <company.example> \
  --vip-sender <person@example.com>
```

### get_body(message_id)

Sub-agent only:

```bash
himalaya -c "$CFG" -o json message read --preview --no-headers -f INBOX <message-id>
```

The child returns only the skill's structured decision contract. The orchestrator never
runs this command.

### apply_action(message_id, action)

Create `Agent-Archived`, `Agent-Quarantine`, `Agent-Reviewed`, and `Agent-Starred` as
folders or Gmail labels first.

```bash
# archive: copy audit label, then remove INBOX by moving to All Mail
himalaya -c "$CFG" message copy Agent-Archived <message-id>
himalaya -c "$CFG" message move "[Gmail]/All Mail" <message-id>

# quarantine: copy or move to recoverable review folder
himalaya -c "$CFG" message move Agent-Quarantine <message-id>

# flag: leave in inbox, add the flagged IMAP flag, optionally copy the audit label
himalaya -c "$CFG" flag add <message-id> flagged
himalaya -c "$CFG" message copy Agent-Starred <message-id>

# keep: leave in inbox and copy the reviewed audit label
himalaya -c "$CFG" message copy Agent-Reviewed <message-id>
```

IMAP message IDs may change after a move. Verify using a stable `Message-Id` header,
subject plus sender, or the destination folder's returned ID. Record the post-action ID
in the Markdown dedup table when needed.

### check_sent(message_id)

```bash
himalaya -c "$CFG" -o json message thread <message-id>
```

If the latest message is from the managed account, the loop is handled.

### check_calendar(clue)

IMAP provides no calendar truth. Define an external calendar read command if available;
otherwise mark date liveness unverified and default to `keep`, not `flag`.
