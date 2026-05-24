---
name: recall
description: >
  Restore context from a prior Hermes session into a fresh one. Run /recall after /new
  to pick up where you left off — especially useful after a context overrun forced you
  to start a new session.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [context, session, recovery, telegram, gateway, productivity]
    related_skills: [cron-healthcheck, pr-review-sweep]
---

# Recall

Restores context from a prior Hermes session into the current one. Useful any time you
hit `/new` (intentionally or because a context overrun forced it) and want the agent to
know what was being worked on.

**This skill documents the `/recall` slash command**, which ships with `hermes-agent`
but is not yet in a tagged release — see Prerequisites. The command is available on any
platform where the gateway runs (Telegram, Discord, Slack, etc.).

## When to use

- After `/new` — you want the agent to remember what you were working on
- After a context overrun forced a session reset
- Switching topics and wanting to pull in context from an earlier thread
- Searching across sessions for something you discussed before

## Prerequisites

- Hermes gateway running with a messaging platform connected (Telegram, Discord, etc.)
- `hermes-agent` from the `feature/recall-command` branch
  ([TechNickAI/hermes-agent](https://github.com/TechNickAI/hermes-agent/tree/feature/recall-command))
  — or the upstream PR once merged into `NousResearch/hermes-agent`
- A prior session in the same thread (for thread mode) or any prior session (for topic
  mode)

## Usage

### Default — restore last session in this thread

```
/recall
```

Finds the most recent session in the current thread/topic, summarises it via a
sub-agent, and injects the summary as context. The agent now knows what was being worked
on. You can keep going immediately.

### Restore last N sessions

```
/recall 3
```

Pulls the last 3 sessions in this thread and summarises them all into a single context
block.

### Time window — everything in the last 24 h / 7 d

```
/recall 24h
/recall 7d
/recall 2w
/recall 1m
```

Finds all sessions active within the window and summarises them.

### Topic search — find sessions about a specific subject

```
/recall hex migration
/recall gateway config
/recall spool stuck
```

Full-text search across all your sessions for the phrase. Returns the most relevant
matches regardless of which thread they came from.

### Topic search + time scope

```
/recall hex migration 7d
/recall gateway config 24h
```

Scopes the FTS search to sessions active within the window.

## What the command does

1. Parses your args to determine mode (thread / window / topic)
2. Finds the matching prior session(s) via `SessionDB`
3. Loads each transcript
4. Spawns a sub-agent to read through the transcript and produce a structured summary:
   - What was being worked on
   - Key decisions made
   - Open questions / blockers
   - Suggested next steps
5. Injects that summary as a `user`-role message into the current session transcript
6. Replies with the summary so you can read it and confirm the agent is oriented

## The /new tip

After every `/new` (session reset), if a prior session exists in the same thread, the
agent automatically appends:

> 💡 Run /recall to restore context from your prior session.

So the breadcrumb is visible right when you need it — no need to remember the command
mid-flow.

## Modes reference

| Syntax                | Mode           | What it finds                    |
| --------------------- | -------------- | -------------------------------- |
| `/recall`             | thread         | Last session in this thread      |
| `/recall 3`           | thread         | Last 3 sessions in this thread   |
| `/recall 24h`         | window         | All sessions active in last 24 h |
| `/recall 7d`          | window         | All sessions active in last 7 d  |
| `/recall <phrase>`    | topic          | FTS search across all sessions   |
| `/recall <phrase> 7d` | topic + window | FTS search scoped to last 7 d    |

## Pitfalls

- **Cold starts** — if you just installed Hermes and have no prior sessions, `/recall`
  will say "No prior sessions found." That's correct; there's nothing to pull in yet.
- **Very long sessions** — transcripts over ~40 K tokens are chunked before
  summarisation; the summary will reflect the whole transcript but may miss fine detail
  from the middle. There is no workaround within `/recall` for this — if deep precision
  matters, use a model with a larger context window (`/model`) before running `/recall`.
- **Topic search misses** — FTS5 searches exact substrings by default. If
  `/recall database migration` returns nothing, try `/recall migration` (shorter phrase,
  more matches).
- **Wrong thread** — `/recall` (default) scopes to the current Telegram topic / Discord
  thread. If you moved to a new topic, the prior session is in a different `session_key`
  and won't appear. Use `/recall <phrase>` (topic mode) to search across threads.
- **Summariser model** — the sub-agent that reads the transcript uses whatever model is
  configured for your session. On a slow or cheap model, the summary may be brief.
  Switch to a smarter model with `/model` before running `/recall` if depth matters.

## Installing the gateway command

The `/recall` command ships with `hermes-agent`. To verify it's available:

```
/help
```

Look for `recall` in the Session section. If it's missing, update Hermes:

```bash
hermes update
```

Then restart the gateway:

```bash
hermes gateway restart
```

## Raw Telegram history fallback (tgcli)

`/recall` reads Hermes's own `state.db` session transcripts. That works perfectly when
the prior context was a single Hermes session — but there are cases where the agent
genuinely **never saw** the messages you want to restore:

- A different agent / bot was the one talking (e.g. you switched personas mid-thread)
- The gateway was down while you were typing notes to "future you"
- You want context from before you ever installed Hermes on this box
- The session DB was rotated / cleared

For those cases, point the agent at the raw Telegram history via
[`tgcli`](https://github.com/kaosb/tgcli) — an MTProto user-account CLI that mirrors
your real Telegram message store into a local SQLite database. This is read-as-you,
not as a bot, so it sees everything you can see in the Telegram client.

### Install + auth

```bash
# Install (Go binary; check the tgcli repo for current install method)
# Requires TGCLI_APP_ID and TGCLI_APP_HASH env vars from https://my.telegram.org/apps
tgcli login
# Walk the phone + code prompts. Session persists in ~/.tgcli/.
```

### Sync the chat(s) you want recallable

```bash
# Sync a single chat by username, phone, or numeric ID
tgcli sync --chat <chat_id_or_username> --msgs-per-chat 200

# Or sync the recent N messages across all chats (default 100 per chat)
tgcli sync --msgs-per-chat 100
```

Messages land in `~/.tgcli/tgcli.db` (table: `messages`, columns include
`chat_id, chat_name, msg_id, sender_id, sender_name, ts, from_me, text`).

### Use as a recall source

When `/recall` returns "no prior sessions found" but you know there's context in the
Telegram thread, fall back to a sub-agent prompt of the form:

> Pull the last 50 messages from chat `<chat_id>` out of `~/.tgcli/tgcli.db`. Summarise
> what was being discussed, key decisions, open threads, and what I most likely meant
> by my last message. Inject the summary back as context so the agent knows where I
> left off.

The query the sub-agent should run:

```sql
SELECT sender_name, datetime(ts, 'unixepoch') AS when_, text
FROM messages
WHERE chat_id = '<chat_id>'
ORDER BY ts DESC
LIMIT 50;
```

(Re-order ASC for display; DESC + LIMIT for "the most recent N".)

### Why this isn't the default

- `/recall`'s session-DB path is faster, cheaper, and contains the agent's own actions
  and tool outputs — far richer than raw text.
- The tgcli path only catches the user-visible chat surface; it won't show what the
  agent did, only what was said.
- Use tgcli history as a **fallback** when sessions are gone, or as a **supplement**
  when you need to restore context that lived in the chat but never made it into a
  Hermes session (notes to self, decisions made in a sister thread, etc.).

### Forum-supergroup topics caveat

Telegram has two flavours of "topic":

1. **DM lanes (incl. Hermes-faked DM topics)** — the chat is a 1:1 DM
   (positive chat_id). Telegram has no server-side topic for DMs; the whole chat is one
   flat stream. tgcli pulls every message into one timeline indexed by `chat_id` — no
   topic filtering needed.
2. **Forum-supergroup topics** — supergroups with the Topics feature enabled
   (negative `-100…` chat_ids). Each message has a `message_thread_id` that scopes it
   to a topic lane.

For flavour 1 (the common `/new` recovery case), tgcli works as-is.

For flavour 2, **upstream tgcli does NOT expose `message_thread_id`** — neither as a
column in the local DB nor as a `--topic` flag on `msg ls` / `export`. The underlying
Go library (`gotd/td`) supports forum topics fully, so this is a fixable upstream gap
rather than a fundamental limit. Until that patch lands, raw-history recall in a forum
supergroup will return the whole chat, not just the topic lane you were in. Filter
manually in the sub-agent's prompt ("only consider messages whose text references X")
or fall back to `/recall <phrase>` (Hermes-side FTS search) for topic-scoped recovery.

See also the diagnostic recipe in any agent-side skill that documents the three
flavours of Telegram "topic" (forum-supergroup, native DM topic, gateway-faked DM
lane) — knowing which flavour you're looking at decides whether the topic dimension
is even meaningful.

### Pitfalls

- **Bot-token chats are blind to tgcli.** tgcli authenticates as your user account,
  so it sees the chats your user is in. A bot-only channel where the user isn't a
  participant won't appear.
- **Sync is on-demand.** tgcli doesn't tail live. Run `tgcli sync --chat <id>` (or a
  whole-fleet sync) right before `/recall` if you want the freshest messages.
- **Session file is sensitive.** `~/.tgcli/` contains a working Telegram user-account
  session — treat it like an SSH key. Don't sync it to Dropbox unencrypted.

## Origin

Designed to make `/new` feel safe — context overruns and intentional resets shouldn't
mean losing the thread. The pattern was: a sub-agent reads the raw transcript and
produces a structured briefing, injected as a user message so the next turn picks it up
naturally without any special prompt engineering.
