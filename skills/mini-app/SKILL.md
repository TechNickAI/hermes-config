---
name: mini-app
description: >
  Use when adding, removing, password-protecting, or troubleshooting a mini-app served
  by the Hermes app-router (Caddy + PM2 + an Express auth sidecar + Tailscale
  Serve/Funnel) on a host. Covers install, the Caddy route pattern, the auth sidecar
  conventions, exposing Hermes dashboards behind a password, the web_dist build step,
  the HTTPS Secure-cookie requirement, and the recurring pitfalls (Tailscale serve
  resets, the PM2 $HOME trap, funnel-eligible ports, strip-prefix requirements).
version: 0.3.0
license: MIT
metadata:
  hermes:
    tags: [devops, app-router, caddy, pm2, tailscale, funnel, mini-app, fleet]
    related_skills: [cron-healthcheck]
---

# Mini-App

**Mission:** Operate the Hermes app-router on this machine — the lightweight stack that
exposes one or more named "mini-apps" at clean URL paths on a single Tailscale HTTPS
host, with optional per-app password gating. One front door, many apps, no cloud.

A mini-app is any process that binds to a localhost port and serves HTTP — a Node
service, a Python FastAPI app, a Hermes dashboard, a webhook receiver. The app-router
fronts them all with Caddy and gives each one:

- A clean path (`https://<host>/<slug>/`)
- Optional password protection via an Express auth sidecar
- HTTPS for free via Tailscale Serve (and optional public exposure via Funnel)
- PM2 supervision so it survives crashes and reboots

The router stack (Caddy + auth sidecar + PM2 templates) is a small self-contained bundle
you deploy into `~/mini-apps/` on each host. This skill is the operator playbook — what
to do once it's installed.

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
the auth sidecar's source itself (that's a change to the router bundle, not an operator
task).

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

| File                                      | What it controls                                           |
| ----------------------------------------- | ---------------------------------------------------------- |
| `~/mini-apps/ecosystem.config.js`         | PM2 process list + ALL env vars (incl. auth passwords)     |
| `~/mini-apps/router/Caddyfile`            | Path → upstream routing                                    |
| `~/mini-apps/router/tailscale-serve.json` | Public/tailnet exposure (huJSON; compiled by apply script) |

Never run ad-hoc `tailscale serve …` commands. Edit the JSON, run the apply script.

## First-time install on this machine

```bash
brew install caddy            # macOS — use the equivalent on Linux
npm install -g pm2

# from the router bundle directory:
bash scripts/install.sh
```

The installer copies templates into `~/mini-apps/`, renders the Caddyfile with the right
paths, installs auth-service deps, and stages the launchd plist (macOS only). Re-running
is safe; `--force` overwrites existing files.

**Then by hand:**

1. Edit `~/mini-apps/ecosystem.config.js`:
   - Set `AUTH_SECRET` to `openssl rand -hex 32` (one per machine)
   - Add `APP_PASSWORD_<SLUG>` / `APP_TITLE_<SLUG>` / `APP_DESC_<SLUG>` for any gated
     apps
2. Edit `~/mini-apps/router/Caddyfile` to declare each app's route
3. Edit `~/mini-apps/router/tailscale-serve.json` if you want a non-default serve layout
   (default exposes Caddy on `:443`)

**Start everything under PM2:**

```bash
pm2 start ~/mini-apps/ecosystem.config.js
pm2 start /opt/homebrew/bin/caddy --name caddy --interpreter none -- \
  run --config ~/mini-apps/router/Caddyfile --adapter caddyfile
pm2 save
pm2 startup    # paste the printed sudo command — needed for resurrect-on-reboot
```

**Apply Tailscale Serve:**

```bash
~/mini-apps/router/apply-tailscale-serve.sh
# macOS launchd that replays on login:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.mini-app.router-serve.plist
```

