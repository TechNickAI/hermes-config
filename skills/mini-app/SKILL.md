---
name: mini-app
description: >
  Use when adding, removing, password-protecting, or troubleshooting a mini-app served
  by the openclaw-config app-router (Caddy + PM2 + auth sidecar + Tailscale
  Serve/Funnel) on a fleet machine. Covers install, the Caddy route pattern, the auth
  sidecar conventions, exposing apps publicly via Funnel, Hermes dashboards behind a
  password, and the recurring pitfalls (Tailscale "serve reset" wars, the PM2 $HOME
  trap, funnel-eligible ports, strip-prefix requirements).
version: 0.1.0
license: MIT
metadata:
  hermes:
    tags: [devops, app-router, caddy, pm2, tailscale, funnel, mini-app, fleet]
    related_skills: [cron-healthcheck]
---

# Mini-App

**Mission:** Operate the openclaw-config app-router on this machine — the lightweight
stack that exposes one or more named "mini-apps" at clean URL paths on a single
Tailscale HTTPS host, with optional per-app password gating. One front door, many apps,
no cloud.

A mini-app is any process that binds to a localhost port and serves HTTP — a Node
service, a Python FastAPI app, a Hermes dashboard, a webhook receiver. The app-router
fronts them all with Caddy and gives each one:

- A clean path (`https://<host>/<slug>/`)
- Optional password protection via an Express auth sidecar
- HTTPS for free via Tailscale Serve (and optional public exposure via Funnel)
- PM2 supervision so it survives crashes and reboots

The router stack lives upstream in
[openclaw-config](https://github.com/TechNickAI/openclaw-config) under
`devops/app-router/`. This skill is the operator playbook — what to do once it's
installed.

## When to use

Load this skill when you (a Hermes fleet agent) are asked to:

- Add a new mini-app to this host's router (Hermes dashboard, webhook receiver, status
  page, anything that binds to localhost)
- Remove or rename an existing mini-app
- Add or change a password on a gated mini-app
- Expose a mini-app publicly via Tailscale Funnel
- Diagnose a 502 / 404 / auth-loop on a mini-app
- Reload Caddy after editing the Caddyfile
- Restore Tailscale Serve after another tool wiped it
- Verify the front door is up after a reboot or upgrade

**Don't use for:** writing the mini-app's code itself (that's a normal app), or changing
the auth sidecar's source (that's an upstream PR in openclaw-config).

## Stack at a glance

```
Internet (optional)
   │ Tailscale Funnel on :443  (only if you've opted in per-host)
   ▼
Tailnet host: <machine>.<your-tailnet>.ts.net
   │
   ▼  127.0.0.1:8080  (Caddy — declarative, hot-reloadable)
   ├── /auth/*       → auth sidecar      (Node + Express, JWT-style cookies)
   ├── /health       → "ok" 200
   ├── /hooks/*      → optional bearer-injected webhook proxy
   ├── /<slug-1>/*   → mini-app on a localhost port
   ├── /<slug-2>/*   → mini-app on a localhost port (password-gated)
   └── /            → static welcome page
```

**Single sources of truth on this machine** (after install):

| File                                          | What it controls                                           |
| --------------------------------------------- | ---------------------------------------------------------- |
| `~/openclaw-apps/ecosystem.config.js`         | PM2 process list + ALL env vars (incl. auth passwords)     |
| `~/openclaw-apps/router/Caddyfile`            | Path → upstream routing                                    |
| `~/openclaw-apps/router/tailscale-serve.json` | Public/tailnet exposure (huJSON; compiled by apply script) |

Never run ad-hoc `tailscale serve …` commands. Edit the JSON, run the apply script.

## First-time install on this machine

```bash
brew install caddy            # macOS — use the equivalent on Linux
npm install -g pm2

cd ~/src/openclaw-config       # clone if you don't have it
bash devops/app-router/scripts/install.sh
```

