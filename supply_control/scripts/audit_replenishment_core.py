from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from replenishment.forecast import forecast_monthly  # noqa: E402
from replenishment.loaders import load_systeme_snapshot  # noqa: E402
from replenishment.models import SkuPolicy  # noqa: E402
from replenishment.reorder import calculate_recommendation  # noqa: E402


CONSOLIDATED = ROOT / "data/extracted/systeme/Systeme electric/Товар в пути_SystemElectric на 22.09.2026.xlsx"
MOQ = ROOT / "data/extracted/systeme/Systeme electric/MOQ SystemElectric.xlsx"
OUTPUT = ROOT / "data/analysis/replenishment_core_audit_systeme.json"
AS_OF = date(2026, 9, 22)


def main() -> None:
    records = load_systeme_snapshot(CONSOLIDATED, MOQ, as_of=AS_OF)
    demand_classes: Counter[str] = Counter()
    models: Counter[str] = Counter()
    review_reasons: Counter[str] = Counter()
    recommendation_statuses: Counter[str] = Counter()
    blocking_reasons: Counter[str] = Counter()
    samples = []

    for record in records:
        result = forecast_monthly(record.monthly_demand, as_of=AS_OF)
        demand_classes[result.demand_class.value] += 1
        models[result.model] += 1
        review_reasons.update(result.review_reasons)

        # The source does not provide durable lead-time and safety-stock policy.
        # Missing values intentionally block a production recommendation.
        recommendation = calculate_recommendation(
            SkuPolicy(
                sku=record.sku,
                supplier=record.supplier,
                moq=record.moq,
                lead_time_days=None,
                review_period_days=30,
                safety_stock_days=None,
                category=record.category,
            ),
            record.inventory,
            result,
            as_of=AS_OF,
        )
        recommendation_statuses[recommendation.status.value] += 1
        blocking_reasons.update(recommendation.reasons)
        if len(samples) < 5:
            samples.append(
                {
                    "sku": record.sku,
                    "name": record.name,
                    "forecast": asdict(result),
                    "recommendation": asdict(recommendation),
                }
            )

    payload = {
        "as_of": AS_OF.isoformat(),
        "source_records": len(records),
        "records_with_moq": sum(record.moq is not None for record in records),
        "partial_september_excluded_by_forecast": True,
        "source_growth_and_seasonality_not_applied_without_business_definition": True,
        "demand_classes": dict(demand_classes),
        "forecast_models": dict(models),
        "forecast_review_reasons": dict(review_reasons),
        "recommendation_statuses": dict(recommendation_statuses),
        "blocking_reasons": dict(blocking_reasons),
        "samples": samples,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in payload if key != "samples"}, ensure_ascii=False))


if __name__ == "__main__":
    main()

