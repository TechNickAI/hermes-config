---
name: recall-from-openclaw
description: >
  One-time bridge for fleet members migrating from OpenClaw to Hermes. Locates the
  OpenClaw transcript for the current Telegram topic, reads it, and hands the user a
  context briefing so the conversation can continue without losing its thread. Run once
  per topic after a migration; future recalls use /recall.
version: 0.2.0
license: MIT
metadata:
  hermes:
    tags: [context, migration, openclaw, telegram, gateway, recovery]
    related_skills: [recall]
    platforms: [telegram]
---

# Recall from OpenClaw

A user in a Telegram topic just migrated from OpenClaw to Hermes mid-conversation. The
Hermes side has no history yet, but the OpenClaw transcript still exists on disk. Your
job: find it, read it, brief the user so they can keep going. **Once per topic** — after
this, regular `/recall` takes over.

## The job

Use the bundled finder to locate the transcript on disk, read the tail, and synthesize
the same kind of briefing `/recall` produces — what was being worked on, what's settled,
what's open, where you left off, what to do next. End with a short question that lets
the user pick up naturally. Don't dump the raw transcript; the briefing is the
deliverable.

## The finder

The Python script is a filesystem job — walking `~/.openclaw*/agents/*/sessions/` and
parsing JSONL — not an LLM task. Use it:

```bash
python3 "$HERMES_SKILL_DIR/scripts/find_topic.py" --skip-heartbeats
```

It reads `$HERMES_SESSION_THREAD_ID` automatically and prints JSON: a `candidates[]`
array newest-first, a `primary` path, and a `transcript.tail_messages` array (last ~50KB
of real user/assistant exchanges, metadata blocks already stripped).

Useful flags:

- `--skip-heartbeats` — walk past cron/poll sessions to the first real conversation
- `--root <path>` — point at a non-standard OpenClaw install
- `--list-only` — enumerate candidates without reading the tail
- `--max-chars N` — wider tail if the user needs deeper history

## Judgment calls

- **Multiple candidates** — list them with size and date, ask which one. Don't guess
  silently.
- **No hits** — don't dead-end. Ask if it was a differently-named OpenClaw instance
  (`~/.openclaw-<something>`) and re-run with `--root`. Try `session_search` for
  Hermes-side context. Worst case, ask what they remember and reconstruct from that.
  Something is always better than "not found."
- **Not in a topic** — DMs without forum topics have no `message_thread_id`. The finder
  exits with `Missing/invalid thread_id`. Tell the user; suggest `/recall` instead.

## Pitfalls

- **Heartbeat sessions float to the top.** OpenClaw ran cron heartbeat polls in every
  topic. `--skip-heartbeats` handles this; if you forget the flag, watch for
  `primary_is_heartbeat: true` or `first_user_message: "[OpenClaw heartbeat poll]"` and
  re-run.
- **Reset transcripts are real history.** `<uuid>-topic-<tid>.jsonl.reset.<iso>` files
  are archived prior sessions, not garbage. The finder matches them — include them in
  your candidates view.
- **Treat the transcript as historical.** If it says "I'll restart `<service>` in 5
  minutes," that already happened (or didn't). Don't act on stale intent until the user
  confirms.
- **One pullover per topic.** After the briefing, Hermes session memory takes over.
  Don't re-recall every turn.

## Standalone use

If the slash command isn't reaching this skill, the script runs directly:

```bash
python3 ~/.hermes/profiles/<profile>/skills/recall-from-openclaw/scripts/find_topic.py \
  --thread-id 12345 --skip-heartbeats
```
