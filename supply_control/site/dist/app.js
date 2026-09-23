const state = {
  mode: new URLSearchParams(window.location.search).get("mode") || document.body.dataset.defaultMode || "synthetic",
  publicOnly: document.body.dataset.publicDemo === "true",
  publicExpanded: document.body.dataset.publicDemo === "expanded",
  payload: null,
  rows: [],
  filtered: [],
  selected: new Set(),
  approved: new Set(JSON.parse(localStorage.getItem("supply-control-approved-v2") || "[]")),
  page: 1,
  pageSize: 15,
  filters: { search: "", urgency: "all", status: "all", positiveOnly: true, sort: "priority" },
};

const dataSources = {
  synthetic: "./data/synthetic.json",
  partner: "./data/recommendations.json",
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
  one_off_excluded: "Разовый крупный заказ исключён",
  customer_level_outlier: "Выброс подтверждён на уровне клиента",
  stockout_compensated: "Упущенный спрос восстановлен",
  partner_answers_partial: "Ответы партнёра применены частично",
  partner_answers_confirmed: "Ключевые параметры подтверждены партнёром",
  inbound_eta_unconfirmed: "Смысл даты товара в пути не подтверждён",
  "Дефицит оценён по нулевому начальному остатку": "Дефицит оценён по нулевому начальному остатку",
};

const formatNumber = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const formatDecimal = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
const formatMoney = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0, style: "currency", currency: "KZT" });
const formatSignedNumber = (value) => `${Number(value) > 0 ? "+" : ""}${formatNumber.format(Number(value) || 0)}`;
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);

function cacheElements() {
  [
    "metric-primary-label", "metric-value", "metric-lines", "metric-critical", "metric-review", "metric-blocked",
    "quality-a-label", "quality-a", "quality-a-note", "quality-b-label", "quality-b", "quality-b-note",
    "quality-c-label", "quality-c", "quality-c-note", "quality-d-label", "quality-d", "quality-d-note",
    "requirements-proof", "proof-grid", "recommendations-body", "selected-count", "review-button", "export-button",
    "search-input", "urgency-filter", "status-filter", "sort-select", "positive-filter", "table-count", "page-indicator",
    "prev-page", "next-page", "assumption-list", "detail-dialog", "detail-title", "detail-content", "approval-dialog",
    "approval-copy", "confirm-button", "file-input", "toast", "scenario-link", "dataset-mode", "source-name",
    "source-date", "source-count", "data-badge", "warning-title", "warning-copy",
    "partner-questions", "question-count", "question-confirmed", "questions-body", "questionnaire-download",
    "scenario-comparison", "scenario-comparison-body", "scenario-comparison-note",
  ].forEach((id) => { elements[id] = document.getElementById(id); });
}

async function fetchPayload(mode) {
  const response = await fetch(dataSources[mode], { cache: "no-store" });
  if (!response.ok) throw new Error("Не удалось загрузить расчёт");
  return response.json();
}

function validatePayload(payload) {
  if (!payload || !payload.summary || !payload.assumptions || !Array.isArray(payload.recommendations)) {
    throw new Error("Ожидается JSON с разделами summary, assumptions и recommendations");
  }
  return payload;
}

function approvalKey(row) { return `${state.mode}:${row.sku}`; }

function setPayload(payload, mode = state.mode) {
  state.mode = mode;
  state.payload = validatePayload(payload);
  state.rows = payload.recommendations;
  state.selected.clear();
  state.page = 1;
  renderSource();
  renderSummary();
  renderQuality();
  renderPartnerQuestions();
  renderScenarioComparison();
  renderProofs();
  renderAssumptions();
  applyFilters();
}

function renderSource() {
  const summary = state.payload.summary;
  elements["source-name"].textContent = summary.source_label || summary.scenario || "Расчёт";
  elements["source-date"].textContent = `Срез на ${new Date(`${summary.as_of}T12:00:00`).toLocaleDateString("ru-RU")}`;
  elements["source-count"].textContent = `${formatNumber.format(summary.source_skus || 0)} товарных позиций`;
  elements["data-badge"].textContent = state.mode === "synthetic"
    ? "SAFE DEMO"
    : state.publicExpanded ? "PUBLIC DERIVED" : "PRIVATE";
  elements["data-badge"].classList.toggle("private", state.mode === "partner");
  elements["warning-title"].textContent = state.mode === "synthetic"
    ? "Безопасная проверка требований"
    : state.publicExpanded ? "Расширенный публичный режим" : "Демонстрационный расчёт на данных партнёра";
  elements["warning-copy"].textContent = summary.warning || "Результат требует проверки менеджером.";
  elements["scenario-link"].textContent = state.mode === "synthetic" ? "Показать методику" : "Показать допущения";
}

