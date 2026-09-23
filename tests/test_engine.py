import tempfile
import unittest
from datetime import date
from pathlib import Path

from kaz_ai.engine import Arrival, ForecastSettings, Product, Sale, recommend, recommend_all
from kaz_ai.server import AppState


AS_OF = date(2026, 9, 22)


def months(value=100):
    return {f"{year}-{month:02d}": value for year in (2024, 2025) for month in range(1, 13)}


def product(**changes):
    p = Product("Supplier A", "SKU-1", "Тестовый товар", months(), free_stock=0,
                stock_as_of=AS_OF, category="A", moq=1)
    for key, value in changes.items():
        setattr(p, key, value)
    return p


def run(p, as_of=AS_OF, days=30, context=None):
    context = context or {(p.supplier, p.category or "__supplier__"): {m: 1.0 for m in range(1, 13)}}
    return recommend(p, ForecastSettings(as_of, days), context)


class ReplenishmentAcceptanceTests(unittest.TestCase):
    def test_exact_gap_and_moq_rounding(self):
        p = product(free_stock=20, moq=10,
                    inbound=[Arrival(date(2026, 10, 1), 10)])
        row = run(p)
        self.assertEqual(row["quantity"], 70)
        self.assertIn("Потребность до округления", row["reason"])

    def test_each_baseline_input_changes_recommendation(self):
        p = product()
        base = run(p)["quantity"]
        p.free_stock = 20
        self.assertLess(run(p)["quantity"], base)
        p.free_stock = 0
        p.inbound = [Arrival(date(2026, 10, 1), 20)]
        self.assertLess(run(p)["quantity"], base)
        p.inbound = []
        p.source_growth = 0.5
        self.assertGreater(run(p)["quantity"], base)
        p.source_growth = None
        p.monthly_sales = {month: value * 1.3 for month, value in p.monthly_sales.items()}
        self.assertGreater(run(p)["quantity"], base)
        p.monthly_sales = months()
        context = {
            (p.supplier, "A"): {m: 1.0 for m in range(1, 13)},
            (p.supplier, "B"): {m: 1.7 for m in range(1, 13)},
        }
        p.category = "A"
        a = run(p, context=context)["quantity"]
        p.category = "B"
        self.assertGreater(run(p, context=context)["quantity"], a)

    def test_seasonality_and_sustained_growth(self):
        values = months()
        for month in values:
            if month.endswith("-01"):
                values[month] = 230
            if month.endswith("-07"):
                values[month] = 35
        p = product(monthly_sales=values, stock_as_of=date(2026, 1, 1))
        january = run(p, as_of=date(2026, 1, 1))["forecast"]
        p.stock_as_of = date(2026, 7, 1)
        july = run(p, as_of=date(2026, 7, 1))["forecast"]
        self.assertGreater(january, july * 2)
        rising = months()
        for month in sorted(rising)[-6:]:
            rising[month] = 150
        growing = run(product(monthly_sales=rising))
        self.assertGreater(growing["growth"], 0)
        self.assertGreater(growing["forecast"], run(product())["forecast"])

    def test_stockout_imputes_lost_demand(self):
        values = months()
        values["2025-07"] = 8
        raw = run(product(monthly_sales=values))
        corrected = run(product(monthly_sales=values, stockout_days={"2025-07": 24}))
        self.assertGreater(corrected["imputed_stockouts"], 0)
        self.assertGreater(corrected["forecast"], raw["forecast"])

    def test_one_off_customer_order_does_not_inflate_regular_need(self):
        baseline = run(product())["quantity"]
        values = months()
        values["2025-05"] += 1000
        spike = product(monthly_sales=values, sales=[Sale("2025-05", 1000, "INV-1", "anon-1")])
        result = run(spike)
        self.assertGreater(result["excluded_outliers"], 900)
        self.assertLessEqual(result["quantity"], baseline + 5)

    def test_supplier_grouping_reason_moq_and_missing_stock(self):
        a = product(moq=10)
        b = product(supplier="Supplier B", code="SKU-2", free_stock=None, stock_as_of=None)
        rows = recommend_all([b, a], ForecastSettings(AS_OF, 30))
        self.assertEqual([r["supplier"] for r in rows], ["Supplier A", "Supplier B"])
        self.assertEqual(rows[0]["quantity"] % 10, 0)
        self.assertIn("Спрос", rows[0]["reason"])
        self.assertEqual(rows[1]["status"], "needs_stock")
        self.assertIsNone(rows[1]["quantity"])

    def test_human_approval_persists_and_export_requires_it(self):
        p = product()
        report = {"as_of": AS_OF.isoformat(), "archives": ["test"], "products": 1}
        with tempfile.TemporaryDirectory() as temp:
            storage = Path(temp) / "orders.json"
            app = AppState([p], report, storage)
            suggested = app.rows(30)[0]["quantity"]
            self.assertEqual(app.approve([{"supplier": p.supplier, "code": p.code, "quantity": suggested}], 30), 1)
            self.assertIn("SKU-1", app.export_csv().decode("utf-8-sig"))
            self.assertEqual(len(AppState([product()], report, storage).approved), 1)
            app.set_stock(p.supplier, p.code, 20)
            self.assertEqual(len(app.approved), 0)


if __name__ == "__main__":
    unittest.main()
