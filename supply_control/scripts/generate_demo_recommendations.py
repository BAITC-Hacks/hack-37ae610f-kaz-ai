from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from replenishment.forecast import forecast_monthly  # noqa: E402
from replenishment.loaders import load_systeme_snapshot  # noqa: E402
from replenishment.models import RecommendationStatus  # noqa: E402
from replenishment.reorder import calculate_recommendation  # noqa: E402
from replenishment.scenario import DemoScenario  # noqa: E402


CONSOLIDATED = ROOT / "data/extracted/systeme/Systeme electric/Товар в пути_SystemElectric на 22.09.2026.xlsx"
MOQ = ROOT / "data/extracted/systeme/Systeme electric/MOQ SystemElectric.xlsx"
CONFIG = ROOT / "config/demo_systeme_assumptions.json"
OUTPUT_JSON = ROOT / "outputs/demo/systeme_recommendations_demo.json"
OUTPUT_MD = ROOT / "outputs/demo/systeme_recommendations_demo.md"
AS_OF = date(2026, 9, 22)
URGENCY_RANK = {"critical": 0, "high": 1, "normal": 2, "none": 3, "unknown": 4}


def _json_safe(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value)!r}")


def _money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def main() -> None:
    scenario = DemoScenario.from_json(CONFIG)
    records = load_systeme_snapshot(CONSOLIDATED, MOQ, as_of=AS_OF)
    rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    urgency_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    clamped_growth = 0
    clamped_seasonality = 0

    for record in records:
        growth_multiplier, growth_was_clamped = scenario.growth_multiplier(
            record.source_growth_rate
        )
        seasonality_multiplier, seasonality_was_clamped = scenario.seasonality_multiplier(
            record.source_seasonality_coefficient
        )
        clamped_growth += int(growth_was_clamped)
        clamped_seasonality += int(seasonality_was_clamped)
        category_counts[record.category or "missing"] += 1

        forecast = forecast_monthly(
            record.monthly_demand,
            as_of=AS_OF,
            growth_multiplier=growth_multiplier,
            seasonality_indices={AS_OF.month: seasonality_multiplier},
        )
        forecast = replace(
            forecast,
            review_reasons=tuple(
                dict.fromkeys(forecast.review_reasons + ("demo_assumptions",))
            ),
        )
        policy = scenario.policy_for(record)
        recommendation = calculate_recommendation(
            policy,
            record.inventory,
            forecast,
            as_of=AS_OF,
        )
        status_counts[recommendation.status.value] += 1
        urgency_counts[recommendation.urgency] += 1
        quantity = recommendation.recommended_quantity
        order_value = (
            quantity * record.unit_cost
            if quantity is not None and record.unit_cost is not None
            else None
        )
        rows.append(
            {
                "sku": record.sku,
                "supplier_sku": record.supplier_sku,
                "name": record.name,
                "supplier": record.supplier,
                "category": record.category,
                "unit_cost": record.unit_cost,
                "growth_multiplier_demo": growth_multiplier,
                "seasonality_multiplier_demo": seasonality_multiplier,
                "safety_stock_days_demo": policy.safety_stock_days,
                "forecast": asdict(forecast),
                "recommendation": asdict(recommendation),
                "order_value_demo": order_value,
            }
        )

    rows.sort(
        key=lambda row: (
            URGENCY_RANK.get(row["recommendation"]["urgency"], 9),
            -(row["order_value_demo"] or 0),
            row["sku"],
        )
    )
    positive = [
        row
        for row in rows
        if (row["recommendation"]["recommended_quantity"] or 0) > 0
    ]
    blocked = [
        row
        for row in rows
        if row["recommendation"]["status"] == RecommendationStatus.BLOCKED
    ]
    total_units = sum(
        row["recommendation"]["recommended_quantity"] or 0 for row in positive
    )
    valued = [row for row in positive if row["order_value_demo"] is not None]
    total_value = sum(row["order_value_demo"] or 0 for row in valued)

    summary = {
        "scenario": scenario.name,
        "as_of": AS_OF.isoformat(),
        "warning": "DEMO ONLY — supplier parameters and category legend are not confirmed",
        "source_skus": len(rows),
        "positive_order_lines": len(positive),
        "zero_quantity_lines": len(rows) - len(positive) - len(blocked),
        "blocked_lines": len(blocked),
        "total_order_units_demo": total_units,
        "valued_order_lines": len(valued),
        "total_order_value_demo": total_value,
        "status_counts": dict(status_counts),
        "urgency_counts": dict(urgency_counts),
        "category_counts": dict(category_counts),
        "growth_rates_clamped": clamped_growth,
        "seasonality_rates_clamped": clamped_seasonality,
    }
    payload = {
        "summary": summary,
        "assumptions": asdict(scenario),
        "recommendations": rows,
    }
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_safe),
        encoding="utf-8",
    )

    lines = [
        "# Демо-рекомендации по заказу Systeme Electric",
        "",
        "> **ДЕМО.** Срок поставки, страховой запас и трактовка категорий не подтверждены партнёром. Не отправлять поставщику.",
        "",
        "## Сводка",
        "",
        f"- SKU в источнике: {len(rows)}",
        f"- Положительные рекомендации: {len(positive)}",
        f"- Заблокировано из-за отсутствия MOQ: {len(blocked)}",
        f"- Количество к заказу: {_money(total_units)} шт.",
        f"- Оценочная стоимость: {_money(total_value)} ₸ по {len(valued)} строкам с ценой",
        f"- Ограничен коэффициент роста: {clamped_growth} SKU",
        f"- Ограничен коэффициент сезонности: {clamped_seasonality} SKU",
        "",
        "Все незаблокированные строки имеют статус `review_required`: отсутствуют подтверждённые stockout-данные и применены демо-допущения.",
        "",
        "## Топ-30 рекомендаций",
        "",
        "| Срочность | SKU | Категория | Наименование | Кол-во | MOQ | Стоимость, ₸ |",
        "|---|---|---:|---|---:|---:|---:|",
    ]
    for row in positive[:30]:
        recommendation = row["recommendation"]
        safe_name = row["name"].replace("|", "/")
        lines.append(
            "| {urgency} | {sku} | {category} | {name} | {qty:g} | {moq:g} | {value} |".format(
                urgency=recommendation["urgency"],
                sku=row["sku"],
                category=row["category"] or "—",
                name=safe_name,
                qty=recommendation["recommended_quantity"],
                moq=recommendation["moq"],
                value=_money(row["order_value_demo"] or 0),
            )
        )
    lines.extend(
        [
            "",
            "## Демо-допущения",
            "",
            f"- Срок поставки: {scenario.lead_time_days} дней",
            f"- Период пересмотра: {scenario.review_period_days} дней",
            f"- Страховой запас по категориям: {dict(scenario.category_safety_stock_days)}",
            f"- Ограничение множителя роста: {scenario.growth_multiplier_bounds}",
            f"- Ограничение множителя сезонности: {scenario.seasonality_multiplier_bounds}",
            "",
        ]
    )
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()

