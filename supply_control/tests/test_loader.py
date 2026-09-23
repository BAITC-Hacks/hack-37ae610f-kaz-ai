from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from replenishment.loaders import load_systeme_snapshot


ROOT = Path(__file__).resolve().parents[1]
CONSOLIDATED = ROOT / "data/extracted/systeme/Systeme electric/Товар в пути_SystemElectric на 22.09.2026.xlsx"
MOQ = ROOT / "data/extracted/systeme/Systeme electric/MOQ SystemElectric.xlsx"


@unittest.skipUnless(CONSOLIDATED.exists() and MOQ.exists(), "Source workbooks are not present")
class SystemeLoaderIntegrationTests(unittest.TestCase):
    def test_loads_source_without_rewriting_it(self) -> None:
        records = load_systeme_snapshot(CONSOLIDATED, MOQ, as_of=date(2026, 9, 22))
        self.assertEqual(len(records), 497)
        self.assertEqual(records[0].sku, "300200428_")
        self.assertEqual(len(records[0].monthly_demand), 33)
        self.assertEqual(records[0].monthly_demand[-1].period, date(2026, 9, 1))
        self.assertEqual(records[0].monthly_demand[-1].quantity, 135)
        self.assertEqual(records[0].inventory.free_stock, records[0].source_free_stock)


if __name__ == "__main__":
    unittest.main()