**If this host also runs a messaging gateway with a Tailscale integration**, disable it
BEFORE the gateway restarts, or it will wipe your serve config. For an OpenClaw gateway,
set this under `gateway` in `~/.openclaw/openclaw.json`:

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
  cwd: "~/mini-apps",
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
caddy reload --config ~/mini-apps/router/Caddyfile --adapter caddyfile
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

## Slug convention for Hermes dashboards

Hermes agent dashboards use the slug `hermes-<agent>` (e.g. `hermes-<name>`). Two
reasons:

1. **Grouping** — every Hermes dashboard sorts together under one prefix.
2. **Collision avoidance** — a non-Hermes app may already own the bare agent name. If a
   product web app already owns `/<name>/`, the agent's dashboard must be
   `/hermes-<name>/` (a separate slug + profile). Never mount a Hermes dashboard at a
   bare name that collides with an existing app slug.

Port convention for the dashboards: reserve a clear local range and increment per agent.
The env slug-to-password rule still applies: `hermes-<name>` ⇒
`APP_PASSWORD_HERMES_<NAME>` (e.g. `hermes-ops` ⇒ `APP_PASSWORD_HERMES_OPS`).

## Front-door outage: Caddy died (the #1 silent failure)

Symptom: every app 502s at once, or `curl http://127.0.0.1:8080/health` returns `000`
(connection refused), but `pm2 list` shows all the backend apps `online`. The router —
Caddy — is dead and nothing restarted it.

**Root cause is almost always that Caddy was started as a bare process, not under PM2.**
A bare `caddy run …` (or one launched outside the ecosystem) has no supervisor, so when
it crashes or the machine churns, it stays dead while every backend keeps running. The
backends being healthy is what makes this confusing.

**Recovery — bring the front door back, supervised this time:**

```bash
export PM2_HOME=<home-dir>/.pm2          # literal path, no ~ or $HOME under Hermes
CADDY=$(command -v caddy || echo /opt/homebrew/bin/caddy)

# 1. Find the config path the OLD process actually used — do NOT assume the skill's
#    default. The router dir may have been renamed (see next pitfall).
ps aux | grep "caddy.*run" | grep -v grep      # reveals --config <path> if still running

# 2. Validate before starting
"$CADDY" validate --config <real-Caddyfile> --adapter caddyfile

# 3. Start UNDER PM2 so it resurrects on crash
pm2 start "$CADDY" --name caddy --interpreter none -- \
  run --config <real-Caddyfile> --adapter caddyfile
pm2 save

# 4. Verify
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/health   # expect 200
```

After this, `pm2 list` should show a `caddy` entry. If it never had one, that was the
bug — Caddy must always be a PM2 process.

## Reboot resurrection is a separate, latent outage

`pm2 save` persists the process list, but it does NOTHING on reboot unless a launchd/
systemd boot hook exists. Check it explicitly — a missing hook is a time-bomb identical
to the bare-Caddy outage, just triggered by a restart:

```bash
launchctl list 2>/dev/null | grep -i pm2 || echo "NO pm2 boot hook — reboot resurrects nothing"
```

Fix (needs the user's sudo — surface it, don't try to run sudo yourself under Hermes):

```bash
sudo env PATH=$PATH PM2_HOME=<home-dir>/.pm2 pm2 startup launchd -u <user> --hp <home-dir>
```

## The router dir may have been renamed (find it, never assume)

The historical path was `~/mini-apps/`; it has been renamed to `~/mini-apps/` on at
least one host. Hardcoded paths in this skill or in old notes will `stat: No such file`
even though the stack is alive. Locate the real one from ground truth rather than
guessing:

```bash
ps aux | grep "caddy.*run" | grep -v grep           # --config reveals the registry dir
find <home-dir> -maxdepth 4 -name ecosystem.config.js 2>/dev/null | grep -v node_modules
find <home-dir> -maxdepth 5 -name Caddyfile 2>/dev/null | grep -v node_modules
```

