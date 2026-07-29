/* Pages-only UI shell. Download and update actions are attached in later phases. */
const gate = document.querySelector("[data-pwa-gate]");
const gateStatus = document.querySelector("[data-pwa-gate-status]");

function setGate(state) {
  if (!gate) return;
  gate.dataset.pwaGate = state.status;
  gate.hidden = state.status === "ready";
  if (gateStatus) gateStatus.textContent = gate.dataset.pwaGateLabel || state.status;
}

window.addEventListener("bsb-pwa-state", (event) => setGate(event.detail));
if (window.BsbPwa) setGate(window.BsbPwa.state());

document.querySelector("[data-pwa-start]")?.addEventListener("click", () => {
  window.BsbPwa?.initialize();
});
