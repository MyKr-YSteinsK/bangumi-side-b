/* Native, offline state helpers shared by quarter and Archive views. */
(() => {
  "use strict";

  const PAGE_SIZE_KEY = "bsb-archive-page-size";
  const VIEW_MODE_KEY = "bsb-browse-view-mode";
  const PAGE_SIZES = Object.freeze([20, 40, 60, 100]);
  const VIEW_MODES = Object.freeze(["grid", "list"]);
  const SORTS = Object.freeze({
    "score-desc": "评分：高到低",
    "score-asc": "评分：低到高",
    "rating-count-desc": "评分人数：多到少",
    "rating-count-asc": "评分人数：少到多",
  });
  let activeBrowseTransition = null;

  function normalize(value) {
    return String(value ?? "")
      .normalize("NFKC")
      .trim()
      .toLocaleLowerCase();
  }

  function formatRating(value) {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(1) : "—";
  }

  function hasEpisodeCount(value) {
    return typeof value === "number" && Number.isInteger(value) && value > 0;
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

  function readViewMode(storage = window.localStorage) {
    try {
      const value = storage.getItem(VIEW_MODE_KEY);
      return VIEW_MODES.includes(value) ? value : "grid";
    } catch {
      return "grid";
    }
  }

  function writeViewMode(value, storage = window.localStorage) {
    const mode = VIEW_MODES.includes(value) ? value : "grid";
    try {
      storage.setItem(VIEW_MODE_KEY, mode);
    } catch {
      // Private browsing can reject localStorage; the in-memory state still works.
    }
    return mode;
  }

  function createState(overrides = {}) {
    const state = {
      media: "tv",
      scope: { kind: "quarter", value: "" },
      query: "",
      filterOptionQuery: "",
      filters: { sources: [], tags: [], sections: [] },
      draftFilters: null,
      sort: "score-desc",
      page: 1,
      pageSize: readPageSize(),
      viewMode: readViewMode(),
      selectedSubjectId: null,
      selectedOccurrence: null,
      workspaceMode: "scope",
      listScrollTop: 0,
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
    state.viewMode = VIEW_MODES.includes(state.viewMode) ? state.viewMode : "grid";
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

  function positionPopover(popover, anchor) {
    if (!popover || !anchor) return;
    const anchorBox = anchor.getBoundingClientRect();
    const viewportPadding = 8;
    const width = Math.min(
      Math.max(popover.offsetWidth, 12 * parseFloat(getComputedStyle(document.documentElement).fontSize || "16")),
      Math.max(0, window.innerWidth - viewportPadding * 2),
    );
    const left = Math.max(
      viewportPadding,
      Math.min(anchorBox.right - width, window.innerWidth - width - viewportPadding),
    );
    const below = anchorBox.bottom + 6;
    const top = below + popover.offsetHeight <= window.innerHeight - viewportPadding
      ? below
      : Math.max(viewportPadding, anchorBox.top - popover.offsetHeight - 6);
    popover.style.left = `${Math.round(left)}px`;
    popover.style.top = `${Math.round(top)}px`;
    popover.style.width = `${Math.round(width)}px`;
  }

  function clearPopoverPosition(popover) {
    if (!popover) return;
    popover.style.removeProperty("left");
    popover.style.removeProperty("top");
    popover.style.removeProperty("width");
  }

  function prefersReducedMotion() {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
  }

  function withBrowseTransition(reason, mutate) {
    void reason;
    if (typeof mutate !== "function") return undefined;
    let mutated = false;
    const runMutation = () => {
      if (mutated) return undefined;
      mutated = true;
      return mutate();
    };
    if (typeof document.startViewTransition !== "function" || prefersReducedMotion()) {
      return runMutation();
    }
    if (activeBrowseTransition) {
      try {
        activeBrowseTransition.skipTransition?.();
      } finally {
        activeBrowseTransition = null;
      }
    }
    let transition;
    try {
      transition = document.startViewTransition(() => runMutation());
    } catch (error) {
      if (mutated) throw error;
      // An API-level failure happens before the update callback.  The
      // mutation remains the authoritative synchronous fallback.
      return runMutation();
    }
    if (!transition) {
      return mutated ? transition : runMutation();
    }
    activeBrowseTransition = transition;
    const clearActive = () => {
      if (activeBrowseTransition === transition) activeBrowseTransition = null;
    };
    if (transition.finished && typeof transition.finished.then === "function") {
      transition.finished.then(clearActive, clearActive);
    }
    return transition;
  }

  function subjectTransitionName(record) {
    const token = String(record?.key || recordKey(record))
      .replace(/[^a-zA-Z0-9_-]/g, "-");
    return `subject-${token}`;
  }

  function playEntranceStagger(root, limit = 10) {
    if (!root || prefersReducedMotion()) return;
    const count = Math.min(Math.max(Number(limit) || 10, 0), 10);
    [...root.querySelectorAll(".subject-row:not([hidden])")]
      .slice(0, count)
      .forEach((row, index) => {
        row.style.setProperty("--stagger-index", String(index));
        row.classList.add("is-entering");
        const animationTarget = row.querySelector(".subject-row__open") || row;
        animationTarget.addEventListener(
          "animationend",
          () => row.classList.remove("is-entering"),
          { once: true },
        );
      });
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

  function filterOptionMetadata(records, state) {
    const available = availableFilterValues(records, state.media);
    const groups = ["sources", "tags", "sections"];
    const metadata = {};
    for (const group of groups) {
      const filters = {
        sources: [...(state.filters?.sources || [])],
        tags: [...(state.filters?.tags || [])],
        sections: [...(state.filters?.sections || [])],
      };
      filters[group] = [];
      const query = normalize(state.query);
      const candidates = recordsForMedia(records, state.media).filter((record) => {
        if (query && !String(record.search || "").includes(query)) return false;
        return matchesFilters(record, filters);
      });
      const counts = new Map(available[group].map((value) => [value, 0]));
      candidates.forEach((record) => {
        const values = group === "tags"
          ? record.allowed_tags
          : [group === "sections" ? record.appearance : record.source];
        new Set(values).forEach((value) => {
          if (counts.has(value)) counts.set(value, counts.get(value) + 1);
        });
      });
      metadata[group] = available[group].map((value) => ({
        value,
        label: group === "sources"
          ? sourceLabel(value)
          : group === "sections"
            ? appearanceLabel(value)
            : value,
        count: counts.get(value) || 0,
        selected: (state.filters?.[group] || []).includes(value),
      }));
    }
    return metadata;
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
    VIEW_MODES,
    SORTS,
    PAGE_SIZE_KEY,
    VIEW_MODE_KEY,
    normalize,
    formatRating,
    hasEpisodeCount,
    readPageSize,
    writePageSize,
    readViewMode,
    writeViewMode,
    createState,
    recordKey,
    asRecord,
    recordsFromQuarter,
    recordsFromCatalog,
    matchesFilters,
    compareRecords,
    applyPipeline,
    positionPopover,
    clearPopoverPosition,
    prefersReducedMotion,
    withBrowseTransition,
    subjectTransitionName,
    playEntranceStagger,
    scopeCounts,
    selectedRecord,
    appearanceLabel,
    sourceLabel,
    recordsForMedia,
    availableFilterValues,
    filterOptionMetadata,
    normalizeFiltersForMedia,
    paginationTokens,
  });
  window.BsbArchive = api;
})();

/* Small native listbox primitive shared by archive controls and Settings. */
(() => {
  "use strict";

  let nextListboxId = 0;

  function normalizeOptions(options) {
    return (Array.isArray(options) ? options : []).map((option) => ({
      value: String(option?.value ?? ""),
      label: String(option?.label ?? option?.value ?? ""),
      disabled: Boolean(option?.disabled),
    }));
  }

  function create(root, config = {}) {
    if (!root) return null;
    const options = normalizeOptions(config.options);
    const label = String(config.label || root.getAttribute("aria-label") || "选择");
    const listboxId = root.id ? `${root.id}-listbox` : `bsb-listbox-${++nextListboxId}`;
    const triggerId = root.id ? `${root.id}-trigger` : `${listboxId}-trigger`;
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "select-trigger";
    trigger.setAttribute("role", "combobox");
    trigger.id = triggerId;
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-label", label);
    trigger.setAttribute("aria-controls", listboxId);
    const listbox = document.createElement("div");
    listbox.className = "select-listbox";
    listbox.id = listboxId;
    listbox.hidden = true;
    listbox.setAttribute("role", "listbox");
    listbox.setAttribute("aria-labelledby", triggerId);
    root.classList.add("select-control");
    root.replaceChildren(trigger, listbox);

    let current = String(config.value ?? options.find((option) => !option.disabled)?.value ?? "");
    let activeIndex = Math.max(0, options.findIndex((option) => option.value === current));
    const onPointerDown = (event) => {
      if (!root.contains(event.target)) close();
    };

    const enabledIndex = (start, direction) => {
      if (!options.length) return -1;
      let index = start;
      for (let attempts = 0; attempts < options.length; attempts += 1) {
        index = (index + direction + options.length) % options.length;
        if (!options[index].disabled) return index;
      }
      return -1;
    };

    const firstEnabledIndex = () => options.findIndex((option) => !option.disabled);

    function normalizeActiveIndex() {
      if (activeIndex < 0 || activeIndex >= options.length || options[activeIndex]?.disabled) {
        activeIndex = firstEnabledIndex();
      }
    }

    function syncActive() {
      const optionNodes = [...listbox.querySelectorAll('[role="option"]')];
      normalizeActiveIndex();
      optionNodes.forEach((node, index) => {
        node.classList.toggle("is-active", index === activeIndex);
      });
      const active = optionNodes[activeIndex];
      if (active && !options[activeIndex]?.disabled) {
        trigger.setAttribute("aria-activedescendant", active.id);
        if (!listbox.hidden) active.scrollIntoView({ block: "nearest" });
      } else {
        trigger.removeAttribute("aria-activedescendant");
      }
    }

    function render() {
      const selected = options.find((option) => option.value === current) || options[0];
      trigger.textContent = selected?.label || label;
      listbox.replaceChildren(...options.map((option, index) => {
        const node = document.createElement("button");
        node.type = "button";
        node.className = "select-option";
        node.id = `${listboxId}-option-${index}`;
        node.setAttribute("role", "option");
        node.setAttribute("aria-selected", String(option.value === current));
        node.setAttribute("aria-disabled", String(option.disabled));
        node.disabled = option.disabled;
        node.textContent = option.label;
        node.addEventListener("click", () => select(option.value));
        return node;
      }));
      activeIndex = Math.max(0, options.findIndex((option) => option.value === current));
      normalizeActiveIndex();
      syncActive();
    }

    function close({ restoreFocus = false } = {}) {
      if (listbox.hidden) return;
      listbox.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      if (restoreFocus) trigger.focus();
    }

    function open() {
      if (!options.length) return;
      normalizeActiveIndex();
      listbox.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      activeIndex = Math.max(0, options.findIndex((option) => option.value === current));
      syncActive();
    }

    function select(value) {
      const option = options.find((candidate) => candidate.value === String(value));
      if (!option || option.disabled) return;
      const changed = current !== option.value;
      current = option.value;
      render();
      close({ restoreFocus: true });
      if (changed && typeof config.onChange === "function") config.onChange(current);
    }

    function moveTo(index) {
      if (!options.length) return;
      let next = Math.min(Math.max(index, 0), options.length - 1);
      if (options[next]?.disabled) next = enabledIndex(next, index < activeIndex ? -1 : 1);
      if (next >= 0) {
        activeIndex = next;
        syncActive();
      }
    }

    trigger.addEventListener("click", () => {
      if (listbox.hidden) open();
      else close({ restoreFocus: true });
    });
    trigger.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (listbox.hidden) open();
        moveTo(enabledIndex(activeIndex, event.key === "ArrowDown" ? 1 : -1));
      } else if (event.key === "Home" && !listbox.hidden) {
        event.preventDefault();
        moveTo(0);
      } else if (event.key === "End" && !listbox.hidden) {
        event.preventDefault();
        moveTo(options.length - 1);
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        if (listbox.hidden) open();
        else if (options[activeIndex]) select(options[activeIndex].value);
      } else if (event.key === "Escape" && !listbox.hidden) {
        event.preventDefault();
        close({ restoreFocus: true });
      } else if (event.key === "Tab") {
        close();
      }
    });
    document.addEventListener("pointerdown", onPointerDown);
    render();

    return {
      getValue: () => current,
      setValue: (value, { notify = false } = {}) => {
        const previous = current;
        current = String(value ?? "");
        render();
        if (notify && previous !== current && typeof config.onChange === "function") config.onChange(current);
      },
      setOptions: (nextOptions, { value = current, notify = false } = {}) => {
        options.splice(0, options.length, ...normalizeOptions(nextOptions));
        current = String(value ?? options.find((option) => !option.disabled)?.value ?? "");
        activeIndex = Math.max(0, options.findIndex((option) => option.value === current));
        render();
        if (notify && typeof config.onChange === "function") config.onChange(current);
      },
      focus: () => trigger.focus(),
      trigger,
      listbox,
      close,
      destroy: () => document.removeEventListener("pointerdown", onPointerDown),
    };
  }

  window.BsbListbox = Object.freeze({ create });
})();

