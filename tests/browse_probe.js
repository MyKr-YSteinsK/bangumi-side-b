/* Playwright-only measurements. This file is never a site input. */
(() => {
  const metrics = {};
  const stamps = {};
  let action = null;

  function measured(name, started) {
    const entry = metrics[name] ||= { calls: 0, ms: 0 };
    entry.calls += 1;
    entry.ms += performance.now() - started;
  }

  function instrument(globalName, methods) {
    let api;
    Object.defineProperty(window, globalName, {
      configurable: true,
      get: () => api,
      set(value) {
        api = { ...value };
        for (const name of methods) {
          const original = value[name];
          if (typeof original !== "function") continue;
          api[name] = (...args) => {
            const started = performance.now();
            const result = original(...args);
            if (result instanceof Promise) {
              return result.finally(() => measured(name, started));
            }
            measured(name, started);
            return result;
          };
        }
      },
    });
  }

  instrument("BsbArchive", [
    "recordsFromCatalog", "recordsFromQuarter", "applyPipeline",
    "filterOptionMetadata", "browseCoverPath", "reconcileChildren",
  ]);
  instrument("BsbPwa", ["readActiveQuarterData", "listQuarterStates"]);
  const createElement = Document.prototype.createElement;
  Document.prototype.createElement = function (tag, ...args) {
    if (tag === "article") {
      const entry = metrics.createdArticles ||= { calls: 0, ms: 0 };
      entry.calls += 1;
    }
    return createElement.call(this, tag, ...args);
  };
  const readJson = Response.prototype.json;
  Response.prototype.json = function (...args) {
    const pathname = this.url && new URL(this.url).pathname;
    if (!pathname?.includes("/data/")) return readJson.call(this, ...args);
    const started = performance.now();
    return readJson.call(this, ...args).finally(() => measured("readJson", started));
  };

  const select = (selector) => document.querySelector(selector);
  const root = () => select('[data-archive-app][data-page="archive"]');
  const ready = () => root()?.getAttribute("aria-busy") === "false"
    && select(".master-pane")?.inert === false;
  const rows = () => [...document.querySelectorAll(".result-list .subject-row")]
    .filter((row) => !row.closest("[hidden]"));
  const scope = () => select("[data-archive-scope-label]")?.textContent || "";
  const detail = () => select("[data-detail-panel]:not([hidden]) h2");
  const summary = () => select("[data-results-summary]")?.textContent || "";

  function markFrame(name, predicate, start = 0) {
    if (stamps[name] !== undefined) return;
    stamps[name] = null;
    // Report DOM readiness separately from the next painted frame opportunity.
    const beforeFrame = performance.now();
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        stamps[name] = predicate()
          ? { dom: beforeFrame - start, frame: performance.now() - start }
          : undefined;
      });
    });
  }

  function observe() {
    if (root()?.querySelector("h1") && select("[data-search]")) {
      markFrame("shell", () => Boolean(root()));
    }
    if (ready() && rows().length) {
      markFrame("ready", () => ready() && Boolean(rows()[0]?.getClientRects().length));
      if (stamps.cover === undefined) {
        const cover = rows().map((row) => row.querySelector("img"))
          .find((image) => image?.naturalWidth > 0
            && image.getBoundingClientRect().top < innerHeight);
        if (cover) markFrame("cover", () => cover.isConnected);
      }
    }
    if (action?.start === undefined) return;
    const { name, expected, start } = action;
    const done = {
      search: () => ready() && rows().length > 0
        && rows().every((row) => row.dataset.subjectId === expected),
      year: () => ready() && scope() === expected && rows().length > 0,
      detail: () => detail()?.textContent === expected,
      back: () => ready() && !location.hash && select("[data-detail-panel]")?.hidden
        && rows()[0]?.dataset.subjectId === expected,
      filter: () => select("[data-filter-panel]:not([hidden]) input[data-filter-group]"),
      facet: () => ready() && rows().length > 0
        && rows().every((row) => row.dataset.appearance === "continuing")
        && new URLSearchParams(location.search).get("section") === "continuing",
      local: () => scope().startsWith("LOCAL")
        && select("[data-local-status]")?.textContent.includes(expected)
        && (!expected.startsWith("本机覆盖") || (ready() && rows().length > 0)),
    }[name];
    if (done()) markFrame(name, done, start);
  }
  new MutationObserver(observe).observe(document, {
    childList: true, subtree: true, attributes: true, characterData: true,
  });

  function start(event) {
    if (!action || action.start !== undefined) return;
    const target = event.target;
    if (target instanceof Element && target.closest(action.selector)) {
      action.start = performance.now();
      observe();
    }
  }
  document.addEventListener("click", start, true);
  document.addEventListener("input", start, true);
  document.addEventListener("load", observe, true);

  function resourceEntries() {
    return performance.getEntriesByType("resource").map((entry) => ({
      path: new URL(entry.name).pathname,
      start: entry.startTime,
      responseStart: entry.responseStart,
      end: entry.responseEnd,
      bytes: entry.encodedBodySize,
      transfers: entry.transferSize,
    }));
  }

  window.browseProbe = {
    stamps,
    arm(name, selector, expected) {
      delete stamps[name];
      action = { name, selector, expected };
    },
    snapshot() {
      return {
        stamps, metrics, summary: summary(), scope: scope(),
        rowIds: rows().map((row) => row.dataset.recordKey),
        resources: resourceEntries(),
      };
    },
  };
})();
