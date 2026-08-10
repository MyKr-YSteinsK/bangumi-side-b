/* Native, offline state helpers shared by quarter and Archive views. */
(() => {
  "use strict";

  const PAGE_SIZE_KEY = "bsb-archive-page-size";
  const PAGE_SIZES = Object.freeze([20, 40, 60, 100]);
  const SORTS = Object.freeze({
    "score-desc": "评分：高到低",
    "score-asc": "评分：低到高",
    "rating-count-desc": "评分人数：多到少",
    "rating-count-asc": "评分人数：少到多",
  });

  function normalize(value) {
    return String(value ?? "")
      .normalize("NFKC")
      .trim()
      .toLocaleLowerCase();
  }

  function readPageSize(storage = window.localStorage) {
    try {
      const value = Number(storage.getItem(PAGE_SIZE_KEY));
      return PAGE_SIZES.includes(value) ? value : PAGE_SIZES[0];
    } catch {
      return PAGE_SIZES[0];
    }
  }

  function writePageSize(value, storage = window.localStorage) {
    const size = PAGE_SIZES.includes(Number(value)) ? Number(value) : PAGE_SIZES[0];
    try {
      storage.setItem(PAGE_SIZE_KEY, String(size));
    } catch {
      // Private browsing can reject localStorage; the in-memory state still works.
    }
    return size;
  }

  function createState(overrides = {}) {
    const state = {
      media: "tv",
      scope: { kind: "quarter", value: "" },
      query: "",
      filters: { sources: [], tags: [], sections: [] },
      sort: "score-desc",
      page: 1,
      pageSize: readPageSize(),
      selectedSubjectId: null,
      selectedOccurrence: null,
      workspaceMode: "scope",
      ...overrides,
    };
    state.filters = {
      sources: [],
      tags: [],
      sections: [],
      ...(overrides.filters || {}),
    };
    state.pageSize = PAGE_SIZES.includes(Number(state.pageSize))
      ? Number(state.pageSize)
      : PAGE_SIZES[0];
    state.sort = SORTS[state.sort] ? state.sort : "score-desc";
    state.media = state.media === "movie" ? "movie" : "tv";
    return state;
  }

  function recordKey(record) {
    return [record.id ?? record.subject_id, record.quarter || "", record.appearance || record.appearance_kind || ""]
      .join("@");
  }

  function asRecord(value) {
    const record = { ...value };
    record.id = Number(record.id ?? record.subject_id);
    record.subject_id = record.id;
    record.media = String(record.media ?? record.media_format ?? "TV").toUpperCase();
    record.appearance = String(record.appearance ?? record.appearance_kind ?? "premiere");
    record.aliases = Array.isArray(record.aliases) ? record.aliases : [];
    record.allowed_tags = Array.isArray(record.allowed_tags) ? record.allowed_tags : [];
    record.search = normalize([
      record.preferred_title,
      record.original_title,
      ...record.aliases,
      record.id,
    ].join(" "));
    record.key = record.key || recordKey(record);
    return record;
  }

  function recordsFromQuarter(payload) {
    if (!payload || typeof payload !== "object") return [];
    const records = [];
    for (const [media, groups] of [["TV", payload.tv], ["MOVIE", payload.movie]]) {
      if (!groups || typeof groups !== "object") continue;
      for (const [appearance, values] of Object.entries(groups)) {
        if (!Array.isArray(values)) continue;
        for (const value of values) {
          records.push(asRecord({ ...value, id: value.subject_id, media, appearance, quarter: payload.quarter }));
        }
      }
    }
    return records;
  }

  function recordsFromCatalog(payload) {
    if (!payload || !Array.isArray(payload.records)) return [];
    return payload.records.map((value) => asRecord(value));
  }

  function matchesFilters(record, filters) {
    const sources = filters.sources || [];
    const tags = filters.tags || [];
    const sections = filters.sections || [];
    if (sources.length && !sources.includes(record.source)) return false;
    if (tags.length && !tags.some((tag) => record.allowed_tags.includes(tag))) return false;
    if (sections.length && !sections.includes(record.appearance)) return false;
    return true;
  }

  function compareNullable(a, b, direction = 1) {
    const aMissing = a === null || a === undefined;
    const bMissing = b === null || b === undefined;
    if (aMissing || bMissing) {
      if (aMissing && bMissing) return 0;
      return aMissing ? 1 : -1;
    }
    if (a < b) return -1 * direction;
    if (a > b) return 1 * direction;
    return 0;
  }

  function compareRecords(left, right, sort) {
    const scoreDirection = sort === "score-asc" ? 1 : -1;
    if (sort === "score-desc" || sort === "score-asc") {
      const score = compareNullable(left.score ?? left.rating_score, right.score ?? right.rating_score, scoreDirection);
      if (score) return score;
    } else {
      const countDirection = sort === "rating-count-asc" ? 1 : -1;
      const count = compareNullable(left.rating_count, right.rating_count, countDirection);
      if (count) return count;
    }
    const ratingCount = compareNullable(left.rating_count, right.rating_count, -1);
    if (ratingCount) return ratingCount;
    const air = compareNullable(left.air_date, right.air_date, 1);
    if (air) return air;
    const id = Number(left.id) - Number(right.id);
    if (id) return id;
    return String(left.key).localeCompare(String(right.key));
  }

  function applyPipeline(records, state) {
    const media = state.media === "movie" ? "MOVIE" : "TV";
    let visible = records.filter((record) => record.media === media);
    const query = normalize(state.query);
    if (query) visible = visible.filter((record) => record.search.includes(query));
    visible = visible.filter((record) => matchesFilters(record, state.filters));
    visible = visible.map((record, index) => ({ record, index }));
    visible.sort((a, b) => compareRecords(a.record, b.record, state.sort) || a.index - b.index);
    const ordered = visible.map((item) => item.record);
    const pageCount = Math.max(1, Math.ceil(ordered.length / state.pageSize));
    const page = Math.min(Math.max(1, Number(state.page) || 1), pageCount);
    const start = (page - 1) * state.pageSize;
    return {
      all: ordered,
      page: ordered.slice(start, start + state.pageSize),
      page,
      pageCount,
      total: ordered.length,
    };
  }

  function scopeCounts(records) {
    return records.reduce((counts, record) => {
      const media = record.media === "MOVIE" ? "movie" : "tv";
      counts[media] += 1;
      counts[record.appearance === "continuing" ? "continuing" : "premiere"] += 1;
      return counts;
    }, { tv: 0, movie: 0, premiere: 0, continuing: 0 });
  }

  function selectedRecord(records, subjectId, occurrence = null) {
    if (subjectId === null || subjectId === undefined) return null;
    const candidates = records.filter((record) => Number(record.id) === Number(subjectId));
    if (occurrence) return candidates.find((record) => record.key === occurrence) || null;
    return candidates.find((record) => record.appearance === "premiere") || candidates[0] || null;
  }

  const api = Object.freeze({
    PAGE_SIZES,
    SORTS,
    PAGE_SIZE_KEY,
    normalize,
    readPageSize,
    writePageSize,
    createState,
    recordKey,
    asRecord,
    recordsFromQuarter,
    recordsFromCatalog,
    matchesFilters,
    compareRecords,
    applyPipeline,
    scopeCounts,
    selectedRecord,
  });
  window.BsbArchive = api;
})();