The Caddyfile commonly lives at `<router-dir>/Caddyfile`, not directly in the router
dir. Note also: under a Hermes tool environment, `$HOME` is rewritten to the profile
home, so `~/mini-apps` and bare `ls`/`grep` may fail to see the real dir — use absolute
`<home-dir>/...` paths, and the Read/Grep tools (which resolve absolute paths) rather
than shell `ls`/`cat` when the shell is sandboxed.

## Hermes dashboards behind the router

> **Deep detail:** see `references/hermes-dashboard-rollout.md` for the full debugging
> path — the Secure-cookie/HTTPS trap, the `--skip-build` web_dist crash-loop fix (a
> FRESH host has no compiled `web_dist`; build once with
> `cd ~/.hermes/hermes-agent/web && npm install && npm run build`), checking who owns
> `:443` before exposure decisions, profile-vs-root DB selection, the SSH-hairpin
> gotcha, matching a custom index page's design system when adding a dashboard card, and
> the end-to-end verification sequence. Read it before any new `hermes-<name>` rollout.

**Slug convention:** mount Hermes dashboards at `/hermes-<agent>/` (e.g.
`/hermes-<name>/`) so the agent dashboard never collides with a same-named product app
(a product app may own `/<name>/`; the agent dashboard uses `/hermes-<name>/`).

**Critical: the auth cookie is `Secure`, so the dashboard MUST be reached over HTTPS.**
A plain-HTTP tailnet door (Tailscale `serve --http=PORT`) silently breaks login:
`POST /auth/login` returns 303 + cookie, but the authed GET comes back 302 because the
browser won't resend a Secure cookie over HTTP. If Caddy doesn't own a 443 funnel on the
host, add a tailnet-only HTTPS door:
`tailscale serve --bg --https=8443 http://127.0.0.1:8080`.

A Hermes dashboard is a normal mini-app, with three extra requirements.

**1. ecosystem.config.js** — the script is the `hermes` CLI, not a JS file. Use a
node-via-shell trick or PM2's interpreter override:

```js
{
  name: "my-dashboard",
  script: "hermes",
  args: "dashboard --port <dashboard-port> --no-open --skip-build",
  interpreter: "none",
  cwd: "~/mini-apps",
  env: {
    PORT: <dashboard-port>,
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
    reverse_proxy 127.0.0.1:<dashboard-port> {
        header_up Host {upstream_hostport}
        header_up X-Forwarded-Prefix /my-dashboard
    }
}
```

`X-Forwarded-Prefix` is used by Hermes ONLY for HTML rewriting (asset paths and
`__HERMES_BASE_PATH__`). API routes still mount at root, so `uri strip_prefix` is
mandatory.

**3a. The dashboard frontend must be built (`web_dist`) before `--skip-build` works.**
On a fresh fleet machine the Hermes web UI is often unbuilt, and a dashboard launched
with `--skip-build` will crash-loop with:
`✗ --skip-build was passed but no web dist found at: …/hermes_cli/web_dist`. PM2 will
show the process `errored` with a climbing restart count, and the port won't listen.
Build it ONCE (takes ~1-2 min), then restart the PM2 process:

```bash
cd ~/.hermes/hermes-agent/web && npm install --no-audit --no-fund && npm run build
# build writes to ../hermes_cli/web_dist/ ; then:
PM2_HOME=<home-dir>/.pm2 pm2 restart <name>-dashboard
PM2_HOME=<home-dir>/.pm2 pm2 reset <name>-dashboard   # clear the crash-loop counter
```

Always check `ls ~/.hermes/hermes-agent/hermes_cli/web_dist/index.html` during
pre-flight; some machines have it pre-built, others don't.

**3b. Verify there are sessions to show — don't assume the profile name.** Some fleet
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

**4. Build the dashboard web UI first, or `--skip-build` crash-loops.** The dashboard is
served from a pre-built `web_dist/` directory. On a fresh Hermes install it may not
exist yet, and PM2 will show the process `errored` with restart count climbing. Check
`pm2 logs <name>` for:

