from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Mapping, Sequence

from .loaders import SourceSkuRecord
from .models import ForecastResult, PurchaseRecommendation, RecommendationStatus
from .reorder import DAYS_PER_MONTH, calculate_recommendation
from .scenario import DemoScenario


@dataclass(frozen=True)
class ScenarioVariant:
    key: str
    label: str
    description: str
    safety_stock_factor: float
    scenario: DemoScenario


def _scaled_safety_stock(
    values: Mapping[str, float], factor: float
) -> dict[str, float]:
    return {key: round(value * factor, 2) for key, value in values.items()}


def build_scenario_variants(base: DemoScenario) -> tuple[ScenarioVariant, ...]:
    """Build transparent sensitivity bounds around the effective base scenario."""
    cautious = replace(
        base,
        name="Осторожный сценарий",
        lead_time_days=base.lead_time_days + 30,
        default_safety_stock_days=round(base.default_safety_stock_days * 1.5, 2),
        category_safety_stock_days=_scaled_safety_stock(
            base.category_safety_stock_days, 1.5
        ),
    )
    economical = replace(
        base,
        name="Экономный сценарий",
        lead_time_days=max(1, base.lead_time_days - 15),
        default_safety_stock_days=round(base.default_safety_stock_days * 0.75, 2),
        category_safety_stock_days=_scaled_safety_stock(
            base.category_safety_stock_days, 0.75
        ),
    )
    return (
        ScenarioVariant(
            key="base",
            label="Базовый",
            description="Текущие ответы партнёра или явно отмеченные демо-допущения.",
            safety_stock_factor=1.0,
            scenario=base,
        ),
        ScenarioVariant(
            key="cautious",
            label="Осторожный",
            description="Срок поставки +30 дней, страховой запас +50%.",
            safety_stock_factor=1.5,
            scenario=cautious,
        ),
        ScenarioVariant(
            key="economical",
            label="Экономный",
            description="Срок поставки −15 дней, страховой запас −25%.",
            safety_stock_factor=0.75,
            scenario=economical,
        ),
    )


def _shortage_before_lead_end(
    record: SourceSkuRecord,
    forecast: ForecastResult,
    *,
    lead_time_days: int,
    as_of: date,
) -> float:
    """Estimate the maximum negative balance before the replenishment horizon."""
    daily_demand = forecast.monthly_units / DAYS_PER_MONTH
    lead_end = as_of + timedelta(days=lead_time_days)
    stock = record.inventory.free_stock
    cursor = as_of
    maximum_shortage = max(0.0, -stock)
    for shipment in sorted(record.inventory.inbound, key=lambda item: item.expected_date):
        if shipment.expected_date > lead_end or shipment.quantity <= 0:
            continue
        elapsed = max(0, (shipment.expected_date - cursor).days)
        stock -= daily_demand * elapsed
        maximum_shortage = max(maximum_shortage, -stock)
        stock += shipment.quantity
        cursor = max(cursor, shipment.expected_date)
    stock -= daily_demand * max(0, (lead_end - cursor).days)
    return max(maximum_shortage, -stock, 0.0)


def _projected_buffer(
    recommendation: PurchaseRecommendation, forecast: ForecastResult
) -> float:
    if (
        recommendation.status == RecommendationStatus.BLOCKED
        or recommendation.cover_days is None
    ):
        return 0.0
    daily_demand = forecast.monthly_units / DAYS_PER_MONTH
    projected_stock = (
        recommendation.free_stock
        + recommendation.eligible_inbound
        + (recommendation.recommended_quantity or 0.0)
        - daily_demand * recommendation.cover_days
    )
    return max(0.0, projected_stock)


def compare_scenarios(
    records: Sequence[SourceSkuRecord],
    forecasts: Mapping[str, ForecastResult],
    base: DemoScenario,
    *,
    as_of: date,
) -> dict[str, object]:
    """Calculate comparable operational and financial metrics for three policies."""
    rows: list[dict[str, object]] = []
    for variant in build_scenario_variants(base):
        recommendations: list[tuple[SourceSkuRecord, ForecastResult, PurchaseRecommendation]] = []
        for record in records:
            forecast = forecasts[record.sku]
            recommendation = calculate_recommendation(
                variant.scenario.policy_for(record),
                record.inventory,
                forecast,
                as_of=as_of,
            )
            recommendations.append((record, forecast, recommendation))

        positive = [
            item
            for item in recommendations
            if (item[2].recommended_quantity or 0.0) > 0
        ]
        valued = [
            item
            for item in positive
            if item[0].unit_cost is not None
        ]
        shortage_by_sku = [
            _shortage_before_lead_end(
                record,
                forecast,
                lead_time_days=variant.scenario.lead_time_days,
                as_of=as_of,
            )
            for record, forecast, _ in recommendations
        ]
        total_units = sum(
            recommendation.recommended_quantity or 0.0
            for _, _, recommendation in positive
        )
        total_value = sum(
            (recommendation.recommended_quantity or 0.0) * (record.unit_cost or 0.0)
            for record, _, recommendation in valued
        )
        rows.append(
            {
                "key": variant.key,
                "label": variant.label,
                "description": variant.description,
                "lead_time_days": variant.scenario.lead_time_days,
                "review_period_days": variant.scenario.review_period_days,
                "safety_stock_factor": variant.safety_stock_factor,
                "positive_order_lines": len(positive),
                "blocked_lines": sum(
                    recommendation.status == RecommendationStatus.BLOCKED
                    for _, _, recommendation in recommendations
                ),
                "critical_lines": sum(
                    recommendation.urgency == "critical"
                    for _, _, recommendation in recommendations
                ),
                "high_risk_lines": sum(
                    recommendation.urgency in {"critical", "high"}
                    for _, _, recommendation in recommendations
                ),
                "estimated_shortage_lines": sum(value > 0.0 for value in shortage_by_sku),
                "estimated_shortage_units": round(sum(shortage_by_sku), 2),
                "estimated_buffer_units": round(
                    sum(
                        _projected_buffer(recommendation, forecast)
                        for _, forecast, recommendation in recommendations
                    ),
                    2,
                ),
                "total_order_units_demo": round(total_units, 2),
                "valued_order_lines": len(valued),
                "total_order_value_demo": round(total_value, 2),
            }
        )

    baseline = rows[0]
    for row in rows:
        row["delta_order_units_from_base"] = round(
            float(row["total_order_units_demo"])
            - float(baseline["total_order_units_demo"]),
            2,
        )
        row["delta_order_value_from_base"] = round(
            float(row["total_order_value_demo"])
            - float(baseline["total_order_value_demo"]),
            2,
        )
        row["delta_shortage_units_from_base"] = round(
            float(row["estimated_shortage_units"])
            - float(baseline["estimated_shortage_units"]),
            2,
        )
        row["delta_buffer_units_from_base"] = round(
            float(row["estimated_buffer_units"])
            - float(baseline["estimated_buffer_units"]),
            2,
        )

    return {
        "baseline_key": "base",
        "method": (
            "Дефицит — максимальный расчётный минус остатка до конца "
            "срока поставки с учётом товара в пути. Запас сверх спроса — "
            "расчётный остаток после покрытия lead time + review period."
        ),
        "warning": (
            "Осторожный и экономный сценарии — внутренние границы "
            "чувствительности, а не подтверждённые партнёром условия."
        ),
        "variants": rows,
    }
