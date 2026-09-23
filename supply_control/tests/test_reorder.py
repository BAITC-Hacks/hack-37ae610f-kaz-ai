from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date

from replenishment.models import (
    DemandClass,
    ForecastResult,
    InboundShipment,
    InventoryState,
    RecommendationStatus,
    SkuPolicy,
)
from replenishment.reorder import calculate_recommendation, group_by_supplier


def forecast(monthly_units: float = 304.375, reasons: tuple[str, ...] = ()) -> ForecastResult:
    return ForecastResult(
        base_monthly_units=monthly_units,
        monthly_units=monthly_units,
        model="last_complete_month",
        demand_class=DemandClass.SMOOTH,
        history_months=24,
        seasonality_factor=1.0,
        growth_multiplier=1.0,
        category_multiplier=1.0,
        stockout_uplift_units=0.0,
        review_reasons=reasons,
    )


def policy(**changes: object) -> SkuPolicy:
    base = SkuPolicy(
        sku="SKU-1",
        supplier="Supplier A",
        moq=50,
        lead_time_days=20,
        review_period_days=10,
        safety_stock_days=5,
    )
    return replace(base, **changes)


class ReorderTests(unittest.TestCase):
    def test_missing_critical_input_blocks_calculation(self) -> None:
        result = calculate_recommendation(
            policy(lead_time_days=None),
            InventoryState(on_hand=0),
            forecast(),
            as_of=date(2026, 9, 22),
        )
        self.assertEqual(result.status, RecommendationStatus.BLOCKED)
        self.assertIsNone(result.recommended_quantity)
        self.assertIn("missing_or_invalid_lead_time", result.reasons)

    def test_inbound_before_horizon_reduces_need_and_late_inbound_does_not(self) -> None:
        as_of = date(2026, 9, 22)
        early = InventoryState(
            on_hand=0,
            inbound=(InboundShipment(100, date(2026, 10, 1)),),
        )
        late = InventoryState(
            on_hand=0,
            inbound=(InboundShipment(100, date(2026, 11, 15)),),
        )
        early_result = calculate_recommendation(policy(), early, forecast(), as_of=as_of)
        late_result = calculate_recommendation(policy(), late, forecast(), as_of=as_of)
        self.assertEqual(early_result.eligible_inbound, 100)
        self.assertEqual(late_result.eligible_inbound, 0)
        self.assertLess(early_result.recommended_quantity or 0, late_result.recommended_quantity or 0)

    def test_projected_stockout_date_includes_timely_inbound(self) -> None:
        as_of = date(2026, 9, 22)
        without_inbound = calculate_recommendation(
            policy(), InventoryState(on_hand=20), forecast(), as_of=as_of
        )
        with_inbound = calculate_recommendation(
            policy(),
            InventoryState(
                on_hand=20,
                inbound=(InboundShipment(100, date(2026, 9, 23)),),
            ),
            forecast(),
            as_of=as_of,
        )
        self.assertGreater(
            with_inbound.projected_stockout_date,
            without_inbound.projected_stockout_date,
        )
        self.assertNotIn("inbound_arrives_after_projected_stockout", with_inbound.reasons)

    def test_moq_rounding_and_explanation(self) -> None:
        result = calculate_recommendation(
            policy(),
            InventoryState(on_hand=0),
            forecast(),
            as_of=date(2026, 9, 22),
        )
        self.assertEqual(result.recommended_quantity, 350)
        self.assertIn("MOQ 50", result.explanation)
        self.assertIn("свободный остаток", result.explanation)

    def test_review_flag_prevents_ready_status(self) -> None:
        result = calculate_recommendation(
            policy(),
            InventoryState(on_hand=0),
            forecast(reasons=("lumpy_demand",)),
            as_of=date(2026, 9, 22),
        )
        self.assertEqual(result.status, RecommendationStatus.REVIEW_REQUIRED)

    def test_uncertain_forecast_does_not_silently_return_no_order(self) -> None:
        result = calculate_recommendation(
            policy(),
            InventoryState(on_hand=10_000),
            forecast(reasons=("stockout_data_missing",)),
            as_of=date(2026, 9, 22),
        )
        self.assertEqual(result.recommended_quantity, 0)
        self.assertEqual(result.status, RecommendationStatus.REVIEW_REQUIRED)

    def test_grouping_by_supplier(self) -> None:
        first = calculate_recommendation(
            policy(), InventoryState(on_hand=0), forecast(), as_of=date(2026, 9, 22)
        )
        second = calculate_recommendation(
            policy(sku="SKU-2", supplier="Supplier B"),
            InventoryState(on_hand=0),
            forecast(),
            as_of=date(2026, 9, 22),
        )
        grouped = group_by_supplier([second, first])
        self.assertEqual(list(grouped), ["Supplier A", "Supplier B"])


if __name__ == "__main__":
    unittest.main()
