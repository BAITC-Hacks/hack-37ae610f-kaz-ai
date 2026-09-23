"""Local-only review UI. Supplier dispatch is deliberately absent."""

from __future__ import annotations

import csv
import io
import json
import hashlib
import math
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from .engine import ForecastSettings, Product, recommend_all


ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "web"


def _csv_text(value: object) -> str:
    text = str(value if value is not None else "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


class AppState:
    def __init__(self, products: list[Product], report: dict, storage: Path):
        self.products = products
        self.report = report
        self.as_of = date.fromisoformat(report["as_of"])
        self.storage = storage
        self.stock_storage = storage.with_name(storage.stem + "-stocks.json")
        self.audit_storage = storage.with_name(storage.stem + "-audit.jsonl")
        self.lock = Lock()
        self.cache: dict[int, list[dict]] = {}
        self.approved: dict[str, dict] = {}
        self.by_key = {(p.supplier, p.code): p for p in products}
        if self.stock_storage.exists():
            saved_stocks = json.loads(self.stock_storage.read_text(encoding="utf-8"))
            for key, value in saved_stocks.items():
                supplier, _, code = key.partition("\u241f")
                product = self.by_key.get((supplier, code))
                if product and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                    product.free_stock = float(value)
                    product.stock_as_of = self.as_of
                    product.warnings.append("Остаток внесён вручную")
        if storage.exists() and report.get("mode") != "partner":
            saved = json.loads(storage.read_text(encoding="utf-8"))
            self.approved = {str(k): v for k, v in saved.items() if isinstance(v, dict)}

    def rows(self, days: int) -> list[dict]:
        if days not in self.cache:
            rows = recommend_all(self.products, ForecastSettings(self.as_of, days))
            if self.report.get("mode") == "partner":
                # Archive exports do not confirm lead times or safety-stock policy.
                # Show demand forecasts, but block unverified purchase quantities.
                for row in rows:
                    row["quantity"] = None
                    if row["status"] == "ready":
                        row["status"] = "review_required"
                    row["flags"] = list(dict.fromkeys([*row["flags"], "Параметры заказа не подтверждены"]))
                    row["reason"] = "Предварительный прогноз. Заказ заблокирован до подтверждения срока поставки, страхового запаса и кратности заказа. " + row["reason"]
            self.cache[days] = rows
        return self.cache[days]

    def approve(self, items: list[dict], days: int) -> int:
        if self.report.get("mode") == "partner":
            raise ValueError("Заказ по данным партнёра заблокирован до подтверждения параметров закупки")
        available = {(r["supplier"], r["code"]): r for r in self.rows(days)}
        prepared: list[tuple[str, dict]] = []
        seen: set[str] = set()
        if not isinstance(items, list) or not items or len(items) > 500:
            raise ValueError("Выберите от 1 до 500 позиций")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Некорректная позиция заказа")
            supplier, code = str(item.get("supplier", "")), str(item.get("code", ""))
            row = available.get((supplier, code))
            if not row or row["status"] != "ready":
                raise ValueError(f"Нет подтверждённого остатка для {code}")
            amount = item.get("quantity")
            if isinstance(amount, bool) or not isinstance(amount, int):
                raise ValueError(f"Некорректное количество для {code}") from None
            if amount <= 0 or amount > 10_000_000 or amount % row["moq"] != 0:
                raise ValueError(f"Количество {code} должно быть положительным и кратным {row['moq']}")
            key = f"{supplier}\u241f{code}"
            if key in seen:
                raise ValueError(f"Позиция {code} выбрана повторно")
            seen.add(key)
            prepared.append((key, {
                "supplier": supplier, "code": code, "name": row["name"], "quantity": amount,
                "recommended_quantity": row["quantity"], "reason": row["reason"],
                "as_of": self.as_of.isoformat(), "coverage_days": days,
                "approved_at": datetime.now(timezone.utc).isoformat(),
            }))
        with self.lock:
            events = []
            for key, item in prepared:
                previous = self.approved.get(key)
                self.approved[key] = item
                events.append({"event": "approval", "at": item["approved_at"],
                               "supplier": item["supplier"], "code": item["code"],
                               "recommended_quantity": item["recommended_quantity"],
                               "approved_quantity": item["quantity"],
                               "previous_quantity": previous["quantity"] if previous else None,
                               "coverage_days": days})
            self.storage.parent.mkdir(parents=True, exist_ok=True)
            temp = self.storage.with_suffix(".tmp")
            temp.write_text(json.dumps(self.approved, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.storage)
            self._append_audit(events)
        return len(prepared)

    def _append_audit(self, events: list[dict]) -> None:
        with self.audit_storage.open("a", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def audit_events(self, limit: int = 30) -> list[dict]:
        if not self.audit_storage.exists():
            return []
        lines = self.audit_storage.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in reversed(lines[-limit:])]

    def supplier_summary(self, days: int) -> list[dict]:
        result = {supplier: {"supplier": supplier, "recommendations": 0, "recommended_units": 0,
                             "approved_positions": 0, "approved_units": 0}
                  for supplier in sorted({product.supplier for product in self.products})}
        for row in self.rows(days):
            if (row.get("quantity") or 0) > 0:
                result[row["supplier"]]["recommendations"] += 1
                result[row["supplier"]]["recommended_units"] += row["quantity"]
        for item in self.approved.values():
            if item["supplier"] in result:
                result[item["supplier"]]["approved_positions"] += 1
                result[item["supplier"]]["approved_units"] += item["quantity"]
        return list(result.values())

    def set_stock(self, supplier: str, code: str, amount: object) -> None:
        product = self.by_key.get((supplier, code))
        if product is None:
            raise ValueError("Артикул не найден")
        if isinstance(amount, bool):
            raise ValueError("Некорректный остаток")
        try:
            number = float(amount)
        except (ValueError, TypeError):
            raise ValueError("Некорректный остаток") from None
        if not math.isfinite(number) or number < 0 or number > 100_000_000:
            raise ValueError("Остаток должен быть неотрицательным числом")
        with self.lock:
            previous = product.free_stock if product.stock_as_of == self.as_of else None
            product.free_stock = number
            product.stock_as_of = self.as_of
            if "Остаток внесён вручную" not in product.warnings:
                product.warnings.append("Остаток внесён вручную")
            key = f"{supplier}\u241f{code}"
            stocks = json.loads(self.stock_storage.read_text(encoding="utf-8")) if self.stock_storage.exists() else {}
            stocks[key] = number
            self.stock_storage.parent.mkdir(parents=True, exist_ok=True)
            temp = self.stock_storage.with_suffix(".tmp")
            temp.write_text(json.dumps(stocks, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.stock_storage)
            self.approved.pop(key, None)
            if self.storage.exists():
                self.storage.write_text(json.dumps(self.approved, ensure_ascii=False, indent=2), encoding="utf-8")
            self.cache.clear()
            self._append_audit([{"event": "stock", "at": datetime.now(timezone.utc).isoformat(),
                                 "supplier": supplier, "code": code,
                                 "previous_stock": previous, "new_stock": number}])

    def export_csv(self, supplier: str | None = None) -> bytes:
        if self.report.get("mode") == "partner":
            raise ValueError("Экспорт заказа по данным партнёра заблокирован до подтверждения параметров закупки")
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Поставщик", "Код 1С", "Наименование", "Количество", "Рекомендация", "Дата среза", "Горизонт, дни", "Обоснование"])
        for item in sorted(self.approved.values(), key=lambda x: (x["supplier"], x["code"])):
            if supplier and item["supplier"] != supplier:
                continue
            writer.writerow([_csv_text(item.get(key)) for key in (
                "supplier", "code", "name", "quantity", "recommended_quantity", "as_of", "coverage_days", "reason"
            )])
        return ("\ufeff" + output.getvalue()).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    state: AppState

    def _send(self, body: bytes, mime: str, status: int = 200, disposition: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200):
        self._send(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self):
        path = urlsplit(self.path)
        pages = {"/": "index.html", "/recommendations.html": "recommendations.html",
                 "/suppliers.html": "suppliers.html", "/audit.html": "audit.html",
                 "/settings.html": "settings.html", "/app.js": "app.js",
                 "/i18n.js": "i18n.js", "/style.css": "style.css"}
        if path.path in pages:
            filename = pages[path.path]
            mime = "text/html; charset=utf-8" if filename.endswith(".html") else "text/javascript; charset=utf-8" if filename.endswith(".js") else "text/css; charset=utf-8"
            self._send((PUBLIC / filename).read_bytes(), mime)
            return
        if path.path == "/api/meta":
            suppliers = sorted({p.supplier for p in self.state.products})
            categories = sorted({p.category for p in self.state.products if p.category})
            self._json({**self.state.report, "suppliers": suppliers, "categories": categories,
                        "approved_count": len(self.state.approved)})
            return
        if path.path == "/api/recommendations":
            query = parse_qs(path.query)
            try:
                days = int(query.get("days", ["30"])[0])
                settings = ForecastSettings(self.state.as_of, days)
                limit = min(250, max(1, int(query.get("limit", ["100"])[0])))
                offset = max(0, int(query.get("offset", ["0"])[0]))
            except (ValueError, TypeError) as exc:
                self._json({"error": str(exc)}, 400)
                return
            rows = self.state.rows(settings.coverage_days)
            supplier = query.get("supplier", [""])[0]
            category = query.get("category", [""])[0]
            search = query.get("q", [""])[0].strip().casefold()
            only_orders = query.get("orders", ["1"])[0] == "1"
            filtered = [r for r in rows if (not supplier or r["supplier"] == supplier)
                        and (not category or r.get("category") == category)
                        and (not search or search in r["code"].casefold() or search in r["name"].casefold())
                        and (not only_orders or (r.get("quantity") or 0) > 0)]
            self._json({
                "rows": filtered[offset:offset + limit], "total": len(filtered), "offset": offset,
                "suppliers": self.state.supplier_summary(settings.coverage_days),
                "summary": {"products": len(rows), "ready": sum(r["status"] == "ready" for r in rows),
                            "orders": sum((r.get("quantity") or 0) > 0 for r in rows),
                            "needs_stock": sum(r["status"] == "needs_stock" for r in rows),
                            "insufficient_history": sum(r["status"] == "insufficient_history" for r in rows)},
            })
            return
        if path.path == "/api/approved":
            self._json({"items": list(self.state.approved.values())})
            return
        if path.path == "/api/audit":
            self._json({"events": self.state.audit_events()})
            return
        if path.path == "/api/synthetic-sales.csv":
            if self.state.report.get("mode") != "synthetic":
                self._json({"error": "Доступно только для синтетического набора"}, 404)
                return
            output = io.StringIO()
            writer = csv.writer(output, delimiter=";")
            writer.writerow(["Поставщик", "Артикул", "Месяц", "Документ", "ID клиента", "Количество"])
            for product in self.state.products:
                for sale in product.sales:
                    writer.writerow([_csv_text(value) for value in (
                        product.supplier, product.code, sale.month, sale.document,
                        sale.customer_id, sale.quantity
                    )])
            self._send(("\ufeff" + output.getvalue()).encode("utf-8"), "text/csv; charset=utf-8",
                       disposition='attachment; filename="synthetic_customer_sales.csv"')
            return
        if path.path == "/api/export.csv":
            supplier = parse_qs(path.query).get("supplier", [None])[0]
            if supplier and supplier not in {p.supplier for p in self.state.products}:
                self._json({"error": "Поставщик не найден"}, 404)
                return
            if not any(not supplier or item["supplier"] == supplier for item in self.state.approved.values()):
                self._json({"error": "Нет утверждённых позиций"}, 400)
                return
            filename = "supplier_orders.csv" if not supplier else f"supplier_order_{hashlib.sha256(supplier.encode()).hexdigest()[:8]}.csv"
            self._send(self.state.export_csv(supplier), "text/csv; charset=utf-8",
                       disposition=f'attachment; filename="{filename}"')
            return
        self._json({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ("/api/approve", "/api/stock"):
            self._json({"error": "Not found"}, 404)
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._json({"error": "Expected application/json"}, 415)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 100_000:
                raise ValueError("Размер запроса превышен")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("Ожидается объект JSON")
            if path == "/api/stock":
                self.state.set_stock(str(body.get("supplier", "")), str(body.get("code", "")), body.get("quantity"))
                self._json({"saved": True, "approved_count": len(self.state.approved)})
                return
            days = body.get("days", 30)
            if isinstance(days, bool) or not isinstance(days, int):
                raise ValueError("Некорректный горизонт расчёта")
            ForecastSettings(self.state.as_of, days)
            count = self.state.approve(body.get("items", []), days)
            self._json({"approved": count, "approved_count": len(self.state.approved)})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)

    def log_message(self, format: str, *args):
        pass


def serve(products: list[Product], report: dict, port: int = 8765, storage: Path | None = None):
    dataset_id = report.get("dataset_id") or hashlib.sha256(
        json.dumps([report.get("mode"), report["archives"], report["as_of"], report["products"]], ensure_ascii=False).encode()
    ).hexdigest()[:12]
    state = AppState(products, report, storage or ROOT / ".local" / f"{dataset_id}-orders.json")
    handler = type("KazAIHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Kaz-AI: http://127.0.0.1:{httpd.server_port}/")
    print(f"Source: {', '.join(report['archives'])}; products: {report['products']}; as of: {report['as_of']}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
