const ASSET_VERSION =
  document.querySelector('meta[name="asset-version"]')?.content || "dev";

const state = {
  live: null,
  hotQueries: [],
  suggestions: [],
  currentSelection: null,
  baseSnapshot: null,
  currentSnapshot: null,
  currentBranchIndex: 0,
  activeSuggestionIndex: -1,
  searchTimer: null,
  lastMarker: "",
  lastSearchQuery: "",
  pendingRefresh: false,
  isSearching: false,
  isLoadingSnapshot: false,
  isRefreshing: false,
  controllers: {
    search: null,
    snapshot: null,
    status: null,
  },
  retryTimers: {
    search: null,
    snapshot: null,
    status: null,
  },
  retryCounts: {
    search: 0,
    snapshot: 0,
    status: 0,
  },
  lifecycle: {
    hiddenAt: 0,
    lastResumeAt: 0,
  },
};

function getInitialQueryFromUrl() {
  const params = new URLSearchParams(window.location.search || "");
  return String(params.get("q") || "").trim();
}

function replaceController(key) {
  state.controllers[key]?.abort();
  const controller = new AbortController();
  state.controllers[key] = controller;
  return controller;
}

function releaseController(key, controller) {
  if (state.controllers[key] === controller) {
    state.controllers[key] = null;
  }
}

function clearRetryTimer(key) {
  const timer = state.retryTimers[key];
  if (timer) {
    window.clearTimeout(timer);
    state.retryTimers[key] = null;
  }
}

function resetRetry(key) {
  clearRetryTimer(key);
  state.retryCounts[key] = 0;
}

function markHidden() {
  state.lifecycle.hiddenAt = Date.now();
}

function markResumed() {
  state.lifecycle.lastResumeAt = Date.now();
}

function recentlyResumed(windowMs = 4000) {
  const lastResumeAt = Number(state.lifecycle.lastResumeAt || 0);
  return Boolean(lastResumeAt) && Date.now() - lastResumeAt <= windowMs;
}

function isVisibleDocument() {
  return document.visibilityState === "visible";
}

function abortAllControllers() {
  Object.keys(state.controllers).forEach((key) => {
    state.controllers[key]?.abort();
    state.controllers[key] = null;
  });
}

function isTransientNetworkError(error) {
  if (!error || isAbortError(error)) return false;
  if (error instanceof TypeError) return true;
  const message = String(error?.message || "").toLowerCase();
  return (
    message.includes("failed to fetch") ||
    message.includes("load failed") ||
    message.includes("network") ||
    message.includes("networkerror") ||
    message.includes("network request failed") ||
    message.includes("the network connection was lost")
  );
}

function shouldQuietTransientError(error) {
  return isTransientNetworkError(error) && (!navigator.onLine || !isVisibleDocument() || recentlyResumed());
}

function scheduleRetry(key, action, { delay = 900, maxAttempts = 2 } = {}) {
  if (state.retryCounts[key] >= maxAttempts) return false;
  clearRetryTimer(key);
  state.retryCounts[key] += 1;
  state.retryTimers[key] = window.setTimeout(() => {
    state.retryTimers[key] = null;
    if (!isVisibleDocument()) return;
    action().catch(() => {});
  }, delay);
  return true;
}

function scheduleResumeSync({ delay = 240, refreshCurrent = true } = {}) {
  clearRetryTimer("status");
  state.retryTimers.status = window.setTimeout(() => {
    state.retryTimers.status = null;
    if (!isVisibleDocument()) return;
    if (state.isSearching || state.isLoadingSnapshot) return;
    loadStatus({ refreshCurrent: refreshCurrent && Boolean(state.currentSelection) }).catch(() => {});
  }, delay);
}

async function api(path, { signal } = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    signal,
    headers: {
      "X-Asset-Version": ASSET_VERSION,
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `请求失败：${response.status}`);
  }
  return payload;
}

function isAbortError(error) {
  return error?.name === "AbortError";
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatMoney(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number <= 0) return "--";
  return `¥${number.toLocaleString("zh-CN")}`;
}

function formatTime(value) {
  const text = String(value || "").trim();
  if (!text) return "--";
  return text.length >= 16 ? text.slice(5, 16) : text;
}

function firstText(...values) {
  for (const value of values) {
    const text = String(value || "").trim();
    if (text) return text;
  }
  return "";
}

function numberValue(...values) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return null;
}

function integerText(value, fallback = "--") {
  const number = numberValue(value);
  if (number === null) return fallback;
  return Math.round(number).toLocaleString("zh-CN");
}

function formatRange(value) {
  return firstText(value, "--");
}

