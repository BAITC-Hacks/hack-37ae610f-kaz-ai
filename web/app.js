const $ = (id) => document.getElementById(id);
const page = document.body.dataset.page;
const state = { meta: null, rows: [], summary: null, suppliers: [], total: 0, offset: 0, limit: 100, selected: new Map(), days: 30, approvedCount: 0 };
const languageKey = 'kaz-ai-language';
const preferencesKey = 'kaz-ai-preferences';
const savedPreferences = (() => { try { return JSON.parse(localStorage.getItem(preferencesKey)) || {}; } catch { return {}; } })();
const preferences = {
  coverageDays: [14, 30, 45, 60].includes(savedPreferences.coverageDays) ? savedPreferences.coverageDays : 30,
  supplier: typeof savedPreferences.supplier === 'string' ? savedPreferences.supplier : '',
  ordersOnly: typeof savedPreferences.ordersOnly === 'boolean' ? savedPreferences.ordersOnly : true,
};
let language = ['kk', 'ru'].includes(localStorage.getItem(languageKey)) ? localStorage.getItem(languageKey) : 'ru';
const locale = () => ({ kk: 'kk-KZ', ru: 'ru-RU' })[language];
let fmt = new Intl.NumberFormat(locale(), { maximumFractionDigits: 1 });
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const key = (row) => `${row.supplier}␟${row.code}`;
const positionWord = (count) => count % 10 === 1 && count % 100 !== 11 ? 'позиция' : count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14) ? 'позиции' : 'позиций';
const t = (name, values = {}) => (window.KazI18n[language][name] || window.KazI18n.ru[name] || name)
  .replace(/\{(\w+)\}/g, (_, field) => String(values[field] ?? ''));
const demoName = (kind, original, code) => state.meta?.mode === 'synthetic'
  ? (window.KazI18n.demo[kind][code || original]?.[language] || original) : original;
const displaySupplier = (name) => demoName('suppliers', name);
const displayCategory = (name) => demoName('categories', name);
const displayProduct = (row) => demoName('products', row.name, row.code);
const flagKeys = {
  'Детальные и месячные продажи расходятся': 'flagSalesMismatch',
  'Дефицит оценён по нулевому начальному остатку': 'flagEstimatedStockout',
  'Остаток не на дату расчёта': 'flagOldStock',
  'Отрицательный остаток учтён как ноль': 'flagNegativeStock',
  'Срок поставки не задан; использован только выбранный горизонт': 'flagNoLead',
  'Остаток внесён вручную': 'flagManualStock',
  'Параметры заказа не подтверждены': 'flagUnconfirmedTerms',
};
const displayFlag = (flag) => flagKeys[flag] ? t(flagKeys[flag]) : flag;

function localizedReason(row) {
  if (language === 'ru') return row.reason || '';
  if (state.meta?.mode === 'partner' && row.status === 'review_required') return t('partnerBlockedReason', { forecast: fmt.format(row.forecast) });
  if (row.status === 'insufficient_history') return t('insufficientHistory');
  const stock = row.free_stock;
  const date = new Date(`${state.meta.as_of}T12:00:00`);
  date.setDate(date.getDate() + row.target_days);
  let reason = t(stock == null ? 'reasonUnknown' : 'reasonKnown', {
    days: row.target_days, forecast: fmt.format(row.forecast), stock: fmt.format(stock),
  });
  reason += t('reasonSettings', {
    coverage: state.days, lead: row.lead_time_days ?? 0, safety: row.safety_days ?? 0,
    extra: fmt.format(Math.max(0, row.forecast - (row.coverage_forecast ?? row.forecast))),
    date: date.toLocaleDateString(locale()), inbound: fmt.format(row.inbound),
    growth: new Intl.NumberFormat(locale(), { style: 'percent', maximumFractionDigits: 0, signDisplay: 'always' }).format(row.growth), moq: row.moq,
  });
  if (stock != null) reason += t('reasonShortage', {
    shortage: fmt.format(Math.max(0, row.forecast - Math.max(0, stock) - row.inbound)), quantity: fmt.format(row.quantity),
  });
  if (row.excluded_outliers) reason += t('reasonExcluded', { excluded: fmt.format(row.excluded_outliers) });
  const customers = Object.entries(row.excluded_customers || {});
  if (customers.length) reason += t('reasonCustomers', { customers: customers.map(([id, amount]) => `${id}: ${fmt.format(amount)}`).join(', ') });
  if (row.imputed_stockouts) reason += t('reasonImputed', { imputed: fmt.format(row.imputed_stockouts) });
  return reason;
}

