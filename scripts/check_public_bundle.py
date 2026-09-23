"""Fail release if the published bundle includes non-synthetic order data."""

from __future__ import annotations

import json
import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PREFIX = "window.KAZ_STATIC_DATA = "


def main() -> None:
    source = (SITE / "static_data.js").read_text(encoding="utf-8").strip()
    if not source.startswith(PREFIX) or not source.endswith(";"):
        raise SystemExit("Unexpected public data format")
    data = json.loads(source[len(PREFIX):-1])
    if data["meta"]["mode"] != "synthetic":
        raise SystemExit("Public bundle is not in synthetic mode")
    rows = [row for group in data["rows"].values() for row in group]
    if not rows or any(not row["code"].startswith("DEMO-") for row in rows):
        raise SystemExit("Public recommendations contain a non-demo product")
    if not data["sales"] or any(not sale["customer_id"].startswith("DEMO-") or not sale["code"].startswith("DEMO-") for sale in data["sales"]):
        raise SystemExit("Public sales contain a non-demo customer or product")
    with (SITE / "synthetic_customer_sales.csv").open(encoding="utf-8-sig", newline="") as stream:
        exported_sales = list(csv.DictReader(stream, delimiter=";"))
    if len(exported_sales) != len(data["sales"]) or any(
        not sale["ID клиента"].startswith("DEMO-") or not sale["Артикул"].startswith("DEMO-")
        for sale in exported_sales
    ):
        raise SystemExit("Public sales CSV is incomplete or contains non-demo IDs")
    for page in ("index", "recommendations", "suppliers", "audit", "settings"):
        if not (SITE / f"{page}.html").is_file():
            raise SystemExit(f"Missing public page: {page}")
    if (SITE / "data" / "recommendations.json").exists():
        raise SystemExit("Partner recommendations copied into public bundle")
    print(f"Public bundle verified: {len(data['rows']['30'])} synthetic products, {len(data['sales'])} synthetic sales")


if __name__ == "__main__":
    main()
