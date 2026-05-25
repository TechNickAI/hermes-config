---
name: recall-from-openclaw
description: >
  One-time bridge for fleet members migrating from OpenClaw to Hermes. Finds the
  OpenClaw transcript matching the current Telegram topic, reads it, and injects a
  context briefing so the conversation can continue without losing its thread. Run
  /recall-from-openclaw once per topic after a migration; future recalls use /recall.
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [context, migration, openclaw, telegram, gateway, recovery]
    related_skills: [recall]
    platforms: [telegram]
---

# Recall from OpenClaw

**Mission:** A user in a Telegram topic has migrated from OpenClaw to Hermes
mid-conversation. The Hermes side has no history yet, but the OpenClaw transcript still
exists on disk. Find it, read it, and hand the user back a clean context briefing so
they can keep going.

This is **one-time per topic** — once you've recalled, future turns use `/recall`
normally.

## When invoked

The user ran `/recall-from-openclaw` inside a Telegram topic (forum supergroup thread or
a DM topic that maps to a `message_thread_id`). Their previous bot was OpenClaw; their
current bot is Hermes; this topic has prior history that needs to come across.

If they pass an argument (`/recall-from-openclaw <hint>`), treat it as a search phrase
to disambiguate when multiple transcripts match.

## What to do

### 1. Discover the matching transcript

Run the bundled finder script. It reads `$HERMES_SESSION_THREAD_ID` from the gateway
environment and probes the standard OpenClaw session locations:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/find_topic.py"
```

The output is JSON. The shape you care about:

- `ok: true` plus a `candidates[]` array (newest first) and a `primary` path → you found
  at least one transcript
- `ok: false` with `error: "No OpenClaw transcript found..."` → nothing on this thread
- `ok: false` with `error: "Missing/invalid thread_id..."` → the user is not in a
  forum/DM topic (1:1 DMs without topics don't have `message_thread_id`)
- `ok: false` with `error: "No OpenClaw sessions/ directory found..."` → no OpenClaw
  install detected; ask the user where it lives and re-run with `--root <abs-path>`

The script also returns `transcript.tail_messages` — the last ~50KB of user/assistant
exchanges already extracted and ready to read.

### 2. Disambiguate if needed

If `candidates[]` has more than one entry, list them with their sizes and dates and ask
the user which one to pull from. Don't guess silently — when in doubt, ask.

### 3. Synthesize a context briefing

Read the `transcript.tail_messages` from the JSON output (re-run without `--list-only`
if step 1 was list-only). Pull out:

- **What was being worked on** — the topic / project / decision under discussion
- **Key decisions or conclusions** — what's settled
- **Open threads** — questions, unresolved blockers, pending approvals
- **What the last exchange was** — what the user was about to do next, or what they were
  waiting on you for
- **Anything the previous agent committed to** — promises, deadlines, follow-ups

Render the briefing exactly like `/recall` does:

> **Recalled context from OpenClaw — [topic title]**
>
> **Source:** `<path-to-jsonl>` (last active <date>)
>
> **What was being worked on:** ...
>
> **Key decisions / conclusions:** ...
>
> **Open threads:** ...
>
> **Where we left off:** ...
>
> **What to do next:** ...
>
> _This is a one-time pullover from OpenClaw. Future recalls in this topic should use
> /recall._

### 4. Hand off

End with a short question that lets the user pick up naturally: "Want me to keep going
from where we left off, or are you switching gears?" Don't dump the full transcript on
them — the briefing is the deliverable.

### 5. Never dead-end

If the finder script returns no hits, **don't stop there**. Fall through:

1. Ask the user if the prior conversation might have been on a different OpenClaw
   instance (e.g. they renamed `~/.openclaw-foo`), and re-run with
   `--root /path/to/sessions`.
2. If they remember roughly what was discussed, run `session_search` against Hermes
   sessions too — maybe some context already migrated.
3. As a last resort, ask them what they remember and reconstruct a working summary from
   that. That's still more useful than "no results found."

## Pitfalls

- **DMs without topics have no thread_id.** OpenClaw + Telegram only attach
  `message_thread_id` in forum supergroups and DM topics. A plain 1:1 DM won't have one,
  and the finder will exit with `Missing/invalid thread_id`. Tell the user that
  case-by-case — they may want `/recall` (session-based) instead.
- **Multiple instances.** Users sometimes ran `~/.openclaw` and `~/.openclaw-<name>` in
  parallel. The finder auto-probes both, but if neither matches and the user knows
  there's a third location, pass `--root` explicitly.
- **Large transcripts.** The 50KB tail cap is intentional — it keeps context spend
  reasonable. If the user needs deeper history, they can ask follow-up questions and you
  can re-read more of the file with a wider `--max-chars`.
- **Stop spamming the briefing.** Run this **once** per topic. After the briefing is
  injected, Hermes' own session memory takes over. Don't re-recall on every turn.
- **Heartbeat/cron sessions float to the top.** OpenClaw ran scheduled heartbeat polls
  in every topic. If `primary_is_heartbeat: true` is in the script output (or if
  `first_user_message` is `[OpenClaw heartbeat poll]`), the newest session is a cron
  artifact, not a real conversation. Re-run with `--skip-heartbeats` to automatically
  walk past cron sessions to the next substantive candidate. You can also run
  `--list-only` and pick the largest candidate manually.
- **Don't act on stale state.** The transcript ended at some point in the past. If it
  mentions "I'll restart Drishti in 5 minutes," that already happened (or didn't) —
  treat the prior intent as historical, not as a live to-do, until the user confirms.

## CLI escape hatch

If the slash command isn't reaching this skill yet, the script is callable standalone:

```bash
python3 "${HERMES_SKILL_DIR}/scripts/find_topic.py" --thread-id 12345 --max-chars 100000
```

Outside a skill context (where `$HERMES_SKILL_DIR` isn't set), use the absolute path to
wherever your Hermes profile stores skills, e.g.
`~/.hermes/profiles/<profile>/skills/recall-from-openclaw/scripts/find_topic.py`.

Useful for debugging from a terminal or running outside a Telegram session.
