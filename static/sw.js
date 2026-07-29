/* Stable service-worker URL. Builder replaces the marker with shell file paths. */
const SHELL_CACHE = "bsb-shell-v1";
const CONTROL_CACHE = "bsb-control-v1";
const CONTROL_KEY = "__bsb_control__/state";
const SHELL_FILES = /* __BSB_SHELL_FILES__ */;
const FORBIDDEN_PARTS = ["/media/characters/", ".sqlite", "/workspace/", "/reports/"];
let aborter = null;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_FILES)));
  self.skipWaiting();
});
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

function controlRequest() {
  return new Request(new URL(CONTROL_KEY, self.registration.scope).href);
}
async function readState() {
  const response = await (await caches.open(CONTROL_CACHE)).match(controlRequest());
  return response ? response.json() : { schema: 1, active: null, staging: null, status: "first-install-required" };
}
async function writeState(state) {
  const cache = await caches.open(CONTROL_CACHE);
  await cache.put(controlRequest(), new Response(JSON.stringify(state), { headers: { "Content-Type": "application/json" } }));
  await broadcast({ type: "bsb-state", state });
  return state;
}
async function broadcast(message) {
  const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  clients.forEach((client) => client.postMessage(message));
}
function safeError(error) {
  return error instanceof Error && /^[a-z0-9-]+$/i.test(error.message) ? error.message : "download-failed";
}
function hash(buffer) {
  return crypto.subtle.digest("SHA-256", buffer).then((value) => [...new Uint8Array(value)].map((part) => part.toString(16).padStart(2, "0")).join(""));
}
function contentHash(files) {
  const text = files.slice().sort((a, b) => a.url.localeCompare(b.url)).map((file) => `${file.url}\0${file.sha256}\0${file.size_bytes}\n`).join("");
  return hash(new TextEncoder().encode(text));
}
function scopePath() { return new URL(self.registration.scope).pathname; }
function sameScope(url) { return url.origin === self.location.origin && url.pathname.startsWith(scopePath()); }
function isForbidden(url) { return FORBIDDEN_PARTS.some((part) => url.pathname.includes(part)); }
function attemptId() { return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function releaseUrl() { return new URL("release.json", self.registration.scope); }
function entryRequest(file) { return new Request(new URL(file.url, self.location.origin).href); }

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
    if (!sameScope(url) || isForbidden(url) || urls.has(url.href) || !/^[0-9a-f]{64}$/.test(file.sha256) || !Number.isInteger(file.size_bytes) || file.size_bytes < 0) throw new Error("manifest-entry-invalid");
    urls.add(url.href); bytes += file.size_bytes;
  }
  if (manifest.entry_count !== manifest.files.length || manifest.total_bytes !== bytes || await contentHash(manifest.files) !== manifest.content_hash || manifest.content_hash !== release.content_hash) throw new Error("manifest-content-invalid");
  return { release, manifest, manifestHash: release.manifest_sha256 };
}

