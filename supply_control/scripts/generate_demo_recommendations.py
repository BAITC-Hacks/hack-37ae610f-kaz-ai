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
from replenishment.partner_answers import (  # noqa: E402
    answer_value,
    apply_answers_to_scenario,
    load_partner_answers,
    load_questionnaire,
    questionnaire_view,
)
from replenishment.reorder import calculate_recommendation  # noqa: E402
from replenishment.scenario import DemoScenario  # noqa: E402
from replenishment.scenario_comparison import compare_scenarios  # noqa: E402


CONSOLIDATED = ROOT / "data/extracted/systeme/Systeme electric/Товар в пути_SystemElectric на 22.09.2026.xlsx"
MOQ = ROOT / "data/extracted/systeme/Systeme electric/MOQ SystemElectric.xlsx"
CONFIG = ROOT / "config/demo_systeme_assumptions.json"
QUESTIONNAIRE = ROOT / "config/partner_questionnaire.json"
PARTNER_ANSWERS = ROOT / "config/partner_answers.json"
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
    base_config = json.loads(CONFIG.read_text(encoding="utf-8"))
    questionnaire = load_questionnaire(QUESTIONNAIRE)
    partner_answers = load_partner_answers(PARTNER_ANSWERS)
    effective_config = apply_answers_to_scenario(base_config, partner_answers)
    scenario = DemoScenario.from_mapping(effective_config)
    confirmation = scenario.partner_confirmation or {}
    core_confirmed = bool(confirmation.get("core_parameters_confirmed"))
    records = load_systeme_snapshot(CONSOLIDATED, MOQ, as_of=AS_OF)
    rows: list[dict[str, Any]] = []
    forecasts: dict[str, Any] = {}
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
        scenario_reason = (
            "partner_answers_confirmed"
            if core_confirmed
            else "partner_answers_partial"
            if partner_answers
            else "demo_assumptions"
        )
        extra_reasons = [scenario_reason]
        inbound_definition = answer_value(partner_answers, "inbound_eta_definition")
        if record.inventory.inbound and inbound_definition != "Дата доступности товара для продажи":
            extra_reasons.append("inbound_eta_unconfirmed")
        forecast = replace(
            forecast,
            review_reasons=tuple(
                dict.fromkeys(forecast.review_reasons + tuple(extra_reasons))
            ),
        )
        forecasts[record.sku] = forecast
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
    budget = scenario.order_budget_kzt
    minimum_total = scenario.minimum_total_order_kzt
    budget_overage = max(0.0, total_value - budget) if budget is not None else None
    below_minimum = (
        total_value < minimum_total if minimum_total is not None else None
    )
    question_rows = questionnaire_view(questionnaire, partner_answers)
    confirmed_questions = sum(
        row["status"] == "partner_confirmed" for row in question_rows
    )
    if core_confirmed:
        warning = (
            "Ключевые параметры подтверждены партнёром. Строки с отсутствующими "
            "stockout-данными всё равно требуют ручной проверки."
        )
    elif partner_answers:
        warning = (
            "Ответы партнёра применены частично. Неподтверждённые параметры остаются "
            "демо-допущениями и требуют ручной проверки."
        )
    else:
        warning = "DEMO ONLY — supplier parameters and category legend are not confirmed"

    summary = {
        "scenario": scenario.name,
        "as_of": AS_OF.isoformat(),
        "warning": warning,
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
        "partner_questions_total": len(question_rows),
        "partner_questions_confirmed": confirmed_questions,
        "partner_core_parameters_confirmed": core_confirmed,
        "order_budget_kzt": budget,
        "budget_overage_kzt": budget_overage,
        "minimum_total_order_kzt": minimum_total,
        "below_minimum_total_order": below_minimum,
    }
    scenario_comparison = compare_scenarios(
        records,
        forecasts,
        scenario,
        as_of=AS_OF,
    )
    payload = {
        "summary": summary,
        "assumptions": asdict(scenario),
        "scenario_comparison": scenario_comparison,
        "partner_questions": question_rows,
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
        f"> {warning}",
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
        f"- Вопросов подтверждено партнёром: {confirmed_questions} из {len(question_rows)}",
        f"- Бюджет заказа: {_money(budget)} ₸" if budget is not None else "- Бюджет заказа: не подтверждён",
        f"- Превышение бюджета: {_money(budget_overage or 0)} ₸" if budget is not None else "- Превышение бюджета: не рассчитывается",
        "",
        "Все незаблокированные строки имеют статус `review_required`: отсутствуют подтверждённые stockout-данные и применены демо-допущения.",
        "",
        "## Сравнение сценариев",
        "",
        "| Сценарий | Lead time | Строк к заказу | Кол-во | Стоимость, ₸ | Риск дефицита, SKU | Дефицит, шт. | Запас сверх спроса, шт. |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in scenario_comparison["variants"]:
        lines.append(
            "| {label} | {lead:g} | {lines:g} | {units:g} | {value} | {risk:g} | {shortage:g} | {buffer:g} |".format(
                label=variant["label"],
                lead=variant["lead_time_days"],
                lines=variant["positive_order_lines"],
                units=variant["total_order_units_demo"],
                value=_money(variant["total_order_value_demo"]),
                risk=variant["estimated_shortage_lines"],
                shortage=variant["estimated_shortage_units"],
                buffer=variant["estimated_buffer_units"],
            )
        )
    lines.extend(
        [
        "",
        f"> {scenario_comparison['warning']}",
        "",
        "## Топ-30 рекомендаций",
        "",
        "| Срочность | SKU | Категория | Наименование | Кол-во | Кратность | Стоимость, ₸ |",
        "|---|---|---:|---|---:|---:|---:|",
        ]
    )
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
            "## Применённые параметры",
            "",
            f"- Срок поставки: {scenario.lead_time_days} дней",
            f"- Период пересмотра: {scenario.review_period_days} дней",
            f"- Страховой запас по категориям: {dict(scenario.category_safety_stock_days)}",
            f"- Ограничение множителя роста: {scenario.growth_multiplier_bounds}",
            f"- Ограничение множителя сезонности: {scenario.seasonality_multiplier_bounds}",
            f"- Коэффициент роста включён: {'да' if scenario.apply_growth_coefficient else 'нет'}",
            f"- Коэффициент сезонности включён: {'да' if scenario.apply_seasonality_coefficient else 'нет'}",
            "",
        ]
    )
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