function renderSummary() {
  const summary = state.payload.summary;
  const hasValue = summary.total_order_value_demo != null && Number.isFinite(Number(summary.total_order_value_demo));
  elements["metric-primary-label"].textContent = hasValue ? "Оценочная стоимость" : "Рекомендации к заказу";
  elements["metric-value"].textContent = hasValue ? formatMoney.format(summary.total_order_value_demo) : formatNumber.format(summary.positive_order_lines || 0);
  elements["metric-lines"].textContent = hasValue ? `${formatNumber.format(summary.positive_order_lines || 0)} строк к заказу` : "положительное количество";
  elements["metric-critical"].textContent = formatNumber.format(summary.urgency_counts?.critical || 0);
  elements["metric-review"].textContent = formatNumber.format((summary.status_counts?.review_required || 0) + (summary.status_counts?.ready_for_approval || 0));
  elements["metric-blocked"].textContent = formatNumber.format(summary.blocked_lines || 0);
}

function setQuality(slot, label, value, note, bad = false) {
  elements[`quality-${slot}-label`].textContent = label;
  elements[`quality-${slot}`].textContent = value;
  elements[`quality-${slot}-note`].textContent = note;
  elements[`quality-${slot}`].classList.toggle("bad", bad);
}

function renderQuality() {
  const summary = state.payload.summary;
  if (state.mode === "synthetic") {
    setQuality("a", "Stockout-периоды", `${summary.stockout_coverage_percent || 0}%`, "контрольный сценарий");
    setQuality("b", "ID клиентов", `${summary.customer_id_coverage_percent || 0}%`, "обезличенные вымышленные ID");
    setQuality("c", "Must-have", `${summary.requirements_passed || 0}/${summary.requirements_total || 0}`, "проверено сценариями");
    setQuality("d", "Коммерческие данные", "0", "полностью синтетический набор");
    return;
  }
  const withMoq = (summary.source_skus || 0) - (summary.blocked_lines || 0);
  const moqCoverage = summary.source_skus ? withMoq / summary.source_skus : 0;
  setQuality("a", "Stockout-периоды", `${summary.stockout_coverage_percent || 0}%`, "не переданы", true);
  setQuality("b", "MOQ", `${Math.round(moqCoverage * 100)}%`, "покрытие справочника");
  setQuality("c", "Ошибка прогноза WAPE", `${formatDecimal.format((summary.forecast_wape || 0) * 100)}%`, summary.forecast_validation_period || "rolling backtest");
  setQuality("d", "Смещение прогноза", `${formatDecimal.format((summary.forecast_bias || 0) * 100)}%`, summary.forecast_model || "bias");
}

function renderProofs() {
  const proofs = state.payload.proofs || [];
  elements["requirements-proof"].hidden = proofs.length === 0;
  elements["proof-grid"].innerHTML = proofs.map((proof, index) => `
    <article class="proof-item ${escapeHtml(proof.status)}">
      <span>${String(index + 1).padStart(2, "0")}</span>
      <div><strong>${escapeHtml(proof.title)}</strong><p>${escapeHtml(proof.evidence)}</p></div>
      <b>${proof.status === "passed" ? "Пройдено" : "Проверить"}</b>
    </article>`).join("");
}

function displayAnswer(value) {
  if (value === true) return "Да";
  if (value === false) return "Нет";
  if (value == null || value === "") return "—";
  return String(value);
}