```
✗ --skip-build was passed but no web dist found at: .../hermes_cli/web_dist
```

Fix — build it once (takes ~1-2 min), then restart:

```bash
cd ~/.hermes/hermes-agent/web && npm install && npm run build
# emits ~/.hermes/hermes-agent/hermes_cli/web_dist/
pm2 restart <name> && pm2 reset <name>   # reset clears the inflated restart counter
```

After that `--skip-build` is correct (fast start, no rebuild per boot).

**5. Pin `HERMES_HOME` in env and pass the profile as a CLI arg, not env.** The reliable
combo is `args: "--profile <name> dashboard ..."` (omit `--profile` for the root DB)
plus
`env: { HERMES_HOME: "<home-dir>/.hermes", PATH: "<venv-bin>:/opt/homebrew/bin:..." }`.
Set `HERMES_HOME` to an ABSOLUTE path — under PM2 the rewritten `$HOME` otherwise points
the dashboard at an empty DB. Use the absolute venv hermes binary as `script` (e.g.
`<home-dir>/.hermes/hermes-agent/venv/bin/hermes`), not the bare `hermes` name.

**Verify session presence at the DB, NOT through the proxied API.** The Hermes
dashboard's `/api/sessions` endpoint enforces its own token auth that only the
in-browser SPA supplies — a plain `curl` (even with a valid mini-app auth cookie) gets
`{"detail":"Unauthorized"}` or parses as 0 sessions. That is NOT evidence of an empty
dashboard. Confirm data the honest way:

```bash
sqlite3 <home-dir>/.hermes/profiles/<profile>/state.db "SELECT COUNT(*) FROM sessions;"
# a root-DB agent (no profile) runs against: ~/.hermes/state.db
```

The end-to-end proof for a Hermes dashboard is: backend port returns 200 on loopback +
login round-trip serves the SPA shell (200, `<title>Hermes Agent - Dashboard</title>`) +
the pinned DB has a non-zero session count. The SPA shell is ~700 bytes; data hydrates
client-side after login.

## Public exposure via Tailscale Funnel

Tailscale Serve is tailnet-only by default. To share an app publicly, add the
funnel-enabled port to `~/mini-apps/router/tailscale-serve.json`:

```jsonc
"AllowFunnel": {
  "${HOST}:443": true
}
```

Then re-apply: `~/mini-apps/router/apply-tailscale-serve.sh`.

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
# In ~/mini-apps/router/Caddyfile, ABOVE the main :8080 block:
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

If this host runs the messaging gateway, you can let Caddy front the gateway's
`/hooks/*` endpoint with bearer-token injection. Set `GATEWAY_HOOK_TOKEN` in the Caddy
process env (via the PM2 ecosystem env block), uncomment the `handle /hooks/* { ... }`
block in the Caddyfile, and reload Caddy. External callers can then
`POST https://<host>/hooks/<name>` with no `Authorization` header — Caddy injects
`Bearer <token>` before forwarding.

### Public exposure audit for hooks

Do not infer public exposure from a process binding `0.0.0.0`. On these hosts there are
two separate layers:

- **Raw listener:** e.g. a local webhook process on `*:8644`; reachable on
  local/LAN/tailnet interfaces depending on firewall, but not necessarily public.
- **Public front door:** Tailscale Funnel routes (`tailscale serve status`) usually
  point public HTTPS traffic at loopback Caddy, and Caddy may then expose selected paths
  such as `/hooks/*`.

When asked "how is this public?" or before rolling out a webhook, verify all layers:

1. `lsof -nP -iTCP:<port> -sTCP:LISTEN` — identify the raw listener and bind address.
2. `tailscale serve status` — identify which ports/paths are Funnel-enabled vs
   tailnet-only.
3. Inspect the active Caddyfile for `handle /hooks/*` or webhook-specific proxy blocks.
4. Check for PF/NAT redirects if the port appears reachable but is not in Funnel/Caddy.
5. Redact injected bearer tokens/secrets when reporting the route.

