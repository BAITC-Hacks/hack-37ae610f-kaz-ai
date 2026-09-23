"""Deterministic fictional catalogue and customer-level sales for public demos."""

from __future__ import annotations

from datetime import date

from .engine import Arrival, Product, Sale
from .parameters import scenario_parameter


def demo_dataset(as_of: date = date(2026, 9, 22)) -> tuple[list[Product], dict[str, dict[str, float]]]:
    """Return observable transactions and independent regular-demand ground truth."""
    months = [f"{year}-{month:02d}" for year in (2024, 2025, 2026)
              for month in range(1, 13) if (year, month) <= (2026, 8)]

    def history(base: float, seasonal: dict[int, float] | None = None,
                annual_growth: float = 0) -> dict[str, float]:
        return {key: round(base * (seasonal or {}).get(int(key[-2:]), 1)
                           * (1 + annual_growth * (int(key[:4]) - 2024))) for key in months}

    winter = {1: 1.8, 2: 1.5, 6: 0.6, 7: 0.6, 8: 0.7, 12: 1.6}
    summer = {1: 0.6, 2: 0.7, 6: 1.5, 7: 1.8, 8: 1.6, 12: 0.7}
    a, b = "Демо-поставщик А", "Демо-поставщик Б"
    products = [
        Product(a, "DEMO-A101", "Автоматический выключатель 16А", history(64, winter),
                free_stock=38, stock_as_of=as_of, category="Защита", moq=10,
                inbound=[Arrival(date(2026, 10, 1), 20)]),
        Product(a, "DEMO-A202", "Кабель монтажный", history(85, annual_growth=0.18),
                free_stock=25, stock_as_of=as_of, category="Кабель", moq=5),
        Product(b, "DEMO-B310", "Розетка с заземлением", history(52),
                free_stock=14, stock_as_of=as_of, category="Розетки", moq=10),
        Product(b, "DEMO-B440", "Переключатель двухклавишный", history(24),
                free_stock=80, stock_as_of=as_of, category="Выключатели", moq=4),
        Product(a, "DEMO-A303", "Распределительная коробка", history(18, summer),
                free_stock=6, stock_as_of=as_of, category="Монтаж", moq=12),
        Product(a, "DEMO-A404", "Гофротруба 20 мм", history(50, summer),
                free_stock=120, stock_as_of=as_of, category="Монтаж", moq=10),
        Product(b, "DEMO-B510", "Диммер электронный", history(14, annual_growth=0.22),
                free_stock=3, stock_as_of=as_of, category="Выключатели", moq=5,
                inbound=[Arrival(date(2026, 10, 8), 10)]),
        Product(b, "DEMO-B620", "Рамка двухместная", history(42),
                free_stock=15, stock_as_of=as_of, category="Розетки", moq=10),
        Product(a, "DEMO-A505", "Контактор модульный", history(8),
                free_stock=2, stock_as_of=as_of, category="Защита", moq=2),
        Product(b, "DEMO-B730", "Панель декоративная", history(11),
                free_stock=60, stock_as_of=as_of, category="Розетки", moq=5),
        Product(a, "DEMO-A606", "УЗО двухполюсное", history(33),
                free_stock=0, stock_as_of=as_of, category="Защита", moq=6,
                inbound=[Arrival(date(2026, 11, 20), 24)]),
        Product(b, "DEMO-B840", "Корпус щита", history(6),
                free_stock=None, stock_as_of=None, category="Монтаж", moq=2),
    ]

    safety_by_category = {"Защита": 7, "Кабель": 5, "Монтаж": 4,
                          "Розетки": 6, "Выключатели": 5}
    fictional_unit_costs = {
        "DEMO-A101": 4200, "DEMO-A202": 780, "DEMO-B310": 3100,
        "DEMO-B440": 3600, "DEMO-A303": 1250, "DEMO-A404": 690,
        "DEMO-B510": 8900, "DEMO-B620": 1150, "DEMO-A505": 12400,
        "DEMO-B730": 2400, "DEMO-A606": 15900, "DEMO-B840": 18500,
    }
    for product in products:
        product.lead_time_days = 12 if product.supplier == a else 21
        product.safety_days = safety_by_category[product.category]
        product.parameters.set(
            "lead_time_days",
            scenario_parameter(product.lead_time_days, "days", "Вымышленный срок для хакатонного сценария"),
        )
        product.parameters.set(
            "safety_days",
            scenario_parameter(product.safety_days, "days", "Вымышленная политика страхового запаса"),
        )
        product.parameters.set(
            "moq",
            scenario_parameter(product.moq, "units", "Вымышленная кратность демонстрационного товара"),
        )
        product.parameters.set(
            "unit_cost",
            scenario_parameter(fictional_unit_costs[product.code], "KZT", "Вымышленная закупочная цена"),
        )
    regular_demand = {product.code: dict(product.monthly_sales) for product in products}

    # Detail lines reconcile to monthly totals; all customer IDs are fictional.
    products[1].monthly_sales["2025-05"] += 900
    products[2].monthly_sales["2025-09"] = 4
    products[2].stockout_days["2025-09"] = 22
    for product in products:
        for month, total in product.monthly_sales.items():
            regular = total - (900 if product.code == "DEMO-A202" and month == "2025-05" else 0)
            parts = [regular // 4 + (i < regular % 4) for i in range(4)]
            for i, quantity in enumerate(parts, start=1):
                if quantity:
                    product.sales.append(Sale(month, quantity,
                                              f"{product.code}-{month}-{i}", f"DEMO-C{i:03d}"))
        if product.code == "DEMO-A202":
            product.sales.append(Sale("2025-05", 900, "DEMO-ONE-OFF-001", "DEMO-C017"))
    return products, regular_demand


def demo_products(as_of: date = date(2026, 9, 22)) -> list[Product]:
    return demo_dataset(as_of)[0]