function applyLanguage() {
  document.documentElement.lang = language;
  document.title = `${t(({home:'navHome', recommendations:'navRecommendations', suppliers:'navSuppliers', audit:'navAudit', settings:'navSettings'})[page])} — KAZ-AI`;
  document.querySelectorAll('[data-nav]').forEach((link) => {
    if (link.dataset.nav === page) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  fmt = new Intl.NumberFormat(locale(), { maximumFractionDigits: 1 });
  document.querySelectorAll('[data-i18n]').forEach((node) => {
    node.textContent = t(node.dataset.i18n === 'footerRight' && window.KazStatic ? 'publicFooter' : node.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((node) => { node.placeholder = t(node.dataset.i18nPlaceholder); });
  document.querySelectorAll('[data-i18n-aria-label]').forEach((node) => { node.setAttribute('aria-label', t(node.dataset.i18nAriaLabel)); });
  ['days', 'defaultDays'].forEach((id) => $(id)?.querySelectorAll('option').forEach((option) => { option.textContent = `${option.value} ${language === 'kk' ? 'күн' : 'дней'}`; }));
  document.querySelectorAll('[data-lang]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.lang === language));
    button.classList.toggle('active', button.dataset.lang === language);
  });
}

function renderSettings() {
  if (page !== 'settings' || !state.meta) return;
  $('defaultDays').value = String(preferences.coverageDays);
  $('defaultOrdersOnly').checked = preferences.ordersOnly;
  $('defaultSupplier').innerHTML = `<option value="">${esc(t('allSuppliers'))}</option>` +
    state.meta.suppliers.map((name) => `<option value="${esc(name)}">${esc(displaySupplier(name))}</option>`).join('');
  $('defaultSupplier').value = state.meta.suppliers.includes(preferences.supplier) ? preferences.supplier : '';
}

function savePreferences() {
  preferences.coverageDays = Number($('defaultDays').value);
  preferences.supplier = $('defaultSupplier').value;
  preferences.ordersOnly = $('defaultOrdersOnly').checked;
  localStorage.setItem(preferencesKey, JSON.stringify(preferences));
  notify(t('preferencesSaved'));
}

function notify(message, error = false) {
  const node = $('notice');
  node.textContent = message;
  node.classList.toggle('error', error);
  node.hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => { node.hidden = true; }, 4500);
}

async function request(url, options) {
  if (window.KazStatic) return window.KazStatic.request(url, options);
  const response = await fetch(url, options);
  const type = response.headers.get('content-type') || '';
  const data = type.includes('json') ? await response.json() : null;
  if (!response.ok) throw new Error(data?.error || t('requestError', { status: response.status }));
  return data;
}

function renderMeta() {
  const meta = state.meta;
  if ($('asOf')) $('asOf').textContent = new Date(`${meta.as_of}T12:00:00`).toLocaleDateString(locale());
  if ($('sourceMode')) $('sourceMode').textContent = t(meta.mode === 'synthetic' ? 'synthetic' : 'partnerExports');
  if ($('salesLink')) {
    $('salesLink').hidden = meta.mode !== 'synthetic';
    if (meta.mode === 'synthetic') $('salesLink').textContent = t('salesLink', { sales: fmt.format(meta.transactions), customers: fmt.format(meta.customers) });
  }
  if ($('supplier')) {
    const supplier = $('supplier').value;
    const category = $('category').value;
    $('supplier').innerHTML = `<option value="">${esc(t('allSuppliers'))}</option>` + meta.suppliers.map((name) => `<option value="${esc(name)}">${esc(displaySupplier(name))}</option>`).join('');
    $('category').innerHTML = `<option value="">${esc(t('allCategories'))}</option>` + (meta.categories || []).map((name) => `<option value="${esc(name)}">${esc(displayCategory(name))}</option>`).join('');
    $('supplier').value = supplier;
    $('category').value = category;
    $('approvedCount').textContent = fmt.format(state.approvedCount);
    $('exportLink').classList.toggle('disabled', state.approvedCount === 0);
    $('exportLink').setAttribute('aria-disabled', String(state.approvedCount === 0));
    const warnings = meta.warnings || [];
    $('warnings').hidden = warnings.length === 0;
    $('warnings').innerHTML = warnings.length ? `<strong>${esc(t('warningsTitle'))}</strong><ul>${warnings.map((w) => `<li>${esc(displayFlag(w))}</li>`).join('')}</ul>` : '';
  }
}

function renderSummary(summary) {
  if (!$('statProducts')) return;
  $('statProducts').textContent = fmt.format(summary.products);
  $('statOrders').textContent = fmt.format(summary.orders);
  $('statReady').textContent = fmt.format(summary.ready);
  $('statMissing').textContent = fmt.format(summary.needs_stock);
}

function renderAnalytics(rows) {
  if (!$('analytics')) return;
  $('analyticsRisk').textContent = fmt.format(rows.filter((row) => row.status === 'ready' && (row.risk_before_delivery || 0) > 0).length);
  $('analyticsOutliers').textContent = fmt.format(rows.filter((row) => (row.excluded_outliers || 0) > 0).length);
  $('analyticsStockout').textContent = fmt.format(rows.filter((row) => (row.imputed_stockouts || 0) > 0).length);
  $('analyticsStock').textContent = `${fmt.format(rows.filter((row) => row.free_stock != null).length)}/${fmt.format(rows.length)}`;
}

function renderSuppliers(suppliers) {
  if (!$('supplierCards')) return;
  $('supplierCards').innerHTML = suppliers.map((item) => {
    const hasApproved = item.approved_positions > 0;
    const href = `/api/export.csv?supplier=${encodeURIComponent(item.supplier)}`;
    const exportControl = window.KazStatic
      ? `<button type="button" data-supplier="${esc(item.supplier)}" class="supplier-export ${hasApproved ? '' : 'disabled'}" aria-disabled="${!hasApproved}">${esc(t('supplierCsv'))}</button>`
      : `<a href="${esc(href)}" class="supplier-export ${hasApproved ? '' : 'disabled'}" aria-disabled="${!hasApproved}">${esc(t('supplierCsv'))}</a>`;
    return `<article class="supplier-card">
      <div class="supplier-card-name">${esc(displaySupplier(item.supplier))}</div>
      <div class="supplier-card-stats"><strong>${fmt.format(item.recommendations)}</strong><span>${esc(t('supplierRecommendations', { count: fmt.format(item.recommendations), word: positionWord(item.recommendations) }))}</span><strong>${fmt.format(item.approved_positions)}</strong><span>${esc(t('supplierApproved'))}</span></div>
      <div class="supplier-card-actions"><button class="supplier-open" type="button" data-supplier="${esc(item.supplier)}">${esc(t('showItems'))}</button>${exportControl}</div>
    </article>`;
  }).join('');
}

const auditExamples = [
  { at: '2026-09-22T10:15:00+05:00', event: 'approval', code: 'DEMO-B310', supplier: 'Демо-поставщик Б', recommended_quantity: 90, approved_quantity: 100, previous_quantity: null },
  { at: '2026-09-22T09:40:00+05:00', event: 'stock', code: 'DEMO-B840', supplier: 'Демо-поставщик Б', previous_stock: null, new_stock: 0 },
];

function renderAuditEvents(events, example = false) {
  return events.map((event) => {
    const when = new Date(event.at).toLocaleString(locale(), { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    const detail = event.event === 'approval'
      ? t('approvalDetail', { approved: fmt.format(event.approved_quantity), recommended: fmt.format(event.recommended_quantity) }) + (event.previous_quantity == null ? '' : t('previousQty', { previous: fmt.format(event.previous_quantity) }))
      : t('stockDetail', { previous: event.previous_stock == null ? t('unspecified') : fmt.format(event.previous_stock), next: fmt.format(event.new_stock) });
    return `<div class="audit-item"><time datetime="${esc(event.at)}">${esc(when)}</time><div><strong>${esc(event.code)}</strong><span>${esc(displaySupplier(event.supplier))}</span>${example ? `<span class="audit-sample-badge">${esc(t('auditExampleBadge'))}</span>` : ''}<p>${esc(detail)}</p></div></div>`;
  }).join('');
}

async function loadAudit() {
  const data = await request('/api/audit');
  $('auditList').innerHTML = data.events.length ? renderAuditEvents(data.events.slice(0, 30)) : esc(t('emptyAudit'));
  const examples = $('auditExamples');
  examples.hidden = state.meta.mode !== 'synthetic';
  if (!examples.hidden) $('auditExampleList').innerHTML = renderAuditEvents(auditExamples, true);
}

function renderRows() {
  if (!state.rows.length) {
    $('rows').innerHTML = `<tr><td colspan="8" class="empty">${esc(t('noRows'))}</td></tr>`;
  } else {
    $('rows').innerHTML = state.rows.map((row) => {
      const selected = state.selected.get(key(row));
      const quantity = selected?.quantity ?? row.quantity;
      const ready = row.status === 'ready' && (row.quantity || 0) > 0;
      const urgency = row.status !== 'ready' ? ['badge-missing', t('urgencyMissing')] : row.urgency === 'высокая' ? ['badge-high', t('urgencyHigh')] : ['badge-normal', t('urgencyNormal')];
      return `<tr data-key="${esc(key(row))}">
        <td><input class="row-check" type="checkbox" ${selected ? 'checked' : ''} ${ready ? '' : 'disabled'} aria-label="${esc(t('selectItem', { code: row.code }))}"></td>
        <td><div class="item-name">${esc(displayProduct(row))}<span class="item-code">${esc(row.code)} · ${esc(t('categoryRow', { category: displayCategory(row.category || '—') }))}</span></div>
          <div class="reason">${esc(localizedReason(row))}${(row.flags || []).map((flag) => `<span class="flag">${esc(displayFlag(flag))}</span>`).join('')}</div></td>
        <td data-label="${esc(t('supplier'))}"><span class="supplier-tag">${esc(displaySupplier(row.supplier))}</span></td>
        <td data-label="${esc(t('demand'))}">${row.forecast == null ? '—' : fmt.format(row.forecast)}</td>
        <td data-label="${esc(t('available'))}">${row.free_stock == null && row.status === 'needs_stock' ? `<div class="stock-entry"><input class="stock-input" type="number" min="0" step="1" placeholder="${esc(t('stockPlaceholder'))}" aria-label="${esc(t('stockLabel', { code: row.code }))}"><button class="save-stock" type="button">${esc(t('save'))}</button></div>` : row.free_stock == null ? '—' : fmt.format(row.free_stock)}</td>
        <td data-label="${esc(t('inbound'))}">${row.inbound == null ? '—' : fmt.format(row.inbound)}</td>
        <td data-label="${esc(t('order'))}">${ready ? `<input class="qty-input" type="number" min="${row.moq}" step="${row.moq}" value="${quantity}" aria-label="${esc(t('quantityLabel', { code: row.code }))}">` : '—'}</td>
        <td data-label="${esc(t('urgency'))}"><span class="badge ${urgency[0]}">${urgency[1]}</span>${(row.risk_before_delivery || 0) > 0 ? `<span class="risk-gap">${esc(t('riskGap', { value: fmt.format(row.risk_before_delivery) }))}</span>` : ''}</td>
      </tr>`;
    }).join('');
  }
  const first = state.total ? state.offset + 1 : 0;
  const last = Math.min(state.offset + state.rows.length, state.total);
  $('paginationText').textContent = t('pagination', { first: fmt.format(first), last: fmt.format(last), total: fmt.format(state.total) });
  $('prevPage').disabled = state.offset === 0;
  $('nextPage').disabled = state.offset + state.limit >= state.total;
  updateSelection();
}

function updateSelection() {
  const count = state.selected.size;
  $('selectionCount').textContent = t('selectionCount', { count: fmt.format(count), word: t(count === 1 ? 'positionOne' : count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14) ? 'positionFew' : 'positionMany'), verb: t(count === 1 ? 'selectedOne' : 'selectedMany') });
  $('approve').disabled = count === 0;
  $('actionBar').hidden = count === 0;
}

async function loadRows() {
  const search = $('search').value.trim();
  const translatedSearch = state.meta?.mode === 'synthetic' && language === 'kk' && search;
  const params = new URLSearchParams({ days: String(state.days), supplier: $('supplier').value, category: $('category').value, q: translatedSearch ? '' : search, orders: $('ordersOnly').checked ? '1' : '0', offset: String(state.offset), limit: String(state.limit) });
  const data = await request(`/api/recommendations?${params}`);
  state.rows = translatedSearch ? data.rows.filter((row) => [row.code, row.name, displayProduct(row)].some((name) => name.toLocaleLowerCase(locale()).includes(search.toLocaleLowerCase(locale())))) : data.rows;
  state.total = translatedSearch ? state.rows.length : data.total;
  state.summary = data.summary;
  state.suppliers = data.suppliers;
  renderRows();
}

function clearAndLoad() {
  state.offset = 0;
  state.selected.clear();
  loadRows().catch((error) => notify(error.message, true));
}

async function loadOverview() {
  const analyticsEnabled = page === 'home' && state.meta.mode === 'synthetic';
  const params = `days=${preferences.coverageDays}&orders=0&limit=${analyticsEnabled ? 250 : 1}`;
  const data = await request(`/api/recommendations?${params}`);
  renderSummary(data.summary);
  renderSuppliers(data.suppliers);
  if ($('analytics')) {
    $('analytics').hidden = !analyticsEnabled;
    if (analyticsEnabled) {
      const rows = [...data.rows];
      while (rows.length < data.total) {
        const next = await request(`/api/recommendations?${params}&offset=${rows.length}`);
        if (!next.rows.length) break;
        rows.push(...next.rows);
      }
      renderAnalytics(rows);
    }
  }
}

async function init() {
  applyLanguage();
  state.meta = await request('/api/meta');
  if (page === 'settings') { renderSettings(); return; }
  state.approvedCount = state.meta.approved_count;
  renderMeta();
  if (page === 'recommendations') {
    state.days = preferences.coverageDays;
    $('days').value = String(state.days);
    $('ordersOnly').checked = state.meta.mode === 'partner' ? false : preferences.ordersOnly;
    if (state.meta.suppliers.includes(preferences.supplier)) $('supplier').value = preferences.supplier;
    const supplier = new URLSearchParams(location.search).get('supplier');
    if (supplier && state.meta.suppliers.includes(supplier)) $('supplier').value = supplier;
    await loadRows();
  } else if (page === 'home' || page === 'suppliers') await loadOverview();
  else if (page === 'audit') await loadAudit();
}

if (page === 'suppliers') $('supplierCards').addEventListener('click', (event) => {
  const control = event.target.closest('.supplier-export');
  if (control?.classList.contains('disabled')) event.preventDefault();
  else if (control && window.KazStatic) {
    event.preventDefault();
    try { window.KazStatic.exportCsv(control.dataset.supplier); }
    catch (error) { notify(error.message, true); }
  }
  const button = event.target.closest('button.supplier-open');
  if (button) location.href = `./recommendations.html?supplier=${encodeURIComponent(button.dataset.supplier)}`;
});

if (page === 'settings') document.querySelectorAll('[data-lang]').forEach((button) => button.addEventListener('click', () => {
  language = button.dataset.lang;
  localStorage.setItem(languageKey, language);
  applyLanguage();
  renderSettings();
}));

if (page === 'settings') ['defaultDays', 'defaultSupplier', 'defaultOrdersOnly'].forEach((id) => $(id).addEventListener('change', savePreferences));

if (page === 'recommendations') {
$('supplier').addEventListener('change', clearAndLoad);
$('category').addEventListener('change', clearAndLoad);
$('days').addEventListener('change', () => { state.days = Number($('days').value); clearAndLoad(); });
$('ordersOnly').addEventListener('change', clearAndLoad);
let searchTimer;
$('search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(clearAndLoad, 220); });
$('prevPage').addEventListener('click', () => { state.offset = Math.max(0, state.offset - state.limit); loadRows().catch((e) => notify(e.message, true)); });
$('nextPage').addEventListener('click', () => { state.offset += state.limit; loadRows().catch((e) => notify(e.message, true)); });
$('rows').addEventListener('change', (event) => {
  const tr = event.target.closest('tr[data-key]');
  if (!tr) return;
  const row = state.rows.find((r) => key(r) === tr.dataset.key);
  if (!row) return;
  const input = tr.querySelector('.qty-input');
  if (event.target.classList.contains('row-check')) {
    if (event.target.checked) state.selected.set(key(row), { supplier: row.supplier, code: row.code, quantity: Number(input.value) });
    else state.selected.delete(key(row));
  }
  updateSelection();
});
$('rows').addEventListener('input', (event) => {
  if (!event.target.classList.contains('qty-input')) return;
  const tr = event.target.closest('tr[data-key]');
  const selected = state.selected.get(tr?.dataset.key);
  if (selected) selected.quantity = Number(event.target.value);
});
$('rows').addEventListener('click', async (event) => {
  if (!event.target.classList.contains('save-stock')) return;
  const tr = event.target.closest('tr[data-key]');
  const row = state.rows.find((r) => key(r) === tr?.dataset.key);
  const input = tr?.querySelector('.stock-input');
  if (!row || !input || input.value === '') return notify(t('stockPrompt'), true);
  try {
    const result = await request('/api/stock', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ supplier: row.supplier, code: row.code, quantity: Number(input.value) }) });
    state.approvedCount = result.approved_count;
    renderMeta();
    await loadRows();
    notify(t('stockSaved'));
  } catch (error) { notify(error.message, true); }
});
$('approve').addEventListener('click', async () => {
  try {
    // Read the visible inputs at submission time as well; browser autofill and scripted edits
    // can update a value without emitting the events used to keep selection state in sync.
    $('rows').querySelectorAll('tr[data-key]').forEach((tr) => {
      const selected = state.selected.get(tr.dataset.key);
      if (selected && tr.querySelector('.row-check')?.checked) {
        selected.quantity = Number(tr.querySelector('.qty-input')?.value);
      }
    });
    const result = await request('/api/approve', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ days: state.days, items: [...state.selected.values()] }) });
    state.approvedCount = result.approved_count;
    state.selected.clear();
    renderMeta();
    await loadRows();
    notify(t('approvedNotice', { count: fmt.format(result.approved) }));
  } catch (error) { notify(error.message, true); }
});
$('exportLink').addEventListener('click', (event) => {
  if (state.approvedCount === 0) return event.preventDefault();
  if (window.KazStatic) {
    event.preventDefault();
    try { window.KazStatic.exportCsv(); } catch (error) { notify(error.message, true); }
  }
});
}
if (page === 'home') $('salesLink').addEventListener('click', (event) => {
  if (!window.KazStatic) return;
  event.preventDefault();
  window.KazStatic.exportSales();
});
init().catch((error) => notify(error.message, true));