A common safe conclusion: existing `/hooks/*` may be public via Funnel + Caddy bearer
injection, while a separate Hermes webhook listener is only bound locally/tailnet and is
not public until a Caddy/Tailscale route is added.

## The PM2 $HOME trap (CRITICAL when running tools inside Hermes)

Hermes rewrites `$HOME` for tool execution to `~/.hermes/profiles/<profile>/home/`. PM2
keys its socket off `$HOME`. Without `PM2_HOME` set, you talk to a **shadow** PM2 daemon
that supervises nothing.

**Always export this first:**

```bash
# Real shell only — DO NOT use this under Hermes (both $HOME and ~ expand to the rewritten profile home):
export PM2_HOME=$HOME/.pm2
```

If you're calling PM2 from inside a Hermes tool environment, hardcode the absolute path
with **no shell expansion** — neither `~` nor `$HOME` resolves to the user's real home
under the rewritten environment:

```bash
# Substitute your real username; do NOT use ~ or $HOME here.
export PM2_HOME=<home-dir>/.pm2     # macOS
# export PM2_HOME=/home/<your-username>/.pm2    # Linux
```

Quick sanity check after exporting — this should print the real user's home, not a
`.hermes/profiles/...` path:

```bash
echo "$PM2_HOME"
```

Detect the trap:

```bash
ps -ef | grep "PM2.*God" | grep -v grep
```

Two daemons with different `$HOME` paths = you spawned a shadow. Clean up:

```bash
pm2 kill                                  # kills the shadow
export PM2_HOME=<home-dir>/.pm2   # the real one — literal path, no ~ or $HOME
pm2 list                                  # now shows the actual fleet
```

Same trap applies to skill-asset paths: `~/mini-apps/...` resolves under the rewritten
Hermes home. Hardcode the user's real path if you're scripting from inside a Hermes
profile.

## Auth-service env reload trap

The auth sidecar reads `APP_PASSWORD_*` / `APP_TITLE_*` / `APP_DESC_*` at **startup**.
Changing a password in `ecosystem.config.js` and running
`pm2 restart auth-service --update-env` does NOT pick up changes from the ecosystem file
— `--update-env` only re-reads env from PM2's own state.

To actually reload:

```bash
export PM2_HOME=<home-dir>/.pm2   # literal path — no ~ or $HOME under Hermes
pm2 delete auth-service
pm2 start ~/mini-apps/ecosystem.config.js --only auth-service
```

## When Tailscale Serve goes sideways

Any tool that runs `tailscale serve reset` on its own startup will wipe your config —
most commonly the messaging gateway's Tailscale integration if it's enabled. Diagnosis:
`tailscale serve status` shows the wrong routes (or nothing), or a funnel'd port
disappeared. Recovery is one command:

```bash
~/mini-apps/router/apply-tailscale-serve.sh
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

**Always run a NEGATIVE auth test too** — a passing positive test doesn't prove the gate
actually blocks. A correct gate returns 302 (redirect to login) for a wrong password:

```bash
HOST=<host>.<tailnet>.ts.net
JAR=/tmp/jar-bad.txt && rm -f $JAR
curl -sk -o /dev/null -c $JAR -X POST "https://$HOST/auth/login" \
    --data-urlencode "app=<slug>" --data-urlencode "password=WRONG" \
    --data-urlencode "next=/<slug>/" -H "Origin: https://$HOST" >/dev/null
