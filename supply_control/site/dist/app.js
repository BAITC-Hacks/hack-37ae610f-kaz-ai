const state = {
  payload: null,
  rows: [],
  filtered: [],
  selected: new Set(),
  approved: new Set(JSON.parse(localStorage.getItem("supply-control-approved") || "[]")),
  page: 1,
  pageSize: 15,
  filters: { search: "", urgency: "all", status: "all", positiveOnly: true, sort: "priority" },
};

const elements = {};
const urgencyRank = { critical: 0, high: 1, normal: 2, none: 3, unknown: 4 };
const urgencyLabels = { critical: "Критично", high: "Высокий", normal: "Обычный", none: "Не требуется", unknown: "Нет данных" };
const reasonLabels = {
  stockout_data_missing: "Нет данных о stockout",
  demo_assumptions: "Применены демо-допущения",
  lumpy_demand: "Нерегулярный спрос",
  intermittent_demand: "Прерывистый спрос",
  sales_history_gaps: "Пропуски в истории продаж",
  negative_monthly_values_ignored: "Отрицательные продажи исключены",
  missing_or_invalid_moq: "Нет корректного MOQ",
  inbound_arrives_after_projected_stockout: "Поставка придёт после дефицита",
};

const formatNumber = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const formatMoney = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0, style: "currency", currency: "KZT" });
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);

function cacheElements() {
  [
    "metric-value", "metric-lines", "metric-critical", "metric-review", "metric-blocked", "quality-moq",
    "quality-growth", "quality-season", "recommendations-body", "selected-count", "review-button", "export-button",
    "search-input", "urgency-filter", "status-filter", "sort-select", "positive-filter", "table-count", "page-indicator",
    "prev-page", "next-page", "assumption-list", "detail-dialog", "detail-title", "detail-content", "approval-dialog",
    "approval-copy", "confirm-button", "file-input", "toast", "scenario-link",
  ].forEach((id) => { elements[id] = document.getElementById(id); });
}

async function loadDefaultData() {
  const response = await fetch("./data/recommendations.json", { cache: "no-store" });
  if (!response.ok) throw new Error("Не удалось загрузить расчёт");
  return response.json();
}

function validatePayload(payload) {
  if (!payload || !payload.summary || !payload.assumptions || !Array.isArray(payload.recommendations)) {
    throw new Error("Ожидается JSON с разделами summary, assumptions и recommendations");
  }
  return payload;
}

function setPayload(payload) {
  state.payload = validatePayload(payload);
  state.rows = payload.recommendations;
  state.selected.clear();
  state.page = 1;
  renderSummary();
  renderAssumptions();
  applyFilters();
}

function renderSummary() {
  const summary = state.payload.summary;
  elements["metric-value"].textContent = formatMoney.format(summary.total_order_value_demo || 0);
  elements["metric-lines"].textContent = `${formatNumber.format(summary.positive_order_lines || 0)} строк к заказу`;
  elements["metric-critical"].textContent = formatNumber.format(summary.urgency_counts?.critical || 0);
  elements["metric-review"].textContent = formatNumber.format(summary.status_counts?.review_required || 0);
  elements["metric-blocked"].textContent = formatNumber.format(summary.blocked_lines || 0);
  const withMoq = (summary.source_skus || 0) - (summary.blocked_lines || 0);
  const moqCoverage = summary.source_skus ? withMoq / summary.source_skus : 0;
  elements["quality-moq"].textContent = `${Math.round(moqCoverage * 100)}%`;
  elements["quality-growth"].textContent = formatNumber.format(summary.growth_rates_clamped || 0);
  elements["quality-season"].textContent = formatNumber.format(summary.seasonality_rates_clamped || 0);
}

function renderAssumptions() {
  const assumptions = state.payload.assumptions;
  const category = Object.entries(assumptions.category_safety_stock_days || {})
    .map(([key, value]) => `${key}: ${value} дн.`).join(" · ");
  const items = [
    ["Срок поставки", `${assumptions.lead_time_days} дней`],
    ["Период пересмотра", `${assumptions.review_period_days} дней`],
    ["Страховой запас", category || "—"],
    ["Границы роста", (assumptions.growth_multiplier_bounds || []).join("–")],
  ];
  elements["assumption-list"].innerHTML = items.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
}

