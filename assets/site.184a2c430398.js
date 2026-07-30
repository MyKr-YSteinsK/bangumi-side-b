(() => {
  "use strict";

  if ("scrollRestoration" in history) history.scrollRestoration = "manual";

  const detailReturn = document.querySelector("[data-detail-return]");
  const fromQuarter = new URLSearchParams(window.location.search).get("from");
  if (detailReturn && /^\d{4}-(01|04|07|10)$/.test(fromQuarter || "")) {
    detailReturn.href = `../../quarters/${fromQuarter}/index.html`;
    detailReturn.textContent = `返回 ${fromQuarter} 档`;
  }
  const aliasToggle = document.querySelector("[data-toggle-aliases]");
  if (aliasToggle) {
    aliasToggle.addEventListener("click", () => {
      const expanded = aliasToggle.getAttribute("aria-expanded") === "true";
      document.querySelectorAll("[data-extra-alias]").forEach((item) => {
        item.hidden = expanded;
      });
      aliasToggle.setAttribute("aria-expanded", String(!expanded));
      aliasToggle.textContent = expanded ? "展开全部别名" : "收起别名";
    });
  }
  const episodeToggle = document.querySelector("[data-toggle-episodes]");
  if (episodeToggle) {
    episodeToggle.addEventListener("click", () => {
      const expanded = episodeToggle.getAttribute("aria-expanded") === "true";
      document.querySelectorAll("[data-extra-episode]").forEach((item) => {
        item.hidden = expanded;
      });
      episodeToggle.setAttribute("aria-expanded", String(!expanded));
      episodeToggle.textContent = expanded ? "展开剩余章节" : "收起章节";
    });
  }

  const cards = Array.from(document.querySelectorAll(".subject-card"));
  const drawer = document.querySelector("#subject-drawer");
  const dataNode = document.querySelector("#quarter-subject-data");
  if (!cards.length || !drawer || !dataNode) return;

  const searchInput = document.querySelector("[data-search-input]");
  const sortSelect = document.querySelector("[data-sort-select]");
  const clearButton = document.querySelector("[data-clear-filters]");
  const visibleCount = document.querySelector("[data-visible-count]");
  const noResults = document.querySelector("[data-no-results]");
  const drawerContent = drawer.querySelector("[data-drawer-content]");
  const selections = {
    source: new Set(),
    tag: new Set(),
    format: new Set(),
    section: new Set(),
  };
  let records = {};
  let opener = null;
  let closingFromHistory = false;

  try {
    records = JSON.parse(dataNode.textContent || "{}");
  } catch {
    return;
  }

  const normalized = (value) => value.normalize("NFKC").trim().toLocaleLowerCase();
  const values = (card, key) => (card.dataset[key] || "").split("|").filter(Boolean);
  const matchesDimension = (card, group, key) => {
    if (!selections[group].size) return true;
    return values(card, key).some((value) => selections[group].has(value));
  };

  function applyFilters() {
    const term = normalized(searchInput.value || "");
    let count = 0;
    for (const card of cards) {
      const visible =
        (!term || (card.dataset.search || "").includes(term)) &&
        matchesDimension(card, "source", "sources") &&
        matchesDimension(card, "tag", "tags") &&
        matchesDimension(card, "format", "format") &&
        matchesDimension(card, "section", "section");
      card.hidden = !visible;
      if (visible) count += 1;
    }
    for (const section of document.querySelectorAll(".archive-section")) {
      const sectionCards = section.querySelectorAll(".subject-card:not([hidden])");
      section.hidden = !sectionCards.length;
      const sectionCount = section.querySelector("[data-section-visible]");
      if (sectionCount) sectionCount.textContent = String(sectionCards.length);
    }
    visibleCount.textContent = String(count);
    noResults.hidden = count !== 0;
    clearButton.hidden = !(
      term || Object.values(selections).some((selection) => selection.size)
    );
  }

  function applySort() {
    const key = sortSelect.value;
    for (const grid of document.querySelectorAll(".subject-grid")) {
      const ordered = Array.from(grid.querySelectorAll(".subject-card")).sort(
        (left, right) => Number(left.dataset[key]) - Number(right.dataset[key]),
      );
      ordered.forEach((card) => grid.append(card));
    }
  }

  function applyAll() {
    applySort();
    applyFilters();
  }

  function archiveState() {
    return {
      search: searchInput.value,
      sort: sortSelect.value,
      filters: Object.fromEntries(
        Object.entries(selections).map(([group, selection]) => [group, [...selection]]),
      ),
      scrollY: window.scrollY,
    };
  }

  function saveArchiveState() {
    const state = { ...(history.state || {}), bsbArchive: archiveState() };
    delete state.bsbDrawer;
    history.replaceState(state, "", window.location.pathname + window.location.search);
  }

  function restoreArchiveState(state) {
    if (!state || !state.bsbArchive) return;
    const archive = state.bsbArchive;
    searchInput.value = archive.search || "";
    sortSelect.value = archive.sort || "score-desc";
    Object.entries(selections).forEach(([group, selection]) => {
      selection.clear();
      (archive.filters?.[group] || []).forEach((value) => selection.add(value));
    });
    document.querySelectorAll(".filter-chip[data-filter-value]").forEach((chip) => {
      const group = chip.closest("[data-filter-group]").dataset.filterGroup;
      const selected = selections[group].has(chip.dataset.filterValue);
      chip.classList.toggle("is-selected", selected);
      chip.setAttribute("aria-pressed", String(selected));
    });
    applyAll();
    requestAnimationFrame(() => window.scrollTo(0, archive.scrollY || 0));
  }

  function tagList(values, className) {
    const list = document.createElement("ul");
    list.className = className;
    for (const value of values) {
      const item = document.createElement("li");
      item.textContent = typeof value === "string" ? value : value.name || value.source;
      list.append(item);
    }
    return list;
  }

  function fact(label, value) {
    if (value === null || value === undefined || value === "") return null;
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = value;
    row.append(term, detail);
    return row;
  }

  function renderDrawer(record) {
    drawerContent.replaceChildren();
    if (record.cover_href) {
      const image = document.createElement("img");
      image.className = "subject-drawer__cover";
      image.src = record.cover_href;
      image.alt = `${record.preferred_title} 封面`;
      image.loading = "eager";
      drawerContent.append(image);
    }
    const eyebrow = document.createElement("p");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = "QUICK RECORD";
    const title = document.createElement("h2");
    title.id = "drawer-title";
    title.textContent = record.preferred_title;
    drawerContent.append(eyebrow, title);
    if (record.original_title) {
      const original = document.createElement("p");
      original.className = "subject-drawer__original";
      original.textContent = record.original_title;
      drawerContent.append(original);
    }
    if (record.aliases.length) drawerContent.append(tagList(record.aliases, "alias-list"));
    if (record.summary) {
      const summary = document.createElement("p");
      summary.className = "subject-drawer__summary";
      summary.textContent = record.summary;
      drawerContent.append(summary);
    }
    const facts = document.createElement("dl");
    facts.className = "drawer-facts";
    const format = record.media_format === "tv" ? "TV" : "剧场版";
    const episodes = record.declared_episode_count ?? record.total_episode_count;
    [
      fact("形式", format),
      fact("集数", episodes === null ? null : `${episodes} 集`),
      fact("首播", record.air_date),
      fact("完结", record.end_date),
      fact("评分", record.rating_score === null ? "暂无评分" : record.rating_score),
      fact("评分人数", record.rating_count),
    ].filter(Boolean).forEach((row) => facts.append(row));
    drawerContent.append(facts);
    if (record.sources.length) drawerContent.append(tagList(record.sources, "drawer-tags"));
    if (record.tags.length) drawerContent.append(tagList(record.tags, "drawer-tags"));
    const external = document.createElement("a");
    external.className = "text-link";
    external.href = record.bangumi_href;
    external.target = "_blank";
    external.rel = "noopener noreferrer";
    external.textContent = "在 Bangumi 查看";
    drawerContent.append(external);
    if (record.detail_href) {
      const detail = document.createElement("a");
      detail.className = "text-link";
      detail.href = record.detail_href;
      detail.dataset.detailLink = "";
      detail.textContent = "完整资料";
      drawerContent.append(document.createTextNode(" · "), detail);
    }
  }

  function openDrawer(subjectId, button) {
    const record = records[subjectId];
    if (!record) return;
    opener = button;
    renderDrawer(record);
    const state = { ...(history.state || {}), bsbDrawer: subjectId };
    if (drawer.open) {
      history.replaceState(state, "", `#subject-${subjectId}`);
    } else {
      history.pushState(state, "", `#subject-${subjectId}`);
      drawer.showModal();
    }
    drawer.querySelector("[data-close-drawer]").focus();
  }

  function closeDrawer() {
    if (drawer.open) drawer.close();
  }

  document.addEventListener("click", (event) => {
    const openButton = event.target.closest("[data-open-drawer]");
    if (openButton) {
      openDrawer(openButton.dataset.openDrawer, openButton);
      return;
    }
    const detailLink = event.target.closest("[data-detail-link]");
    if (detailLink) {
      saveArchiveState();
      return;
    }
    const chip = event.target.closest(".filter-chip[data-filter-value]");
    if (chip) {
      const group = chip.closest("[data-filter-group]").dataset.filterGroup;
      const selected = selections[group];
      const value = chip.dataset.filterValue;
      if (selected.has(value)) selected.delete(value); else selected.add(value);
      chip.classList.toggle("is-selected", selected.has(value));
      chip.setAttribute("aria-pressed", String(selected.has(value)));
      applyFilters();
    }
  });

  searchInput.addEventListener("input", applyFilters);
  sortSelect.addEventListener("change", applyAll);
  clearButton.addEventListener("click", () => {
    searchInput.value = "";
    Object.values(selections).forEach((selection) => selection.clear());
    document.querySelectorAll(".filter-chip.is-selected").forEach((chip) => {
      chip.classList.remove("is-selected");
      chip.setAttribute("aria-pressed", "false");
    });
    applyFilters();
  });
  drawer.querySelector("[data-close-drawer]").addEventListener("click", closeDrawer);
  drawer.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDrawer();
  });
  drawer.addEventListener("close", () => {
    if (!closingFromHistory && history.state && history.state.bsbDrawer) history.back();
    if (opener) opener.focus();
  });
  drawer.addEventListener("click", (event) => {
    if (event.target === drawer) closeDrawer();
  });
  drawer.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const focusable = Array.from(drawer.querySelectorAll("button, a[href]"));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  window.addEventListener("popstate", (event) => {
    if (drawer.open) {
      closingFromHistory = true;
      drawer.close();
      closingFromHistory = false;
    }
    restoreArchiveState(event.state);
  });

  applyAll();
  const navigation = performance.getEntriesByType("navigation")[0];
  if (navigation?.type !== "reload") restoreArchiveState(history.state);
  document.documentElement.classList.add("js-ready");
})();
