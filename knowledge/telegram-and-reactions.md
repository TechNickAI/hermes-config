# Telegram bot migration & "eyes" reaction in Hermes

Research notes for the OpenClaw → Hermes migration. Two questions answered, both
grounded in the Hermes source tree (`~/.hermes/hermes-agent/`).

> **Status:** Research only. No code or config changed. **Public-repo note:** This file
> lives in a public repo. Personas, instance nicknames, ports, and any path under
> `/Users/<anyone>/...` have been generalized to `~/.hermes/...`. Keep it that way when
> editing.

---

## TL;DR

1. **Reuse the same Telegram bot token.** Hermes is built for this exact handoff — it
   always calls `deleteWebhook` on connect, retries 409 Conflicts up to 3× with 10 s
   delays, and emits a fatal-error string that namechecks OpenClaw. Rotate only in the
   narrow cases listed in the decision framework below.
2. **The 👀 reaction is already a first-class Hermes feature.** Enable with one env var
   (`TELEGRAM_REACTIONS=true`) or one config key (`telegram: { reactions: true }`). No
   plugin needed. Hermes goes one beyond OpenClaw: it swaps 👀 → 👍 / 👎 on completion,
   and explicitly clears the 👀 if the user runs `/stop`.

---

# Question 1 — Reuse or rotate the Telegram bot token?

## What the Bot API actually allows

Telegram permits exactly **one** active update consumer per bot token at a time. That
consumer is either:

- a registered HTTPS webhook (`setWebhook`), or
- a long-polling client (`getUpdates`).

If a second consumer connects, the server returns
`409 Conflict: terminated by other getUpdates request` to whoever calls `getUpdates`
next. There is no "both running side-by-side" mode — the moment Hermes starts polling
with the same token, OpenClaw will start getting 409s, and vice versa.

## How Hermes' Telegram adapter handles the handoff

File: `~/.hermes/hermes-agent/gateway/platforms/telegram.py`

The adapter was clearly designed with this handoff in mind. Three concrete signals:

### Signal 1 — `deleteWebhook` on every connect

Test confirms it
(`tests/gateway/test_telegram_conflict.py::test_connect_clears_webhook_before_polling`):

```python
await adapter.connect()
bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
```

So if OpenClaw (or anything else) had a webhook registered against this token, Hermes
wipes it before its first `getUpdates` call. `drop_pending_updates=False` means
in-flight messages are preserved across the handoff — the next poll picks them up.

### Signal 2 — 409 Conflict retry loop with up to 3 × 10 s backoff

Same file, in `_handle_polling_conflict`:

```python
# Track consecutive conflicts — transient 409s can occur when a
# previous gateway instance hasn't fully released its long-poll
# session on Telegram's server (e.g. during --replace handoffs or
# systemd Restart=on-failure respawns).  Retry a few times before
# giving up, so the old session has time to expire.
MAX_CONFLICT_RETRIES = 3
RETRY_DELAY = 10  # seconds
```

If the count exceeds three, the adapter raises a fatal error with code
`telegram_polling_conflict`. The fatal-error message text explicitly mentions OpenClaw
as a likely cause (per the source string — "...another process such as OpenClaw is still
polling this token..."). That string is a deliberate ergonomic hint: the Hermes
maintainers know the typical migration scenario.

### Signal 3 — Same-machine token lock (`acquire_scoped_lock`)

`telegram.py` calls `acquire_scoped_lock('telegram-bot-token', self.config.token, ...)`
on connect. If two Hermes processes on the same host try to use the same token, the
second exits with a `telegram-bot-token_lock` fatal — preventing Hermes from fighting
itself. **This lock is in-process to Hermes**; it does **not** detect OpenClaw holding
the token. That cross-product is detected only by the 409 path above.

### Bot API call summary

| Hermes action       | Underlying Bot API call                     |
| ------------------- | ------------------------------------------- |
| `connect()` startup | `deleteWebhook(drop_pending_updates=False)` |
| Message ingest      | `getUpdates` (long-poll)                    |
| Migration conflict  | `409 Conflict` → retry 3 × 10 s → fatal     |

## Decision framework — same token or rotate?

**Default: reuse the existing token.** Reuse is the design intent and preserves the
chat-history continuity that motivates the question in the first place (Telegram pins
history to the chat, not the bot — but bot identity, username, avatar, and prior
reactions/buttons all stay coherent only if the token is reused).

**Reuse the same token when:**

- You control the OpenClaw instance and can stop it cleanly before Hermes starts.
- You want bot identity (username, profile pic, prior message reactions, inline
  keyboards still attached to old messages) to stay intact.
- You have group memberships that you don't want to re-issue invites for.
- You're operating only one Hermes process per token. (The lock is per-host; across
  hosts you must coordinate manually.)

