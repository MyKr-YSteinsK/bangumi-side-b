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
  let queueMutation = Promise.resolve();

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

  function progressMetaName(quarter) {
    return `progress/${quarter}.json`;
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
    renderUpdateNotice();
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
    const queue = await readMeta(QUEUE_META) || emptyQueue();
    if (!queue.current) return queue;
    return {
      ...queue,
      progress: await readMeta(progressMetaName(queue.current)),
    };
  }

  function emptyQueue() {
    return {
      schema: 1,
      state: "idle",
      labels: [],
      current: null,
      completed: [],
      progress: null,
      errors: [],
    };
  }

  function updateQueue(mutator) {
    const operation = queueMutation.then(async () => {
      const current = await readMeta(QUEUE_META) || emptyQueue();
      const next = mutator(current);
      await writeMeta(QUEUE_META, next);
      return next;
    });
    queueMutation = operation.catch(() => {});
    return operation;
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
          await writeMeta(progressMetaName(quarter), {
            quarter,
            verified_resources: verified.size,
            total_resources: resources.length,
            verified_bytes: resources
              .filter((item) => verified.has(item.content_hash))
              .reduce((total, item) => total + item.size_bytes, 0),
            total_bytes: resources.reduce((total, item) => total + item.size_bytes, 0),
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
    await updateQueue(() => queue);
    runQueue();
    return queue;
  }

  async function processQueue() {
    while (true) {
      let queue = await currentQueue();
      if (["idle", "paused", "cancelled"].includes(queue.state)) return;
      if (!navigator.onLine) {
        if (queue.state !== "waiting-network") {
          queue = await updateQueue((current) => (
            current.state === "downloading"
              ? { ...current, state: "waiting-network" }
              : current
          ));
        }
        if (!await waitUntilRunnable()) return;
        continue;
      }
      if (queue.state === "waiting-network") {
        queue = await updateQueue((current) => (
          current.state === "waiting-network" && navigator.onLine
            ? { ...current, state: "downloading" }
            : current
        ));
      }
      const remaining = queue.labels.filter((label) => !queue.completed.includes(label));
      if (!remaining.length) {
        await updateQueue((current) => ({
          ...current,
          state: "idle",
          current: null,
          progress: null,
        }));
        return;
      }
      const quarter = remaining[0];
      await deleteMeta(progressMetaName(quarter));
      queue = await updateQueue((current) => (
        current.state === "downloading"
        && current.labels.includes(quarter)
        && !current.completed.includes(quarter)
          ? { ...current, current: quarter }
          : current
      ));
      if (queue.state !== "downloading" || queue.current !== quarter) continue;
      try {
        await downloadQuarter(quarter);
        queue = await updateQueue((current) => {
          if (current.state === "cancelled") return current;
          return {
            ...current,
            completed: [...new Set([...current.completed, quarter])],
            current: null,
            progress: null,
          };
        });
        if (queue.state === "cancelled") return;
      } catch (error) {
        queue = await updateQueue((current) => {
          if (["paused", "cancelled", "waiting-network"].includes(current.state)) {
            return current;
          }
          return {
            ...current,
            completed: [...new Set([...current.completed, quarter])],
            current: null,
            progress: null,
            errors: [...current.errors, { quarter, stage: "resource", summary: shortError(error) }],
          };
        });
        if (["paused", "cancelled", "waiting-network"].includes(queue.state)) return;
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
    await updateQueue((queue) => (
      ["downloading", "waiting-network"].includes(queue.state)
        ? { ...queue, state: "paused" }
        : queue
    ));
  }

  async function resumeQueue() {
    const queue = await updateQueue((current) => (
      ["paused", "waiting-network", "cancelled"].includes(current.state)
        ? {
            ...current,
            state: navigator.onLine ? "downloading" : "waiting-network",
          }
        : current
    ));
    if (["downloading", "waiting-network"].includes(queue.state)) {
      runQueue();
    }
  }

  async function cancelQueue() {
    await updateQueue((queue) => ({
      ...queue,
      state: "cancelled",
      labels: [],
      current: null,
    }));
  }

  async function referencedHashes() {
    const keep = new Set();
    const shell = await readMeta("shell.json");
    for (const item of shell?.resources || []) keep.add(item.content_hash);
    const meta = await caches.open(META_CACHE);
    for (const request of await meta.keys()) {
      if (!request.url.includes(`${META_PATH}shell-pending-`)) continue;
      const response = await meta.match(request);
      if (!response) continue;
      try {
        const pending = await response.json();
        for (const item of pending?.resources || []) {
          if (HEX_64.test(item?.content_hash)) keep.add(item.content_hash);
        }
      } catch {
        // Invalid pending metadata cannot establish an offline guarantee.
      }
    }
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
    await deleteMeta(progressMetaName(quarter));
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
    const queue = await updateQueue((current) => (
      current.state === "downloading" || current.state === "waiting-network"
        ? {
            ...current,
            state: navigator.onLine ? "downloading" : "waiting-network",
          }
        : current
    ));
    if (queue.state === "downloading" || queue.state === "waiting-network") {
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

  async function promptInstall() {
    if (!installPrompt) return null;
    const prompt = installPrompt;
    installPrompt = null;
    await prompt.prompt();
    const result = await prompt.userChoice;
    notify();
    return result;
  }

  let archiveIndex = null;
  let knownOfflineQuarters = [];
  let persistenceResult = null;
  const settingsSelection = { kind: "current", year: "", from: "", to: "" };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatBytes(value) {
    if (!Number.isFinite(value) || value < 0) return "—";
    if (value < 1024) return `${value} B`;
    if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
    if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
    return `${(value / 1024 ** 3).toFixed(1)} GiB`;
  }

  function quarterLabels() {
    const publicLabels = Array.isArray(archiveIndex?.quarters) ? archiveIndex.quarters
      .map((item) => item?.quarter)
      .filter((label) => QUARTER.test(label || ""))
      : [];
    return [...new Set([...publicLabels, ...knownOfflineQuarters])].sort().reverse();
  }

  function years() {
    return [...new Set(quarterLabels().map((label) => label.slice(0, 4)))].sort().reverse();
  }

  function selectedQueueLabels() {
    const labels = quarterLabels();
    if (settingsSelection.kind === "current") return labels.slice(0, 1);
    if (settingsSelection.kind === "year") {
      return labels.filter((label) => label.startsWith(`${settingsSelection.year}-`));
    }
    if (settingsSelection.kind === "range") {
      const from = Number(settingsSelection.from);
      const to = Number(settingsSelection.to);
      if (!Number.isInteger(from) || !Number.isInteger(to)) return [];
      const lower = Math.min(from, to);
      const upper = Math.max(from, to);
      return labels.filter((label) => {
        const year = Number(label.slice(0, 4));
        return year >= lower && year <= upper;
      });
    }
    return labels;
  }

  async function renderAppSettings(container) {
    if (!container) return;
    const hasSupport = supported();
    const controlled = Boolean(navigator.serviceWorker?.controller);
    const canInstall = Boolean(installPrompt);
    container.innerHTML = `
      <dl class="settings-facts">
        <div><dt>PWA</dt><dd>${hasSupport ? "supported" : "unavailable"}</dd></div>
        <div><dt>Service Worker</dt><dd>${controlled ? "controlling" : "not controlling"}</dd></div>
        <div><dt>App update</dt><dd>${updateRegistration?.waiting ? "available" : "current"}</dd></div>
      </dl>
      ${canInstall ? '<button type="button" class="button button--ink" data-install-app>安装应用</button>' : '<p class="settings-note">如浏览器支持，可从浏览器菜单选择“安装”或“添加到主屏幕”。</p>'}
    `;
    container.querySelector("[data-install-app]")?.addEventListener("click", async () => {
      await promptInstall();
      renderSettings();
    });
  }

  async function renderStorageSettings(container) {
    if (!container) return;
    let estimate = null;
    let persisted = "unsupported";
    try {
      estimate = await navigator.storage?.estimate?.();
    } catch {
      estimate = null;
    }
    try {
      if (navigator.storage?.persisted) {
        persisted = await navigator.storage.persisted() ? "granted" : "not granted";
      }
    } catch {
      persisted = "unsupported";
    }
    const estimateHtml = estimate
      ? `<dl class="settings-facts"><div><dt>Usage</dt><dd>${formatBytes(estimate.usage)}</dd></div><div><dt>Quota</dt><dd>${formatBytes(estimate.quota)}</dd></div></dl>`
      : '<p class="settings-note">浏览器未提供存储估算</p>';
    container.innerHTML = `${estimateHtml}<p class="settings-note">Persistent storage: <strong>${persistenceResult || persisted}</strong></p>${navigator.storage?.persist ? '<button type="button" class="button" data-request-persistence>申请持久存储</button>' : ""}`;
    container.querySelector("[data-request-persistence]")?.addEventListener("click", async () => {
      try {
        persistenceResult = await navigator.storage.persist() ? "granted" : "not granted";
      } catch {
        persistenceResult = "unsupported";
      }
      renderSettings();
    });
  }

  function statusLabel(status) {
    return {
      NONE: "未下载",
      INCOMPLETE: "INCOMPLETE",
      COMPLETE: "已离线",
      UPDATE_AVAILABLE: "有更新",
    }[status] || "未下载";
  }

  function actionLabel(status) {
    return {
      NONE: "下载",
      INCOMPLETE: "继续",
      COMPLETE: "移除",
      UPDATE_AVAILABLE: "更新",
    }[status] || "下载";
  }

  async function renderQuarterSettings(container) {
    if (!container) return;
    const states = await listQuarterStates();
    knownOfflineQuarters = states.map((state) => state.quarter);
    const stateByQuarter = new Map(states.map((state) => [state.quarter, state]));
    const labels = quarterLabels();
    if (!labels.length) {
      container.innerHTML = '<p class="settings-note">当前没有可公开季度。</p>';
      return;
    }
    container.innerHTML = `<div class="offline-quarter-list">${labels.map((quarter) => {
      const state = stateByQuarter.get(quarter) || initialQuarterState(quarter);
      return `<article class="offline-quarter" data-offline-quarter="${quarter}">
        <div><strong>${quarter}</strong><span>${statusLabel(state.status)}</span>${state.error ? `<small>${escapeHtml(state.error)}</small>` : ""}</div>
        <button type="button" class="button" data-quarter-action="${actionLabel(state.status)}">${actionLabel(state.status)}</button>
      </article>`;
    }).join("")}</div>`;
    container.querySelectorAll("[data-offline-quarter]").forEach((row) => {
      const quarter = row.dataset.offlineQuarter;
      const state = stateByQuarter.get(quarter) || initialQuarterState(quarter);
      row.querySelector("[data-quarter-action]")?.addEventListener("click", async () => {
        if (state.status === "COMPLETE") {
          if (!window.confirm(`移除 ${quarter} 的离线缓存？`)) return;
          await removeQuarter(quarter);
        } else {
          await enqueue([quarter]);
        }
        renderSettings();
      });
    });
  }

  function renderQueueSelector(container) {
    if (!container) return;
    const availableYears = years();
    if (!settingsSelection.year) settingsSelection.year = availableYears[0] || "";
    if (!settingsSelection.from) settingsSelection.from = availableYears.at(-1) || "";
    if (!settingsSelection.to) settingsSelection.to = availableYears[0] || "";
    const yearOptions = availableYears
      .map((year) => `<option value="${year}">${year}</option>`)
      .join("");
    const labels = selectedQueueLabels();
    container.innerHTML = `<div class="queue-selector">
      <label>范围<select data-queue-kind>
        <option value="current">当前季度</option><option value="year">指定年份</option>
        <option value="range">年份范围</option><option value="all">全部季度</option>
      </select></label>
      <label data-queue-year ${settingsSelection.kind === "year" ? "" : "hidden"}>年份<select>${yearOptions}</select></label>
      <div class="queue-range" data-queue-range ${settingsSelection.kind === "range" ? "" : "hidden"}>
        <label>从<select>${yearOptions}</select></label><label>到<select>${yearOptions}</select></label>
      </div>
      <p class="queue-preview"><strong>${labels.length}</strong> 个季度 · newest → oldest</p>
      <button type="button" class="button button--ink" data-start-queue ${!labels.length || !navigator.onLine ? "disabled" : ""}>加入下载队列</button>
    </div>`;
    const kind = container.querySelector("[data-queue-kind]");
    kind.value = settingsSelection.kind;
    kind.addEventListener("change", () => {
      settingsSelection.kind = kind.value;
      renderSettings();
    });
    const year = container.querySelector("[data-queue-year] select");
    if (year) {
      year.value = settingsSelection.year;
      year.addEventListener("change", () => {
        settingsSelection.year = year.value;
        renderSettings();
      });
    }
    const range = container.querySelectorAll("[data-queue-range] select");
    if (range.length === 2) {
      range[0].value = settingsSelection.from;
      range[1].value = settingsSelection.to;
      range[0].addEventListener("change", () => {
        settingsSelection.from = range[0].value;
        renderSettings();
      });
      range[1].addEventListener("change", () => {
        settingsSelection.to = range[1].value;
        renderSettings();
      });
    }
    container.querySelector("[data-start-queue]")?.addEventListener("click", async () => {
      await enqueue(selectedQueueLabels());
      renderSettings();
    });
  }

  async function renderQueue(container) {
    if (!container) return;
    const queue = await currentQueue();
    const progress = queue.progress;
    const stateLabel = {
      idle: "队列空闲",
      downloading: "正在下载",
      paused: "已暂停",
      "waiting-network": "等待网络",
      cancelled: "已取消",
    }[queue.state] || queue.state;
    container.innerHTML = `<div class="queue-status">
      <p><strong>${stateLabel}</strong>${queue.current ? ` · ${queue.current}` : ""}</p>
      ${progress ? `<p>${progress.verified_resources} / ${progress.total_resources} resources<br>${formatBytes(progress.verified_bytes)} / ${formatBytes(progress.total_bytes)}<br>${queue.completed.length} / ${queue.labels.length} quarters</p>` : `<p>${queue.completed.length} / ${queue.labels.length} quarters</p>`}
      ${queue.errors.length ? `<ul class="queue-errors">${queue.errors.map((error) => `<li><strong>${escapeHtml(error.quarter)}</strong> · ${escapeHtml(error.stage)} · ${escapeHtml(error.summary)}</li>`).join("")}</ul>` : ""}
      <div class="queue-actions">
        ${["downloading", "waiting-network"].includes(queue.state) ? '<button type="button" class="button" data-queue-pause>暂停</button>' : ""}
        ${queue.state === "paused" ? '<button type="button" class="button button--ink" data-queue-resume>继续</button>' : ""}
        ${["downloading", "waiting-network", "paused"].includes(queue.state) ? '<button type="button" class="button" data-queue-cancel>取消</button>' : ""}
      </div></div>`;
    container.querySelector("[data-queue-pause]")?.addEventListener("click", pauseQueue);
    container.querySelector("[data-queue-resume]")?.addEventListener("click", resumeQueue);
    container.querySelector("[data-queue-cancel]")?.addEventListener("click", cancelQueue);
  }

  async function renderSettings() {
    const root = document.querySelector("[data-pwa-settings]");
    if (!root) return;
    await Promise.all([
      renderAppSettings(root.querySelector("[data-settings-app]")),
      renderStorageSettings(root.querySelector("[data-settings-storage]")),
      renderQuarterSettings(root.querySelector("[data-settings-quarters]")),
      renderQueue(root.querySelector("[data-settings-queue]")),
    ]);
    renderQueueSelector(root.querySelector("[data-settings-selector]"));
  }

  async function initializeSettings() {
    const root = document.querySelector("[data-pwa-settings]");
    if (!root) return;
    try {
      const response = await fetch(root.dataset.archiveIndexUrl, {
        credentials: "same-origin",
      });
      if (response.ok) archiveIndex = await response.json();
    } catch {
      archiveIndex = null;
    }
    subscribe(renderSettings);
    await renderSettings();
    await detectUpdates();
  }

  function renderUpdateNotice() {
    const notice = document.querySelector("[data-pwa-update-notice]");
    if (!notice) return;
    notice.hidden = !updateRegistration?.waiting;
    const refresh = notice.querySelector("[data-pwa-refresh]");
    if (refresh) refresh.onclick = refreshApp;
  }

  async function renderQuarterOfflineControl() {
    const root = document.querySelector("[data-quarter-offline]");
    if (!root) return;
    const status = root.querySelector("[data-quarter-offline-status]");
    const actions = root.querySelector("[data-quarter-offline-actions]");
    const quarter = root.dataset.quarter;
    if (!supported()) {
      status.textContent = "当前浏览器不支持离线下载；在线浏览仍可正常使用。";
      actions.replaceChildren();
      return;
    }
    const [state, queue] = await Promise.all([
      getQuarterState(quarter),
      currentQueue(),
    ]);
    const progress = queue.current === quarter ? queue.progress : null;
    if (progress) {
      const total = progress.total_bytes || progress.total_resources;
      const completed = progress.total_bytes
        ? progress.verified_bytes
        : progress.verified_resources;
      const percent = total ? Math.floor((completed / total) * 100) : 0;
      status.textContent = `${statusLabel(state.status)} · ${percent}%`;
    } else {
      status.textContent = statusLabel(state.status);
    }
    actions.replaceChildren();
    if (queue.current === quarter && ["downloading", "waiting-network"].includes(queue.state)) {
      const link = document.createElement("a");
      link.href = "../settings/index.html";
      link.textContent = queue.state === "waiting-network" ? "等待网络 · 打开 Settings" : "查看下载队列";
      actions.append(link);
      return;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = `button${state.status === "NONE" ? " button--ink" : ""}`;
    button.textContent = state.status === "NONE"
      ? "下载当前季度供离线使用"
      : state.status === "INCOMPLETE"
        ? "继续离线下载"
        : state.status === "UPDATE_AVAILABLE"
          ? "更新离线资料"
          : "移除离线缓存";
    button.disabled = !navigator.onLine && state.status !== "COMPLETE";
    button.addEventListener("click", async () => {
      if (state.status === "COMPLETE") {
        if (!window.confirm(`移除 ${quarter} 的离线缓存？`)) return;
        await removeQuarter(quarter);
      } else {
        await enqueue([quarter]);
      }
      renderQuarterOfflineControl();
    });
    actions.append(button);
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
    await updateQueue((queue) => (
      queue.state === "downloading"
        ? { ...queue, state: "waiting-network" }
        : queue
    ));
    notify();
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (refreshRequested) window.location.reload();
      else notify();
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
    promptInstall,
    updateAvailable: () => Boolean(updateRegistration?.waiting),
  });

  renderUpdateNotice();
  initializeSettings();
  subscribe(renderQuarterOfflineControl);
  renderQuarterOfflineControl();
  openLatestQuarter();
})();