curl -sk -o /dev/null -w "wrong-pw authed GET => %{http_code}\n" -b $JAR "https://$HOST/<slug>/"
# expect 302 (denied). 200 here means the gate is broken/open.
```

A successful login returns 303/302 with a Set-Cookie; the authed GET then returns 200.

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
caddy reload --config ~/mini-apps/router/Caddyfile --adapter caddyfile

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

## The auth cookie is `Secure` — gated apps REQUIRE an HTTPS door

The auth sidecar sets `oc_auth_<slug>` with the `Secure` flag. A browser (and `curl`
across hosts) will only send it back over **HTTPS**. Consequences:

- A plain-HTTP tailnet door (e.g. `tailscale serve --http=4243`) will let you log in
  (303 + Set-Cookie) but the **authed GET still 302s** because the cookie never comes
  back. Gated apps are effectively unusable there.
- **Loopback curl gives a false positive:** `curl -c jar … && curl -b jar …` over
  `http://127.0.0.1:8080` "works" because curl reuses the jar within the same call
  regardless of the Secure flag. Don't trust a loopback round-trip to prove auth — test
  over the real HTTPS tailnet/funnel URL.

**Always front gated apps with an HTTPS door.** On a machine where Caddy owns `:443`
(funnel), that's covered. On a machine where the messaging gateway owns `:443`, add a
**tailnet-only HTTPS door** on `:8443` pointed at Caddy's loopback `:8080`:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8080
# tailnet-only (no --funnel) — survives reboot natively. Verify:
tailscale serve status   # → https://<host>.<tailnet>.ts.net:8443 (tailnet only)
```

Then the app is reachable at `https://<host>.<tailnet>.ts.net:8443/<slug>/`.

**Correct end-to-end verification (run from a DIFFERENT host on the tailnet, over
HTTPS):**

```bash
BASE="https://<host>.<tailnet>.ts.net:8443"; JAR=/tmp/jar.txt; rm -f $JAR
login=$(curl -sk -o /dev/null -w "%{http_code}" -c $JAR -X POST "$BASE/auth/login" \
  --data-urlencode "app=<slug>" --data-urlencode "password=<pw>" \
  --data-urlencode "next=/<slug>/" -H "Origin: $BASE")
authed=$(curl -sk -o /dev/null -w "%{http_code}" -b $JAR "$BASE/<slug>/")
echo "login=$login authed=$authed"   # want 303 + 200; wrong password → authed 302
```

Note: a box hitting its OWN tailnet hostname from inside an SSH session can hang
(hairpin) — run the cross-host test from a different fleet machine instead.

## When NOT to take over the public `:443` funnel

If the target machine's messaging gateway already owns the public `:443` funnel (check
`tailscale serve status` → `:443 … proxy http://127.0.0.1:<gateway-port>` and
`~/.openclaw/openclaw.json` gateway.tailscale.mode), do NOT seize `:443` for Caddy
without asking — it disrupts her gateway. Default to a **tailnet-only HTTPS `:8443`
door** (works on any device signed into the tailnet, not public). Add a public funnel
later only on explicit request. This is the safe, reversible resting state.

## Pre-flight survey for a NEW machine (one SSH pass)

Before installing anything on a fresh fleet host, gather all of this at once:

```bash
# identity + prereqs
whoami; echo $HOME; sw_vers -productVersion; uname -m
ls /opt/homebrew/bin/brew /opt/homebrew/bin/caddy 2>/dev/null   # caddy often MISSING
which node pm2 hermes
ls ~/.local/bin/hermes ~/.hermes/hermes-agent/venv/bin/hermes 2>/dev/null
# sessions per DB (root vs profiles) — picks the right --profile flag
for db in ~/.hermes/state.db ~/.hermes/profiles/*/state.db; do
  [ -f "$db" ] && echo "$(sqlite3 "$db" 'SELECT COUNT(*) FROM sessions') $db"; done
ls ~/.hermes/hermes-agent/hermes_cli/web_dist/index.html 2>/dev/null  # build needed?
ls -d ~/mini-apps ~/mini-apps 2>/dev/null                          # existing router?
# who owns :443?
python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.openclaw/openclaw.json')));print(d.get('gateway',{}).get('tailscale'))"
/opt/homebrew/bin/tailscale serve status
```

