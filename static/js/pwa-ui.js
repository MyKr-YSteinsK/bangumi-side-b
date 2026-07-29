/* State rendering only; it never independently fetches release metadata. */
const gate = document.querySelector("[data-pwa-gate]");
const gateStatus = document.querySelector("[data-pwa-gate-status]");
const progress = document.querySelector("[data-pwa-progress]");
const progressLabel = document.querySelector("[data-pwa-progress-label]");
const start = document.querySelector("[data-pwa-start]");
const pause = document.querySelector("[data-pwa-pause]");
const resume = document.querySelector("[data-pwa-resume]");
const cancel = document.querySelector("[data-pwa-cancel]");
const check = document.querySelector("[data-pwa-check]");
const redownload = document.querySelector("[data-pwa-redownload]");
const clear = document.querySelector("[data-pwa-clear]");
const resumeSettings = document.querySelector("[data-pwa-resume-settings]");
const cancelSettings = document.querySelector("[data-pwa-cancel-settings]");
const updateDialog = document.querySelector("[data-pwa-update-dialog]");

function formatBytes(value) {
  if (!Number.isFinite(value) || value < 1024) return `${value || 0} B`;
  const units = ["KB", "MB", "GB"];
  let size = value / 1024; let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unit]}`;
}
function describe(state) {
  if (state.status === "ready") return state.last_error ? `当前资料仍可用；${state.last_error}` : "本地快照已就绪";
  if (state.status === "downloading") return "正在校验并下载完整资料快照";
  if (state.status === "paused") return "下载已暂停，可在此继续";
  if (state.status === "failed") return `下载未完成：${state.last_error || "unknown"}`;
  return "需要联网完成首次初始化";
}
function setText(selector, value) {
  const node = document.querySelector(selector); if (node) node.textContent = value;
}
function renderSettings(state) {
  const active = state.active;
  setText("[data-pwa-active-release]", active?.release_version || "尚未初始化");
  setText("[data-pwa-generated-at]", active?.generated_at || "—");
  setText("[data-pwa-counts]", active ? `${active.quarter_count} 个季度 / ${active.subject_count} 部` : "—");
  setText("[data-pwa-snapshot-size]", active ? formatBytes(active.total_bytes) : "—");
  setText("[data-pwa-status]", describe(state));
  [check, redownload, clear].filter(Boolean).forEach((button) => { button.disabled = !active; });
  const pending = state.staging && ["paused", "failed"].includes(state.staging.status);
  if (resumeSettings) resumeSettings.hidden = !pending;
  if (cancelSettings) cancelSettings.hidden = !pending;
  navigator.storage?.estimate?.().then((estimate) => setText("[data-pwa-storage]", `${formatBytes(estimate.usage || 0)} / ${formatBytes(estimate.quota || 0)}`)).catch(() => setText("[data-pwa-storage]", "浏览器未提供估算"));
}
function renderUpdate(update) {
  if (!update || !updateDialog) return;
  const facts = updateDialog.querySelector("[data-pwa-update-facts]");
  if (facts) facts.replaceChildren(...[["最新资料", update.release_version], ["程序版本", update.app_version], ["季度 / 作品", `${update.quarter_count} / ${update.subject_count}`], ["下载大小", formatBytes(update.total_bytes)]].map(([label, value]) => { const row = document.createElement("div"); const term = document.createElement("dt"); const detail = document.createElement("dd"); term.textContent = label; detail.textContent = value; row.append(term, detail); return row; }));
  setText("[data-pwa-update-summary]", update.summary || "完整快照将在校验后原子切换。");
  if (!updateDialog.open) updateDialog.showModal();
}
function setGate(state) {
  if (gate) { gate.dataset.pwaGate = state.status; gate.hidden = state.status === "ready"; }
  if (gateStatus) gateStatus.textContent = describe(state);
  const staging = state.staging || {}; const total = Number(staging.total_bytes || 0); const bytes = Number(staging.downloaded_bytes || 0);
  if (progress) { progress.hidden = !total; progress.max = total || 1; progress.value = bytes; }
  if (progressLabel) { progressLabel.hidden = !total; progressLabel.textContent = total ? `${formatBytes(bytes)} / ${formatBytes(total)}` : ""; }
  if (start) start.hidden = !["first-install-required", "failed"].includes(state.status);
  if (pause) pause.hidden = state.status !== "downloading";
  if (resume) resume.hidden = !["paused", "failed"].includes(state.status);
  if (cancel) cancel.hidden = !["downloading", "paused", "failed"].includes(state.status);
  renderSettings(state); renderUpdate(state.available_update);
}

window.addEventListener("bsb-pwa-state", (event) => setGate(event.detail));
setGate(window.BsbPwa?.state?.() || { status: "checking-local-state" });
function command(action) {
  Promise.resolve(action()).catch((error) => {
    window.dispatchEvent(new CustomEvent("bsb-pwa-state", { detail: { ...window.BsbPwa?.state?.(), last_error: error?.message || "worker-command-timeout" } }));
  });
}
start?.addEventListener("click", () => command(() => window.BsbPwa?.initialize()));
pause?.addEventListener("click", () => command(() => window.BsbPwa?.pause()));
resume?.addEventListener("click", () => command(() => window.BsbPwa?.resume()));
cancel?.addEventListener("click", () => command(() => window.BsbPwa?.cancel()));
check?.addEventListener("click", () => command(() => window.BsbPwa?.checkUpdate()));
redownload?.addEventListener("click", () => command(() => window.BsbPwa?.redownload()));
resumeSettings?.addEventListener("click", () => command(() => window.BsbPwa?.resume()));
cancelSettings?.addEventListener("click", () => command(() => window.BsbPwa?.cancel()));
clear?.addEventListener("click", () => { if (window.confirm("将删除已下载的季度、详情与封面，应用 Shell 会保留。确定继续吗？")) command(() => window.BsbPwa?.clear()); });
document.querySelector("[data-pwa-update-start]")?.addEventListener("click", () => { updateDialog?.close(); command(() => window.BsbPwa?.update()); });
document.querySelector("[data-pwa-update-later]")?.addEventListener("click", () => updateDialog?.close());
