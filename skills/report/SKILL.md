---
name: report
description: >
  File a bug report or piece of feedback from any Hermes platform session. Load this
  skill when the user says "/report", "file a bug", "report a bug", "something's
  broken", "something's wrong", "that's weird", "this is wrong", "this isn't working",
  "log this issue", or any other signal that they want to capture a problem for triage.
  Works on Telegram, Discord, Slack, and the CLI — the closed loop (a notification back
  to this chat when the issue is resolved) is wired automatically for gateway sessions.
version: 0.2.0
license: MIT
metadata:
  hermes:
    tags: [bugs, feedback, kanban, triage, fleet, reporting]
    related_skills: [kanban-worker, kanban-orchestrator]
---

# Report

File a bug or piece of feedback, land it in the triage column of the shared kanban
board, and wire a closed-loop notification so the reporter hears back when it's resolved
— all from a single `/report` or a natural-language sentence.

## When to load

Load this skill automatically when the current user turn contains any of these patterns
(case-insensitive):

- `/report` (exact slash-command)
- "file a bug" / "report a bug" / "report this bug"
- "file a report" / "file an issue" / "log this issue"
- "something's broken" / "something is broken"
- "something's wrong" / "something is wrong"
- "this is broken" / "this isn't working" / "this doesn't work"
- "that's weird" / "this is weird" / "that was weird"
- "there's a bug" / "i found a bug"

Also load when the user explicitly says "load the report skill" or asks to file feedback
or a suggestion.

## What you do

1. **Acknowledge immediately.** Reply with a short line so the user knows you've got it:
   `"On it — filing that now."`
2. **Gather the title.** If the user's message contains a clear description, distill it
   into ≤10 words. If they just said `/report` with no detail, ask one brief question:
   `"What's the issue in one sentence?"`. Do not ask for more — the transcript excerpt
   provides context.
3. **Build the body.** Compose a markdown body with:
   - **What the user reported:** their exact words (quote them)
   - **Session context:** profile name, platform, timestamp (from env)
   - **Transcript excerpt:** the last 5–10 turns of the current session in condensed
     form (enough to let a triager reproduce or understand)
   - Do NOT include raw API keys, tokens, or personal credentials anywhere in the body.
4. **Write body to a temp file** (e.g. `/tmp/bug-body-<session_id>.md`).
5. **Run the helper script** (see Script Path below). It handles both paths:
   - **Local (board-owner profile):** `hermes kanban create --triage` +
     `hermes kanban notify-subscribe`

- **Remote (all other fleet members):** HMAC-signed POST to `$BUG_WEBHOOK_URL`; dropfile
  fallback if gateway is down dropfile fallback if gateway is down

6. **Parse the result.** On success, confirm to the user with the task id. On failure,
   DM the maintainer the raw report body so nothing drops.
7. **Confirm.** Tell the user the card id and, if the closed loop was wired: _"You'll
   get a ping here when it's resolved."_

## Script path

The helper lives alongside this skill. Resolve it relative to this file:

```bash
SKILL_DIR=$(dirname "$(hermes skills list --paths 2>/dev/null | grep '/report/SKILL.md' | head -1)")
SCRIPT="$SKILL_DIR/scripts/file_bug.py"
```

Or resolve via Hermes's skills directory convention:

```bash
# The skill is installed at one of these locations:
#   ~/.hermes/skills/report/scripts/file_bug.py          (single-profile install)
#   ~/.hermes/profiles/<name>/skills/*/report/scripts/file_bug.py  (profile install)
SCRIPT=$(find ~/.hermes -path "*/report/scripts/file_bug.py" 2>/dev/null | head -1)
```

If the script is not found, fall back to calling `hermes kanban create` inline (see
Fallback section below).

## Invocation

```bash
python3 "$SCRIPT" \
  --title "<10-word title you distilled>" \
  --body-file /tmp/bug-body-<session_id>.md \
  --reporter "<user's display name or Telegram handle>" \
  --json
```

Parse stdout as JSON:

```json
{
  "ok": true,
  "path": "local",
  "task_id": "t_a1b2c3d4",
  "status": "triage",
  "subscribed": true
}
```

- `ok: true` → confirm to the user
- `ok: false, dropfile: "..."` → post that path as a note and DM the maintainer
- `ok: false, error: "..."` → use the fallback path below

## Fallback (if script not found)

```bash
hermes kanban create "<title>" \
  --triage \
  --tenant fleet-bugs \
  --created-by "<reporter>" \
  --body "<condensed body>" \
  --json
```

Then:

```bash
hermes kanban notify-subscribe <task_id> \
  --platform <HERMES_SESSION_PLATFORM> \
  --chat-id <HERMES_SESSION_CHAT_ID> \
  [--thread-id <HERMES_SESSION_THREAD_ID>] \
  [--user-id <HERMES_SESSION_USER_ID>]
```

## User-facing confirmation

After a successful `ok: true` result:

> Filed as **t_a1b2c3d4**. I'll look into it and you'll get a ping here when it's
> resolved.

After a deduped result (same session within the 2-minute window, same id returned):

> Already filed as **t_a1b2c3d4** — no duplicate created.

After a failure with dropfile:

> Couldn't reach the board right now, but your report is saved locally and I've flagged
> it for the maintainer. Nothing dropped.

## Configuration

Set these in `~/.hermes/.env` (or the profile's `.env`):

| Variable             | Required on        | Purpose                                                                               |
| -------------------- | ------------------ | ------------------------------------------------------------------------------------- |
| `BUG_BOARD_OWNER`    | All profiles       | Profile name that owns the triage board (required — no default ships in the template) |
| `BUG_WEBHOOK_URL`    | Non-owner profiles | Endpoint to POST the report to                                                        |
| `BUG_WEBHOOK_SECRET` | Non-owner profiles | HMAC-SHA256 signing secret                                                            |

The board-owner profile (the one where `$HERMES_PROFILE` matches `$BUG_BOARD_OWNER`)
does not need the webhook env vars — it calls `hermes kanban` directly. All other fleet
members need `BUG_BOARD_OWNER` plus both webhook vars.

## Closed-loop notification

When the triager calls `hermes kanban complete <id>`, the gateway's kanban notifier
automatically delivers one message to the subscribed chat/thread:

```
✔ @<user> Kanban t_a1b2c3d4 done — <title>
```

If the card is blocked: `⏸ Kanban ... blocked` If it fails: `✖ Kanban ... gave up`

The subscription is removed automatically after the done notification. No polling, no
manual DM required.

## Privacy and data-handling notes

- Transcript excerpts included in the report body will be visible to the triager (the
  maintainer). Only include turns from the session where `/report` was invoked.
- Do **not** include personal credentials, API keys, or sensitive financial/health
  context in the body.
- The `fleet-bugs` kanban board is local (SQLite on the maintainer's machine), not
  cloud-synced. The webhook payload is HMAC-signed for integrity but transmitted over
  the network — use Tailscale or another private tunnel, not a public URL.

## Triage runbook

See `references/triage.md` for how the maintainer handles the triage column, closes
cards, and what the automatic notification message looks like.
