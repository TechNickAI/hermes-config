---
name: omnirouter
description: >
  Use when operating, configuring, migrating to, or auditing OmniRoute — the
  self-hosted multi-provider LLM router that fronts Claude, OpenAI/Codex, Gemini,
  OpenRouter, and local models behind one OpenAI-compatible (and Anthropic
  Messages-compatible) endpoint. Covers pointing a Hermes agent at OmniRoute,
  importing OAuth/api-key provider credentials via the encrypt-on-write HTTP API
  (no re-login), the decisive refresh-token liveness checks, end-to-end
  verification that proves an imported credential actually works, and the
  provider-pool / priority model. Also use when diagnosing an agent that reports a
  provider-flavored error (401/402/429/billing) that is really a router fallback
  step, or answering "which provider/model served this request?" — the answer is
  in the router's call logs, not the agent's self-report.
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [omniroute, router, llm, providers, oauth, migration, model-routing, devops]
    related_skills: [hermes-agent, native-mcp]
---

# OmniRoute

**Mission:** Operate OmniRoute — a self-hosted LLM router that aggregates multiple
upstream providers (Claude OAuth, OpenAI/Codex OAuth, Gemini, OpenRouter, local
model servers) behind a single endpoint and exposes both an OpenAI-compatible
(`/v1/chat/completions`) and an Anthropic Messages-compatible (`/v1/messages`)
surface. Hermes points at OmniRoute as a custom provider; OmniRoute handles
upstream auth, token refresh, fallback, and per-provider attribution.

OmniRoute stores provider credentials **encrypted at rest** (keyed by its own
`STORAGE_ENCRYPTION_KEY`) and refreshes OAuth tokens automatically. You administer
it through an HTTP API guarded by a management key.

## When to use

- Pointing a Hermes agent at OmniRoute for model routing.
- Importing provider credentials (Claude/Codex OAuth, OpenRouter/api-key) into
  OmniRoute without forcing a human re-login.
- Verifying that an imported credential actually works upstream (not just that a
  row exists).
- Diagnosing a provider-flavored error that is really a fallback step in the pool.
- Answering "which provider/account/model served this request?" from router logs.

## Pointing Hermes at OmniRoute

Use the mapped `providers:` dict form (NOT the list-form `custom_providers:`, which
the OpenClaw migrator emits and Hermes runtime warns about). Two provider blocks —
one per API shape your router exposes:

```yaml
model:
  default: chat
  provider: custom:omniroute-anthropic
  base_url: http://<router-host>:<port>
  api_mode: anthropic_messages
  context_length: 200000

providers:
  omniroute:
    name: OmniRoute OpenAI Compat
    base_url: http://<router-host>:<port>/v1
    key_env: OMNIROUTE_KEY
    api_mode: chat_completions
    models:
      chat: { context_length: 200000 }
      think: { context_length: 200000 }
      work: { context_length: 200000 }
      simple: { context_length: 200000 }
      cheap: { context_length: 128000 }
  omniroute-anthropic:
    name: OmniRoute Anthropic Compat
    base_url: http://<router-host>:<port>
    key_env: OMNIROUTE_KEY
    api_mode: anthropic_messages
    models:
      chat: { context_length: 200000 }
      think: { context_length: 200000 }
      work: { context_length: 200000 }
      simple: { context_length: 200000 }
      cheap: { context_length: 128000 }
```

