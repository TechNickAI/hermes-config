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

**This skill documents the `/recall` slash command**, which is built into the Hermes
gateway. The command is available on any platform where the gateway runs (Telegram,
Discord, Slack, etc.).

## When to use

- After `/new` — you want the agent to remember what you were working on
- After a context overrun forced a session reset
- Switching topics and wanting to pull in context from an earlier thread
- Searching across sessions for something you discussed before

## Prerequisites

- Hermes gateway running with a messaging platform connected (Telegram, Discord, etc.)
- `hermes-agent` ≥ the commit that introduced `/recall`
  ([NousResearch/hermes-agent#XXXX](https://github.com/NousResearch/hermes-agent))
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
  from the middle. Run `/recall 2` to split across two session boundaries if precision
  matters.
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

## Origin

Designed to make `/new` feel safe — context overruns and intentional resets shouldn't
mean losing the thread. The pattern was: a sub-agent reads the raw transcript and
produces a structured briefing, injected as a user message so the next turn picks it up
naturally without any special prompt engineering.
