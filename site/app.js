const $ = (id) => document.getElementById(id);
const state = { meta: null, rows: [], total: 0, offset: 0, limit: 100, selected: new Map(), days: 30, approvedCount: 0 };
const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const key = (row) => `${row.supplier}␟${row.code}`;
const positionWord = (count) => count % 10 === 1 && count % 100 !== 11 ? 'позиция' : count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14) ? 'позиции' : 'позиций';

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
  if (!response.ok) throw new Error(data?.error || `Ошибка ${response.status}`);
  return data;
}

function renderMeta() {
  const meta = state.meta;
  $('asOf').textContent = new Date(`${meta.as_of}T12:00:00`).toLocaleDateString('ru-RU');
  $('sourceMode').textContent = meta.mode === 'synthetic' ? 'Синтетический пример' : 'Выгрузки партнёра';
  $('salesLink').hidden = meta.mode !== 'synthetic';
  if (meta.mode === 'synthetic') $('salesLink').textContent = `${fmt.format(meta.transactions)} продаж · ${fmt.format(meta.customers)} ID клиентов ↗`;
  $('supplier').innerHTML = '<option value="">Все поставщики</option>' + meta.suppliers.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join('');
  $('category').innerHTML = '<option value="">Все категории</option>' + (meta.categories || []).map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join('');
  $('approvedCount').textContent = fmt.format(state.approvedCount);
  $('exportLink').classList.toggle('disabled', state.approvedCount === 0);
  $('exportLink').setAttribute('aria-disabled', String(state.approvedCount === 0));
  const warnings = meta.warnings || [];
  $('warnings').hidden = warnings.length === 0;
  $('warnings').innerHTML = warnings.length ? `<strong>Что важно учитывать в этих данных</strong><ul>${warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : '';
}

function renderSummary(summary) {
  $('statProducts').textContent = fmt.format(summary.products);
  $('statOrders').textContent = fmt.format(summary.orders);
  $('statReady').textContent = fmt.format(summary.ready);
  $('statMissing').textContent = fmt.format(summary.needs_stock);
}

function renderSuppliers(suppliers) {
  $('supplierCards').innerHTML = suppliers.map((item) => {
    const hasApproved = item.approved_positions > 0;
    const href = `/api/export.csv?supplier=${encodeURIComponent(item.supplier)}`;
    return `<article class="supplier-card">
      <div class="supplier-card-name">${esc(item.supplier)}</div>
      <div class="supplier-card-stats"><strong>${fmt.format(item.recommendations)}</strong><span>${positionWord(item.recommendations)} к заказу</span><strong>${fmt.format(item.approved_positions)}</strong><span>утверждено</span></div>
      <div class="supplier-card-actions"><button class="supplier-open" type="button" data-supplier="${esc(item.supplier)}">Показать позиции</button><a href="${esc(href)}" class="supplier-export ${hasApproved ? '' : 'disabled'}" aria-disabled="${!hasApproved}">CSV поставщика ↗</a></div>
    </article>`;
  }).join('');
}

async function loadAudit() {
  const data = await request('/api/audit');
  $('auditList').innerHTML = data.events.length ? data.events.slice(0, 10).map((event) => {
    const when = new Date(event.at).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    const detail = event.event === 'approval'
      ? `Утверждено ${fmt.format(event.approved_quantity)} шт. · рекомендация ${fmt.format(event.recommended_quantity)} шт.${event.previous_quantity == null ? '' : ` · ранее ${fmt.format(event.previous_quantity)} шт.`}`
      : `Остаток ${event.previous_stock == null ? 'не указан' : fmt.format(event.previous_stock)} → ${fmt.format(event.new_stock)} шт.`;
    return `<div class="audit-item"><time>${esc(when)}</time><div><strong>${esc(event.code)}</strong><span>${esc(event.supplier)}</span><p>${esc(detail)}</p></div></div>`;
  }).join('') : 'Пока нет действий закупщика.';
}

function renderRows() {
  if (!state.rows.length) {
    $('rows').innerHTML = '<tr><td colspan="8" class="empty">По выбранным условиям рекомендаций нет. Измените фильтры.</td></tr>';
  } else {
    $('rows').innerHTML = state.rows.map((row) => {
      const selected = state.selected.get(key(row));
      const quantity = selected?.quantity ?? row.quantity;
      const ready = row.status === 'ready' && (row.quantity || 0) > 0;
      const urgency = row.urgency === 'высокая' ? ['badge-high', 'Высокая'] : row.status === 'ready' ? ['badge-normal', 'Обычная'] : ['badge-missing', 'Нужны данные'];
      return `<tr data-key="${esc(key(row))}">
        <td><input class="row-check" type="checkbox" ${selected ? 'checked' : ''} ${ready ? '' : 'disabled'} aria-label="Выбрать ${esc(row.code)}"></td>
        <td><div class="item-name">${esc(row.name)}<span class="item-code">${esc(row.code)} · категория ${esc(row.category || '—')}</span></div>
          <div class="reason">${esc(row.reason || '')}${(row.flags || []).map((flag) => `<span class="flag">${esc(flag)}</span>`).join('')}</div></td>
        <td><span class="supplier-tag">${esc(row.supplier)}</span></td>
        <td>${row.forecast == null ? '—' : fmt.format(row.forecast)}</td>
        <td>${row.free_stock == null && row.status === 'needs_stock' ? `<div class="stock-entry"><input class="stock-input" type="number" min="0" step="1" placeholder="Остаток" aria-label="Остаток ${esc(row.code)}"><button class="save-stock" type="button">Сохранить</button></div>` : row.free_stock == null ? '—' : fmt.format(row.free_stock)}</td>
        <td>${row.inbound == null ? '—' : fmt.format(row.inbound)}</td>
        <td>${ready ? `<input class="qty-input" type="number" min="${row.moq}" step="${row.moq}" value="${quantity}" aria-label="Количество ${esc(row.code)}">` : '—'}</td>
        <td><span class="badge ${urgency[0]}">${urgency[1]}</span></td>
      </tr>`;
    }).join('');
  }
  const first = state.total ? state.offset + 1 : 0;
  const last = Math.min(state.offset + state.rows.length, state.total);
  $('paginationText').textContent = `${fmt.format(first)}–${fmt.format(last)} из ${fmt.format(state.total)}`;
  $('prevPage').disabled = state.offset === 0;
  $('nextPage').disabled = state.offset + state.limit >= state.total;
  updateSelection();
}

function updateSelection() {
  const count = state.selected.size;
  $('selectionCount').textContent = `${count} ${positionWord(count)} ${count === 1 ? 'выбрана' : 'выбрано'}`;
  $('approve').disabled = count === 0;
  $('actionBar').hidden = count === 0;
}

async function loadRows() {
  const params = new URLSearchParams({ days: String(state.days), supplier: $('supplier').value, category: $('category').value, q: $('search').value.trim(), orders: $('ordersOnly').checked ? '1' : '0', offset: String(state.offset), limit: String(state.limit) });
  const data = await request(`/api/recommendations?${params}`);
  state.rows = data.rows;
  state.total = data.total;
  renderSummary(data.summary);
  renderSuppliers(data.suppliers);
  renderRows();
}

function clearAndLoad() {
  state.offset = 0;
  state.selected.clear();
  loadRows().catch((error) => notify(error.message, true));
}

async function init() {
  state.meta = await request('/api/meta');
  state.approvedCount = state.meta.approved_count;
  renderMeta();
  await loadRows();
  await loadAudit();
}

$('supplierCards').addEventListener('click', (event) => {
  const link = event.target.closest('a.supplier-export');
  if (link?.classList.contains('disabled')) event.preventDefault();
  else if (link && window.KazStatic) {
    event.preventDefault();
    try { window.KazStatic.exportCsv(new URL(link.href).searchParams.get('supplier')); }
    catch (error) { notify(error.message, true); }
  }
  const button = event.target.closest('button.supplier-open');
  if (!button) return;
  $('supplier').value = button.dataset.supplier;
  clearAndLoad();
  $('supplier').scrollIntoView({ behavior: 'smooth', block: 'center' });
});

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
  } else if (event.target.classList.contains('qty-input') && state.selected.has(key(row))) {
    state.selected.get(key(row)).quantity = Number(input.value);
  }
  updateSelection();
});
$('rows').addEventListener('click', async (event) => {
  if (!event.target.classList.contains('save-stock')) return;
  const tr = event.target.closest('tr[data-key]');
  const row = state.rows.find((r) => key(r) === tr?.dataset.key);
  const input = tr?.querySelector('.stock-input');
  if (!row || !input || input.value === '') return notify('Введите свободный остаток на дату среза', true);
  try {
    const result = await request('/api/stock', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ supplier: row.supplier, code: row.code, quantity: Number(input.value) }) });
    state.approvedCount = result.approved_count;
    renderMeta();
    await loadRows();
    await loadAudit();
    notify('Остаток сохранён. Рекомендация пересчитана.');
  } catch (error) { notify(error.message, true); }
});
$('approve').addEventListener('click', async () => {
  try {
    const result = await request('/api/approve', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ days: state.days, items: [...state.selected.values()] }) });
    state.approvedCount = result.approved_count;
    state.selected.clear();
    renderMeta();
    await loadRows();
    await loadAudit();
    notify(`${result.approved} ${positionWord(result.approved)} утверждено. CSV доступен для выгрузки.`);
  } catch (error) { notify(error.message, true); }
});
$('exportLink').addEventListener('click', (event) => {
  if (state.approvedCount === 0) return event.preventDefault();
  if (window.KazStatic) {
    event.preventDefault();
    try { window.KazStatic.exportCsv(); } catch (error) { notify(error.message, true); }
  }
});
$('salesLink').addEventListener('click', (event) => {
  if (!window.KazStatic) return;
  event.preventDefault();
  window.KazStatic.exportSales();
});
init().catch((error) => notify(error.message, true));
