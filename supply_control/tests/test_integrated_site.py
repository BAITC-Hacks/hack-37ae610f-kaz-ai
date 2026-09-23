from __future__ import annotations

import unittest

from scripts.build_integrated_site import _synthetic_payload


class IntegratedSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = _synthetic_payload()

    def test_public_payload_contains_only_synthetic_skus(self) -> None:
        rows = self.payload["recommendations"]
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(row["sku"].startswith("DEMO-") for row in rows))

    def test_all_required_scenarios_pass(self) -> None:
        proofs = self.payload["proofs"]
        self.assertEqual(len(proofs), 5)
        self.assertTrue(all(item["status"] == "passed" for item in proofs))

    def test_outlier_and_stockout_evidence_is_exposed(self) -> None:
        rows = {row["sku"]: row for row in self.payload["recommendations"]}
        cable = rows["DEMO-A202"]["recommendation"]
        socket = rows["DEMO-B310"]["recommendation"]
        self.assertEqual(cable["excluded_customers"], {"DEMO-C017": 900.0})
        self.assertGreater(socket["imputed_stockouts"], 0)


if __name__ == "__main__":
    unittest.main()