/* Lightweight static navigation controls shared by every generated page. */
(() => {
  "use strict";

  const menu = document.querySelector("[data-mobile-menu]");
  const menuToggle = document.querySelector("[data-mobile-menu-toggle]");
  if (menu && menuToggle) {
    let useNativePopover = typeof menu.showPopover === "function"
      && typeof menu.hidePopover === "function";
    let restoreFocusOnClose = false;
    menu.dataset.menuMode = useNativePopover ? "popover" : "fallback";

    const isOpen = () => useNativePopover
      ? menu.matches(":popover-open")
      : menu.dataset.menuOpen === "true";
    const setOpenState = (open) => {
      menu.dataset.menuOpen = String(open);
      menuToggle.setAttribute("aria-expanded", String(open));
    };
    const clearMenuPosition = () => {
      for (const property of ["top", "right", "max-width", "max-height"]) {
        menu.style.removeProperty(property);
      }
    };
    const positionMenu = () => {
      if (!isOpen()) return;
      const anchor = menuToggle.getBoundingClientRect();
      const viewportPadding = 8;
      const right = Math.max(viewportPadding, window.innerWidth - anchor.right);
      const maxWidth = Math.max(0, window.innerWidth - viewportPadding * 2);
      const maxHeight = Math.max(0, window.innerHeight - viewportPadding * 2);
      menu.style.right = `${Math.round(right)}px`;
      menu.style.maxWidth = `${Math.round(maxWidth)}px`;
      menu.style.maxHeight = `${Math.round(maxHeight)}px`;
      const height = menu.getBoundingClientRect().height;
      const below = anchor.bottom + 8;
      const top = below + height <= window.innerHeight - viewportPadding
        ? below
        : Math.max(viewportPadding, anchor.top - height - 8);
      menu.style.top = `${Math.round(top)}px`;
    };
    const closeCompetingSurfaces = () => {
      document.dispatchEvent(new Event("bsb-mobile-menu-open"));
      document.querySelectorAll('[data-filter-toggle][aria-expanded="true"]')
        .forEach((button) => button.click());
    };
    const closeMenu = (restoreFocus = false) => {
      restoreFocusOnClose = restoreFocus;
      if (useNativePopover && isOpen()) {
        try {
          menu.hidePopover();
          setOpenState(false);
          clearMenuPosition();
          if (restoreFocus) menuToggle.focus();
        } catch {
          useNativePopover = false;
          menu.dataset.menuMode = "fallback";
          setOpenState(false);
          clearMenuPosition();
          if (restoreFocus) menuToggle.focus();
        }
      } else {
        setOpenState(false);
        clearMenuPosition();
        if (restoreFocus) menuToggle.focus();
      }
    };
    const openMenu = () => {
      closeCompetingSurfaces();
      if (useNativePopover) {
        try {
          menu.showPopover();
        } catch {
          useNativePopover = false;
          menu.dataset.menuMode = "fallback";
          setOpenState(true);
        }
      } else {
        setOpenState(true);
      }
      setOpenState(true);
      positionMenu();
      requestAnimationFrame(positionMenu);
      menu.querySelector("a, button")?.focus();
    };
    menu.addEventListener("toggle", (event) => {
      const open = event.newState === "open";
      setOpenState(open);
      if (open) {
        positionMenu();
        requestAnimationFrame(positionMenu);
      } else {
        clearMenuPosition();
        if (restoreFocusOnClose) menuToggle.focus();
        restoreFocusOnClose = false;
      }
    });
    menuToggle.addEventListener("pointerdown", (event) => event.stopPropagation());
    menuToggle.addEventListener("click", () => {
      if (isOpen()) closeMenu();
      else openMenu();
    });
    menu.addEventListener("click", (event) => {
      if (event.target instanceof HTMLAnchorElement) closeMenu();
    });
    document.addEventListener("pointerdown", (event) => {
      if (isOpen() && !menu.contains(event.target) && event.target !== menuToggle) {
        closeMenu();
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && isOpen()) {
        event.preventDefault();
        closeMenu(true);
      }
    });
    window.addEventListener("resize", positionMenu, { passive: true });
    window.addEventListener("scroll", positionMenu, { passive: true });
  }

  const quarterToggle = document.querySelector("[data-quarter-selector]");
  const quarterSheet = document.querySelector("[data-quarter-sheet]");
  if (quarterToggle && quarterSheet) {
    const closeSheet = (restoreFocus = false) => {
      quarterSheet.hidden = true;
      quarterToggle.setAttribute("aria-expanded", "false");
      if (restoreFocus) quarterToggle.focus();
    };
    quarterToggle.addEventListener("click", () => {
      const opening = quarterSheet.hidden;
      quarterSheet.hidden = !opening;
      quarterToggle.setAttribute("aria-expanded", String(opening));
      if (opening) document.dispatchEvent(new Event("bsb-quarter-sheet-open"));
      if (opening) quarterSheet.querySelector("a, button")?.focus();
    });
    document.addEventListener("bsb-mobile-menu-open", () => closeSheet());
    quarterSheet.querySelector("[data-quarter-sheet-close]")?.addEventListener(
      "click",
      () => closeSheet(true),
    );
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !quarterSheet.hidden) {
        event.preventDefault();
        closeSheet(true);
      }
    });
  }
})();