function rowQuantity(row) { return Number(row.recommendation?.recommended_quantity || 0); }
function rowValue(row) { return Number(row.order_value_demo || 0); }
function isSelectable(row) { return rowQuantity(row) > 0 && row.recommendation?.status !== "blocked"; }

function applyFilters() {
  const query = state.filters.search.trim().toLocaleLowerCase("ru");
  state.filtered = state.rows.filter((row) => {
    const haystack = `${row.sku} ${row.supplier_sku || ""} ${row.name}`.toLocaleLowerCase("ru");
    return (!query || haystack.includes(query))
      && (state.filters.urgency === "all" || row.recommendation.urgency === state.filters.urgency)
      && (state.filters.status === "all" || row.recommendation.status === state.filters.status)
      && (!state.filters.positiveOnly || rowQuantity(row) > 0);
  });
  const sorter = {
    priority: (a, b) => urgencyRank[a.recommendation.urgency] - urgencyRank[b.recommendation.urgency] || rowValue(b) - rowValue(a),
    value: (a, b) => rowValue(b) - rowValue(a),
    quantity: (a, b) => rowQuantity(b) - rowQuantity(a),
    sku: (a, b) => String(a.sku).localeCompare(String(b.sku), "ru"),
  }[state.filters.sort];
  state.filtered.sort(sorter);
  const pageCount = Math.max(1, Math.ceil(state.filtered.length / state.pageSize));
  state.page = Math.min(state.page, pageCount);
  renderTable();
  updateSelectionSummary();
}

function renderTable() {
  const start = (state.page - 1) * state.pageSize;
  const pageRows = state.filtered.slice(start, start + state.pageSize);
  if (!pageRows.length) {
    elements["recommendations-body"].innerHTML = `<tr><td class="empty-row" colspan="10">По выбранным фильтрам позиций нет.</td></tr>`;
  } else {
    elements["recommendations-body"].innerHTML = pageRows.map(renderRow).join("");
  }
  const pageCount = Math.max(1, Math.ceil(state.filtered.length / state.pageSize));
  elements["table-count"].textContent = `Показано ${pageRows.length} из ${state.filtered.length}`;
  elements["page-indicator"].textContent = `${state.page} / ${pageCount}`;
  elements["prev-page"].disabled = state.page <= 1;
  elements["next-page"].disabled = state.page >= pageCount;
}

function renderRow(row) {
  const rec = row.recommendation;
  const approved = state.approved.has(row.sku);
  const selectable = isSelectable(row);
  const checked = state.selected.has(row.sku) ? "checked" : "";
  return `<tr class="${approved ? "approved-row" : ""}">
    <td class="checkbox-cell"><input type="checkbox" data-select-sku="${escapeHtml(row.sku)}" ${checked} ${selectable ? "" : "disabled"} aria-label="Выбрать ${escapeHtml(row.sku)}" /></td>
    <td><span class="pill ${escapeHtml(rec.urgency)}">${escapeHtml(urgencyLabels[rec.urgency] || rec.urgency)}</span>${approved ? '<span class="approved-tag">Подтверждено</span>' : ""}</td>
    <td class="product-cell"><strong>${escapeHtml(row.sku)}</strong><span title="${escapeHtml(row.name)}">${escapeHtml(row.name)}</span></td>
    <td>${escapeHtml(row.category || "—")}</td>
    <td class="number">${formatNumber.format(row.forecast?.monthly_units || 0)}</td>
    <td class="number">${formatNumber.format(rec.free_stock || 0)}</td>
    <td class="number">${formatNumber.format(rec.eligible_inbound || 0)}</td>
    <td class="number qty-order">${rec.recommended_quantity == null ? "—" : formatNumber.format(rec.recommended_quantity)}</td>
    <td class="number">${row.order_value_demo == null ? "—" : formatMoney.format(row.order_value_demo)}</td>
    <td><button class="row-action" type="button" data-detail-sku="${escapeHtml(row.sku)}" aria-label="Открыть обоснование ${escapeHtml(row.sku)}">→</button></td>
  </tr>`;
}

