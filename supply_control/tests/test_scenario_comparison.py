from __future__ import annotations

import unittest
from datetime import date

from replenishment.loaders import SourceSkuRecord
from replenishment.models import (
    DemandClass,
    ForecastResult,
    InventoryState,
)
from replenishment.scenario import DemoScenario
from replenishment.scenario_comparison import (
    build_scenario_variants,
    compare_scenarios,
)


class ScenarioComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.as_of = date(2026, 9, 22)
        self.base = DemoScenario(
            name="base",
            lead_time_days=60,
            review_period_days=30,
            default_safety_stock_days=15,
            category_safety_stock_days={"1": 30, "7": 0},
            growth_multiplier_bounds=(0.75, 1.5),
            seasonality_multiplier_bounds=(0.5, 2.0),
        )
        self.record = SourceSkuRecord(
            sku="SKU-1",
            supplier="Supplier",
            supplier_sku=None,
            name="Test product",
            category="1",
            unit_cost=100.0,
            monthly_demand=(),
            inventory=InventoryState(on_hand=0.0),
            moq=1.0,
            source_growth_rate=None,
            source_seasonality_coefficient=None,
            source_free_stock=0.0,
        )
        self.forecast = ForecastResult(
            base_monthly_units=304.375,
            monthly_units=304.375,
            model="test",
            demand_class=DemandClass.SMOOTH,
            history_months=8,
            seasonality_factor=1.0,
            growth_multiplier=1.0,
            category_multiplier=1.0,
            stockout_uplift_units=0.0,
        )

    def test_variants_apply_documented_sensitivity_bounds(self) -> None:
        variants = {item.key: item for item in build_scenario_variants(self.base)}
        self.assertEqual(variants["base"].scenario.lead_time_days, 60)
        self.assertEqual(variants["cautious"].scenario.lead_time_days, 90)
        self.assertEqual(variants["cautious"].scenario.safety_stock_days("1"), 45)
        self.assertEqual(variants["economical"].scenario.lead_time_days, 45)
        self.assertEqual(variants["economical"].scenario.safety_stock_days("1"), 22.5)

    def test_comparison_shows_quantity_value_shortage_and_buffer_tradeoff(self) -> None:
        comparison = compare_scenarios(
            [self.record],
            {"SKU-1": self.forecast},
            self.base,
            as_of=self.as_of,
        )
        rows = {row["key"]: row for row in comparison["variants"]}
        self.assertGreater(
            rows["cautious"]["total_order_units_demo"],
            rows["base"]["total_order_units_demo"],
        )
        self.assertLess(
            rows["economical"]["total_order_value_demo"],
            rows["base"]["total_order_value_demo"],
        )
        self.assertGreater(
            rows["cautious"]["estimated_shortage_units"],
            rows["base"]["estimated_shortage_units"],
        )
        self.assertGreater(
            rows["cautious"]["estimated_buffer_units"],
            rows["base"]["estimated_buffer_units"],
        )
        self.assertEqual(rows["base"]["delta_order_units_from_base"], 0)
        self.assertEqual(rows["base"]["delta_order_value_from_base"], 0)


if __name__ == "__main__":
    unittest.main()
