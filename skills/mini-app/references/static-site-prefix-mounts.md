# Static sites mounted under a mini-app prefix

Session lesson from serving `example.com` through the app-router.

## When this applies

Use this when a static website is served behind a Caddy path prefix such as `/heartcentered/`, especially when the site was originally built for domain-root hosting and contains root-absolute asset URLs like:

- `/styles.css`
- `/images/og-image.jpg`
- `/favicon.ico`
- `/some-section/`

With a simple Caddy `uri strip_prefix /slug` route, the HTML loads but the browser requests assets at the router root (`/styles.css`) instead of the mini-app path (`/slug/styles.css`), causing missing CSS/images.

## Proven pattern

Run a tiny localhost static server for the site, and make it prefix-aware for emitted HTML.

Caddy stays simple:

```caddy
handle /heartcentered/* {
    uri strip_prefix /heartcentered
    reverse_proxy 127.0.0.1:3007
}
handle /heartcentered {
    redir /heartcentered/ 308
}
```

The server:

1. Serves files from the real site root.
2. Serves `index.html` for directory requests.
3. For `.html` responses only, rewrites root-absolute `href="/..."` and `src="/..."` to include `BASE_PATH`, e.g. `href="/heartcentered/styles.css"`.
4. Leaves relative links (`../principles/`, `section/`, `#vision`) untouched.
5. Prevents directory traversal with normalized path joining.

## Minimal server sketch

```js
const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.env.PORT || 3007);
const ROOT = process.env.SITE_ROOT;
const BASE_PATH = (process.env.BASE_PATH || "").replace(/\/$/, "");

function rewriteHtml(html) {
  if (!BASE_PATH) return html;
  return html
    .replace(/(href|src)="\/(?!\/)/g, `$1="${BASE_PATH}/`)
    .replace(/(href|src)='\/(?!\/)/g, `$1='${BASE_PATH}/`);
}

function safeJoin(root, urlPath) {
  let p = decodeURIComponent(urlPath.split("?")[0].split("#")[0]);
  p = path.normalize(p).replace(/^(\.\.[/\\])+/, "");
  const full = path.join(root, p);
  return full.startsWith(root) ? full : null;
}

http.createServer((req, res) => {
  let target = safeJoin(ROOT, req.url || "/");
  if (!target) return res.writeHead(403).end("Forbidden");
  if (fs.existsSync(target) && fs.statSync(target).isDirectory()) {
    target = path.join(target, "index.html");
  }
  fs.readFile(target, (err, data) => {
    if (err) return res.writeHead(404).end("Not Found");
    const isHtml = path.extname(target).toLowerCase() === ".html";
    res.writeHead(200, { "Content-Type": isHtml ? "text/html; charset=utf-8" : "application/octet-stream" });
    res.end(isHtml ? rewriteHtml(data.toString("utf8")) : data);
  });
}).listen(PORT, "127.0.0.1");
```

## PM2 entry example

```js
{
  name: "heartcentered",
  script: "./heartcentered/server.js",
  cwd: "/Users/<user>/mini-apps",
  env: {
    PORT: 3007,
    SITE_ROOT: "/Users/<user>/src/example.com",
    BASE_PATH: "/heartcentered",
  },
}
```

## Verification checklist

```bash
export PM2_HOME=/Users/<user>/.pm2
curl -sI http://127.0.0.1:3007/ | head
curl -sI http://127.0.0.1:8080/heartcentered/ | head
curl -sI http://127.0.0.1:8080/heartcentered/styles.css | head
curl -s http://127.0.0.1:8080/heartcentered/love-equation/ | grep 'href="/heartcentered/styles.css"'
```

Also verify the front-door URL in a browser and inspect computed stylesheet URL, because a 200 HTML response can still be visually broken if absolute assets were not rewritten.

## Pitfalls

- Do not mount a root-absolute static site behind a prefix with Caddy alone unless the site is prefix-aware.
- `uri strip_prefix` fixes the upstream request path, not the browser-visible asset URLs emitted by HTML.
- If the router's main HTTPS host is already Tailscale Funnel-enabled, any open mini-app route inherits that exposure. Do not assume "I didn't add Funnel" means "not publicly reachable."
- In this user's current setup, the active app-router config lives under `/Users/<user>/mini-apps/` (`_registry/Caddyfile`, `ecosystem.config.js`), not the older documented `~/openclaw-apps/` path. Prefer confirming the running Caddy process args before editing.
