/**
 * Regression test for the orphan-tab leak in getPage().
 *
 * The bug (issue #34): getPage() captured the window's tab Map, then awaited
 * ctx.newPage(). If a /close for that window landed during the await, close()
 * called windows.delete(window), detaching the captured Map. A later getPage()
 * installed a *fresh* Map. When the original newPage() resolved it wrote into
 * the detached Map — leaving a real Chrome page that no windows lookup could
 * ever reach. It leaked until the entire browser context closed.
 *
 * Reproducing it needs only the Map bookkeeping, not a real browser, so this
 * models getPage()'s exact structure against a fake newPage() whose resolution
 * we control. `node --test` runs it with no Playwright and no Chrome.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

/**
 * The BUGGY implementation: writes to the tab Map captured before the await.
 */
function makeBuggyGetPage(windows, newPage) {
  return async function getPage(window, tab) {
    let tabs = windows.get(window);
    if (!tabs) {
      tabs = new Map();
      windows.set(window, tabs);
    }
    const page = await newPage();
    tabs.set(tab, { page, lastUsed: Date.now() }); // stale reference
    return page;
  };
}

/**
 * The FIXED implementation: re-resolves the live Map after the await.
 * Mirrors browserd.mjs getPage().
 */
function makeFixedGetPage(windows, newPage) {
  return async function getPage(window, tab) {
    let tabs = windows.get(window);
    if (!tabs) {
      tabs = new Map();
      windows.set(window, tabs);
    }
    const page = await newPage();
    let live = windows.get(window);
    if (!live) {
      live = new Map();
      windows.set(window, live);
    }
    live.set(tab, { page, lastUsed: Date.now() });
    return page;
  };
}

/** Close a whole window, exactly as browserd's close() does. */
function closeWindow(windows, window) {
  windows.delete(window);
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

/**
 * Drive the exact interleaving from issue #34:
 *   1. Request A calls getPage("w", "tab1") and blocks in newPage().
 *   2. /close runs for "w", detaching the Map A captured.
 *   3. Request B calls getPage("w", "tab2"), installing a fresh Map.
 *   4. A's newPage() resolves and records its page.
 */
async function runRace(makeGetPage) {
  const windows = new Map();
  let releaseA;
  const aBlocked = new Promise((resolve) => {
    releaseA = resolve;
  });

  let call = 0;
  const newPage = async () => {
    call += 1;
    if (call === 1) {
      await aBlocked; // Request A parks here
      return { id: "pageA" };
    }
    return { id: `page${call}` };
  };

  const getPage = makeGetPage(windows, newPage);

  const aPromise = getPage("w", "tab1"); // step 1
  await Promise.resolve(); // let A reach the await
  closeWindow(windows, "w"); // step 2
  const pageB = await getPage("w", "tab2"); // step 3
  releaseA();
  const pageA = await aPromise; // step 4

  return { windows, pageA, pageB };
}

test("the race leaks an unreachable page under the buggy implementation", async () => {
  const { windows, pageA } = await runRace(makeBuggyGetPage);
  // Documents the bug this fix removes: A's page is open but orphaned.
  assert.equal(
    isReachable(windows, pageA),
    false,
    "expected the buggy version to orphan page A (if this fails, the test no longer models the bug)",
  );
});

test("close racing newPage does not orphan the created page", async () => {
  const { windows, pageA, pageB } = await runRace(makeFixedGetPage);

  assert.equal(
    isReachable(windows, pageA),
    true,
    "page A must be reachable through the windows registry, not leaked",
  );
  assert.equal(isReachable(windows, pageB), true, "page B must remain reachable");

  // Both tabs land in the same live window map.
  const tabs = windows.get("w");
  assert.ok(tabs, "window 'w' must exist");
  assert.equal(tabs.get("tab1").page, pageA);
  assert.equal(tabs.get("tab2").page, pageB);
  assert.equal(windows.size, 1, "exactly one window map should be registered");
});

test("a page created after its window is closed is still reachable", async () => {
  // Degenerate case: window closed and never re-created by another request.
  const windows = new Map();
  let release;
  const blocked = new Promise((resolve) => {
    release = resolve;
  });
  const newPage = async () => {
    await blocked;
    return { id: "solo" };
  };
  const getPage = makeFixedGetPage(windows, newPage);

  const promise = getPage("w", "tab1");
  await Promise.resolve();
  closeWindow(windows, "w");
  release();
  const page = await promise;

  assert.equal(
    isReachable(windows, page),
    true,
    "the page must be registered under a freshly installed window map",
  );
});
