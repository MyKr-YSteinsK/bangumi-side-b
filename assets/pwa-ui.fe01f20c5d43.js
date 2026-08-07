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
const initializeSettings = document.querySelector("[data-pwa-initialize]");
const enterArchive = document.querySelector("[data-pwa-enter]");
const completion = document.querySelector("[data-pwa-complete]");
const enableRetry = document.querySelectorAll("[data-pwa-enable-retry]");
const resumeSettings = document.querySelector("[data-pwa-resume-settings]");
const cancelSettings = document.querySelector("[data-pwa-cancel-settings]");
const updateDialog = document.querySelector("[data-pwa-update-dialog]");
const homeLink = document.querySelector("[data-pwa-home]");
let gateInitializationStarted = false;

const errorMessages = {
  "release-unavailable": "暂时无法读取发布信息，请检查网络后重试。",
  "manifest-hash-invalid": "发布清单校验失败，未开始下载资料。",
  "storage-insufficient": "浏览器剩余空间不足，旧资料已保留。",
  "file-unavailable": "下载文件不可用，可重试或取消未完成下载。",
  "file-size-invalid": "下载文件大小不正确，可重试或取消未完成下载。",
  "file-hash-invalid": "下载文件校验失败，可重试或取消未完成下载。",
  "file-cache-write-failed": "浏览器无法写入下载文件，可重试或取消未完成下载。",
  "snapshot-verify-failed": "完整快照复核失败，可重试或取消未完成下载。",
  "staging-release-changed": "下载期间发布版本已变化，请决定是否取消旧下载。",
  "cache-cleanup-incomplete": "资料已切换，但部分旧缓存尚未清理。",
  "snapshot-corrupted": "本地快照不完整，请重新下载。",
  "worker-command-timeout": "浏览器未及时确认命令，请稍后查看当前状态。",
  "worker-registration-failed": "未能启用离线控制器，请重试。",
  "controller-unavailable": "离线控制器尚未接管当前页面，请重试启用。",
  "service-worker-unavailable": "此浏览器不支持离线控制器，无法初始化本地资料库。",
  "initialization-in-progress": "初始化已在进行。",
  "operation-busy": "操作已在进行。",
};