async function storageCheck(total, activeBytes) {
  if (!self.navigator.storage?.estimate) return;
  const estimate = await self.navigator.storage.estimate();
  if (!Number.isFinite(estimate.quota) || !Number.isFinite(estimate.usage)) return;
  const required = total + activeBytes + Math.max(20 * 1024 * 1024, Math.ceil(total * 0.1));
  if (estimate.quota - estimate.usage < required) throw new Error("storage-insufficient");
}
async function validCached(cache, file) {
  const response = await cache.match(entryRequest(file));
  if (!response) return null;
  const bytes = await response.arrayBuffer();
  return bytes.byteLength === file.size_bytes && await hash(bytes) === file.sha256 ? bytes.byteLength : null;
}
async function download(state, bundle) {
  const staging = state.staging;
  const cache = await caches.open(staging.cache_name);
  const completed = new Set();
  let downloaded = 0;
  for (const file of bundle.manifest.files) {
    const size = await validCached(cache, file);
    if (size !== null) { completed.add(file.url); downloaded += size; }
  }
  staging.completed_urls = [...completed].sort(); staging.downloaded_bytes = downloaded;
  staging.total_bytes = bundle.manifest.total_bytes; staging.status = "downloading"; staging.last_error = null; state.status = "downloading";
  await writeState(state);
  const pending = bundle.manifest.files.filter((file) => !completed.has(file.url));
  let cursor = 0; aborter = new AbortController();
  const worker = async () => {
    while (cursor < pending.length && !aborter.signal.aborted) {
      const file = pending[cursor++];
      const response = await fetch(entryRequest(file), { cache: "no-store", signal: aborter.signal });
      if (!response.ok) throw new Error("file-unavailable");
      const bytes = await response.arrayBuffer();
      if (bytes.byteLength !== file.size_bytes || await hash(bytes) !== file.sha256) throw new Error("file-integrity-invalid");
      await cache.put(entryRequest(file), new Response(bytes, { headers: { "Content-Type": response.headers.get("Content-Type") || file.content_type } }));
      completed.add(file.url); downloaded += bytes.byteLength;
      staging.completed_urls = [...completed].sort(); staging.downloaded_bytes = downloaded; staging.updated_at = new Date().toISOString();
      await writeState(state);
    }
  };
  try { await Promise.all(Array.from({ length: Math.min(3, pending.length) }, worker)); }
  catch (error) {
    if (staging.status === "paused") return state;
    staging.status = "failed"; staging.last_error = safeError(error); await writeState(state); return state;
  } finally { aborter = null; }
  if (staging.status === "paused") return state;
  for (const file of bundle.manifest.files) if (await validCached(cache, file) === null) throw new Error("snapshot-verify-failed");
  if (staging.completed_urls.length !== bundle.manifest.entry_count || staging.downloaded_bytes !== bundle.manifest.total_bytes) throw new Error("snapshot-count-invalid");
  const oldCache = state.active?.cache_name;
  state.active = { release_version: bundle.release.release_version, cache_name: staging.cache_name, manifest_hash: bundle.manifestHash, activated_at: new Date().toISOString(), total_bytes: bundle.manifest.total_bytes, generated_at: bundle.release.generated_at, quarter_count: bundle.release.quarter_count, subject_count: bundle.release.subject_count };
  state.staging = null; state.status = "ready"; await writeState(state);
  await broadcast({ type: "bsb-reload" });
  if (oldCache && oldCache !== state.active.cache_name) await caches.delete(oldCache);
  for (const name of await caches.keys()) if (name.startsWith("bsb-snapshot-") && name !== state.active.cache_name) await caches.delete(name);
  return state;
}
async function begin(reason, clientId) {
  const state = await readState();
  if (state.staging?.status === "downloading" && state.staging.owner !== clientId && Date.parse(state.staging.lease_until || "") > Date.now()) return state;
  try {
    const bundle = await fetchRelease();
    const activeBytes = state.active?.total_bytes || 0;
    await storageCheck(bundle.manifest.total_bytes, activeBytes);
    if (!state.staging || state.staging.manifest_hash !== bundle.manifestHash) {
      if (state.staging) await caches.delete(state.staging.cache_name);
      state.staging = { release_version: bundle.release.release_version, cache_name: `bsb-snapshot-${bundle.release.release_version}-${attemptId()}`, manifest_hash: bundle.manifestHash, status: "downloading", completed_urls: [], downloaded_bytes: 0, total_bytes: bundle.manifest.total_bytes, updated_at: new Date().toISOString(), last_error: null, owner: clientId, lease_until: new Date(Date.now() + 120000).toISOString(), reason };
    } else {
      state.staging.owner = clientId; state.staging.lease_until = new Date(Date.now() + 120000).toISOString();
    }
    state.available_update = null; state.status = "initializing"; await writeState(state); return await download(state, bundle);
  } catch (error) {
    state.status = state.active ? "ready" : "failed";
    state.last_error = safeError(error);
    state.staging = state.staging || null;
    if (state.staging) { state.staging.status = "failed"; state.staging.last_error = safeError(error); }
    await writeState(state); return state;
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
        summary: [...(summary.system || []), ...(summary.data || [])].join("；"),
      };
      state.last_error = null;
    }
  } catch (error) { state.last_error = safeError(error); }
  state.status = state.active ? "ready" : "first-install-required";
  await writeState(state); return state;
}
async function pause() { const state = await readState(); if (state.staging?.status === "downloading") { state.staging.status = "paused"; state.status = "paused"; aborter?.abort(); await writeState(state); } return state; }
async function cancel() { const state = await readState(); aborter?.abort(); if (state.staging) await caches.delete(state.staging.cache_name); state.staging = null; state.status = state.active ? "ready" : "first-install-required"; await writeState(state); return state; }
async function clearSnapshots() {
  const state = await readState(); aborter?.abort();
  const names = await caches.keys();
  const targets = names.filter((name) => name.startsWith("bsb-snapshot-"));
  const results = await Promise.all(targets.map((name) => caches.delete(name)));
  state.active = null; state.staging = null; state.available_update = null; state.last_error = results.every(Boolean) ? null : "cache-cleanup-incomplete"; state.status = "first-install-required";
  await writeState(state); await broadcast({ type: "bsb-cleared" }); return state;
}

self.addEventListener("message", (event) => {
  const reply = (state) => event.ports[0]?.postMessage(state);
  const type = event.data?.type;
  const work = type === "state" ? readState() : type === "start" || type === "resume" || type === "redownload" ? begin(event.data.reason || (type === "redownload" ? "redownload" : "resume"), event.source?.id || "unknown") : type === "check" ? checkUpdate() : type === "pause" ? pause() : type === "cancel" ? cancel() : type === "clear" ? clearSnapshots() : Promise.resolve({ error: "message-invalid" });
  event.waitUntil(work.then(reply));
});

self.addEventListener("fetch", (event) => {
  const request = event.request; const url = new URL(request.url);
  if (!sameScope(url) || url.href === controlRequest().url || url.pathname.endsWith("release.json") || url.pathname.endsWith("snapshot-manifest.json")) return;
  event.respondWith((async () => {
    const state = await readState();
    if (state.active) {
      const response = await (await caches.open(state.active.cache_name)).match(request);
      if (response) return response;
      return request.mode === "navigate" ? (await caches.open(SHELL_CACHE)).match("offline.html") || Response.error() : Response.error();
    }
    const shell = await (await caches.open(SHELL_CACHE)).match(request);
    if (shell) return shell;
    return request.mode === "navigate" ? (await caches.open(SHELL_CACHE)).match("offline.html") || Response.error() : fetch(request);
  })());
});
