/* Unified-site PWA registration and stable root entry. */
(() => {
  "use strict";

  const scriptUrl = document.currentScript?.src || "";

  async function openLatestQuarter() {
    const root = document.querySelector('[data-page="root"]');
    if (!root) return;
    try {
      const response = await fetch(root.dataset.archiveIndexUrl, {
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("archive index unavailable");
      const payload = await response.json();
      if (typeof payload.latest_quarter !== "string") return;
      window.location.replace(`${payload.latest_quarter}/index.html`);
    } catch {
      root.querySelector("[data-root-loading]")?.setAttribute("hidden", "");
      root.querySelector("[data-root-fallback]")?.removeAttribute("hidden");
    }
  }

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      const workerUrl = new URL("../sw.js", scriptUrl);
      const scopeUrl = new URL("../", scriptUrl);
      navigator.serviceWorker.register(workerUrl, { scope: scopeUrl.pathname }).catch(() => {
        // PWA enhancement failure must not block the online archive.
      });
    });
  }

  openLatestQuarter();
})();
