"""Build a public, client-only demo from fictional data (stdlib only)."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kaz_ai.demo import demo_products
from kaz_ai.engine import ForecastSettings, recommend_all


def build(output: Path) -> None:
    as_of = date(2026, 9, 22)
    products = demo_products(as_of)
    customers = {sale.customer_id for product in products for sale in product.sales if sale.customer_id}
    meta = {
        "mode": "synthetic", "as_of": as_of.isoformat(), "dataset_id": "synthetic-v3-2026-09-22",
        "archives": ["Синтетический пример"], "products": len(products),
        "customers": len(customers), "transactions": sum(len(product.sales) for product in products),
        "suppliers": sorted({product.supplier for product in products}),
        "categories": sorted({product.category for product in products if product.category}),
        "warnings": [],
    }
    payload = {
        "meta": meta,
        "rows": {str(days): recommend_all(products, ForecastSettings(as_of, days))
                 for days in (14, 30, 45, 60)},
        "sales": [{"supplier": product.supplier, "code": product.code, "month": sale.month,
                   "document": sale.document, "customer_id": sale.customer_id, "quantity": sale.quantity}
                  for product in products for sale in product.sales],
    }
    output.mkdir(parents=True, exist_ok=True)
    for name in ("style.css", "i18n.js", "app.js", "static-api.js"):
        shutil.copyfile(ROOT / "web" / name, output / name)
    for page in ("index", "recommendations", "suppliers", "audit", "settings"):
        html = (ROOT / "web" / f"{page}.html").read_text(encoding="utf-8")
        html = html.replace('href="/style.css"', 'href="./style.css"')
        html = html.replace('src="/i18n.js"', 'src="./i18n.js"')
        html = html.replace('src="/app.js"', 'src="./static_data.js"></script>\n  <script src="./static-api.js"></script>\n  <script src="./app.js"')
        html = html.replace("Локальный прототип · данные не отправляются поставщикам",
                            "Демоверсия · решения хранятся только в этом браузере")
        if page == "index":
            html = html.replace('href="/api/synthetic-sales.csv"',
                                'href="./synthetic_customer_sales.csv" download="synthetic_customer_sales.csv"')
        if page == "recommendations":
            html = html.replace('<a id="exportLink" href="/api/export.csv"',
                                '<button id="exportLink" type="button"')
            html = html.replace('data-i18n="exportCsv">Экспорт CSV ↗</a>',
                                'data-i18n="exportCsv">Экспорт CSV ↗</button>')
        (output / f"{page}.html").write_text(html, encoding="utf-8")
    with (output / "synthetic_customer_sales.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\n")
        writer.writerow(["Поставщик", "Артикул", "Месяц", "Документ", "ID клиента", "Количество"])
        writer.writerows((sale["supplier"], sale["code"], sale["month"], sale["document"],
                          sale["customer_id"], sale["quantity"]) for sale in payload["sales"])
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    (output / "static_data.js").write_text("window.KAZ_STATIC_DATA = " + data + ";\n", encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Built fictional public demo in {output}: {len(products)} products, {len(payload['sales'])} sales")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "site")
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
