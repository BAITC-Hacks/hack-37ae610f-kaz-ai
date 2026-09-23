"""Build a public, client-only demo from fictional data (stdlib only)."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date
from pathlib import Path

from kaz_ai.demo import demo_products
from kaz_ai.engine import ForecastSettings, recommend_all


ROOT = Path(__file__).resolve().parent.parent


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
    for name in ("style.css", "app.js", "static-api.js"):
        shutil.copyfile(ROOT / "web" / name, output / name)
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/style.css"', 'href="./style.css"')
    html = html.replace('src="/app.js"', 'src="./static_data.js"></script>\n  <script src="./static-api.js"></script>\n  <script src="./app.js"')
    html = html.replace("Локальный прототип · данные не отправляются поставщикам",
                        "Демоверсия · решения хранятся только в этом браузере")
    (output / "index.html").write_text(html, encoding="utf-8")
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