`tailscale` lives at `/opt/homebrew/bin/tailscale` on macOS (the GUI app's binary is
also linked there); it's usually NOT on a non-interactive SSH PATH, so call it by full
path or `export PATH=/opt/homebrew/bin:$PATH` first. Same for `brew`/`caddy`. nvm-based
node lives at `~/.nvm/versions/node/<ver>/bin`.

## Standing up the router on a bare machine

`brew install caddy && npm install -g pm2`. There's no public installer for the
auth-service on a bare box, so copy a known-good `auth-service/` from an existing fleet
machine. **Use `scp`, not an SSH-pipe heredoc** — `tar czf - … | ssh host 'tar xzf -'`
fails ("Unrecognized archive format") and base64-through-heredoc fails (the heredoc
consumes stdin so the piped data never arrives). Reliable pattern:

```bash
ssh src 'cd ~/mini-apps && tar czf - --exclude=node_modules auth-service' > /tmp/a.tgz
scp /tmp/a.tgz dst:/tmp/a.tgz
ssh dst 'cd ~/mini-apps && tar xzf /tmp/a.tgz && cd auth-service && npm install --no-audit --no-fund'
```

Then write `ecosystem.config.js` (fresh per-host `AUTH_SECRET` via
`openssl rand -hex 32` — NEVER reuse between machines), the `Caddyfile`, and an index
page, start all three under PM2 (auth-service, the dashboard, caddy), `pm2 save`, add
the `:8443` serve, and do the HTTPS round-trip test.

## Run Caddy UNDER PM2 — not as a bare process

If Caddy runs as a standalone process (not under PM2) and dies, nothing restarts it and
the whole front door 502s while every backend stays healthy — a confusing outage where
`pm2 list` looks fine but `https://<host>/` is down. Diagnose:
`ps aux | grep "caddy run"` shows nothing, `curl http://127.0.0.1:8080/health`
returns 000. Fix and prevent:

```bash
caddy validate --config <Caddyfile> --adapter caddyfile   # validate first
pm2 start /opt/homebrew/bin/caddy --name caddy --interpreter none -- \
  run --config <Caddyfile> --adapter caddyfile
pm2 save
```

## Index-page cards: match the page's own design system

When adding a dashboard card to an existing index page, READ the file first and reuse
its existing card markup/classes (for example, some pages use `.card` + `.pill` instead
of an older `.card-lock` span). Two failure modes seen:

1. **Orphaned card** — a naive "insert before `</body>`" lands the card OUTSIDE the
   styled `<main>`/`.grid` container, so it renders as unstyled floating text. Insert
   INSIDE the `.grid` (e.g. right after `<div class="grid">`).
2. **Wrong markup** — copying a `.card-lock` pattern onto a page that only defines
   `.pill` yields an unstyled card. Use the target page's own classes.

After editing, the served HTML is authoritative; a stale browser view is just cache
(hard-refresh ⌘⇧R). Verify with `curl -sk "$BASE/" | grep -n hermes-<slug>` and confirm
the card line sits between `<div class="grid">` and `</main>`.

## Reboot-resurrect needs sudo (flag it, don't skip it)

`pm2 save` persists the process list, but resurrection on reboot needs a launchd entry
from `sudo pm2 startup` — which can't run unattended. Check
`launchctl list | grep -i pm2`; if absent, surface the exact command for the user to run
(it prints from `pm2 startup launchd -u <user> --hp <home-dir>`) rather than silently
leaving the host unable to recover from a reboot. Tailscale `serve --bg` config persists
across reboots natively, so only PM2 needs this.

1. **Editing the Caddyfile in `/etc/...` or wherever you found Caddy on the system.**
   The router uses `~/mini-apps/router/Caddyfile` and runs Caddy under PM2 with
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

6. **Forgetting to disable the messaging gateway's Tailscale integration on a host that
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

