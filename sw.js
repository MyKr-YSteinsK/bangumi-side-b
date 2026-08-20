/* Unified Bangumi Side B service worker. */
"use strict";

const BSB_SHELL_REVISION = "138addbf9c556deb6bcfac4aec28cc12ab4fe4cd0f1d9f4a0875186751d89bc7";
const CONTENT_CACHE = "bsb-content-v1";
const RUNTIME_CACHE = "bsb-runtime-v1";
const META_CACHE = "bsb-meta-v1";
const CONTENT_PATH = "__bsb_content__/";
const META_PATH = "__bsb_meta__/";
const SHELL_META = "shell.json";
const PENDING_SHELL_META = `shell-pending-${BSB_SHELL_REVISION}.json`;
const CONTENT_MAINTENANCE_LOCK_NAME = "bsb-pwa-content-maintenance";
const HEX_64 = /^[a-f0-9]{64}$/;

function scopeUrl(relative = "") {
  return new URL(relative, self.registration.scope);
}

function contentRequest(hash) {
  return new Request(scopeUrl(`${CONTENT_PATH}${hash}`));
}

function metaRequest(name) {
  return new Request(scopeUrl(`${META_PATH}${name}`));
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
  if (!value || typeof value !== "object") throw new Error("invalid resource");
  const relative = value.url;
  if (
    typeof relative !== "string"
    || relative.startsWith("/")
    || relative.includes("\\")
    || relative.includes("?")
    || relative.includes("#")
    || relative.split("/").some((part) => part === ".." || part === ".")
  ) {
    throw new Error("unsafe resource URL");
  }
  const url = scopeUrl(relative);
  if (url.origin !== self.location.origin || !url.href.startsWith(self.registration.scope)) {
    throw new Error("out-of-scope resource URL");
  }
  if (!HEX_64.test(value.content_hash)) throw new Error("invalid resource hash");
  if (!Number.isInteger(value.size_bytes) || value.size_bytes < 0) {
    throw new Error("invalid resource size");
  }
  return { url: relative, content_hash: value.content_hash, size_bytes: value.size_bytes };
}

function validateManifest(value, expectedQuarter = null) {
  if (!value || typeof value !== "object" || !Array.isArray(value.resources)) {
    throw new Error("invalid manifest");
  }
  if (expectedQuarter === null) {
    if (value.schema !== 1 || value.revision !== BSB_SHELL_REVISION) {
      throw new Error("shell revision mismatch");
    }
  } else if (value.quarter !== expectedQuarter || typeof value.revision !== "string" || !value.revision) {
    throw new Error("quarter manifest mismatch");
  }
  const seen = new Set();
  const sizesByHash = new Map();
  const resources = value.resources.map((item) => {
    const resource = safeResource(item);
    if (seen.has(resource.url)) throw new Error("duplicate resource URL");
    const knownSize = sizesByHash.get(resource.content_hash);
    if (knownSize !== undefined && knownSize !== resource.size_bytes) {
      throw new Error("conflicting resource size");
    }
    sizesByHash.set(resource.content_hash, resource.size_bytes);
    seen.add(resource.url);
    return resource;
  });
  return { ...value, resources };
}