function formatBytes(value) {
  if (!Number.isFinite(value) || value < 1024) return `${value || 0} B`;
  const units = ["KB", "MB", "GB"];
  let size = value / 1024; let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[unit]}`;
}
function errorText(code) {
  if (!code) return "";
  return errorMessages[code] || "操作未完成，请重试或查看设置页。";
}
function failureText(failure, fallback) {
  if (!failure) return errorText(fallback);
  const details = [errorText(failure.error_code)];
  if (failure.failed_url) details.push(`文件：${failure.failed_url.replace(/^\/(?:[^/]+)\//, "")}`);
  if (Number.isFinite(failure.http_status)) details.push(`HTTP ${failure.http_status}`);
  if (Number.isFinite(failure.expected_bytes) || Number.isFinite(failure.actual_bytes)) {
    details.push(`预期 ${formatBytes(failure.expected_bytes || 0)}，实际 ${formatBytes(failure.actual_bytes || 0)}`);
  }
  return details.join("\n");
}
function describe(state) {
  if (state.command_error) return errorText(state.command_error);
  if (state.status === "waiting-for-controller") return "正在启用离线控制器";
  if (state.status === "checking-local-state") return "正在读取本地资料状态";
  if (state.status === "probing-release") return "正在读取可用资料版本";
  if (state.status === "ready") return state.cleanup_warning ? errorText("cache-cleanup-incomplete") : "本地快照已就绪";
  if (state.status === "ready-to-download" || state.status === "probing") return "已接受命令，正在准备完整资料快照";
  if (state.status === "downloading") return "正在校验并下载完整资料快照";
  if (state.status === "verifying" || state.status === "activating") return "正在验证并切换完整资料快照";
  if (state.status === "paused") return "下载已暂停，可在此继续";
  if (state.status === "staging-release-changed") return errorText("staging-release-changed");
  if (state.status === "failed") return failureText(state.staging?.failure, state.staging?.last_error || state.last_error);
  return "需要初始化本地资料库";
}
function setText(selector, value) {
  const node = document.querySelector(selector); if (node) node.textContent = value;
}
function localTime(value) {
  const date = new Date(value || "");
  if (Number.isNaN(date.valueOf())) return value || "—";
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
function setTime(selector, value) {
  const node = document.querySelector(selector);
  if (!node) return;
  node.textContent = localTime(value);
  node.dataset.iso = value || "";
  if (node instanceof HTMLTimeElement) node.dateTime = value || "";
}
function latestQuarter(value) {
  return /^\d{4}-(01|04|07|10)$/.test(value || "") ? value : null;
}
function archiveHref(quarter) {
  const manifest = document.querySelector('link[rel="manifest"]')?.href;
  if (!manifest) return "";
  return new URL(quarter ? `quarters/${quarter}/index.html` : "index.html", manifest).href;
}
function setHomeHref(state) {
  if (!homeLink) return;
  const manifest = document.querySelector('link[rel="manifest"]')?.href;
  if (!manifest) return;
  const quarter = latestQuarter(state.active?.latest_quarter);
  homeLink.href = new URL(state.active ? (quarter ? `quarters/${quarter}/index.html` : "index.html") : "settings/index.html", manifest).href;
}
function factsFor(state) { return state.active || state.available_release || state.staging || null; }
function renderAvailableFacts(state) {
  const facts = factsFor(state);
  setText("[data-pwa-gate-release]", facts?.release_version || "正在读取发布信息");
  setText("[data-pwa-gate-app-version]", facts?.app_version || "—");
  setText("[data-pwa-gate-counts]", facts ? `${facts.quarter_count || 0} 个季度 / ${facts.subject_count || 0} 部` : "—");
  setText("[data-pwa-gate-size]", facts ? formatBytes(facts.total_bytes) : "—");
  setTime("[data-pwa-gate-generated-at]", facts?.generated_at);
}
function renderSettings(state) {
  const active = state.active;
  const staging = state.staging;
  setText("[data-pwa-active-release]", active?.release_version || "尚未初始化");
  setTime("[data-pwa-generated-at]", active?.generated_at);
  setText("[data-pwa-counts]", active ? `${active.quarter_count} 个季度 / ${active.subject_count} 部` : "—");
  setText("[data-pwa-snapshot-size]", active ? formatBytes(active.total_bytes) : "—");
  setText("[data-pwa-status]", describe(state));
  setText("[data-pwa-cleanup-warning]", state.cleanup_warning ? errorText("cache-cleanup-incomplete") : "");
  [check, clear].filter(Boolean).forEach((button) => { button.disabled = !active || Boolean(staging); });
  const firstInstallState = ["checking-local-state", "waiting-for-controller", "probing-release", "first-install-required"].includes(state.status);
  const canInitialize = !active && !staging && firstInstallState;
  if (initializeSettings) {
    initializeSettings.hidden = !canInitialize;
    initializeSettings.disabled = !state.controller_ready || state.status !== "first-install-required";
    initializeSettings.title = state.controller_ready ? "" : "正在启用离线控制器";
  }
  if (redownload) redownload.disabled = !active || Boolean(staging);
  const resumable = staging && ["paused", "failed"].includes(staging.status);
  if (resumeSettings) resumeSettings.hidden = !resumable;
  if (cancelSettings) cancelSettings.hidden = !staging;
  const quarter = latestQuarter(active?.latest_quarter);
  if (enterArchive) {
    enterArchive.hidden = !active || Boolean(staging);
    enterArchive.href = archiveHref(quarter);
    enterArchive.textContent = quarter ? `进入 ${quarter} 资料库` : "进入资料库";
  }
  if (completion) completion.textContent = active ? `${active.subject_count} 部作品已完成校验，可离线浏览。` : "";
  const estimate = staging?.storage_check;
  if (estimate?.available) setText("[data-pwa-storage]", `${formatBytes(estimate.usage_bytes)} / ${formatBytes(estimate.quota_bytes)}；本次至少需 ${formatBytes(estimate.required_bytes)}`);
  else navigator.storage?.estimate?.().then((value) => setText("[data-pwa-storage]", `${formatBytes(value.usage || 0)} / ${formatBytes(value.quota || 0)}`)).catch(() => setText("[data-pwa-storage]", "浏览器未提供估算，可继续下载"));
}
function listSummary(container, items) {
  if (!container) return;
  container.replaceChildren(...items.map((item) => { const node = document.createElement("li"); node.textContent = item; return node; }));
  container.hidden = !items.length;
}
function renderUpdate(update, staging) {
  if (!update || !updateDialog) return;
  const facts = updateDialog.querySelector("[data-pwa-update-facts]");
  if (facts) facts.replaceChildren(...[["当前资料", window.BsbPwa?.state?.().active?.release_version || "尚未初始化"], ["可用资料", update.release_version], ["程序版本", update.app_version], ["季度 / 作品", `${update.quarter_count} / ${update.subject_count}`], ["下载大小", formatBytes(update.total_bytes)]].map(([label, value]) => { const row = document.createElement("div"); const term = document.createElement("dt"); const detail = document.createElement("dd"); term.textContent = label; detail.textContent = value; row.append(term, detail); return row; }));
  listSummary(updateDialog.querySelector("[data-pwa-update-system]"), update.summary?.system || []);
  listSummary(updateDialog.querySelector("[data-pwa-update-data]"), update.summary?.data || []);
  setText("[data-pwa-update-storage]", staging?.storage_check?.available ? `空间预检通过：至少需要 ${formatBytes(staging.storage_check.required_bytes)}` : "开始下载前将进行空间预检。浏览器不提供估算时仍可继续。");
  if (!updateDialog.open) updateDialog.showModal();
}
function setGate(state) {
  if (gate) { gate.dataset.pwaGate = state.status; gate.hidden = Boolean(state.active); }
  if (gateStatus) gateStatus.textContent = describe(state);
  const staging = state.staging || {}; const total = Number(staging.total_bytes || 0); const verified = Number(staging.verified_bytes || 0);
  if (progress) { progress.hidden = !total; progress.max = total || 1; progress.value = verified; }
  if (progressLabel) {
    progressLabel.hidden = !total;
    progressLabel.textContent = total
      ? `${formatBytes(verified)} / ${formatBytes(total)} · 已复用 ${formatBytes(staging.reused_bytes || 0)} · 新下载 ${formatBytes(staging.downloaded_bytes || 0)}`
      : "";
  }
  const firstInstallState = ["checking-local-state", "waiting-for-controller", "probing-release", "first-install-required"].includes(state.status);
  const canInitialize = !state.active && !staging.operation_id && firstInstallState;
  if (start) {
    start.hidden = !canInitialize;
    start.disabled = !state.controller_ready || state.status !== "first-install-required";
    start.textContent = ["checking-local-state", "probing-release"].includes(state.status) ? "正在读取资料版本" : "初始化本地资料库";
    start.title = state.controller_ready ? "" : "正在启用离线控制器";
  }
  enableRetry.forEach((button) => { button.hidden = state.status !== "failed" || !["worker-registration-failed", "controller-unavailable", "service-worker-unavailable"].includes(state.last_error); });
  if (pause) pause.hidden = staging.status !== "downloading";
  if (resume) resume.hidden = !["paused", "failed"].includes(staging.status);
  if (cancel) cancel.hidden = !["probing", "ready-to-download", "downloading", "verifying", "activating", "paused", "failed", "staging-release-changed"].includes(staging.status);
  renderAvailableFacts(state); renderSettings(state); renderUpdate(state.available_update, staging); setHomeHref(state);
  if (state.active && state.reload_available) {
    if (gateInitializationStarted) {
      const guard = `bsb-pwa-gate-reload:${window.location.pathname}`;
      if (!sessionStorage.getItem(guard)) {
        sessionStorage.setItem(guard, "1");
        window.location.reload();
      }
    } else if (enterArchive) {
      requestAnimationFrame(() => enterArchive.focus());
    }
  } else if (state.active) {
    sessionStorage.removeItem(`bsb-pwa-gate-reload:${window.location.pathname}`);
  }
}

window.addEventListener("bsb-pwa-state", (event) => setGate(event.detail));
setGate(window.BsbPwa?.state?.() || { status: "checking-local-state" });
function command(action) {
  Promise.resolve(action()).catch((error) => {
    window.dispatchEvent(new CustomEvent("bsb-pwa-state", { detail: { ...window.BsbPwa?.state?.(), last_error: error?.message || "worker-command-timeout" } }));
  });
}
function initializeAction(button) {
  if (button) {
    button.textContent = "正在读取资料版本";
  }
  command(() => window.BsbPwa?.initialize());
}
start?.addEventListener("click", () => { gateInitializationStarted = true; initializeAction(start); });
initializeSettings?.addEventListener("click", () => initializeAction(initializeSettings));
enableRetry.forEach((button) => button.addEventListener("click", () => command(() => window.BsbPwa?.enableController())));
pause?.addEventListener("click", () => command(() => window.BsbPwa?.pause()));
resume?.addEventListener("click", () => command(() => window.BsbPwa?.resume()));
cancel?.addEventListener("click", () => command(() => window.BsbPwa?.cancel()));
check?.addEventListener("click", () => command(() => window.BsbPwa?.checkUpdate()));
redownload?.addEventListener("click", () => command(() => window.BsbPwa?.redownload()));
resumeSettings?.addEventListener("click", () => command(() => window.BsbPwa?.resume()));
cancelSettings?.addEventListener("click", () => command(() => window.BsbPwa?.cancel()));
clear?.addEventListener("click", () => { if (window.confirm("将删除已下载的季度页面、作品详情与封面。\n程序外壳仍会保留，之后需要重新初始化。\n确定清除吗？")) command(() => window.BsbPwa?.clear()); });
document.querySelector("[data-pwa-update-start]")?.addEventListener("click", () => { updateDialog?.close(); command(() => window.BsbPwa?.update()); });
document.querySelector("[data-pwa-update-later]")?.addEventListener("click", () => updateDialog?.close());
