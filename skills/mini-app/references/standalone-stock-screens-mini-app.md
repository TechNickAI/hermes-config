# Standalone Stock Screens mini-app session notes

Context: User asked for a stock-screen UI as a **mini-app**, not merely as a Hermes
dashboard plugin. Initial build as a dashboard plugin was useful, but the corrected
deliverable was a standalone HTTP app behind the app-router.

## Key distinction learned

- **Hermes dashboard plugin**: lives inside the Hermes dashboard SPA
  (`~/.hermes/plugins/<name>/dashboard`) and registers with `window.__HERMES_PLUGINS__`.
  It is not itself a router mini-app.
- **Router mini-app**: its own localhost HTTP service, supervised by PM2 and exposed by
  Caddy/Tailscale at a clean path like `/stock-screens/`.

If the user says "as a mini app, not as a Hermes plugin," build the standalone service.
Do not satisfy the request by mounting a dashboard plugin inside `/hermes-<name>/...`.

## Working shape from this session

Standalone app paths:

```text
/Users/<user>/mini-apps/stock-screens/
├── server.js                 # Node HTTP server, no Express dependency
└── public/
    ├── index.html
    ├── app.js                # vanilla JS frontend
    └── styles.css
```

PM2 entry:

```js
{
  name: "stock-screens",
  script: "/Users/<user>/mini-apps/stock-screens/server.js",
  interpreter: "/Users/<user>/.nvm/versions/node/v24.13.0/bin/node",
  cwd: "/Users/<user>/mini-apps/stock-screens",
  env: {
    PORT: 9130,
    SCREENER_DB: "/Users/<user>/.hermes/screener/screener.db",
    SQLITE: "/usr/bin/sqlite3",
    PATH: "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"
  }
}
```

Caddy route:

```caddy
handle /stock-screens/* {
    uri strip_prefix /stock-screens
    reverse_proxy 127.0.0.1:9130
}
```

The app exposes:

```text
GET /health
GET /api/snapshot
GET /api/schedule-draft/<query-id>
GET /               # static frontend
```

Because Caddy strips `/stock-screens`, the browser frontend should use relative URLs,
e.g. compute `new URL('./api/', window.location.href).pathname`, rather than hardcoding
`/api/...` or `/stock-screens/api/...`.

## Data facts from this session

Local DB:

```text
/Users/<user>/.hermes/screener/screener.db
```

Five query counts after exposure data was staged:

- Covered dividends: 2 (`OMC`, `ACN`)
- Fallen angels: 3 (`ACN`, `ADBE`, `INTU`)
- Cash flow after debt: 9 (led by `DBX`, `ACN`, `ADBE`, `OMC`, `INTU`, `KFY`)
- AI-disruption shorts: 2 (`OMC`, `KFY`)
- Dividend traps / stress: 1 (`RHI`)

These are example data, not durable investment conclusions.

## Router/Tailscale lessons

- `tailscale serve --bg --https=443 http://127.0.0.1:8080` followed by
  `tailscale funnel --bg 443` can rewrite the backend to `127.0.0.1:443`. Prefer the
  direct command:

```bash
tailscale serve reset
tailscale funnel --bg http://127.0.0.1:8080
tailscale serve status
```

Expected status:

```text
https://<host>.ts.net (Funnel on)
|-- / proxy http://127.0.0.1:8080
```

- Caddyfile inline HTML heredoc can be brittle / fail parsing
  (`unrecognized directive: <!doctype`). Use a static `router/public/index.html` served
  with `root` + `file_server` for router home pages.
- Caddy should run under PM2, not as a bare process.
- Always export literal `PM2_HOME=/Users/<user>/.pm2` from Hermes tools before `pm2`
  operations.

## Verification pattern

Local checks:

```bash
node --check /Users/<user>/mini-apps/stock-screens/server.js
node --check /Users/<user>/mini-apps/stock-screens/public/app.js
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9130/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/stock-screens/
curl -s http://127.0.0.1:8080/stock-screens/api/snapshot | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["standalone"], d["universe_count"])'
```

Public checks:

```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://<host>.ts.net/stock-screens/
curl -sk -o /dev/null -w '%{http_code}\n' https://<host>.ts.net/stock-screens/api/snapshot
```

Render check with headless Chrome:

```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
$CHROME --headless=new --disable-gpu --virtual-time-budget=8000 \
  --dump-dom https://<host>.ts.net/stock-screens/ > /tmp/ss-dom.html
```

Confirm DOM contains expected app strings (`Gil equity screens`, `Standalone mini-app`,
query tabs, visible tickers) and **does not** contain `__HERMES_PLUGIN_SDK__`.