**Rotate to a fresh bot when:**

- You want OpenClaw to keep running in parallel with the new Hermes bot during a
  soak/comparison period. (You can't share a token; you must rotate.)
- The old token may be compromised — migration is a clean time to rotate.
- You want a visually distinct bot (new name/avatar) so users see the change.
- The previous bot's command list / privacy settings / inline mode flags are wrong and
  you'd rather start from a clean BotFather slate.
- You want to keep OpenClaw's chat history archived under the old bot while Hermes
  builds fresh history under a new one.

**Multi-user / multi-instance case.** OpenClaw runs one Telegram bot per profile, with
the token in each profile's own config. So a primary instance and a partner's secondary
instance have independent tokens, and the reuse-vs-rotate decision is independent per
bot:

- Primary bot → reuse primary token in Hermes (or rotate it)
- Secondary bot → reuse secondary token in Hermes (or rotate it)

No global decision; one bot at a time.

## Recommended cutover sequence (reuse path)

1. In Hermes, set the same `TELEGRAM_BOT_TOKEN` (or `telegram: { token: ... }` in
   `config.yaml`) that OpenClaw is using.
2. **Stop OpenClaw first.** Run `openclaw gateway stop` (or `launchctl bootout` the
   launchd job) and verify the process is gone with `ps`.
3. Wait ~60 s — belt-and-suspenders for the Telegram server-side long-poll session to
   expire.
4. Start Hermes. Watch the log for one of:
   - `Telegram polling started` → success, you're done.
   - `Telegram polling conflict (n/3), will retry in 10s` → an old session hasn't fully
     released; let it retry.
   - `fatal_error_code=telegram_polling_conflict` → after 3 retries; OpenClaw (or a
     stray process somewhere) is still polling. Find it and kill it, then
     `hermes gateway restart`.

If the user wants to do parallel A/B for a few days, rotate to a second bot in BotFather
and put that token into Hermes — leave OpenClaw on the original token untouched. This is
the **only** way to run both at once.

## Things I could not verify

- Whether OpenClaw has its own `deleteWebhook` behavior at shutdown (it presumably
  doesn't, since it polls — but if you ever set a webhook against the token manually,
  Hermes will clear it; OpenClaw would not).
- Exact text of the fatal-error message — I read source paths and tests, not the raw
  string contents around line 1900-ish of `telegram.py`. I'm confident OpenClaw is named
  based on multiple references in the codebase (e.g. `hermes_cli/claw.py` has
  `_cmd_migrate` that handles `~/.openclaw/`, `~/.clawdbot/`, `~/.moltbot/` source
  directories).

---

# Question 2 — "Eyes" reaction (👀) on message receipt

## Answer: Hermes does this natively. Don't build a plugin.

The feature lives in `~/.hermes/hermes-agent/gateway/platforms/telegram.py` (section
comment: `# ── Message reactions (processing lifecycle) ──`). It is **disabled by
default** and turned on with a single env var or `config.yaml` key.

## How to enable

**Option A — environment variable:**

```bash
TELEGRAM_REACTIONS=true
```

Accepts truthy: `true`, `1`. Anything else (including unset, `false`, `0`, `no`)
disables.

**Option B — `config.yaml`:**

```yaml
telegram:
  reactions: true
```

`gateway/config.py` bridges this YAML key to the env var at load time. Env var wins if
both are set (test: `test_config_reactions_env_takes_precedence`).

## What it actually does

Hermes wires reactions to the platform's processing-lifecycle hooks
(`on_processing_start` / `on_processing_complete`) declared on `BasePlatformAdapter` in
`~/.hermes/hermes-agent/gateway/platforms/base.py`.

| Lifecycle event                          | Reaction                        |
| ---------------------------------------- | ------------------------------- |
| Message received, processing starts      | 👀 (`\U0001f440`)               |
| Processing completed successfully        | 👍 (`\U0001f44d`) — replaces 👀 |
| Processing failed                        | 👎 (`\U0001f44e`) — replaces 👀 |
| Processing cancelled (e.g. user `/stop`) | reactions cleared (no emoji)    |

This is one step richer than OpenClaw's 👀-only behavior — Hermes gives the user
persistent visual feedback on outcome, not just acknowledgment of receipt.

## Telegram Bot API call

Single call per state transition:

```python
await self._bot.set_message_reaction(
    chat_id=int(chat_id),
    message_id=int(message_id),
    reaction=emoji,   # or None to clear all bot reactions
)
```

This wraps Telegram's `setMessageReaction` (Bot API 7.0+, July 2023). Unlike Discord
(which is additive), Telegram's `setMessageReaction` **replaces** all existing bot
reactions atomically — so the 👀 → 👍 transition happens in one network round trip with
no flicker, and no separate "remove old reaction" step. Clearing is done by passing
`reaction=None`, equivalent to Bot API 10.0's `deleteMessageReaction` but supported in
`python-telegram-bot` 22.6 already.

## Implementation excerpt

```python
def _reactions_enabled(self) -> bool:
    """Check if message reactions are enabled via config/env."""
    return os.getenv("TELEGRAM_REACTIONS", "false").lower() not in {"false", "0", "no"}

async def on_processing_start(self, event: MessageEvent) -> None:
    """Add an in-progress reaction when message processing begins."""
    if not self._reactions_enabled():
        return
    chat_id = getattr(event.source, "chat_id", None)
    message_id = getattr(event, "message_id", None)
    if chat_id and message_id:
        await self._set_reaction(chat_id, message_id, "\U0001f440")

async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
    if not self._reactions_enabled():
        return
    chat_id = getattr(event.source, "chat_id", None)
    message_id = getattr(event, "message_id", None)
    if not (chat_id and message_id):
        return
    if outcome == ProcessingOutcome.CANCELLED:
        await self._clear_reactions(chat_id, message_id)
    else:
        await self._set_reaction(
            chat_id,
            message_id,
            "\U0001f44d" if outcome == ProcessingOutcome.SUCCESS else "\U0001f44e",
        )
```

## Robustness notes worth knowing

- **Silent failure on permission errors.** `_set_reaction` and `_clear_reactions` both
  wrap `set_message_reaction` in `try/except` and log at `DEBUG`. If the bot lacks
  reaction permission in a group, the reaction call fails quietly and the message still
  gets processed normally. Good UX, but it does mean a misconfigured group will look
  like "reactions aren't enabled" — check logs at DEBUG before assuming the config is
  wrong.
- **No-bot guard.** If `self._bot` is `None` (adapter not connected), the helpers return
  `False` without raising.
- **Missing IDs guard.** `on_processing_start` short-circuits if either `chat_id` or
  `message_id` is missing — relevant for synthetic events that don't originate from a
  real Telegram message (workflow runs, cron, etc.).

## Test coverage

`tests/gateway/test_telegram_reactions.py` covers:

- `_reactions_enabled()` truthiness matrix (`true`/`1` enable; `false`/`0`/`no`/unset
  disable).
- `on_processing_start` adds 👀 when enabled.
- `on_processing_start` no-ops when disabled.
- `on_processing_start` no-ops when `chat_id` / `message_id` are missing.
- `config.yaml`'s `telegram: { reactions: true }` bridges to `TELEGRAM_REACTIONS=true`
  env var (and vice versa: env wins on conflict).

Plus `tests/gateway/test_telegram_conflict.py` for the 409 / `deleteWebhook` behavior
described in Q1.

## Other platforms (bonus)

Matrix has the same feature behind `MATRIX_REACTIONS`
(`~/.hermes/hermes-agent/gateway/platforms/matrix.py`), **enabled by default** there
with the same eyes/check/cross semantics. So this is a cross-platform pattern in Hermes,
not a Telegram one-off — `on_processing_start` / `on_processing_complete` hooks on
`BasePlatformAdapter` exist precisely to support this kind of visual feedback per
platform.

## Things I could not verify

- Whether the docs site at
  `https://hermes-agent.nousresearch.com/docs/user-guide/messaging` mentions
  `TELEGRAM_REACTIONS` (I worked from source + tests, not live docs).
- Whether `reactions: true` can be scoped per-chat or only per-platform — source
  suggests platform-wide only (it reads one env var). If per-chat granularity matters,
  that would be a plugin.

---

# Summary recommendation

- **Token:** Reuse the existing OpenClaw Telegram token in Hermes. Stop OpenClaw first,
  wait ~60 s, start Hermes. The adapter will clear any stale webhook and ride out the
  brief 409 window automatically. Rotate only if you need parallel A/B, want a fresh bot
  identity, or suspect token compromise.
- **Eyes reaction:** Set `TELEGRAM_REACTIONS=true` (or `telegram: { reactions: true }`
  in `config.yaml`). Hermes already does 👀 on receipt, 👍 / 👎 on completion, and
  clears on `/stop`. No plugin needed.
