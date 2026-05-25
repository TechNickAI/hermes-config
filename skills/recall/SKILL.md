---
name: recall
description: >
  Restore context from prior sessions, memories, and transcripts. Run /recall after /new
  to pick up where you left off. Designed to never dead-end — if one source comes up
  empty, keep searching others until you have a useful picture.
version: 0.2.0
license: MIT
metadata:
  hermes:
    tags: [context, session, recovery, telegram, gateway, productivity]
    related_skills: [cron-healthcheck, pr-review-sweep]
---

# Recall

**Mission:** Restore as much relevant context as possible. Never return empty-handed.
If one source has nothing, try the next. Synthesize what you find into a clear briefing
so the conversation can continue naturally.

## When invoked

The user ran `/recall` (possibly with a topic hint like `/recall project X status`
or `/recall alice relationship analysis`) after a `/new` reset or context overrun.
They want to pick up where they left off. Your job is to find that thread and hand
it back to them.

## What to do

Work through these sources in order. **Do not stop at the first miss** — keep going
until you have enough to give a useful briefing, or until all sources are exhausted.

### 1. Session search (start here)

Use `session_search` to find prior sessions matching the topic or recent activity:

- If the user gave a topic hint, search for it: `session_search(query="<topic hint>")`
- Also search for related terms if the first query is thin — break the phrase apart, try synonyms
- Try a broad recency search with no query to see what was recently worked on: `session_search()` (no args = recent sessions)
- Look at multiple results, not just the top one

### 2. Hermes session DB (if session_search has matches)

If `session_search` returns matching sessions, load the transcript for the most
relevant one(s). Read through it and extract:

- What was being worked on
- Key decisions or conclusions reached
- Open questions or blockers left unresolved
- What the user's last message / intent was

### 3. Memory and cortex

Check what the agent already knows that's relevant:

- Search `cortex` for the topic if available: `cortex(action="search", query="...")`
- Read the agent's `MEMORY.md` / `memory/` files for any durable notes on this subject
- Check `USER.md` for relevant user context that bears on the topic

### 4. Raw Telegram history (tgcli fallback)

If sessions and memory come up empty but the user clearly remembers a conversation
happening, it may predate Hermes or have been with a different bot. Use tgcli:

```bash
tgcli sync --chat <chat_id> --msgs-per-chat 200
```

Then query `~/.tgcli/tgcli.db`:

```sql
SELECT sender_name, datetime(ts, 'unixepoch') AS when_, text
FROM messages
WHERE chat_id = '<chat_id>'
  AND (text LIKE '%<keyword>%' OR text LIKE '%<keyword2>%')
ORDER BY ts DESC
LIMIT 50;
```

See the [tgcli section](#raw-telegram-history-fallback-tgcli) below for setup details.

### 5. Synthesize and brief

Once you have gathered what's available, produce a **context briefing**:

> **Recalled context — [topic]**
>
> **What was being worked on:** ...
> **Key decisions / conclusions:** ...
> **Open threads:** ...
> **What to do next:** ...

Inject this briefing into the session so it's visible. Then ask if the user wants to
pick up from there or if they need anything clarified.

If you genuinely found nothing across all sources, say so plainly and offer to help
reconstruct — don't just say "no results found." Ask: what do you remember about it?
When was it roughly? That's still more useful than a dead end.

## The /recall command

`/recall` is a gateway slash command. Common invocations:

| Syntax | What it does |
| --- | --- |
| `/recall` | Find the most recent prior session in this thread |
| `/recall 3` | Summarise the last 3 sessions in this thread |
| `/recall 7d` | All sessions active in the last 7 days |
| `/recall <phrase>` | Search all sessions for the phrase, then fall through to memory/tgcli if thin |
| `/recall <phrase> 7d` | Same, scoped to last 7 days |

**After `/new`**, if a prior session exists in the same thread, the agent automatically
appends:

> 💡 Run /recall to restore context from your prior session.

## Raw Telegram history fallback (tgcli)

Use when sessions are gone, predate Hermes, or were with a different bot.

`tgcli` is an MTProto user-account CLI that mirrors your Telegram history into local
SQLite. It reads as you — not as a bot — so it sees everything your Telegram client sees.

### Install + auth

```bash
# Requires TGCLI_APP_ID and TGCLI_APP_HASH from https://my.telegram.org/apps
tgcli login
```

### Sync

```bash
tgcli sync --chat <chat_id_or_username> --msgs-per-chat 200
```

Messages land in `~/.tgcli/tgcli.db` (table: `messages`, columns: `chat_id,
chat_name, msg_id, sender_id, sender_name, ts, from_me, text`).

### DMs vs forum supergroups

- **DMs** — Telegram has no server-side topic for 1:1 DMs. The whole chat is one flat
  stream. tgcli works as-is; no topic filtering needed.
- **Forum supergroups** — tgcli does NOT expose `message_thread_id` upstream. You get
  the whole supergroup. Filter by keyword in the SQL query, or use `/recall <phrase>`
  for Hermes-side FTS instead.

### Pitfalls

- Bot-only channels are invisible to tgcli (it authenticates as your user account)
- Sync is on-demand — run it fresh before querying
- `~/.tgcli/` contains a live user-account session; treat it like an SSH key

## Pitfalls (general)

- **Never return "no topic matching" and stop.** That's a dead end. Always fall through
  to the next source.
- **Topic search misses on exact phrase** — if `/recall database migration` finds
  nothing, try shorter terms (`/recall migration`), then try `session_search` with
  individual keywords, then check memory.
- **Wrong thread scope** — `/recall` (default) scopes to the current thread. Use a
  topic phrase to search across threads.
- **Very long transcripts** — chunk before summarising if needed; the summary can still
  cover the whole transcript.
