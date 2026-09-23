from __future__ import annotations

import argparse
import hashlib
from datetime import date
from pathlib import Path

from .demo import demo_products
from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Kaz-AI supplier replenishment demo")
    parser.add_argument("--iek", type=Path, help="Path to IEK.zip (kept outside Git)")
    parser.add_argument("--systeme", type=Path, help="Path to Systeme electric.zip (kept outside Git)")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 22), help="Source snapshot date YYYY-MM-DD")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    paths = [p for p in (args.iek, args.systeme) if p is not None]
    if paths:
        from .source import load_archives

        products, report = load_archives(paths, args.as_of)
        report["mode"] = "partner"
        digest = hashlib.sha256()
        for path in paths:
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
        digest.update(args.as_of.isoformat().encode())
        report["dataset_id"] = digest.hexdigest()[:12]
    else:
        products = demo_products(args.as_of)
        customers = {sale.customer_id for product in products for sale in product.sales if sale.customer_id}
        report = {"mode": "synthetic", "as_of": args.as_of.isoformat(), "archives": ["Синтетический пример"],
                  "products": len(products), "ready_stock": sum(p.free_stock is not None for p in products),
                  "customers": len(customers), "transactions": sum(len(p.sales) for p in products),
                  "dataset_id": f"synthetic-v2-{args.as_of.isoformat()}", "warnings": [], "files": []}
    serve(products, report, args.port)


if __name__ == "__main__":
    main()
