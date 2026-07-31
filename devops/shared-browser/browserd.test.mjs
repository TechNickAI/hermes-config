/**
 * Tests for browserd's window/tab registry — the REAL one.
 *
 * These import `createPageRegistry` from page-registry.mjs, the exact module
 * browserd.mjs runs. An earlier version of this file reimplemented the logic and
 * asserted against the copy; that version passed even when the shipped fix was
 * reverted, so it guarded nothing. Caught in review on PR #77.
 *
 * A fake browser context stands in for Playwright, so these run in milliseconds
 * with no Chrome and no dependencies: `node --test`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { createPageRegistry } from "./page-registry.mjs";

/** Minimal stand-in for a Playwright Page. */
function fakePage(id) {
  const handlers = {};
  return {
    id,
    closed: false,
    isClosed() {
      return this.closed;
    },
    on(event, fn) {
      handlers[event] = fn;
    },
    async close() {
      this.closed = true;
      handlers.close?.();
    },
  };
}

/**
 * Fake context whose newPage() can be held open, letting a test park a caller
 * mid-creation and interleave a close — the exact race from issue #34.
 */
function fakeContext() {
  let counter = 0;
  const gates = [];
  return {
    gates,
    async newPage() {
      counter += 1;
      const gate = gates[counter - 1];
      if (gate) await gate;
      return fakePage(`page${counter}`);
    },
  };
}

/** Is `page` reachable by walking the windows registry? */
function isReachable(windows, page) {
  for (const tabs of windows.values()) {
    for (const entry of tabs.values()) {
      if (entry.page === page) return true;
    }
  }
  return false;
}

test("close racing newPage does not orphan the created page", async () => {
  // Issue #34's exact interleaving:
  //   1. Request A calls getPage("w","tab1") and blocks inside newPage().
  //   2. /close runs for "w", detaching the Map A captured.
  //   3. Request B calls getPage("w","tab2"), installing a fresh Map.
  //   4. A's newPage() resolves and records its page.
  const windows = new Map();
  const ctx = fakeContext();
  let releaseA;
  ctx.gates[0] = new Promise((resolve) => {
    releaseA = resolve;
  });

  const { getPage, closePage } = createPageRegistry({
    windows,
    ensureContext: async () => ctx,
  });

  const aPromise = getPage("w", "tab1"); // step 1
  await Promise.resolve(); // let A reach the await
  await closePage({ window: "w" }); // step 2
  const pageB = await getPage("w", "tab2"); // step 3
  releaseA();
  const pageA = await aPromise; // step 4

  assert.equal(
    isReachable(windows, pageA),
    true,
    "page A must be reachable through the windows registry, not leaked into a detached Map",
  );
  assert.equal(isReachable(windows, pageB), true, "page B must remain reachable");

  const tabs = windows.get("w");
  assert.ok(tabs, "window 'w' must exist");
  assert.equal(tabs.get("tab1").page, pageA);
  assert.equal(tabs.get("tab2").page, pageB);
  assert.equal(windows.size, 1, "exactly one window map should be registered");
});

test("a page created after its window is closed is still reachable", async () => {
  // Degenerate case: window closed and never re-created by another request.
  const windows = new Map();
  const ctx = fakeContext();
  let release;
  ctx.gates[0] = new Promise((resolve) => {
    release = resolve;
  });

  const { getPage, closePage } = createPageRegistry({
    windows,
    ensureContext: async () => ctx,
  });

  const promise = getPage("w", "tab1");
  await Promise.resolve();
  await closePage({ window: "w" });
  release();
  const page = await promise;

  assert.equal(
    isReachable(windows, page),
    true,
    "the page must be registered under a freshly installed window map",
  );
});

test("concurrent callers for the same tab share one page", async () => {
  // The pageLocks contract: two racing getPage calls must not both newPage().
  const windows = new Map();
  const ctx = fakeContext();
  const { getPage } = createPageRegistry({ windows, ensureContext: async () => ctx });

  const [p1, p2] = await Promise.all([getPage("w", "t"), getPage("w", "t")]);
  assert.equal(p1, p2, "both callers must receive the same page");
  assert.equal(windows.get("w").size, 1, "only one tab should be registered");
});

test("separate windows never collide", async () => {
  // The core promise of the window model: parallel agents don't clobber.
  const windows = new Map();
  const ctx = fakeContext();
  const { getPage } = createPageRegistry({ windows, ensureContext: async () => ctx });

  const [a, b] = await Promise.all([getPage("agent1", "main"), getPage("agent2", "main")]);
  assert.notEqual(a, b);
  assert.equal(windows.get("agent1").get("main").page, a);
  assert.equal(windows.get("agent2").get("main").page, b);
});

test("a closed page deregisters itself and prunes its empty window", async () => {
  const windows = new Map();
  const ctx = fakeContext();
  const { getPage } = createPageRegistry({ windows, ensureContext: async () => ctx });

  const page = await getPage("w", "t");
  await page.close(); // fires the 'close' handler browserd registers
  assert.equal(windows.has("w"), false, "empty window map should be pruned");
});

test("getPage replaces a page that was closed underneath it", async () => {
  const windows = new Map();
  const ctx = fakeContext();
  const { getPage } = createPageRegistry({ windows, ensureContext: async () => ctx });

  const first = await getPage("w", "t");
  first.closed = true; // closed without firing the handler (crash / external close)
  const second = await getPage("w", "t");

  assert.notEqual(second, first, "a stale closed page must not be handed back");
  assert.equal(windows.get("w").get("t").page, second);
});

test("closing one tab leaves the window's other tabs intact", async () => {
  const windows = new Map();
  const ctx = fakeContext();
  const { getPage, closePage } = createPageRegistry({
    windows,
    ensureContext: async () => ctx,
  });

  const keep = await getPage("w", "keep");
  await getPage("w", "drop");
  await closePage({ window: "w", tab: "drop" });

  assert.equal(windows.get("w").has("drop"), false);
  assert.equal(windows.get("w").get("keep").page, keep);
});

test("reapIdle closes stale tabs but spares default/main", async () => {
  const windows = new Map();
  const ctx = fakeContext();
  let clock = 1000;
  const { getPage, reapIdle } = createPageRegistry({
    windows,
    ensureContext: async () => ctx,
    now: () => clock,
  });

  const protectedPage = await getPage("default", "main");
  const stale = await getPage("agent", "scratch");

  clock += 10_000; // both are now well past the idle threshold
  const reaped = [];
  reapIdle(5_000, (w, t) => reaped.push(`${w}/${t}`));

  assert.deepEqual(reaped, ["agent/scratch"], "only the non-default tab is reaped");
  assert.equal(stale.closed, true, "the stale page should be closed");
  assert.equal(protectedPage.closed, false, "default/main must never be reaped");
  assert.equal(windows.get("default").get("main").page, protectedPage);
  assert.equal(windows.has("agent"), false, "emptied window should be pruned");
});

test("reapIdle keeps recently used tabs", async () => {
  const windows = new Map();
  const ctx = fakeContext();
  let clock = 1000;
  const { getPage, reapIdle } = createPageRegistry({
    windows,
    ensureContext: async () => ctx,
    now: () => clock,
  });

  const page = await getPage("agent", "active");
  clock += 1_000; // still inside the idle window
  reapIdle(5_000);

  assert.equal(page.closed, false);
  assert.equal(windows.get("agent").get("active").page, page);
});