function updateSelectionSummary() {
  const selectedRows = state.rows.filter((row) => state.selected.has(row.sku));
  const value = selectedRows.reduce((sum, row) => sum + rowValue(row), 0);
  elements["selected-count"].textContent = selectedRows.length ? `${selectedRows.length} выбрано · ${formatMoney.format(value)}` : "0 выбрано";
  elements["review-button"].disabled = selectedRows.length === 0;
  elements["export-button"].disabled = state.approved.size === 0;
}

function openDetail(sku) {
  const row = state.rows.find((item) => item.sku === sku);
  if (!row) return;
  const rec = row.recommendation;
  elements["detail-title"].textContent = `${row.sku} · ${row.name}`;
  const metrics = [
    ["Прогноз", `${formatNumber.format(row.forecast.monthly_units)} шт./мес.`],
    ["Горизонт", rec.cover_days == null ? "—" : `${rec.cover_days} дней`],
    ["Целевой запас", rec.target_stock == null ? "—" : formatNumber.format(rec.target_stock)],
    ["Свободный остаток", formatNumber.format(rec.free_stock || 0)],
    ["В пути учтено", formatNumber.format(rec.eligible_inbound || 0)],
    ["MOQ", rec.moq == null ? "—" : formatNumber.format(rec.moq)],
  ];
  elements["detail-content"].innerHTML = `
    <p class="detail-summary">${escapeHtml(rec.explanation)}</p>
    <div class="calculation-grid">${metrics.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}</div>
    <ul class="reason-list">${(rec.reasons || []).map((reason) => `<li>${escapeHtml(reasonLabels[reason] || reason)}</li>`).join("")}</ul>`;
  elements["detail-dialog"].showModal();
}

function openApproval() {
  const rows = state.rows.filter((row) => state.selected.has(row.sku));
  const quantity = rows.reduce((sum, row) => sum + rowQuantity(row), 0);
  const value = rows.reduce((sum, row) => sum + rowValue(row), 0);
  elements["approval-copy"].textContent = `${rows.length} позиций, ${formatNumber.format(quantity)} единиц, оценочная стоимость ${formatMoney.format(value)}.`;
  elements["approval-dialog"].showModal();
}

function confirmSelection() {
  state.selected.forEach((sku) => state.approved.add(sku));
  localStorage.setItem("supply-control-approved", JSON.stringify([...state.approved]));
  const count = state.selected.size;
  state.selected.clear();
  elements["approval-dialog"].close();
  renderTable();
  updateSelectionSummary();
  showToast(`${count} позиций подтверждено. Поставщику ничего не отправлено.`);
}

function csvCell(value) {
  let text = String(value ?? "");
  if (/^[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

function exportApproved() {
  const rows = state.rows.filter((row) => state.approved.has(row.sku));
  if (!rows.length) return;
  const header = ["SKU", "Артикул поставщика", "Наименование", "Поставщик", "Количество", "MOQ", "Срочность", "Стоимость KZT", "Статус"];
  const body = rows.map((row) => [
    row.sku, row.supplier_sku, row.name, row.supplier, row.recommendation.recommended_quantity,
    row.recommendation.moq, row.recommendation.urgency, row.order_value_demo, "Подтверждено менеджером",
  ]);
  const csv = `\uFEFF${[header, ...body].map((line) => line.map(csvCell).join(";")).join("\n")}`;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "systeme-confirmed-orders-demo.csv";
  link.click();
  URL.revokeObjectURL(url);
  showToast(`Экспортировано ${rows.length} подтверждённых позиций.`);
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => elements.toast.classList.remove("visible"), 2600);
}

function bindEvents() {
  elements["search-input"].addEventListener("input", (event) => { state.filters.search = event.target.value; state.page = 1; applyFilters(); });
  elements["urgency-filter"].addEventListener("change", (event) => { state.filters.urgency = event.target.value; state.page = 1; applyFilters(); });
  elements["status-filter"].addEventListener("change", (event) => { state.filters.status = event.target.value; state.page = 1; applyFilters(); });
  elements["sort-select"].addEventListener("change", (event) => { state.filters.sort = event.target.value; state.page = 1; applyFilters(); });
  elements["positive-filter"].addEventListener("change", (event) => { state.filters.positiveOnly = event.target.checked; state.page = 1; applyFilters(); });
  elements["prev-page"].addEventListener("click", () => { state.page -= 1; renderTable(); });
  elements["next-page"].addEventListener("click", () => { state.page += 1; renderTable(); });
  elements["review-button"].addEventListener("click", openApproval);
  elements["confirm-button"].addEventListener("click", confirmSelection);
  elements["export-button"].addEventListener("click", exportApproved);
  elements["scenario-link"].addEventListener("click", () => document.getElementById("scenario").scrollIntoView({ behavior: "smooth" }));
  elements["recommendations-body"].addEventListener("change", (event) => {
    const sku = event.target.dataset.selectSku;
    if (!sku) return;
    event.target.checked ? state.selected.add(sku) : state.selected.delete(sku);
    updateSelectionSummary();
  });
  elements["recommendations-body"].addEventListener("click", (event) => {
    const button = event.target.closest("[data-detail-sku]");
    if (button) openDetail(button.dataset.detailSku);
  });
  document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => document.getElementById(button.dataset.closeDialog).close()));
  elements["file-input"].addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      setPayload(JSON.parse(await file.text()));
      showToast(`Загружен расчёт: ${file.name}`);
    } catch (error) {
      showToast(error.message || "Не удалось прочитать файл");
    } finally {
      event.target.value = "";
    }
  });
}