Store the routing key in `~/.hermes/.env` as `OMNIROUTE_KEY`. The `key_env:` value
must match in both provider blocks. Note the **routing key** (used by clients to
call the router) is distinct from the **management key** (`OMNIROUTE_API_KEY` in
the router host's `.env`, used for `/api/*` admin calls).

### Model aliases

OmniRoute deployments commonly expose semantic aliases — `chat`, `think`, `work`,
`simple`, `cheap` — that map to concrete upstream models, plus provider-prefixed
direct IDs (e.g. `claude/...`, `codex/...`, `openrouter/...`). Confirm the live set
with `GET /v1/models` (routing key) before wiring aux slots.

## Admin API essentials

All `/api/*` calls require `Authorization: Bearer $OMNIROUTE_API_KEY` (the
management key from the router host's `.env`). Without it they return 401.

- **List providers:** `GET /api/providers` → returns `{"connections":[...]}`. It is
  **wrapped**, not a bare array and not under `.providers`/`.data`. Parse
  `body["connections"]` or you will read zero rows on a populated router. Each row:
  `id, provider, authType, name, email, priority, isActive, expiresAt`.
- **Import Claude OAuth:** `POST /api/providers/claude-auth/import`
- **Import Codex OAuth:** `POST /api/providers/codex-auth/import`
- **Import generic api-key (OpenRouter etc.):** `POST /api/providers` with
  `{provider, authType:"apikey", name, apiKey}`
- **Delete a connection:** `DELETE /api/providers/<id>`
- **Per-connection refresh:** `POST /api/providers/<id>/refresh` (note: for
  rotating-refresh providers this may intentionally **skip** and report
  `"skipped":true` to avoid token-family revocation — that is not a failure).

### Import body shape (easy to get wrong)

The credential is wrapped in a `source` envelope; a bare `{claudeAiOauth:{...}}`
returns HTTP 400 `"source: expected object"`:

```json
{
  "source": { "kind": "json", "json": {
      "claudeAiOauth": {
        "accessToken": "...", "refreshToken": "...",
        "expiresAt": "<ISO string or epoch ms>",
        "scopes": ["user:inference", "user:profile"]
      }
  }},
  "name": "Account label",
  "overwriteExisting": false
}
```

`scopes` is an ARRAY in OmniRoute. A successful import appends a NEW connection to
the provider pool at the next free `priority` and does not clobber existing
accounts unless `overwriteExisting` matches.

If Claude import returns `409 identity_unverified` ("could not verify the account
identity … pass overwriteExisting: true"), it means the credential blob carried no
email/account UUID to bootstrap from. `overwriteExisting: true` is acceptable
**only after** you have confirmed there is no existing matching account it would
clobber.

## Credential liveness — the part everyone gets wrong

**Row presence and `isActive` mean NOTHING about whether a credential works.** A row
can show `isActive=1` with access/refresh/id tokens all present and a recent
`updatedAt`, yet the refresh grant is dead at the source. Only an upstream proof is
decisive. Three traps that look like proof but are not:

1. **`POST /api/providers/<id>/test` returning `{"valid":true,...}` is a LOCAL
   structural check** (`source:local`, `latencyMs:1`) — it never hit the upstream
   provider.
2. **`testStatus: active` on a freshly-inserted row** is the insert default, not an
   upstream result.
3. **A generic `/v1/chat/completions` "pong"** routes across the WHOLE pool — if the
   host has other accounts for that provider, the call may have been served by a
   sibling credential, not the one you imported.

### The two decisive proofs

**(a) The `expires_at` JUMP (token-free).** The import body carries the source's
STALE access-token expiry (often weeks old). After OmniRoute makes a real upstream
call it uses the REFRESH token to mint a fresh access token and rewrites
`expires_at` to ~now + `expires_in` (Claude: 8h / `expires_in:28800`). Read the
row's `expires_at` before and after the first live call. A forward jump from a
stale date to now+Nh is unforgeable proof the refresh grant is alive.

**(b) Attributed call via `connection_id`.** To prove a SPECIFIC imported row works
(not a sibling), force routing to it: temporarily raise its `priority` above the
others for that provider, make one direct provider-prefixed call (e.g.
`claude/<model>` or `codex/<model>`), then read the router's call-log table and
confirm the new entry's `connection_id` equals your imported row's id with status
200. **Always snapshot and restore the original priorities afterward** — ideally by
id, since a naive restore can reorder the pool.

### Probe each provider separately

Liveness is per-grant, not per-box. Providers on the same router expire
independently. Probe by the model PREFIX that pins each underlying grant, not a
semantic alias (aliases/combos fall back across providers and can mask a dead one).
A box can have a live Claude grant and a dead Codex grant simultaneously. If a
refresh grant is dead at the source, importing it just copies a dead token — the
human must re-auth that provider; it is not a router bug.

### Stale snapshot trap

If a host previously ran a different local router whose process is now STOPPED, its
on-disk credential DB can hold a **superseded rotating refresh token** (the old
router refreshed in memory past what it wrote to disk, then exited). If the same
host now runs a live local OmniRoute, prefer exporting the LIVE row from that
instance over reading the stopped router's snapshot. Importing the stale snapshot
yields `unrecoverable_refresh_error`; delete any such inactive/expired row you
accidentally create.

## Provider pool & priority model

- Each provider (claude, codex, openrouter, …) has one or more connections.
- `priority` orders connections within a provider; lower number is tried first.
- A request for a provider walks its connections in priority order until one
  succeeds — which is why a "provider X failed" error is often a fallback STEP, not
  the origin. Read the call logs to find which connection actually served (or
  failed) the request.

## Secret hygiene

Do credential work ON the relevant host. Read keys and token blobs from the host's
own files into a script and POST to `127.0.0.1` locally — never echo secret values
back over an SSH channel. Write any transient credential file with `0600`
permissions and shred/remove it on every host afterward (source, destination, and
any intermediate). When inspecting DB rows, print token columns only as
presence/length (`present(len=N)`), never the bytes. Verification should rely on
metadata (`expires_at`, `test_status`, `connection_id`, HTTP status), not token
content.
