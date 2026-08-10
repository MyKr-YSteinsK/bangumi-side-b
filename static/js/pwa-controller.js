/* Pages-only bridge: normal network and Cache Storage operations live in sw.js. */
const pwaState = {
  status: "checking-local-state",
  active: null,
  staging: null,
  controller_ready: false,
};
const mutationCommands = new Set(["start", "resume", "pause", "cancel", "redownload", "clear", "update"]);
const DOWNLOAD_STALL_TIMEOUT_MS = 8000;
let mutationInFlight = false;
let pendingMutation = Promise.resolve();
let settleMutation = null;
let downloadWatchdog = null;
let controllerBootstrap = null;
let probeInFlight = null;
let initializationInFlight = null;

function emit(state) {
  Object.assign(pwaState, { command_error: null }, state);
  resetDownloadWatchdog(pwaState);
  window.dispatchEvent(new CustomEvent("bsb-pwa-state", { detail: { ...pwaState } }));
}

function resetDownloadWatchdog(state) {
  clearTimeout(downloadWatchdog);
  downloadWatchdog = null;
  const operationId = state.staging?.operation_id;
  if (state.status !== "downloading" || !operationId) return;
  downloadWatchdog = setTimeout(() => {
    const current = pwaState.staging;
    if (pwaState.status === "downloading" && current?.operation_id === operationId) {
      failStalledDownload(operationId).catch(() => {});
    }
  }, DOWNLOAD_STALL_TIMEOUT_MS);
}

async function failStalledDownload(operationId) {
  const registration = await navigator.serviceWorker.ready;
  const key = new URL("__bsb_control__/state", registration.scope);
  const cache = await caches.open("bsb-control-v1");
  const response = await cache.match(key);
  if (!response) return;
  const state = await response.json();
  if (state?.status !== "downloading" || state.staging?.operation_id !== operationId) return;
  state.staging.status = "failed";
  state.staging.failure = {
    error_code: "file-download-timeout",
    failed_url: null,
    category: "network",
    http_status: null,
    expected_bytes: null,
    actual_bytes: null,
    expected_sha256_prefix: null,
    actual_sha256_prefix: null,
    failed_at: new Date().toISOString(),
  };
  state.staging.last_error = state.staging.failure.error_code;
  state.status = "failed";
  await cache.put(key, new Response(JSON.stringify(state), {
    headers: { "Content-Type": "application/json" },
  }));
  emit({ ...state, controller_ready: Boolean(controller()) });
}

function controller() {
  return navigator.serviceWorker.controller;
}

async function workerMessage(type, payload = {}) {
  const mutation = mutationCommands.has(type);
  if (mutation && mutationInFlight) {
    const result = { ...pwaState, command_error: "operation-busy" };
    emit(result);
    return result;
  }
  if (mutation) {
    mutationInFlight = true;
    pendingMutation = new Promise((resolve) => { settleMutation = resolve; });
  }
  try {
    const target = controller();
    if (!target) throw new Error("controller-unavailable");
    const channel = new MessageChannel();
    const response = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("worker-command-timeout")), 8000);
      channel.port1.onmessage = (event) => {
        clearTimeout(timeout);
        resolve(event.data);
      };
    });
    target.postMessage({ type, ...payload }, [channel.port2]);
    const reply = await response;
    if (reply?.error) throw new Error(reply.error);
    const state = { ...(reply?.state || reply), command_error: reply?.reason || null, controller_ready: true };
    emit(state);
    return state;
  } catch (error) {
    if (error?.message === "worker-command-timeout") workerMessage("state").catch(() => {});
    throw error;
  } finally {
    if (mutation) {
      mutationInFlight = false;
      settleMutation?.();
      settleMutation = null;
    }
  }
}

async function waitForPendingMutation() {
  while (mutationInFlight) await pendingMutation;
}

function listenForWorkerMessages() {
  navigator.serviceWorker.addEventListener("message", (event) => {
    if (event.data?.type === "bsb-state") emit({ ...event.data.state, controller_ready: Boolean(controller()) });
    if (event.data?.type === "bsb-reload") emit({ reload_available: true });
    if (event.data?.type === "bsb-cleared") emit({ status: "first-install-required" });
  });
}

function waitForController() {
  if (controller()) return Promise.resolve(controller());
  emit({ status: "waiting-for-controller", controller_ready: false, last_error: null });
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      reject(new Error("controller-unavailable"));
    }, 8000);
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      const activeController = controller();
      if (!activeController) return;
      clearTimeout(timeout);
      resolve(activeController);
    }, { once: true });
  });
}

async function probeRelease() {
  if (probeInFlight) return probeInFlight;
  probeInFlight = workerMessage("probe").finally(() => { probeInFlight = null; });
  return probeInFlight;
}

async function readControllerState({ probe = false } = {}) {
  let state = await workerMessage("state");
  if (probe && !state.active && !state.staging && !state.available_release) {
    state = await probeRelease();
  }
  return state;
}

async function enableController() {
  if (controllerBootstrap) return controllerBootstrap;
  controllerBootstrap = (async () => {
    if (!("serviceWorker" in navigator)) throw new Error("service-worker-unavailable");
    await navigator.serviceWorker.register(new URL("../sw.js", import.meta.url), {
      updateViaCache: "none",
    });
    await waitForController();
    return readControllerState({ probe: true });
  })().catch((error) => {
    const code = error?.message || "worker-registration-failed";
    emit({ status: "failed", controller_ready: false, last_error: ["controller-unavailable", "service-worker-unavailable"].includes(code) ? code : "worker-registration-failed" });
    throw error;
  }).finally(() => { controllerBootstrap = null; });
  return controllerBootstrap;
}

async function initialize() {
  if (initializationInFlight) {
    const state = { ...pwaState, command_error: "initialization-in-progress" };
    emit(state);
    return state;
  }
  initializationInFlight = (async () => {
    await waitForPendingMutation();
    let state = pwaState.controller_ready && controller()
      ? await readControllerState()
      : await enableController();
    if (state.active) return state;
    if (state.staging) {
      if (["paused", "failed"].includes(state.staging.status)) {
        return workerMessage("resume", { operation_id: state.staging.operation_id });
      }
      return { ...state, command_error: "operation-busy" };
    }
    if (!state.available_release) state = await probeRelease();
    if (state.active || state.staging || !state.available_release) return state;
    return workerMessage("start", { reason: "first-install" });
  })().finally(() => { initializationInFlight = null; });
  return initializationInFlight;
}

window.BsbPwa = {
  state: () => ({ ...pwaState }),
  initialize,
  enableController,
  checkUpdate: () => workerMessage("check"),
  update: () => workerMessage("start", { reason: "manual-update" }),
  redownload: () => workerMessage("redownload"),
  pause: () => workerMessage("pause", { operation_id: pwaState.staging?.operation_id }),
  resume: () => workerMessage("resume", { operation_id: pwaState.staging?.operation_id }),
  cancel: () => workerMessage("cancel", { operation_id: pwaState.staging?.operation_id }),
  clear: () => workerMessage("clear"),
};

listenForWorkerMessages();
enableController().catch(() => {});
window.addEventListener("pagehide", () => {
  if (pwaState.status === "downloading") window.BsbPwa.pause().catch(() => {});
});
