from __future__ import annotations

import unittest

from replenishment.cleaning import clean_transaction_rows, normalize_sku


class CleaningTests(unittest.TestCase):
    def test_normalize_sku_preserves_identifiers(self) -> None:
        self.assertEqual(normalize_sku(" 030200192_ "), "030200192_")
        self.assertEqual(normalize_sku(123.0), "123")

    def test_sales_returns_summary_and_invalid_are_separate(self) -> None:
        rows = [
            {"Дата": "01.09.2026", "Документ": "S1", "Код 1с": "A", "Количество": 5},
            {"Дата": "02.09.2026", "Документ": "R1", "Код 1с": "A", "Количество": -2},
            {"Дата": "03.09.2026", "Документ": "T", "Код 1с": "Итого", "Количество": 3},
            {"Дата": None, "Документ": "B", "Код 1с": "A", "Количество": 3},
        ]
        result = clean_transaction_rows(rows)
        self.assertEqual([item.quantity for item in result.sales], [5.0])
        self.assertEqual([item.quantity for item in result.returns], [2.0])
        self.assertEqual(result.dropped_summary_rows, (3,))
        self.assertEqual(result.invalid_rows, (4,))


if __name__ == "__main__":
    unittest.main()