The installer copies templates into `~/openclaw-apps/`, renders the Caddyfile with the
right paths, installs auth-service deps, and stages the launchd plist (macOS only).
Re-running is safe; `--force` overwrites existing files.

**Then by hand:**

1. Edit `~/openclaw-apps/ecosystem.config.js`:
   - Set `AUTH_SECRET` to `openssl rand -hex 32` (one per machine)
   - Add `APP_PASSWORD_<SLUG>` / `APP_TITLE_<SLUG>` / `APP_DESC_<SLUG>` for any gated
     apps
2. Edit `~/openclaw-apps/router/Caddyfile` to declare each app's route
3. Edit `~/openclaw-apps/router/tailscale-serve.json` if you want a non-default serve
   layout (default exposes Caddy on `:443`)

**Start everything under PM2:**

```bash
pm2 start ~/openclaw-apps/ecosystem.config.js
pm2 start /opt/homebrew/bin/caddy --name caddy --interpreter none -- \
  run --config ~/openclaw-apps/router/Caddyfile --adapter caddyfile
pm2 save
pm2 startup    # paste the printed sudo command — needed for resurrect-on-reboot
```

**Apply Tailscale Serve:**

```bash
~/openclaw-apps/router/apply-tailscale-serve.sh
# macOS launchd that replays on login:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.app-router-serve.plist
```

**If this host also runs the openclaw gateway**, set this in `~/.openclaw/openclaw.json`
under `gateway` BEFORE the gateway restarts, or it will wipe your serve config:

```json
"tailscale": { "mode": "off", "resetOnExit": false }
```

## Adding a mini-app

Three edits, then reload. Pick a slug (`^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$`) and a
free localhost port (convention: apps start at `3001`).

**1. PM2 ecosystem entry** — append to `apps` in `ecosystem.config.js`:

```js
{
  name: "my-app",
  script: "./my-app/server.js",     // or absolute path, or a python command via interpreter
  cwd: "~/openclaw-apps",
  env: { PORT: 3001 },
},
```

If password-gating, also add to the `auth-service` block's `env`:

```js
APP_PASSWORD_MY_APP: "the-password",
APP_TITLE_MY_APP:    "My App",
APP_DESC_MY_APP:     "One-line description shown on the login form.",
```

The slug-to-env rule is uppercase + `-`→`_`. `my-app` ⇒ `APP_PASSWORD_MY_APP`.

**2. Caddyfile route** — add a `handle` block above the catch-all:

Open app (no auth):

```caddy
handle /my-app/* {
    uri strip_prefix /my-app
    reverse_proxy 127.0.0.1:3001
}
```

Password-gated app:

```caddy
handle /my-app/* {
    forward_auth 127.0.0.1:3000 {
        uri /auth/verify?app=my-app
        copy_headers Cookie
        @unauthorized status 401
        handle_response @unauthorized {
            redir * /auth/login?app=my-app&next={http.request.uri.path} 302
        }
    }
    uri strip_prefix /my-app
    reverse_proxy 127.0.0.1:3001
}
```

**3. Reload PM2 + Caddy:**

```bash
pm2 restart ecosystem.config.js
caddy reload --config ~/openclaw-apps/router/Caddyfile --adapter caddyfile
```

You do **not** need to touch Tailscale when adding an app. Caddy is the path router;
Tailscale just sees one upstream (Caddy on `:8080`).

**Verify:**

```bash
curl -sI http://127.0.0.1:8080/my-app/   # expect 200 (open) or 302 (gated)
```

## Removing a mini-app

```bash
pm2 delete my-app
```

Then drop the Caddyfile `handle` block, the ecosystem entry, and any `APP_*_MY_APP` env
vars from the auth-service block. Reload PM2 + Caddy.

## Hermes dashboards behind the router

A Hermes dashboard is a normal mini-app, with three extra requirements.

**1. ecosystem.config.js** — the script is the `hermes` CLI, not a JS file. Use a
node-via-shell trick or PM2's interpreter override:

