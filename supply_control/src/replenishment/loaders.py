from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from .cleaning import normalize_sku
from .models import InboundShipment, InventoryState, MonthlyDemand


RUSSIAN_MONTHS = {
    "январь": 1,
    "февраль": 2,
    "март": 3,
    "апрель": 4,
    "май": 5,
    "июнь": 6,
    "июль": 7,
    "август": 8,
    "сентябрь": 9,
    "октябрь": 10,
    "ноябрь": 11,
    "декабрь": 12,
}
MONTH_PATTERN = re.compile(r"^(\S+)\s+(20\d{2})\s*г?\.?$", re.IGNORECASE)
DATE_IN_HEADER = re.compile(r"(\d{1,2})\.(\d{1,2})")


@dataclass(frozen=True)
class SourceSkuRecord:
    sku: str
    supplier: str
    supplier_sku: str | None
    name: str
    category: str | None
    unit_cost: float | None
    monthly_demand: tuple[MonthlyDemand, ...]
    inventory: InventoryState
    moq: float | None
    source_growth_rate: float | None
    source_seasonality_coefficient: float | None
    source_free_stock: float | None


def _number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _month_from_header(header: object) -> date | None:
    match = MONTH_PATTERN.match(str(header).strip())
    if not match:
        return None
    month = RUSSIAN_MONTHS.get(match.group(1).casefold())
    return date(int(match.group(2)), month, 1) if month else None


def _shipment_date(header: str, as_of: date) -> date | None:
    match = DATE_IN_HEADER.search(header)
    if not match:
        return None
    day, month = (int(match.group(1)), int(match.group(2)))
    candidate = date(as_of.year, month, day)
    if candidate < as_of and (as_of - candidate).days > 180:
        candidate = date(as_of.year + 1, month, day)
    return candidate


def load_moq(path: str | Path) -> dict[str, float]:
    frame = pd.read_excel(path, sheet_name=0, header=0)
    result: dict[str, float] = {}
    for _, row in frame.iterrows():
        sku = normalize_sku(row.get("Номенклатура.Код"))
        moq = _number(row.get("Кратность"))
        if not sku or moq is None or moq <= 0:
            continue
        if sku in result and not math.isclose(result[sku], moq):
            raise ValueError(f"Conflicting MOQ values for SKU {sku}")
        result[sku] = moq
    return result


def load_systeme_snapshot(
    consolidated_path: str | Path,
    moq_path: str | Path,
    *,
    as_of: date,
    supplier: str = "Systeme Electric",
) -> tuple[SourceSkuRecord, ...]:
    frame = pd.read_excel(consolidated_path, sheet_name="TDSheet", header=1)
    frame = frame[frame["Код 1с"].notna()].copy()
    moq_by_sku = load_moq(moq_path)
    month_columns = [
        (column, month)
        for column in frame.columns
        if (month := _month_from_header(column)) is not None
    ]
    inbound_columns = [
        (column, expected)
        for column in frame.columns
        if "в пути" in str(column).casefold()
        and (expected := _shipment_date(str(column), as_of)) is not None
    ]

    records: list[SourceSkuRecord] = []
    seen_skus: set[str] = set()
    for _, row in frame.iterrows():
        sku = normalize_sku(row["Код 1с"])
        if not sku:
            continue
        if sku in seen_skus:
            raise ValueError(f"Duplicate SKU in consolidated source: {sku}")
        seen_skus.add(sku)

        monthly = tuple(
            MonthlyDemand(
                period=month,
                quantity=_number(row[column]),
                stockout_days=None,
            )
            for column, month in month_columns
        )
        inbound = tuple(
            InboundShipment(
                quantity=quantity,
                expected_date=expected,
                reference=str(column),
            )
            for column, expected in inbound_columns
            if (quantity := _number(row[column])) is not None and quantity > 0
        )
        records.append(
            SourceSkuRecord(
                sku=sku,
                supplier=supplier,
                supplier_sku=normalize_sku(row.get("Артикул поставщика")),
                name=str(row.get("Наименование") or "").strip(),
                category=normalize_sku(row.get("Категория 2026")),
                unit_cost=_number(row.get("СС реал")),
                monthly_demand=monthly,
                inventory=InventoryState(
                    on_hand=_number(row.get("Остаток")) or 0.0,
                    reserved=_number(row.get("Зарезервировано")) or 0.0,
                    inbound=inbound,
                ),
                moq=moq_by_sku.get(sku),
                source_growth_rate=_number(row.get("Кэф. Роста")),
                source_seasonality_coefficient=_number(row.get("Кэф. Сез-ти")),
                source_free_stock=_number(row.get("Свободный остаток")),
            )
        )
    return tuple(records)

