from __future__ import annotations

import calendar
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass(frozen=True)
class Arrival:
    due: date
    quantity: float


@dataclass(frozen=True)
class Sale:
    month: str
    quantity: float
    document: str
    customer_id: str | None = None


@dataclass
class Product:
    supplier: str
    code: str
    name: str
    monthly_sales: dict[str, float]
    opening_stock: dict[str, float] = field(default_factory=dict)
    free_stock: float | None = None
    stock_as_of: date | None = None
    inbound: list[Arrival] = field(default_factory=list)
    category: str | None = None
    source_growth: float | None = None
    moq: int = 1
    sales: list[Sale] = field(default_factory=list)
    stockout_days: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ForecastSettings:
    as_of: date
    coverage_days: int = 30

    def __post_init__(self) -> None:
        if not 1 <= self.coverage_days <= 180:
            raise ValueError("coverage_days must be between 1 and 180")


def _month_date(key: str) -> date:
    return date.fromisoformat(key + "-01")


def _closed_months(values: dict[str, float], as_of: date) -> list[str]:
    return sorted(k for k in values if _month_date(k).replace(day=calendar.monthrange(*map(int, k.split("-")))[1]) < as_of)


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _neighbor_baseline(series: dict[str, float], month: str) -> float:
    target = _month_date(month)
    neighbors = [
        max(0.0, value)
        for key, value in series.items()
        if key != month and abs((_month_date(key).year - target.year) * 12 + _month_date(key).month - target.month) <= 3
    ]
    return _median(neighbors)


def clean_demand(product: Product, as_of: date) -> tuple[dict[str, float], dict[str, float], dict[str, float], list[str]]:
    """Return adjusted monthly demand, excluded spikes, imputed stockout demand and flags."""
    months = _closed_months(product.monthly_sales, as_of)
    raw = {m: max(0.0, float(product.monthly_sales[m])) for m in months}
    adjusted = dict(raw)
    excluded: dict[str, float] = defaultdict(float)
    imputed: dict[str, float] = defaultdict(float)
    flags: list[str] = []

    # One-off order detection uses an anonymized customer where supplied, or a
    # document number. The supplied partner exports have no customer ID.
    positive = [s.quantity for s in product.sales if s.month in raw and s.quantity > 0]
    typical = _median(positive)
    deviation = _median([abs(q - typical) for q in positive])
    order_threshold = max(20.0, 5 * typical, typical + 6 * deviation)
    grouped: dict[tuple[str, str], float] = defaultdict(float)
    detail_total: dict[str, float] = defaultdict(float)
    for sale in product.sales:
        if sale.month not in raw or sale.quantity <= 0:
            continue
        key = sale.customer_id or sale.document
        grouped[(sale.month, key)] += sale.quantity
        detail_total[sale.month] += sale.quantity
    for (month, _), quantity in grouped.items():
        if quantity < order_threshold or quantity < raw[month] * 0.45:
            continue
        # Avoid subtracting from a monthly series that does not reconcile to
        # the detailed transaction export.
        if abs(detail_total[month] - raw[month]) > max(5, raw[month] * 0.15):
            if "Детальные и месячные продажи расходятся" not in flags:
                flags.append("Детальные и месячные продажи расходятся")
            continue
        peer = _neighbor_baseline(raw, month)
        if raw[month] <= max(peer * 2.5, peer + 20):
            continue
        removed = min(quantity, adjusted[month])
        adjusted[month] -= removed
        excluded[month] += removed

    # Monthly spikes catch large anonymous orders when customer IDs are absent.
    for month in months:
        peer = _neighbor_baseline(raw, month)
        if peer <= 0 or len(months) < 7:
            continue
        value = adjusted[month]
        prev_year = f"{int(month[:4]) - 1}{month[4:]}"
        if prev_year in raw and raw[prev_year] >= value * 0.55:
            continue  # recurrent seasonal peak
        if value > max(peer * 3, peer + 20):
            excluded[month] += value - peer
            adjusted[month] = peer

    # Explicit stockout days are authoritative. Zero opening stock is only an
    # estimate and is flagged separately in the explanation.
    for month in months:
        day_count = calendar.monthrange(*map(int, month.split("-")))[1]
        days = min(day_count, max(0, product.stockout_days.get(month, 0)))
        if days:
            peer = _neighbor_baseline(adjusted, month)
            estimate = peer if days == day_count else adjusted[month] * day_count / (day_count - days)
            estimate = max(adjusted[month], min(estimate, max(peer * 2, adjusted[month] * 3)))
            imputed[month] = estimate - adjusted[month]
            adjusted[month] = estimate
        elif month in product.opening_stock and product.opening_stock[month] <= 0:
            peer = _neighbor_baseline(adjusted, month)
            if peer > 0 and adjusted[month] < peer * 0.6:
                imputed[month] = peer - adjusted[month]
                adjusted[month] = peer
                if "Дефицит оценён по нулевому начальному остатку" not in flags:
                    flags.append("Дефицит оценён по нулевому начальному остатку")
    return adjusted, dict(excluded), dict(imputed), flags


def _seasonal_index(series: dict[str, float]) -> dict[int, float]:
    by_year: dict[int, dict[int, float]] = defaultdict(dict)
    for month, value in series.items():
        y, m = map(int, month.split("-"))
        by_year[y][m] = max(0.0, value)
    factors: dict[int, list[float]] = defaultdict(list)
    for values in by_year.values():
        if len(values) < 10:
            continue
        annual_avg = sum(values.values()) / len(values)
        if annual_avg <= 0:
            continue
        for m, value in values.items():
            factors[m].append(_clamp(value / annual_avg, 0.3, 3.0))
    return {m: _clamp(_median(factors.get(m, [])), 0.4, 2.5) if factors.get(m) else 1.0 for m in range(1, 13)}


