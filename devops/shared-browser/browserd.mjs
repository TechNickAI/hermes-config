#!/usr/bin/env node
// browserd — single long-lived owner of one real-Chrome persistent context.
// Shares ONE cookie jar (logins) across every caller. Each AGENT owns a
// WINDOW; within its window an agent may open multiple TABS. Parallel agents
// never collide because each drives its own window.
//
// Why this exists: Playwright MCP is single-client. --shared-browser-context
// shares one tab-set (parallel agents collide); isolated contexts can't share
// a profile (Chrome lock). The only way to get "shared logins + N parallel
// agents" is one context owner handing out per-agent windows. That is this file.
//
// Addressing: every page is keyed by (window, tab). Default window "default",
// default tab "main". Cookies are shared across ALL windows/tabs (one profile).
//
// HTTP API (JSON, localhost only). All take optional {window, tab}:
//   GET  /health                          -> { ok, windows, tabs, uptime }
//   POST /nav     { window, tab, url }     -> { ok, url, title }
//   POST /snap    { window, tab }          -> { ok, snapshot }   (aria tree+refs)
//   POST /text    { window, tab }          -> { ok, text }       (visible text)
//   POST /eval    { window, tab, expr }    -> { ok, value }
//   POST /click   { window, tab, ref }     -> { ok }
//   POST /type    { window, tab, ref, text, submit } -> { ok }
//   POST /shot    { window, tab, path, fullPage } -> { ok, path }
//   POST /links   { window, tab }          -> { ok, links }
//   POST /back    { window, tab }          -> { ok, url }
//   POST /wait    { window, tab, ms|selector } -> { ok }
//   POST /tabs    { window }               -> { ok, tabs }   (list tabs in a window)
//   POST /windows {}                       -> { ok, windows } (list all windows)
//   POST /cookies { domain? }              -> { ok, cookies }
//   POST /close   { window, tab? }         -> { ok }  (close one tab, or whole window)
//   POST /shutdown {}                      -> { ok }

