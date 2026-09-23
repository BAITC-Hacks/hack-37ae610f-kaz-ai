from __future__ import annotations

import unittest
from datetime import date

from replenishment.forecast import forecast_monthly
from replenishment.models import MonthlyDemand


class ForecastTests(unittest.TestCase):
    def test_partial_current_month_is_excluded(self) -> None:
        history = [
            MonthlyDemand(date(2026, 1, 1), 100, 0),
            MonthlyDemand(date(2026, 2, 1), 9_999, 0),
        ]
        result = forecast_monthly(history, as_of=date(2026, 2, 15))
        self.assertEqual(result.history_months, 1)
        self.assertEqual(result.base_monthly_units, 100)

    def test_explicit_stockout_increases_estimated_demand(self) -> None:
        history = [
            MonthlyDemand(date(2026, month, 1), 50, 15 if month == 4 else 0)
            for month in range(1, 7)
        ]
        result = forecast_monthly(history, as_of=date(2026, 7, 10))
        self.assertGreater(result.stockout_uplift_units, 49)
        self.assertNotIn("stockout_data_missing", result.review_reasons)

    def test_missing_stockout_data_stays_visible(self) -> None:
        history = [MonthlyDemand(date(2026, month, 1), 100) for month in range(1, 7)]
        result = forecast_monthly(history, as_of=date(2026, 7, 1))
        self.assertIn("stockout_data_missing", result.review_reasons)

    def test_growth_category_and_seasonality_change_result(self) -> None:
        history = [
            MonthlyDemand(date(2025, month, 1), 100, 0) for month in range(1, 7)
        ]
        result = forecast_monthly(
            history,
            as_of=date(2025, 7, 1),
            growth_multiplier=1.10,
            category_multiplier=1.20,
            seasonality_indices={7: 1.50},
        )
        self.assertAlmostEqual(result.monthly_units, 198.0)

    def test_challenger_must_beat_simple_champion_materially(self) -> None:
        history = [
            MonthlyDemand(date(2025, month, 1), quantity, 0)
            for month, quantity in enumerate([100, 102, 98, 101, 99, 100, 101, 100], start=1)
        ]
        result = forecast_monthly(history, as_of=date(2025, 9, 1))
        self.assertEqual(result.model, "last_complete_month")
        experimental = forecast_monthly(
            history,
            as_of=date(2025, 9, 1),
            allow_challengers=True,
        )
        self.assertEqual(experimental.model, "weighted_3_months")


if __name__ == "__main__":
    unittest.main()
