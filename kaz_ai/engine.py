from __future__ import annotations

import calendar
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .parameters import (
    Confidence,
    ParameterSet,
    ParameterSource,
    ParameterStatus,
    ParameterValue,
    missing_parameter,
)


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
    moq: int | None = None
    lead_time_days: int | None = None
    safety_days: int | None = None
    sales: list[Sale] = field(default_factory=list)
    stockout_days: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    parameters: ParameterSet = field(default_factory=ParameterSet)


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
    return sorted(k for k in values if _month_date(k).replace(day=calendar.monthrange(*map(int, k.split("-")))[1]) <= as_of)


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


def clean_demand(product: Product, as_of: date) -> tuple[dict[str, float], dict[str, float], dict[str, float], list[str], dict[str, float]]:
    """Return adjusted demand, excluded spikes, stockout demand, flags and excluded clients."""
    months = _closed_months(product.monthly_sales, as_of)
    raw = {m: max(0.0, float(product.monthly_sales[m])) for m in months}
    adjusted = dict(raw)
    excluded: dict[str, float] = defaultdict(float)
    imputed: dict[str, float] = defaultdict(float)
    excluded_clients: dict[str, float] = defaultdict(float)
    flags: list[str] = []

    # One-off order detection uses an anonymized customer where supplied, or a
    # document number. The supplied partner exports have no customer ID.
    positive = [s.quantity for s in product.sales if s.month in raw and s.quantity > 0]
    typical = _median(positive)
    deviation = _median([abs(q - typical) for q in positive])
    order_threshold = max(20.0, 5 * typical, typical + 6 * deviation)
    grouped: dict[tuple[str, str, str], float] = defaultdict(float)
    detail_total: dict[str, float] = defaultdict(float)
    for sale in product.sales:
        if sale.month not in raw or sale.quantity <= 0:
            continue
        group_type = "customer" if sale.customer_id else "document"
        grouped[(sale.month, group_type, sale.customer_id or sale.document)] += sale.quantity
        detail_total[sale.month] += sale.quantity
    for (month, group_type, identifier), quantity in grouped.items():
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
        if group_type == "customer":
            excluded_clients[identifier] += removed

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
    return adjusted, dict(excluded), dict(imputed), flags, dict(excluded_clients)


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


def _resolved_parameter(
    product: Product,
    name: str,
    legacy_value: float | int | None,
    unit: str,
    missing_note: str,
) -> ParameterValue:
    explicit = product.parameters.get(name)
    if explicit is not None:
        return explicit
    if legacy_value is None:
        return missing_parameter(unit, missing_note)
    return ParameterValue(
        legacy_value,
        ParameterStatus.CONFIRMED,
        ParameterSource.LEGACY_FIELD,
        Confidence.MEDIUM,
        unit,
        "Значение из существующей модели без отдельной записи происхождения",
    )


def _parameter_text(parameter: ParameterValue, fallback: int = 0) -> str:
    if parameter.status == ParameterStatus.MISSING:
        return "не задан"
    status = {
        ParameterStatus.CONFIRMED: "подтверждено",
        ParameterStatus.USER_INPUT: "ввод пользователя",
        ParameterStatus.ASSUMPTION: "сценарий",
    }[parameter.status]
    return f"{int(parameter.value or fallback)} дн. ({status})"


