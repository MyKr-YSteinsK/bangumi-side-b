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
  const QUEUE_LOCK_NAME = "bsb-offline-queue-runner";
  const QUEUE_MUTATION_LOCK_NAME = "bsb-offline-queue-mutation";
  const FALLBACK_QUEUE_LOCK_PREFIX = "locks/queue-mutation/";
  const FALLBACK_QUEUE_LOCK_TIMEOUT = 5000;
  const FALLBACK_QUEUE_LOCK_LEASE = 30000;
  const QUARTER_MUTATION_LOCK_PREFIX = "bsb-offline-quarter-";
  const CONTENT_MAINTENANCE_LOCK_NAME = "bsb-pwa-content-maintenance";
  const STATE_CHANNEL_NAME = "bsb-pwa-state";
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
  let queueRetryTimer = null;
  let stateChannel = null;
  let capabilityState = "unsupported";
  let registrationPromise = null;
  let serviceWorkerRegistration = null;
  let registrationError = null;
  let fallbackQueueLockSequence = 0;
  let activeFallbackQueueLock = null;
  const fallbackQueueLockPage = crypto.randomUUID?.()
    || [...crypto.getRandomValues(new Uint8Array(16))]
      .map((value) => value.toString(16).padStart(2, "0"))
      .join("");
  const quarterMutations = new Map();
  const watchedRegistrations = new WeakSet();
  const SETTINGS_AREAS = Object.freeze(["app", "storage", "quarter", "queue", "selector"]);
  try {
    if (typeof BroadcastChannel === "function") stateChannel = new BroadcastChannel(STATE_CHANNEL_NAME);
  } catch {
    stateChannel = null;
  }

  function supported() {
    return "serviceWorker" in navigator && "caches" in window && Boolean(crypto?.subtle);
  }

  if (supported()) capabilityState = "registering";

  function capabilityLabel() {
    return {
      unsupported: "unavailable",
      registering: "registering",
      ready: "ready",
      "registration-failed": "registration failed",
    }[capabilityState] || capabilityState;
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
    await assertFallbackQueueMutationLock(cache);
    await cache.put(
      metaRequest(name),
      new Response(JSON.stringify(value), {
        headers: { "Content-Type": "application/json; charset=utf-8" },
      }),
    );
    notify({ areas: metaAreas(name), name });
  }

  async function deleteMeta(name) {
    const cache = await caches.open(META_CACHE);
    await assertFallbackQueueMutationLock(cache);
    await cache.delete(metaRequest(name));
    notify({ areas: metaAreas(name), name });
  }

  function metaAreas(name) {
    if (name === QUEUE_META) return ["queue", "quarter"];
    if (name?.startsWith("progress/")) return ["queue", "quarter"];
    if (name?.startsWith("quarters/")) return ["quarter", "storage"];
    if (name?.startsWith("shell")) return ["app"];
    return [...SETTINGS_AREAS];
  }

  function normalizeNotify(value) {
    if (!value || typeof value !== "object") return { areas: [...SETTINGS_AREAS] };
    const areas = Array.isArray(value.areas)
      ? value.areas.filter((area) => SETTINGS_AREAS.includes(area))
      : [...SETTINGS_AREAS];
    return { ...value, areas: areas.length ? [...new Set(areas)] : [...SETTINGS_AREAS] };
  }

  function notify(eventOrBroadcast = true, broadcast = true) {
    const event = typeof eventOrBroadcast === "boolean"
      ? normalizeNotify(null)
      : normalizeNotify(eventOrBroadcast);
    if (typeof eventOrBroadcast === "boolean") broadcast = eventOrBroadcast;
    renderUpdateNotice();
    window.dispatchEvent(new CustomEvent("bsb:pwa-state", { detail: event }));
    for (const listener of listeners) listener(event);
    if (broadcast) stateChannel?.postMessage({ type: "state-changed", ...event });
  }

  function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function contentLocksAvailable() {
    return typeof navigator.locks?.request === "function";
  }

  function withContentReferenceLease(callback) {
    if (!contentLocksAvailable()) return callback();
    let entered = false;
    return navigator.locks.request(
      CONTENT_MAINTENANCE_LOCK_NAME,
      { mode: "shared" },
      async () => {
        entered = true;
        return callback();
      },
    ).catch((error) => {
      if (entered) throw error;
      return callback();
    });
  }

  async function withContentGcLock(callback) {
    if (!contentLocksAvailable()) return false;
    let entered = false;
    try {
      return await navigator.locks.request(
        CONTENT_MAINTENANCE_LOCK_NAME,
        { mode: "exclusive" },
        async () => {
          entered = true;
          await callback();
          return true;
        },
      );
    } catch (error) {
      if (entered) throw error;
      return false;
    }
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
      || !HEX_64.test(value.data_revision || "")
      || !Array.isArray(value.resources)
      || value.resources.length === 0
    ) throw new Error("季度清单无效");
    const seen = new Set();
    const sizesByHash = new Map();
    const resources = value.resources.map((item) => {
      const resource = safeResource(item);
      if (seen.has(resource.url)) throw new Error("季度清单含重复资源");
      const knownSize = sizesByHash.get(resource.content_hash);
      if (knownSize !== undefined && knownSize !== resource.size_bytes) {
        throw new Error("季度清单含冲突资源大小");
      }
      sizesByHash.set(resource.content_hash, resource.size_bytes);
      seen.add(resource.url);
      return resource;
    });
    return {
      quarter: expectedQuarter,
      revision: value.revision,
      data_revision: value.data_revision,
      resources,
    };
  }

  function effectiveDataRevision(manifest) {
    if (HEX_64.test(manifest?.data_revision || "")) return manifest.data_revision;
    const quarter = manifest?.quarter;
    const resources = Array.isArray(manifest?.resources) ? manifest.resources : [];
    const resource = resources.find(
      (item) => item?.url === `data/quarters/${quarter}.json`,
    );
    return HEX_64.test(resource?.content_hash || "") ? resource.content_hash : null;
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
    const body = response.clone();
    const buffer = await body.arrayBuffer();
    if (buffer.byteLength !== resource.size_bytes) throw new Error("资源大小校验失败");
    if (await sha256(buffer) !== resource.content_hash) throw new Error("资源哈希校验失败");
    return response.clone();
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
      await withContentGcLock(async () => {
        await cache.delete(key);
      });
      return false;
    }
  }

  async function putContent(resource, response, generation = null) {
    return withContentReferenceLease(async () => {
      if (generation) await assertQueueGeneration(generation);
      const content = await caches.open(CONTENT_CACHE);
      await content.put(contentRequest(resource.content_hash), response);
    });
  }

  async function promoteRuntime(resource, generation = null) {
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
        await putContent(resource, verified, generation);
        for (const candidate of candidates) await runtime.delete(candidate);
        return true;
      } catch (error) {
        if (error instanceof StaleQueueError) throw error;
        await runtime.delete(request);
      }
    }
    return false;
  }

  function delay(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  }

  async function fetchVerified(resource, generation = null) {
    let lastError = null;
    for (let attempt = 0; attempt <= RETRY_DELAYS.length; attempt += 1) {
      if (generation) await assertQueueGeneration(generation);
      if (attempt > 0) {
        await delay(RETRY_DELAYS[attempt - 1]);
        if (generation) await assertQueueGeneration(generation);
      }
      try {
        const response = await fetch(absolute(resource.url), {
          cache: "no-store",
          credentials: "same-origin",
        });
        const verified = await verifiedResponse(response, resource);
        await putContent(resource, verified, generation);
        return;
      } catch (error) {
        if (error instanceof StaleQueueError) throw error;
        lastError = error;
      }
    }
    throw lastError || new Error("资源请求失败");
  }

  async function ensureResource(resource, generation = null) {
    if (await existingContent(resource)) return;
    if (await promoteRuntime(resource, generation)) return;
    await fetchVerified(resource, generation);
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

  async function writeQuarterStateUnlocked(state) {
    await writeMeta(quarterMetaName(state.quarter), state);
  }

  async function deleteQuarterStateUnlocked(quarter) {
    await deleteMeta(quarterMetaName(quarter));
  }

  async function deleteQuarterProgressUnlocked(quarter) {
    await deleteMeta(progressMetaName(quarter));
  }

  function newGeneration() {
    if (typeof crypto?.randomUUID === "function") return crypto.randomUUID();
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function emptyQueue() {
    return {
      schema: 2,
      generation: null,
      state: "idle",
      labels: [],
      current: null,
      succeeded: [],
      failed: [],
      errors: [],
    };
  }

  function queueList(values) {
    return normalizeQuarterLabels(Array.isArray(values) ? values : []);
  }

  function normalizeQueue(value) {
    if (!value || typeof value !== "object" || value.schema !== 2) {
      return emptyQueue();
    }
    const queue = emptyQueue();
    const labels = queueList(value.labels);
    const succeeded = queueList(value.succeeded);
    const failed = queueList(value.failed).filter((label) => !succeeded.includes(label));
    return {
      ...queue,
      generation: typeof value.generation === "string" && value.generation
        ? value.generation
        : null,
      state: [
        "idle",
        "downloading",
        "waiting-network",
        "waiting-service-worker",
        "paused",
        "cancelled",
      ].includes(value.state) ? value.state : "idle",
      labels,
      current: QUARTER.test(value.current || "") && labels.includes(value.current)
        ? value.current
        : null,
      succeeded: succeeded.filter((label) => labels.includes(label)),
      failed: failed.filter((label) => labels.includes(label)),
      errors: Array.isArray(value.errors)
        ? value.errors.filter((item) => item && typeof item === "object").slice(-50)
        : [],
    };
  }

  async function readQueue() {
    return normalizeQueue(await readMeta(QUEUE_META));
  }

  async function currentQueue() {
    const queue = await readQueue();
    if (!queue.current) return { ...queue, completed: [...queue.succeeded], progress: null };
    return {
      ...queue,
      completed: [...queue.succeeded],
      progress: await readMeta(progressMetaName(queue.current)),
    };
  }

  function enqueueQueueMutation(operationCallback) {
    const operation = queueMutation.then(async () => {
      const mutate = async () => operationCallback();
      if (typeof navigator.locks?.request !== "function") {
        return withFallbackQueueMutationLock(mutate);
      }
      let entered = false;
      try {
        return await navigator.locks.request(
          QUEUE_MUTATION_LOCK_NAME,
          { mode: "exclusive" },
          async () => {
            entered = true;
            return mutate();
          },
        );
      } catch (error) {
        if (entered) throw error;
        return mutate();
      }
    });
    queueMutation = operation.catch(() => {});
    return operation;
  }

  function fallbackQueueLockName(kind, token) {
    return `${FALLBACK_QUEUE_LOCK_PREFIX}${kind}/${token}.json`;
  }

  async function fallbackQueueLockKeys(cache, kind) {
    const prefix = absolute(`${META_PATH}${FALLBACK_QUEUE_LOCK_PREFIX}${kind}/`).href;
    return (await cache.keys()).filter((request) => request.url.startsWith(prefix));
  }

  async function fallbackQueueLockRecords(cache, kind) {
    const records = [];
    for (const request of await fallbackQueueLockKeys(cache, kind)) {
      const response = await cache.match(request);
      if (!response) continue;
      try {
        const value = await response.json();
        if (
          typeof value?.token === "string"
          && value.token
          && Number.isFinite(value?.expires_at)
        ) {
          if (value.expires_at <= Date.now()) await cache.delete(request);
          else records.push(value);
          continue;
        }
      } catch {
        // Fall through to the fail-closed error below.
      }
      throw new Error("离线队列锁记录无效");
    }
    return records;
  }

  async function fallbackQueueTickets(cache) {
    const tickets = await fallbackQueueLockRecords(cache, "tickets");
    if (tickets.some((value) => !Number.isInteger(value.ticket) || value.ticket <= 0)) {
      throw new Error("离线队列锁记录无效");
    }
    return tickets;
  }

  async function assertFallbackQueueMutationLock(cache) {
    if (!activeFallbackQueueLock) return;
    const response = await cache.match(activeFallbackQueueLock.ticketKey);
    if (!response) throw new Error("离线队列锁已失效");
    try {
      const value = await response.json();
      if (
        value?.token === activeFallbackQueueLock.token
        && Number.isFinite(value?.expires_at)
        && value.expires_at > Date.now()
      ) return;
    } catch {
      // Fall through to the stale-owner error below.
    }
    throw new Error("离线队列锁已失效");
  }

  async function withFallbackQueueMutationLock(callback) {
    const cache = await caches.open(META_CACHE);
    fallbackQueueLockSequence += 1;
    const token = `${fallbackQueueLockPage}-${fallbackQueueLockSequence}`;
    const choosing = metaRequest(fallbackQueueLockName("choosing", token));
    const ticketKey = metaRequest(fallbackQueueLockName("tickets", token));
    const deadline = Date.now() + FALLBACK_QUEUE_LOCK_TIMEOUT;
    const expiresAt = Date.now() + FALLBACK_QUEUE_LOCK_LEASE;
    let ticketWritten = false;
    await cache.put(choosing, new Response(JSON.stringify({ token, expires_at: expiresAt })));
    try {
      const currentTickets = await fallbackQueueTickets(cache);
      const ticket = currentTickets.reduce(
        (maximum, item) => Math.max(maximum, item.ticket),
        0,
      ) + 1;
      await cache.put(
        ticketKey,
        new Response(JSON.stringify({ ticket, token, expires_at: expiresAt }), {
          headers: { "Content-Type": "application/json; charset=utf-8" },
        }),
      );
      ticketWritten = true;
      await cache.delete(choosing);

      while (true) {
        const choosingRecords = await fallbackQueueLockRecords(cache, "choosing");
        if (!choosingRecords.length) {
          const tickets = await fallbackQueueTickets(cache);
          tickets.sort((left, right) => (
            left.ticket - right.ticket || left.token.localeCompare(right.token)
          ));
          if (tickets[0]?.token === token) {
            activeFallbackQueueLock = { token, ticketKey };
            try {
              return await callback();
            } finally {
              activeFallbackQueueLock = null;
            }
          }
        }
        if (Date.now() >= deadline) {
          throw new Error("无法安全串行化离线队列，请关闭其他页面后重试");
        }
        await delay(10);
      }
    } finally {
      await cache.delete(choosing);
      if (ticketWritten) await cache.delete(ticketKey);
    }
  }

  function updateQueue(mutator) {
    return enqueueQueueMutation(async () => {
      const current = await readQueue();
      const next = normalizeQueue(mutator(current));
      await writeMeta(QUEUE_META, next);
      return next;
    });
  }

  function updateOwnedQueue(generation, mutator) {
    return updateQueue((current) => (
      current.generation === generation ? mutator(current) : current
    ));
  }

  function quarterMutationLockName(quarter) {
    return `${QUARTER_MUTATION_LOCK_PREFIX}${quarter}-mutation`;
  }

  function mergeQueueLabels(queue, labels) {
    const additions = queueList([...(queue.labels || []), ...labels]);
    const current = queue.current && additions.includes(queue.current) ? queue.current : null;
    const ordered = current
      ? [current, ...additions.filter((label) => label !== current)]
      : additions;
    const requeued = queueList(labels);
    return {
      ...queue,
      labels: ordered,
      failed: queue.failed.filter((label) => !requeued.includes(label)),
      errors: queue.errors.filter((item) => !requeued.includes(item?.quarter)),
    };
  }

  function canRunGuaranteedQueue() {
    return capabilityState === "ready" && Boolean(serviceWorkerRegistration?.active);
  }

  async function parkQueueForServiceWorker(generation = null) {
    return updateQueue((current) => (
      (!generation || current.generation === generation)
      && ["downloading", "waiting-network"].includes(current.state)
        ? { ...current, state: "waiting-service-worker" }
        : current
    ));
  }

  function shortError(error) {
    const text = error instanceof Error ? error.message : String(error || "下载失败");
    return text
      .replace(/https?:\/\/\S+/g, "资源")
      .replace(/[A-Za-z]:[\\/]\S+/g, "本地路径")
      .slice(0, 160);
  }

  async function waitUntilRunnable(generation) {
    while (true) {
      const queue = await readQueue();
      if (queue.generation !== generation || ["cancelled", "idle"].includes(queue.state)) {
        return false;
      }
      if (!canRunGuaranteedQueue()) {
        await parkQueueForServiceWorker(generation);
        return false;
      }
      if (queue.state === "downloading" && navigator.onLine) return true;
      await delay(150);
    }
  }

  class StaleQueueError extends Error {
    constructor() {
      super("queue generation is no longer owned");
      this.name = "StaleQueueError";
    }
  }

  function withQuarterMutation(quarter, callback) {
    // Every quarter transaction enters the quarter lock before the shared
    // queue lock. Callers must keep network and verification work outside it.
    const previous = quarterMutations.get(quarter) || Promise.resolve();
    const operation = previous.then(async () => withContentReferenceLease(async () => {
      const runWithQueueLock = () => enqueueQueueMutation(callback);
      const runWithQuarterLock = () => {
        if (typeof navigator.locks?.request !== "function") return runWithQueueLock();
        let quarterEntered = false;
        return navigator.locks.request(
          quarterMutationLockName(quarter),
          { mode: "exclusive" },
          async () => {
            quarterEntered = true;
            return runWithQueueLock();
          },
        ).catch((error) => {
          if (quarterEntered) throw error;
          return runWithQueueLock();
        });
      };
      return runWithQuarterLock();
    }));
    const tracked = operation.catch(() => {});
    quarterMutations.set(quarter, tracked);
    return operation.finally(() => {
      if (quarterMutations.get(quarter) === tracked) quarterMutations.delete(quarter);
    });
  }

  function updateOwnedQuarterDownloadState(quarter, generation, mutator) {
    return withQuarterMutation(quarter, async () => {
      const queue = await readQueue();
      if (queue.generation !== generation || queue.state === "cancelled") {
        throw new StaleQueueError();
      }
      const current = await getQuarterState(quarter);
      const progress = await readMeta(progressMetaName(quarter));
      const result = await mutator(current, progress);
      if (!result) return { state: current, progress };
      const latestQueue = await readQueue();
      if (latestQueue.generation !== generation || latestQueue.state === "cancelled") {
        throw new StaleQueueError();
      }
      const nextState = result.state || current;
      if (result.state) await writeQuarterStateUnlocked(nextState);
      if (Object.prototype.hasOwnProperty.call(result, "progress")) {
        if (result.progress === null) await deleteQuarterProgressUnlocked(quarter);
        else await writeMeta(progressMetaName(quarter), result.progress);
      }
      return {
        state: nextState,
        progress: Object.prototype.hasOwnProperty.call(result, "progress")
          ? result.progress
          : progress,
      };
    });
  }

  function updateQuarterDownloadState(quarter, generation, mutator) {
    return updateOwnedQuarterDownloadState(quarter, generation, mutator);
  }

  async function assertQueueGeneration(generation) {
    const queue = await readQueue();
    if (queue.generation !== generation || queue.state === "cancelled") {
      throw new StaleQueueError();
    }
    return queue;
  }

  function quarterProgress(quarter, resources, verified) {
    return {
      quarter,
      verified_resources: resources.filter((item) => verified.has(item.content_hash)).length,
      total_resources: resources.length,
      verified_bytes: resources
        .filter((item) => verified.has(item.content_hash))
        .reduce((total, item) => total + item.size_bytes, 0),
      total_bytes: resources.reduce((total, item) => total + item.size_bytes, 0),
    };
  }

  async function downloadResources(quarter, manifest, state, generation) {
    const resources = manifest.resources;
    const verified = new Set(state.staging?.verified_hashes || []);
    const inFlight = new Map();
    let cursor = 0;
    let failure = null;

    function ensureShared(resource) {
      const hash = resource.content_hash;
      if (!inFlight.has(hash)) {
        const operation = ensureResource(resource, generation).finally(() => inFlight.delete(hash));
        inFlight.set(hash, operation);
      }
      return inFlight.get(hash);
    }

    async function worker() {
      while (cursor < resources.length && !failure) {
        if (!await waitUntilRunnable(generation)) return;
        if (cursor >= resources.length || failure) return;
        const resourceIndex = cursor;
        cursor += 1;
        await assertQueueGeneration(generation);
        const resource = resources[resourceIndex];
        if (!resource) throw new Error("季度资源索引无效");
        if (verified.has(resource.content_hash)) continue;
        try {
          await ensureShared(resource);
          await assertQueueGeneration(generation);
          if (verified.has(resource.content_hash)) continue;
          // Claim the logical hash before the metadata transaction. A second
          // worker can reuse the completed fetch while the owner commits the
          // hash; a failed commit leaves the persisted closure incomplete and
          // forces the next resume to re-persist it.
          verified.add(resource.content_hash);
          const result = await updateQuarterDownloadState(
            quarter,
            generation,
            (current) => {
              if (!current.staging || current.staging.revision !== manifest.revision) {
                throw new StaleQueueError();
              }
              const nextVerified = new Set(current.staging.verified_hashes || []);
              nextVerified.add(resource.content_hash);
              return {
                state: {
                  ...current,
                  status: "INCOMPLETE",
                  staging: {
                    ...current.staging,
                    verified_hashes: [...nextVerified].sort(),
                  },
                  error: null,
                },
                progress: quarterProgress(quarter, resources, nextVerified),
              };
            },
          );
          for (const hash of result.state.staging?.verified_hashes || []) {
            verified.add(hash);
          }
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
    await assertQueueGeneration(generation);
    if (failure) throw failure;
    if (!resources.every((item) => verified.has(item.content_hash))) {
      throw new Error("季度资源尚未完整校验");
    }
    return await getQuarterState(quarter);
  }

  async function verifyManifestContentClosure(manifest, generation) {
    const unique = new Map();
    for (const resource of manifest.resources) unique.set(resource.content_hash, resource);
    for (const resource of unique.values()) {
      await assertQueueGeneration(generation);
      let ready = await existingContent(resource);
      if (!ready) {
        await ensureResource(resource, generation);
        await assertQueueGeneration(generation);
        ready = await existingContent(resource);
      }
      if (!ready) {
        throw new Error("季度内容缓存未完整校验");
      }
    }
  }

  async function downloadQuarter(quarter, generation) {
    let manifest;
    try {
      await assertQueueGeneration(generation);
      manifest = await fetchQuarterManifest(quarter);
    } catch (error) {
      if (error instanceof StaleQueueError) throw error;
      await updateQuarterDownloadState(quarter, generation, (current) => ({
        state: {
          ...current,
          status: current.active ? "UPDATE_AVAILABLE" : "INCOMPLETE",
          error: shortError(error),
        },
        progress: null,
      }));
      throw error;
    }
    const prepared = await updateQuarterDownloadState(
      quarter,
      generation,
      (current) => {
        if (current.active?.revision === manifest.revision && !current.staging) {
          return {
            state: { ...current, status: "COMPLETE", error: null },
            progress: null,
          };
        }
        const verified = current.staging?.revision === manifest.revision
          ? new Set(current.staging.verified_hashes || [])
          : new Set();
        return {
          state: {
            ...current,
            status: "INCOMPLETE",
            staging: { ...manifest, verified_hashes: [...verified].sort() },
            error: null,
          },
          progress: quarterProgress(quarter, manifest.resources, verified),
        };
      },
    );
    let state = prepared.state;
    if (state.active?.revision === manifest.revision && !state.staging) return state;
    try {
      state = await downloadResources(quarter, manifest, state, generation);
      await assertQueueGeneration(generation);
      await verifyManifestContentClosure(manifest, generation);
      const promoted = await updateQuarterDownloadState(
        quarter,
        generation,
        (current) => {
          if (!current.staging || current.staging.revision !== manifest.revision) {
            throw new Error("季度下载状态已变更");
          }
          const verified = new Set(current.staging.verified_hashes || []);
          if (!manifest.resources.every((item) => verified.has(item.content_hash))) {
            throw new Error("季度资源尚未完整校验");
          }
          return {
            state: {
              ...current,
              status: "COMPLETE",
              active: manifest,
              staging: null,
              error: null,
            },
            progress: null,
          };
        },
      );
      state = promoted.state;
      await garbageCollect();
      return state;
    } catch (error) {
      if (error instanceof StaleQueueError) throw error;
      await updateQuarterDownloadState(quarter, generation, (current) => ({
        state: {
          ...current,
          status: "INCOMPLETE",
          error: shortError(error),
        },
      }));
      throw error;
    }
  }

  async function ensureServiceWorkerReady() {
    if (!supported()) throw new Error("浏览器不支持离线下载");
    const registration = await getOrStartServiceWorkerRegistration();
    if (!registration || capabilityState !== "ready") {
      throw new Error("Service Worker 尚未就绪，无法保证离线下载");
    }
    return registration;
  }

  function normalizeQuarterLabels(labels) {
    return [...new Set(labels.filter((label) => QUARTER.test(label)))].sort().reverse();
  }

  async function enqueue(labels) {
    await ensureServiceWorkerReady();
    const normalized = normalizeQuarterLabels(labels);
    if (!normalized.length) return currentQueue();
    const queue = await updateQueue((current) => {
      if (
        current.generation
        && [
          "downloading",
          "waiting-network",
          "waiting-service-worker",
          "paused",
        ].includes(current.state)
      ) {
        const merged = mergeQueueLabels(current, normalized);
        return current.state === "waiting-service-worker"
          ? {
              ...merged,
              state: navigator.onLine ? "downloading" : "waiting-network",
            }
          : merged;
      }
      return {
        ...emptyQueue(),
        generation: newGeneration(),
        state: navigator.onLine ? "downloading" : "waiting-network",
        labels: normalized,
      };
    });
    runQueue();
    return queue;
  }

  async function processQueue() {
    const initial = await readQueue();
    const generation = initial.generation;
    if (!generation) return;
    while (true) {
      if (!canRunGuaranteedQueue()) {
        await parkQueueForServiceWorker(generation);
        return;
      }
      let queue = await readQueue();
      if (queue.generation !== generation || ["idle", "paused", "cancelled"].includes(queue.state)) return;
      if (queue.state === "waiting-service-worker") {
        queue = await updateOwnedQueue(generation, (current) => (
          current.state === "waiting-service-worker"
            ? {
                ...current,
                state: navigator.onLine ? "downloading" : "waiting-network",
              }
            : current
        ));
        if (queue.state !== "downloading" && queue.state !== "waiting-network") return;
      }
      if (!navigator.onLine) {
        if (queue.state !== "waiting-network") {
          queue = await updateOwnedQueue(generation, (current) => (
            current.state === "downloading"
              ? { ...current, state: "waiting-network" }
              : current
          ));
        }
        if (!await waitUntilRunnable(generation)) return;
        continue;
      }
      if (queue.state === "waiting-network") {
        queue = await updateOwnedQueue(generation, (current) => (
          current.state === "waiting-network" && navigator.onLine
            ? { ...current, state: "downloading" }
            : current
        ));
      }
      const remaining = queue.labels.filter(
        (label) => !queue.succeeded.includes(label) && !queue.failed.includes(label),
      );
      if (!remaining.length) {
        queue = await updateOwnedQueue(generation, (current) => (
          { ...current, state: "idle", current: null }
        ));
        return;
      }
      const quarter = remaining[0];
      queue = await updateOwnedQueue(generation, (current) => (
        current.state === "downloading"
        && current.labels.includes(quarter)
        && !current.succeeded.includes(quarter)
        && !current.failed.includes(quarter)
          ? { ...current, current: quarter }
          : current
      ));
      if (queue.generation !== generation) return;
      if (queue.state !== "downloading" || queue.current !== quarter) continue;
      try {
        await updateOwnedQuarterDownloadState(quarter, generation, () => ({
          progress: null,
        }));
      } catch (error) {
        if (error instanceof StaleQueueError) return;
        throw error;
      }
      try {
        await downloadQuarter(quarter, generation);
        queue = await updateOwnedQueue(generation, (current) => {
          if (current.state === "cancelled") return current;
          return {
            ...current,
            succeeded: [...new Set([...current.succeeded, quarter])],
            current: null,
            errors: current.errors.filter((item) => item?.quarter !== quarter),
          };
        });
        if (queue.generation !== generation || queue.state === "cancelled") return;
      } catch (error) {
        if (error instanceof StaleQueueError) return;
        queue = await updateOwnedQueue(generation, (current) => {
          if (["paused", "cancelled", "waiting-network", "waiting-service-worker"].includes(current.state)) {
            return current;
          }
          return {
            ...current,
            failed: [...new Set([...current.failed, quarter])],
            current: null,
            errors: [
              ...current.errors,
              { quarter, stage: "resource", summary: shortError(error) },
            ].slice(-50),
          };
        });
        if (
          queue.generation !== generation
          || ["paused", "cancelled", "waiting-network", "waiting-service-worker"].includes(queue.state)
        ) return;
      }
    }
  }

  function retryQueueLater() {
    if (queueRetryTimer !== null) return;
    queueRetryTimer = window.setTimeout(() => {
      queueRetryTimer = null;
      runQueue();
    }, 1000);
  }

  async function runQueueWithOwnership() {
    if (typeof navigator.locks?.request !== "function") {
      await processQueue();
      return;
    }
    let acquired = false;
    try {
      await navigator.locks.request(
        QUEUE_LOCK_NAME,
        { mode: "exclusive", ifAvailable: true },
        async (lock) => {
          if (!lock) return;
          acquired = true;
          await processQueue();
        },
      );
    } catch {
      // A browser with a partial Web Locks implementation remains safe via generations.
      await processQueue();
      return;
    }
    if (!acquired) retryQueueLater();
  }

  function runQueue() {
    if (!canRunGuaranteedQueue()) return parkQueueForServiceWorker();
    if (!queueRunner) {
      queueRunner = runQueueWithOwnership().finally(async () => {
        queueRunner = null;
        const queue = await readQueue();
        if (["downloading", "waiting-network"].includes(queue.state) && navigator.onLine) {
          retryQueueLater();
        }
      });
    }
    return queueRunner;
  }

  async function waitForQueueRunnerQuiescent() {
    if (typeof navigator.locks?.request === "function") {
      let entered = false;
      try {
        await navigator.locks.request(
          QUEUE_LOCK_NAME,
          { mode: "exclusive" },
          async () => {
            entered = true;
          },
        );
        return entered;
      } catch {
        return false;
      }
    }
    const runner = queueRunner;
    if (!runner) return false;
    try {
      await runner;
      return true;
    } catch {
      return false;
    }
  }

  stateChannel?.addEventListener("message", async (event) => {
    if (event.data?.type !== "state-changed") return;
    notify({ areas: event.data.areas, reason: "cross-tab" }, false);
    const queue = await readQueue();
    if (["downloading", "waiting-network"].includes(queue.state)) runQueue();
  });

  async function pauseQueue() {
    await updateQueue((queue) => (
      ["downloading", "waiting-network"].includes(queue.state)
        ? { ...queue, state: "paused" }
        : queue
    ));
  }

  async function resumeQueue() {
    if (!canRunGuaranteedQueue()) {
      return updateQueue((current) => (
        ["downloading", "waiting-network", "paused"].includes(current.state)
          ? { ...current, state: "waiting-service-worker" }
          : current
      ));
    }
    const queue = await updateQueue((current) => (
      ["paused", "waiting-network"].includes(current.state)
        ? {
            ...current,
            state: navigator.onLine ? "downloading" : "waiting-network",
          }
        : current.state === "cancelled"
          ? {
              ...current,
              generation: newGeneration(),
              state: navigator.onLine ? "downloading" : "waiting-network",
              failed: [],
              errors: [],
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
    return withContentGcLock(async () => {
      const keep = await referencedHashes();
      const content = await caches.open(CONTENT_CACHE);
      for (const request of await content.keys()) {
        const hash = request.url.slice(request.url.lastIndexOf("/") + 1);
        if (!keep.has(hash)) await content.delete(request);
      }
    });
  }

  async function removeQuarter(quarter) {
    await withQuarterMutation(quarter, async () => {
      const current = await readQueue();
      if (current.current === quarter) {
        throw new Error("请先取消当前下载或更新，再移除季度");
      }
      const labels = current.labels.filter((label) => label !== quarter);
      if (current.labels.includes(quarter)) {
        await writeMeta(QUEUE_META, {
          ...current,
          labels,
          succeeded: current.succeeded.filter((label) => label !== quarter),
          failed: current.failed.filter((label) => label !== quarter),
          errors: current.errors.filter((item) => item?.quarter !== quarter),
          state: labels.length ? current.state : "idle",
          current: labels.length ? current.current : null,
        });
      }
      await deleteQuarterStateUnlocked(quarter);
      await deleteQuarterProgressUnlocked(quarter);
    });
    if (await waitForQueueRunnerQuiescent()) await garbageCollect();
  }

  async function detectUpdates() {
    const result = { dataUpdates: [], packageMaintenance: [], current: [] };
    if (!navigator.onLine) return result;
    for (const snapshot of await listQuarterStates()) {
      if (!snapshot.active) continue;
      try {
        // Network I/O stays outside the quarter transaction. The snapshot is
        // only a compare-and-set guard for the short metadata write below.
        const current = await fetchQuarterManifest(snapshot.quarter);
        const classification = await withQuarterMutation(snapshot.quarter, async () => {
          const latest = await getQuarterState(snapshot.quarter);
          if (
            !latest.active
            || latest.active.revision !== snapshot.active.revision
            || latest.staging
          ) return null;
          const activeDataRevision = effectiveDataRevision(latest.active);
          const hasDataUpdate = activeDataRevision !== current.data_revision;
          const needsMigration = (
            latest.active.data_revision !== current.data_revision
            && activeDataRevision === current.data_revision
          );
          if (hasDataUpdate) {
            await writeQuarterStateUnlocked({ ...latest, status: "UPDATE_AVAILABLE" });
            return "DATA_UPDATE";
          }
          if (needsMigration) {
            await writeQuarterStateUnlocked({
              ...latest,
              active: { ...latest.active, data_revision: current.data_revision },
            });
          }
          if (current.revision === latest.active.revision) return "NONE";
          return "PACKAGE_MAINTENANCE";
        });
        if (classification === "DATA_UPDATE") result.dataUpdates.push(snapshot.quarter);
        if (classification === "PACKAGE_MAINTENANCE") {
          result.packageMaintenance.push(snapshot.quarter);
        }
        if (classification === "NONE") result.current.push(snapshot.quarter);
      } catch {
        // Update detection is best effort and never damages active metadata.
      }
    }
    return result;
  }

  async function restoreQueue() {
    if (!supported()) return;
    const ready = canRunGuaranteedQueue();
    const queue = await updateQueue((current) => (
      ["downloading", "waiting-network", "waiting-service-worker"].includes(current.state)
        ? {
            ...current,
            state: ready
              ? navigator.onLine ? "downloading" : "waiting-network"
              : "waiting-service-worker",
          }
        : current
    ));
    if (ready && (queue.state === "downloading" || queue.state === "waiting-network")) {
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
    if (!registration || watchedRegistrations.has(registration)) return;
    watchedRegistrations.add(registration);
    const inspect = () => {
      if (registration.waiting && navigator.serviceWorker.controller) {
        updateRegistration = registration;
        document.documentElement.dataset.pwaUpdateAvailable = "true";
        notify({ areas: ["app"], reason: "app-update" });
      }
    };
    inspect();
    registration.addEventListener("updatefound", () => {
      registration.installing?.addEventListener("statechange", inspect);
    });
  }

  function waitForActiveRegistration(registration) {
    if (registration?.active) return Promise.resolve(registration);
    return new Promise((resolve, reject) => {
      let settled = false;
      const workers = new Map();
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        registration.removeEventListener("updatefound", inspect);
        for (const [worker, handler] of workers) {
          worker.removeEventListener("statechange", handler);
        }
        callback(value);
      };
      const inspect = () => {
        if (registration.active) {
          finish(resolve, registration);
          return;
        }
        const installing = registration.installing;
        if (installing && !workers.has(installing)) {
          const handler = inspect;
          workers.set(installing, handler);
          installing.addEventListener("statechange", handler);
        }
        if (installing?.state === "redundant" || registration.waiting?.state === "redundant") {
          finish(reject, new Error("Service Worker became redundant before activation"));
        }
      };
      registration.addEventListener("updatefound", inspect);
      navigator.serviceWorker.ready.then((ready) => {
        if (ready?.active) finish(resolve, ready);
        else inspect();
      }).catch((error) => finish(reject, error));
      inspect();
    });
  }

  async function startServiceWorkerRegistration() {
    if (!supported()) {
      capabilityState = "unsupported";
      notify({ areas: ["app", "quarter", "queue", "selector"], reason: "capability" });
      return null;
    }
    capabilityState = "registering";
    registrationError = null;
    notify({ areas: ["app", "quarter", "queue", "selector"], reason: "capability" });
    try {
      const workerUrl = new URL("../sw.js", scriptUrl);
      const scopeUrl = new URL("../", scriptUrl);
      const registration = await navigator.serviceWorker.register(workerUrl, {
        scope: scopeUrl.pathname,
      });
      serviceWorkerRegistration = registration;
      watchRegistration(registration);
      serviceWorkerRegistration = await waitForActiveRegistration(registration);
      if (!serviceWorkerRegistration.active) throw new Error("Service Worker inactive");
      capabilityState = "ready";
      registrationError = null;
      notify({ areas: ["app", "quarter", "queue", "selector"], reason: "capability" });
      await restoreQueue();
      return serviceWorkerRegistration;
    } catch (error) {
      serviceWorkerRegistration = null;
      capabilityState = "registration-failed";
      registrationError = shortError(error);
      notify({ areas: ["app", "quarter", "queue", "selector"], reason: "capability" });
      try {
        await parkQueueForServiceWorker();
      } catch {
        // Queue recovery must not mask the registration failure.
      }
      throw error;
    }
  }

  function getOrStartServiceWorkerRegistration() {
    if (!supported()) {
      capabilityState = "unsupported";
      return Promise.resolve(null);
    }
    if (capabilityState === "ready" && serviceWorkerRegistration?.active) {
      return Promise.resolve(serviceWorkerRegistration);
    }
    if (registrationPromise && capabilityState !== "registration-failed") {
      return registrationPromise;
    }
    const attempt = startServiceWorkerRegistration();
    registrationPromise = attempt;
    attempt.catch(() => {
      if (registrationPromise === attempt) registrationPromise = null;
    });
    return attempt;
  }

  async function retryServiceWorkerRegistration() {
    if (!supported() || capabilityState === "registering") return false;
    if (capabilityState === "ready" && serviceWorkerRegistration?.active) return true;
    registrationPromise = null;
    try {
      await getOrStartServiceWorkerRegistration();
      return capabilityState === "ready" && Boolean(serviceWorkerRegistration?.active);
    } catch {
      return false;
    }
  }

  async function refreshApp() {
    const registration = updateRegistration || serviceWorkerRegistration
      || await navigator.serviceWorker.ready;
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
    notify({ areas: ["app"], reason: "install" });
    return result;
  }

  let archiveIndex = null;
  let knownOfflineQuarters = [];
  let persistenceResult = null;
  const settingsSelection = { kind: "current", year: "", from: "", to: "" };
  const settingsDirty = new Set();
  const settingsWaiters = [];
  let settingsRevision = 0;
  let settingsRenderScheduled = false;
  let settingsRenderActive = false;

  function settingsRoot() {
    return document.querySelector("[data-pwa-settings]");
  }

  function settingsAreas(value) {
    if (!value) return new Set(SETTINGS_AREAS);
    const values = Array.isArray(value) ? value : value.areas;
    if (!Array.isArray(values)) return new Set(SETTINGS_AREAS);
    return new Set(values.filter((area) => SETTINGS_AREAS.includes(area)));
  }

  function scheduleSettingsRender() {
    if (settingsRenderScheduled || settingsRenderActive) return;
    settingsRenderScheduled = true;
    const schedule = window.requestAnimationFrame || ((callback) => window.setTimeout(callback, 0));
    schedule(() => { void flushSettingsRender(); });
  }

  async function flushSettingsRender() {
    settingsRenderScheduled = false;
    settingsRenderActive = true;
    const root = settingsRoot();
    const areas = new Set(settingsDirty);
    settingsDirty.clear();
    const revision = settingsRevision;
    if (root && areas.size) {
      await Promise.all([
        areas.has("app") ? renderAppSettings(root.querySelector("[data-settings-app]"), revision) : null,
        areas.has("storage") ? renderStorageSettings(root.querySelector("[data-settings-storage]"), revision) : null,
        areas.has("quarter") ? renderQuarterSettings(root.querySelector("[data-settings-quarters]"), revision) : null,
        areas.has("queue") ? renderQueue(root.querySelector("[data-settings-queue]"), revision) : null,
      ]);
      if (revision === settingsRevision && areas.has("selector")) {
        renderQueueSelector(root.querySelector("[data-settings-selector]"));
      }
    }
    settingsRenderActive = false;
    if (settingsDirty.size) {
      scheduleSettingsRender();
      return;
    }
    const waiters = settingsWaiters.splice(0);
    waiters.forEach((resolve) => resolve());
  }

  function renderSettings(areas = SETTINGS_AREAS) {
    for (const area of settingsAreas(areas)) settingsDirty.add(area);
    settingsRevision += 1;
    scheduleSettingsRender();
    return new Promise((resolve) => settingsWaiters.push(resolve));
  }

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

  function publicQuarterLabels() {
    const labels = Array.isArray(archiveIndex?.quarters) ? archiveIndex.quarters
      .map((item) => item?.quarter)
      .filter((label) => QUARTER.test(label || ""))
      : [];
    return [...new Set(labels)].sort().reverse();
  }

  function displayOfflineQuarterLabels() {
    return [...new Set([...publicQuarterLabels(), ...knownOfflineQuarters])]
      .sort()
      .reverse();
  }

  function years() {
    return [...new Set(publicQuarterLabels().map((label) => label.slice(0, 4)))]
      .sort()
      .reverse();
  }

  function selectedQueueLabels() {
    const labels = publicQuarterLabels();
    if (settingsSelection.kind === "current") {
      const latest = QUARTER.test(archiveIndex?.latest_quarter || "")
        && labels.includes(archiveIndex.latest_quarter)
        ? archiveIndex.latest_quarter
        : labels[0];
      return latest ? [latest] : [];
    }
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

  async function renderAppSettings(container, revision = settingsRevision) {
    if (!container) return;
    const hasSupport = supported();
    const controlled = Boolean(navigator.serviceWorker?.controller);
    const canInstall = Boolean(installPrompt);
    const retryHtml = capabilityState === "registration-failed"
      ? `<p class="settings-note">${escapeHtml(registrationError || "Service Worker registration failed")}</p><button type="button" class="button button--ink" data-retry-service-worker>重试离线能力</button>`
      : "";
    if (revision !== settingsRevision) return;
    container.innerHTML = `
      <dl class="settings-facts">
        <div><dt>PWA</dt><dd>${hasSupport ? "supported" : "unavailable"}</dd></div>
        <div><dt>Service Worker</dt><dd>${capabilityLabel()}${controlled ? " · controlling" : ""}</dd></div>
        <div><dt>App update</dt><dd>${updateRegistration?.waiting ? "available" : "current"}</dd></div>
      </dl>
      ${retryHtml}
      ${canInstall ? '<button type="button" class="button button--ink" data-install-app>安装应用</button>' : '<p class="settings-note">如浏览器支持，可从浏览器菜单选择“安装”或“添加到主屏幕”。</p>'}
    `;
    container.querySelector("[data-install-app]")?.addEventListener("click", async () => {
      await promptInstall();
      await renderSettings(["app"]);
    });
    container.querySelector("[data-retry-service-worker]")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      await retryServiceWorkerRegistration();
      await renderSettings(["app"]);
    });
  }

  async function renderStorageSettings(container, revision = settingsRevision) {
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
    if (revision !== settingsRevision) return;
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
      await renderSettings(["storage"]);
    });
  }

  function statusLabel(status) {
    return {
      NONE: "未下载",
      INCOMPLETE: "下载未完成",
      COMPLETE: "已下载",
      UPDATE_AVAILABLE: "有更新",
      UPDATE_INCOMPLETE: "更新未完成",
    }[status] || "未下载";
  }

  function quarterView(state) {
    if (state?.active && state?.staging) return { status: "UPDATE_INCOMPLETE" };
    if (!state?.active && state?.staging) return { status: "INCOMPLETE" };
    if (state?.active && state.status === "UPDATE_AVAILABLE") {
      return { status: "UPDATE_AVAILABLE" };
    }
    if (state?.active) return { status: "COMPLETE" };
    return { status: "NONE" };
  }

  function actionLabel(status) {
    return {
      NONE: "下载",
      INCOMPLETE: "继续",
      COMPLETE: "…",
      UPDATE_AVAILABLE: "更新",
      UPDATE_INCOMPLETE: "继续更新",
    }[status] || "下载";
  }

  function removeLabel(status) {
    return {
      INCOMPLETE: "移除未完成数据",
      UPDATE_AVAILABLE: "移除离线数据",
      UPDATE_INCOMPLETE: "移除离线数据",
    }[status] || "移除";
  }

  function removeConfirmation(quarter, status) {
    if (status === "INCOMPLETE") return `移除 ${quarter} 已下载但未完成的离线数据？`;
    if (status === "UPDATE_INCOMPLETE") {
      return `移除 ${quarter} 的离线数据？当前可用旧版本与未完成更新都会删除。`;
    }
    return `移除 ${quarter} 的离线缓存？`;
  }

  async function renderQuarterSettings(container, revision = settingsRevision) {
    if (!container) return;
    const states = await listQuarterStates();
    knownOfflineQuarters = states.map((state) => state.quarter);
    const stateByQuarter = new Map(states.map((state) => [state.quarter, state]));
    const labels = displayOfflineQuarterLabels();
    if (revision !== settingsRevision) return;
    if (!labels.length) {
      container.innerHTML = '<p class="settings-note">当前没有可公开季度。</p>';
      return;
    }
    container.innerHTML = `<div class="offline-quarter-list">${labels.map((quarter) => {
      const state = stateByQuarter.get(quarter) || initialQuarterState(quarter);
      const view = quarterView(state);
      const canDownload = capabilityState === "ready";
      const action = view.status === "COMPLETE"
        ? actionLabel(view.status)
        : canDownload ? actionLabel(view.status) : "离线不可用";
      const removable = ["INCOMPLETE", "UPDATE_AVAILABLE", "UPDATE_INCOMPLETE"]
        .includes(view.status);
      const completeMenu = view.status === "COMPLETE"
        ? `<div class="offline-quarter__menu" data-quarter-menu hidden>
            <a class="button" href="../${quarter}/index.html">查看季度</a>
            <button type="button" class="button" data-quarter-check>检查更新</button>
            <button type="button" class="button" data-quarter-remove>移除离线资料</button>
          </div>`
        : "";
      return `<article class="offline-quarter" data-offline-quarter="${quarter}">
        <div><strong>${quarter}</strong><span>${statusLabel(view.status)}</span>${state.error ? `<small>${escapeHtml(state.error)}</small>` : ""}</div>
        <div class="offline-quarter__actions"><button type="button" class="button" data-quarter-action="${action}" aria-haspopup="${view.status === "COMPLETE" ? "menu" : "false"}" ${view.status !== "COMPLETE" && !canDownload ? "disabled" : ""}>${action}</button>${completeMenu}${view.status !== "COMPLETE" && removable ? `<button type="button" class="button" data-quarter-remove>${removeLabel(view.status)}</button>` : ""}</div>
      </article>`;
    }).join("")}</div>`;
    container.querySelectorAll("[data-offline-quarter]").forEach((row) => {
      const quarter = row.dataset.offlineQuarter;
      const state = stateByQuarter.get(quarter) || initialQuarterState(quarter);
      const view = quarterView(state);
      row.querySelector("[data-quarter-action]")?.addEventListener("click", async () => {
        if (view.status === "COMPLETE") {
          const menu = row.querySelector("[data-quarter-menu]");
          const button = row.querySelector("[data-quarter-action]");
          if (menu) {
            menu.hidden = !menu.hidden;
            button?.setAttribute("aria-expanded", String(!menu.hidden));
          }
          return;
        }
        try {
          await enqueue([quarter]);
        } catch (error) {
          window.alert(shortError(error));
        }
        await renderSettings(["quarter", "queue"]);
      });
      row.querySelector("[data-quarter-check]")?.addEventListener("click", async () => {
        await detectUpdates();
        await renderSettings(["quarter"]);
      });
      row.querySelector("[data-quarter-remove]")?.addEventListener("click", async () => {
        if (!window.confirm(removeConfirmation(quarter, view.status))) return;
        try {
          await removeQuarter(quarter);
        } catch (error) {
          window.alert(shortError(error));
        }
        await renderSettings(["quarter"]);
      });
    });
  }

  function renderQueueSelector(container) {
    if (!container) return;
    for (const destroy of container.__bsbListboxDestroyers || []) destroy();
    container.__bsbListboxDestroyers = [];
    const availableYears = years();
    if (!settingsSelection.year) settingsSelection.year = availableYears[0] || "";
    if (!settingsSelection.from) settingsSelection.from = availableYears.at(-1) || "";
    if (!settingsSelection.to) settingsSelection.to = availableYears[0] || "";
    const labels = selectedQueueLabels();
    container.innerHTML = `<div class="queue-selector">
      <label>范围<div data-queue-kind aria-label="范围"></div></label>
      <label data-queue-year ${settingsSelection.kind === "year" ? "" : "hidden"}>年份<div data-queue-year-value aria-label="年份"></div></label>
      <div class="queue-range" data-queue-range ${settingsSelection.kind === "range" ? "" : "hidden"}>
        <label>从<div data-queue-from aria-label="起始年份"></div></label><label>到<div data-queue-to aria-label="结束年份"></div></label>
      </div>
      <p class="queue-preview"><strong>${labels.length}</strong> 个季度 · newest → oldest</p>
      <button type="button" class="button button--ink" data-start-queue ${!labels.length || !navigator.onLine || capabilityState !== "ready" ? "disabled" : ""}>加入下载队列</button>
    </div>`;
    const yearsOptions = availableYears.map((year) => ({ value: String(year), label: String(year) }));
    const controls = [];
    controls.push(window.BsbListbox?.create(container.querySelector("[data-queue-kind]"), {
      label: "范围",
      options: [
        { value: "current", label: "当前季度" },
        { value: "year", label: "指定年份" },
        { value: "range", label: "年份范围" },
        { value: "all", label: "全部季度" },
      ],
      value: settingsSelection.kind,
      onChange: (value) => {
        settingsSelection.kind = value;
        renderSettings(["selector"]);
      },
    }));
    controls.push(window.BsbListbox?.create(container.querySelector("[data-queue-year-value]"), {
      label: "年份",
      options: yearsOptions,
      value: settingsSelection.year,
      onChange: (value) => {
        settingsSelection.year = value;
        renderSettings(["selector"]);
      },
    }));
    controls.push(window.BsbListbox?.create(container.querySelector("[data-queue-from]"), {
      label: "起始年份",
      options: yearsOptions,
      value: settingsSelection.from,
      onChange: (value) => {
        settingsSelection.from = value;
        renderSettings(["selector"]);
      },
    }));
    controls.push(window.BsbListbox?.create(container.querySelector("[data-queue-to]"), {
      label: "结束年份",
      options: yearsOptions,
      value: settingsSelection.to,
      onChange: (value) => {
        settingsSelection.to = value;
        renderSettings(["selector"]);
      },
    }));
    container.__bsbListboxDestroyers = controls.filter(Boolean).map((control) => control.destroy);
    container.querySelector("[data-start-queue]")?.addEventListener("click", async () => {
      await enqueue(selectedQueueLabels());
      await renderSettings(["queue", "quarter"]);
    });
  }

  async function renderQueue(container, revision = settingsRevision) {
    if (!container) return;
    const queue = await currentQueue();
    if (revision !== settingsRevision) return;
    const progress = queue.progress;
    const succeeded = queue.succeeded || queue.completed || [];
    const failed = queue.failed || [];
    const stateLabel = {
      idle: "队列空闲",
      downloading: "正在下载",
      paused: "已暂停",
      "waiting-network": "等待网络",
      "waiting-service-worker": "等待离线能力",
      cancelled: "已取消",
    }[queue.state] || queue.state;
    const progressTotal = progress?.total_bytes || progress?.total_resources || 0;
    const progressCompleted = progress?.total_bytes
      ? progress.verified_bytes
      : progress?.verified_resources || 0;
    const progressPercent = progressTotal
      ? Math.min(100, Math.floor((progressCompleted / progressTotal) * 100))
      : 0;
    const progressHtml = progress
      ? `<div class="queue-progress" data-queue-progress>
          <div class="queue-progress__label"><strong>${queue.current || "下载任务"}</strong><span>${progressPercent}%</span></div>
          <progress max="100" value="${progressPercent}" aria-label="下载进度">${progressPercent}%</progress>
          <p>${progress.verified_resources} / ${progress.total_resources} 个资源<br>${formatBytes(progress.verified_bytes)} / ${formatBytes(progress.total_bytes)}</p>
        </div>`
      : `<div class="queue-progress queue-progress--empty" data-queue-progress><p>当前没有进行中的下载任务。</p></div>`;
    const failureHtml = queue.errors.length
      ? `<p class="queue-failure">下载未完成 · 已保存 ${progressPercent}%</p>`
      : "";
    const errorsHtml = queue.errors.length
      ? `<details class="queue-errors"><summary>查看错误详情（${queue.errors.length}）</summary><ul>${queue.errors.map((error) => `<li><strong>${escapeHtml(error.quarter)}</strong> · ${escapeHtml(error.stage)} · ${escapeHtml(error.summary)}</li>`).join("")}</ul></details>`
      : "";
    container.innerHTML = `<div class="queue-status">
      <p><strong>${stateLabel}</strong>${queue.current ? ` · ${queue.current}` : ""}</p>
      ${progressHtml}
      <p>成功 ${succeeded.length} · 失败 ${failed.length} / ${queue.labels.length} 个季度</p>
      ${failureHtml}
      ${errorsHtml}
      <div class="queue-actions">
        ${["downloading", "waiting-network"].includes(queue.state) ? '<button type="button" class="button" data-queue-pause>暂停</button>' : ""}
        ${queue.state === "paused" ? '<button type="button" class="button button--ink" data-queue-resume>继续</button>' : ""}
        ${["downloading", "waiting-network", "waiting-service-worker", "paused"].includes(queue.state) ? '<button type="button" class="button" data-queue-cancel>取消</button>' : ""}
      </div></div>`;
    container.querySelector("[data-queue-pause]")?.addEventListener("click", pauseQueue);
    container.querySelector("[data-queue-resume]")?.addEventListener("click", resumeQueue);
    container.querySelector("[data-queue-cancel]")?.addEventListener("click", cancelQueue);
  }

  async function initializeSettings() {
    const root = document.querySelector("[data-pwa-settings]");
    if (!root) return;
    await loadArchiveIndex(root.dataset.archiveIndexUrl);
    subscribe((event) => renderSettings(event));
    await renderSettings();
    // Startup capability/queue notifications can supersede the first render
    // before its selector area is committed. Render the selector once after
    // the initial settings barrier so it cannot remain on the loading shell.
    renderQueueSelector(root.querySelector("[data-settings-selector]"));
    await detectUpdates();
  }

  async function loadArchiveIndex(url) {
    if (!url || !navigator.onLine) return false;
    try {
      const response = await fetch(url, { credentials: "same-origin" });
      if (!response.ok) return false;
      archiveIndex = await response.json();
      return true;
    } catch {
      archiveIndex = null;
      return false;
    }
  }

  function renderUpdateNotice() {
    const notice = document.querySelector("[data-pwa-update-notice]");
    if (!notice) return;
    notice.hidden = !updateRegistration?.waiting;
    const refresh = notice.querySelector("[data-pwa-refresh]");
    if (refresh) refresh.onclick = refreshApp;
  }

  async function renderQuarterOfflineControl() {
    const root = document.querySelector(
      "[data-mobile-quarter-offline], [data-quarter-offline]",
    );
    if (!root) return;
    const status = root.querySelector(
      "[data-mobile-quarter-offline-status], [data-quarter-offline-status]",
    );
    const actions = root.querySelector(
      "[data-mobile-quarter-offline-actions], [data-quarter-offline-actions]",
    );
    const quarter = root.dataset.quarter;
    if (!supported()) {
      status.textContent = "当前浏览器不支持离线下载；在线浏览仍可正常使用。";
      actions.replaceChildren();
      const link = document.createElement("a");
      link.href = "../settings/index.html";
      link.textContent = "离线能力不可用 · 打开 Settings";
      actions.append(link);
      return;
    }
    if (capabilityState === "registration-failed") {
      status.textContent = "Service Worker 注册失败；在线浏览仍可正常使用。";
      actions.replaceChildren();
      const link = document.createElement("a");
      link.href = "../settings/index.html";
      link.textContent = "离线能力不可用 · 打开 Settings";
      actions.append(link);
      return;
    }
    if (capabilityState !== "ready") {
      status.textContent = "正在准备离线下载能力…";
      actions.replaceChildren();
      return;
    }
    const [state, queue] = await Promise.all([
      getQuarterState(quarter),
      currentQueue(),
    ]);
    const view = quarterView(state);
    const progress = queue.current === quarter ? queue.progress : null;
    if (progress) {
      const total = progress.total_bytes || progress.total_resources;
      const completed = progress.total_bytes
        ? progress.verified_bytes
        : progress.verified_resources;
      const percent = total ? Math.floor((completed / total) * 100) : 0;
      status.textContent = `${statusLabel(view.status)} · ${percent}%`;
    } else {
      status.textContent = statusLabel(view.status);
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
    button.className = `button${view.status === "NONE" ? " button--ink" : ""}`;
    button.textContent = view.status === "NONE"
      ? "下载当前季度供离线使用"
      : ["INCOMPLETE", "UPDATE_INCOMPLETE"].includes(view.status)
        ? "继续离线下载"
        : view.status === "UPDATE_AVAILABLE"
          ? "更新离线资料"
          : "移除离线缓存";
    button.disabled = !navigator.onLine && view.status !== "COMPLETE";
    button.addEventListener("click", async () => {
      try {
        if (view.status === "COMPLETE") {
          if (!window.confirm(`移除 ${quarter} 的离线缓存？`)) return;
          await removeQuarter(quarter);
        } else {
          await enqueue([quarter]);
        }
      } catch (error) {
        window.alert(shortError(error));
      }
      renderQuarterOfflineControl();
    });
    actions.append(button);
    if (["INCOMPLETE", "UPDATE_AVAILABLE", "UPDATE_INCOMPLETE"].includes(view.status)) {
      const link = document.createElement("a");
      link.href = "../settings/index.html";
      link.textContent = "管理离线数据 · Settings";
      actions.append(link);
    }
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    notify({ areas: ["app"], reason: "install" });
  });

  window.addEventListener("online", async () => {
    const settings = document.querySelector("[data-pwa-settings]");
    await loadArchiveIndex(settings?.dataset.archiveIndexUrl);
    if (settings) await detectUpdates();
    const queue = await currentQueue();
    if (queue.state === "waiting-network") await resumeQueue();
    notify({ areas: ["app", "quarter", "queue", "selector"], reason: "online" });
  });
  window.addEventListener("offline", async () => {
    await updateQueue((queue) => (
      queue.state === "downloading"
        ? { ...queue, state: "waiting-network" }
        : queue
    ));
    notify({ areas: ["app", "quarter", "queue", "selector"], reason: "offline" });
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (refreshRequested) window.location.reload();
      else notify({ areas: ["app"], reason: "controller-change" });
    });
    window.addEventListener("load", async () => {
      await getOrStartServiceWorkerRegistration().catch(() => null);
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
    capabilityState: () => capabilityState,
    capabilityLabel,
    validateQuarterManifest,
    fetchQuarterManifest,
    getQuarterState,
    __quarterProgress: quarterProgress,
    __updateQuarterDownloadState: updateQuarterDownloadState,
    listQuarterStates,
    currentQueue,
    enqueue,
    pauseQueue,
    resumeQueue,
    cancelQueue,
    removeQuarter,
    retryServiceWorkerRegistration,
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
