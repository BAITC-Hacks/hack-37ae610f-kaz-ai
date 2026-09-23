from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .loaders import SourceSkuRecord
from .models import SkuPolicy


@dataclass(frozen=True)
class DemoScenario:
    name: str
    lead_time_days: int
    review_period_days: int
    default_safety_stock_days: float
    category_safety_stock_days: Mapping[str, float]
    growth_multiplier_bounds: tuple[float, float]
    seasonality_multiplier_bounds: tuple[float, float]
    apply_growth_coefficient: bool = True
    apply_seasonality_coefficient: bool = True
    order_budget_kzt: float | None = None
    minimum_total_order_kzt: float | None = None
    partner_confirmation: Mapping[str, Any] | None = None
    notes: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, path: str | Path) -> "DemoScenario":
        payload: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DemoScenario":
        return cls(
            name=str(payload["scenario_name"]),
            lead_time_days=int(payload["lead_time_days"]),
            review_period_days=int(payload["review_period_days"]),
            default_safety_stock_days=float(payload["default_safety_stock_days"]),
            category_safety_stock_days={
                str(key): float(value)
                for key, value in payload["category_safety_stock_days"].items()
            },
            growth_multiplier_bounds=tuple(
                float(value) for value in payload["growth_multiplier_bounds"]
            ),
            seasonality_multiplier_bounds=tuple(
                float(value) for value in payload["seasonality_multiplier_bounds"]
            ),
            apply_growth_coefficient=bool(
                payload.get("apply_growth_coefficient", True)
            ),
            apply_seasonality_coefficient=bool(
                payload.get("apply_seasonality_coefficient", True)
            ),
            order_budget_kzt=(
                float(payload["order_budget_kzt"])
                if payload.get("order_budget_kzt") is not None
                else None
            ),
            minimum_total_order_kzt=(
                float(payload["minimum_total_order_kzt"])
                if payload.get("minimum_total_order_kzt") is not None
                else None
            ),
            partner_confirmation=(
                dict(payload["partner_confirmation"])
                if isinstance(payload.get("partner_confirmation"), Mapping)
                else None
            ),
            notes=tuple(str(note) for note in payload.get("notes", [])),
        )

    @staticmethod
    def _bounded_rate_multiplier(
        rate: float | None, bounds: tuple[float, float]
    ) -> tuple[float, bool]:
        raw_multiplier = 1.0 if rate is None else 1.0 + rate
        lower, upper = bounds
        bounded = min(max(raw_multiplier, lower), upper)
        return bounded, bounded != raw_multiplier

    def growth_multiplier(self, rate: float | None) -> tuple[float, bool]:
        if not self.apply_growth_coefficient:
            return 1.0, False
        return self._bounded_rate_multiplier(rate, self.growth_multiplier_bounds)

    def seasonality_multiplier(self, rate: float | None) -> tuple[float, bool]:
        if not self.apply_seasonality_coefficient:
            return 1.0, False
        return self._bounded_rate_multiplier(rate, self.seasonality_multiplier_bounds)

    def safety_stock_days(self, category: str | None) -> float:
        if category is None:
            return self.default_safety_stock_days
        return self.category_safety_stock_days.get(
            category, self.default_safety_stock_days
        )

    def policy_for(self, record: SourceSkuRecord) -> SkuPolicy:
        return SkuPolicy(
            sku=record.sku,
            supplier=record.supplier,
            moq=record.moq,
            lead_time_days=self.lead_time_days,
            review_period_days=self.review_period_days,
            safety_stock_days=self.safety_stock_days(record.category),
            category=record.category,
        )
