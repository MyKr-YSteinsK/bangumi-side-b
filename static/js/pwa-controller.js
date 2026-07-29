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
    setTimeout(() => reject(new Error("worker-timeout")), 8000);
  });
  target.postMessage({ type, ...payload }, [channel.port2]);
  const state = await response;
  if (state?.error) throw new Error(state.error);
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
    await workerMessage("state");
  } catch {
    emit({ status: "failed", last_error: "service-worker-unavailable" });
  }
}

window.BsbPwa = {
  state: () => ({ ...pwaState }),
  initialize: () => workerMessage("start", { reason: "first-install" }),
  pause: () => workerMessage("pause"),
  resume: () => workerMessage("resume"),
  cancel: () => workerMessage("cancel"),
};

registerWorker();
