---
name: recall
description: >
  Restore context from prior sessions, memories, and transcripts. Run /recall after /new
  to pick up where you left off, and also use when a bare-pronoun follow-up like "ship
  it", "do it", or "send that" likely points at a prior-session artifact. Designed to
  never dead-end — if one source has nothing, keep searching others until you have a
  useful picture.
version: 0.2.1
license: MIT
metadata:
  hermes:
    tags: [context, session, recovery, telegram, gateway, productivity]
    related_skills: [cron-healthcheck, pr-review-sweep]
---

# Recall

**Mission:** Restore as much relevant context as possible. Never return empty-handed. If
one source has nothing, try the next. Synthesize what you find into a clear briefing so
the conversation can continue naturally.

## When invoked

The user ran `/recall` (possibly with a topic hint like `/recall project X status` or
`/recall alice relationship analysis`) after a `/new` reset or context overrun. They
want to pick up where they left off. Your job is to find that thread and hand it back to
them.

**Implicit recall (no `/recall` command):** A common case is a bare-pronoun follow-up
right after a daily reset or `/new` — e.g. "ok deliver this", "ship it", "send that",
"do it", "go ahead". The antecedent ("this", "that", "it") points at an artifact from
the previous session that you no longer have in context. Treat these as recall triggers:
go straight to step 2b (raw jsonl tail of the most recent prior session) and find the
last assistant artifact — usually a draft, a ranked list, a proposed plan, or a
pending-approval message. Then either confirm what they mean before acting, or surface
the candidate artifact verbatim so they can say yes/no. Do **not** guess and execute on
a deictic without verifying — the wrong "this" is worse than a clarifying question.

## What to do

Work through these sources in order. **Do not stop at the first miss** — keep going
until you have enough to give a useful briefing, or until all sources are exhausted.

### 1. Session search (start here)

Use `session_search` to find prior sessions matching the topic or recent activity:

- If the user gave a topic hint, search for it: `session_search(query="<topic hint>")`
- Also search for related terms if the first query is thin — break the phrase apart, try
  synonyms
- Try a broad recency search with no query to see what was recently worked on:
  `session_search()` (no args = recent sessions)
- Look at multiple results, not just the top one

### 2. Hermes session DB (if session_search has matches)

If `session_search` returns matching sessions, load the transcript for the most relevant
one(s). Read through it and extract:

- What was being worked on
- Key decisions or conclusions reached
- Open questions or blockers left unresolved
- What the user's last message / intent was

### 2b. Raw transcript (when summaries are too lossy)

`session_search` returns **summaries**, not transcripts. When the previous session ended
mid-task and you need exact state — the actual file paths touched, the last todo state,
the precise question the assistant was waiting on, the diff that was proposed but not
yet applied — the summary will be too coarse. Go to the raw transcript.

Transcripts live in a SQLite state store. **Resolve it from the active Hermes home, not
a hardcoded path** — a named profile keeps its own store, and reading the wrong one
returns an empty result that looks exactly like a missing session:

```
$HERMES_HOME/state.db          # active profile, when HERMES_HOME is set
~/.hermes/state.db             # root agent, when it is not
```

Read the tail of a session directly:

```python
import sqlite3, os

# Resolve the store the way the running agent does, or a profile session
# will appear to be missing when it is simply in another file.
home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
db = os.path.join(home, "state.db")

con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)   # read-only: never disturb a live session

session_id = "..."   # from session_search
rows = con.execute("""
    SELECT role, tool_name, content
    FROM messages
    WHERE session_id = ?
    ORDER BY id DESC
    LIMIT 12
""", (session_id,)).fetchall()

for role, tool, content in reversed(rows):
    print(f"=== {role}{'/' + tool if tool else ''} ===")
    print(str(content)[:3500])
```

Open the DB **read-only** (`mode=ro`). A live session may be writing to it.

If a lookup comes back empty, check the other store before concluding anything is gone.
`find $HOME/.hermes -maxdepth 3 -name state.db` lists every store on the host.