```js
{
  name: "my-dashboard",
  script: "hermes",
  args: "dashboard --port 9120 --no-open --skip-build",
  interpreter: "none",
  cwd: "~/openclaw-apps",
  env: {
    PORT: 9120,
    HERMES_PROFILE: "my-profile",   // omit if you want the root state DB
  },
},
```

**2. Caddyfile** — add the prefix header so the SPA rewrites asset URLs correctly, but
STILL keep `uri strip_prefix` — the FastAPI routes mount at root:

```caddy
handle /my-dashboard/* {
    forward_auth 127.0.0.1:3000 {
        uri /auth/verify?app=my-dashboard
        copy_headers Cookie
        @unauthorized status 401
        handle_response @unauthorized {
            redir * /auth/login?app=my-dashboard&next={http.request.uri.path} 302
        }
    }
    uri strip_prefix /my-dashboard
    reverse_proxy 127.0.0.1:9120 {
        header_up Host {upstream_hostport}
        header_up X-Forwarded-Prefix /my-dashboard
    }
}
```

`X-Forwarded-Prefix` is used by Hermes ONLY for HTML rewriting (asset paths and
`__HERMES_BASE_PATH__`). API routes still mount at root, so `uri strip_prefix` is
mandatory.

**3. Don't assume the profile name — verify there are sessions to show.** Some fleet
agents run as root cron jobs against the root `~/.hermes/state.db`, not against
`~/.hermes/profiles/<name>/state.db`. Pinning the wrong DB shows an empty dashboard.
Quick triage:

```bash
for db in $(find ~/.hermes -name state.db 2>/dev/null); do
    n=$(sqlite3 "$db" "SELECT COUNT(*) FROM sessions;" 2>/dev/null)
    echo "  $n  $db"
done
```

Pin to whichever DB actually holds the sessions.

## Public exposure via Tailscale Funnel

Tailscale Serve is tailnet-only by default. To share an app publicly, add the
funnel-enabled port to `~/openclaw-apps/router/tailscale-serve.json`:

```jsonc
"AllowFunnel": {
  "${HOST}:443": true
}
```

Then re-apply: `~/openclaw-apps/router/apply-tailscale-serve.sh`.

**Funnel-allowed ports are exactly `{443, 8443, 10000}`.** Anything else fails silently
or is rejected by Tailscale.

**Security rule (non-negotiable):** a funnel'd port is fully public. Only password-gated
or token-gated upstreams may live behind it. Put passwordless admin UIs on a separate
**tailnet-only** port (e.g. `:8443`) by adding a `Web` entry _without_ a matching
`AllowFunnel` entry.

### The Tailscale "Proxy" → loopback constraint

The `Proxy` field in `tailscale-serve.json` (and `tailscale serve --bg http://…`) **only
works with loopback backends** (`127.0.0.1:*`). Pointing a Serve/Funnel handler at a
tailnet-IP backend (e.g. `100.x.y.z:20128`) returns HTTP 502 through the funnel and
silently strips the Funnel off that port.

**Fix pattern:** add a dedicated Caddy listener on a loopback port whose only job is to
reverse-proxy to the tailnet-IP backend, then funnel that loopback listener. Example for
an LLM proxy bound to a tailnet IP:

```caddy
# In ~/openclaw-apps/router/Caddyfile, ABOVE the main :8080 block:
:8090 {
    bind 127.0.0.1
    reverse_proxy 100.x.y.z:20128 {
        header_up Host {upstream_hostport}
    }
}
```

Then in `tailscale-serve.json`:

```jsonc
"${HOST}:10000": {
  "Handlers": { "/": { "Proxy": "http://127.0.0.1:8090" } }
},
"AllowFunnel": { "${HOST}:10000": true }
```

Use a dedicated listener (NOT a path under `:8080`) when the upstream is a SPA or
Next.js app with absolute `/_next/static/...` asset paths — mounting it on a prefix
inside `:8080` would 404 every asset.

## Hooks / webhooks (optional)

