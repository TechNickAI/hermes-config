# Hermes Dashboard Rollout (fresh machine)

Rolling a Hermes dashboard mini-app onto a NEW fleet host (e.g. exposing Orion on a peer
host). The base `mini-app` recipe assumes the dashboard frontend is already built and an
HTTPS door exists. On a fresh box, neither is guaranteed. These are the two traps that
cost real time, plus the supporting checks.

## TRAP 1 — `--skip-build` crash-loops: dashboard frontend never built

**Symptom:** the dashboard PM2 process flaps `errored` with restarts climbing. PM2
out-log shows, repeating:

```
✗ --skip-build was passed but no web dist found at:
  /Users/<user>/.hermes/hermes-agent/hermes_cli/web_dist
  Pre-build first:  cd web && npm install && npm run build
```

**Cause:** a fresh Hermes install has the dashboard SOURCE (`hermes-agent/web/`) but not
the compiled `hermes_cli/web_dist/`. `hermes dashboard --skip-build` refuses to run
without it. Mac Studio worked only because its dist was already built.

**Fix (one-time per host):**

```bash
export PATH=$HOME/.nvm/versions/node/<ver>/bin:/opt/homebrew/bin:$PATH
cd ~/.hermes/hermes-agent/web
npm install --no-audit --no-fund
npm run build          # writes ../hermes_cli/web_dist/
```

Build takes ~2-5s once deps install. Then `pm2 restart <dashboard>` and confirm
`web_dist/index.html` exists. After this, `--skip-build` is correct and fast.

**Pre-flight check before starting the dashboard:**

```bash
ls ~/.hermes/hermes-agent/hermes_cli/web_dist/index.html 2>/dev/null \
  || echo "MUST build web_dist first"
```

## TRAP 2 — auth loop over plain HTTP: the cookie is `Secure`

**Symptom:** login round-trip on a plain-HTTP door looks half-broken: `POST /auth/login`
returns 303 (success) and sets a cookie, but the follow-up `GET /<app>/` still returns
302 (denied). curl-on-loopback "passes" because curl reuses the jar within one process
regardless of scheme — masking the bug. The real failure shows up cross-node or in a
browser.

**Cause:** the auth sidecar sets the session cookie with the **`Secure`** flag:

```
Set-Cookie: oc_auth_<slug>=...; Path=/<slug>/; HttpOnly; Secure; SameSite=Lax
```

A `Secure` cookie is NEVER sent back over `http://` — only `https://`. So a tailnet HTTP
door (e.g. `tailscale serve --http=4243`) can never complete login.

**Fix:** serve the dashboard over an HTTPS door. Options, in order of preference:

- Public funnel `:443` (HTTPS) — if Caddy owns it (Mac Studio model).
- **Tailnet-only HTTPS** when something else (e.g. the openclaw gateway) already owns
  `:443`:
  ```bash
  tailscale serve --bg --https=8443 http://127.0.0.1:8080
  # → https://<host>.<tailnet>.ts.net:8443/  (tailnet only, real TLS cert)
  ```
  `--bg` config persists across reboots. No funnel = not public.

**Verify over HTTPS, not HTTP** (this is the only test that proves it works):

```bash
BASE="https://<host>.<tailnet>.ts.net:8443"
JAR=/tmp/j.txt; rm -f $JAR
curl -sk -o /dev/null -w "%{http_code}\n" -c $JAR -X POST "$BASE/auth/login" \
  --data-urlencode "app=<slug>" --data-urlencode "password=<pw>" \
  --data-urlencode "next=/<slug>/" -H "Origin: $BASE"   # 303
curl -sk -o /dev/null -w "%{http_code}\n" -b $JAR "$BASE/<slug>/"   # 200 = WORKS
```

## Check who owns :443 BEFORE making exposure decisions

Do not assume Caddy owns the public door. On hosts running the openclaw gateway, the
gateway often owns the `:443` funnel (→ its own gateway port, e.g. 18080), and the Caddy
router is only on a tailnet port. Check first:

```bash
tailscale serve status            # who maps :443 / which ports exist
tailscale serve status --json | python3 -c "import json,sys; d=json.load(sys.stdin); print('TCP',d.get('TCP')); print('Funnel',d.get('AllowFunnel'))"
```

If the gateway owns `:443`, do NOT seize it unilaterally — that disrupts the human's
gateway. Default to a tailnet-only HTTPS door (TRAP 2) and ask before going public. This
is a one-way-door, ask-first decision.

## Profile vs root state.db — pin the dashboard to where sessions actually live

Some agents (Scout, Orion) run cron against the ROOT `~/.hermes/state.db` with NO
profile; others (Atlas, Vega, Nova) use `~/.hermes/profiles/<name>/state.db`. Pinning
the wrong DB shows an empty dashboard. Triage:

```bash
for db in ~/.hermes/state.db ~/.hermes/profiles/*/state.db; do
  [ -f "$db" ] && echo "$(sqlite3 "$db" 'SELECT COUNT(*) FROM sessions' 2>/dev/null)  $db"
done
```

- Root-DB agent: dashboard args `dashboard --port N ...`, env `HERMES_HOME=~/.hermes`,
  NO `--profile`.
- Profile agent: dashboard args `--profile <name> dashboard --port N ...`.

## SSH-hairpin: a box can't reliably reach its own tailnet hostname

Inside an SSH session ON the host, `curl https://<that-host>.ts.net:8443/...` can hang
(hairpin routing). Don't conclude the route is broken. Test auth either (a) via loopback
`http://127.0.0.1:8080` on the box, or (b) over the tailnet HTTPS URL from a DIFFERENT
node. Both together prove the path end-to-end.

## Front door 502 but all apps healthy → Caddy itself died

If `/` and every route 502 but `pm2 list` shows the backends `online`, the Caddy process
itself is down. Most common cause: Caddy was started as a bare process (not under PM2),
so nothing restarted it when it crashed. Always run Caddy UNDER PM2
(`pm2 start <caddy> --name caddy --interpreter none -- run --config ...`) and `pm2 save`
so it resurrects. Verify with `ps aux | grep "caddy.*run"` and
`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/health`.

## Slug naming for fleet Hermes dashboards

Use `hermes-<name>` (e.g. `hermes-atlas`, `hermes-orion`) to namespace agent dashboards
and avoid colliding with same-named product apps (e.g. the `/nova/` CFO app vs the
`hermes-nova` agent dashboard). Auth env key derives by uppercasing + `-`→`_`:
`hermes-orion` ⇒ `APP_PASSWORD_HERMES_ORION`.

## Custom index pages: match the host's existing design system

The app-router index can be EITHER an inline Caddyfile `respond` block (Mac Studio) OR a
file_server serving `_registry/index.html` (a custom dark/glass theme). When adding a
dashboard card, READ the existing index first and reuse ITS card/pill classes. Do not
paste generic `card-lock` markup — it renders unstyled. Insert the card INSIDE the
existing grid/container, never after `</main>` (an orphan outside the styled wrapper
renders as raw floating text). Back up the file before editing and verify with a fresh
(cache-busting) browser load.
