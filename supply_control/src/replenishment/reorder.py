from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable

from .models import (
    ForecastResult,
    InventoryState,
    PurchaseRecommendation,
    RecommendationStatus,
    SkuPolicy,
)


DAYS_PER_MONTH = 365.25 / 12.0


def _blocking_reasons(policy: SkuPolicy) -> list[str]:
    reasons: list[str] = []
    if not policy.sku.strip():
        reasons.append("missing_sku")
    if not policy.supplier or not policy.supplier.strip():
        reasons.append("missing_supplier")
    if policy.moq is None or policy.moq <= 0:
        reasons.append("missing_or_invalid_moq")
    if policy.lead_time_days is None or policy.lead_time_days < 0:
        reasons.append("missing_or_invalid_lead_time")
    if policy.review_period_days is None or policy.review_period_days <= 0:
        reasons.append("missing_or_invalid_review_period")
    if policy.safety_stock_days is None or policy.safety_stock_days < 0:
        reasons.append("missing_or_invalid_safety_stock")
    return reasons


def _project_stockout_date(
    *,
    as_of: date,
    free_stock: float,
    daily_demand: float,
    inbound: Iterable,
) -> date | None:
    if daily_demand <= 0:
        return None
    stock = free_stock
    cursor = as_of
    for shipment in sorted(inbound, key=lambda item: item.expected_date):
        days_until_arrival = max(0, (shipment.expected_date - cursor).days)
        demand_until_arrival = daily_demand * days_until_arrival
        if demand_until_arrival > stock:
            days_left = max(0, math.floor(stock / daily_demand))
            return cursor + timedelta(days=days_left)
        stock -= demand_until_arrival
        stock += max(0.0, shipment.quantity)
        cursor = shipment.expected_date
    days_left = max(0, math.floor(stock / daily_demand))
    return cursor + timedelta(days=days_left)


def calculate_recommendation(
    policy: SkuPolicy,
    inventory: InventoryState,
    forecast: ForecastResult,
    *,
    as_of: date,
) -> PurchaseRecommendation:
    blockers = _blocking_reasons(policy)
    free_stock = inventory.free_stock
    if blockers:
        explanation = "Расчёт заблокирован: " + ", ".join(blockers) + "."
        return PurchaseRecommendation(
            sku=policy.sku,
            supplier=policy.supplier,
            status=RecommendationStatus.BLOCKED,
            recommended_quantity=None,
            urgency="unknown",
            forecast_monthly_units=forecast.monthly_units,
            cover_days=None,
            target_stock=None,
            free_stock=free_stock,
            eligible_inbound=0.0,
            excluded_inbound=sum(max(0.0, item.quantity) for item in inventory.inbound),
            raw_need=None,
            moq=policy.moq,
            projected_stockout_date=None,
            explanation=explanation,
            reasons=tuple(blockers),
        )

    assert policy.lead_time_days is not None
    assert policy.review_period_days is not None
    assert policy.safety_stock_days is not None
    assert policy.moq is not None

    cover_days = float(policy.lead_time_days + policy.review_period_days)
    coverage_end = as_of + timedelta(days=math.ceil(cover_days))
    eligible_inbound = sum(
        max(0.0, shipment.quantity)
        for shipment in inventory.inbound
        if shipment.expected_date <= coverage_end
    )
    excluded_inbound = sum(
        max(0.0, shipment.quantity)
        for shipment in inventory.inbound
        if shipment.expected_date > coverage_end
    )
    daily_demand = forecast.monthly_units / DAYS_PER_MONTH
    target_stock = daily_demand * (cover_days + policy.safety_stock_days)
    raw_need = max(0.0, target_stock - free_stock - eligible_inbound)
    recommended = (
        0.0 if raw_need == 0 else math.ceil(raw_need / policy.moq) * policy.moq
    )

    eligible_shipments = tuple(
        shipment
        for shipment in inventory.inbound
        if shipment.expected_date <= coverage_end and shipment.quantity > 0
    )
    stockout_date = _project_stockout_date(
        as_of=as_of,
        free_stock=free_stock,
        daily_demand=daily_demand,
        inbound=eligible_shipments,
    )

    late_inbound = bool(
        stockout_date
        and any(
            stockout_date < shipment.expected_date <= coverage_end and shipment.quantity > 0
            for shipment in eligible_shipments
        )
    )
    review_reasons = list(forecast.review_reasons)
    if late_inbound:
        review_reasons.append("inbound_arrives_after_projected_stockout")

    if recommended == 0:
        status = (
            RecommendationStatus.REVIEW_REQUIRED
            if review_reasons
            else RecommendationStatus.NO_ORDER
        )
        urgency = "none"
    else:
        status = (
            RecommendationStatus.REVIEW_REQUIRED
            if review_reasons
            else RecommendationStatus.READY_FOR_APPROVAL
        )
        if stockout_date and stockout_date <= as_of + timedelta(days=policy.lead_time_days):
            urgency = "critical"
        elif stockout_date and stockout_date <= coverage_end:
            urgency = "high"
        else:
            urgency = "normal"

    explanation = (
        f"Прогноз {forecast.monthly_units:.1f} шт./мес.; горизонт {cover_days:.0f} дн. "
        f"+ страховой запас {policy.safety_stock_days:.0f} дн.; целевой запас "
        f"{target_stock:.1f}; свободный остаток {free_stock:.1f}; учитываемый товар "
        f"в пути {eligible_inbound:.1f}; чистая потребность {raw_need:.1f}; "
        f"MOQ {policy.moq:g}; рекомендация {recommended:g}."
    )
    return PurchaseRecommendation(
        sku=policy.sku,
        supplier=policy.supplier,
        status=status,
        recommended_quantity=recommended,
        urgency=urgency,
        forecast_monthly_units=forecast.monthly_units,
        cover_days=cover_days,
        target_stock=target_stock,
        free_stock=free_stock,
        eligible_inbound=eligible_inbound,
        excluded_inbound=excluded_inbound,
        raw_need=raw_need,
        moq=policy.moq,
        projected_stockout_date=stockout_date,
        explanation=explanation,
        reasons=tuple(dict.fromkeys(review_reasons)),
    )


def group_by_supplier(
    recommendations: Iterable[PurchaseRecommendation],
) -> dict[str, list[PurchaseRecommendation]]:
    grouped: dict[str, list[PurchaseRecommendation]] = defaultdict(list)
    for recommendation in recommendations:
        key = recommendation.supplier or "UNASSIGNED"
        grouped[key].append(recommendation)
    urgency_rank = {"critical": 0, "high": 1, "normal": 2, "none": 3, "unknown": 4}
    return {
        supplier: sorted(items, key=lambda item: (urgency_rank.get(item.urgency, 9), item.sku))
        for supplier, items in sorted(grouped.items())
    }