If this host runs the openclaw gateway, you can let Caddy front the gateway's `/hooks/*`
endpoint with bearer-token injection. Set `OPENCLAW_HOOK_TOKEN` in the Caddy process env
(via the PM2 ecosystem env block), uncomment the `handle /hooks/* { ... }` block in the
Caddyfile, and reload Caddy. External callers can then
`POST https://<host>/hooks/<name>` with no `Authorization` header — Caddy injects
`Bearer <token>` before forwarding.

## The PM2 $HOME trap (CRITICAL when running tools inside Hermes)

Hermes rewrites `$HOME` for tool execution to `~/.hermes/profiles/<profile>/home/`. PM2
keys its socket off `$HOME`. Without `PM2_HOME` set, you talk to a **shadow** PM2 daemon
that supervises nothing.

**Always export this first:**

```bash
export PM2_HOME=$HOME/.pm2   # on a real shell — under Hermes, hardcode the user's real home
```

If you're calling PM2 from inside a Hermes tool environment, hardcode the absolute path:

```bash
export PM2_HOME=~/.pm2
# (resolves under your real user, NOT the rewritten Hermes profile home)
```

Detect the trap:

```bash
ps -ef | grep "PM2.*God" | grep -v grep
```

Two daemons with different `$HOME` paths = you spawned a shadow. Clean up:

```bash
pm2 kill                              # kills the shadow
export PM2_HOME=~/.pm2                # the real one
pm2 list                              # now shows the actual fleet
```

Same trap applies to skill-asset paths: `~/openclaw-apps/...` resolves under the
rewritten Hermes home. Hardcode the user's real path if you're scripting from inside a
Hermes profile.

## Auth-service env reload trap

The auth sidecar reads `APP_PASSWORD_*` / `APP_TITLE_*` / `APP_DESC_*` at **startup**.
Changing a password in `ecosystem.config.js` and running
`pm2 restart auth-service --update-env` does NOT pick up changes from the ecosystem file
— `--update-env` only re-reads env from PM2's own state.

To actually reload:

```bash
export PM2_HOME=~/.pm2
pm2 delete auth-service
pm2 start ~/openclaw-apps/ecosystem.config.js --only auth-service
```

## When Tailscale Serve goes sideways

Any tool that runs `tailscale serve reset` on its own startup will wipe your config —
most commonly the openclaw gateway's Tailscale integration if it's enabled. Diagnosis:
`tailscale serve status` shows the wrong routes (or nothing), or a funnel'd port
disappeared. Recovery is one command:

```bash
~/openclaw-apps/router/apply-tailscale-serve.sh
```

The apply script is idempotent — safe to run any time.

If a freshly added funnel route returns 502, work through this in order:

1. `tailscale funnel status` — confirm the funnel is still on for that port. If it's
   missing, you almost certainly pointed it at a non-loopback backend (see the loopback
   constraint above).
2. `lsof -nP -iTCP:<backend-port> -sTCP:LISTEN` — confirm the backend binds loopback. If
   it binds a tailnet IP only, use the Caddy-bridge pattern.
3. `curl -sI http://127.0.0.1:<port>/` — direct loopback check. Connection refused =
   tailnet-IP-only backend.

## Login round-trip (for debugging an auth loop)

```bash
JAR=/tmp/mini-app-cookies.txt && rm -f $JAR

# 1. Login: 303 + Set-Cookie
curl -si -c $JAR -X POST "http://127.0.0.1:8080/auth/login" \
    --data-urlencode "app=my-app" \
    --data-urlencode "password=the-password" \
    --data-urlencode "next=/my-app/" \
    -H "Origin: http://127.0.0.1:8080" -H "Host: 127.0.0.1:8080" | head -10

# 2. Authed GET — 200, not 302
curl -sI -b $JAR "http://127.0.0.1:8080/my-app/" | head -5
```

## End-to-end verification after any change