function renderPartnerQuestions() {
  const questions = state.payload.partner_questions || [];
  elements["partner-questions"].hidden = questions.length === 0;
  if (!questions.length) return;
  const confirmed = questions.filter((row) => row.status === "partner_confirmed").length;
  elements["question-confirmed"].textContent = formatNumber.format(confirmed);
  elements["question-count"].textContent = formatNumber.format(questions.length);
  elements["questions-body"].innerHTML = questions.map((row) => {
    const status = row.status === "partner_confirmed" ? "Подтверждено партнёром" : row.current_status || "Ожидает ответа";
    return `<tr>
      <td><span class="priority-tag ${row.priority === "Критично" ? "critical" : "important"}">${escapeHtml(row.priority)}</span></td>
      <td class="question-cell"><strong>${escapeHtml(row.id)}</strong><span>${escapeHtml(row.question)}</span></td>
      <td>${escapeHtml(displayAnswer(row.current_answer))}<small>${escapeHtml(row.evidence || "")}</small></td>
      <td><span class="answer-status ${row.status === "partner_confirmed" ? "confirmed" : "pending"}">${escapeHtml(status)}</span></td>
      <td>${row.partner_answer == null ? '<span class="muted-answer">Ожидается</span>' : `<strong>${escapeHtml(displayAnswer(row.partner_answer))}</strong>`}</td>
      <td>${escapeHtml(row.project_impact || "—")}</td>
    </tr>`;
  }).join("");
}

function renderScenarioComparison() {
  const comparison = state.payload.scenario_comparison;
  const variants = comparison?.variants || [];
  elements["scenario-comparison"].hidden = variants.length === 0;
  if (!variants.length) return;
  elements["scenario-comparison-body"].innerHTML = variants.map((row) => {
    const hasValue = row.total_order_value_demo != null && Number.isFinite(Number(row.total_order_value_demo));
    const value = hasValue ? formatMoney.format(row.total_order_value_demo) : "Скрыто";
    return `<tr class="${row.key === comparison.baseline_key ? "scenario-base-row" : ""}">
      <td class="scenario-name"><strong>${escapeHtml(row.label)}</strong><span>${escapeHtml(row.description)}</span></td>
      <td class="number">${formatNumber.format(row.lead_time_days || 0)} дн.</td>
      <td class="number">${formatNumber.format(row.positive_order_lines || 0)}</td>
      <td class="number"><strong>${formatNumber.format(row.total_order_units_demo || 0)}</strong></td>
      <td class="number scenario-delta">${formatSignedNumber(row.delta_order_units_from_base)}</td>
      <td class="number">${escapeHtml(value)}</td>
      <td class="number">${formatNumber.format(row.estimated_shortage_lines || 0)} SKU</td>
      <td class="number">${formatNumber.format(row.estimated_shortage_units || 0)} шт.</td>
      <td class="number">${formatNumber.format(row.estimated_buffer_units || 0)} шт.</td>
    </tr>`;
  }).join("");
  elements["scenario-comparison-note"].textContent = `${comparison.warning || ""} ${comparison.method || ""}`.trim();
}

