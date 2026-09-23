"""Reproducible rolling-origin evaluation on fictional customer-level data."""

from __future__ import annotations

import argparse
import calendar
import json
from datetime import date, timedelta
from pathlib import Path

from .demo import demo_dataset
from .engine import ForecastSettings, Product, recommend_all


def _metrics(points: list[dict]) -> dict:
    actual = sum(point["actual"] for point in points)
    model_error = sum(abs(point["forecast"] - point["actual"]) for point in points)
    baseline_error = sum(abs(point["baseline"] - point["actual"]) for point in points)
    return {
        "observations": len(points),
        "model_wape": round(model_error / actual, 4) if actual else None,
        "baseline_wape": round(baseline_error / actual, 4) if actual else None,
        "model_mae": round(model_error / len(points), 2) if points else None,
        "baseline_mae": round(baseline_error / len(points), 2) if points else None,
        "model_better": model_error < baseline_error,
    }


def evaluate() -> dict:
    products, truth = demo_dataset()
    points: list[dict] = []
    months = sorted(next(iter(truth.values())))
    for month in [value for value in months if "2025-01" <= value <= "2026-08"]:
        year, number = map(int, month.split("-"))
        snapshot = date(year, number, 1) - timedelta(days=1)
        days = calendar.monthrange(year, number)[1]
        forecasts = {row["code"]: row for row in recommend_all(products, ForecastSettings(snapshot, days))}
        for product in products:
            history = [product.monthly_sales[key] for key in sorted(product.monthly_sales)
                       if date(*map(int, key.split("-")),
                               calendar.monthrange(*map(int, key.split("-")))[1]) <= snapshot]
            trailing = history[-12:]
            baseline = sum(trailing) / len(trailing) if trailing else 0.0
            points.append({
                "month": month, "supplier": product.supplier, "code": product.code,
                "actual": truth[product.code][month],
                "forecast": forecasts[product.code]["coverage_forecast"],
                "baseline": round(baseline, 2),
            })
    return {
        "dataset": "Deterministic fictional products and customers; regular demand is known before anomalies",
        "period": "2025-01..2026-08",
        "method": "Monthly rolling origin; forecast and trailing-12-month baseline use only months closed by snapshot",
        "overall": _metrics(points),
        "by_supplier": {supplier: _metrics([p for p in points if p["supplier"] == supplier])
                        for supplier in sorted({p["supplier"] for p in points})},
        "by_month": {month: _metrics([p for p in points if p["month"] == month])
                     for month in sorted({p["month"] for p in points})},
        "points": points,
    }


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def markdown_report(report: dict) -> str:
    overall = report["overall"]
    lines = [
        "# Бэктест прогноза KAZ-AI",
        "",
        "**Данные вымышлены.** Результат измеряет поведение прототипа на известных сценариях и не доказывает точность на продажах партнёра.",
        "",
        f"Период: {report['period']}; {overall['observations']} прогнозов по товарам и месяцам.",
        "Перед каждым прогнозом доступны только завершённые к той дате месяцы. Цель — регулярный спрос до внесённых в демонстрационный набор разового заказа и дефицита.",
        "База сравнения: среднее сырых продаж за последние 12 завершённых месяцев. WAPE = сумма абсолютных ошибок / сумма фактического регулярного спроса; меньше — лучше.",
        "",
        "| Срез | Модель WAPE | Простая база WAPE | Модель MAE | База MAE |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| Все товары | {_percent(overall['model_wape'])} | {_percent(overall['baseline_wape'])} | {overall['model_mae']} | {overall['baseline_mae']} |",
    ]
    for supplier, metrics in report["by_supplier"].items():
        lines.append(f"| {supplier} | {_percent(metrics['model_wape'])} | {_percent(metrics['baseline_wape'])} | {metrics['model_mae']} | {metrics['baseline_mae']} |")
    lines += ["", "## По месяцам", "", "| Месяц | Модель WAPE | База WAPE | Лучше |",
              "| --- | ---: | ---: | --- |"]
    for month, metrics in report["by_month"].items():
        lines.append(f"| {month} | {_percent(metrics['model_wape'])} | {_percent(metrics['baseline_wape'])} | {'Модель' if metrics['model_better'] else 'База'} |")
    lines += ["", "Сценарии разового заказа и дефицита созданы вручную; файл `backtest.json` содержит все пары прогноз/факт.",
              "Повторить расчёт: `python3 -m kaz_ai.backtest`.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate fictional replenishment demand forecasts")
    parser.add_argument("--output", type=Path, default=Path("reports"))
    args = parser.parse_args()
    report = evaluate()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "backtest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "backtest.md").write_text(markdown_report(report), encoding="utf-8")
    print(f"Model WAPE: {_percent(report['overall']['model_wape'])}; baseline WAPE: {_percent(report['overall']['baseline_wape'])}")
    print(f"Wrote {args.output / 'backtest.md'} and {args.output / 'backtest.json'}")


if __name__ == "__main__":
    main()