(() => {
  "use strict";

  const archive = window.BsbArchive;
  if (!archive) return;
  const quarterRoot = document.querySelector('[data-page="quarter"][data-archive-app]');
  if (!quarterRoot) return;

  const state = archive.createState({
    scope: { kind: "quarter", value: quarterRoot.dataset.quarter || "" },
  });
  const rows = [...quarterRoot.querySelectorAll(".subject-row")];
  const rowByKey = new Map(rows.map((row) => [row.dataset.recordKey, row]));
  const sections = [...quarterRoot.querySelectorAll("[data-list-section]")];
  const scopePanel = quarterRoot.querySelector("[data-scope-panel]");
  const detailPanel = quarterRoot.querySelector("[data-detail-panel]");
  const filterPanel = quarterRoot.querySelector("[data-filter-panel]");
  const search = quarterRoot.querySelector("[data-search]");
  const summary = quarterRoot.querySelector("[data-results-summary]");
  const noResults = quarterRoot.querySelector("[data-no-results]");
  const activeFilters = quarterRoot.querySelector("[data-active-filters]");
  const pager = quarterRoot.querySelector("[data-pager]");
  let records = [];
  let payload = null;
  let loadError = false;

  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  function hashFor(record) {
    return `#bgm-${record.id}`;
  }

  function setHash(record, replace) {
    const url = new URL(window.location.href);
    url.hash = record ? hashFor(record) : "";
    if (replace) window.history.replaceState({}, "", url);
    else window.history.pushState({}, "", url);
  }

  function pageSizeSelect() {
    const select = quarterRoot.querySelector("[data-page-size]");
    if (!select) return;
    select.replaceChildren(...archive.PAGE_SIZES.map((size) => {
      const option = document.createElement("option");
      option.value = String(size);
      option.textContent = String(size);
      option.selected = size === state.pageSize;
      return option;
    }));
  }

  function scopeText(result) {
    const counts = archive.scopeCounts(records);
    const filters = [];
    if (state.query) filters.push(`搜索“${state.query}”`);
    if (state.filters.sources.length) filters.push(`来源 ${state.filters.sources.length}`);
    if (state.filters.tags.length) filters.push(`标签 ${state.filters.tags.length}`);
    if (state.filters.sections.length) filters.push(`分区 ${state.filters.sections.length}`);
    return { counts, filters, result };
  }

  function renderScope(result) {
    if (!scopePanel) return;
    const info = scopeText(result);
    scopePanel.innerHTML = `<p class="workspace-panel__code">ARCHIVE SCOPE</p>
      <h2>${esc(state.scope.value)}</h2>
      <p class="workspace-panel__lead">当前季度的已核验播出资料。</p>
      <dl class="scope-facts"><div><dt>TV</dt><dd>${info.counts.tv}</dd></div>
      <div><dt>MOVIE</dt><dd>${info.counts.movie}</dd></div>
      <div><dt>PREMIERE</dt><dd>${info.counts.premiere}</dd></div>
      <div><dt>CONTINUING</dt><dd>${info.counts.continuing}</dd></div></dl>
      <p class="workspace-panel__summary">${info.filters.length ? esc(info.filters.join(" · ")) : "当前没有额外筛选"}</p>`;
  }

  function renderRows(result) {
    const visible = new Set(result.page.map((record) => record.key));
    const position = new Map(result.all.map((record, index) => [record.key, index + 1]));
    for (const row of rows) {
      const record = records.find((item) => item.key === row.dataset.recordKey);
      const show = record && visible.has(record.key);
      row.hidden = !show;
      row.classList.toggle("is-selected", Boolean(record && state.selectedOccurrence === record.key));
      const button = row.querySelector("[data-open-subject]");
      if (button) button.setAttribute("aria-expanded", String(show && state.selectedOccurrence === record.key));
      const sequence = row.querySelector(".subject-row__sequence");
      if (sequence && record) sequence.textContent = String(position.get(record.key) || 0).padStart(3, "0");
    }
    for (const section of sections) {
      const media = section.dataset.listSection;
      const appearance = section.dataset.appearanceSection;
      const count = result.all.filter((record) =>
        (record.media === (media === "movie" ? "MOVIE" : "TV"))
        && record.appearance === appearance).length;
      section.hidden = state.media !== media || count === 0;
      const counter = section.querySelector("[data-section-count]");
      if (counter) counter.textContent = String(count).padStart(2, "0");
    }
    if (summary) summary.textContent = `${result.total} / ${records.filter((record) => record.media === (state.media === "movie" ? "MOVIE" : "TV")).length} 部 · 第 ${result.page} / ${result.pageCount} 页`;
    if (noResults) noResults.hidden = result.total !== 0;
  }

  function renderPager(result) {
    if (!pager) return;
    pager.replaceChildren();
    if (result.pageCount <= 1) {
      pager.hidden = true;
      return;
    }
    pager.hidden = false;
    const add = (label, page, disabled = false, current = false) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.disabled = disabled;
      button.setAttribute("aria-label", `第 ${page} 页`);
      if (current) button.setAttribute("aria-current", "page");
      button.addEventListener("click", () => {
        state.page = page;
        clearSelection(true);
        render();
        quarterRoot.querySelector(".master-pane")?.scrollIntoView({ block: "start" });
      });
      pager.append(button);
    };
    add("上一页", result.page - 1, result.page <= 1);
    for (let page = 1; page <= result.pageCount; page += 1) {
      if (result.pageCount > 7 && page > 2 && page < result.pageCount - 1 && Math.abs(page - result.page) > 1) {
        if (!pager.querySelector("[data-ellipsis]")) {
          const ellipsis = document.createElement("span");
          ellipsis.textContent = "…";
          ellipsis.dataset.ellipsis = "true";
          pager.append(ellipsis);
        }
        continue;
      }
      add(String(page).padStart(2, "0"), page, false, page === result.page);
    }
    add("下一页", result.page + 1, result.page >= result.pageCount);
  }

  function renderActiveFilters() {
    if (!activeFilters) return;
    activeFilters.replaceChildren();
    const values = [
      ...(state.query ? [{ label: `搜索：${state.query}`, type: "query", value: state.query }] : []),
      ...state.filters.sources.map((value) => ({ label: `来源：${value}`, type: "sources", value })),
      ...state.filters.tags.map((value) => ({ label: `标签：${value}`, type: "tags", value })),
      ...state.filters.sections.map((value) => ({ label: `分区：${value}`, type: "sections", value })),
    ];
    activeFilters.hidden = values.length === 0;
    for (const item of values) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "active-filter";
      button.textContent = `${item.label} ×`;
      button.addEventListener("click", () => {
        if (item.type === "query") {
          state.query = "";
          if (search) search.value = "";
        } else {
          state.filters[item.type] = state.filters[item.type].filter((value) => value !== item.value);
        }
        state.page = 1;
        clearSelection(true);
        render();
      });
      activeFilters.append(button);
    }
  }

  function render() {
    if (loadError) return;
    const result = archive.applyPipeline(records, state);
    state.page = result.page;
    renderRows(result);
    renderScope(result);
    renderActiveFilters();
    renderPager(result);
    if (detailPanel) detailPanel.hidden = state.workspaceMode !== "detail";
    if (scopePanel) scopePanel.hidden = state.workspaceMode !== "scope";
    if (filterPanel) filterPanel.hidden = state.workspaceMode !== "filter";
    quarterRoot.dataset.workspaceMode = state.workspaceMode;
    const sortButton = quarterRoot.querySelector("[data-sort-toggle]");
    if (sortButton) sortButton.textContent = archive.SORTS[state.sort];
    const filterButton = quarterRoot.querySelector("[data-filter-toggle]");
    if (filterButton) filterButton.querySelector("[data-filter-count]").textContent = filterCount() ? `(${filterCount()})` : "";
  }

  function filterCount() {
    return state.filters.sources.length + state.filters.tags.length + state.filters.sections.length;
  }

  function clearSelection(removeHash = false) {
    state.selectedSubjectId = null;
    state.selectedOccurrence = null;
    state.workspaceMode = "scope";
    if (removeHash) setHash(null, true);
  }

  function detailHtml(record) {
    const aliases = Array.isArray(record.aliases) ? record.aliases : [];
    const shownAliases = aliases.slice(0, 3);
    const moreAliases = Math.max(0, aliases.length - shownAliases.length);
    const cover = record.cover || record.cover_url;
    const coverHtml = cover
      ? `<button type="button" class="detail-cover-button" data-lightbox aria-label="查看封面"><img src="../${esc(String(cover).split("?", 1)[0])}" alt="${esc(record.preferred_title)}" width="180" height="270"></button>`
      : `<div class="detail-cover detail-cover--missing"><span>ARCHIVE</span></div>`;
    const tags = (record.allowed_tags || []).map((tag) => `<span class="tag">${esc(tag)}</span>`).join("");
    const summaryText = record.display_summary || record.summary;
    return `<div class="detail-head"><button type="button" class="detail-close" data-detail-close aria-label="关闭详情">×</button>
      <p class="workspace-panel__code">${esc(record.media)} / ${esc(record.appearance)}</p>
      <div class="detail-hero">${coverHtml}<div><h2>${esc(record.preferred_title)}</h2>
      ${record.original_title ? `<p class="detail-original">${esc(record.original_title)}</p>` : ""}
      <p class="detail-id">SUBJECT / ${esc(record.id)}</p></div></div></div>
      <dl class="detail-facts"><div><dt>播出季度</dt><dd>${esc(record.quarter)}</dd></div>
      <div><dt>首播季度</dt><dd>${esc(record.premiere_quarter || record.quarter || "—")}</dd></div>
      <div><dt>集数</dt><dd>${esc(record.episode_count ?? "—")}</dd></div>
      <div><dt>播出日期</dt><dd>${esc(record.air_date || "—")}</dd></div>
      <div><dt>评分</dt><dd class="detail-score">${record.score ?? record.rating_score ?? "—"}</dd></div>
      <div><dt>评分人数</dt><dd>${esc(record.rating_count ?? "—")}</dd></div></dl>
      ${record.appearance === "continuing" ? `<p class="detail-continuing">当前 appearance：continuing · premiere ${esc(record.premiere_quarter || "—")}</p>` : ""}
      ${shownAliases.length ? `<section class="detail-section"><h3>别名</h3><div class="detail-tags">${shownAliases.map((alias) => `<span class="tag">${esc(alias)}</span>`).join("")}${moreAliases ? `<span class="detail-more">+ 另外 ${moreAliases} 个标题</span>` : ""}</div></section>` : ""}
      ${tags ? `<section class="detail-section"><h3>标签 / 来源</h3><div class="detail-tags"><span class="tag tag--source">${esc(record.source || "unknown")}</span>${tags}</div></section>` : ""}
      ${summaryText ? `<section class="detail-section detail-summary"><h3>简介</h3><p>${esc(summaryText).replaceAll("\n", "<br>")}</p></section>` : ""}
      <p class="detail-footer"><a class="text-link" href="${esc(record.bangumi_url || `https://bgm.tv/subject/${record.id}`)}" target="_blank" rel="noreferrer">在 Bangumi 查看 ↗</a></p>`;
  }

  function selectRecord(record, replace = false) {
    if (!record) return;
    const hadSelection = state.selectedOccurrence !== null;
    state.selectedSubjectId = record.id;
    state.selectedOccurrence = record.key;
    state.workspaceMode = "detail";
    if (hadSelection) setHash(record, true);
    else setHash(record, replace);
    if (detailPanel) {
      detailPanel.innerHTML = detailHtml(record);
      detailPanel.hidden = false;
      detailPanel.querySelector("[data-detail-close]")?.addEventListener("click", () => {
        clearSelection(true);
        render();
      });
      detailPanel.querySelector("[data-lightbox]")?.addEventListener("click", () => openLightbox(record));
      detailPanel.scrollTop = 0;
    }
    render();
  }

  function openLightbox(record) {
    const cover = record.cover || record.cover_url;
    if (!cover) return;
    const dialog = document.createElement("dialog");
    dialog.className = "cover-lightbox";
    dialog.innerHTML = `<button type="button" class="lightbox-close" aria-label="关闭封面">×</button><img src="../${esc(String(cover).split("?", 1)[0])}" alt="${esc(record.preferred_title)}">`;
    document.body.append(dialog);
    const close = () => { dialog.close(); dialog.remove(); };
    dialog.querySelector("button").addEventListener("click", close);
    dialog.addEventListener("click", (event) => { if (event.target === dialog) close(); });
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    dialog.showModal();
  }

  function openHash() {
    const match = window.location.hash.match(/^#bgm-(\d+)$/);
    if (!match || !records.length) {
      if (!match && state.selectedOccurrence !== null) { clearSelection(false); render(); }
      return;
    }
    const candidate = archive.selectedRecord(records, Number(match[1]));
    if (!candidate) {
      setHash(null, true);
      return;
    }
    if (candidate.media === "MOVIE") state.media = "movie";
    const result = archive.applyPipeline(records, state);
    const index = result.all.findIndex((record) => record.key === candidate.key);
    if (index >= 0) state.page = Math.floor(index / state.pageSize) + 1;
    selectRecord(candidate, true);
  }

  function bindControls() {
    pageSizeSelect();
    search?.addEventListener("input", () => {
      state.query = search.value;
      state.page = 1;
      clearSelection(true);
      render();
    });
    quarterRoot.querySelectorAll("[data-media-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        state.media = button.dataset.mediaMode === "movie" ? "movie" : "tv";
        state.page = 1;
        clearSelection(true);
        render();
      });
    });
    quarterRoot.querySelector("[data-page-size]")?.addEventListener("change", (event) => {
      state.pageSize = archive.writePageSize(event.target.value);
      state.page = 1;
      clearSelection(true);
      render();
      pageSizeSelect();
    });
    quarterRoot.querySelector("[data-sort-toggle]")?.addEventListener("click", () => {
      const popover = quarterRoot.querySelector("[data-sort-popover]");
      if (!popover) return;
      popover.hidden = !popover.hidden;
      if (!popover.hidden) {
        popover.replaceChildren(...Object.entries(archive.SORTS).map(([value, label]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = label;
          button.setAttribute("aria-pressed", String(state.sort === value));
          button.addEventListener("click", () => {
            state.sort = value;
            state.page = 1;
            popover.hidden = true;
            clearSelection(true);
            render();
          });
          return button;
        }));
      }
    });
    quarterRoot.querySelector("[data-filter-toggle]")?.addEventListener("click", () => {
      state.workspaceMode = state.workspaceMode === "filter" ? "scope" : "filter";
      renderFilterPanel();
      render();
    });
    quarterRoot.querySelector("[data-clear-all]")?.addEventListener("click", () => {
      state.query = "";
      state.filters = { sources: [], tags: [], sections: [] };
      if (search) search.value = "";
      state.page = 1;
      clearSelection(true);
      render();
    });
    rows.forEach((row) => row.querySelector("[data-open-subject]")?.addEventListener("click", () => {
      selectRecord(records.find((record) => record.key === row.dataset.recordKey));
    }));
    window.addEventListener("popstate", openHash);
    window.addEventListener("hashchange", openHash);
  }

  function renderFilterPanel() {
    if (!filterPanel) return;
    const options = {
      sources: [...new Set(records.map((record) => record.source).filter(Boolean))].sort(),
      tags: [...new Set(records.flatMap((record) => record.allowed_tags))].sort(),
      sections: state.media === "tv" ? ["premiere", "continuing"] : ["premiere"],
    };
    filterPanel.innerHTML = `<div class="filter-panel__head"><p class="workspace-panel__code">FILTER WORKSPACE</p><button type="button" class="detail-close" data-filter-close aria-label="关闭筛选">×</button></div><h2>筛选资料</h2>`;
    for (const [group, values] of Object.entries(options)) {
      if (values.length <= 1) continue;
      const section = document.createElement("fieldset");
      section.className = "filter-group";
      const legend = document.createElement("legend");
      legend.textContent = group === "sources" ? "来源" : group === "tags" ? "标签" : "TV 分区";
      section.append(legend);
      for (const value of values) {
        const label = document.createElement("label");
        label.className = "filter-option";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.checked = state.filters[group].includes(value);
        input.addEventListener("change", () => {
          state.filters[group] = input.checked
            ? [...state.filters[group], value]
            : state.filters[group].filter((item) => item !== value);
          state.page = 1;
          clearSelection(true);
          render();
          renderFilterPanel();
        });
        label.append(input, document.createTextNode(value));
        section.append(label);
      }
      filterPanel.append(section);
    }
    filterPanel.querySelector("[data-filter-close]")?.addEventListener("click", () => {
      state.workspaceMode = "scope";
      render();
    });
  }

  async function load() {
    bindControls();
    renderScope({ all: [], page: [], pageCount: 1, page: 1, total: 0 });
    try {
      const response = await fetch(quarterRoot.dataset.dataUrl, { credentials: "same-origin" });
      if (!response.ok) throw new Error("data unavailable");
      payload = await response.json();
      records = archive.recordsFromQuarter(payload);
      renderFilterPanel();
      render();
      openHash();
    } catch {
      loadError = true;
      if (scopePanel) scopePanel.innerHTML = '<p class="workspace-panel__code">DATA UNAVAILABLE</p><h2>页面资料未完整生成</h2><p>建议重新 build。</p>';
    }
  }

  load();
})();