def recommend(product: Product, settings: ForecastSettings, context: dict[tuple[str, str], dict[int, float]]) -> dict:
    adjusted, excluded, imputed, flags, excluded_clients = clean_demand(product, settings.as_of)
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
    lead_parameter = _resolved_parameter(
        product, "lead_time_days", product.lead_time_days, "days", "Срок поставки не предоставлен"
    )
    safety_parameter = _resolved_parameter(
        product, "safety_days", product.safety_days, "days", "Страховой запас не предоставлен"
    )
    moq_parameter = _resolved_parameter(
        product, "moq", product.moq, "units", "Кратность заказа не предоставлена"
    )
    unit_cost_parameter = product.parameters.get("unit_cost") or missing_parameter(
        "KZT", "Закупочная цена не предоставлена"
    )
    resolved_parameters = ParameterSet({
        "lead_time_days": lead_parameter,
        "safety_days": safety_parameter,
        "moq": moq_parameter,
        "unit_cost": unit_cost_parameter,
    })
    terms_readiness = resolved_parameters.readiness(("lead_time_days", "safety_days", "moq"))
    lead_days = max(0, int(lead_parameter.value or 0))
    safety_days = max(0, int(safety_parameter.value or 0))
    moq = max(1, int(moq_parameter.value or 1))
    target_days = settings.coverage_days + lead_days + safety_days
    coverage_forecast = _forecast_days(settings.as_of, settings.coverage_days, base, season, growth)
    forecast = _forecast_days(settings.as_of, target_days, base, season, growth)
    horizon_end = settings.as_of + timedelta(days=target_days)
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
        quantity = math.ceil((shortage - 1e-9) / moq) * moq if shortage > 1e-9 else 0
    soon_days = lead_days if lead_days else min(14, settings.coverage_days)
    soon_demand = _forecast_days(settings.as_of, soon_days, base, season, growth)
    soon_inbound = sum(max(0.0, a.quantity) for a in product.inbound
                       if settings.as_of < a.due <= settings.as_of + timedelta(days=soon_days))
    risk_before_delivery = max(0.0, soon_demand - max(0.0, stock) - soon_inbound) if stock is not None else None
    urgency = "данные" if stock is None else "высокая" if risk_before_delivery > 0 else "обычная"
    reason = (
        f"Спрос на {target_days} дн.: {forecast:.1f}; "
        f"свободный остаток: {stock:.0f}; " if stock is not None else
        f"Спрос на {target_days} дн.: {forecast:.1f}; текущий остаток не предоставлен; "
    ) + (f"покрытие: {settings.coverage_days} дн.; срок поставки: {_parameter_text(lead_parameter)}; "
         f"страховой запас: {_parameter_text(safety_parameter)} (+{forecast - coverage_forecast:.1f} к спросу); "
         f"в пути до {horizon_end:%d.%m}: {inbound:.0f}; рост: {growth:+.0%}; "
         f"кратность: {moq}.")
    if lead_parameter.status == ParameterStatus.MISSING:
        flags.append("Срок поставки не задан; использован только выбранный горизонт")
    if safety_parameter.status == ParameterStatus.MISSING:
        flags.append("Политика страхового запаса не задана")
    if moq_parameter.status == ParameterStatus.MISSING:
        flags.append("Кратность заказа не задана; для сценария использована 1 единица")
    if terms_readiness["status"] == "scenario":
        flags.append("Расчёт использует сценарные параметры")
    elif terms_readiness["status"] == "blocked":
        flags.append("Параметры заказа неполные")
    if shortage is not None:
        reason += f" Потребность до округления: {shortage:.1f}; предложено: {quantity}."
    if excluded:
        reason += f" Исключено разовых всплесков: {sum(excluded.values()):.0f}."
    if excluded_clients:
        details = ", ".join(f"{identifier}: {amount:.0f}" for identifier, amount in sorted(excluded_clients.items()))
        reason += f" Из них по ID клиента: {details}."
    if imputed:
        reason += f" Восстановлено спроса при дефиците: {sum(imputed.values()):.0f}."
    return {
        "supplier": product.supplier, "code": product.code, "name": product.name,
        "category": product.category or "—", "status": status, "quantity": quantity,
        "urgency": urgency, "forecast": round(forecast, 2), "base_monthly": round(base, 2),
        "coverage_forecast": round(coverage_forecast, 2), "target_days": target_days,
        "lead_time_days": lead_parameter.value, "safety_days": safety_parameter.value,
        "lead_demand": round(soon_demand, 2), "lead_inbound": round(soon_inbound, 2),
        "risk_before_delivery": round(risk_before_delivery, 2) if risk_before_delivery is not None else None,
        "free_stock": stock, "inbound": round(inbound, 2), "moq": moq,
        "unit_cost": unit_cost_parameter.value,
        "order_value": round(quantity * float(unit_cost_parameter.value), 2)
        if quantity is not None and unit_cost_parameter.value is not None else None,
        "pricing_status": unit_cost_parameter.status.value,
        "parameter_trace": resolved_parameters.trace(("lead_time_days", "safety_days", "moq", "unit_cost")),
        "terms_readiness": terms_readiness,
        "growth": round(growth, 4), "seasonal_factor": round(season[(settings.as_of + timedelta(days=1)).month], 3),
        "excluded_outliers": round(sum(excluded.values()), 2),
        "excluded_customers": {identifier: round(amount, 2) for identifier, amount in excluded_clients.items()},
        "imputed_stockouts": round(sum(imputed.values()), 2),
        "flags": list(dict.fromkeys(product.warnings + flags)), "reason": reason,
    }


def recommend_all(products: list[Product], settings: ForecastSettings) -> list[dict]:
    context = seasonal_context(products, settings.as_of)
    rows = [recommend(p, settings, context) for p in products]
    return sorted(rows, key=lambda r: (r["supplier"], r["status"] != "ready", r.get("urgency") != "высокая", -(r["quantity"] or 0), r["code"]))
