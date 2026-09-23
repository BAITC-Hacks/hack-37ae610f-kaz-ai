"""Small synthetic set covering all required behavior without partner data."""

from __future__ import annotations

from datetime import date

from .engine import Arrival, Product, Sale


def demo_products() -> list[Product]:
    months = [f"{year}-{month:02d}" for year in (2024, 2025, 2026) for month in range(1, 13) if (year, month) <= (2026, 8)]

    def history(base: float, seasonal: dict[int, float] | None = None, annual_growth: float = 0) -> dict[str, float]:
        return {key: round(base * (seasonal or {}).get(int(key[-2:]), 1) * (1 + annual_growth * (int(key[:4]) - 2024))) for key in months}

    winter = {1: 1.8, 2: 1.5, 6: 0.6, 7: 0.6, 8: 0.7, 12: 1.6}
    p1 = Product("IEK", "IEK-101", "Автоматический выключатель 16А", history(64, winter),
                 free_stock=38, stock_as_of=date(2026, 9, 22), category="Электрика", moq=10,
                 inbound=[Arrival(date(2026, 10, 1), 20)])
    p2 = Product("IEK", "IEK-202", "Кабель монтажный", history(85, annual_growth=0.18),
                 free_stock=25, stock_as_of=date(2026, 9, 22), category="Кабель", moq=5)
    p2.monthly_sales["2025-05"] += 900
    p2.sales.append(Sale("2025-05", 900, "ONE-OFF", "anon-17"))
    p3 = Product("Systeme electric", "SE-310", "Розетка с заземлением", history(52),
                 free_stock=14, stock_as_of=date(2026, 9, 22), category="1", moq=10)
    p3.monthly_sales["2025-09"] = 4
    p3.stockout_days["2025-09"] = 22
    p4 = Product("Systeme electric", "SE-440", "Переключатель двухклавишный", history(24),
                 free_stock=80, stock_as_of=date(2026, 9, 22), category="2", moq=4)
    return [p1, p2, p3, p4]