def seasonal_context(products: list[Product], as_of: date) -> dict[tuple[str, str], dict[int, float]]:
    grouped: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for p in products:
        key = (p.supplier, p.category or "__supplier__")
        for month in _closed_months(p.monthly_sales, as_of):
            grouped[key][month] += max(0.0, p.monthly_sales[month])
            grouped[(p.supplier, "__supplier__")][month] += max(0.0, p.monthly_sales[month]) if key[1] != "__supplier__" else 0
    return {key: _seasonal_index(values) for key, values in grouped.items()}


def _trend(series: dict[str, float], source_growth: float | None) -> float:
    values = [series[m] for m in sorted(series)[-12:]]
    observed = 0.0
    if len(values) >= 12 and sum(values[:6]) > 0:
        observed = _clamp((sum(values[6:]) / sum(values[:6]) - 1) * 0.5, -0.4, 0.8)
    if source_growth is None or not math.isfinite(source_growth):
        return observed
    return _clamp(0.5 * observed + 0.5 * _clamp(source_growth, -0.6, 1.0), -0.5, 0.9)


def _forecast_days(as_of: date, days: int, monthly_rate: float, season: dict[int, float], growth: float) -> float:
    result = 0.0
    for offset in range(1, days + 1):
        day = as_of + timedelta(days=offset)
        result += monthly_rate * season[day.month] * (1 + growth) / calendar.monthrange(day.year, day.month)[1]
    return max(0.0, result)


def recommend(product: Product, settings: ForecastSettings, context: dict[tuple[str, str], dict[int, float]]) -> dict:
    adjusted, excluded, imputed, flags = clean_demand(product, settings.as_of)
    months = sorted(adjusted)
    if len(months) < 3:
        return {"supplier": product.supplier, "code": product.code, "name": product.name,
                "status": "insufficient_history", "quantity": None, "reason": "Недостаточно истории продаж"}
    trailing = months[-12:]
    base = sum(adjusted[m] for m in trailing) / len(trailing)
    own_season = _seasonal_index(adjusted)
    peer = context.get((product.supplier, product.category or "__supplier__"),
                       context.get((product.supplier, "__supplier__"), {m: 1.0 for m in range(1, 13)}))
    own_weight = 0.7 if sum(v > 0 for v in adjusted.values()) >= 12 else 0.25
    season = {m: _clamp(own_weight * own_season[m] + (1 - own_weight) * peer[m], 0.4, 2.5) for m in range(1, 13)}
    growth = _trend(adjusted, product.source_growth)
    forecast = _forecast_days(settings.as_of, settings.coverage_days, base, season, growth)
    horizon_end = settings.as_of + timedelta(days=settings.coverage_days)
    inbound = sum(max(0.0, a.quantity) for a in product.inbound if settings.as_of < a.due <= horizon_end)
    stock = product.free_stock if product.stock_as_of == settings.as_of else None
    if product.free_stock is not None and product.stock_as_of != settings.as_of:
        flags.append("Остаток не на дату расчёта")
    if stock is not None and stock < 0:
        flags.append("Отрицательный остаток учтён как ноль")
    quantity = None
    shortage = None
    status = "needs_stock" if stock is None else "ready"
    if stock is not None:
        shortage = max(0.0, forecast - max(0.0, stock) - inbound)
        multiple = max(1, int(product.moq))
        quantity = math.ceil((shortage - 1e-9) / multiple) * multiple if shortage > 1e-9 else 0
    soon_days = min(14, settings.coverage_days)
    soon_demand = _forecast_days(settings.as_of, soon_days, base, season, growth)
    soon_inbound = sum(max(0.0, a.quantity) for a in product.inbound
                       if settings.as_of < a.due <= settings.as_of + timedelta(days=soon_days))
    urgency = "данные" if stock is None else "высокая" if max(0.0, stock) + soon_inbound < soon_demand else "обычная"
    reason = (
        f"Спрос на {settings.coverage_days} дн.: {forecast:.1f}; "
        f"свободный остаток: {stock:.0f}; " if stock is not None else
        f"Спрос на {settings.coverage_days} дн.: {forecast:.1f}; текущий остаток не предоставлен; "
    ) + f"в пути до {horizon_end:%d.%m}: {inbound:.0f}; рост: {growth:+.0%}; кратность: {max(1, product.moq)}."
    if shortage is not None:
        reason += f" Потребность до округления: {shortage:.1f}; предложено: {quantity}."
    if excluded:
        reason += f" Исключено разовых всплесков: {sum(excluded.values()):.0f}."
    if imputed:
        reason += f" Восстановлено спроса при дефиците: {sum(imputed.values()):.0f}."
    return {
        "supplier": product.supplier, "code": product.code, "name": product.name,
        "category": product.category or "—", "status": status, "quantity": quantity,
        "urgency": urgency, "forecast": round(forecast, 2), "base_monthly": round(base, 2),
        "free_stock": stock, "inbound": round(inbound, 2), "moq": max(1, product.moq),
        "growth": round(growth, 4), "seasonal_factor": round(season[(settings.as_of + timedelta(days=1)).month], 3),
        "excluded_outliers": round(sum(excluded.values()), 2),
        "imputed_stockouts": round(sum(imputed.values()), 2),
        "flags": list(dict.fromkeys(product.warnings + flags)), "reason": reason,
    }


def recommend_all(products: list[Product], settings: ForecastSettings) -> list[dict]:
    context = seasonal_context(products, settings.as_of)
    rows = [recommend(p, settings, context) for p in products]
    return sorted(rows, key=lambda r: (r["supplier"], r["status"] != "ready", r.get("urgency") != "высокая", -(r["quantity"] or 0), r["code"]))
