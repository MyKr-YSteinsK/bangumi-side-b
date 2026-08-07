/* Stable service-worker URL. Builder replaces the marker with shell file paths. */
const SHELL_SCHEMA = 5;
const SHELL_CACHE = `bsb-shell-${SHELL_SCHEMA}`;
const CONTROL_CACHE = "bsb-control-v1";
const CONTROL_KEY = "__bsb_control__/state";
const MANIFEST_KEY_PREFIX = "__bsb_control__/manifest/";
const SHELL_FILES = /* __BSB_SHELL_FILES__ */;
const FORBIDDEN_PARTS = [
  ".sql" + "ite",
  "/work" + "space/",
  "/reports/",
];
const LEASE_MS = 120000;
const SNAPSHOT_PREFIX = "bsb-snapshot-";
const aborters = new Map();
const progressCheckpoints = new Map();

class DownloadFailure extends Error {
  constructor(error_code, file = null, details = {}) {
    super(error_code);
    this.error_code = error_code;
    this.file = file;
    this.details = details;
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_FILES)));
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(Promise.all([self.clients.claim(), cleanupShellCaches()]));
});

function controlRequest() {
  return new Request(new URL(CONTROL_KEY, self.registration.scope).href);
}
function manifestRequest(manifestHash) {
  return new Request(new URL(`${MANIFEST_KEY_PREFIX}${manifestHash}`, self.registration.scope).href);
}
function emptyState() {
  return {
    schema: 2,
    active: null,
    staging: null,
    status: "first-install-required",
    available_release: null,
    available_update: null,
    cleanup_warning: null,
  };
}
function normalizeState(value) {
  const state = value && typeof value === "object" ? value : emptyState();
  state.schema = 2;
  state.active = state.active && typeof state.active === "object" ? state.active : null;
  state.staging = state.staging && typeof state.staging === "object" ? state.staging : null;
  if (state.staging) {
    state.staging.operation_id ||= attemptId();
    state.staging.owner_client_id ||= state.staging.owner || null;
    state.staging.attempt_cache_name ||= state.staging.cache_name || null;
    delete state.staging.owner;
    delete state.staging.cache_name;
  }
  state.status ||= state.staging?.status || (state.active ? "ready" : "first-install-required");
  state.available_release ||= null;
  state.available_update ||= null;
  state.cleanup_warning ||= null;
  return state;
}
async function readState() {
  const response = await (await caches.open(CONTROL_CACHE)).match(controlRequest());
  return normalizeState(response ? await response.json() : emptyState());
}
async function writeState(state) {
  const normalized = normalizeState(state);
  const cache = await caches.open(CONTROL_CACHE);
  await cache.put(controlRequest(), new Response(JSON.stringify(normalized), { headers: { "Content-Type": "application/json" } }));
  await broadcast({ type: "bsb-state", state: normalized });
  return normalized;
}
async function broadcast(message) {
  const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  clients.forEach((client) => client.postMessage(message));
}
function safeError(error) {
  const code = error instanceof DownloadFailure ? error.error_code : error?.message;
  return typeof code === "string" && /^[a-z0-9-]+$/i.test(code) ? code : "download-failed";
}
function failureDetails(error) {
  const code = safeError(error);
  const file = error instanceof DownloadFailure ? error.file : null;
  const details = error instanceof DownloadFailure ? error.details : {};
  const failed_url = file?.url && safeDeploymentPath(file.url) ? safeDeploymentPath(file.url) : null;
  return {
    error_code: code,
    failed_url,
    category: typeof details.category === "string" ? details.category : "operation",
    http_status: Number.isInteger(details.http_status) ? details.http_status : null,
    expected_bytes: Number.isInteger(file?.size_bytes) ? file.size_bytes : null,
    actual_bytes: Number.isInteger(details.actual_bytes) ? details.actual_bytes : null,
    expected_sha256_prefix: /^[0-9a-f]{64}$/.test(file?.sha256 || "") ? file.sha256.slice(0, 12) : null,
    actual_sha256_prefix: /^[0-9a-f]{64}$/.test(details.actual_sha256 || "") ? details.actual_sha256.slice(0, 12) : null,
    failed_at: new Date().toISOString(),
  };
}
function safeDeploymentPath(value) {
  try {
    const url = new URL(value, self.location.origin);
    return sameScope(url) && !unsafePath(url) && !url.search && !url.hash ? url.pathname : null;
  } catch {
    return null;
  }
}
function hash(buffer) {
  return crypto.subtle.digest("SHA-256", buffer).then((value) => [...new Uint8Array(value)].map((part) => part.toString(16).padStart(2, "0")).join(""));
}
function contentHash(files) {
  const text = files.slice().sort((a, b) => a.url.localeCompare(b.url)).map((file) => `${file.url}\0${file.sha256}\0${file.size_bytes}\n`).join("");
  return hash(new TextEncoder().encode(text));
}
function scopePath() { return new URL(self.registration.scope).pathname; }
function sameScope(url) {
  const scope = scopePath();
  return url.origin === self.location.origin && (url.pathname === scope.slice(0, -1) || url.pathname.startsWith(scope));
}
function unsafePath(url) {
  return url.pathname.split("/").some((part) => {
    try {
      const decoded = decodeURIComponent(part);
      return decoded === ".." || decoded.includes("/") || decoded.includes("\\");
    } catch {
      return true;
    }
  });
}
function isForbidden(url) { return FORBIDDEN_PARTS.some((part) => url.pathname.includes(part)); }
function attemptId() { return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function leaseUntil() { return new Date(Date.now() + LEASE_MS).toISOString(); }
function releaseUrl() { return new URL("release.json", self.registration.scope); }
function isControlUrl(url) { return url.pathname.endsWith("release.json") || url.pathname.endsWith("snapshot-manifest.json"); }
function entryRequest(file) { return new Request(new URL(file.url, self.location.origin).href); }
function isHtml(file) { return file.content_type === "text/html" || file.url.endsWith(".html"); }
function refreshStatus(state) { state.status = state.staging?.status || (state.active ? "ready" : "first-install-required"); }
function ownsOperation(state, operationId) { return state.staging?.operation_id === operationId; }
function operationIsRunning(staging) {
  return Boolean(staging && ["probing", "ready-to-download", "downloading", "verifying", "activating"].includes(staging.status));
}
function downloadPriority(file) {
  if (["html", "metadata", "shell"].includes(file.category)) return 0;
  if (file.category === "icon") return 1;
  if (file.category === "cover") return 2;
  return 1;
}

async function cleanupShellCaches() {
  const names = await caches.keys();
  await Promise.all(names.filter((name) => name.startsWith("bsb-shell-") && name !== SHELL_CACHE).map((name) => caches.delete(name)));
}
async function writeManifestIndex(manifestHash, index) {
  const cache = await caches.open(CONTROL_CACHE);
  await cache.put(manifestRequest(manifestHash), new Response(JSON.stringify(index), { headers: { "Content-Type": "application/json" } }));
}
async function readManifestIndex(active) {
  if (!active?.manifest_hash) return null;
  const cache = await caches.open(CONTROL_CACHE);
  const saved = await cache.match(manifestRequest(active.manifest_hash));
  if (saved) {
    const index = await saved.json();
    if (index?.manifest_hash === active.manifest_hash && Array.isArray(index.entries)) return index;
  }
  // Plan 04 snapshots predate the internal index. Derive one only from their
  // already-cached, verified files so the existing active archive remains readable.
  const activeCache = await caches.open(active.cache_name);
  const entries = (await activeCache.keys()).map((request) => ({ url: request.url, html: request.url.endsWith(".html") }));
  const index = { schema: 1, manifest_hash: active.manifest_hash, entries };
  await writeManifestIndex(active.manifest_hash, index);
  return index;
}
function indexFromManifest(manifest, manifestHash) {
  return {
    schema: 1,
    manifest_hash: manifestHash,
    entries: manifest.files.map((file) => ({ url: new URL(file.url, self.location.origin).href, html: isHtml(file) })),
  };
}
function canonicalSnapshotRequest(request, index) {
  const url = new URL(request.url);
  if (!sameScope(url) || unsafePath(url)) return null;
  const entries = new Map(index.entries.map((entry) => [entry.url, entry]));
  if (request.mode === "navigate") {
    const canonical = new URL(url.pathname, self.location.origin);
    const entry = entries.get(canonical.href);
    return entry?.html ? new Request(canonical.href) : null;
  }
  if (url.search || url.hash) return null;
  return entries.has(url.href) ? request : null;
}
function snapshotCorruption() {
  return new Response("资料快照不完整，请在设置中重新下载。", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
function initialisationResponse() {
  const settings = new URL("settings/index.html", self.registration.scope).pathname;
  return new Response(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>需要初始化本地资料库</title><main><h1>需要初始化本地资料库</h1><p>此 PWA 不提供在线浏览。请先在设置页完成初始化。</p><p><a href="${settings}">前往设置</a></p></main></html>`, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

async function fetchRelease() {
  const response = await fetch(releaseUrl(), { cache: "no-store" });
  if (!response.ok) throw new Error("release-unavailable");
  const release = await response.json();
  if (release?.schema !== 1 || typeof release.release_version !== "string" || release.manifest_url !== "snapshot-manifest.json") throw new Error("release-invalid");
  const manifestUrl = new URL(release.manifest_url, self.registration.scope);
  if (!sameScope(manifestUrl)) throw new Error("manifest-scope-invalid");
  const manifestResponse = await fetch(manifestUrl, { cache: "no-store" });
  if (!manifestResponse.ok) throw new Error("manifest-unavailable");
  const manifestBytes = await manifestResponse.arrayBuffer();
  if (await hash(manifestBytes) !== release.manifest_sha256) throw new Error("manifest-hash-invalid");
  const manifest = JSON.parse(new TextDecoder().decode(manifestBytes));
  if (manifest?.schema !== 1 || manifest.release_version !== release.release_version || manifest.deployment_path !== scopePath() || !Array.isArray(manifest.files)) throw new Error("manifest-invalid");
  const urls = new Set();
  let bytes = 0;
  for (const file of manifest.files) {
    const url = new URL(file.url, self.location.origin);
    if (!sameScope(url) || unsafePath(url) || isForbidden(url) || url.search || urls.has(url.href) || !/^[0-9a-f]{64}$/.test(file.sha256) || !Number.isInteger(file.size_bytes) || file.size_bytes < 0) throw new Error("manifest-entry-invalid");
    urls.add(url.href); bytes += file.size_bytes;
  }
  if (manifest.entry_count !== manifest.files.length || manifest.total_bytes !== bytes || await contentHash(manifest.files) !== manifest.content_hash || manifest.content_hash !== release.content_hash) throw new Error("manifest-content-invalid");
  return { release, manifest, manifestHash: release.manifest_sha256, index: indexFromManifest(manifest, release.manifest_sha256) };
}

async function storageCheck(total) {
  if (!self.navigator.storage?.estimate) return { available: false };
  const estimate = await self.navigator.storage.estimate();
  if (!Number.isFinite(estimate.quota) || !Number.isFinite(estimate.usage)) return { available: false };
  const safety_bytes = Math.max(20 * 1024 * 1024, Math.ceil(total * 0.1));
  const required = total + safety_bytes;
  if (estimate.quota - estimate.usage < required) throw new Error("storage-insufficient");
  return { available: true, quota_bytes: estimate.quota, usage_bytes: estimate.usage, required_bytes: required, safety_bytes };
}
async function validCached(cache, file) {
  const response = await cache.match(entryRequest(file));
  if (!response) return null;
  const bytes = await response.arrayBuffer();
  return bytes.byteLength === file.size_bytes && await hash(bytes) === file.sha256 ? bytes.byteLength : null;
}
async function renewLease(operationId, mutate) {
  const state = await readState();
  if (!ownsOperation(state, operationId)) throw new Error("operation-superseded");
  if (mutate) mutate(state.staging);
  state.staging.lease_until = leaseUntil();
  state.staging.updated_at = new Date().toISOString();
  refreshStatus(state);
  return writeState(state);
}
async function ownerStillExists(clientId) {
  return Boolean(clientId && await self.clients.get(clientId));
}
async function operationMayRun(staging, clientId) {
  if (!operationIsRunning(staging)) return true;
  if (!await ownerStillExists(staging.owner_client_id)) return true;
  if (staging.owner_client_id === clientId) return false;
  const currentLease = Date.parse(staging.lease_until || "");
  return currentLease <= Date.now();
}
async function acceptOperation(reason, clientId, requestedOperationId) {
  const state = await readState();
  const existing = state.staging;
  if (requestedOperationId && existing?.operation_id !== requestedOperationId) return { state, run: false, reason: "operation-superseded" };
  if (existing?.status === "staging-release-changed") return { state, run: false, reason: "staging-release-changed" };
  if (!await operationMayRun(existing, clientId)) return { state, run: false, reason: "operation-busy" };
  if (!existing) {
    state.staging = {
      operation_id: attemptId(),
      owner_client_id: clientId,
      lease_until: leaseUntil(),
      release_version: null,
      manifest_hash: null,
      attempt_cache_name: null,
      status: "probing",
      reason,
      completed_urls: [],
      downloaded_bytes: 0,
      total_bytes: 0,
      updated_at: new Date().toISOString(),
      last_error: null,
      failure: null,
    };
  } else {
    existing.owner_client_id = clientId;
    existing.lease_until = leaseUntil();
    existing.reason = reason;
    existing.status = "probing";
    existing.last_error = null;
    existing.failure = null;
    existing.updated_at = new Date().toISOString();
  }
  state.available_update = null;
  refreshStatus(state);
  const written = await writeState(state);
  return { state: written, run: true, reason: null };
}
async function failOperation(operationId, error) {
  const state = await readState();
  if (!ownsOperation(state, operationId)) return state;
  if (state.staging.status === "paused" && (error?.name === "AbortError" || safeError(error) === "download-failed")) return state;
  state.staging.status = "failed";
  state.staging.failure = failureDetails(error);
  state.staging.last_error = state.staging.failure.error_code;
  refreshStatus(state);
  return writeState(state);
}
async function checkpoint(operationId, completed, downloaded, force = false) {
  const now = Date.now();
  const previous = progressCheckpoints.get(operationId) || { count: 0, at: 0, broadcast_at: 0 };
  const shouldPersist = force || completed.size - previous.count >= 12 || now - previous.at >= 400;
  if (!shouldPersist) {
    if (now - previous.broadcast_at >= 350) {
      const state = await readState();
      if (ownsOperation(state, operationId)) {
        state.staging.downloaded_bytes = downloaded;
        await broadcast({ type: "bsb-state", state });
      }
      progressCheckpoints.set(operationId, { ...previous, broadcast_at: now });
    }
    return null;
  }
  progressCheckpoints.set(operationId, { count: completed.size, at: now, broadcast_at: now });
  return renewLease(operationId, (staging) => {
    staging.completed_urls = [...completed].sort();
    staging.downloaded_bytes = downloaded;
  });
}
async function cleanupAfterActivation(previousCache, activeCache, operationId) {
  const failures = [];
  if (previousCache && previousCache !== activeCache) {
    try { if (!await caches.delete(previousCache)) failures.push("previous-snapshot"); } catch { failures.push("previous-snapshot"); }
  }
  for (const name of await caches.keys()) {
    if (!name.startsWith(SNAPSHOT_PREFIX) || name === activeCache) continue;
    try { if (!await caches.delete(name)) failures.push("stale-snapshot"); } catch { failures.push("stale-snapshot"); }
  }
  if (!failures.length) return;
  const state = await readState();
  if (state.active?.operation_id === operationId) {
    state.cleanup_warning = [...new Set(failures)];
    await writeState(state);
  }
}
async function activateSnapshot(operationId, bundle) {
  let state = await renewLease(operationId, (staging) => { staging.status = "activating"; });
  const staging = state.staging;
  const cache = await caches.open(staging.attempt_cache_name);
  for (const file of bundle.manifest.files) {
    if (await validCached(cache, file) === null) {
      throw new DownloadFailure("snapshot-verify-failed", file, { category: "snapshot-verify" });
    }
  }
  const previousCache = state.active?.cache_name;
  await writeManifestIndex(bundle.manifestHash, bundle.index);
  state = await readState();
  if (!ownsOperation(state, operationId)) return state;
  state.active = {
    operation_id: operationId,
    release_version: bundle.release.release_version,
    cache_name: state.staging.attempt_cache_name,
    manifest_hash: bundle.manifestHash,
    activated_at: new Date().toISOString(),
    total_bytes: bundle.manifest.total_bytes,
    generated_at: bundle.release.generated_at,
    quarter_count: bundle.release.quarter_count,
    subject_count: bundle.release.subject_count,
    latest_quarter: typeof bundle.release.latest_quarter === "string" ? bundle.release.latest_quarter : null,
  };
  state.staging = null;
  progressCheckpoints.delete(operationId);
  state.cleanup_warning = null;
  refreshStatus(state);
  await writeState(state);
  await broadcast({ type: "bsb-reload" });
  await cleanupAfterActivation(previousCache, state.active.cache_name, operationId);
  return state;
}
async function download(operationId, bundle) {
  let state = await readState();
  if (!ownsOperation(state, operationId) || state.staging.status === "paused") return state;
  const staging = state.staging;
  const cache = await caches.open(staging.attempt_cache_name);
  const completed = new Set();
  let downloaded = 0;
  for (const file of bundle.manifest.files) {
    const size = await validCached(cache, file);
    if (size !== null) { completed.add(file.url); downloaded += size; }
  }
  await checkpoint(operationId, completed, downloaded, true);
  state = await readState();
  if (!ownsOperation(state, operationId) || state.staging.status === "paused") return state;
  state.staging.status = "downloading";
  state.staging.total_bytes = bundle.manifest.total_bytes;
  state.staging.last_error = null;
  refreshStatus(state);
  await writeState(state);
  const pending = bundle.manifest.files.filter((file) => !completed.has(file.url));
  pending.sort((left, right) => downloadPriority(left) - downloadPriority(right) || left.url.localeCompare(right.url));
  const controller = new AbortController();
  aborters.set(operationId, controller);
  let cursor = 0;
  const worker = async () => {
    while (cursor < pending.length && !controller.signal.aborted) {
      const file = pending[cursor++];
      let response;
      try {
        response = await fetch(entryRequest(file), { cache: "no-store", signal: controller.signal });
      } catch (error) {
        if (error?.name === "AbortError") throw error;
        throw new DownloadFailure("file-unavailable", file, { category: "network" });
      }
      if (!response.ok) {
        throw new DownloadFailure("file-unavailable", file, { category: "http", http_status: response.status });
      }
      let bytes;
      try {
        bytes = await response.arrayBuffer();
      } catch {
        throw new DownloadFailure("file-unavailable", file, { category: "body" });
      }
      if (bytes.byteLength !== file.size_bytes) {
        throw new DownloadFailure("file-size-invalid", file, { category: "integrity", actual_bytes: bytes.byteLength });
      }
      const actual_sha256 = await hash(bytes);
      if (actual_sha256 !== file.sha256) {
        throw new DownloadFailure("file-hash-invalid", file, { category: "integrity", actual_bytes: bytes.byteLength, actual_sha256 });
      }
      const current = await readState();
      if (!ownsOperation(current, operationId) || current.staging.status !== "downloading") throw new Error("operation-superseded");
      try {
        await cache.put(entryRequest(file), new Response(bytes, { headers: { "Content-Type": response.headers.get("Content-Type") || file.content_type } }));
      } catch {
        throw new DownloadFailure("file-cache-write-failed", file, { category: "cache", actual_bytes: bytes.byteLength });
      }
      completed.add(file.url); downloaded += bytes.byteLength;
      await checkpoint(operationId, completed, downloaded);
    }
  };
  try {
    await Promise.all(Array.from({ length: Math.min(3, pending.length) }, worker));
  } catch (error) {
    controller.abort();
    const latest = await readState();
    if (ownsOperation(latest, operationId) && latest.staging.status === "paused") return latest;
    return failOperation(operationId, error);
  } finally {
    aborters.delete(operationId);
  }
  await checkpoint(operationId, completed, downloaded, true);
  state = await renewLease(operationId, (current) => { current.status = "verifying"; });
  if (state.staging.status === "paused") return state;
  return activateSnapshot(operationId, bundle);
}
async function runOperation(operationId) {
  try {
    const bundle = await fetchRelease();
    let state = await readState();
    if (!ownsOperation(state, operationId) || state.staging.status === "paused") return state;
    const staging = state.staging;
    if (staging.manifest_hash && (staging.manifest_hash !== bundle.manifestHash || staging.release_version !== bundle.release.release_version)) {
      staging.status = "staging-release-changed";
      staging.remote_release_version = bundle.release.release_version;
      staging.remote_manifest_hash = bundle.manifestHash;
      refreshStatus(state);
      return writeState(state);
    }
    staging.release_version = bundle.release.release_version;
    staging.manifest_hash = bundle.manifestHash;
    staging.attempt_cache_name ||= `${SNAPSHOT_PREFIX}${bundle.release.release_version}-${attemptId()}`;
    staging.total_bytes = bundle.manifest.total_bytes;
    staging.status = "ready-to-download";
    staging.last_error = null;
    staging.failure = null;
    staging.lease_until = leaseUntil();
    refreshStatus(state);
    await writeState(state);
    staging.storage_check = await storageCheck(bundle.manifest.total_bytes);
    await writeState(state);
    return download(operationId, bundle);
  } catch (error) {
    return failOperation(operationId, error);
  }
}
async function checkUpdate() {
  const state = await readState();
  try {
    const bundle = await fetchRelease();
    const current = state.active;
    if (current && current.release_version === bundle.release.release_version && current.manifest_hash === bundle.manifestHash) {
      state.available_update = null; state.last_error = null;
    } else {
      const summary = bundle.release.summary || {};
      state.available_update = {
        release_version: bundle.release.release_version,
        app_version: bundle.release.app_version,
        quarter_count: bundle.release.quarter_count,
        subject_count: bundle.release.subject_count,
        total_bytes: bundle.manifest.total_bytes,
        summary: { system: summary.system || [], data: summary.data || [] },
      };
      state.last_error = null;
    }
  } catch (error) { state.last_error = safeError(error); }
  refreshStatus(state);
  return writeState(state);
}
async function probeRelease() {
  const state = await readState();
  if (state.active) return state;
  state.status = "probing-release";
  state.last_error = null;
  await writeState(state);
  try {
    const bundle = await fetchRelease();
    const summary = bundle.release.summary || {};
    state.available_release = {
      release_version: bundle.release.release_version,
      app_version: bundle.release.app_version,
      generated_at: bundle.release.generated_at,
      quarter_count: bundle.release.quarter_count,
      subject_count: bundle.release.subject_count,
      total_bytes: bundle.manifest.total_bytes,
      latest_quarter: typeof bundle.release.latest_quarter === "string" ? bundle.release.latest_quarter : null,
      summary: { system: summary.system || [], data: summary.data || [] },
    };
    state.last_error = null;
    state.status = "first-install-required";
  } catch (error) {
    state.available_release = null;
    state.last_error = safeError(error);
    state.status = "failed";
  }
  return writeState(state);
}
async function pause(clientId, operationId) {
  const state = await readState();
  if (!ownsOperation(state, operationId) || state.staging.status !== "downloading") return state;
  if (state.staging.owner_client_id !== clientId && await ownerStillExists(state.staging.owner_client_id)) throw new Error("operation-owner-mismatch");
  state.staging.owner_client_id = clientId;
  state.staging.status = "paused";
  state.staging.lease_until = leaseUntil();
  refreshStatus(state);
  await writeState(state);
  aborters.get(operationId)?.abort();
  return state;
}
async function cancel(clientId, operationId) {
  const state = await readState();
  if (!state.staging) return state;
  if (operationId && !ownsOperation(state, operationId)) return state;
  aborters.get(state.staging.operation_id)?.abort();
  const cacheName = state.staging.attempt_cache_name;
  progressCheckpoints.delete(state.staging.operation_id);
  state.staging = null;
  refreshStatus(state);
  await writeState(state);
  if (cacheName && !await caches.delete(cacheName)) {
    const latest = await readState();
    latest.cleanup_warning = ["staging-snapshot"];
    await writeState(latest);
  }
  return state;
}
async function clearSnapshots(clientId) {
  const state = await readState();
  aborters.get(state.staging?.operation_id)?.abort();
  const names = await caches.keys();
  const targets = names.filter((name) => name.startsWith(SNAPSHOT_PREFIX));
  const results = await Promise.all(targets.map((name) => caches.delete(name)));
  state.active = null; state.staging = null; state.available_release = null; state.available_update = null;
  state.cleanup_warning = results.every(Boolean) ? null : ["snapshot-clear"];
  refreshStatus(state);
  await writeState(state);
  await broadcast({ type: "bsb-cleared" });
  return state;
}

self.addEventListener("message", (event) => {
  const type = event.data?.type;
  const clientId = event.source?.id || "unknown";
  const operationId = event.data?.operation_id;
  if (["start", "resume", "redownload"].includes(type)) {
    const task = acceptOperation(event.data.reason || (type === "redownload" ? "redownload" : type), clientId, operationId).then(({ state, run, reason }) => {
      event.ports[0]?.postMessage({ accepted: run, operation_id: state.staging?.operation_id || null, reason, state });
      return run ? runOperation(state.staging.operation_id) : state;
    });
    event.waitUntil(task);
    return;
  }
  const work = type === "state" ? readState() : type === "probe" ? probeRelease() : type === "check" ? checkUpdate() : type === "pause" ? pause(clientId, operationId) : type === "cancel" ? cancel(clientId, operationId) : type === "clear" ? clearSnapshots(clientId) : Promise.resolve({ error: "message-invalid" });
  event.waitUntil(work.then((state) => event.ports[0]?.postMessage(state)).catch((error) => event.ports[0]?.postMessage({ error: safeError(error) })));
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (!sameScope(url) || url.href === controlRequest().url) return;
  if (isControlUrl(url)) { event.respondWith(Promise.resolve(Response.error())); return; }
  event.respondWith((async () => {
    const state = await readState();
    if (state.active) {
      const index = await readManifestIndex(state.active);
      const canonical = index && canonicalSnapshotRequest(request, index);
      if (!canonical) return snapshotCorruption();
      const response = await (await caches.open(state.active.cache_name)).match(canonical);
      return response || snapshotCorruption();
    }
    const shell = await (await caches.open(SHELL_CACHE)).match(request);
    if (shell) return shell;
    return request.mode === "navigate" ? initialisationResponse() : Response.error();
  })());
});
