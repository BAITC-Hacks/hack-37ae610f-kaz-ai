from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class Transaction:
    transaction_date: date
    document_id: str
    sku: str
    quantity: float
    customer_id: str | None = None


@dataclass(frozen=True)
class CleanedTransactions:
    sales: tuple[Transaction, ...]
    returns: tuple[Transaction, ...]
    invalid_rows: tuple[int, ...]
    dropped_summary_rows: tuple[int, ...]


SKU_KEYS = ("sku", "Код 1с", "Код", "Номенклатура.Код")
DATE_KEYS = ("date", "Дата", "Период")
DOCUMENT_KEYS = ("document", "document_id", "Документ", "Регистратор")
QUANTITY_KEYS = ("quantity", "Количество", "Кол-во")
CUSTOMER_KEYS = ("customer_id", "Клиент ID", "Контрагент ID")
NAME_KEYS = ("name", "Наименование", "Номенклатура")
SUMMARY_MARKERS = ("итого", "всего", "total")


def normalize_sku(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text or None


def _pick(row: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _is_summary(row: Mapping[str, Any], sku: str | None) -> bool:
    candidates = [sku, normalize_sku(_pick(row, NAME_KEYS))]
    return any(
        value and any(value.casefold().startswith(marker) for marker in SUMMARY_MARKERS)
        for value in candidates
    )


def clean_transaction_rows(rows: Iterable[Mapping[str, Any]]) -> CleanedTransactions:
    sales: list[Transaction] = []
    returns: list[Transaction] = []
    invalid: list[int] = []
    summary: list[int] = []

    for row_number, row in enumerate(rows, start=1):
        sku = normalize_sku(_pick(row, SKU_KEYS))
        if _is_summary(row, sku):
            summary.append(row_number)
            continue

        transaction_date = _parse_date(_pick(row, DATE_KEYS))
        document = normalize_sku(_pick(row, DOCUMENT_KEYS))
        try:
            quantity = float(_pick(row, QUANTITY_KEYS))
        except (TypeError, ValueError):
            quantity = 0.0

        if not sku or not transaction_date or not document or quantity == 0.0:
            invalid.append(row_number)
            continue

        customer = normalize_sku(_pick(row, CUSTOMER_KEYS))
        transaction = Transaction(
            transaction_date=transaction_date,
            document_id=document,
            sku=sku,
            quantity=abs(quantity),
            customer_id=customer,
        )
        if quantity > 0:
            sales.append(transaction)
        else:
            returns.append(transaction)

    return CleanedTransactions(
        sales=tuple(sales),
        returns=tuple(returns),
        invalid_rows=tuple(invalid),
        dropped_summary_rows=tuple(summary),
    )

