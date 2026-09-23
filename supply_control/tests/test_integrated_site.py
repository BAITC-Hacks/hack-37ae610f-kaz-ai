from __future__ import annotations

import json
import importlib.util
import unittest
from pathlib import Path

from scripts.build_integrated_site import _synthetic_payload


PUBLIC_BUILDER_PATH = Path(__file__).resolve().parents[1] / "site/scripts/build_public_demo.py"
PUBLIC_BUILDER_SPEC = importlib.util.spec_from_file_location("public_demo_builder", PUBLIC_BUILDER_PATH)
assert PUBLIC_BUILDER_SPEC and PUBLIC_BUILDER_SPEC.loader
PUBLIC_BUILDER = importlib.util.module_from_spec(PUBLIC_BUILDER_SPEC)
PUBLIC_BUILDER_SPEC.loader.exec_module(PUBLIC_BUILDER)
public_partner_payload = PUBLIC_BUILDER.public_partner_payload


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

    def test_public_partner_payload_keeps_derived_rows_but_removes_sensitive_data(self) -> None:
        source = {
            "summary": {
                "mode": "partner",
                "source_skus": 1,
                "valued_order_lines": 1,
                "total_order_value_demo": 999.0,
            },
            "assumptions": {"notes": ["internal"]},
            "proofs": [],
            "recommendations": [
                {
                    "sku": "REAL-1",
                    "name": "Partner product",
                    "unit_cost": 333.0,
                    "order_value_demo": 999.0,
                    "customer_id": "CUSTOMER-1",
                    "recommendation": {
                        "recommended_quantity": 3,
                        "free_stock": 2,
                        "excluded_customers": {"CUSTOMER-1": 100},
                    },
                }
            ],
        }
        public = public_partner_payload(source)
        row = public["recommendations"][0]
        self.assertEqual(row["sku"], "REAL-1")
        self.assertEqual(row["recommendation"]["recommended_quantity"], 3)
        self.assertIsNone(row["unit_cost"])
        self.assertIsNone(row["order_value_demo"])
        self.assertIsNone(public["summary"]["total_order_value_demo"])
        rendered = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("CUSTOMER-1", rendered)
        self.assertNotIn("customer_id", rendered)


if __name__ == "__main__":
    unittest.main()
