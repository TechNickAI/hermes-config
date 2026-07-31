/**
 * Window/tab registry for browserd — the (window, tab) → page bookkeeping.
 *
 * Extracted from browserd.mjs so it can be tested directly. browserd.mjs starts
 * a real Chrome on import, which makes the daemon itself untestable in CI; this
 * module holds the pure bookkeeping and takes its browser context and clock as
 * parameters, so tests drive the SAME code the daemon runs rather than a copy.
 *
 * That distinction matters: a test that reimplements the logic it is checking
 * passes even when the shipped code regresses. (Caught in review on PR #77.)
 */

/**
 * Create a registry over a `windows` Map of window → Map(tab → {page, lastUsed}).
 *
 * @param {object} opts
 * @param {Map} opts.windows        registry of window → tab Map
 * @param {Function} opts.ensureContext  async () => browser context with .newPage()
 * @param {Function} [opts.now]     clock, defaults to Date.now
 */
export function createPageRegistry({ windows, ensureContext, now = Date.now }) {
  // Per-(window,tab) creation locks so two concurrent calls for the same tab
  // don't both newPage() and orphan one. Keyed "window\u0000tab".
  const pageLocks = new Map();

  /** Get-or-create the tab Map for a window. MUST stay synchronous. */
  function tabsFor(window) {
    // No await between get and set: Node's single-threaded model then makes
    // this atomic, so two concurrent callers for the same new window can't
    // each create a map and overwrite the other.
    let tabs = windows.get(window);
    if (!tabs) {
      tabs = new Map();
      windows.set(window, tabs);
    }
    return tabs;
  }

  async function getPage(window = "default", tab = "main") {
    const ctx = await ensureContext();
    const tabs = tabsFor(window);
    const entry = tabs.get(tab);
    if (entry && !entry.page.isClosed()) {
      entry.lastUsed = now();
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
          existing.lastUsed = now();
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
        // Re-resolve the window's tab map instead of reusing the `tabs`
        // reference captured before the await. A /close for this window during
        // newPage() detaches that Map from `windows` (close() calls
        // windows.delete), and a later getPage() installs a fresh one. Writing
        // the new page into the detached Map would leave it open in Chrome but
        // unreachable by any lookup — an orphan tab that leaks until the whole
        // context closes.
        tabsFor(window).set(tab, { page, lastUsed: now() });
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

  /** Close one tab, or the whole window when `tab` is omitted. */
  async function closePage({ window = "default", tab } = {}) {
    const tabs = windows.get(window);
    if (!tabs) return { ok: true };
    if (tab) {
      const e = tabs.get(tab);
      if (e) {
        await e.page.close().catch(() => {});
        tabs.delete(tab);
      }
      if (tabs.size === 0) windows.delete(window);
    } else {
      for (const e of tabs.values()) await e.page.close().catch(() => {});
      windows.delete(window);
    }
    return { ok: true };
  }

  /** Close tabs idle longer than `idleMs`. Never reaps default/main. */
  function reapIdle(idleMs, onReap = () => {}) {
    const t = now();
    for (const [window, tabs] of windows) {
      for (const [tab, { page, lastUsed }] of tabs) {
        if (window === "default" && tab === "main") continue;
        if (t - lastUsed > idleMs) {
          onReap(window, tab);
          page.close().catch(() => {});
          tabs.delete(tab);
        }
      }
      if (tabs.size === 0) windows.delete(window);
    }
  }

  return { getPage, closePage, reapIdle };
}
