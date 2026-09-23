import tempfile
import unittest
from datetime import date
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from kaz_ai.demo import demo_products
from kaz_ai.engine import Arrival, ForecastSettings, Product, Sale, recommend, recommend_all
from kaz_ai.parameters import (
    ParameterSet,
    ParameterSource,
    confirmed_parameter,
    missing_parameter,
    scenario_parameter,
    user_parameter,
)
from kaz_ai.server import AppState, Handler


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
    def test_parameter_readiness_distinguishes_fact_scenario_and_missing(self):
        values = ParameterSet()
        values.set("lead_time_days", confirmed_parameter(14, ParameterSource.PARTNER_EXPORT, "days"))
        values.set("safety_days", scenario_parameter(5, "days"))
        values.set("moq", missing_parameter("units"))
        readiness = values.readiness(("lead_time_days", "safety_days", "moq"))
        self.assertEqual(readiness["status"], "blocked")
        self.assertEqual(readiness["missing"], ["moq"])
        self.assertEqual(readiness["unverified"], ["safety_days"])

    def test_user_parameters_are_order_ready_and_drive_cost(self):
        p = product(moq=99, lead_time_days=None, safety_days=0)
        p.parameters.set("lead_time_days", user_parameter(10, "days"))
        p.parameters.set("safety_days", user_parameter(4, "days"))
        p.parameters.set("moq", user_parameter(5, "units"))
        p.parameters.set("unit_cost", user_parameter(1200, "KZT"))
        row = run(p)
        self.assertEqual(row["terms_readiness"]["status"], "confirmed")
        self.assertTrue(row["terms_readiness"]["order_ready"])
        self.assertEqual(row["moq"], 5)
        self.assertEqual(row["lead_time_days"], 10)
        self.assertEqual(row["safety_days"], 4)
        self.assertEqual(row["order_value"], row["quantity"] * 1200)
        self.assertEqual(row["pricing_status"], "user_input")

    def test_demo_cost_is_explicitly_an_assumption(self):
        row = run(demo_products()[0])
        self.assertEqual(row["terms_readiness"]["status"], "scenario")
        self.assertFalse(row["terms_readiness"]["order_ready"])
        self.assertGreater(row["order_value"], 0)
        self.assertEqual(row["pricing_status"], "assumption")
        self.assertEqual(row["parameter_trace"]["unit_cost"]["source"], "demo_assumption")

    def test_unspecified_purchase_terms_are_not_silently_confirmed(self):
        p = Product("Supplier A", "SKU-MISSING", "Без условий", months(), free_stock=0, stock_as_of=AS_OF)
        row = run(p)
        self.assertEqual(row["terms_readiness"]["status"], "blocked")
        self.assertCountEqual(row["terms_readiness"]["missing"], ["lead_time_days", "safety_days", "moq"])
        self.assertIsNone(row["lead_time_days"])
        self.assertIsNone(row["safety_days"])
        self.assertEqual(row["moq"], 1)
        self.assertIn("Параметры заказа неполные", row["flags"])

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

    def test_lead_time_and_safety_stock_change_order(self):
        base = run(product())
        with_lead = run(product(lead_time_days=12))
        with_safety = run(product(lead_time_days=12, safety_days=6))
        self.assertGreater(with_lead["quantity"], base["quantity"])
        self.assertGreater(with_safety["quantity"], with_lead["quantity"])
        self.assertEqual(with_safety["target_days"], 48)
        self.assertIn("страховой запас: 6 дн.", with_safety["reason"])
        self.assertGreater(with_safety["forecast"], with_safety["coverage_forecast"])

    def test_urgency_uses_demand_before_new_delivery(self):
        exposed = run(product(free_stock=10, lead_time_days=12))
        covered = run(product(free_stock=10, lead_time_days=12,
                              inbound=[Arrival(date(2026, 10, 1), 40)]))
        self.assertGreater(exposed["risk_before_delivery"], 0)
        self.assertEqual(exposed["urgency"], "высокая")
        self.assertEqual(covered["risk_before_delivery"], 0)
        self.assertEqual(covered["urgency"], "обычная")

    def test_one_off_customer_order_does_not_inflate_regular_need(self):
        baseline = run(product())["quantity"]
        values = months()
        values["2025-05"] += 1000
        regular = [Sale("2025-05", 25, f"REG-{i}", f"DEMO-C{i:03d}") for i in range(4)]
        one_customer = [Sale("2025-05", 100, f"BIG-{i}", "DEMO-C017") for i in range(10)]
        spike = product(monthly_sales=values, sales=regular + one_customer)
        result = run(spike)
        self.assertGreater(result["excluded_outliers"], 900)
        self.assertEqual(result["excluded_customers"], {"DEMO-C017": 1000.0})
        self.assertLessEqual(result["quantity"], baseline + 5)

    def test_synthetic_sales_have_customer_ids_and_reconcile(self):
        products = demo_products()
        self.assertEqual(len(products), 12)
        for item in products:
            self.assertTrue(all(sale.customer_id for sale in item.sales))
            for month, quantity in item.monthly_sales.items():
                self.assertEqual(sum(sale.quantity for sale in item.sales if sale.month == month), quantity)
        cable = next(item for item in products if item.code == "DEMO-A202")
        result = run(cable)
        self.assertEqual(result["excluded_customers"], {"DEMO-C017": 900.0})
        self.assertIn("DEMO-C017", result["reason"])

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
            csv_bytes = app.export_csv()
            self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))
            csv_text = csv_bytes.decode("utf-8-sig")
            self.assertEqual(csv_text.splitlines()[0].split(";"), [
                "Поставщик", "Код 1С", "Наименование", "Количество", "Рекомендация",
                "Дата среза", "Горизонт, дни", "Обоснование",
            ])
            self.assertIn("SKU-1", csv_text)
            self.assertEqual(len(AppState([product()], report, storage).approved), 1)
            app.set_stock(p.supplier, p.code, 20)
            self.assertEqual(len(app.approved), 0)

    def test_approval_rejects_fractional_quantity_and_invalid_items(self):
        p = product(moq=1)
        report = {"as_of": AS_OF.isoformat(), "archives": ["test"], "products": 1}
        with tempfile.TemporaryDirectory() as temp:
            app = AppState([p], report, Path(temp) / "orders.json")
            for quantity in (1.5, True, "1.5"):
                with self.subTest(quantity=quantity), self.assertRaisesRegex(ValueError, "Некорректное количество"):
                    app.approve([{"supplier": p.supplier, "code": p.code, "quantity": quantity}], 30)
            with self.assertRaisesRegex(ValueError, "Некорректная позиция"):
                app.approve(["SKU-1"], 30)
            with self.assertRaisesRegex(ValueError, "Выберите от 1 до 500"):
                app.approve({"code": p.code}, 30)
            self.assertEqual(app.approved, {})

    def test_api_rejects_non_object_json_without_crashing(self):
        p = product()
        report = {"as_of": AS_OF.isoformat(), "archives": ["test"], "products": 1}
        with tempfile.TemporaryDirectory() as temp:
            state = AppState([p], report, Path(temp) / "orders.json")
            handler = type("TestHandler", (Handler,), {"state": state})
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request("POST", "/api/approve", "[]", {"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                self.assertIn("Ожидается объект JSON", response.read().decode())
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_supplier_exports_and_adjustments_have_audit_history(self):
        a = product(moq=10)
        b = product(supplier="Supplier B", code="SKU-2", moq=5)
        report = {"as_of": AS_OF.isoformat(), "archives": ["test"], "products": 2}
        with tempfile.TemporaryDirectory() as temp:
            app = AppState([a, b], report, Path(temp) / "orders.json")
            rows = {(row["supplier"], row["code"]): row for row in app.rows(30)}
            app.approve([{"supplier": supplier, "code": code, "quantity": row["quantity"]}
                         for (supplier, code), row in rows.items()], 30)
            csv_a = app.export_csv("Supplier A").decode("utf-8-sig")
            self.assertIn("SKU-1", csv_a)
            self.assertNotIn("SKU-2", csv_a)
            self.assertEqual(app.supplier_summary(30)[0]["approved_positions"], 1)
            adjusted = rows[("Supplier A", "SKU-1")]["quantity"] + 10
            app.approve([{"supplier": "Supplier A", "code": "SKU-1", "quantity": adjusted}], 30)
            audit = app.audit_events()
            self.assertEqual(len(audit), 3)
            self.assertEqual(audit[0]["previous_quantity"], adjusted - 10)
            self.assertEqual(audit[0]["approved_quantity"], adjusted)

    def test_partner_orders_remain_blocked_without_confirmed_terms(self):
        p = product()
        report = {"mode": "partner", "as_of": AS_OF.isoformat(), "archives": ["partner.zip"], "products": 1}
        with tempfile.TemporaryDirectory() as temp:
            storage = Path(temp) / "orders.json"
            storage.write_text('{"Supplier A␟SKU-1":{"supplier":"Supplier A","code":"SKU-1","quantity":10}}', encoding="utf-8")
            app = AppState([p], report, storage)
            row = app.rows(30)[0]
            self.assertGreater(row["forecast"], 0)
            self.assertEqual(row["status"], "review_required")
            self.assertIsNone(row["quantity"])
            self.assertEqual(app.approved, {})
            with self.assertRaisesRegex(ValueError, "заблокирован"):
                app.approve([{"supplier": p.supplier, "code": p.code, "quantity": 10}], 30)
            with self.assertRaisesRegex(ValueError, "заблокирован"):
                app.export_csv()

    def test_partner_order_unlocks_only_after_explicit_terms(self):
        p = product(lead_time_days=None, safety_days=None, moq=None)
        p.parameters.set("lead_time_days", user_parameter(14, "days"))
        p.parameters.set("safety_days", user_parameter(5, "days"))
        p.parameters.set("moq", confirmed_parameter(10, ParameterSource.PARTNER_EXPORT, "units"))
        p.parameters.set("unit_cost", user_parameter(2500, "KZT"))
        report = {"mode": "partner", "as_of": AS_OF.isoformat(), "archives": ["partner.zip"], "products": 1}
        with tempfile.TemporaryDirectory() as temp:
            app = AppState([p], report, Path(temp) / "orders.json")
            row = app.rows(30)[0]
            self.assertEqual(row["status"], "ready")
            self.assertTrue(row["terms_readiness"]["order_ready"])
            self.assertGreater(row["quantity"], 0)
            self.assertEqual(row["order_value"], row["quantity"] * 2500)
            self.assertEqual(
                app.approve([{"supplier": p.supplier, "code": p.code, "quantity": row["quantity"]}], 30),
                1,
            )
            self.assertIn("SKU-1", app.export_csv().decode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