function normalizeSearchSurface(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .normalize("NFKC")
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function compactSearchSurface(value) {
  return normalizeSearchSurface(value).replace(/\s+/g, "");
}

function extractIdentityVariants(value) {
  const variants = new Set();
  const normalized = normalizeSearchSurface(value);
  const compact = compactSearchSurface(value);
  if (normalized) variants.add(normalized);
  if (compact) variants.add(compact);
  return [...variants];
}

function currentSelectionIdentityTokens() {
  const values = new Set();
  [
    state.currentSelection?.label,
    state.currentSelection?.model_title,
    state.currentSelection?.family_title,
    state.baseSnapshot?.header?.title,
  ].forEach((value) => {
    extractIdentityVariants(value).forEach((item) => values.add(item));
  });
  return [...values];
}

function isCurrentSelectionQuery(query) {
  const normalized = normalizeSearchSurface(query);
  const compact = compactSearchSurface(query);
  if (!normalized && !compact) return false;
  const tokens = currentSelectionIdentityTokens();
  return tokens.includes(normalized) || tokens.includes(compact);
}

function refinementState(snapshot) {
  const refinement = snapshot?.resolution?.refinement;
  return refinement && typeof refinement === "object" ? refinement : {};
}

function toArray(value) {
  return Array.isArray(value) ? value : [];
}

function formatPrimaryDisplay(group) {
  const range = formatRange(group?.price_range);
  if (range !== "--") return range;
  return formatMoney(group?.market_price);
}

function capacitySortKey(label) {
  const text = String(label || "")
    .trim()
    .toUpperCase()
    .replace(/\s+/g, "")
    .replace(/GB/g, "G")
    .replace(/TB/g, "T");
  const match = text.match(/^(?:(\d+)\+)?(\d+)(G|T)$/);
  if (!match) return [Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER];
  const memory = Number(match[1] || 0);
  const storage = Number(match[2] || 0) * (match[3] === "T" ? 1024 : 1);
  return [memory, storage];
}

function sortCapacityGroups(groups = []) {
  return [...groups].sort((left, right) => {
    const leftMatchScore = Number(left?.__matchScore || 0);
    const rightMatchScore = Number(right?.__matchScore || 0);
    if (leftMatchScore !== rightMatchScore) return rightMatchScore - leftMatchScore;
    const [leftMemory, leftStorage] = capacitySortKey(left.capacity_label);
    const [rightMemory, rightStorage] = capacitySortKey(right.capacity_label);
    if (leftMemory !== rightMemory) return leftMemory - rightMemory;
    if (leftStorage !== rightStorage) return leftStorage - rightStorage;
    return String(left.capacity_label || "").localeCompare(String(right.capacity_label || ""), "zh-CN", {
      numeric: true,
    });
  });
}

function renderLiveStatus() {
  const host = document.getElementById("liveTimestamp");
  if (!host) return;
  host.textContent = state.live
    ? formatTime(state.live.latest_imported_at || state.live.latest_task_event)
    : "--";
}

function renderSearchStatus(message, tone = "") {
  const host = document.getElementById("searchStatus");
  if (!host) return;
  host.textContent = message;
  host.className = "search-status";
  if (tone) host.classList.add(`is-${tone}`);
}

function renderResultFeedback(message = "", tone = "") {
  const host = document.getElementById("resultFeedback");
  if (!host) return;
  host.hidden = !message;
  host.textContent = message;
  host.className = "result-feedback";
  if (tone) host.classList.add(`is-${tone}`);
}

function setResultEmpty(message = "", tone = "") {
  const host = document.getElementById("resultEmpty");
  if (!host) return;
  host.hidden = !message;
  host.textContent = message;
  host.className = "result-empty";
  if (tone) host.classList.add(`is-${tone}`);
}

function renderRecovery({ message = "", actions = [] } = {}) {
  const wrap = document.getElementById("resultRecovery");
  const copy = document.getElementById("recoveryCopy");
  const host = document.getElementById("recoveryActions");
  if (!wrap || !copy || !host) return;

  const visible = Boolean(message || actions.length);
  wrap.hidden = !visible;
  copy.textContent = message;
  host.innerHTML = actions
    .map((action, index) => {
      const label = escapeHtml(action.label);
      const query = escapeHtml(action.query || "");
      const flavor = action.kind === "primary" ? "is-primary" : "is-secondary";
      return `
        <button
          class="recovery-chip ${flavor}"
          data-recovery-index="${index}"
          data-recovery-query="${query}"
          type="button"
        >
          ${label}
        </button>
      `;
    })
    .join("");
}

function buildRecoveryActions({ includeRetry = false } = {}) {
  const actions = [];
  const seen = new Set();

  if (includeRetry && state.lastSearchQuery) {
    actions.push({
      label: `重试：${state.lastSearchQuery}`,
      query: state.lastSearchQuery,
      kind: "primary",
    });
    seen.add(state.lastSearchQuery);
  }

  for (const item of state.hotQueries) {
    const label = item?.label || item?.model_title || item?.series_title || "";
    if (!label || seen.has(label)) continue;
    actions.push({
      label,
      query: label,
      kind: "secondary",
    });
    seen.add(label);
    if (actions.length >= 5) break;
  }

  return actions;
}

function syncBusyState() {
  const searchBar = document.getElementById("searchBar");
  const searchInput = document.getElementById("searchInput");
  const queryBtn = document.getElementById("queryBtn");
  const resultCard = document.getElementById("resultCard");
  const refreshBtn = document.getElementById("refreshBtn");
  const searchShell = document.getElementById("searchShell");

  const searchBusy = state.isSearching || state.isLoadingSnapshot;
  searchBar?.classList.toggle("is-busy", searchBusy);
  searchShell?.classList.toggle("is-open", state.suggestions.length > 0);
  resultCard?.classList.toggle("is-busy", state.isLoadingSnapshot);
  resultCard?.setAttribute("aria-busy", state.isLoadingSnapshot ? "true" : "false");

  if (searchInput) searchInput.setAttribute("aria-busy", searchBusy ? "true" : "false");
  if (queryBtn) {
    queryBtn.disabled = searchBusy;
    queryBtn.textContent = searchBusy ? "查询中…" : "立即查询";
  }
  if (refreshBtn) {
    refreshBtn.disabled = state.isRefreshing || state.isLoadingSnapshot;
    refreshBtn.textContent = state.isRefreshing ? "同步中…" : "同步最新";
    refreshBtn.classList.toggle("is-attention", state.pendingRefresh);
  }
}

function highlightLabel(label, query) {
  const source = String(label || "");
  const tokens = String(query || "")
    .trim()
    .split(/\s+/)
    .map((token) => token.toLowerCase())
    .filter((token) => token.length >= 2);

  if (!tokens.length) return escapeHtml(source);
  const lowerSource = source.toLowerCase();
  const token = tokens.find((candidate) => lowerSource.includes(candidate));
  if (!token) return escapeHtml(source);

  const start = lowerSource.indexOf(token);
  const end = start + token.length;
  return [
    escapeHtml(source.slice(0, start)),
    `<mark>${escapeHtml(source.slice(start, end))}</mark>`,
    escapeHtml(source.slice(end)),
  ].join("");
}

function getSuggestionMeta(item) {
  return [item.brand_title, item.series_title].filter(Boolean).join(" / ");
}

function syncSuggestionState({ scrollIntoView = false } = {}) {
  const host = document.getElementById("suggestionList");
  const input = document.getElementById("searchInput");
  const shell = document.getElementById("searchShell");
  if (!host || !input) return;

  const options = [...host.querySelectorAll("[data-suggest-index]")];
  const hasSuggestions = options.length > 0;
  host.classList.toggle("is-open", hasSuggestions);
  shell?.classList.toggle("is-open", hasSuggestions);
  input.setAttribute("aria-expanded", hasSuggestions ? "true" : "false");

  let activeId = "";
  options.forEach((option, index) => {
    const active = index === state.activeSuggestionIndex;
    option.classList.toggle("is-active", active);
    option.setAttribute("aria-selected", active ? "true" : "false");
    if (active) {
      activeId = option.id;
      if (scrollIntoView) {
        option.scrollIntoView({ block: "nearest" });
      }
    }
  });
  input.setAttribute("aria-activedescendant", activeId);
}

function closeSuggestions() {
  state.suggestions = [];
  state.activeSuggestionIndex = -1;
  const host = document.getElementById("suggestionList");
  if (host) host.innerHTML = "";
  syncSuggestionState();
}

function renderSuggestions() {
  const host = document.getElementById("suggestionList");
  if (!host) return;

  if (!state.suggestions.length) {
    host.innerHTML = "";
    syncSuggestionState();
    return;
  }

  const query = document.getElementById("searchInput")?.value.trim() || state.lastSearchQuery;
  const suggestionItems = state.suggestions
    .slice(0, 6)
    .map((item, index) => {
      const active = index === state.activeSuggestionIndex;
      return `
        <button
          id="suggestion-option-${index}"
          class="suggestion-item ${active ? "is-active" : ""}"
          data-suggest-index="${index}"
          type="button"
          role="option"
          aria-selected="${active ? "true" : "false"}"
        >
          <span class="suggestion-main">
            <span class="suggestion-title">${highlightLabel(item.label, query)}</span>
            <span class="suggestion-meta">${escapeHtml(getSuggestionMeta(item))}</span>
          </span>
          <span class="suggestion-support">点击查看</span>
        </button>
      `;
    })
    .join("");
  host.innerHTML = `
    <div class="suggestion-results">${suggestionItems}</div>
    <div class="suggestion-actions">
      <button class="suggestion-dismiss" data-suggest-close type="button">收起候选</button>
    </div>
  `;
  syncSuggestionState();
}

function setActiveSuggestion(index, { scrollIntoView = false } = {}) {
  if (!state.suggestions.length) {
    state.activeSuggestionIndex = -1;
    syncSuggestionState();
    return;
  }
  const clamped = Math.max(0, Math.min(index, state.suggestions.length - 1));
  state.activeSuggestionIndex = clamped;
  syncSuggestionState({ scrollIntoView });

  const active = state.suggestions[clamped];
  if (active) {
    renderSearchStatus(
      `已锁定 ${state.suggestions.length} 个候选，按 Enter 直接打开：${active.label}`,
      "ready",
    );
  }
}

function dismissSuggestions({ blurInput = false } = {}) {
  closeSuggestions();
  if (blurInput) {
    document.getElementById("searchInput")?.blur();
  }
  renderSearchStatus("候选结果已收起。");
}

function renderCapacityCard(group) {
  const rows = (group.colors || []).length
    ? group.colors
    : [
        {
          color_label: "默认规格",
          price_range: group.price_range,
        },
      ];
  return `
    <section class="capacity-card">
      <div class="capacity-head">
        <div class="capacity-head-copy">
          <div class="capacity-title">${escapeHtml(group.capacity_label || "默认规格")}</div>
          ${
            group.latest_imported_at
              ? `<div class="capacity-meta"><span>更新 ${escapeHtml(formatTime(group.latest_imported_at))}</span></div>`
              : ""
          }
        </div>
        <div class="capacity-price">${escapeHtml(formatPrimaryDisplay(group))}</div>
      </div>
      <div class="variant-list">
        ${rows
          .map(
            (color) => `
              <div class="variant-row ${color.__matched ? "is-match" : ""}">
                <div class="variant-copy">
                  <div class="variant-title">${escapeHtml(color.color_label || "标准规格")}</div>
                  <div class="variant-meta">${escapeHtml(
                    [
                      color.latest_imported_at ? `更新 ${formatTime(color.latest_imported_at)}` : "",
                    ]
                      .filter(Boolean)
                      .join(" · ") || "查看对应区间",
                  )}</div>
                </div>
                <span class="variant-range">${escapeHtml(formatRange(color.price_range || group.price_range))}</span>
              </div>
            `,
          )
          .join("")}
      </div>
    </section>
  `;
}

function renderBranchPanels(branches = []) {
  const wrap = document.getElementById("variantSection");
  const host = document.getElementById("variantPanels");
  if (!wrap || !host) return;

  if (!branches.length) {
    wrap.hidden = true;
    host.innerHTML = "";
    return;
  }

  wrap.hidden = false;

  if (branches.length === 1) {
    const groups = sortCapacityGroups(branches[0].capacity_groups || []);
    host.innerHTML = `
      <div class="section-head">
        <div>
          <div class="section-kicker">容量明细</div>
          <div class="section-title">${escapeHtml(branches[0].branch_title || "当前型号")}</div>
        </div>
        <div class="section-meta">${groups.length} 个容量</div>
      </div>
      <div class="capacity-grid">${groups.map((group) => renderCapacityCard(group)).join("")}</div>
    `;
    return;
  }

  state.currentBranchIndex = Math.max(0, Math.min(state.currentBranchIndex, branches.length - 1));
  const activeBranch = branches[state.currentBranchIndex];
  const groups = sortCapacityGroups(activeBranch.capacity_groups || []);

  host.innerHTML = `
    <div class="branch-switcher" role="tablist" aria-label="型号分支">
      ${branches
        .map((branch, index) => {
          const active = index === state.currentBranchIndex;
          return `
            <button
              class="branch-chip ${active ? "is-active" : ""}"
              data-branch-index="${index}"
              type="button"
              role="tab"
              aria-selected="${active ? "true" : "false"}"
              tabindex="${active ? "0" : "-1"}"
            >
              <span class="branch-chip-main">${escapeHtml(branch.branch_title || `分支 ${index + 1}`)}</span>
              <span class="branch-chip-sub">${escapeHtml((branch.capacity_groups || []).length ? `${branch.capacity_groups.length} 个容量` : "更多容量")}</span>
            </button>
          `;
        })
        .join("")}
    </div>
    <div class="section-head">
      <div>
        <div class="section-kicker">容量明细</div>
        <div class="section-title">${escapeHtml(activeBranch.branch_title || "当前型号")}</div>
      </div>
      <div class="section-meta">${groups.length} 个容量</div>
    </div>
    <div class="capacity-grid">
      ${groups.map((group) => renderCapacityCard(group)).join("")}
    </div>
  `;
}

function clearCurrentResultState() {
  state.currentSelection = null;
   state.baseSnapshot = null;
  state.currentSnapshot = null;
  state.currentBranchIndex = 0;
  state.pendingRefresh = false;
}

function renderEmptyState() {
  clearCurrentResultState();
  const overview = document.getElementById("resultOverview");
  if (overview) overview.hidden = true;
  renderResultFeedback("");
  setResultEmpty("输入型号后查看结果");
  renderBranchPanels([]);
  renderRecovery({
    message: state.hotQueries.length ? "先试试这些常用型号。" : "",
    actions: buildRecoveryActions(),
  });
  renderSearchStatus("支持口语、拼音和模糊输入。");
  syncBusyState();
}

function renderNoResult(message = "没有锁定到明确型号，建议换个更短或更接近口语的关键词再试。") {
  clearCurrentResultState();
  const overview = document.getElementById("resultOverview");
  if (overview) overview.hidden = true;
  renderSearchStatus(message, "warn");
  setResultEmpty("没有锁定到明确型号", "warn");
  renderResultFeedback(message, "warn");
  renderBranchPanels([]);
  renderRecovery({
    message: "你可以直接重试，或者先点这些常用型号继续看。",
    actions: buildRecoveryActions({ includeRetry: true }),
  });
  syncBusyState();
}

function renderSnapshot(snapshot, { announce = "" } = {}) {
  state.currentSnapshot = snapshot;
  state.currentBranchIndex = 0;
  state.pendingRefresh = false;
  setResultEmpty("");
  renderRecovery();
  const overview = document.getElementById("resultOverview");
  const title = document.getElementById("resultTitle");
  const subtitle = document.getElementById("resultSubtitle");
  const badge = document.getElementById("resultBadge");
  const headerTitle = snapshot?.header?.title || state.currentSelection?.label || "当前型号";
  const headerMeta = state.currentSelection?.meta || [state.currentSelection?.brand_title, state.currentSelection?.series_title].filter(Boolean).join(" / ");
  const branchCount = Array.isArray(snapshot?.branches) ? snapshot.branches.length : 0;
  const refinementSummary = String(snapshot?.refinementSummary || "").trim();
  const refinement = refinementState(snapshot);

  if (overview) overview.hidden = false;
  if (title) title.textContent = headerTitle;
  if (subtitle) {
    subtitle.textContent = headerMeta || "直接查看各容量和配色区间。";
  }
  if (badge) {
    badge.textContent = refinementSummary
      ? `已筛 ${refinementSummary}`
      : state.pendingRefresh
        ? "有更新"
        : branchCount > 1
          ? `${branchCount} 个分支`
          : "已同步";
  }
  renderBranchPanels(Array.isArray(snapshot?.branches) ? snapshot.branches : []);
  renderSearchStatus(
    refinementSummary ? `已在 ${headerTitle} 内继续筛选：${refinementSummary}` : `已打开：${headerTitle}`,
    "ready",
  );
  renderResultFeedback(
    announce || (refinement.applied ? `已按 ${refinementSummary || refinement.requested_query || "当前条件"} 收拢当前结果。` : ""),
    announce || refinementSummary ? "ready" : "",
  );
  syncBusyState();
}

function buildSnapshotQuery(selection, { refinementQuery = "" } = {}) {
  const queryRef =
    selection?.query_ref && typeof selection.query_ref === "object" ? selection.query_ref : null;
  const payload = queryRef
    ? {
        data_source: queryRef.data_source || selection?.data_source || "quote_rows",
        run_key: queryRef.run_key || "",
        brand_title: queryRef.brand_title || "",
        series_title: queryRef.series_title || "",
        model_title: queryRef.model_title || "",
        family_title: queryRef.family_title || "",
        group_title: queryRef.group_title || "",
        condition_bucket: queryRef.condition_bucket || "",
        external_key: queryRef.external_key || "",
        detail_key: queryRef.detail_key || selection?.detail_key || "",
      }
    : {
        data_source: selection?.data_source || "quote_rows",
        run_key: selection?.run_key || "",
        brand_title: selection?.brand_title || "",
        series_title: selection?.series_title || "",
        model_title: selection?.model_title || "",
        family_title: selection?.family_title || selection?.model_title || "",
        group_title: "",
        condition_bucket: selection?.condition_bucket || "",
        external_key: selection?.external_key || "",
        detail_key: selection?.detail_key || "",
      };
  if (refinementQuery) {
    payload.refinement_query = refinementQuery;
  }

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(payload)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  return params;
}

async function loadSnapshot(selection, { silent = false, announce = "" } = {}) {
  if (!selection) return;

  const searchInput = document.getElementById("searchInput");
  state.currentSelection = selection;
  state.isLoadingSnapshot = true;
  syncBusyState();
  closeSuggestions();
  const selectionLabel = firstText(selection.label, selection.model_title, selection.family_title, "当前型号");
  if (searchInput) searchInput.value = selectionLabel;
  renderResultFeedback(`正在打开 ${selectionLabel}…`, "loading");

  const controller = replaceController("snapshot");
  const params = buildSnapshotQuery(selection);

  try {
    const snapshot = await api(`/api/sku?${params.toString()}`, { signal: controller.signal });
    resetRetry("snapshot");
    state.baseSnapshot = snapshot;
    renderSnapshot(snapshot, { announce });
  } catch (error) {
    if (!isAbortError(error)) {
      if (shouldQuietTransientError(error)) {
        renderSearchStatus("网络恢复中，正在重新连接结果…", "loading");
        renderResultFeedback("网络刚恢复，稍后会自动重试。", "loading");
        if (scheduleRetry("snapshot", () => loadSnapshot(selection, { silent: true, announce }), { delay: 1000 })) {
          return;
        }
      }
      renderNoResult(error.message || "加载失败");
      if (!silent && !state.currentSnapshot) {
        setResultEmpty("暂时无法打开这个型号", "warn");
      }
    }
  } finally {
    state.isLoadingSnapshot = false;
    syncBusyState();
    releaseController("snapshot", controller);
  }
}

async function previewCurrentSnapshot(query, { announce = "" } = {}) {
  if (!state.currentSelection || !state.baseSnapshot) return false;
  if (!query || isCurrentSelectionQuery(query)) {
    closeSuggestions();
    renderSnapshot(state.baseSnapshot);
    return true;
  }

  state.isLoadingSnapshot = true;
  syncBusyState();
  const controller = replaceController("snapshot");
  const params = buildSnapshotQuery(state.currentSelection, { refinementQuery: query });

  try {
    const snapshot = await api(`/api/sku?${params.toString()}`, { signal: controller.signal });
    resetRetry("snapshot");
    const refinement = refinementState(snapshot);
    if (!refinement.applied) {
      return false;
    }
    closeSuggestions();
    renderSnapshot(snapshot, {
      announce: announce || `已按 ${refinement.summary || refinement.requested_query || query} 收拢当前结果。`,
    });
    return true;
  } catch (error) {
    if (!isAbortError(error)) {
      if (shouldQuietTransientError(error)) {
        renderSearchStatus("网络恢复中，正在重新连接结果…", "loading");
      }
    }
    return false;
  } finally {
    state.isLoadingSnapshot = false;
    syncBusyState();
    releaseController("snapshot", controller);
  }
}

async function loadStatus({ refreshCurrent = false } = {}) {
  state.isRefreshing = true;
  syncBusyState();
  const controller = replaceController("status");

  try {
    const payload = await api("/api/status", { signal: controller.signal });
    resetRetry("status");
    state.live = payload.live || null;
    state.hotQueries = payload.hot_queries || [];
    renderLiveStatus();

    const marker = [
      payload.live?.run_key || "",
      payload.live?.quote_count || 0,
      payload.live?.latest_imported_at || "",
      payload.live?.latest_run_event || payload.live?.latest_task_event || "",
    ].join("|");
    const markerChanged = Boolean(state.lastMarker) && marker !== state.lastMarker;
    state.lastMarker = marker;

    if (!state.currentSnapshot && !state.lastSearchQuery) {
      renderRecovery({
        message: state.hotQueries.length ? "试试这些常用型号。" : "",
        actions: buildRecoveryActions(),
      });
    }

    if (refreshCurrent && state.currentSelection) {
      const activeQuery = document.getElementById("searchInput")?.value.trim() || "";
      const shouldReapplyRefinement = Boolean(activeQuery) && !isCurrentSelectionQuery(activeQuery);
      await loadSnapshot(state.currentSelection, { silent: true, announce: "已同步最新结果" });
      if (shouldReapplyRefinement) {
        const searchInput = document.getElementById("searchInput");
        if (searchInput) searchInput.value = activeQuery;
        await previewCurrentSnapshot(activeQuery, { announce: "已同步最新结果，并重新应用当前筛选" });
      }
      return;
    }

    if (markerChanged && state.currentSelection) {
      state.pendingRefresh = true;
      renderResultFeedback("检测到新数据，点击“同步最新”即可刷新当前结果。", "info");
    }
  } catch (error) {
    if (!isAbortError(error)) {
      if (shouldQuietTransientError(error)) {
        renderSearchStatus("网络恢复中，状态会自动重新同步。", "loading");
        scheduleRetry("status", () => loadStatus({ refreshCurrent }), { delay: 1200, maxAttempts: 3 });
      } else {
        renderSearchStatus("状态同步失败，稍后可点“同步最新”重试。", "warn");
      }
    }
  } finally {
    state.isRefreshing = false;
    syncBusyState();
    releaseController("status", controller);
  }
}

async function performSearch({ selectFirst = false } = {}) {
  const searchInput = document.getElementById("searchInput");
  const query = searchInput?.value.trim() || "";
  state.lastSearchQuery = query;

  if (!query) {
    closeSuggestions();
    if (state.baseSnapshot && state.currentSelection) {
      renderSnapshot(state.baseSnapshot);
      renderSearchStatus("当前结果已保留，继续输入可重新搜索型号。");
      return;
    }
    renderEmptyState();
    return;
  }

  if (query.length < 2) {
    renderSearchStatus("继续输入更多关键词，会更快锁定对应型号。");
    return;
  }

  if (state.baseSnapshot && state.currentSelection && (await previewCurrentSnapshot(query))) {
    return;
  }

  state.isSearching = true;
  syncBusyState();
  renderSearchStatus("正在锁定最可能的型号…", "loading");
  const controller = replaceController("search");

  try {
    const payload = await api(`/api/search?q=${encodeURIComponent(query)}&limit=6`, { signal: controller.signal });
    resetRetry("search");
    state.suggestions = payload.results || [];

    if (!state.suggestions.length) {
      closeSuggestions();
      renderNoResult();
      return;
    }

    state.activeSuggestionIndex = 0;
    renderSuggestions();
    setActiveSuggestion(0);

    if (selectFirst) {
      await loadSnapshot(state.suggestions[0]);
    }
  } catch (error) {
    if (!isAbortError(error)) {
      if (shouldQuietTransientError(error)) {
        renderSearchStatus("网络恢复中，正在重新查找…", "loading");
        if (scheduleRetry("search", () => performSearch({ selectFirst }), { delay: 900 })) {
          return;
        }
      }
      renderNoResult(error.message || "搜索失败");
    }
  } finally {
    state.isSearching = false;
    syncBusyState();
    releaseController("search", controller);
  }
}

function commitActiveSuggestion() {
  if (state.activeSuggestionIndex < 0 || state.activeSuggestionIndex >= state.suggestions.length) return;
  loadSnapshot(state.suggestions[state.activeSuggestionIndex]);
}

function scheduleSearch() {
  if (state.searchTimer) window.clearTimeout(state.searchTimer);
  const query = document.getElementById("searchInput")?.value.trim() || "";
  const delay = query.length >= 5 ? 140 : 220;
  state.searchTimer = window.setTimeout(() => {
    performSearch({ selectFirst: false }).catch(() => {});
  }, delay);
}

function bindEvents() {
  const searchShell = document.getElementById("searchShell");
  const searchInput = document.getElementById("searchInput");
  const queryBtn = document.getElementById("queryBtn");
  const refreshBtn = document.getElementById("refreshBtn");
  const suggestionList = document.getElementById("suggestionList");
  const resultRecovery = document.getElementById("resultRecovery");
  const variantPanels = document.getElementById("variantPanels");

  searchInput.addEventListener("focus", () => {
    if (state.suggestions.length) {
      renderSuggestions();
    }
  });

  searchInput.addEventListener("input", () => {
    const query = searchInput.value.trim();
    state.lastSearchQuery = query;
    if (!query) {
      if (state.searchTimer) {
        window.clearTimeout(state.searchTimer);
        state.searchTimer = null;
      }
      closeSuggestions();
      if (state.baseSnapshot && state.currentSelection) {
        renderSnapshot(state.baseSnapshot);
        renderSearchStatus("当前结果已保留，继续输入可重新搜索型号。");
        return;
      }
      renderEmptyState();
      return;
    }
    if (state.baseSnapshot && state.currentSelection && isCurrentSelectionQuery(query)) {
      if (state.searchTimer) {
        window.clearTimeout(state.searchTimer);
        state.searchTimer = null;
      }
      closeSuggestions();
      renderSnapshot(state.baseSnapshot);
      return;
    }
    scheduleSearch();
  });

  searchInput.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!state.suggestions.length) return;
      setActiveSuggestion(state.activeSuggestionIndex + 1, { scrollIntoView: true });
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!state.suggestions.length) return;
      setActiveSuggestion(state.activeSuggestionIndex - 1, { scrollIntoView: true });
      return;
    }

    if (event.key === "Escape") {
      closeSuggestions();
      renderSearchStatus("候选结果已收起。");
      if (state.baseSnapshot && state.currentSelection) {
        renderSnapshot(state.baseSnapshot);
      }
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      if (state.suggestions.length) {
        commitActiveSuggestion();
      } else {
        performSearch({ selectFirst: true }).catch(() => {});
      }
    }
  });

  suggestionList.addEventListener("mouseover", (event) => {
    const button = event.target.closest("[data-suggest-index]");
    if (!button) return;
    const index = Number(button.dataset.suggestIndex);
    if (!Number.isFinite(index) || index === state.activeSuggestionIndex) return;
    setActiveSuggestion(index);
  });

  suggestionList.addEventListener("pointerdown", (event) => {
    const dismissButton = event.target.closest("[data-suggest-close]");
    if (dismissButton) {
      event.preventDefault();
      dismissSuggestions({ blurInput: true });
      return;
    }

    const button = event.target.closest("[data-suggest-index]");
    if (!button) return;
    event.preventDefault();
    const index = Number(button.dataset.suggestIndex);
    if (!Number.isFinite(index) || index < 0 || index >= state.suggestions.length) return;
    state.activeSuggestionIndex = index;
    commitActiveSuggestion();
  });

  suggestionList.addEventListener("click", (event) => {
    const dismissButton = event.target.closest("[data-suggest-close]");
    if (!dismissButton) return;
    event.preventDefault();
    dismissSuggestions({ blurInput: true });
  });

  queryBtn.addEventListener("click", () => {
    performSearch({ selectFirst: true }).catch(() => {});
  });

  refreshBtn.addEventListener("click", () => {
    loadStatus({ refreshCurrent: true }).catch(() => {});
  });

  resultRecovery.addEventListener("click", (event) => {
    const button = event.target.closest("[data-recovery-query]");
    if (!button) return;
    const query = button.dataset.recoveryQuery || "";
    if (!query) return;
    searchInput.value = query;
    state.lastSearchQuery = query;
    performSearch({ selectFirst: true }).catch(() => {});
  });

  variantPanels.addEventListener("click", (event) => {
    const button = event.target.closest("[data-branch-index]");
    if (!button || !state.currentSnapshot) return;
    const index = Number(button.dataset.branchIndex);
    if (!Number.isFinite(index) || index === state.currentBranchIndex) return;
    state.currentBranchIndex = index;
    renderBranchPanels(state.currentSnapshot.branches || []);
  });

  variantPanels.addEventListener("keydown", (event) => {
    const button = event.target.closest("[data-branch-index]");
    if (!button || !state.currentSnapshot) return;

    const total = (state.currentSnapshot.branches || []).length;
    let nextIndex = null;
    if (event.key === "ArrowRight") nextIndex = Math.min(state.currentBranchIndex + 1, total - 1);
    if (event.key === "ArrowLeft") nextIndex = Math.max(state.currentBranchIndex - 1, 0);
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = total - 1;
    if (nextIndex === null || nextIndex === state.currentBranchIndex) return;

    event.preventDefault();
    state.currentBranchIndex = nextIndex;
    renderBranchPanels(state.currentSnapshot.branches || []);
    variantPanels.querySelector(`[data-branch-index="${nextIndex}"]`)?.focus();
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      markHidden();
      abortAllControllers();
      return;
    }
    markResumed();
    scheduleResumeSync({ delay: 220 });
  });

  window.addEventListener("pageshow", (event) => {
    markResumed();
    if (event.persisted) {
      scheduleResumeSync({ delay: 120 });
      return;
    }
    scheduleResumeSync({ delay: 220 });
  });

  window.addEventListener("pagehide", () => {
    markHidden();
    abortAllControllers();
  });

  window.addEventListener("focus", () => {
    if (!isVisibleDocument()) return;
    markResumed();
    scheduleResumeSync({ delay: 220, refreshCurrent: false });
  });

  window.addEventListener("online", () => {
    markResumed();
    renderSearchStatus("网络已恢复，正在重新同步…", "loading");
    scheduleResumeSync({ delay: 120 });
  });
}

async function bootstrap() {
  bindEvents();
  renderEmptyState();
  await loadStatus();
  const initialQuery = getInitialQueryFromUrl();
  if (initialQuery) {
    const searchInput = document.getElementById("searchInput");
    if (searchInput) searchInput.value = initialQuery;
    state.lastSearchQuery = initialQuery;
    await performSearch({ selectFirst: true });
  }

  window.setInterval(() => {
    if (document.visibilityState !== "visible") return;
    if (state.isSearching || state.isLoadingSnapshot) return;
    loadStatus().catch(() => {});
  }, 60000);
}

bootstrap().catch((error) => {
  renderResultFeedback(error.message || "初始化失败", "warn");
  renderSearchStatus("初始化失败，稍后可点“同步最新”重试。", "warn");
});
