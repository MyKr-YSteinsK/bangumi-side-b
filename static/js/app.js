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
      filterOptionQuery: "",
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
      pageRecords: ordered.slice(start, start + state.pageSize),
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

  function appearanceLabel(value) {
    return value === "continuing" ? "续播" : "首播";
  }

  function sourceLabel(value) {
    return value && value !== "unknown" ? value : "来源未知";
  }

  function recordsForMedia(records, media) {
    const value = media === "movie" ? "MOVIE" : "TV";
    return records.filter((record) => record.media === value);
  }

  function availableFilterValues(records, media) {
    const local = recordsForMedia(records, media);
    const appearances = new Set(local.map((record) => record.appearance));
    return {
      sources: [...new Set(local.map((record) => record.source).filter(Boolean))].sort(),
      tags: [...new Set(local.flatMap((record) => record.allowed_tags))].sort(),
      sections: ["premiere", "continuing"].filter((value) => appearances.has(value)),
    };
  }

  function normalizeFiltersForMedia(state, records) {
    const available = availableFilterValues(records, state.media);
    for (const group of ["sources", "tags", "sections"]) {
      state.filters[group] = state.filters[group].filter((value) => available[group].includes(value));
    }
  }

  function paginationTokens(page, pageCount) {
    const count = Math.max(1, Math.floor(Number(pageCount) || 1));
    const current = Math.min(Math.max(1, Math.floor(Number(page) || 1)), count);
    if (count <= 7) return Array.from({ length: count }, (_, index) => index + 1);
    if (current <= 4) return [1, 2, 3, 4, "ellipsis", count];
    if (current >= count - 3) {
      return [1, "ellipsis", count - 3, count - 2, count - 1, count];
    }
    return [1, "ellipsis", current - 1, current, current + 1, "ellipsis", count];
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
    appearanceLabel,
    sourceLabel,
    recordsForMedia,
    availableFilterValues,
    normalizeFiltersForMedia,
    paginationTokens,
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
  let recordByKey = new Map();
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
    const visible = new Set(result.pageRecords.map((record) => record.key));
    const position = new Map(result.all.map((record, index) => [record.key, index + 1]));
    for (const row of rows) {
      const record = recordByKey.get(row.dataset.recordKey);
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
    for (const token of archive.paginationTokens(result.page, result.pageCount)) {
      if (token === "ellipsis") {
        const ellipsis = document.createElement("span");
        ellipsis.textContent = "…";
        ellipsis.dataset.ellipsis = "true";
        ellipsis.setAttribute("aria-hidden", "true");
        pager.append(ellipsis);
      } else {
        add(String(token).padStart(2, "0"), token, false, token === result.page);
      }
    }
    add("下一页", result.page + 1, result.page >= result.pageCount);
  }

  function renderActiveFilters() {
    if (!activeFilters) return;
    activeFilters.replaceChildren();
    const values = [
      ...(state.query ? [{ label: `搜索：${state.query}`, type: "query", value: state.query }] : []),
      ...state.filters.sources.map((value) => ({ label: `来源：${archive.sourceLabel(value)}`, type: "sources", value })),
      ...state.filters.tags.map((value) => ({ label: `标签：${value}`, type: "tags", value })),
      ...state.filters.sections.map((value) => ({ label: `分区：${archive.appearanceLabel(value)}`, type: "sections", value })),
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
    quarterRoot.querySelectorAll("[data-media-mode]").forEach((button) => {
      button.setAttribute("aria-selected", String(button.dataset.mediaMode === state.media));
    });
    const filterButton = quarterRoot.querySelector("[data-filter-toggle]");
    if (filterButton) {
      filterButton.querySelector("[data-filter-count]").textContent = filterCount() ? `(${filterCount()})` : "";
      filterButton.setAttribute("aria-expanded", String(state.workspaceMode === "filter"));
    }
    if (sortButton) sortButton.setAttribute("aria-expanded", String(quarterRoot.querySelector("[data-sort-popover]")?.hidden === false));
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

  function closeFilter() {
    const result = archive.applyPipeline(records, state);
    const selected = archive.selectedRecord(records, state.selectedSubjectId, state.selectedOccurrence);
    if (selected && result.all.some((record) => record.key === selected.key)) {
      state.workspaceMode = "detail";
    } else {
      clearSelection(true);
    }
  }

  function focusRecordTrigger(recordKey) {
    rows.find((row) => row.dataset.recordKey === recordKey)
      ?.querySelector("[data-open-subject]")?.focus();
  }

  function closeFilterAndRestoreFocus() {
    closeFilter();
    render();
    quarterRoot.querySelector("[data-filter-toggle]")?.focus();
  }

  function detailHtml(record) {
    const aliases = Array.isArray(record.aliases) ? record.aliases : [];
    const cover = record.cover || record.cover_url;
    const coverHtml = cover
      ? `<button type="button" class="detail-cover-button" data-lightbox aria-label="查看封面"><img src="../${esc(String(cover))}" alt="${esc(record.preferred_title)}" width="180" height="270"></button>`
      : `<div class="detail-cover detail-cover--missing"><span>ARCHIVE</span></div>`;
    const tags = (record.allowed_tags || []).map((tag) => `<span class="tag">${esc(tag)}</span>`).join("");
    const summaryText = record.display_summary || record.summary;
    const facts = [
      record.quarter ? ["播出季度", record.quarter] : null,
      record.premiere_quarter ? ["首播季度", record.premiere_quarter] : null,
      record.episode_count !== null && record.episode_count !== undefined ? ["集数", record.episode_count] : null,
      record.air_date ? ["播出日期", record.air_date] : null,
      record.end_date ? ["结束日期", record.end_date] : null,
      ["评分", record.score ?? record.rating_score ?? "—", "detail-score"],
      record.rating_count !== null && record.rating_count !== undefined ? ["评分人数", record.rating_count] : null,
      ["来源", archive.sourceLabel(record.source)],
    ].filter(Boolean).map(([label, value, className]) => `<div><dt>${label}</dt><dd${className ? ` class="${className}"` : ""}>${esc(value)}</dd></div>`).join("");
    return `<div class="detail-head"><button type="button" class="detail-close" data-detail-close aria-label="关闭详情">×</button>
      <p class="workspace-panel__code">${esc(record.media)} / ${esc(record.appearance)}</p>
      <div class="detail-hero">${coverHtml}<div><h2>${esc(record.preferred_title)}</h2>
      ${record.original_title ? `<p class="detail-original">${esc(record.original_title)}</p>` : ""}
      <p class="detail-id">SUBJECT / ${esc(record.id)}</p></div></div></div>
      <dl class="detail-facts">${facts}</dl>
      ${record.appearance === "continuing" ? `<p class="detail-continuing">当前归档：续播${record.premiere_quarter ? ` · 首播 ${esc(record.premiere_quarter)}` : ""}</p>` : ""}
      ${aliases.length ? `<section class="detail-section"><h3>别名</h3><div class="detail-tags">${aliases.map((alias) => `<span class="tag">${esc(alias)}</span>`).join("")}</div></section>` : ""}
      ${tags ? `<section class="detail-section"><h3>标签</h3><div class="detail-tags">${tags}</div></section>` : ""}
      ${summaryText ? `<section class="detail-section detail-summary"><h3>简介</h3><p>${esc(summaryText).replaceAll("\n", "<br>")}</p></section>` : ""}
      <p class="detail-footer"><a class="text-link" href="${esc(record.bangumi_url || ("https://" + "bgm.tv/subject/" + record.id))}" target="_blank" rel="noreferrer">在 Bangumi 查看 ↗</a></p>`;
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
        focusRecordTrigger(record.key);
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
    dialog.innerHTML = `<button type="button" class="lightbox-close" aria-label="关闭封面">×</button><img src="../${esc(String(cover))}" alt="${esc(record.preferred_title)}">`;
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
    state.media = candidate.media === "MOVIE" ? "movie" : "tv";
    archive.normalizeFiltersForMedia(state, records);
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
        archive.normalizeFiltersForMedia(state, records);
        state.page = 1;
        clearSelection(true);
        renderFilterPanel();
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
      if (state.workspaceMode === "filter") {
        closeFilter();
      } else {
        state.workspaceMode = "filter";
      }
      renderFilterPanel();
      render();
    });
    quarterRoot.querySelector("[data-clear-all]")?.addEventListener("click", () => {
      state.query = "";
      state.filterOptionQuery = "";
      state.filters = { sources: [], tags: [], sections: [] };
      if (search) search.value = "";
      state.page = 1;
      clearSelection(true);
      render();
    });
    rows.forEach((row) => row.querySelector("[data-open-subject]")?.addEventListener("click", () => {
      selectRecord(recordByKey.get(row.dataset.recordKey));
    }));
    window.addEventListener("popstate", openHash);
    window.addEventListener("hashchange", openHash);
  }

  function renderFilterPanel() {
    if (!filterPanel) return;
    const options = archive.availableFilterValues(records, state.media);
    filterPanel.innerHTML = `<div class="filter-panel__head"><p class="workspace-panel__code">FILTER WORKSPACE</p><button type="button" class="detail-close" data-filter-close aria-label="关闭筛选">×</button></div><h2>筛选资料</h2><label class="filter-option-search"><span class="sr-only">搜索筛选选项</span><input type="search" data-filter-option-search placeholder="搜索选项名称"></label>`;
    const optionSearch = filterPanel.querySelector("[data-filter-option-search]");
    if (optionSearch) optionSearch.value = state.filterOptionQuery;
    const applyOptionQuery = () => {
      const query = archive.normalize(state.filterOptionQuery);
      filterPanel.querySelectorAll("[data-filter-option]").forEach((option) => {
        option.hidden = option.dataset.filterOption?.includes(query) === false;
      });
    };
    optionSearch?.addEventListener("input", () => {
      state.filterOptionQuery = optionSearch.value;
      applyOptionQuery();
    });
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
        const shown = group === "sections"
          ? archive.appearanceLabel(value)
          : group === "sources"
            ? archive.sourceLabel(value)
            : value;
        label.dataset.filterOption = archive.normalize(shown);
        const input = document.createElement("input");
        input.type = "checkbox";
        input.dataset.filterGroup = group;
        input.dataset.filterValue = value;
        input.checked = state.filters[group].includes(value);
        input.addEventListener("change", () => {
          state.filters[group] = input.checked
            ? [...state.filters[group], value]
            : state.filters[group].filter((item) => item !== value);
          state.page = 1;
          state.workspaceMode = "filter";
          render();
          renderFilterPanel();
          const replacement = [...filterPanel.querySelectorAll("[data-filter-group]")]
            .find((candidate) => candidate.dataset.filterGroup === group
              && candidate.dataset.filterValue === value);
          (replacement || filterPanel.querySelector("[data-filter-option-search]"))?.focus();
        });
        label.append(input, document.createTextNode(shown));
        section.append(label);
      }
      filterPanel.append(section);
    }
    applyOptionQuery();
    const applyButton = document.createElement("button");
    applyButton.type = "button";
    applyButton.className = "filter-apply-mobile button button--ink";
    applyButton.dataset.filterClose = "true";
    applyButton.textContent = `显示 ${archive.applyPipeline(records, state).total} 部`;
    applyButton.addEventListener("click", closeFilterAndRestoreFocus);
    filterPanel.append(applyButton);
    filterPanel.querySelector("[data-filter-close]")?.addEventListener(
      "click",
      closeFilterAndRestoreFocus,
    );
  }

  async function load() {
    bindControls();
    rows.forEach((row) => {
      const image = row.querySelector(".subject-row__cover img");
      image?.addEventListener("error", () => {
        image.remove();
        const cover = row.querySelector(".subject-row__cover");
        cover?.classList.add("subject-row__cover--missing");
        if (cover) cover.innerHTML = "<span>ARCHIVE</span>";
      }, { once: true });
    });
    renderScope({ all: [], page: [], pageCount: 1, page: 1, total: 0 });
    try {
      const response = await fetch(quarterRoot.dataset.dataUrl, { credentials: "same-origin" });
      if (!response.ok) throw new Error("data unavailable");
      payload = await response.json();
      records = archive.recordsFromQuarter(payload);
      recordByKey = new Map(records.map((record) => [record.key, record]));
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

(() => {
  "use strict";

  const archive = window.BsbArchive;
  if (!archive) return;
  const root = document.querySelector('[data-page="archive"][data-archive-app]');
  if (!root) return;

  const selectors = {
    quarter: root.querySelector("[data-archive-quarter-selector]"),
    year: root.querySelector("[data-archive-year-selector]"),
    range: root.querySelector("[data-archive-range-selector]"),
    browser: root.querySelector("[data-archive-browser]"),
    list: root.querySelector("[data-list-sections]"),
    scopeLabel: root.querySelector("[data-archive-scope-label]"),
    search: root.querySelector("[data-search]"),
    summary: root.querySelector("[data-results-summary]"),
    noResults: root.querySelector("[data-no-results]"),
    activeFilters: root.querySelector("[data-active-filters]"),
    pager: root.querySelector("[data-pager]"),
    scopePanel: root.querySelector("[data-scope-panel]"),
    detailPanel: root.querySelector("[data-detail-panel]"),
    filterPanel: root.querySelector("[data-filter-panel]"),
    sortPopover: root.querySelector("[data-sort-popover]"),
  };
  const state = archive.createState({ scope: { kind: "year", value: "" } });
  let index = null;
  let records = [];
  let rows = [];
  let recordByKey = new Map();
  const detailByQuarter = new Map();
  let detailRequest = 0;
  let loadError = false;

  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  function setScopeUrl(kind, value, replace = false) {
    const url = new URL(window.location.href);
    url.search = "";
    if (kind === "year") url.searchParams.set("year", value);
    if (kind === "range") {
      url.searchParams.set("from", String(value.from));
      url.searchParams.set("to", String(value.to));
    }
    url.hash = "";
    if (replace) window.history.replaceState({}, "", url);
    else window.history.pushState({}, "", url);
  }

  function scopeFromLocation() {
    const params = new URLSearchParams(window.location.search);
    const year = params.get("year");
    if (year && (index?.years || []).map(String).includes(year)) {
      return { kind: "year", value: year };
    }
    const range = normalRange(params.get("from"), params.get("to"));
    if (params.has("from") && params.has("to") && range) {
      return { kind: "range", value: range };
    }
    const latest = String(index?.latest_quarter || "").slice(0, 4)
      || String(Math.max(...(index?.years || [0])));
    return { kind: "year", value: latest };
  }

  function sameScope(left, right) {
    if (left.kind !== right.kind) return false;
    if (left.kind === "range") {
      return left.value?.from === right.value.from && left.value?.to === right.value.to;
    }
    return String(left.value) === String(right.value);
  }

  async function restoreArchiveLocation() {
    if (!index) return;
    const location = scopeFromLocation();
    if (sameScope(state.scope, location) && records.length) {
      await openHash();
      return;
    }
    await setScope(location.kind, location.value, false);
  }

  function setTab(kind) {
    root.querySelectorAll("[data-scope-choice]").forEach((button) => {
      const selected = button.dataset.scopeChoice === kind;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    for (const [name, node] of Object.entries(selectors)) {
      if (!["quarter", "year", "range", "browser"].includes(name) || !node) continue;
      if (name === "browser") node.hidden = kind === "quarter";
      else node.hidden = name !== kind;
    }
  }

  function renderQuarterSelector() {
    if (!selectors.quarter || !index) return;
    const entries = new Map((index.quarters || []).map((item) => [item.quarter, item]));
    const years = [...new Set((index.quarters || []).map((item) => Number(item.year)))].sort((a, b) => b - a);
    const grid = document.createElement("div");
    grid.className = "archive-quarter-grid";
    years.forEach((year, yearIndex) => {
      const row = document.createElement("div");
      row.className = `archive-year-row${yearIndex > 4 ? " is-older" : ""}`;
      const label = document.createElement("span");
      label.className = "archive-year-row__label";
      label.textContent = String(year);
      const slots = document.createElement("div");
      slots.className = "archive-quarter-slots";
      [1, 4, 7, 10].forEach((month) => {
        const quarter = `${year}-${String(month).padStart(2, "0")}`;
        const entry = entries.get(quarter);
        const link = document.createElement("a");
        link.className = `archive-quarter-slot${entry ? "" : " is-disabled"}`;
        link.textContent = String(month).padStart(2, "0");
        if (entry) {
          link.href = `../${quarter}/index.html`;
          link.dataset.quarter = quarter;
          link.title = `${quarter} · ${entry.count} 部`;
        } else {
          link.setAttribute("aria-disabled", "true");
          link.tabIndex = -1;
        }
        slots.append(link);
      });
      row.append(label, slots);
      grid.append(row);
    });
    selectors.quarter.replaceChildren(grid);
    if (!years.length) selectors.quarter.innerHTML = '<p class="empty-state">Archive 为空。</p>';
  }

  function renderYearSelector() {
    const select = root.querySelector("[data-archive-year-select]");
    if (!select || !index) return;
    select.replaceChildren(...(index.years || []).slice().sort((a, b) => b - a).map((year) => {
      const option = document.createElement("option");
      option.value = String(year);
      option.textContent = String(year);
      option.selected = Number(state.scope.value) === Number(year);
      return option;
    }));
  }

  function normalRange(from, to) {
    const values = [Number(from), Number(to)].filter((value) => Number.isFinite(value));
    if (!values.length) return null;
    return { from: Math.min(...values), to: Math.max(...values) };
  }

  async function loadCatalogs(years) {
    if (!years.length) return [];
    const responses = await Promise.all(years.map(async (year) => {
      const response = await fetch(`../data/catalog/${year}.json`, { credentials: "same-origin" });
      if (!response.ok) throw new Error("catalog unavailable");
      return response.json();
    }));
    return responses.flatMap((payload) => archive.recordsFromCatalog(payload));
  }

  async function loadDetailRecord(record) {
    let quarterRecords = detailByQuarter.get(record.quarter);
    if (!quarterRecords) {
      const response = await fetch(`../data/quarters/${record.quarter}.json`, {
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("quarter detail unavailable");
      quarterRecords = archive.recordsFromQuarter(await response.json());
      detailByQuarter.set(record.quarter, quarterRecords);
    }
    const detail = quarterRecords.find((item) => item.key === record.key);
    if (!detail) throw new Error("quarter detail record unavailable");
    return detail;
  }

  function yearsForScope(kind, value) {
    const available = (index?.years || []).map(Number);
    if (kind === "year") return available.includes(Number(value)) ? [Number(value)] : [];
    if (kind === "range") return available.filter((year) => year >= value.from && year <= value.to);
    return [];
  }

  function createRow(record, sequence) {
    const article = document.createElement("article");
    article.className = "subject-row";
    article.setAttribute("role", "listitem");
    article.dataset.subjectId = String(record.id);
    article.dataset.recordKey = record.key;
    article.dataset.media = record.media.toLowerCase();
    article.dataset.appearance = record.appearance;
    article.dataset.searchText = record.search;
    article.dataset.source = record.source || "";
    article.dataset.tags = record.allowed_tags.join("|");
    article.dataset.quarter = record.quarter || "";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "subject-row__open";
    button.dataset.openSubject = "true";
    button.setAttribute("aria-label", `打开 ${record.preferred_title}`);
    button.setAttribute("aria-controls", "detail-panel");
    button.setAttribute("aria-expanded", "false");
    const number = document.createElement("span");
    number.className = "subject-row__sequence";
    number.setAttribute("aria-hidden", "true");
    number.textContent = String(sequence).padStart(3, "0");
    button.append(number);
    const cover = document.createElement("span");
    cover.className = "subject-row__cover";
    const coverPath = record.cover ? String(record.cover) : "";
    if (coverPath) {
      const image = document.createElement("img");
      image.width = 52;
      image.height = 74;
      image.loading = sequence <= 10 ? "eager" : "lazy";
      image.src = `../${coverPath}`;
      image.alt = "";
      image.addEventListener("error", () => {
        image.remove();
        cover.classList.add("subject-row__cover--missing");
        cover.innerHTML = "<span>ARCHIVE</span>";
      }, { once: true });
      cover.append(image);
    } else {
      cover.classList.add("subject-row__cover--missing");
      cover.innerHTML = "<span>ARCHIVE</span>";
    }
    button.append(cover);
    const content = document.createElement("span");
    content.className = "subject-row__content";
    const title = document.createElement("strong");
    title.className = "subject-row__title";
    title.textContent = record.preferred_title || "—";
    content.append(title);
    const original = document.createElement("span");
    original.className = "subject-row__original";
    original.textContent = record.original_title || "";
    content.append(original);
    const metadata = document.createElement("span");
    metadata.className = "subject-row__meta";
    metadata.textContent = [record.media, record.episode_count ? `${record.episode_count}话` : "", record.air_date || "", archive.sourceLabel(record.source), record.quarter || ""]
      .filter(Boolean).join(" · ");
    content.append(metadata);
    const tagList = document.createElement("span");
    tagList.className = "subject-row__tags";
    record.allowed_tags.slice(0, 2).forEach((tag) => {
      const span = document.createElement("span");
      span.className = "tag";
      span.textContent = tag;
      tagList.append(span);
    });
    content.append(tagList);
    button.append(content);
    const score = document.createElement("span");
    score.className = "subject-row__score";
    const scoreValue = document.createElement("b");
    scoreValue.textContent = record.score === null || record.score === undefined ? "—" : Number(record.score).toFixed(1);
    const count = document.createElement("small");
    count.textContent = record.rating_count === null || record.rating_count === undefined ? "—" : String(record.rating_count);
    score.append(scoreValue, count);
    button.append(score);
    article.append(button);
    button.addEventListener("click", () => { void selectRecord(record); });
    return article;
  }

  function buildLists(result) {
    if (!selectors.list) return;
    selectors.list.replaceChildren();
    const positions = new Map(result.all.map((record, index) => [record.key, index + 1]));
    const groups = [
      ["tv", "premiere", "本季度新番"],
      ["tv", "continuing", "跨季度续播"],
      ["movie", "premiere", "剧场版"],
    ];
    groups.forEach(([media, appearance, title]) => {
      const allValues = result.all.filter((record) =>
        record.media === (media === "movie" ? "MOVIE" : "TV") && record.appearance === appearance);
      const pageValues = result.pageRecords.filter((record) =>
        record.media === (media === "movie" ? "MOVIE" : "TV") && record.appearance === appearance);
      const section = document.createElement("section");
      section.className = "result-section";
      section.dataset.listSection = media;
      section.dataset.appearanceSection = appearance;
      const header = document.createElement("header");
      header.className = "result-section__header";
      const code = document.createElement("p");
      code.className = "result-section__code";
      code.textContent = `${media.toUpperCase()} / ${appearance.toUpperCase()}`;
      const heading = document.createElement("h2");
      heading.textContent = title;
      const counter = document.createElement("span");
      counter.dataset.sectionCount = "true";
      counter.textContent = String(allValues.length).padStart(2, "0");
      header.append(code, heading, counter);
      const list = document.createElement("div");
      list.className = "result-list";
      pageValues.forEach((record) => {
        const row = createRow(record, positions.get(record.key) || 0);
        list.append(row);
      });
      section.append(header, list);
      section.hidden = state.media !== media || pageValues.length === 0;
      selectors.list.append(section);
    });
    rows = [...selectors.list.querySelectorAll(".subject-row")];
  }

  function scopeCounts() {
    return archive.scopeCounts(records);
  }

  function renderScope(result) {
    if (!selectors.scopePanel) return;
    const counts = scopeCounts();
    const label = state.scope.kind === "range"
      ? `${state.scope.value.from}—${state.scope.value.to}`
      : state.scope.value;
    selectors.scopePanel.innerHTML = `<p class="workspace-panel__code">ARCHIVE SCOPE</p><h2>${esc(label)}</h2>
      <p class="workspace-panel__lead">范围内每个 quarter appearance 都保留其原始坐标。</p>
      <dl class="scope-facts"><div><dt>TV</dt><dd>${counts.tv}</dd></div><div><dt>MOVIE</dt><dd>${counts.movie}</dd></div>
      <div><dt>PREMIERE</dt><dd>${counts.premiere}</dd></div><div><dt>CONTINUING</dt><dd>${counts.continuing}</dd></div></dl>
      <p class="workspace-panel__summary">当前结果 ${result.total} / ${records.length} 部 appearance。</p>`;
  }

  function clearSelection(removeHash = false) {
    detailRequest += 1;
    state.selectedSubjectId = null;
    state.selectedOccurrence = null;
    state.workspaceMode = "scope";
    if (removeHash) {
      const url = new URL(window.location.href);
      url.hash = "";
      window.history.replaceState({}, "", url);
    }
  }

  function closeFilter() {
    const result = archive.applyPipeline(records, state);
    const selected = archive.selectedRecord(records, state.selectedSubjectId, state.selectedOccurrence);
    if (selected && result.all.some((record) => record.key === selected.key)) {
      state.workspaceMode = "detail";
    } else {
      clearSelection(true);
    }
  }

  function focusRecordTrigger(recordKey) {
    rows.find((row) => row.dataset.recordKey === recordKey)
      ?.querySelector("[data-open-subject]")?.focus();
  }

  function closeFilterAndRestoreFocus() {
    closeFilter();
    render();
    root.querySelector("[data-filter-toggle]")?.focus();
  }

  function renderRows(result) {
    buildLists(result);
    rows.forEach((row) => {
      const record = recordByKey.get(row.dataset.recordKey);
      row.classList.toggle("is-selected", Boolean(record && record.key === state.selectedOccurrence));
      const button = row.querySelector("[data-open-subject]");
      if (button) button.setAttribute("aria-expanded", String(record && record.key === state.selectedOccurrence));
    });
    if (selectors.summary) selectors.summary.textContent = `${result.total} / ${records.filter((record) => record.media === (state.media === "movie" ? "MOVIE" : "TV")).length} 部 appearance · 第 ${result.page} / ${result.pageCount} 页`;
    if (selectors.noResults) selectors.noResults.hidden = result.total !== 0;
  }

  function renderPager(result) {
    if (!selectors.pager) return;
    selectors.pager.replaceChildren();
    selectors.pager.hidden = result.pageCount <= 1;
    if (result.pageCount <= 1) return;
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
        root.querySelector(".master-pane")?.scrollIntoView({ block: "start" });
      });
      selectors.pager.append(button);
    };
    add("上一页", result.page - 1, result.page <= 1);
    for (const token of archive.paginationTokens(result.page, result.pageCount)) {
      if (token === "ellipsis") {
        const ellipsis = document.createElement("span");
        ellipsis.textContent = "…";
        ellipsis.dataset.ellipsis = "true";
        ellipsis.setAttribute("aria-hidden", "true");
        selectors.pager.append(ellipsis);
      } else {
        add(String(token).padStart(2, "0"), token, false, token === result.page);
      }
    }
    add("下一页", result.page + 1, result.page >= result.pageCount);
  }

  function renderActiveFilters() {
    if (!selectors.activeFilters) return;
    selectors.activeFilters.replaceChildren();
    const values = [
      ...(state.query ? [{ type: "query", value: state.query, label: `搜索：${state.query}` }] : []),
      ...state.filters.sources.map((value) => ({ type: "sources", value, label: `来源：${archive.sourceLabel(value)}` })),
      ...state.filters.tags.map((value) => ({ type: "tags", value, label: `标签：${value}` })),
      ...state.filters.sections.map((value) => ({ type: "sections", value, label: `分区：${archive.appearanceLabel(value)}` })),
    ];
    selectors.activeFilters.hidden = values.length === 0;
    values.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "active-filter";
      button.textContent = `${item.label} ×`;
      button.addEventListener("click", () => {
        if (item.type === "query") {
          state.query = "";
          if (selectors.search) selectors.search.value = "";
        } else state.filters[item.type] = state.filters[item.type].filter((value) => value !== item.value);
        state.page = 1;
        clearSelection(true);
        render();
      });
      selectors.activeFilters.append(button);
    });
  }

  function render() {
    if (loadError) return;
    const result = archive.applyPipeline(records, state);
    state.page = result.page;
    renderRows(result);
    renderScope(result);
    renderActiveFilters();
    renderPager(result);
    if (selectors.scopePanel) selectors.scopePanel.hidden = state.workspaceMode !== "scope";
    if (selectors.detailPanel) selectors.detailPanel.hidden = state.workspaceMode !== "detail";
    if (selectors.filterPanel) selectors.filterPanel.hidden = state.workspaceMode !== "filter";
    root.dataset.workspaceMode = state.workspaceMode;
    const sortButton = root.querySelector("[data-sort-toggle]");
    if (sortButton) sortButton.textContent = archive.SORTS[state.sort];
    root.querySelectorAll("[data-media-mode]").forEach((button) => {
      button.setAttribute("aria-selected", String(button.dataset.mediaMode === state.media));
    });
    const filterButton = root.querySelector("[data-filter-toggle]");
    if (filterButton) {
      filterButton.querySelector("[data-filter-count]").textContent = filterCount() ? `(${filterCount()})` : "";
      filterButton.setAttribute("aria-expanded", String(state.workspaceMode === "filter"));
    }
    if (sortButton) sortButton.setAttribute("aria-expanded", String(selectors.sortPopover?.hidden === false));
  }

  function filterCount() {
    return state.filters.sources.length + state.filters.tags.length + state.filters.sections.length;
  }

  function detailHtml(record) {
    const aliases = record.aliases || [];
    const cover = record.cover || record.cover_url;
    const coverHtml = cover
      ? `<button type="button" class="detail-cover-button" data-lightbox aria-label="查看封面"><img src="../${esc(String(cover))}" alt="${esc(record.preferred_title)}" width="180" height="270"></button>`
      : `<div class="detail-cover detail-cover--missing"><span>ARCHIVE</span></div>`;
    const tags = (record.allowed_tags || []).map((tag) => `<span class="tag">${esc(tag)}</span>`).join("");
    const summary = record.display_summary || "";
    const facts = [
      record.quarter ? ["当前季度", record.quarter] : null,
      record.premiere_quarter ? ["首播季度", record.premiere_quarter] : null,
      record.episode_count !== null && record.episode_count !== undefined ? ["集数", record.episode_count] : null,
      record.air_date ? ["播出日期", record.air_date] : null,
      record.end_date ? ["结束日期", record.end_date] : null,
      ["评分", record.score ?? "—", "detail-score"],
      record.rating_count !== null && record.rating_count !== undefined ? ["评分人数", record.rating_count] : null,
      ["来源", archive.sourceLabel(record.source)],
    ].filter(Boolean).map(([label, value, className]) => `<div><dt>${label}</dt><dd${className ? ` class="${className}"` : ""}>${esc(value)}</dd></div>`).join("");
    return `<div class="detail-head"><button type="button" class="detail-close" data-detail-close aria-label="关闭详情">×</button><p class="workspace-panel__code">${esc(record.media)} / ${esc(record.appearance)}</p>
      <div class="detail-hero">${coverHtml}<div><h2>${esc(record.preferred_title)}</h2>${record.original_title ? `<p class="detail-original">${esc(record.original_title)}</p>` : ""}<p class="detail-id">SUBJECT / ${esc(record.id)}</p></div></div></div>
      <dl class="detail-facts">${facts}</dl>
      ${record.appearance === "continuing" ? `<p class="detail-continuing">当前归档：续播${record.premiere_quarter ? ` · 首播 ${esc(record.premiere_quarter)}` : ""}</p>` : ""}
      ${aliases.length ? `<section class="detail-section"><h3>别名</h3><div class="detail-tags">${aliases.map((alias) => `<span class="tag">${esc(alias)}</span>`).join("")}</div></section>` : ""}
      ${tags ? `<section class="detail-section"><h3>标签</h3><div class="detail-tags">${tags}</div></section>` : ""}
      ${summary ? `<section class="detail-section detail-summary"><h3>简介</h3><p>${esc(summary).replaceAll("\n", "<br>")}</p></section>` : ""}
      <p class="detail-footer"><a class="text-link" href="${esc(record.bangumi_url || ("https://" + "bgm.tv/subject/" + record.id))}" target="_blank" rel="noreferrer">在 Bangumi 查看 ↗</a></p>`;
  }

  function openLightbox(record) {
    const cover = record.cover || record.cover_url;
    if (!cover) return;
    const dialog = document.createElement("dialog");
    dialog.className = "cover-lightbox";
    dialog.innerHTML = `<button type="button" class="lightbox-close" aria-label="关闭封面">×</button><img src="../${esc(String(cover))}" alt="${esc(record.preferred_title)}">`;
    document.body.append(dialog);
    const close = () => { dialog.close(); dialog.remove(); };
    dialog.querySelector("button").addEventListener("click", close);
    dialog.addEventListener("click", (event) => { if (event.target === dialog) close(); });
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    dialog.showModal();
  }

  async function selectRecord(record, replace = false) {
    if (!record) return;
    const request = ++detailRequest;
    const hadSelection = state.selectedOccurrence !== null;
    state.selectedSubjectId = record.id;
    state.selectedOccurrence = record.key;
    state.workspaceMode = "detail";
    const url = new URL(window.location.href);
    url.hash = `#bgm-${record.id}`;
    if (hadSelection) window.history.replaceState({}, "", url);
    else if (!replace) window.history.pushState({}, "", url);
    else window.history.replaceState({}, "", url);
    if (selectors.detailPanel) {
      selectors.detailPanel.innerHTML = '<p class="workspace-panel__code">DETAIL</p><p class="loading-state">正在读取季度详情…</p>';
      selectors.detailPanel.scrollTop = 0;
    }
    render();
    try {
      const detail = await loadDetailRecord(record);
      if (request !== detailRequest || state.selectedOccurrence !== record.key) return;
      if (selectors.detailPanel) {
        selectors.detailPanel.innerHTML = detailHtml(detail);
        selectors.detailPanel.querySelector("[data-detail-close]")?.addEventListener("click", () => {
          clearSelection(true);
          render();
          focusRecordTrigger(record.key);
        });
        selectors.detailPanel.querySelector("[data-lightbox]")?.addEventListener("click", () => openLightbox(detail));
      }
    } catch {
      if (request !== detailRequest || state.selectedOccurrence !== record.key) return;
      if (selectors.detailPanel) {
        selectors.detailPanel.innerHTML = '<p class="workspace-panel__code">DATA UNAVAILABLE</p><h2>当前资料详情未完整生成</h2><p>建议重新 build。</p>';
      }
    }
  }

  async function openHash() {
    const match = window.location.hash.match(/^#bgm-(\d+)$/);
    if (!match || !records.length) {
      if (!match && state.selectedOccurrence !== null) {
        clearSelection(false);
        render();
      }
      return;
    }
    const candidate = archive.selectedRecord(records, Number(match[1]));
    if (!candidate) {
      const url = new URL(window.location.href);
      url.hash = "";
      window.history.replaceState({}, "", url);
      return;
    }
    state.media = candidate.media === "MOVIE" ? "movie" : "tv";
    archive.normalizeFiltersForMedia(state, records);
    const result = archive.applyPipeline(records, state);
    const position = result.all.findIndex((record) => record.key === candidate.key);
    if (position >= 0) state.page = Math.floor(position / state.pageSize) + 1;
    await selectRecord(candidate, true);
  }

  function renderFilterPanel() {
    if (!selectors.filterPanel) return;
    const options = archive.availableFilterValues(records, state.media);
    selectors.filterPanel.innerHTML = `<div class="filter-panel__head"><p class="workspace-panel__code">FILTER WORKSPACE</p><button type="button" class="detail-close" data-filter-close aria-label="关闭筛选">×</button></div><h2>筛选资料</h2><label class="filter-option-search"><span class="sr-only">搜索筛选选项</span><input type="search" data-filter-option-search placeholder="搜索选项名称"></label>`;
    const optionSearch = selectors.filterPanel.querySelector("[data-filter-option-search]");
    if (optionSearch) optionSearch.value = state.filterOptionQuery;
    const applyOptionQuery = () => {
      const query = archive.normalize(state.filterOptionQuery);
      selectors.filterPanel.querySelectorAll("[data-filter-option]").forEach((option) => {
        option.hidden = option.dataset.filterOption?.includes(query) === false;
      });
    };
    optionSearch?.addEventListener("input", () => {
      state.filterOptionQuery = optionSearch.value;
      applyOptionQuery();
    });
    Object.entries(options).forEach(([group, values]) => {
      if (values.length <= 1) return;
      const fieldset = document.createElement("fieldset");
      fieldset.className = "filter-group";
      const legend = document.createElement("legend");
      legend.textContent = group === "sources" ? "来源" : group === "tags" ? "标签" : "TV 分区";
      fieldset.append(legend);
      values.forEach((value) => {
        const label = document.createElement("label");
        label.className = "filter-option";
        const shown = group === "sections"
          ? archive.appearanceLabel(value)
          : group === "sources"
            ? archive.sourceLabel(value)
            : value;
        label.dataset.filterOption = archive.normalize(shown);
        const input = document.createElement("input");
        input.type = "checkbox";
        input.dataset.filterGroup = group;
        input.dataset.filterValue = value;
        input.checked = state.filters[group].includes(value);
        input.addEventListener("change", () => {
          state.filters[group] = input.checked ? [...state.filters[group], value] : state.filters[group].filter((item) => item !== value);
          state.page = 1;
          state.workspaceMode = "filter";
          render();
          renderFilterPanel();
          const replacement = [...selectors.filterPanel.querySelectorAll("[data-filter-group]")]
            .find((candidate) => candidate.dataset.filterGroup === group
              && candidate.dataset.filterValue === value);
          (replacement || selectors.filterPanel.querySelector("[data-filter-option-search]"))?.focus();
        });
        label.append(input, document.createTextNode(shown));
        fieldset.append(label);
      });
      selectors.filterPanel.append(fieldset);
    });
    applyOptionQuery();
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "filter-apply-mobile button button--ink";
    apply.textContent = `显示 ${archive.applyPipeline(records, state).total} 部`;
    apply.addEventListener("click", closeFilterAndRestoreFocus);
    selectors.filterPanel.append(apply);
    selectors.filterPanel.querySelector("[data-filter-close]")?.addEventListener(
      "click",
      closeFilterAndRestoreFocus,
    );
  }

  function bindControls() {
    const pageSize = root.querySelector("[data-page-size]");
    if (pageSize) {
      pageSize.replaceChildren(...archive.PAGE_SIZES.map((size) => {
        const option = document.createElement("option");
        option.value = String(size);
        option.textContent = String(size);
        option.selected = size === state.pageSize;
        return option;
      }));
      pageSize.addEventListener("change", () => { state.pageSize = archive.writePageSize(pageSize.value); state.page = 1; clearSelection(true); render(); });
    }
    selectors.search?.addEventListener("input", () => { state.query = selectors.search.value; state.page = 1; clearSelection(true); render(); });
    root.querySelectorAll("[data-media-mode]").forEach((button) => button.addEventListener("click", () => { state.media = button.dataset.mediaMode === "movie" ? "movie" : "tv"; archive.normalizeFiltersForMedia(state, records); state.page = 1; clearSelection(true); renderFilterPanel(); render(); }));
    root.querySelector("[data-filter-toggle]")?.addEventListener("click", () => { if (state.workspaceMode === "filter") closeFilter(); else state.workspaceMode = "filter"; renderFilterPanel(); render(); });
    root.querySelector("[data-sort-toggle]")?.addEventListener("click", () => {
      if (!selectors.sortPopover) return;
      selectors.sortPopover.hidden = !selectors.sortPopover.hidden;
      if (!selectors.sortPopover.hidden) {
        selectors.sortPopover.replaceChildren(...Object.entries(archive.SORTS).map(([value, label]) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = label;
          button.setAttribute("aria-pressed", String(value === state.sort));
          button.addEventListener("click", () => { state.sort = value; state.page = 1; selectors.sortPopover.hidden = true; clearSelection(true); render(); });
          return button;
        }));
      }
    });
    root.querySelector("[data-clear-all]")?.addEventListener("click", () => { state.query = ""; state.filterOptionQuery = ""; state.filters = { sources: [], tags: [], sections: [] }; if (selectors.search) selectors.search.value = ""; state.page = 1; clearSelection(true); render(); });
    root.querySelectorAll("[data-scope-choice]").forEach((button) => button.addEventListener("click", () => {
      const kind = button.dataset.scopeChoice;
      setTab(kind);
      if (kind === "quarter") { state.scope = { kind: "quarter", value: "" }; selectors.browser.hidden = true; return; }
      if (kind === "year") {
        const value = root.querySelector("[data-archive-year-select]")?.value || String(index?.latest_quarter || "").slice(0, 4);
        setScope("year", value);
      }
      if (kind === "range") {
        const fallback = Number(
          state.scope.kind === "year"
            ? state.scope.value
            : String(index?.latest_quarter || "").slice(0, 4)
        );
        const from = root.querySelector("[data-archive-from]");
        const to = root.querySelector("[data-archive-to]");
        if (from && !from.value) from.value = String(fallback);
        if (to && !to.value) to.value = String(fallback);
      }
    }));
    root.querySelector("[data-archive-year-select]")?.addEventListener("change", (event) => setScope("year", event.target.value));
    root.querySelector("[data-range-apply]")?.addEventListener("click", () => {
      const range = normalRange(root.querySelector("[data-archive-from]")?.value, root.querySelector("[data-archive-to]")?.value);
      if (range) setScope("range", range);
    });
    root.querySelectorAll("[data-range-shortcut]").forEach((button) => button.addEventListener("click", () => {
      if (!index?.years?.length) return;
      const newest = Math.max(...index.years.map(Number));
      const value = button.dataset.rangeShortcut === "all" ? { from: Math.min(...index.years), to: newest } : { from: newest - Number(button.dataset.rangeShortcut) + 1, to: newest };
      root.querySelector("[data-archive-from]").value = String(value.from);
      root.querySelector("[data-archive-to]").value = String(value.to);
      setScope("range", value);
    }));
    window.addEventListener("popstate", () => { void restoreArchiveLocation(); });
    window.addEventListener("hashchange", openHash);
  }

  async function setScope(kind, value, push = true) {
    if (!index) return;
    const normalized = kind === "range" ? normalRange(value.from, value.to) : String(value);
    if (kind === "range" && !normalized) return;
    state.scope = { kind, value: normalized };
    state.page = 1;
    state.query = "";
    state.filters = { sources: [], tags: [], sections: [] };
    if (selectors.search) selectors.search.value = "";
    clearSelection(false);
    if (push) setScopeUrl(kind, normalized);
    setTab(kind);
    renderYearSelector();
    if (kind === "range") {
      const from = root.querySelector("[data-archive-from]");
      const to = root.querySelector("[data-archive-to]");
      if (from) from.value = String(normalized.from);
      if (to) to.value = String(normalized.to);
    }
    if (kind === "quarter") return;
    loadError = false;
    try {
      records = await loadCatalogs(yearsForScope(kind, normalized));
      recordByKey = new Map(records.map((record) => [record.key, record]));
      renderFilterPanel();
      if (selectors.scopeLabel) selectors.scopeLabel.textContent = kind === "range" ? `RANGE / ${normalized.from}—${normalized.to}` : `YEAR / ${normalized}`;
      render();
      await openHash();
    } catch {
      loadError = true;
      if (selectors.scopePanel) selectors.scopePanel.innerHTML = '<p class="workspace-panel__code">DATA UNAVAILABLE</p><h2>页面资料未完整生成</h2><p>建议重新 build。</p>';
    }
  }

  async function load() {
    try {
      const response = await fetch(root.dataset.archiveIndexUrl, { credentials: "same-origin" });
      if (!response.ok) throw new Error("archive index unavailable");
      index = await response.json();
      renderQuarterSelector();
      renderYearSelector();
      bindControls();
      await restoreArchiveLocation();
    } catch {
      loadError = true;
      setTab("quarter");
      if (selectors.quarter) selectors.quarter.innerHTML = '<p class="empty-state">DATA UNAVAILABLE · 页面资料未完整生成</p>';
    }
  }

  load();
})();
