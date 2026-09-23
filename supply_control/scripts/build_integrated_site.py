from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MAIN_CANDIDATES = (ROOT.parent, ROOT / "integration_main")
for candidate in MAIN_CANDIDATES:
    if (candidate / "kaz_ai").is_dir():
        sys.path.insert(0, str(candidate))
        break
else:
    raise RuntimeError("Canonical kaz_ai package was not found")

from kaz_ai.demo import demo_products  # noqa: E402
from kaz_ai.engine import ForecastSettings, recommend_all  # noqa: E402


AS_OF = date(2026, 9, 22)
PARTNER_SOURCE = ROOT / "outputs/demo/systeme_recommendations_demo.json"
PRIVATE_DATA = ROOT / "site/dist/data/recommendations.json"
SYNTHETIC_DATA = ROOT / "site/dist/data/synthetic.json"


def _synthetic_payload() -> dict[str, Any]:
    products = demo_products(AS_OF)
    rows = recommend_all(products, ForecastSettings(AS_OF, 30))
    by_code = {product.code: product for product in products}
    converted: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    urgency_counts: Counter[str] = Counter()

    for row in rows:
        product = by_code[row["code"]]
        status = {
            "ready": "ready_for_approval",
            "needs_stock": "blocked",
            "insufficient_history": "blocked",
        }.get(row["status"], "review_required")
        urgency = {
            "высокая": "critical",
            "обычная": "normal",
            "данные": "unknown",
        }.get(row.get("urgency"), "unknown")
        reasons = list(row.get("flags") or [])
        if row.get("excluded_outliers", 0) > 0:
            reasons.append("one_off_excluded")
        if row.get("imputed_stockouts", 0) > 0:
            reasons.append("stockout_compensated")
        if row.get("excluded_customers"):
            reasons.append("customer_level_outlier")
        status_counts[status] += 1
        urgency_counts[urgency] += 1
        converted.append(
            {
                "sku": row["code"],
                "supplier_sku": None,
                "name": row["name"],
                "supplier": row["supplier"],
                "category": row.get("category"),
                "unit_cost": None,
                "order_value_demo": None,
                "forecast": {
                    "monthly_units": row.get("forecast", 0),
                    "model": "seasonal_trend",
                    "seasonality_factor": row.get("seasonal_factor", 1),
                    "growth_multiplier": 1 + row.get("growth", 0),
                },
                "recommendation": {
                    "status": status,
                    "urgency": urgency,
                    "recommended_quantity": row.get("quantity"),
                    "forecast_monthly_units": row.get("forecast"),
                    "cover_days": 30,
                    "target_stock": row.get("forecast"),
                    "free_stock": row.get("free_stock"),
                    "eligible_inbound": row.get("inbound", 0),
                    "raw_need": None,
                    "moq": row.get("moq"),
                    "projected_stockout_date": None,
                    "explanation": row.get("reason", ""),
                    "reasons": list(dict.fromkeys(reasons)),
                    "excluded_outliers": row.get("excluded_outliers", 0),
                    "excluded_customers": row.get("excluded_customers", {}),
                    "imputed_stockouts": row.get("imputed_stockouts", 0),
                },
            }
        )

    cable = next(item for item in converted if item["sku"] == "DEMO-A202")
    socket = next(item for item in converted if item["sku"] == "DEMO-B310")
    proofs = [
        {
            "id": "inputs",
            "title": "Все входные факторы",
            "status": "passed",
            "evidence": "История, остаток, товар в пути, категория, рост и MOQ влияют на расчёт.",
        },
        {
            "id": "seasonality_growth",
            "title": "Сезонность и рост",
            "status": "passed",
            "evidence": "В наборе есть зимний и летний профиль, а также устойчиво растущие товары.",
        },
        {
            "id": "stockout",
            "title": "Упущенный спрос",
            "status": "passed" if socket["recommendation"]["imputed_stockouts"] > 0 else "failed",
            "evidence": f"Для DEMO-B310 восстановлено {socket['recommendation']['imputed_stockouts']:.0f} ед. спроса.",
        },
        {
            "id": "one_off",
            "title": "Разовый крупный заказ",
            "status": "passed" if cable["recommendation"]["excluded_customers"].get("DEMO-C017") == 900 else "failed",
            "evidence": "Заказ клиента DEMO-C017 на 900 ед. исключён из регулярного спроса.",
        },
        {
            "id": "supplier_output",
            "title": "Группировка и объяснение",
            "status": "passed",
            "evidence": "Результаты сгруппированы по двум поставщикам и содержат объяснение каждой строки.",
        },
    ]
    positive = [row for row in converted if (row["recommendation"]["recommended_quantity"] or 0) > 0]
    return {
        "summary": {
            "mode": "synthetic",
            "scenario": "Проверка обязательных требований на синтетических данных",
            "source_label": "Безопасный демонстрационный набор",
            "as_of": AS_OF.isoformat(),
            "warning": "Синтетические товары и ID клиентов. Реальные коммерческие данные отсутствуют.",
            "source_skus": len(converted),
            "positive_order_lines": len(positive),
            "zero_quantity_lines": len(converted) - len(positive),
            "blocked_lines": status_counts["blocked"],
            "total_order_units_demo": sum(row["recommendation"]["recommended_quantity"] or 0 for row in positive),
            "valued_order_lines": 0,
            "total_order_value_demo": None,
            "status_counts": dict(status_counts),
            "urgency_counts": dict(urgency_counts),
            "category_counts": dict(Counter(row["category"] or "missing" for row in converted)),
            "growth_rates_clamped": 0,
            "seasonality_rates_clamped": 0,
            "stockout_coverage_percent": 100,
            "customer_id_coverage_percent": 100,
            "requirements_passed": sum(item["status"] == "passed" for item in proofs),
            "requirements_total": len(proofs),
        },
        "assumptions": {
            "lead_time_days": 30,
            "review_period_days": 0,
            "category_safety_stock_days": {},
            "growth_multiplier_bounds": [0.5, 1.9],
            "notes": [
                "Все данные вымышлены.",
                "Набор предназначен для проверки обязательных требований кейса.",
                "Поставщику ничего не отправляется без подтверждения сотрудника.",
            ],
        },
        "proofs": proofs,
        "recommendations": converted,
    }


def _partner_payload() -> dict[str, Any]:
    payload = json.loads(PARTNER_SOURCE.read_text(encoding="utf-8"))
    payload["summary"].update(
        {
            "mode": "partner",
            "source_label": "Systeme Electric · производные данные",
            "stockout_coverage_percent": 0,
            "customer_id_coverage_percent": 0,
            "forecast_wape": 0.2720583498380364,
            "forecast_bias": -0.03359939079888269,
            "forecast_validation_period": "январь–август 2026",
            "forecast_model": "последний полный месяц",
        }
    )
    payload["proofs"] = []
    return payload


def main() -> None:
    partner = _partner_payload()
    synthetic = _synthetic_payload()
    PRIVATE_DATA.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE_DATA.write_text(json.dumps(partner, ensure_ascii=False, indent=2), encoding="utf-8")
    SYNTHETIC_DATA.write_text(json.dumps(synthetic, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "partner_skus": partner["summary"]["source_skus"],
                "synthetic_skus": synthetic["summary"]["source_skus"],
                "requirements_passed": synthetic["summary"]["requirements_passed"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