Useful columns: `messages.role`, `content`, `tool_name`, `timestamp`, `session_id`;
`sessions.id`, `source`, `model`, `started_at`, `ended_at`, `message_count`. The store
also carries an FTS index, which is what `session_search` queries — prefer the tool over
hand-rolled SQL for search, and use SQL for exact tail recovery.

**When this beats session_search:**

- Compaction failed mid-session and the summary is a stub
- A rollout / multi-step task paused mid-sequence; you need the exact "where"
- The last assistant message was a pause-for-approval; you need the proposed diff
  verbatim
- Tool outputs (which summaries drop) hold the recon data you'd otherwise re-run

**Legacy per-profile JSONL.** Older installs wrote
`~/.hermes/profiles/<profile>/sessions/<session_id>.jsonl`. Those files may still exist
but are **not** written by current versions, so treat them as a historical archive only.
Checking them first is a common way to conclude a session is "missing" when it is
present in `state.db` — verify against the DB before reporting anything absent.

**Finding a session:** `session_search` returns `session_id`; query `state.db` with it
directly. The shell sandbox sometimes resolves `~` to a fake home — expand `$HOME`
explicitly (`os.path.expanduser` in Python) so reads always hit the real home directory.

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
> **What was being worked on:** ... **Key decisions / conclusions:** ... **Open
> threads:** ... **What to do next:** ...

Inject this briefing into the session so it's visible. Then ask if the user wants to
pick up from there or if they need anything clarified.

If you genuinely found nothing across all sources, say so plainly and offer to help
reconstruct — don't just say "no results found." Ask: what do you remember about it?
When was it roughly? That's still more useful than a dead end.

## The /recall command

`/recall` is a gateway slash command. As of the 2026-05-25 rewrite, args are
**free-form** — no rigid mode parsing. Python gathers a candidate pool (parent-chain
sessions, FTS hits if the args contain letters, recent same-platform sessions for
backfill), then one LLM call interprets the args and writes the briefing. See
`gateway/recall.py` for the implementation.

Common invocations (the agent decides what they mean):

| Syntax                | What the agent does                             |
| --------------------- | ----------------------------------------------- |
| `/recall`             | Recent in-thread sessions (parent chain)        |
| `/recall 3`           | Last few in-thread sessions                     |
| `/recall 7d`          | Sessions whose age fits the window              |
| `/recall <phrase>`    | Topic search (in-thread + cross-thread via FTS) |
| `/recall <phrase> 7d` | Topic + window, combined                        |

If you're editing `gateway/recall.py`, keep this principle: **Python = mechanical
candidate gathering. LLM = judgment.** Don't reintroduce regex mode parsing or a
two-stage hunter+summarizer — that's the design the 2026-05-25 rewrite explicitly
removed. The maintainer's framing: _"way too complicated to do all the parsing in
Python. Just have the LLM do the work."_

**After `/new`**, if a prior session exists in the same thread, the agent automatically
appends:

> 💡 Run /recall to restore context from your prior session.

## Raw Telegram history fallback (tgcli)

Use when sessions are gone, predate Hermes, or were with a different bot.

`tgcli` is an MTProto user-account CLI that mirrors your Telegram history into local
SQLite. It reads as you — not as a bot — so it sees everything your Telegram client
sees.

### Install + auth

```bash
# Requires TGCLI_APP_ID and TGCLI_APP_HASH from https://my.telegram.org/apps
tgcli login
```

### Sync

```bash
tgcli sync --chat <chat_id_or_username> --msgs-per-chat 200
```

Messages land in `~/.tgcli/tgcli.db` (table: `messages`, columns:
`chat_id, chat_name, msg_id, sender_id, sender_name, ts, from_me, text`).

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
- **Wrong thread scope** — `/recall` (default) scopes to the current thread. Use a topic
  phrase to search across threads.
- **Very long transcripts** — chunk before summarising if needed; the summary can still
  cover the whole transcript.
