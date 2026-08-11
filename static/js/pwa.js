/* Unified-site PWA registration, verified quarter downloads, and update state. */
(() => {
  "use strict";

  const scriptUrl = document.currentScript?.src || "";
  const siteRoot = new URL("../", scriptUrl || location.href);
  const CONTENT_CACHE = "bsb-content-v1";
  const RUNTIME_CACHE = "bsb-runtime-v1";
  const META_CACHE = "bsb-meta-v1";
  const CONTENT_PATH = "__bsb_content__/";
  const META_PATH = "__bsb_meta__/";
  const QUEUE_META = "queue.json";
  const HEX_64 = /^[a-f0-9]{64}$/;
  const QUARTER = /^\d{4}-(?:01|04|07|10)$/;
  const RETRY_DELAYS = Object.freeze([1000, 3000, 10000]);
  const MAX_CONCURRENT_RESOURCES = 3;
  const listeners = new Set();
  let queueRunner = null;
  let installPrompt = null;
  let updateRegistration = null;
  let refreshRequested = false;

  function supported() {
    return "serviceWorker" in navigator && "caches" in window && Boolean(crypto?.subtle);
  }

  function absolute(relative = "") {
    return new URL(relative, siteRoot);
  }

  function contentRequest(hash) {
    return new Request(absolute(`${CONTENT_PATH}${hash}`));
  }

  function metaRequest(name) {
    return new Request(absolute(`${META_PATH}${name}`));
  }

  function quarterMetaName(quarter) {
    return `quarters/${quarter}.json`;
  }

  async function readMeta(name) {
    const cache = await caches.open(META_CACHE);
    const response = await cache.match(metaRequest(name));
    if (!response) return null;
    try {
      return await response.json();
    } catch {
      return null;
    }
  }

  async function writeMeta(name, value) {
    const cache = await caches.open(META_CACHE);
    await cache.put(
      metaRequest(name),
      new Response(JSON.stringify(value), {
        headers: { "Content-Type": "application/json; charset=utf-8" },
      }),
    );
    notify();
  }

  async function deleteMeta(name) {
    const cache = await caches.open(META_CACHE);
    await cache.delete(metaRequest(name));
    notify();
  }

  function notify() {
    window.dispatchEvent(new CustomEvent("bsb:pwa-state"));
    for (const listener of listeners) listener();
  }

  function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function safeResource(value) {
    if (!value || typeof value !== "object") throw new Error("资源记录无效");
    const relative = value.url;
    if (
      typeof relative !== "string"
      || !relative
      || relative.startsWith("/")
      || relative.includes("\\")
      || relative.includes("?")
      || relative.includes("#")
      || relative.split("/").some((part) => part === "." || part === "..")
    ) throw new Error("资源路径无效");
    const url = absolute(relative);
    if (url.origin !== location.origin || !url.href.startsWith(siteRoot.href)) {
      throw new Error("资源超出站点范围");
    }
    if (!HEX_64.test(value.content_hash)) throw new Error("资源哈希无效");
    if (!Number.isInteger(value.size_bytes) || value.size_bytes < 0) {
      throw new Error("资源大小无效");
    }
    return {
      url: relative,
      content_hash: value.content_hash,
      size_bytes: value.size_bytes,
    };
  }

  function validateQuarterManifest(value, expectedQuarter) {
    if (
      !value
      || typeof value !== "object"
      || value.quarter !== expectedQuarter
      || typeof value.revision !== "string"
      || !value.revision
      || !Array.isArray(value.resources)
      || value.resources.length === 0
    ) throw new Error("季度清单无效");
    const seen = new Set();
    const resources = value.resources.map((item) => {
      const resource = safeResource(item);
      if (seen.has(resource.url)) throw new Error("季度清单含重复资源");
      seen.add(resource.url);
      return resource;
    });
    return { quarter: expectedQuarter, revision: value.revision, resources };
  }

  async function fetchQuarterManifest(quarter) {
    if (!QUARTER.test(quarter)) throw new Error("季度编号无效");
    const response = await fetch(absolute(`data/offline/${quarter}.json`), {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!response.ok) throw new Error("季度清单不可用");
    return validateQuarterManifest(await response.json(), quarter);
  }

  async function sha256(buffer) {
    const digest = await crypto.subtle.digest("SHA-256", buffer);
    return [...new Uint8Array(digest)]
      .map((value) => value.toString(16).padStart(2, "0"))
      .join("");
  }

  async function verifiedResponse(response, resource) {
    if (!response || !response.ok) throw new Error("资源请求失败");
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength !== resource.size_bytes) throw new Error("资源大小校验失败");
    if (await sha256(buffer) !== resource.content_hash) throw new Error("资源哈希校验失败");
    return new Response(buffer, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  }

  async function existingContent(resource) {
    const cache = await caches.open(CONTENT_CACHE);
    const key = contentRequest(resource.content_hash);
    const response = await cache.match(key);
    if (!response) return false;
    try {
      await verifiedResponse(response, resource);
      return true;
    } catch {
      await cache.delete(key);
      return false;
    }
  }

  async function promoteRuntime(resource) {
    const runtime = await caches.open(RUNTIME_CACHE);
    const candidates = [
      new Request(absolute(resource.url)),
      new Request(absolute(`${resource.url}?v=${resource.content_hash}`)),
    ];
    for (const request of candidates) {
      const response = await runtime.match(request);
      if (!response) continue;
      try {
        const verified = await verifiedResponse(response, resource);
        const content = await caches.open(CONTENT_CACHE);
        await content.put(contentRequest(resource.content_hash), verified);
        for (const candidate of candidates) await runtime.delete(candidate);
        return true;
      } catch {
        await runtime.delete(request);
      }
    }
    return false;
  }

  function delay(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  }

  async function fetchVerified(resource) {
    let lastError = null;
    for (let attempt = 0; attempt <= RETRY_DELAYS.length; attempt += 1) {
      if (attempt > 0) await delay(RETRY_DELAYS[attempt - 1]);
      try {
        const response = await fetch(absolute(resource.url), {
          cache: "no-store",
          credentials: "same-origin",
        });
        const verified = await verifiedResponse(response, resource);
        const content = await caches.open(CONTENT_CACHE);
        await content.put(contentRequest(resource.content_hash), verified);
        return;
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError || new Error("资源请求失败");
  }

  async function ensureResource(resource) {
    if (await existingContent(resource)) return;
    if (await promoteRuntime(resource)) return;
    await fetchVerified(resource);
  }

  function initialQuarterState(quarter) {
    return {
      schema: 1,
      quarter,
      status: "NONE",
      active: null,
      staging: null,
      error: null,
    };
  }

  async function getQuarterState(quarter) {
    return await readMeta(quarterMetaName(quarter)) || initialQuarterState(quarter);
  }

  async function listQuarterStates() {
    if (!supported()) return [];
    const cache = await caches.open(META_CACHE);
    const values = [];
    for (const request of await cache.keys()) {
      if (!request.url.includes(`${META_PATH}quarters/`)) continue;
      const response = await cache.match(request);
      if (!response) continue;
      try {
        const value = await response.json();
        if (QUARTER.test(value?.quarter || "")) values.push(value);
      } catch {
        // Invalid records are not displayed as downloaded quarters.
      }
    }
    return values.sort((left, right) => right.quarter.localeCompare(left.quarter));
  }

  async function saveQuarterState(state) {
    await writeMeta(quarterMetaName(state.quarter), state);
  }

  async function currentQueue() {
    return await readMeta(QUEUE_META) || {
      schema: 1,
      state: "idle",
      labels: [],
      current: null,
      completed: [],
      progress: null,
      errors: [],
    };
  }

  async function saveQueue(queue) {
    await writeMeta(QUEUE_META, queue);
  }

  function shortError(error) {
    const text = error instanceof Error ? error.message : String(error || "下载失败");
    return text
      .replace(/https?:\/\/\S+/g, "资源")
      .replace(/[A-Za-z]:[\\/]\S+/g, "本地路径")
      .slice(0, 160);
  }

  async function waitUntilRunnable() {
    while (true) {
      const queue = await currentQueue();
      if (queue.state === "cancelled" || queue.state === "idle") return false;
      if (queue.state === "downloading" && navigator.onLine) return true;
      await delay(150);
    }
  }

  async function downloadResources(quarter, state) {
    const resources = state.staging.resources;
    const verified = new Set(state.staging.verified_hashes || []);
    let cursor = 0;
    let failure = null;

    async function worker() {
      while (cursor < resources.length && !failure) {
        if (!await waitUntilRunnable()) return;
        if (cursor >= resources.length || failure) return;
        const resource = resources[cursor];
        cursor += 1;
        if (verified.has(resource.content_hash)) continue;
        try {
          await ensureResource(resource);
          verified.add(resource.content_hash);
          state = {
            ...state,
            staging: { ...state.staging, verified_hashes: [...verified].sort() },
          };
          await saveQuarterState(state);
          const queue = await currentQueue();
          await saveQueue({
            ...queue,
            progress: {
              quarter,
              verified_resources: verified.size,
              total_resources: resources.length,
              verified_bytes: resources
                .filter((item) => verified.has(item.content_hash))
                .reduce((total, item) => total + item.size_bytes, 0),
              total_bytes: resources.reduce((total, item) => total + item.size_bytes, 0),
            },
          });
        } catch (error) {
          failure = error;
        }
      }
    }

    await Promise.all(
      Array.from(
        { length: Math.min(MAX_CONCURRENT_RESOURCES, resources.length) },
        () => worker(),
      ),
    );
    if (failure) throw failure;
    if (verified.size !== new Set(resources.map((item) => item.content_hash)).size) {
      throw new Error("季度资源尚未完整校验");
    }
    return state;
  }

  async function downloadQuarter(quarter) {
    let state = await getQuarterState(quarter);
    let manifest;
    try {
      manifest = await fetchQuarterManifest(quarter);
    } catch (error) {
      state = { ...state, status: state.active ? "UPDATE_AVAILABLE" : "INCOMPLETE", error: shortError(error) };
      await saveQuarterState(state);
      throw error;
    }
    if (state.active?.revision === manifest.revision && !state.staging) {
      state = { ...state, status: "COMPLETE", error: null };
      await saveQuarterState(state);
      return state;
    }
    if (state.staging?.revision !== manifest.revision) {
      state = {
        ...state,
        status: "INCOMPLETE",
        staging: { ...manifest, verified_hashes: [] },
        error: null,
      };
      await saveQuarterState(state);
    }
    try {
      state = await downloadResources(quarter, state);
      state = {
        ...state,
        status: "COMPLETE",
        active: manifest,
        staging: null,
        error: null,
      };
      await saveQuarterState(state);
      await garbageCollect();
      return state;
    } catch (error) {
      state = {
        ...state,
        status: "INCOMPLETE",
        error: shortError(error),
      };
      await saveQuarterState(state);
      throw error;
    }
  }

  function normalizeQuarterLabels(labels) {
    return [...new Set(labels.filter((label) => QUARTER.test(label)))].sort().reverse();
  }

  async function enqueue(labels) {
    if (!supported()) throw new Error("浏览器不支持离线下载");
    const normalized = normalizeQuarterLabels(labels);
    const queue = {
      schema: 1,
      state: navigator.onLine ? "downloading" : "waiting-network",
      labels: normalized,
      current: null,
      completed: [],
      progress: null,
      errors: [],
    };
    await saveQueue(queue);
    runQueue();
    return queue;
  }

  async function processQueue() {
    while (true) {
      let queue = await currentQueue();
      if (["idle", "paused", "cancelled"].includes(queue.state)) return;
      if (!navigator.onLine) {
        if (queue.state !== "waiting-network") {
          queue = { ...queue, state: "waiting-network" };
          await saveQueue(queue);
        }
        if (!await waitUntilRunnable()) return;
        continue;
      }
      if (queue.state === "waiting-network") {
        queue = { ...queue, state: "downloading" };
        await saveQueue(queue);
      }
      const remaining = queue.labels.filter((label) => !queue.completed.includes(label));
      if (!remaining.length) {
        await saveQueue({ ...queue, state: "idle", current: null, progress: null });
        return;
      }
      const quarter = remaining[0];
      await saveQueue({ ...queue, current: quarter });
      try {
        await downloadQuarter(quarter);
        queue = await currentQueue();
        if (queue.state === "cancelled") return;
        await saveQueue({
          ...queue,
          completed: [...new Set([...queue.completed, quarter])],
          current: null,
          progress: null,
        });
      } catch (error) {
        queue = await currentQueue();
        if (["paused", "cancelled", "waiting-network"].includes(queue.state)) return;
        await saveQueue({
          ...queue,
          completed: [...new Set([...queue.completed, quarter])],
          current: null,
          progress: null,
          errors: [...queue.errors, { quarter, stage: "resource", summary: shortError(error) }],
        });
      }
    }
  }

  function runQueue() {
    if (!queueRunner) {
      queueRunner = processQueue().finally(async () => {
        queueRunner = null;
        const queue = await currentQueue();
        if (queue.state === "downloading" && navigator.onLine) runQueue();
      });
    }
    return queueRunner;
  }

  async function pauseQueue() {
    const queue = await currentQueue();
    if (["downloading", "waiting-network"].includes(queue.state)) {
      await saveQueue({ ...queue, state: "paused" });
    }
  }

  async function resumeQueue() {
    const queue = await currentQueue();
    if (["paused", "waiting-network", "cancelled"].includes(queue.state)) {
      await saveQueue({
        ...queue,
        state: navigator.onLine ? "downloading" : "waiting-network",
      });
      runQueue();
    }
  }

  async function cancelQueue() {
    const queue = await currentQueue();
    await saveQueue({ ...queue, state: "cancelled", labels: [], current: null });
  }

  async function referencedHashes() {
    const keep = new Set();
    const shell = await readMeta("shell.json");
    for (const item of shell?.resources || []) keep.add(item.content_hash);
    for (const state of await listQuarterStates()) {
      for (const manifest of [state.active, state.staging]) {
        for (const item of manifest?.resources || []) keep.add(item.content_hash);
      }
    }
    return keep;
  }

  async function garbageCollect() {
    const keep = await referencedHashes();
    const content = await caches.open(CONTENT_CACHE);
    for (const request of await content.keys()) {
      const hash = request.url.slice(request.url.lastIndexOf("/") + 1);
      if (!keep.has(hash)) await content.delete(request);
    }
  }

  async function removeQuarter(quarter) {
    await deleteMeta(quarterMetaName(quarter));
    await garbageCollect();
  }

  async function detectUpdates() {
    if (!navigator.onLine) return [];
    const changed = [];
    for (let state of await listQuarterStates()) {
      if (!state.active) continue;
      try {
        const current = await fetchQuarterManifest(state.quarter);
        if (current.revision !== state.active.revision) {
          state = { ...state, status: "UPDATE_AVAILABLE" };
          await saveQuarterState(state);
          changed.push(state.quarter);
        }
      } catch {
        // Update detection is best effort and never damages active metadata.
      }
    }
    return changed;
  }

  async function restoreQueue() {
    if (!supported()) return;
    const queue = await currentQueue();
    if (queue.state === "downloading" || queue.state === "waiting-network") {
      await saveQueue({
        ...queue,
        state: navigator.onLine ? "downloading" : "waiting-network",
      });
      if (navigator.onLine) runQueue();
    }
  }

  async function openLatestQuarter() {
    const root = document.querySelector('[data-page="root"]');
    if (!root) return;
    try {
      const response = await fetch(root.dataset.archiveIndexUrl, {
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("archive index unavailable");
      const payload = await response.json();
      if (typeof payload.latest_quarter !== "string") throw new Error("archive empty");
      window.location.replace(`${payload.latest_quarter}/index.html`);
    } catch {
      root.querySelector("[data-root-loading]")?.setAttribute("hidden", "");
      root.querySelector("[data-root-fallback]")?.removeAttribute("hidden");
    }
  }

  function watchRegistration(registration) {
    const inspect = () => {
      if (registration.waiting && navigator.serviceWorker.controller) {
        updateRegistration = registration;
        document.documentElement.dataset.pwaUpdateAvailable = "true";
        notify();
      }
    };
    inspect();
    registration.addEventListener("updatefound", () => {
      registration.installing?.addEventListener("statechange", inspect);
    });
  }

  async function refreshApp() {
    const registration = updateRegistration || await navigator.serviceWorker.ready;
    if (!registration.waiting) return false;
    refreshRequested = true;
    registration.waiting.postMessage({ type: "SKIP_WAITING" });
    return true;
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    notify();
  });

  window.addEventListener("online", async () => {
    const queue = await currentQueue();
    if (queue.state === "waiting-network") await resumeQueue();
    notify();
  });
  window.addEventListener("offline", async () => {
    const queue = await currentQueue();
    if (queue.state === "downloading") {
      await saveQueue({ ...queue, state: "waiting-network" });
    }
    notify();
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (refreshRequested) window.location.reload();
    });
    window.addEventListener("load", async () => {
      try {
        const workerUrl = new URL("../sw.js", scriptUrl);
        const scopeUrl = new URL("../", scriptUrl);
        const registration = await navigator.serviceWorker.register(workerUrl, {
          scope: scopeUrl.pathname,
        });
        watchRegistration(registration);
      } catch {
        // PWA enhancement failure must not block the online archive.
      }
      await restoreQueue();
    });
  }

  window.BsbPwa = Object.freeze({
    CONTENT_CACHE,
    RUNTIME_CACHE,
    META_CACHE,
    RETRY_DELAYS,
    MAX_CONCURRENT_RESOURCES,
    supported,
    validateQuarterManifest,
    fetchQuarterManifest,
    getQuarterState,
    listQuarterStates,
    currentQueue,
    enqueue,
    pauseQueue,
    resumeQueue,
    cancelQueue,
    removeQuarter,
    detectUpdates,
    garbageCollect,
    subscribe,
    refreshApp,
    installPrompt: () => installPrompt,
    promptInstall: async () => installPrompt?.prompt(),
    updateAvailable: () => Boolean(updateRegistration?.waiting),
  });

  openLatestQuarter();
})();