async function sha256(buffer) {
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

async function verifiedResponse(response, resource) {
  if (!response || !response.ok) throw new Error(`resource unavailable: ${resource.url}`);
  const body = response.clone();
  const buffer = await body.arrayBuffer();
  if (buffer.byteLength !== resource.size_bytes || await sha256(buffer) !== resource.content_hash) {
    throw new Error(`resource verification failed: ${resource.url}`);
  }
  return response.clone();
}

async function fetchJson(relative) {
  const response = await fetch(scopeUrl(relative), {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error(`metadata unavailable: ${relative}`);
  return response.json();
}

async function ensureContent(resource) {
  const cache = await caches.open(CONTENT_CACHE);
  const key = contentRequest(resource.content_hash);
  const existing = await cache.match(key);
  if (existing) {
    try {
      await verifiedResponse(existing.clone(), resource);
      return;
    } catch {
      await cache.delete(key);
    }
  }
  const response = await fetch(scopeUrl(resource.url), {
    cache: "no-store",
    credentials: "same-origin",
  });
  await cache.put(key, await verifiedResponse(response, resource));
}

async function installShell() {
  const manifest = validateManifest(await fetchJson("data/pwa-shell.json"));
  await withContentReferenceLease(async () => {
    await Promise.all(manifest.resources.map(ensureContent));
    await writeMeta(PENDING_SHELL_META, manifest);
  });
}

async function activeQuarterMetadata() {
  const cache = await caches.open(META_CACHE);
  const keys = await cache.keys();
  const values = [];
  for (const key of keys) {
    const marker = `${META_PATH}quarters/`;
    if (!key.url.includes(marker)) continue;
    const response = await cache.match(key);
    if (!response) continue;
    try {
      values.push(await response.json());
    } catch {
      // Invalid metadata is ignored and cannot create an offline guarantee.
    }
  }
  return values;
}

async function referencedHashes(shell) {
  const hashes = new Set((shell?.resources || []).map((item) => item.content_hash));
  const meta = await caches.open(META_CACHE);
  for (const request of await meta.keys()) {
    if (!request.url.includes(`${META_PATH}shell-pending-`)) continue;
    const response = await meta.match(request);
    if (!response) continue;
    try {
      const pending = await response.json();
      for (const item of pending?.resources || []) {
        if (HEX_64.test(item?.content_hash)) hashes.add(item.content_hash);
      }
    } catch {
      // Invalid pending metadata does not retain unverifiable content.
    }
  }
  for (const quarter of await activeQuarterMetadata()) {
    for (const manifest of [quarter?.active, quarter?.staging]) {
      for (const item of manifest?.resources || []) {
        if (HEX_64.test(item?.content_hash)) hashes.add(item.content_hash);
      }
    }
  }
  return hashes;
}

async function garbageCollectUnlocked(shell) {
  const keep = await referencedHashes(shell);
  const cache = await caches.open(CONTENT_CACHE);
  for (const request of await cache.keys()) {
    const hash = request.url.slice(request.url.lastIndexOf("/") + 1);
    if (!keep.has(hash)) await cache.delete(request);
  }
}

async function garbageCollect(shell) {
  return withContentGcLock(() => garbageCollectUnlocked(shell));
}

async function activateShell() {
  const pending = await readMeta(PENDING_SHELL_META);
  if (!pending) throw new Error("pending shell metadata unavailable");
  const manifest = validateManifest(pending);
  const activate = async (collect) => {
    await Promise.all(manifest.resources.map(ensureContent));
    await writeMeta(SHELL_META, manifest);
    const meta = await caches.open(META_CACHE);
    for (const request of await meta.keys()) {
      if (request.url.includes(`${META_PATH}shell-pending-`)) await meta.delete(request);
    }
    if (collect) await garbageCollectUnlocked(manifest);
  };
  const collected = await withContentGcLock(() => activate(true));
  if (!collected) await activate(false);
  await self.clients.claim();
}

function physicalPath(requestUrl) {
  const url = new URL(requestUrl);
  const scope = new URL(self.registration.scope);
  return decodeURIComponent(url.pathname.slice(scope.pathname.length));
}

function resourceForPath(manifest, path) {
  return manifest?.resources?.find((item) => item.url === path) || null;
}

async function contentFor(resource) {
  if (!resource || !HEX_64.test(resource.content_hash)) return null;
  const cache = await caches.open(CONTENT_CACHE);
  const key = contentRequest(resource.content_hash);
  const response = await cache.match(key);
  if (!response) return null;
  try {
    return await verifiedResponse(response, resource);
  } catch {
    await cache.delete(key);
    return null;
  }
}

async function authorizedResource(path, hash) {
  if (!HEX_64.test(hash || "")) return null;
  const shell = await readMeta(SHELL_META);
  const shellResource = resourceForPath(shell, path);
  if (shellResource?.content_hash === hash) return shellResource;
  for (const quarter of await activeQuarterMetadata()) {
    const resource = resourceForPath(quarter?.active, path);
    if (resource?.content_hash === hash) return resource;
  }
  return null;
}

async function putAuthorizedContent(path, resource, response) {
  return withContentReferenceLease(async () => {
    const current = await authorizedResource(path, resource.content_hash);
    if (!current) return false;
    const content = await caches.open(CONTENT_CACHE);
    await content.put(contentRequest(current.content_hash), response.clone());
    return true;
  });
}

async function guaranteedResponse(request) {
  const path = physicalPath(request.url);
  const url = new URL(request.url);
  if (url.searchParams.has("v")) {
    const resource = await authorizedResource(path, url.searchParams.get("v"));
    return resource ? contentFor(resource) : null;
  }
  const shell = await readMeta(SHELL_META);
  const shellResponse = await contentFor(resourceForPath(shell, path));
  if (shellResponse) return shellResponse;
  for (const quarter of await activeQuarterMetadata()) {
    const response = await contentFor(resourceForPath(quarter?.active, path));
    if (response) return response;
  }
  return null;
}

function runtimeCategory(request) {
  const path = physicalPath(request.url);
  if (request.mode === "navigate") return ["navigation", 40];
  if (path.endsWith(".json")) return ["json", 80];
  if (path.startsWith("covers/")) return ["cover", 160];
  return ["other", 40];
}

async function trimRuntime(category, limit) {
  const cache = await caches.open(RUNTIME_CACHE);
  const keys = (await cache.keys()).filter((request) => runtimeCategory(request)[0] === category);
  while (keys.length > limit) await cache.delete(keys.shift());
}

async function rememberRuntime(request, response) {
  if (!response || !response.ok) return;
  const [category, limit] = runtimeCategory(request);
  const cache = await caches.open(RUNTIME_CACHE);
  await cache.put(request, response.clone());
  await trimRuntime(category, limit);
}

async function versionedResponse(request) {
  const url = new URL(request.url);
  const expected = url.searchParams.get("v");
  const path = physicalPath(url.href);
  if (!HEX_64.test(expected || "") || !path.match(/^(assets\/|covers\/)/)) return null;
  const authorized = await authorizedResource(path, expected);
  if (!authorized) {
    try {
      const response = await fetch(request);
      if (response.ok) await rememberRuntime(request, response);
      return response;
    } catch {
      return (await caches.open(RUNTIME_CACHE)).match(request) || null;
    }
  }
  const cached = await contentFor(authorized);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    const verified = await verifiedResponse(response, authorized);
    await putAuthorizedContent(path, authorized, verified);
    return verified;
  } catch {
    const runtime = await caches.open(RUNTIME_CACHE);
    const candidate = await runtime.match(request);
    if (candidate) {
      try {
        const verified = await verifiedResponse(candidate, authorized);
        await putAuthorizedContent(path, authorized, verified);
        await runtime.delete(request);
        return verified;
      } catch {
        await runtime.delete(request);
      }
    }
    return new Response("Versioned resource unavailable", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
}

async function settingsFallback() {
  const resource = resourceForPath(await readMeta(SHELL_META), "settings/index.html");
  const response = await contentFor(resource);
  if (response) return Response.redirect(scopeUrl("settings/index.html"), 302);
  return new Response("Offline archive unavailable", {
    status: 503,
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      await rememberRuntime(request, response);
      return response;
    }
    const guaranteed = await guaranteedResponse(request);
    if (guaranteed) return guaranteed;
    return response;
  } catch {
    const guaranteed = await guaranteedResponse(request);
    if (guaranteed) return guaranteed;
    const runtime = await (await caches.open(RUNTIME_CACHE)).match(request);
    if (runtime) return runtime;
    if (request.mode === "navigate") return settingsFallback();
    return new Response("Offline resource unavailable", { status: 503 });
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(installShell());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(activateShell());
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING" || event.data === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (
    request.method !== "GET"
    || url.origin !== self.location.origin
    || !url.href.startsWith(self.registration.scope)
  ) return;
  const versioned = versionedResponse(request);
  event.respondWith(versioned.then((response) => response || networkFirst(request)));
});