import http from "node:http";
import { createRequire } from "node:module";
import { execSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const HOME = process.env.HOME;
const BROWSER_HOME =
  process.env.BROWSER_HOME || path.join(HOME, ".hermes/shared-browser");
const PROFILE = path.join(BROWSER_HOME, "chrome-profile");
const LOG_DIR = path.join(BROWSER_HOME, "logs");
const PORT = parseInt(process.env.BROWSER_PORT || "18722", 10);
const HOST = "127.0.0.1";
const IDLE_MS = parseInt(process.env.BROWSER_IDLE_MS || "1800000", 10); // 30 min idle-tab reap
const UA =
  process.env.BROWSER_UA ||
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36";
// Browser binary selection (priority order):
//   1. BROWSER_EXECUTABLE — explicit path to a browser binary (any platform).
//   2. BROWSER_CHANNEL — a Playwright channel like "chrome"/"msedge" (uses the
//      OS-installed app). NOT the default: real Chrome ties into the macOS login
//      keychain and the user's own Chrome profile lock, which caused infinite
//      "Keychain Not Found" loops and broke the user's real Chrome.
//   3. (default) Playwright's BUNDLED Chromium — a fully separate "Chrome for
//      Testing" app under ms-playwright cache. Zero keychain association, cannot
//      collide with the user's real Chrome. This is the robust default.
const EXECUTABLE = process.env.BROWSER_EXECUTABLE || "";
const CHANNEL = process.env.BROWSER_CHANNEL || "";
// Headless by default (fleet runs unattended). Set BROWSER_HEADLESS=0/false to
// open a VISIBLE window — needed for a human to log into a site (2FA, captcha)
// once; the session then persists in the shared profile for headless runs.
const HEADLESS = !/^(0|false|no)$/i.test(process.env.BROWSER_HEADLESS || "1");

fs.mkdirSync(LOG_DIR, { recursive: true });
const log = (...a) => {
  const line = `[${new Date().toISOString()}] ${a.join(" ")}\n`;
  fs.appendFileSync(path.join(LOG_DIR, "browserd.log"), line);
};

// --spawn: fork a fully-detached daemon and exit immediately. This is how the
// CLI starts us without shell-level nohup/& (which sandboxed terminals block).
if (process.argv.includes("--spawn")) {
  const out = fs.openSync(path.join(LOG_DIR, "browserd.log"), "a");
  const child = spawn(process.execPath, [process.argv[1]], {
    detached: true,
    stdio: ["ignore", out, out],
    env: process.env,
  });
  child.unref();
  process.exit(0);
}

// Clear a stale Chrome profile lock left by a crashed/killed prior Chrome.
// Without this, launchPersistentContext blocks forever waiting for the lock.
function clearStaleLock() {
  for (const f of ["SingletonLock", "SingletonCookie", "SingletonSocket"]) {
    try {
      fs.rmSync(path.join(PROFILE, f), { force: true });
    } catch {}
  }
}

// Resolve playwright whether installed locally or globally.
function loadPlaywright() {
  try {
    return createRequire(import.meta.url)("playwright");
  } catch {}
  try {
    const groot = execSync("npm root -g", { encoding: "utf8" }).trim();
    return createRequire(path.join(groot, "x.js"))("playwright");
  } catch (e) {
    log("FATAL: cannot resolve playwright:", e.message);
    process.exit(2);
  }
}
const { chromium } = loadPlaywright();

let context = null;
let launching = null;
// windows: Map<windowName, Map<tabName, { page, lastUsed }>>
const windows = new Map();
const startedAt = Date.now();

async function ensureContext() {
  if (context) return context;
  if (launching) return launching;
  launching = (async () => {
    clearStaleLock();
    log("launching persistent context");
    const ctx = await chromium.launchPersistentContext(PROFILE, {
      // Binary: explicit executable > explicit channel > "chromium" channel.
      // channel:"chromium" runs the FULL Chrome-for-Testing in new-headless mode
      // (a real browser engine, not the stripped chromium-headless-shell), which
      // beats bot detection AND is a separate app with no macOS keychain tie-in
      // and no clash with the user's real Chrome. Default when nothing is set.
      ...(EXECUTABLE
        ? { executablePath: EXECUTABLE }
        : { channel: CHANNEL || "chromium" }),
      headless: HEADLESS,
      userAgent: UA,
      viewport: { width: 1280, height: 800 },
      // --password-store=basic + --use-mock-keychain keep automation Chrome
      // OUT of the macOS login keychain. Without these, real Chrome tries to
      // store its "Safe Storage" key in the keychain and, under an agent's
      // overridden $HOME (no keychain there), loops forever on a
      // "Keychain Not Found" prompt. Automation never needs OS keychain access.
      args: [
        "--no-first-run",
        "--no-default-browser-check",
        "--password-store=basic",
        "--use-mock-keychain",
      ],
    });
    // Stealth: real Chrome UA above + kill the webdriver tell.
    await ctx.addInitScript(() => {
      Object.defineProperty(navigator, "webdriver", { get: () => false });
    });
    ctx.on("close", () => {
      log("context closed");
      context = null;
      windows.clear();
    });
    context = ctx;
    return ctx;
  })();
  try {
    return await launching;
  } catch (e) {
    // A failed launch must NOT leave a rejected promise cached forever, or
    // every later request awaits the same failure. Clear it so the next call
    // retries Chrome startup from scratch.
    log("context launch failed, will retry next call:", e.message);
    throw e;
  } finally {
    launching = null;
  }
}

// Per-(window,tab) creation locks so two concurrent calls for the same tab
// don't both newPage() and orphan one. Keyed "window\u0000tab".
const pageLocks = new Map();

async function getPage(window = "default", tab = "main") {
  const ctx = await ensureContext();
  let tabs = windows.get(window);
  if (!tabs) {
    tabs = new Map();
    windows.set(window, tabs);
  }
  const entry = tabs.get(tab);
  if (entry && !entry.page.isClosed()) {
    entry.lastUsed = Date.now();
    return entry.page;
  }
  // Serialize creation for this exact window/tab.
  const key = `${window}\u0000${tab}`;
  let pending = pageLocks.get(key);
  if (!pending) {
    pending = (async () => {
      // Re-check inside the lock: a racing caller may have just created it.
      const existing = tabs.get(tab);
      if (existing && !existing.page.isClosed()) {
        existing.lastUsed = Date.now();
        return existing.page;
      }
      const page = await ctx.newPage();
      page.on("close", () => {
        const t = windows.get(window);
        if (t) {
          const e = t.get(tab);
          if (e && e.page === page) t.delete(tab);
          if (t.size === 0) windows.delete(window);
        }
      });
      tabs.set(tab, { page, lastUsed: Date.now() });
      return page;
    })();
    pageLocks.set(key, pending);
  }
  try {
    return await pending;
  } finally {
    pageLocks.delete(key);
  }
}

// Reap idle tabs (cookies live in the context, so closing tabs is safe).
// Never reaps the default/main page.
setInterval(() => {
  const now = Date.now();
  for (const [window, tabs] of windows) {
    for (const [tab, { page, lastUsed }] of tabs) {
      if (window === "default" && tab === "main") continue;
      if (now - lastUsed > IDLE_MS) {
        log(`reaping idle tab: ${window}/${tab}`);
        page.close().catch(() => {});
        tabs.delete(tab);
      }
    }
    if (tabs.size === 0) windows.delete(window);
  }
}, 60000).unref();

// Compact aria snapshot with refs an agent can click.
async function snapshot(page) {
  try {
    return await page.locator("body").ariaSnapshot({ ref: true });
  } catch {
    return await page.locator("body").ariaSnapshot();
  }
}

function countTabs() {
  let n = 0;
  for (const tabs of windows.values()) n += tabs.size;
  return n;
}

const handlers = {
  async health() {
    return {
      ok: true,
      windows: windows.size,
      tabs: countTabs(),
      uptime: Date.now() - startedAt,
    };
  },
  async nav({ window, tab, url }) {
    if (!url) throw new Error("url required");
    if (!/^https?:|^file:|^about:/.test(url)) url = "https://" + url;
    const page = await getPage(window, tab);
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
    return { ok: true, url: page.url(), title: await page.title() };
  },
  async snap({ window, tab }) {
    const page = await getPage(window, tab);
    return { ok: true, url: page.url(), snapshot: await snapshot(page) };
  },
  async text({ window, tab }) {
    const page = await getPage(window, tab);
    return { ok: true, text: await page.evaluate(() => document.body.innerText) };
  },
  async eval({ window, tab, expr }) {
    if (!expr) throw new Error("expr required");
    const page = await getPage(window, tab);
    const value = await page.evaluate((e) => eval(e), expr);
    return { ok: true, value };
  },
  async click({ window, tab, ref }) {
    if (!ref) throw new Error("ref required");
    const page = await getPage(window, tab);
    await page.locator(`aria-ref=${ref}`).click({ timeout: 10000 });
    return { ok: true };
  },
  async type({ window, tab, ref, text, submit }) {
    const page = await getPage(window, tab);
    const loc = page.locator(`aria-ref=${ref}`);
    await loc.fill(text ?? "", { timeout: 10000 });
    if (submit) await loc.press("Enter");
    return { ok: true };
  },
  async shot({ window, tab, path: out, fullPage }) {
    const page = await getPage(window, tab);
    const file =
      out || path.join(BROWSER_HOME, "output", `shot-${Date.now()}.png`);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    await page.screenshot({ path: file, fullPage: !!fullPage });
    return { ok: true, path: file };
  },
  async links({ window, tab }) {
    const page = await getPage(window, tab);
    const links = await page.evaluate(() =>
      Array.from(document.querySelectorAll("a[href]"))
        .map((a) => ({ text: a.innerText.trim().slice(0, 80), href: a.href }))
        .filter((l) => l.text)
        .slice(0, 200),
    );
    return { ok: true, links };
  },
  async back({ window, tab }) {
    const page = await getPage(window, tab);
    await page.goBack({ waitUntil: "domcontentloaded" });
    return { ok: true, url: page.url() };
  },
  async wait({ window, tab, ms, selector }) {
    const page = await getPage(window, tab);
    if (selector) await page.locator(selector).first().waitFor({ timeout: 30000 });
    else await page.waitForTimeout(Math.min(parseInt(ms || "1000", 10), 30000));
    return { ok: true };
  },
  async tabs({ window = "default" }) {
    const tabs = windows.get(window) || new Map();
    return {
      ok: true,
      window,
      tabs: [...tabs.entries()].map(([tab, e]) => ({
        tab,
        url: e.page.url(),
        lastUsed: e.lastUsed,
      })),
    };
  },
  async windows() {
    return {
      ok: true,
      windows: [...windows.entries()].map(([window, tabs]) => ({
        window,
        tabs: tabs.size,
      })),
    };
  },
  async cookies({ domain }) {
    const ctx = await ensureContext();
    const all = await ctx.cookies();
    const cookies = domain ? all.filter((c) => c.domain.includes(domain)) : all;
    return { ok: true, count: cookies.length, cookies };
  },
  async close({ window = "default", tab }) {
    const tabs = windows.get(window);
    if (!tabs) return { ok: true };
    if (tab) {
      const e = tabs.get(tab);
      if (e) {
        await e.page.close().catch(() => {});
        tabs.delete(tab);
      }
    } else {
      // Close the whole window (all its tabs).
      for (const e of tabs.values()) await e.page.close().catch(() => {});
      windows.delete(window);
    }
    return { ok: true };
  },
  async shutdown() {
    log("shutdown requested");
    if (context) await context.close().catch(() => {});
    setTimeout(() => process.exit(0), 50);
    return { ok: true };
  },
};

const server = http.createServer((req, res) => {
  const send = (code, obj) => {
    res.writeHead(code, { "content-type": "application/json" });
    res.end(JSON.stringify(obj));
  };
  if (req.method === "GET" && req.url === "/health") {
    handlers.health().then((r) => send(200, r));
    return;
  }
  if (req.method !== "POST") return send(405, { ok: false, error: "POST only" });
  const route = req.url.slice(1);
  const fn = handlers[route];
  if (!fn) return send(404, { ok: false, error: `unknown route ${route}` });
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", async () => {
    let args = {};
    try {
      args = body ? JSON.parse(body) : {};
    } catch {
      return send(400, { ok: false, error: "bad json" });
    }
    try {
      send(200, await fn(args));
    } catch (e) {
      log("ERR", route, e.message);
      send(500, { ok: false, error: e.message });
    }
  });
});

server.listen(PORT, HOST, () => {
  log(`browserd listening on http://${HOST}:${PORT} profile=${PROFILE}`);
  if (process.send) process.send("ready");
});

// If the port is already bound, another daemon won the start race. Exit
// cleanly so the racing starter just uses the existing one — never crash-loop.
server.on("error", (e) => {
  if (e.code === "EADDRINUSE") {
    log("port in use, another browserd is running — exiting cleanly");
    process.exit(0);
  }
  log("FATAL server error:", e.message);
  process.exit(1);
});

// Clean Chrome shutdown on termination so we never leave a stale profile lock.
async function gracefulExit() {
  try {
    if (context) await context.close();
  } catch {}
  process.exit(0);
}
process.on("SIGTERM", gracefulExit);
process.on("SIGINT", gracefulExit);
