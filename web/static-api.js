/* Public demo adapter. All data is fictional; decisions remain in this browser. */
(() => {
  const data = window.KAZ_STATIC_DATA;
  const storageKey = `kaz-ai-${data.meta.dataset_id}`;
  const empty = () => ({ approved: {}, stocks: {}, audit: [] });
  let state;
  try { state = JSON.parse(localStorage.getItem(storageKey) || 'null') || empty(); }
  catch { state = empty(); }
  state.approved ||= {};
  state.stocks ||= {};
  state.audit ||= [];
  const key = (supplier, code) => `${supplier}␟${code}`;
  const persist = () => localStorage.setItem(storageKey, JSON.stringify(state));

  function rowsFor(days) {
    const source = data.rows[String(days)];
    if (!source) throw new Error('Недопустимый горизонт расчёта');
    return source.map((original) => {
      const row = { ...original, flags: [...(original.flags || [])] };
      const stock = state.stocks[key(row.supplier, row.code)];
      if (stock === undefined || row.status === 'insufficient_history') return row;
      row.free_stock = stock;
      row.status = 'ready';
      const shortage = Math.max(0, row.forecast - stock - row.inbound);
      row.quantity = shortage > 1e-9 ? Math.ceil((shortage - 1e-9) / row.moq) * row.moq : 0;
      row.risk_before_delivery = Math.max(0, row.lead_demand - stock - row.lead_inbound);
      row.urgency = row.risk_before_delivery > 0 ? 'высокая' : 'обычная';
      row.reason = row.reason.replace('текущий остаток не предоставлен;', `свободный остаток: ${stock};`);
      if (!row.reason.includes('Потребность до округления')) row.reason += ` Потребность до округления: ${shortage.toFixed(1)}; предложено: ${row.quantity}.`;
      if (!row.flags.includes('Остаток внесён вручную')) row.flags.push('Остаток внесён вручную');
      return row;
    });
  }

  function supplierSummary(rows) {
    return data.meta.suppliers.map((supplier) => {
      const recommendations = rows.filter((row) => row.supplier === supplier && row.quantity > 0);
      const approved = Object.values(state.approved).filter((item) => item.supplier === supplier);
      return { supplier, recommendations: recommendations.length,
        recommended_units: recommendations.reduce((total, row) => total + row.quantity, 0),
        approved_positions: approved.length,
        approved_units: approved.reduce((total, item) => total + item.quantity, 0) };
    });
  }

  async function request(rawUrl, options = {}) {
    const url = new URL(rawUrl, location.origin);
    if (url.pathname === '/api/meta') return { ...data.meta, approved_count: Object.keys(state.approved).length };
    if (url.pathname === '/api/audit') return { events: [...state.audit].reverse().slice(0, 30) };
    if (url.pathname === '/api/recommendations') {
      const days = Number(url.searchParams.get('days') || 30);
      const rows = rowsFor(days);
      const supplier = url.searchParams.get('supplier') || '';
      const category = url.searchParams.get('category') || '';
      const search = (url.searchParams.get('q') || '').trim().toLocaleLowerCase();
      const onlyOrders = url.searchParams.get('orders') !== '0';
      const offset = Math.max(0, Number(url.searchParams.get('offset') || 0));
      const limit = Math.min(250, Math.max(1, Number(url.searchParams.get('limit') || 100)));
      const filtered = rows.filter((row) => (!supplier || row.supplier === supplier)
        && (!category || row.category === category)
        && (!search || row.code.toLocaleLowerCase().includes(search) || row.name.toLocaleLowerCase().includes(search))
        && (!onlyOrders || row.quantity > 0));
      return { rows: filtered.slice(offset, offset + limit), total: filtered.length, offset,
        suppliers: supplierSummary(rows),
        summary: { products: rows.length, ready: rows.filter((row) => row.status === 'ready').length,
          orders: rows.filter((row) => row.quantity > 0).length,
          needs_stock: rows.filter((row) => row.status === 'needs_stock').length,
          insufficient_history: rows.filter((row) => row.status === 'insufficient_history').length } };
    }
    const body = JSON.parse(options.body || '{}');
    if (url.pathname === '/api/stock') {
      const product = data.rows['30'].find((row) => row.supplier === body.supplier && row.code === body.code);
      const amount = Number(body.quantity);
      if (!product) throw new Error('Артикул не найден');
      if (!Number.isFinite(amount) || amount < 0 || amount > 100000000) throw new Error('Некорректный остаток');
      const itemKey = key(body.supplier, body.code);
      const previous = state.stocks[itemKey] ?? product.free_stock;
      state.stocks[itemKey] = amount;
      delete state.approved[itemKey];
      state.audit.push({ event: 'stock', at: new Date().toISOString(), supplier: body.supplier,
        code: body.code, previous_stock: previous, new_stock: amount });
      persist();
      return { saved: true, approved_count: Object.keys(state.approved).length };
    }
    if (url.pathname === '/api/approve') {
      const days = Number(body.days || 30);
      const available = new Map(rowsFor(days).map((row) => [key(row.supplier, row.code), row]));
      if (!Array.isArray(body.items) || !body.items.length || body.items.length > 500) throw new Error('Выберите от 1 до 500 позиций');
      const seen = new Set();
      const prepared = body.items.map((item) => {
        const itemKey = key(item.supplier, item.code);
        const row = available.get(itemKey);
        const amount = Number(item.quantity);
        if (seen.has(itemKey)) throw new Error('Позиция выбрана повторно');
        seen.add(itemKey);
        if (!row || row.status !== 'ready') throw new Error(`Нет подтверждённого остатка для ${item.code}`);
        if (!Number.isInteger(amount) || amount <= 0 || amount > 10000000 || amount % row.moq) throw new Error(`Количество ${item.code} должно быть положительным и кратным ${row.moq}`);
        return { itemKey, row, amount };
      });
      for (const { itemKey, row, amount } of prepared) {
        const previous = state.approved[itemKey];
        const at = new Date().toISOString();
        state.approved[itemKey] = { supplier: row.supplier, code: row.code, name: row.name,
          quantity: amount, recommended_quantity: row.quantity, reason: row.reason,
          as_of: data.meta.as_of, coverage_days: days, approved_at: at };
        state.audit.push({ event: 'approval', at, supplier: row.supplier, code: row.code,
          recommended_quantity: row.quantity, approved_quantity: amount,
          previous_quantity: previous?.quantity ?? null, coverage_days: days });
      }
      persist();
      return { approved: prepared.length, approved_count: Object.keys(state.approved).length };
    }
    throw new Error('Неизвестный адрес демоверсии');
  }

  function csvField(value) {
    let text = String(value ?? '');
    if (/^\s*[=+\-@]/.test(text)) text = `'${text}`;
    return /[;"\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  }
  function download(rows, filename) {
    const content = '\ufeff' + rows.map((row) => row.map(csvField).join(';')).join('\r\n') + '\r\n';
    const url = URL.createObjectURL(new Blob([content], { type: 'text/csv;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function exportCsv(supplier = null) {
    const items = Object.values(state.approved).filter((item) => !supplier || item.supplier === supplier)
      .sort((a, b) => a.supplier.localeCompare(b.supplier) || a.code.localeCompare(b.code));
    if (!items.length) throw new Error('Нет утверждённых позиций');
    download([
      ['Поставщик', 'Код 1С', 'Наименование', 'Количество', 'Рекомендация', 'Дата среза', 'Горизонт, дни', 'Обоснование'],
      ...items.map((item) => [item.supplier, item.code, item.name, item.quantity,
        item.recommended_quantity, item.as_of, item.coverage_days, item.reason]),
    ], supplier ? 'supplier_order.csv' : 'supplier_orders.csv');
  }
  function exportSales() {
    download([['Поставщик', 'Артикул', 'Месяц', 'Документ', 'ID клиента', 'Количество'],
      ...data.sales.map((sale) => [sale.supplier, sale.code, sale.month, sale.document,
        sale.customer_id, sale.quantity])], 'synthetic_customer_sales.csv');
  }
  window.KazStatic = { request, exportCsv, exportSales };
})();