function registerWebMcpTools() {
  const context = document.modelContext;
  if (!context?.registerTool) return;
  const register = (tool) => Promise.resolve(context.registerTool(tool)).catch(() => {});
  register({
    name: "filter_recommendations",
    title: "Фильтровать рекомендации",
    description: "Изменяет видимые фильтры очереди рекомендаций по срочности и наличию заказа.",
    inputSchema: { type: "object", properties: { urgency: { enum: ["all", "critical", "high", "normal", "none"] }, positiveOnly: { type: "boolean" } }, additionalProperties: false },
    annotations: { readOnlyHint: false, untrustedContentHint: false },
    execute(input) {
      const urgency = input?.urgency ?? "all";
      if (!["all", "critical", "high", "normal", "none"].includes(urgency)) throw new Error("Недопустимая срочность");
      state.filters.urgency = urgency;
      if (typeof input?.positiveOnly === "boolean") state.filters.positiveOnly = input.positiveOnly;
      elements["urgency-filter"].value = state.filters.urgency;
      elements["positive-filter"].checked = state.filters.positiveOnly;
      state.page = 1;
      applyFilters();
      return { visible: state.filtered.length, urgency, positiveOnly: state.filters.positiveOnly };
    },
  });
  register({
    name: "stage_recommendations",
    title: "Выбрать позиции",
    description: "Выбирает допустимые SKU для последующего ручного подтверждения; поставщику ничего не отправляется.",
    inputSchema: { type: "object", properties: { skus: { type: "array", items: { type: "string" }, minItems: 1 } }, required: ["skus"], additionalProperties: false },
    annotations: { readOnlyHint: false, untrustedContentHint: false },
    execute(input) {
      if (!Array.isArray(input?.skus) || !input.skus.length) throw new Error("Нужен хотя бы один SKU");
      const valid = input.skus.filter((sku) => state.rows.some((row) => row.sku === sku && isSelectable(row)));
      if (!valid.length) throw new Error("Нет доступных для подтверждения SKU");
      valid.forEach((sku) => state.selected.add(sku));
      renderTable();
      updateSelectionSummary();
      return { staged: valid, skipped: input.skus.filter((sku) => !valid.includes(sku)) };
    },
  });
  register({
    name: "approve_staged_recommendations",
    title: "Подтвердить выбранные позиции",
    description: "Подтверждает ранее выбранные позиции локально в браузере. Не отправляет заказ поставщику.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    annotations: { readOnlyHint: false, untrustedContentHint: false },
    execute() {
      if (!state.selected.size) throw new Error("Сначала выберите позиции");
      const approved = [...state.selected];
      confirmSelection();
      return { approved, sentToSupplier: false };
    },
  });
}

async function init() {
  cacheElements();
  bindEvents();
  try {
    setPayload(await loadDefaultData());
    registerWebMcpTools();
  } catch (error) {
    elements["recommendations-body"].innerHTML = `<tr><td class="empty-row" colspan="10">${escapeHtml(error.message)}</td></tr>`;
    showToast(error.message || "Ошибка загрузки");
  }
}

document.addEventListener("DOMContentLoaded", init);

