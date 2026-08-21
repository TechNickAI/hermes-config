---
name: telegram-agent-steward
description: >
  Use when an agent's Telegram rooms have become unreadable — walls of text, cron output
  nobody reads, progress bubbles burying real findings, or an owner who returns to
  hundreds of unread messages and cannot tell what needs a decision. Sweeps an agent's
  OWN messages: deletes UI ephemera, escalates repeated unacknowledged alarms instead of
  hiding them, and leaves substantive content alone. Also fires on "clean up the
  channel", "summarize what I missed", "too many messages", "I wake up to walls of
  text", and "the bot is spamming me".
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags:
      [telegram, noise, cleanup, notifications, alarms, cron, forum-topics, curation]
    # referenced but not shipped here: agent-notification-suppression,
    # capacity-monitoring-and-alerting — the skill degrades gracefully without them
    related_skills: []
---

# Telegram Agent Steward

## Overview

An agent that talks a lot makes its own room unreadable. The steward walks the agent's
rooms on a schedule and leaves behind something a human can actually scan: UI ephemera
deleted, repeated alarms made louder, real content untouched.

**The counterintuitive finding this skill is built around:** in a measured 7-day sample
of a live fleet (8,663 agent messages), the most-repeated messages were not noise — they
were **unacknowledged alarms**. A severity-1 trading halt reprinted 54 times over 95
hours. A "positions are not being watched" monitor failure reprinted 29 times over 5
days. The owner sent 209 messages in that window and never responded to either.

A naive deduplicator would have deleted the evidence that anything was wrong.

> **Repetition is a severity signal, not a noise signal.** The same alarm 30 times is
> how something gets ignored, not how it gets noticed. Escalate before you ever consider
> collapsing.

## When to Use

- An owner says they wake up to walls of text, or cannot tell what needs them
- Cron output is burying real findings
- A room has more agent messages than human ones by an order of magnitude
- Someone asks to "clean up", "summarize", or "delete what I don't need"

Do **not** use this to fix a noisy _job_ — that is `agent-notification-suppression`
(prompt contracts and `[SILENT]`). The steward cleans up what was already sent;
suppression stops it being sent. Do both, suppression first.

## Hard constraints (verified live, not assumed)

These are Telegram platform facts. Probe them yourself before designing around anything
else:

| Constraint                                                         | Consequence                                                                               |
| ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| Bots can only delete their **own** messages                        | Cleanup must be per-agent; no central janitor                                             |
| Bots cannot delete anything **older than 48h**                     | Hourly cadence is mandatory; miss the window and it is permanent                          |
| Bots **cannot read history** at all                                | Reading requires a user session (telethon); `getUpdates` returns 0 against a live gateway |
| A bot may set **exactly one** reaction, from a fixed ~34 emoji set | `✅` and `👀` are rejected with `REACTION_INVALID`; `🔥 👍 💯 🥱` work                    |
| Forum topic **name and icon** are editable live                    | The topic title can be an ambient status bar                                              |
| `GetForumTopicsRequest` takes `peer=`, not `channel=`              | It lives under `functions.messages`, not `functions.channels`                             |
| `ForumTopic.read_inbox_max_id` is a per-topic **read watermark**   | This is the only reliable "has the owner seen it" signal                                  |

**Presence (`UserStatus`) is a trap.** If you read it through the owner's own telethon
session, it always reports `UserStatusOnline` — because that session _is_ the owner
online. It tells you nothing. Use read watermarks instead.

## Architecture

Two credentials with different reach, and the job needs both:

- **User session (telethon) = the eyes.** Enumerates topics, reads history, supplies
  read watermarks.
- **Agent's own bot token = the hands.** Deletes, reacts, pins, edits.

Install **one job per agent, on its own host**, cleaning only its own rooms. This is not
a preference — a bot cannot even resolve a chat it is not a member of
(`Bad Request: chat not found`).

## Walking topics without redoing work

Two independent gates, both required:

1. **Per-topic cursor** in SQLite. Compare `ForumTopic.top_message` against the stored
   cursor; if nothing is newer, skip the topic without fetching a single message. Pass
   `min_id=cursor` to `iter_messages` so the server only returns new messages.

2. **Skip unread topics entirely.** If `unread_count > 0`, the owner has not caught up —
   deleting or collapsing there destroys messages before they are ever seen. Wait.

**Pitfall that makes the cursor useless:** only saving a cursor when a topic yielded
messages. Dormant topics then never earn one and are re-walked forever. Measured: 6 of
22 topics skippable instead of 22. Always record the cursor at the topic head, even when
the batch was empty.

Steady state on a real room: **0 topics walked, 22 skipped.**

## Classification

| Class           | Rule                                           | Action                             |
| --------------- | ---------------------------------------------- | ---------------------------------- |
| `keep-critical` | Matches machine-emitted alarm shapes           | Never delete; escalate if unacked  |
| `ephemeral`     | Whole-message match against known UI templates | Archive, then delete               |
| `routine`       | Everything else                                | Held unless explicitly allowlisted |
| held            | Media, empty, service messages, unread         | Never touched                      |

**Anchor the alarm regex to machine-emitted shapes, not prose.** A first version matched
any message containing "halt" or "escalate" and flagged 385 ordinary conversational
messages as critical. Require ALL-CAPS tokens, line starts, or structured cron
emissions. Test both directions: real alarms must match, and ordinary sentences about
those alarms must not.

**Do not classify ephemera by first character.** Match the whole message shape. And
beware `✍` — U+270D, not U+270F; a character-class miss silently leaves hundreds of
messages unclassified.

## Safety model

Deletion is irreversible. Four properties, all load-bearing:

1. **Allowlist, not blocklist.** Only patterns with no information _by construction_ are
   delete-eligible. Unrecognized output is held.
2. **Archive before delete, verified.** Write JSONL, `flush()` + `os.fsync()`, then
   re-read and confirm every record round-trips before deleting anything. File
   readability alone is not proof — parse each line and match the count.
3. **Never auto-delete media.** A text-only archive turns a chart-only alert into an
   empty string. Hold anything with `media`.
4. **Dry-run by default.** `--apply` is required to mutate, and dry-run must not write
   to the archive either.

Never delete: human messages, anything the owner replied to, pinned messages, service
messages, unread messages, or the newest member of any cluster.

## Escalation, not suppression

For each cluster of identical messages:

- **Unacknowledged and (critical or repeated ≥5 times over ≥6h)** → react `🔥`, record
  in state, and **never collapse**.
- **Acknowledged critical** → react `👍`.
- **Benign repeats** → collapse only if the signature is in an operator-approved
  allowlist, and post a survivor line stating the count explicitly (`⟳ 14× over 5h`),
  never a bare emoji that reads as "handled".

**Escalation state must outlive the deletion window.** Deletion expires at 48h; the real
incident ran 95 hours. Persist first-seen, count, and ack state in SQLite or the tool
stops escalating exactly when it matters most.

## Verification

- [ ] Alarm regex tested both directions against real message text
- [ ] Second run skips topics the first run swept (cursor proven)
- [ ] Unread topics skipped
- [ ] Archived record count matches deleted count; every line parses
- [ ] Media and service messages held, not deleted
- [ ] A known repeated alarm escalates rather than collapsing
- [ ] Dry-run leaves both Telegram and the archive untouched

## Pitfalls

1. **Deduplicating alarms.** The headline failure mode. Repetition means ignored, not
   noisy.
2. **Trusting presence.** The owner's own session always reads Online.
3. **Cursor only on non-empty batches.** Dormant topics re-walked forever.
4. **Digit-normalized cluster signatures.** Collapsing `order 851` with `order 902`
   merges distinct events. Cluster on exact canonical text.
5. **`channel=` on `GetForumTopicsRequest`.** It is `peer=`, under `functions.messages`.
6. **Assuming a reaction set.** Probe it; `✅` is rejected.
7. **Cleaning another person's room.** An agent cleans its own output only, and never
   posts status into someone else's support room without that owner's consent.
