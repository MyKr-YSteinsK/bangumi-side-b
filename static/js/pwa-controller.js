/* Pages-only bridge: all network and Cache Storage operations live in sw.js. */
const pwaState = { status: "checking-local-state", active: null, staging: null };

function emit(state) {
  Object.assign(pwaState, state);
  window.dispatchEvent(new CustomEvent("bsb-pwa-state", { detail: { ...pwaState } }));
}

async function workerMessage(type, payload = {}) {
  const registration = await navigator.serviceWorker.ready;
  const target = navigator.serviceWorker.controller || registration.active;
  if (!target) throw new Error("worker-unavailable");
  const channel = new MessageChannel();
  const response = new Promise((resolve, reject) => {
    channel.port1.onmessage = (event) => resolve(event.data);
    const timeout = setTimeout(() => reject(new Error("worker-command-timeout")), 8000);
    channel.port1.addEventListener("message", () => clearTimeout(timeout), { once: true });
  });
  target.postMessage({ type, ...payload }, [channel.port2]);
  const reply = await response;
  if (reply?.error) throw new Error(reply.error);
  const state = reply?.state || reply;
  emit(state);
  return state;
}

async function registerWorker() {
  if (!("serviceWorker" in navigator)) {
    emit({ status: "failed", last_error: "service-worker-unavailable" });
    return;
  }
  try {
    await navigator.serviceWorker.register(new URL("../sw.js", import.meta.url));
    navigator.serviceWorker.addEventListener("message", (event) => {
      if (event.data?.type === "bsb-state") emit(event.data.state);
      if (event.data?.type === "bsb-reload") emit({ reload_available: true });
      if (event.data?.type === "bsb-cleared") emit({ status: "first-install-required" });
    });
    const state = await workerMessage("state");
    if (!state.active && !state.staging && !state.available_release) {
      workerMessage("probe").catch((error) => emit({ status: "failed", last_error: error.message }));
    }
  } catch {
    emit({ status: "failed", last_error: "service-worker-unavailable" });
  }
}

window.BsbPwa = {
  state: () => ({ ...pwaState }),
  initialize: () => (!pwaState.active && !pwaState.staging && !pwaState.available_release ? workerMessage("probe") : workerMessage("start", { reason: "first-install" })),
  checkUpdate: () => workerMessage("check"),
  update: () => workerMessage("start", { reason: "manual-update" }),
  redownload: () => workerMessage("redownload"),
  pause: () => workerMessage("pause", { operation_id: pwaState.staging?.operation_id }),
  resume: () => workerMessage("resume", { operation_id: pwaState.staging?.operation_id }),
  cancel: () => workerMessage("cancel", { operation_id: pwaState.staging?.operation_id }),
  clear: () => workerMessage("clear"),
};

registerWorker();
window.addEventListener("pagehide", () => {
  if (pwaState.status === "downloading") window.BsbPwa.pause().catch(() => {});
});
