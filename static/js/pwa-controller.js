/* Pages-only bridge: all network and Cache Storage operations live in sw.js. */
const pwaState = { status: "checking-local-state", active: null, staging: null };
const mutationCommands = new Set(["start", "resume", "pause", "cancel", "redownload", "clear", "update"]);
let mutationInFlight = false;

function emit(state) {
  Object.assign(pwaState, state);
  window.dispatchEvent(new CustomEvent("bsb-pwa-state", { detail: { ...pwaState } }));
}

async function workerMessage(type, payload = {}) {
  const mutation = mutationCommands.has(type);
  if (mutation && mutationInFlight) {
    const state = await workerMessage("state");
    const result = { ...state, command_error: "operation-busy" };
    emit(result);
    return result;
  }
  if (mutation) mutationInFlight = true;
  try {
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
    const state = { ...(reply?.state || reply), command_error: reply?.reason || null };
    emit(state);
    return state;
  } catch (error) {
    if (error?.message === "worker-command-timeout") {
      workerMessage("state").catch(() => {});
    }
    throw error;
  } finally {
    if (mutation) mutationInFlight = false;
  }
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
  } catch (error) {
    emit({ status: "failed", last_error: error?.message || "service-worker-unavailable" });
  }
}

window.BsbPwa = {
  state: () => ({ ...pwaState }),
  initialize: () => {
    if (!pwaState.active && !pwaState.staging && !pwaState.available_release) return workerMessage("probe");
    if (pwaState.staging) {
      if (["paused", "failed"].includes(pwaState.staging.status)) return workerMessage("resume", { operation_id: pwaState.staging.operation_id });
      return workerMessage("state");
    }
    return workerMessage("start", { reason: "first-install" });
  },
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
