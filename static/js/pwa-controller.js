/* PWA transport is deliberately separate from archive interaction code. */
const pwaState = { status: "checking-local-state", active: null };

function announce(status, detail = {}) {
  pwaState.status = status;
  window.dispatchEvent(new CustomEvent("bsb-pwa-state", { detail: { ...pwaState, ...detail } }));
}

async function registerWorker() {
  if (!("serviceWorker" in navigator)) return;
  try {
    await navigator.serviceWorker.register(new URL("../sw.js", import.meta.url));
  } catch {
    // The Phase 3 shell remains honest: initialization cannot proceed yet.
  }
}

window.BsbPwa = {
  state: () => ({ ...pwaState }),
  initialize: () => announce("first-install-required"),
};

registerWorker().finally(() => announce("first-install-required"));