```bash
# 1. PM2 supervises everything
export PM2_HOME=~/.pm2
pm2 list

# 2. Expected ports listen
lsof -iTCP -sTCP:LISTEN -P -n | grep -E ":3000|:8080|<your-app-ports>"

# 3. Caddy is on the latest config
caddy reload --config ~/openclaw-apps/router/Caddyfile --adapter caddyfile

# 4. Routes return expected codes
for path in "" "auth/login?app=my-app" "my-app/" "health"; do
    code=$(curl -sk -o /dev/null -w "%{http_code}" \
        "https://<host>.<tailnet>.ts.net/$path")
    echo "  $code  /$path"
done

# 5. Persist PM2 state across reboot
pm2 save
```

Expected:

- `/` → 200 (welcome page)
- `/auth/login?app=…` → 200
- `/<gated-app>/` → 302 (redirect to login when not authed)
- `/<open-app>/` → 200
- `/health` → 200
- `/hooks/test` → 405 (POST-only) if hooks are enabled

## Common pitfalls

1. **Editing the Caddyfile in `/etc/...` or wherever you found Caddy on the system.**
   The router uses `~/openclaw-apps/router/Caddyfile` and runs Caddy under PM2 with
   `--config` pointing at that file. Edit there, reload from there.

2. **Forgetting `uri strip_prefix /<slug>` on a Hermes dashboard.** Hermes receives
   `X-Forwarded-Prefix` but only uses it for HTML rewriting. The API routes mount at
   root and 404 without strip-prefix.

3. **`pm2 restart auth-service --update-env` after editing `ecosystem.config.js`.**
   `--update-env` doesn't re-read the file. Use `pm2 delete` + `pm2 start --only` to
   actually reload.

4. **Pointing Tailscale Serve/Funnel at a non-loopback backend.** Returns 502 and strips
   Funnel off the port silently. Use a Caddy loopback bridge.

5. **Putting passwordless admin UIs on a funnel'd port.** A funnel'd port is fully
   public. Move them to `:8443` (tailnet-only) by adding a `Web` entry without an
   `AllowFunnel` entry.

6. **Forgetting to disable the openclaw gateway's Tailscale integration on a host that
   runs openclaw.** It will call `tailscale serve reset` on every restart and wipe your
   config. Set `gateway.tailscale.mode: "off"` and
   `gateway.tailscale.resetOnExit: false` in `~/.openclaw/openclaw.json`.

7. **Calling PM2 from inside a Hermes tool environment without exporting `PM2_HOME`.**
   You talk to a shadow daemon that supervises nothing. Export first, every time.

8. **Adding a port outside `{443, 8443, 10000}` to `AllowFunnel`.** Tailscale rejects it
   silently. Stick to those three for public exposure.

9. **`pm2 save` without `PM2_HOME` exported.** You save the wrong process list to the
   wrong dump file, and reboot resurrects nothing.

10. **Slugs with uppercase, underscores, or `>32` chars.** The auth sidecar rejects them
    at `/auth/verify` and `/auth/login` with 400. Pattern is
    `^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$`.

## Verification checklist

- [ ] App appears in `pm2 list` as `online`
- [ ] Backend port responds on loopback: `curl -sI http://127.0.0.1:<port>/`
- [ ] Caddy reloaded with no parse errors: `pm2 logs caddy --lines 30`
- [ ] Front door returns the right code via Tailscale URL (200 / 302 / 405)
- [ ] If gated: login round-trip succeeds, authed GET returns 200
- [ ] If Hermes dashboard: session count > 0 on the pinned DB
- [ ] `tailscale serve status` matches `tailscale-serve.json`
- [ ] `pm2 save` after any ecosystem change (with `PM2_HOME` exported)
- [ ] If exposing publicly: only password/token-gated upstreams on funnel'd ports

## Reference

- Source of truth for the stack: `~/src/openclaw-config/devops/app-router/`
  (`README.md`, `templates/`, `scripts/`, `auth-service/`)
- Tailscale Funnel docs: https://tailscale.com/kb/1223/funnel
- Caddy `forward_auth` docs:
  https://caddyserver.com/docs/caddyfile/directives/forward_auth
