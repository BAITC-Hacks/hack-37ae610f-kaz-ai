from __future__ import annotations

import unittest
from pathlib import Path

from replenishment.scenario import DemoScenario


ROOT = Path(__file__).resolve().parents[1]


class DemoScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = DemoScenario.from_json(
            ROOT / "config/demo_systeme_assumptions.json"
        )

    def test_category_changes_safety_stock_days(self) -> None:
        self.assertEqual(self.scenario.safety_stock_days("1"), 30)
        self.assertEqual(self.scenario.safety_stock_days("7"), 0)
        self.assertEqual(self.scenario.safety_stock_days("unknown"), 15)

    def test_growth_and_seasonality_are_bounded(self) -> None:
        self.assertEqual(self.scenario.growth_multiplier(4.0), (1.5, True))
        self.assertEqual(self.scenario.growth_multiplier(-0.1), (0.9, False))
        self.assertEqual(self.scenario.seasonality_multiplier(-0.9), (0.5, True))


if __name__ == "__main__":
    unittest.main()