11. **Serving a password-protected app over plain HTTP.** The session cookie is
    `Secure`, so browsers and `curl` refuse to send it back over `http://`. Symptom:
    login POST returns 303 (looks like success) but every subsequent GET bounces back to
    the login page because the cookie never returns. ALWAYS use an HTTPS door:
    `tailscale serve --bg --https=8443 http://127.0.0.1:8080` for tailnet-only, or the
    `:443` HTTPS funnel. Beware a pre-existing plain-HTTP `:4243`-style serve listener —
    it's the trap. When testing cross-node, use the `https://` URL with `curl -sk`; a
    loopback test on the box can falsely "pass" because curl reuses the jar within one
    invocation regardless of Secure.

12. **`pm2 startup` needs sudo and bakes in the wrong `--hp` under Hermes.** Reboot
    resurrection requires a launchd entry (`launchctl list | grep pm2`). Generating it
    needs an interactive sudo, so it CANNOT be done unattended — hand the user the exact
    command. Generate it with a clean HOME or the rewritten profile `$HOME` leaks into
    `--hp`:
    `HOME=<home-dir> PM2_HOME=<home-dir>/.pm2 pm2 startup launchd -u <user> --hp <home-dir>`.
    The `--hp` must match where `dump.pm2` actually lives. The `pm2 save` dump and
    `tailscale serve --bg` config both persist across reboot on their own; only the
    launchd entry needs the one-time sudo.

13. **Streaming a tarball over `ssh ... 'bash -s' <<heredoc` to transfer files.** The
    heredoc consumes stdin, so piped binary data never arrives ("Unrecognized archive
    format" / "error decoding base64"). Use `scp` for file transfer, and reserve the
    heredoc-over-ssh pattern for running commands that don't also need piped stdin.

14. **Running Caddy unsupervised (bare process, not under PM2).** If Caddy dies, nothing
    restarts it and every route 502s while backend apps stay healthy — looks like a
    total outage but it's just the front door. Always start Caddy under PM2
    (`pm2 start <caddy> --name caddy --interpreter none -- run --config ...`).
    Diagnosis: backends `online` in `pm2 list` but `curl http://127.0.0.1:8080/health`
    is refused/000 ⇒ Caddy itself is down.

15. **Starting a Hermes dashboard with `--skip-build` on a fresh host that never built
    the frontend.** The process crash-loops `errored` with
    `✗ --skip-build was passed but no web dist found at: …/hermes_cli/web_dist`. Build
    it once: `cd ~/.hermes/hermes-agent/web && npm install && npm run build`, then
    restart. Pre-flight:
    `ls ~/.hermes/hermes-agent/hermes_cli/web_dist/index.html || echo "build web_dist first"`.

16. **Reaching a Hermes dashboard over a plain-HTTP tailnet door.** The auth cookie is
    `Secure`, so it's never resent over `http://` — login returns 303 but the authed GET
    stays 302 (looks like a broken gate). Serve over HTTPS: a 443 funnel if Caddy owns
    it, else a tailnet-only HTTPS door
    `tailscale serve --bg --https=8443 http://127.0.0.1:8080`. Always run the login
    round-trip over the `https://` URL — an HTTP test gives a false negative.

17. **Pasting generic card markup into a host's custom index page.** Index pages differ
    per host: some use an inline Caddyfile `respond` block; others use a file_server
    `router/public/index.html` with a bespoke theme. READ the existing index first and
    reuse ITS card/pill classes; insert the new card INSIDE the existing grid/container,
    never after `</main>` (an orphan outside the styled wrapper renders as raw, unstyled
    floating text — a user-visible "looks funny" defect). Back up before editing; verify
    with a cache-busting browser reload.

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

- Source of truth for the stack: `hermes-config/devops/app-router/` (`README.md`,
  `templates/`, `scripts/`, `auth-service/`) — the same repo this skill ships in. Re-run
  `scripts/install.sh` to update an installed router.
- Tailscale Funnel docs: https://tailscale.com/kb/1223/funnel
- Caddy `forward_auth` docs:
  https://caddyserver.com/docs/caddyfile/directives/forward_auth