function renderAssumptions() {
  const assumptions = state.payload.assumptions;
  const category = Object.entries(assumptions.category_safety_stock_days || {}).map(([key, value]) => `${key}: ${value} дн.`).join(" · ");
  const items = state.mode === "synthetic"
    ? [
        ["Данные", "Полностью синтетические"],
        ["Горизонт", `${assumptions.lead_time_days || 30} дней`],
        ["Проверка", "5 обязательных сценариев"],
        ["Отправка", "Только после подтверждения сотрудника"],
      ]
    : [
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
  elements["recommendations-body"].innerHTML = pageRows.length
    ? pageRows.map(renderRow).join("")
    : `<tr><td class="empty-row" colspan="10">По выбранным фильтрам позиций нет.</td></tr>`;
  const pageCount = Math.max(1, Math.ceil(state.filtered.length / state.pageSize));
  elements["table-count"].textContent = `Показано ${pageRows.length} из ${state.filtered.length}`;
  elements["page-indicator"].textContent = `${state.page} / ${pageCount}`;
  elements["prev-page"].disabled = state.page <= 1;
  elements["next-page"].disabled = state.page >= pageCount;
}

function renderRow(row) {
  const rec = row.recommendation;
  const approved = state.approved.has(approvalKey(row));
  const selectable = isSelectable(row);
  const checked = state.selected.has(row.sku) ? "checked" : "";
  const freeStock = rec.free_stock == null ? "—" : formatNumber.format(rec.free_stock);
  return `<tr class="${approved ? "approved-row" : ""}">
    <td class="checkbox-cell"><input type="checkbox" data-select-sku="${escapeHtml(row.sku)}" ${checked} ${selectable ? "" : "disabled"} aria-label="Выбрать ${escapeHtml(row.sku)}" /></td>
    <td><span class="pill ${escapeHtml(rec.urgency)}">${escapeHtml(urgencyLabels[rec.urgency] || rec.urgency)}</span>${approved ? '<span class="approved-tag">Подтверждено</span>' : ""}</td>
    <td class="product-cell"><strong>${escapeHtml(row.sku)}</strong><span title="${escapeHtml(row.name)}">${escapeHtml(row.name)}</span></td>
    <td>${escapeHtml(row.category || "—")}</td>
    <td class="number">${formatNumber.format(row.forecast?.monthly_units || 0)}</td>
    <td class="number">${freeStock}</td>
    <td class="number">${formatNumber.format(rec.eligible_inbound || 0)}</td>
    <td class="number qty-order">${rec.recommended_quantity == null ? "—" : formatNumber.format(rec.recommended_quantity)}</td>
    <td class="number">${row.order_value_demo == null ? "—" : formatMoney.format(row.order_value_demo)}</td>
    <td><button class="row-action" type="button" data-detail-sku="${escapeHtml(row.sku)}" aria-label="Открыть обоснование ${escapeHtml(row.sku)}">→</button></td>
  </tr>`;
}

function updateSelectionSummary() {
  const selectedRows = state.rows.filter((row) => state.selected.has(row.sku));
  const value = selectedRows.reduce((sum, row) => sum + rowValue(row), 0);
  const valueText = value ? ` · ${formatMoney.format(value)}` : "";
  elements["selected-count"].textContent = selectedRows.length ? `${selectedRows.length} выбрано${valueText}` : "0 выбрано";
  elements["review-button"].disabled = selectedRows.length === 0;
  elements["export-button"].disabled = !state.rows.some((row) => state.approved.has(approvalKey(row)));
}

function openDetail(sku) {
  const row = state.rows.find((item) => item.sku === sku);
  if (!row) return;
  const rec = row.recommendation;
  elements["detail-title"].textContent = `${row.sku} · ${row.name}`;
  const metrics = [
    ["Прогноз", `${formatNumber.format(row.forecast.monthly_units || 0)} шт./мес.`],
    ["Горизонт", rec.cover_days == null ? "—" : `${rec.cover_days} дней`],
    ["Целевой запас", rec.target_stock == null ? "—" : formatNumber.format(rec.target_stock)],
    ["Свободный остаток", rec.free_stock == null ? "—" : formatNumber.format(rec.free_stock)],
    ["В пути учтено", formatNumber.format(rec.eligible_inbound || 0)],
    ["Кратность", rec.moq == null ? "—" : formatNumber.format(rec.moq)],
  ];
  const audit = [];
  if (rec.excluded_outliers) audit.push(`Исключено разовых заказов: ${formatNumber.format(rec.excluded_outliers)}`);
  if (rec.imputed_stockouts) audit.push(`Восстановлено спроса: ${formatNumber.format(rec.imputed_stockouts)}`);
  if (rec.excluded_customers && Object.keys(rec.excluded_customers).length) audit.push(`Клиент: ${Object.keys(rec.excluded_customers).join(", ")}`);
  elements["detail-content"].innerHTML = `
    <p class="detail-summary">${escapeHtml(rec.explanation)}</p>
    <div class="calculation-grid">${metrics.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}</div>
    ${audit.length ? `<div class="audit-note">${audit.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : ""}
    <ul class="reason-list">${(rec.reasons || []).map((reason) => `<li>${escapeHtml(reasonLabels[reason] || reason)}</li>`).join("")}</ul>`;
  elements["detail-dialog"].showModal();
}

function openApproval() {
  const rows = state.rows.filter((row) => state.selected.has(row.sku));
  const quantity = rows.reduce((sum, row) => sum + rowQuantity(row), 0);
  const value = rows.reduce((sum, row) => sum + rowValue(row), 0);
  const valueText = value ? `, оценочная стоимость ${formatMoney.format(value)}` : "";
  const budget = Number(state.payload.summary.order_budget_kzt);
  const minimum = Number(state.payload.summary.minimum_total_order_kzt);
  if (Number.isFinite(budget) && budget > 0 && value > budget) {
    showToast(`Выбранный заказ превышает бюджет на ${formatMoney.format(value - budget)}.`);
    return;
  }
  if (Number.isFinite(minimum) && minimum > 0 && value < minimum) {
    showToast(`Сумма заказа ниже минимальной на ${formatMoney.format(minimum - value)}.`);
    return;
  }
  elements["approval-copy"].textContent = `${rows.length} позиций, ${formatNumber.format(quantity)} единиц${valueText}.`;
  elements["approval-dialog"].showModal();
}

function confirmSelection() {
  const selectedRows = state.rows.filter((row) => state.selected.has(row.sku));
  const selectedValue = selectedRows.reduce((sum, row) => sum + rowValue(row), 0);
  const budget = Number(state.payload.summary.order_budget_kzt);
  const minimum = Number(state.payload.summary.minimum_total_order_kzt);
  if (Number.isFinite(budget) && budget > 0 && selectedValue > budget) {
    throw new Error(`Выбранный заказ превышает бюджет на ${formatMoney.format(selectedValue - budget)}`);
  }
  if (Number.isFinite(minimum) && minimum > 0 && selectedValue < minimum) {
    throw new Error(`Сумма заказа ниже минимальной на ${formatMoney.format(minimum - selectedValue)}`);
  }
  state.selected.forEach((sku) => {
    const row = state.rows.find((item) => item.sku === sku);
    if (row) state.approved.add(approvalKey(row));
  });
  localStorage.setItem("supply-control-approved-v2", JSON.stringify([...state.approved]));
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
  const rows = state.rows.filter((row) => state.approved.has(approvalKey(row)));
  if (!rows.length) return;
  const header = ["SKU", "Артикул поставщика", "Наименование", "Поставщик", "Количество", "Кратность", "Срочность", "Стоимость KZT", "Статус"];
  const body = rows.map((row) => [
    row.sku, row.supplier_sku, row.name, row.supplier, row.recommendation.recommended_quantity,
    row.recommendation.moq, row.recommendation.urgency, row.order_value_demo, "Подтверждено менеджером",
  ]);
  const csv = `\uFEFF${[header, ...body].map((line) => line.map(csvCell).join(";")).join("\n")}`;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${state.mode}-confirmed-orders.csv`;
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

async function switchMode(mode) {
  if (state.publicOnly) mode = "synthetic";
  elements["recommendations-body"].innerHTML = '<tr><td class="empty-row" colspan="10">Загружаем выбранный режим…</td></tr>';
  const payload = await fetchPayload(mode);
  setPayload(payload, mode);
  elements["dataset-mode"].value = mode;
  if (!state.publicOnly) history.replaceState(null, "", `${location.pathname}?mode=${mode}`);
}

function bindEvents() {
  elements["dataset-mode"].addEventListener("change", (event) => switchMode(event.target.value).catch((error) => showToast(error.message)));
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
      setPayload(JSON.parse(await file.text()), "uploaded");
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
    name: "switch_dataset_mode",
    title: "Переключить режим данных",
    description: "Переключает безопасную проверку требований и расширенные производные рекомендации.",
    inputSchema: { type: "object", properties: { mode: { enum: ["synthetic", "partner"] } }, required: ["mode"], additionalProperties: false },
    annotations: { readOnlyHint: false, untrustedContentHint: false },
    async execute(input) {
      if (state.publicOnly && input.mode !== "synthetic") throw new Error("В этой версии доступны только синтетические данные");
      await switchMode(input.mode);
      return { mode: state.mode, rows: state.rows.length };
    },
  });
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
  if (state.publicOnly) {
    state.mode = "synthetic";
    elements["dataset-mode"].closest(".mode-control").style.display = "none";
    elements["file-input"].closest(".file-button").style.display = "none";
  } else if (state.publicExpanded) {
    elements["file-input"].closest(".file-button").style.display = "none";
  }
  try {
    await switchMode(state.mode in dataSources ? state.mode : "synthetic");
    registerWebMcpTools();
  } catch (error) {
    elements["recommendations-body"].innerHTML = `<tr><td class="empty-row" colspan="10">${escapeHtml(error.message)}</td></tr>`;
    showToast(error.message || "Ошибка загрузки");
  }
}

document.addEventListener("DOMContentLoaded", init);
