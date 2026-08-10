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