/* Standalone-only detail edge navigation.  Normal Safari keeps ownership of
   its native history gesture; the app gesture is deliberately opt-in. */
(() => {
  "use strict";

  function isStandalone() {
    return window.navigator.standalone === true
      || window.matchMedia?.("(display-mode: standalone)").matches === true;
  }

  function bind({ root, panel, surface = panel, onComplete }) {
    if (!root || !panel) return () => {};
    let pointerId = null;
    let startX = 0;
    let startY = 0;
    let gestureState = "idle";
    let backgroundLock = null;

    const setState = (value) => {
      gestureState = value;
      root.dataset.detailGesture = value;
      panel.dataset.detailGesture = value;
    };
    const resetTransform = () => {
      surface.style.removeProperty("transform");
      surface.style.removeProperty("will-change");
    };
    const releasePointer = () => {
      try {
        if (pointerId !== null && panel.hasPointerCapture?.(pointerId)) {
          panel.releasePointerCapture(pointerId);
        }
      } catch {
        // Synthetic browser regression events do not own a real pointer.
      }
      pointerId = null;
    };
    const lockBackground = () => {
      if (backgroundLock) return;
      backgroundLock = {
        scrollTop: window.scrollY,
        htmlOverflow: document.documentElement.style.overflow,
        bodyOverflow: document.body.style.overflow,
        bodyOverscroll: document.body.style.overscrollBehavior,
      };
      document.documentElement.style.overflow = "hidden";
      document.body.style.overflow = "hidden";
      document.body.style.overscrollBehavior = "none";
    };
    const lockGesture = () => {
      lockBackground();
      panel.style.touchAction = "none";
      surface.style.willChange = "transform";
    };
    const unlockGesture = () => {
      panel.style.removeProperty("touch-action");
    };
    const unlockBackground = () => {
      if (!backgroundLock) return;
      const lock = backgroundLock;
      backgroundLock = null;
      document.documentElement.style.overflow = lock.htmlOverflow;
      document.body.style.overflow = lock.bodyOverflow;
      document.body.style.overscrollBehavior = lock.bodyOverscroll;
      unlockGesture();
      if (window.scrollY !== lock.scrollTop) {
        window.scrollTo({ top: lock.scrollTop, behavior: "auto" });
      }
    };
    const cancel = () => {
      if (gestureState !== "possible-drag" && gestureState !== "dragging") return;
      releasePointer();
      resetTransform();
      unlockGesture();
      setState("cancel");
    };
    const finish = () => {
      if (gestureState !== "dragging") return;
      releasePointer();
      resetTransform();
      unlockGesture();
      setState("commit");
      onComplete?.();
    };
    const onPointerDown = (event) => {
      if (!isStandalone() || panel.hidden || pointerId !== null
        || (gestureState === "possible-drag" || gestureState === "dragging")
        || event.isPrimary === false) return;
      const edge = 32 + (parseFloat(getComputedStyle(document.documentElement)
        .getPropertyValue("--safe-area-left")) || 0);
      if (event.clientX > edge) return;
      pointerId = event.pointerId;
      startX = event.clientX;
      startY = event.clientY;
      setState("possible-drag");
      try {
        panel.setPointerCapture?.(pointerId);
      } catch {
        // Synthetic browser regression events are still valid gesture input.
      }
    };
    const onPointerMove = (event) => {
      if (event.pointerId !== pointerId
        || (gestureState !== "possible-drag" && gestureState !== "dragging")) return;
      const dx = event.clientX - startX;
      const dy = event.clientY - startY;
      if (gestureState === "possible-drag") {
        if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
        if (dx <= 0 || Math.abs(dy) >= Math.abs(dx)) {
          cancel();
          return;
        }
        lockGesture();
        setState("dragging");
      }
      if (dx <= 0 || Math.abs(dy) > Math.abs(dx)) {
        cancel();
        return;
      }
      surface.style.transform = `translateX(${Math.min(dx, window.innerWidth)}px)`;
      event.preventDefault();
    };
    const onPointerUp = (event) => {
      if (event.pointerId !== pointerId) return;
      if (gestureState === "possible-drag") {
        cancel();
        return;
      }
      if (gestureState !== "dragging") return;
      const dx = event.clientX - startX;
      if (dx >= Math.max(100, window.innerWidth * 0.3)) finish();
      else cancel();
    };
    panel.addEventListener("pointerdown", onPointerDown);
    panel.addEventListener("pointermove", onPointerMove, { passive: false });
    panel.addEventListener("pointerup", onPointerUp);
    panel.addEventListener("pointercancel", cancel);
    setState("idle");
    const cleanup = () => {
      panel.removeEventListener("pointerdown", onPointerDown);
      panel.removeEventListener("pointermove", onPointerMove);
      panel.removeEventListener("pointerup", onPointerUp);
      panel.removeEventListener("pointercancel", cancel);
      releasePointer();
      resetTransform();
      unlockGesture();
      unlockBackground();
    };
    cleanup.lockBackground = lockBackground;
    cleanup.unlockBackground = unlockBackground;
    return cleanup;
  }

  window.BsbDetailGesture = Object.freeze({ bind, isStandalone });
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
  let pageSizeControl = null;
  let detailHistoryEntry = false;
  let detailGesture = null;
  let entrancePlayed = false;

  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  function hashFor(record) {
    return `#bgm-${record.id}`;
  }

  function setHash(record, replace, detailEntry = false) {
    const url = new URL(window.location.href);
    url.hash = record ? hashFor(record) : "";
    const historyState = record && detailEntry
      ? { bsbDetailEntry: true, bsbDetailKey: record.key }
      : {};
    if (replace) window.history.replaceState(historyState, "", url);
    else window.history.pushState(historyState, "", url);
  }

  function pageSizeSelect() {
    const root = quarterRoot.querySelector("[data-page-size]");
    if (!root || !window.BsbListbox) return;
    const options = archive.PAGE_SIZES.map((size) => ({ value: String(size), label: String(size) }));
    if (!pageSizeControl) {
      pageSizeControl = window.BsbListbox.create(root, {
        label: "每页数量",
        options,
        value: String(state.pageSize),
        onChange: (value) => {
          state.pageSize = archive.writePageSize(value);
          state.page = 1;
          clearSelection(true);
          render();
          pageSizeSelect();
        },
      });
    } else {
      pageSizeControl.setValue(String(state.pageSize));
    }
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
    const visibleRecords = window.innerWidth < 768 ? result.all : result.pageRecords;
    const visible = new Set(visibleRecords.map((record) => record.key));
    const position = new Map(result.all.map((record, index) => [record.key, index + 1]));
    for (const section of sections) {
      const media = section.dataset.listSection === "movie" ? "MOVIE" : "TV";
      const appearance = section.dataset.appearanceSection;
      const sectionRecords = records.filter((record) => (
        record.media === media
        && (appearance === "all" || record.appearance === appearance)
      ));
      const orderedKeys = new Set(result.all.filter((record) => (
        record.media === media
        && (appearance === "all" || record.appearance === appearance)
      )).map((record) => record.key));
      const orderedRecords = [
        ...result.all.filter((record) => (
          record.media === media
          && (appearance === "all" || record.appearance === appearance)
        )),
        ...sectionRecords.filter((record) => !orderedKeys.has(record.key)),
      ];
      const list = section.querySelector("[data-list]");
      orderedRecords.forEach((record) => {
        const row = rowByKey.get(record.key);
        if (row) list?.append(row);
      });
    }
    for (const row of rows) {
      const record = recordByKey.get(row.dataset.recordKey);
      const show = record && visible.has(record.key);
      if (record) row.style.viewTransitionName = archive.subjectTransitionName(record);
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
        && (appearance === "all" || record.appearance === appearance)).length;
      section.hidden = state.media !== media || count === 0;
      const counter = section.querySelector("[data-section-count]");
      if (counter) counter.textContent = String(count).padStart(2, "0");
    }
    if (summary) {
      const total = records.filter(
        (record) => record.media === (state.media === "movie" ? "MOVIE" : "TV"),
      ).length;
      summary.textContent = window.innerWidth < 768
        ? `${result.total} / ${total} 部`
        : `${result.total} / ${total} 部 · 第 ${result.page} / ${result.pageCount} 页`;
    }
    if (noResults) noResults.hidden = result.total !== 0;
  }

  function renderPager(result) {
    if (!pager) return;
    pager.replaceChildren();
    if (window.innerWidth < 768) {
      pager.hidden = true;
      return;
    }
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
    quarterRoot.dataset.viewMode = state.viewMode;
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
    quarterRoot.querySelectorAll("[data-view-mode]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.viewMode === state.viewMode));
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

  function cloneFilters(filters) {
    return {
      sources: [...(filters.sources || [])],
      tags: [...(filters.tags || [])],
      sections: [...(filters.sections || [])],
    };
  }

  function isMobileFilter() {
    return window.innerWidth < 768;
  }

  function beginFilterDraft() {
    state.draftFilters = isMobileFilter() ? cloneFilters(state.filters) : null;
  }

  function discardFilterDraft() {
    state.draftFilters = null;
  }

  function applyFilterDraft() {
    if (!state.draftFilters) return;
    state.filters = state.draftFilters;
    state.draftFilters = null;
    state.page = 1;
    clearSelection(true);
  }

  function clearSelection(removeHash = false) {
    detailGesture?.unlockBackground?.();
    detailHistoryEntry = false;
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
      ?.querySelector("[data-open-subject]")?.focus({ preventScroll: true });
  }

  function restoreListScroll() {
    if (window.innerWidth >= 768 || !state.listScrollTop) return;
    const target = state.listScrollTop;
    window.scrollTo({ top: target, behavior: "auto" });
    requestAnimationFrame(() => window.scrollTo({ top: target, behavior: "auto" }));
  }

  function closeFilterAndRestoreFocus(applyDraft = false) {
    archive.withBrowseTransition("filter", () => {
      if (applyDraft) applyFilterDraft();
      else discardFilterDraft();
      closeFilter();
      render();
    });
    quarterRoot.querySelector("[data-filter-toggle]")?.focus();
    restoreListScroll();
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
      archive.hasEpisodeCount(record.episode_count) ? ["集数", record.episode_count] : null,
      record.air_date ? ["播出日期", record.air_date] : null,
      record.end_date ? ["结束日期", record.end_date] : null,
      ["评分", archive.formatRating(record.score ?? record.rating_score), "detail-score"],
      record.rating_count !== null && record.rating_count !== undefined ? ["评分人数", record.rating_count] : null,
      ["来源", archive.sourceLabel(record.source)],
    ].filter(Boolean).map(([label, value, className]) => `<div><dt>${label}</dt><dd${className ? ` class="${className}"` : ""}>${esc(value)}</dd></div>`).join("");
    return `<div class="detail-head"><div class="detail-topbar" role="navigation" aria-label="详情导航"><button type="button" class="detail-close" data-detail-close aria-label="返回结果"><span class="detail-close__icon" aria-hidden="true">←</span><span class="detail-close__back">返回结果</span></button>
      <p class="workspace-panel__code detail-topbar__context">${esc(record.media)} / ${esc(record.appearance)}${record.appearance === "continuing" ? ' <span class="detail-appearance-badge">续播</span>' : ""}</p></div>
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
    if (window.innerWidth < 768 && state.workspaceMode !== "detail") {
      state.listScrollTop = window.scrollY;
      detailGesture?.lockBackground?.();
    }
    const hadSelection = state.selectedOccurrence !== null;
    const nextDetailHistoryEntry = hadSelection
      ? detailHistoryEntry
      : replace
        ? window.history.state?.bsbDetailEntry === true
        : true;
    state.selectedSubjectId = record.id;
    state.selectedOccurrence = record.key;
    state.workspaceMode = "detail";
    quarterRoot.dataset.detailGesture = "idle";
    detailPanel?.setAttribute("data-detail-gesture", "idle");
    if (hadSelection) setHash(record, true, nextDetailHistoryEntry);
    else setHash(record, replace, nextDetailHistoryEntry);
    detailHistoryEntry = nextDetailHistoryEntry;
    if (detailPanel) {
      detailPanel.innerHTML = detailHtml(record);
      detailPanel.hidden = false;
      detailPanel.querySelector("[data-detail-close]")?.addEventListener("click", () => closeDetail(record.key));
      detailPanel.querySelector("[data-lightbox]")?.addEventListener("click", () => openLightbox(record));
      detailPanel.scrollTop = 0;
    }
    render();
  }

  function closeDetail(recordKey) {
    const currentHash = window.location.hash === hashFor({ id: state.selectedSubjectId });
    if (detailHistoryEntry && currentHash) {
      detailHistoryEntry = false;
      clearSelection(false);
      render();
      focusRecordTrigger(recordKey);
      restoreListScroll();
      window.history.back();
      return;
    }
    clearSelection(true);
    render();
    focusRecordTrigger(recordKey);
    restoreListScroll();
  }

  function bindDetailGesture() {
    detailGesture = window.BsbDetailGesture?.bind({
      root: quarterRoot,
      panel: detailPanel,
      surface: quarterRoot.querySelector(".workspace"),
      onComplete: () => closeDetail(state.selectedOccurrence),
    }) || null;
  }

  function openLightbox(record) {
    const cover = record.cover || record.cover_url;
    if (!cover) return;
    const dialog = document.createElement("dialog");
    dialog.className = "cover-lightbox";
    dialog.setAttribute("aria-label", `查看 ${record.preferred_title} 封面`);
    dialog.innerHTML = `<img src="../${esc(String(cover))}" alt="${esc(record.preferred_title)}">`;
    document.body.append(dialog);
    const close = () => { dialog.close(); dialog.remove(); };
    dialog.addEventListener("click", (event) => { if (event.target === dialog) close(); });
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    dialog.showModal();
  }

  function openHash() {
    const match = window.location.hash.match(/^#bgm-(\d+)$/);
    if (!match || !records.length) {
      if (!match && state.selectedOccurrence !== null) {
        const previousKey = state.selectedOccurrence;
        clearSelection(false);
        render();
        focusRecordTrigger(previousKey);
        restoreListScroll();
      }
      if (!match && state.selectedOccurrence === null) restoreListScroll();
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
    let closeSortPopover = () => {};
    search?.addEventListener("input", () => {
      archive.withBrowseTransition("search", () => {
        state.query = search.value;
        state.page = 1;
        clearSelection(true);
        render();
      });
    });
    quarterRoot.querySelectorAll("[data-media-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        closeSortPopover();
        archive.withBrowseTransition("media", () => {
          state.media = button.dataset.mediaMode === "movie" ? "movie" : "tv";
          discardFilterDraft();
          archive.normalizeFiltersForMedia(state, records);
          state.page = 1;
          clearSelection(true);
          renderFilterPanel();
          render();
        });
      });
    });
    quarterRoot.querySelectorAll("[data-view-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        closeSortPopover();
        archive.withBrowseTransition("view-mode", () => {
          state.viewMode = archive.writeViewMode(button.dataset.viewMode);
          render();
        });
        button.focus();
      });
    });
    const sortButton = quarterRoot.querySelector("[data-sort-toggle]");
    const sortPopover = quarterRoot.querySelector("[data-sort-popover]");
    closeSortPopover = (restoreFocus = false) => {
      if (!sortPopover || sortPopover.hidden) return;
      sortPopover.hidden = true;
      archive.clearPopoverPosition(sortPopover);
      sortButton?.setAttribute("aria-expanded", "false");
      if (restoreFocus) sortButton?.focus();
    };
    const openSortPopover = (focusOption = false) => {
      if (!sortPopover) return;
      sortPopover.hidden = false;
      sortButton?.setAttribute("aria-expanded", "true");
      sortPopover.replaceChildren(...Object.entries(archive.SORTS).map(([value, label]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.setAttribute("role", "menuitemradio");
        button.textContent = label;
        button.setAttribute("aria-checked", String(state.sort === value));
        button.addEventListener("click", () => {
          archive.withBrowseTransition("sort", () => {
            state.sort = value;
            state.page = 1;
            closeSortPopover(true);
            clearSelection(true);
            render();
          });
        });
        return button;
      }));
      archive.positionPopover(sortPopover, sortButton);
      if (focusOption) sortPopover.querySelector('[aria-checked="true"]')?.focus();
    };
    sortPopover?.addEventListener("keydown", (event) => {
      const items = [...sortPopover.querySelectorAll('[role="menuitemradio"]')];
      if (!items.length) return;
      const currentIndex = Math.max(0, items.indexOf(document.activeElement));
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const offset = event.key === "ArrowDown" ? 1 : -1;
        items[(currentIndex + offset + items.length) % items.length].focus();
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        items[event.key === "Home" ? 0 : items.length - 1].focus();
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        document.activeElement?.click();
      } else if (event.key === "Escape") {
        event.preventDefault();
        closeSortPopover(true);
      }
    });
    sortButton?.addEventListener("click", (event) => {
      if (!sortPopover) return;
      if (sortPopover.hidden) openSortPopover(event.detail === 0);
      else closeSortPopover(true);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && sortPopover && !sortPopover.hidden) {
        event.preventDefault();
        closeSortPopover(true);
      }
    });
    document.addEventListener("pointerdown", (event) => {
      if (sortPopover && !sortPopover.hidden && !sortPopover.contains(event.target) && event.target !== sortButton) {
        closeSortPopover();
      }
    });
    document.addEventListener("bsb-quarter-sheet-open", () => closeSortPopover());
    document.addEventListener("bsb-mobile-menu-open", () => closeSortPopover());
    window.addEventListener("scroll", () => closeSortPopover(), { passive: true });
    quarterRoot.querySelector("[data-filter-toggle]")?.addEventListener("click", () => {
      closeSortPopover();
      if (state.workspaceMode === "filter") {
        closeFilterAndRestoreFocus(false);
      } else {
        if (window.innerWidth < 768) state.listScrollTop = window.scrollY;
        beginFilterDraft();
        state.workspaceMode = "filter";
      }
      renderFilterPanel();
      render();
    });
    quarterRoot.querySelector("[data-clear-all]")?.addEventListener("click", () => {
      archive.withBrowseTransition("clear-filter", () => {
        state.query = "";
        state.filterOptionQuery = "";
        state.filters = { sources: [], tags: [], sections: [] };
        if (search) search.value = "";
        state.page = 1;
        clearSelection(true);
        render();
      });
    });
    rows.forEach((row) => row.querySelector("[data-open-subject]")?.addEventListener("click", () => {
      selectRecord(recordByKey.get(row.dataset.recordKey));
    }));
    window.addEventListener("resize", () => render());
    window.addEventListener("popstate", openHash);
    window.addEventListener("hashchange", openHash);
    bindDetailGesture();
  }

  function renderFilterPanel() {
    if (!filterPanel) return;
    const panelFilters = state.draftFilters || state.filters;
    const panelState = { ...state, filters: panelFilters };
    const options = archive.filterOptionMetadata(records, panelState);
    filterPanel.innerHTML = `<div class="filter-panel__head"><p class="workspace-panel__code">FILTER WORKSPACE</p><button type="button" class="detail-close" data-filter-close aria-label="关闭筛选"><span aria-hidden="true">×</span><span class="detail-close__back">返回结果</span></button></div><h2>筛选资料</h2><p class="filter-workspace-summary" data-filter-workspace-summary></p><div class="active-filter-strip filter-workspace-active" data-filter-workspace-active hidden></div><button type="button" class="text-button filter-workspace-clear" data-filter-workspace-clear>清除全部筛选</button><label class="filter-option-search"><span class="sr-only">搜索筛选选项</span><input type="search" data-filter-option-search placeholder="搜索选项名称"></label>`;
    const result = archive.applyPipeline(records, panelState);
    const workspaceSummary = filterPanel.querySelector("[data-filter-workspace-summary]");
    if (workspaceSummary) workspaceSummary.textContent = `当前结果 ${result.total} 部`;
    const workspaceActive = filterPanel.querySelector("[data-filter-workspace-active]");
    const activeValues = [
      ...(state.query ? [{ label: `搜索：${state.query}`, type: "query", value: state.query }] : []),
      ...panelFilters.sources.map((value) => ({ label: `来源：${archive.sourceLabel(value)}`, type: "sources", value })),
      ...panelFilters.tags.map((value) => ({ label: `标签：${value}`, type: "tags", value })),
      ...panelFilters.sections.map((value) => ({ label: `分区：${archive.appearanceLabel(value)}`, type: "sections", value })),
    ];
    if (workspaceActive) {
      workspaceActive.hidden = activeValues.length === 0;
      activeValues.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "active-filter";
        button.textContent = `${item.label} ×`;
        button.addEventListener("click", () => {
          if (item.type === "query") {
            state.query = "";
            if (search) search.value = "";
          } else {
            panelFilters[item.type] = panelFilters[item.type].filter((value) => value !== item.value);
          }
          if (!state.draftFilters) {
            state.page = 1;
            state.workspaceMode = "filter";
            render();
          }
          renderFilterPanel();
        });
        workspaceActive.append(button);
      });
    }
    const workspaceClear = filterPanel.querySelector("[data-filter-workspace-clear]");
    if (workspaceClear) {
      workspaceClear.disabled = activeValues.length === 0;
      workspaceClear.addEventListener("click", () => {
        state.query = "";
        state.filterOptionQuery = "";
        if (state.draftFilters) {
          state.draftFilters = { sources: [], tags: [], sections: [] };
        } else {
          state.filters = { sources: [], tags: [], sections: [] };
        }
        if (search) search.value = "";
        state.workspaceMode = "filter";
        renderFilterPanel();
        if (!state.draftFilters) {
          state.page = 1;
          clearSelection(true);
          render();
        }
      });
    }
    const optionSearch = filterPanel.querySelector("[data-filter-option-search]");
    if (optionSearch) optionSearch.value = state.filterOptionQuery;
    const applyOptionQuery = () => {
      const query = archive.normalize(state.filterOptionQuery);
      filterPanel.querySelectorAll("[data-filter-option]").forEach((option) => {
        const selected = option.querySelector("input")?.checked === true;
        option.hidden = Boolean(
          query
          && option.dataset.filterOption?.includes(query) === false
          && !selected
        );
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
      for (const option of values) {
        const { value, label: optionLabel, count, selected } = option;
        const label = document.createElement("label");
        label.className = "filter-option";
        const shown = optionLabel;
        label.dataset.filterOption = archive.normalize(shown);
        label.dataset.filterOptionCount = String(count);
        label.classList.toggle("is-selected", selected);
        const input = document.createElement("input");
        input.type = "checkbox";
        input.dataset.filterGroup = group;
        input.dataset.filterValue = value;
        input.checked = selected;
        input.setAttribute("aria-label", shown);
        input.addEventListener("change", () => {
          const target = state.draftFilters || state.filters;
          target[group] = input.checked
            ? [...target[group], value]
            : target[group].filter((item) => item !== value);
          if (!state.draftFilters) {
            state.page = 1;
            state.workspaceMode = "filter";
            render();
          }
          renderFilterPanel();
          const replacement = [...filterPanel.querySelectorAll("[data-filter-group]")]
            .find((candidate) => candidate.dataset.filterGroup === group
              && candidate.dataset.filterValue === value);
          (replacement || filterPanel.querySelector("[data-filter-option-search]"))?.focus();
        });
        const countNode = document.createElement("span");
        countNode.className = "filter-option__count";
        countNode.textContent = String(count);
        label.append(input, document.createTextNode(shown), countNode);
        section.append(label);
      }
      filterPanel.append(section);
    }
    applyOptionQuery();
    const applyButton = document.createElement("button");
    applyButton.type = "button";
    applyButton.className = "filter-apply-mobile button button--ink";
    applyButton.dataset.filterClose = "true";
    applyButton.textContent = `返回结果 · ${result.total} 部`;
    applyButton.setAttribute("aria-label", `返回结果，当前 ${result.total} 部`);
    applyButton.addEventListener("click", () => closeFilterAndRestoreFocus(true));
    filterPanel.append(applyButton);
    filterPanel.querySelector("[data-filter-close]")?.addEventListener(
      "click",
      () => closeFilterAndRestoreFocus(false),
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
      if (!entrancePlayed) {
        entrancePlayed = true;
        archive.playEntranceStagger(quarterRoot);
      }
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
  let listSignature = "";
  let listSections = new Map();
  let rowByKey = new Map();
  let recordByKey = new Map();
  const detailByQuarter = new Map();
  let detailRequest = 0;
  let loadError = false;
  let yearControl = null;
  let pageSizeControl = null;
  let detailHistoryEntry = false;
  let detailGesture = null;
  let entrancePlayed = false;

  const esc = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

  function setDetailHash(record, replace, detailEntry = false) {
    const url = new URL(window.location.href);
    url.hash = record ? `#bgm-${record.id}` : "";
    const historyState = record && detailEntry
      ? { bsbDetailEntry: true, bsbDetailKey: record.key }
      : {};
    if (replace) window.history.replaceState(historyState, "", url);
    else window.history.pushState(historyState, "", url);
  }

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
    const selectRoot = root.querySelector("[data-archive-year-select]");
    if (!selectRoot || !index || !window.BsbListbox) return;
    const options = (index.years || []).slice().sort((a, b) => b - a)
      .map((year) => ({ value: String(year), label: String(year) }));
    const value = state.scope.kind === "year"
      ? String(state.scope.value)
      : String(index.latest_quarter || "").slice(0, 4);
    if (!yearControl) {
      yearControl = window.BsbListbox.create(selectRoot, {
        label: "年份",
        options,
        value,
        onChange: (next) => setScope("year", next),
      });
    } else {
      yearControl.setOptions(options, { value });
    }
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

  function compactGridDate(value) {
    const text = String(value || "");
    return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text.slice(2) : text;
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
    const ratingScore = record.score ?? record.rating_score;
    article.dataset.score = ratingScore === null || ratingScore === undefined ? "" : String(ratingScore);
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
    if (record.appearance === "continuing") {
      const badge = document.createElement("span");
      badge.className = "subject-row__appearance-badge";
      badge.dataset.appearanceBadge = "continuing";
      badge.textContent = "续播";
      content.append(badge);
    }
    const original = document.createElement("span");
    original.className = "subject-row__original";
    original.textContent = record.original_title || "";
    content.append(original);
    const metadata = document.createElement("span");
    metadata.className = "subject-row__meta";
    const metadataFull = document.createElement("span");
    metadataFull.className = "subject-row__meta-full";
    metadataFull.textContent = [record.media, archive.hasEpisodeCount(record.episode_count) ? `${record.episode_count}话` : "", record.air_date || "", archive.sourceLabel(record.source), record.quarter || ""]
      .filter(Boolean).join(" · ");
    const metadataGrid = document.createElement("span");
    metadataGrid.className = "subject-row__meta-grid";
    metadataGrid.setAttribute("aria-hidden", "true");
    metadataGrid.textContent = [record.media, archive.hasEpisodeCount(record.episode_count) ? `${record.episode_count}话` : "", compactGridDate(record.air_date), archive.sourceLabel(record.source), record.quarter || ""]
      .filter(Boolean).join(" · ");
    metadata.append(metadataFull, metadataGrid);
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
    scoreValue.textContent = archive.formatRating(record.score ?? record.rating_score);
    const count = document.createElement("small");
    count.textContent = record.rating_count === null || record.rating_count === undefined ? "—" : String(record.rating_count);
    score.append(scoreValue, count);
    button.append(score);
    article.append(button);
    article.style.viewTransitionName = archive.subjectTransitionName(record);
    button.addEventListener("click", () => { void selectRecord(record); });
    return article;
  }

  function buildLists(result) {
    if (!selectors.list) return;
    const signature = records.map((record) => record.key).join("\u0000");
    if (signature !== listSignature) {
      selectors.list.replaceChildren();
      listSections = new Map();
      rowByKey = new Map();
      const groups = [
        ["tv", "all", "电视节目"],
        ["movie", "premiere", "剧场版"],
      ];
      groups.forEach(([media, appearance, title]) => {
        const sectionRecords = records.filter((record) => (
          record.media === (media === "movie" ? "MOVIE" : "TV")
          && (appearance === "all" || record.appearance === appearance)
        ));
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
        header.append(code, heading, counter);
        const list = document.createElement("div");
        list.className = "result-list";
        sectionRecords.forEach((record, index) => {
          const row = createRow(record, index + 1);
          rowByKey.set(record.key, row);
          list.append(row);
        });
        section.append(header, list);
        selectors.list.append(section);
        listSections.set(media, { section, list, records: sectionRecords });
      });
      rows = [...selectors.list.querySelectorAll(".subject-row")];
      listSignature = signature;
    }
    const positions = new Map(result.all.map((record, index) => [record.key, index + 1]));
    const visibleRecords = window.innerWidth < 768 ? result.all : result.pageRecords;
    const visible = new Set(visibleRecords.map((record) => record.key));
    for (const [media, group] of listSections) {
      const sectionMedia = media === "movie" ? "MOVIE" : "TV";
      const matching = result.all.filter((record) => record.media === sectionMedia);
      const matchingKeys = new Set(matching.map((record) => record.key));
      const ordered = [
        ...matching,
        ...group.records.filter((record) => !matchingKeys.has(record.key)),
      ];
      group.list.replaceChildren(
        ...ordered
          .filter((record) => visible.has(record.key))
          .map((record) => rowByKey.get(record.key)),
      );
      const count = matching.length;
      group.section.hidden = state.media !== media || count === 0;
      const counter = group.section.querySelector("[data-section-count]");
      if (counter) counter.textContent = String(count).padStart(2, "0");
      group.records.forEach((record) => {
        const row = rowByKey.get(record.key);
        if (!row) return;
        row.hidden = false;
        row.classList.toggle("is-selected", record.key === state.selectedOccurrence);
        const button = row.querySelector("[data-open-subject]");
        if (button) button.setAttribute("aria-expanded", String(record.key === state.selectedOccurrence));
        const sequence = row.querySelector(".subject-row__sequence");
        if (sequence) sequence.textContent = String(positions.get(record.key) || 0).padStart(3, "0");
      });
    }
    rows = [...rowByKey.values()];
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
    detailGesture?.unlockBackground?.();
    detailHistoryEntry = false;
    detailRequest += 1;
    state.selectedSubjectId = null;
    state.selectedOccurrence = null;
    state.workspaceMode = "scope";
    if (removeHash) {
      setDetailHash(null, true);
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
      ?.querySelector("[data-open-subject]")?.focus({ preventScroll: true });
  }

  function restoreListScroll() {
    if (window.innerWidth >= 768 || !state.listScrollTop) return;
    const target = state.listScrollTop;
    window.scrollTo({ top: target, behavior: "auto" });
    requestAnimationFrame(() => window.scrollTo({ top: target, behavior: "auto" }));
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
    root.dataset.viewMode = state.viewMode;
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
    root.querySelectorAll("[data-view-mode]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.viewMode === state.viewMode));
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

  function cloneFilters(filters) {
    return {
      sources: [...(filters.sources || [])],
      tags: [...(filters.tags || [])],
      sections: [...(filters.sections || [])],
    };
  }

  function beginFilterDraft() {
    state.draftFilters = window.innerWidth < 768 ? cloneFilters(state.filters) : null;
  }

  function discardFilterDraft() {
    state.draftFilters = null;
  }

  function applyFilterDraft() {
    if (!state.draftFilters) return;
    state.filters = state.draftFilters;
    state.draftFilters = null;
    state.page = 1;
    clearSelection(true);
  }

  function closeFilterAndRestoreFocus(applyDraft = false) {
    archive.withBrowseTransition("filter", () => {
      if (applyDraft) applyFilterDraft();
      else discardFilterDraft();
      closeFilter();
      render();
    });
    root.querySelector("[data-filter-toggle]")?.focus();
    restoreListScroll();
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
      archive.hasEpisodeCount(record.episode_count) ? ["集数", record.episode_count] : null,
      record.air_date ? ["播出日期", record.air_date] : null,
      record.end_date ? ["结束日期", record.end_date] : null,
      ["评分", archive.formatRating(record.score ?? record.rating_score), "detail-score"],
      record.rating_count !== null && record.rating_count !== undefined ? ["评分人数", record.rating_count] : null,
      ["来源", archive.sourceLabel(record.source)],
    ].filter(Boolean).map(([label, value, className]) => `<div><dt>${label}</dt><dd${className ? ` class="${className}"` : ""}>${esc(value)}</dd></div>`).join("");
    return `<div class="detail-head"><div class="detail-topbar" role="navigation" aria-label="详情导航"><button type="button" class="detail-close" data-detail-close aria-label="返回结果"><span class="detail-close__icon" aria-hidden="true">←</span><span class="detail-close__back">返回结果</span></button><p class="workspace-panel__code detail-topbar__context">${esc(record.media)} / ${esc(record.appearance)}${record.appearance === "continuing" ? ' <span class="detail-appearance-badge">续播</span>' : ""}</p></div>
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
    dialog.setAttribute("aria-label", `查看 ${record.preferred_title} 封面`);
    dialog.innerHTML = `<img src="../${esc(String(cover))}" alt="${esc(record.preferred_title)}">`;
    document.body.append(dialog);
    const close = () => { dialog.close(); dialog.remove(); };
    dialog.addEventListener("click", (event) => { if (event.target === dialog) close(); });
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    dialog.showModal();
  }

  async function selectRecord(record, replace = false) {
    if (!record) return;
    if (window.innerWidth < 768 && state.workspaceMode !== "detail") {
      state.listScrollTop = window.scrollY;
      detailGesture?.lockBackground?.();
    }
    const request = ++detailRequest;
    const hadSelection = state.selectedOccurrence !== null;
    const nextDetailHistoryEntry = hadSelection
      ? detailHistoryEntry
      : replace
        ? window.history.state?.bsbDetailEntry === true
        : true;
    state.selectedSubjectId = record.id;
    state.selectedOccurrence = record.key;
    state.workspaceMode = "detail";
    root.dataset.detailGesture = "idle";
    selectors.detailPanel?.setAttribute("data-detail-gesture", "idle");
    if (hadSelection) setDetailHash(record, true, nextDetailHistoryEntry);
    else setDetailHash(record, replace, nextDetailHistoryEntry);
    detailHistoryEntry = nextDetailHistoryEntry;
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
        selectors.detailPanel.querySelector("[data-detail-close]")?.addEventListener("click", () => closeDetail(record.key));
        selectors.detailPanel.querySelector("[data-lightbox]")?.addEventListener("click", () => openLightbox(detail));
      }
    } catch {
      if (request !== detailRequest || state.selectedOccurrence !== record.key) return;
      if (selectors.detailPanel) {
        selectors.detailPanel.innerHTML = '<p class="workspace-panel__code">DATA UNAVAILABLE</p><h2>当前资料详情未完整生成</h2><p>建议重新 build。</p>';
      }
    }
  }

  function closeDetail(recordKey) {
    const currentHash = window.location.hash === `#bgm-${state.selectedSubjectId}`;
    if (detailHistoryEntry && currentHash) {
      detailHistoryEntry = false;
      clearSelection(false);
      render();
      focusRecordTrigger(recordKey);
      restoreListScroll();
      window.history.back();
      return;
    }
    clearSelection(true);
    render();
    focusRecordTrigger(recordKey);
    restoreListScroll();
  }

  function bindDetailGesture() {
    detailGesture = window.BsbDetailGesture?.bind({
      root,
      panel: selectors.detailPanel,
      surface: selectors.detailPanel?.closest(".workspace"),
      onComplete: () => closeDetail(state.selectedOccurrence),
    }) || null;
  }

  async function openHash() {
    const match = window.location.hash.match(/^#bgm-(\d+)$/);
    if (!match || !records.length) {
      if (!match && state.selectedOccurrence !== null) {
        const previousKey = state.selectedOccurrence;
        clearSelection(false);
        render();
        focusRecordTrigger(previousKey);
        restoreListScroll();
      }
      if (!match && state.selectedOccurrence === null) restoreListScroll();
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
    const panelFilters = state.draftFilters || state.filters;
    const panelState = { ...state, filters: panelFilters };
    const options = archive.filterOptionMetadata(records, panelState);
    selectors.filterPanel.innerHTML = `<div class="filter-panel__head"><p class="workspace-panel__code">FILTER WORKSPACE</p><button type="button" class="detail-close" data-filter-close aria-label="关闭筛选"><span aria-hidden="true">×</span><span class="detail-close__back">返回结果</span></button></div><h2>筛选资料</h2><p class="filter-workspace-summary" data-filter-workspace-summary></p><div class="active-filter-strip filter-workspace-active" data-filter-workspace-active hidden></div><button type="button" class="text-button filter-workspace-clear" data-filter-workspace-clear>清除全部筛选</button><label class="filter-option-search"><span class="sr-only">搜索筛选选项</span><input type="search" data-filter-option-search placeholder="搜索选项名称"></label>`;
    const result = archive.applyPipeline(records, panelState);
    const workspaceSummary = selectors.filterPanel.querySelector("[data-filter-workspace-summary]");
    if (workspaceSummary) workspaceSummary.textContent = `当前结果 ${result.total} 部`;
    const workspaceActive = selectors.filterPanel.querySelector("[data-filter-workspace-active]");
    const activeValues = [
      ...(state.query ? [{ label: `搜索：${state.query}`, type: "query", value: state.query }] : []),
      ...panelFilters.sources.map((value) => ({ label: `来源：${archive.sourceLabel(value)}`, type: "sources", value })),
      ...panelFilters.tags.map((value) => ({ label: `标签：${value}`, type: "tags", value })),
      ...panelFilters.sections.map((value) => ({ label: `分区：${archive.appearanceLabel(value)}`, type: "sections", value })),
    ];
    if (workspaceActive) {
      workspaceActive.hidden = activeValues.length === 0;
      activeValues.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "active-filter";
        button.textContent = `${item.label} ×`;
        button.addEventListener("click", () => {
          if (item.type === "query") {
            state.query = "";
            if (selectors.search) selectors.search.value = "";
          } else {
            panelFilters[item.type] = panelFilters[item.type].filter((value) => value !== item.value);
          }
          if (!state.draftFilters) {
            state.page = 1;
            state.workspaceMode = "filter";
            render();
          }
          renderFilterPanel();
        });
        workspaceActive.append(button);
      });
    }
    const workspaceClear = selectors.filterPanel.querySelector("[data-filter-workspace-clear]");
    if (workspaceClear) {
      workspaceClear.disabled = activeValues.length === 0;
      workspaceClear.addEventListener("click", () => {
        state.query = "";
        state.filterOptionQuery = "";
        if (state.draftFilters) {
          state.draftFilters = { sources: [], tags: [], sections: [] };
        } else {
          state.filters = { sources: [], tags: [], sections: [] };
        }
        if (selectors.search) selectors.search.value = "";
        state.workspaceMode = "filter";
        renderFilterPanel();
        if (!state.draftFilters) {
          state.page = 1;
          clearSelection(true);
          render();
        }
      });
    }
    const optionSearch = selectors.filterPanel.querySelector("[data-filter-option-search]");
    if (optionSearch) optionSearch.value = state.filterOptionQuery;
    const applyOptionQuery = () => {
      const query = archive.normalize(state.filterOptionQuery);
      selectors.filterPanel.querySelectorAll("[data-filter-option]").forEach((option) => {
        const selected = option.querySelector("input")?.checked === true;
        option.hidden = Boolean(
          query
          && option.dataset.filterOption?.includes(query) === false
          && !selected
        );
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
      values.forEach((option) => {
        const { value, label: optionLabel, count, selected } = option;
        const label = document.createElement("label");
        label.className = "filter-option";
        const shown = optionLabel;
        label.dataset.filterOption = archive.normalize(shown);
        label.dataset.filterOptionCount = String(count);
        label.classList.toggle("is-selected", selected);
        const input = document.createElement("input");
        input.type = "checkbox";
        input.dataset.filterGroup = group;
        input.dataset.filterValue = value;
        input.checked = selected;
        input.setAttribute("aria-label", shown);
        input.addEventListener("change", () => {
          const target = state.draftFilters || state.filters;
          target[group] = input.checked ? [...target[group], value] : target[group].filter((item) => item !== value);
          if (!state.draftFilters) {
            state.page = 1;
            state.workspaceMode = "filter";
            render();
          }
          renderFilterPanel();
          const replacement = [...selectors.filterPanel.querySelectorAll("[data-filter-group]")]
            .find((candidate) => candidate.dataset.filterGroup === group
              && candidate.dataset.filterValue === value);
          (replacement || selectors.filterPanel.querySelector("[data-filter-option-search]"))?.focus();
        });
        const countNode = document.createElement("span");
        countNode.className = "filter-option__count";
        countNode.textContent = String(count);
        label.append(input, document.createTextNode(shown), countNode);
        fieldset.append(label);
      });
      selectors.filterPanel.append(fieldset);
    });
    applyOptionQuery();
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "filter-apply-mobile button button--ink";
    apply.textContent = `返回结果 · ${result.total} 部`;
    apply.setAttribute("aria-label", `返回结果，当前 ${result.total} 部`);
    apply.addEventListener("click", () => closeFilterAndRestoreFocus(true));
    selectors.filterPanel.append(apply);
    selectors.filterPanel.querySelector("[data-filter-close]")?.addEventListener(
      "click",
      () => closeFilterAndRestoreFocus(false),
    );
  }

  function bindControls() {
    let closeSortPopover = () => {};
    const pageSize = root.querySelector("[data-page-size]");
    if (pageSize && window.BsbListbox) {
      pageSizeControl = window.BsbListbox.create(pageSize, {
        label: "每页数量",
        options: archive.PAGE_SIZES.map((size) => ({ value: String(size), label: String(size) })),
        value: String(state.pageSize),
        onChange: (value) => {
          state.pageSize = archive.writePageSize(value);
          state.page = 1;
          clearSelection(true);
          render();
          pageSizeControl?.setValue(String(state.pageSize));
        },
      });
    }
    selectors.search?.addEventListener("input", () => archive.withBrowseTransition("search", () => { state.query = selectors.search.value; state.page = 1; clearSelection(true); render(); }));
    root.querySelectorAll("[data-media-mode]").forEach((button) => button.addEventListener("click", () => { closeSortPopover(); archive.withBrowseTransition("media", () => { state.media = button.dataset.mediaMode === "movie" ? "movie" : "tv"; discardFilterDraft(); archive.normalizeFiltersForMedia(state, records); state.page = 1; clearSelection(true); renderFilterPanel(); render(); }); }));
    root.querySelectorAll("[data-view-mode]").forEach((button) => button.addEventListener("click", () => { closeSortPopover(); archive.withBrowseTransition("view-mode", () => { state.viewMode = archive.writeViewMode(button.dataset.viewMode); render(); }); button.focus(); }));
    root.querySelector("[data-filter-toggle]")?.addEventListener("click", () => {
      closeSortPopover();
      if (state.workspaceMode === "filter") closeFilterAndRestoreFocus(false);
      else {
        if (window.innerWidth < 768) state.listScrollTop = window.scrollY;
        beginFilterDraft();
        state.workspaceMode = "filter";
      }
      renderFilterPanel();
      render();
    });
    const sortButton = root.querySelector("[data-sort-toggle]");
    closeSortPopover = (restoreFocus = false) => {
      if (!selectors.sortPopover || selectors.sortPopover.hidden) return;
      selectors.sortPopover.hidden = true;
      archive.clearPopoverPosition(selectors.sortPopover);
      sortButton?.setAttribute("aria-expanded", "false");
      if (restoreFocus) sortButton?.focus();
    };
    const openSortPopover = (focusOption = false) => {
      if (!selectors.sortPopover) return;
      selectors.sortPopover.hidden = false;
      sortButton?.setAttribute("aria-expanded", "true");
      selectors.sortPopover.replaceChildren(...Object.entries(archive.SORTS).map(([value, label]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.setAttribute("role", "menuitemradio");
        button.textContent = label;
        button.setAttribute("aria-checked", String(value === state.sort));
        button.addEventListener("click", () => {
          archive.withBrowseTransition("sort", () => {
            state.sort = value;
            state.page = 1;
            closeSortPopover(true);
            clearSelection(true);
            render();
          });
        });
        return button;
      }));
      archive.positionPopover(selectors.sortPopover, sortButton);
      if (focusOption) selectors.sortPopover.querySelector('[aria-checked="true"]')?.focus();
    };
    selectors.sortPopover?.addEventListener("keydown", (event) => {
      const items = [...selectors.sortPopover.querySelectorAll('[role="menuitemradio"]')];
      if (!items.length) return;
      const currentIndex = Math.max(0, items.indexOf(document.activeElement));
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const offset = event.key === "ArrowDown" ? 1 : -1;
        items[(currentIndex + offset + items.length) % items.length].focus();
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        items[event.key === "Home" ? 0 : items.length - 1].focus();
      } else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        document.activeElement?.click();
      } else if (event.key === "Escape") {
        event.preventDefault();
        closeSortPopover(true);
      }
    });
    sortButton?.addEventListener("click", (event) => {
      if (!selectors.sortPopover) return;
      if (selectors.sortPopover.hidden) openSortPopover(event.detail === 0);
      else closeSortPopover(true);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && selectors.sortPopover && !selectors.sortPopover.hidden) {
        event.preventDefault();
        closeSortPopover(true);
      }
    });
    document.addEventListener("pointerdown", (event) => {
      if (selectors.sortPopover && !selectors.sortPopover.hidden && !selectors.sortPopover.contains(event.target) && event.target !== sortButton) {
        closeSortPopover();
      }
    });
    document.addEventListener("bsb-quarter-sheet-open", () => closeSortPopover());
    document.addEventListener("bsb-mobile-menu-open", () => closeSortPopover());
    window.addEventListener("scroll", () => closeSortPopover(), { passive: true });
    root.querySelector("[data-clear-all]")?.addEventListener("click", () => archive.withBrowseTransition("clear-filter", () => { state.query = ""; state.filterOptionQuery = ""; state.filters = { sources: [], tags: [], sections: [] }; if (selectors.search) selectors.search.value = ""; state.page = 1; clearSelection(true); render(); }));
    root.querySelectorAll("[data-scope-choice]").forEach((button) => button.addEventListener("click", () => {
      closeSortPopover();
      const kind = button.dataset.scopeChoice;
      setTab(kind);
      if (kind === "quarter") { state.scope = { kind: "quarter", value: "" }; selectors.browser.hidden = true; return; }
      if (kind === "year") {
        const value = yearControl?.getValue() || String(index?.latest_quarter || "").slice(0, 4);
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
    window.addEventListener("resize", () => render());
    bindDetailGesture();
  }

  async function setScope(kind, value, push = true) {
    if (!index) return;
    const normalized = kind === "range" ? normalRange(value.from, value.to) : String(value);
    if (kind === "range" && !normalized) return;
    state.scope = { kind, value: normalized };
    state.page = 1;
    state.query = "";
    state.filters = { sources: [], tags: [], sections: [] };
    discardFilterDraft();
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
      if (!entrancePlayed) {
        entrancePlayed = true;
        archive.playEntranceStagger(root);
      }
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
