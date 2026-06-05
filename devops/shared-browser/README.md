# Shared Browser

A login-capable, bot-detection-beating web browser that **multiple agents can drive in
parallel** on one machine — sharing a single cookie jar so you log into a site once and
every agent is logged in.

It is intentionally tiny: **one Node daemon + one shell CLI.** No MCP, no mcporter, no
launchd, no plist.

## The problem it solves

Agents need a real browser to scrape JS-rendered pages, fill forms, and act on logged-in
services. Three things make that hard:

1. **Bot detection.** Headless Chromium leaks a `HeadlessChrome` user agent and
   `navigator.webdriver === true`, so sites (search engines, social, commerce) block or
   empty out. You need _real_ Chrome with the automation tells removed.
2. **Shared logins.** You don't want to log into the same site separately for every
   agent. Cookies should live in one profile that everyone reuses and that survives
   restarts.
3. **Parallelism without collisions.** Several agents may browse at once. They must not
   clobber each other's "current page."

Playwright MCP can't satisfy all three: it is single-client. Its shared-context mode
shares one tab-set (parallel clients collide), and isolated contexts can't share a
profile (Chrome's profile lock). So "shared logins + N parallel agents" is
architecturally impossible through MCP — which is why MCP-based setups need a pile of
launchd/mcporter scaffolding and still don't parallelize.

## Design

**`browserd.mjs`** is a long-lived daemon that owns exactly one
`chromium.launchPersistentContext` against real Google Chrome (`channel: "chrome"`). It
applies stealth (real Chrome UA + an init script setting `navigator.webdriver = false`)
and exposes a small localhost JSON/HTTP API.

The model: **each agent owns a _window_; within its window it opens as many _tabs_ as it
wants.** Pages are keyed by `window::tab`. Parallel agents never collide because each
drives its own window. All windows/tabs share ONE cookie jar (the on-disk profile), so
logins are shared and persist across daemon and Chrome restarts.

**`bin/browser`** is the only thing agents call. It:

- **lazy-starts** the daemon on first use, via Node `spawn(detached).unref()` (works
  inside sandboxed terminals that block `nohup`/`&`);
- **self-heals** — if the daemon died it restarts it; if a page/context died it
  recreates just that tab and retries;
- **elects one starter** under a thundering herd via an atomic `mkdir` lock, so N cold
  agents starting at once produce exactly one daemon;
- **returns honest exit codes** — non-zero on any real failure, never an exit-0 lie.

```
agent A ─┐                                  ┌─ window A (tabs: main, research)
agent B ─┼─ browser CLI ──HTTP──> browserd ─┼─ window B (tabs: main)
agent C ─┘   (lazy/self-heal)     (1 Chrome) └─ window C (tabs: main, compare)
                                       │
                              shared cookie jar (on-disk profile)
```

## Install

```bash
# Installs browserd + the browser CLI, links `browser` onto PATH, and verifies
# with a cold-start live-nav check. Idempotent.
./install.sh

# Then, from any agent or shell:
browser nav example.com --window myagent
```

Dependencies (the installer fetches what's missing): `node`, Playwright's `playwright`
library, and the `chrome` browser channel (`npx playwright install chrome`).

## Verify

```bash
./verify.sh         # 6 checks: CLI on PATH, deep preflight, clean Chrome UA,
                    # webdriver=false, DuckDuckGo bot-detection dodge, daemon running
./test-parallel.sh  # 5 agents hit 5 sites in their own windows at once;
                    # asserts each window kept its own page (no clobber)
```

## Usage

```bash
browser nav <url> --window W [--tab T]   # navigate (defaults: window=default tab=main)
browser snap  --window W                 # aria tree with clickable refs (e1, e2, ...)
browser click <ref> --window W           # click by ref from snap
browser type  <ref> "text" [--submit] --window W
browser text  --window W                 # visible page text
browser eval  "document.title" --window W
browser links --window W
browser shot  [path] [--full] --window W
browser back  --window W
browser wait  <ms> | --selector <sel> --window W
browser tabs  --window W                 # list tabs in a window
browser windows                          # list all windows
browser cookies [domain]                 # count cookies (shared jar)
browser close --window W [--tab T]       # close a tab, or a whole window
browser preflight                        # deep health -> prints "ok"
browser restart | stop
```

### Auth

Log into a site once (drive it with `snap`/`type`/`click`, or sign in by hand on the
real-Chrome profile for 2FA/CAPTCHA). Cookies persist in the shared profile, so every
subsequent `browser nav` from any agent reuses the session. **Never** put passwords in
commands or scripts — login is a one-time human step.

## Configuration

All via environment variables (sane defaults):

| Variable          | Default                     | Purpose                                 |
| ----------------- | --------------------------- | --------------------------------------- |
| `BROWSER_HOME`    | `~/.hermes/shared-browser`  | Profile, logs, output, daemon location. |
| `BROWSER_PORT`    | `18722`                     | Daemon localhost port.                  |
| `BROWSER_UA`      | a current desktop Chrome UA | User agent presented to sites.          |
| `BROWSER_IDLE_MS` | `1800000` (30 min)          | Idle non-default tabs are reaped.       |
| `BIN_DIR`         | `~/.local/bin`              | Where the `browser` symlink is placed.  |

## Scope

Single machine, single user account, trusted local processes. The daemon binds
`127.0.0.1` only — it is not exposed off-box. All local agents share one login store by
design; do not use this where local processes should not share each other's sessions.

A matching agent skill (`browser-automation`) documents when to use this vs. the host's
built-in browser tools vs. desktop control, with worked examples and pitfalls.
