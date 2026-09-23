from __future__ import annotations

import unittest
from datetime import date

from replenishment.cleaning import Transaction
from replenishment.outliers import detect_one_off_orders, exclude_flagged_orders


class OutlierTests(unittest.TestCase):
    def test_90000_unit_single_customer_order_is_excluded(self) -> None:
        quantities = [10, 11, 9, 10, 12, 8, 10, 90_000]
        transactions = [
            Transaction(date(2025, 5, index + 1), f"DOC-{index}", "030200192_", qty, "C-1")
            for index, qty in enumerate(quantities)
        ]
        decisions = detect_one_off_orders(transactions)
        kept, flagged = exclude_flagged_orders(decisions)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].transaction.quantity, 90_000)
        self.assertIn("single_customer", flagged[0].reason or "")
        self.assertEqual(sum(item.quantity for item in kept), 70)

    def test_split_lines_in_same_document_are_aggregated_before_detection(self) -> None:
        base = [
            Transaction(date(2026, 1, day), f"D{day}", "SKU", 10)
            for day in range(1, 7)
        ]
        base.extend(
            [
                Transaction(date(2026, 1, 20), "BIG", "SKU", 500),
                Transaction(date(2026, 1, 20), "BIG", "SKU", 500),
            ]
        )
        flagged = [item for item in detect_one_off_orders(base) if item.is_outlier]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].transaction.quantity, 1_000)


if __name__ == "__main__":
    unittest.main()

