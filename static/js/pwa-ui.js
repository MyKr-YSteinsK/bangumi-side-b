/* State rendering only; it never independently fetches release metadata. */
const gate = document.querySelector("[data-pwa-gate]");
const gateStatus = document.querySelector("[data-pwa-gate-status]");
const progress = document.querySelector("[data-pwa-progress]");
const progressLabel = document.querySelector("[data-pwa-progress-label]");
const start = document.querySelector("[data-pwa-start]");
const pause = document.querySelector("[data-pwa-pause]");
const resume = document.querySelector("[data-pwa-resume]");
const cancel = document.querySelector("[data-pwa-cancel]");

function describe(state) {
  if (state.status === "ready") return "本地快照已就绪";
  if (state.status === "downloading") return "正在校验并下载完整资料快照";
  if (state.status === "paused") return "下载已暂停，可在此继续";
  if (state.status === "failed") return `下载未完成：${state.last_error || "unknown"}`;
  return "需要联网完成首次初始化";
}

function setGate(state) {
  if (gate) {
    gate.dataset.pwaGate = state.status;
    gate.hidden = state.status === "ready";
  }
  if (gateStatus) gateStatus.textContent = describe(state);
  const staging = state.staging || {};
  const total = Number(staging.total_bytes || 0);
  const bytes = Number(staging.downloaded_bytes || 0);
  if (progress) {
    progress.hidden = !total;
    progress.max = total || 1;
    progress.value = bytes;
  }
  if (progressLabel) {
    progressLabel.hidden = !total;
    progressLabel.textContent = total ? `${bytes} / ${total} B` : "";
  }
  if (start) start.hidden = !["first-install-required", "failed"].includes(state.status);
  if (pause) pause.hidden = state.status !== "downloading";
  if (resume) resume.hidden = !["paused", "failed"].includes(state.status);
  if (cancel) cancel.hidden = !["downloading", "paused", "failed"].includes(state.status);
}

window.addEventListener("bsb-pwa-state", (event) => setGate(event.detail));
setGate(window.BsbPwa?.state?.() || { status: "checking-local-state" });

start?.addEventListener("click", () => window.BsbPwa?.initialize());
pause?.addEventListener("click", () => window.BsbPwa?.pause());
resume?.addEventListener("click", () => window.BsbPwa?.resume());
cancel?.addEventListener("click", () => window.BsbPwa?.cancel());
