from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook


ROOT = Path("data/extracted")


RU_MONTHS = {
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "май": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}


def json_safe(value):
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        if math.isnan(float(value)) or math.isinf(float(value)):
            return None
        return float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def find_one(pattern: str) -> Path:
    matches = sorted(ROOT.rglob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one match for {pattern}, got {matches}")
    return matches[0]


def month_from_header(value):
    if not isinstance(value, str):
        return None
    text = value.lower().replace("г.", "").strip()
    year_match = re.search(r"(20\d{2})", text)
    if not year_match:
        return None
    month = None
    for prefix, number in RU_MONTHS.items():
        if text.startswith(prefix):
            month = number
            break
    if month is None:
        return None
    return pd.Timestamp(year=int(year_match.group(1)), month=month, day=1)


def transaction_stats(path: Path):
    raw = pd.read_excel(path, dtype={"Код": "string", "Номер": "string"})
    raw["Дата_parsed"] = pd.to_datetime(raw["Дата"], dayfirst=True, errors="coerce")
    raw["Количество_num"] = pd.to_numeric(raw["Количество"], errors="coerce")
    df = raw[
        raw["Дата_parsed"].notna()
        & raw["Код"].notna()
        & raw["Количество_num"].notna()
    ].copy()
    qty = df["Количество_num"]
    negative = qty < 0
    positive = qty > 0
    exact_dupes = int(df.drop(columns=["Дата_parsed", "Количество_num"]).duplicated().sum())
    abs_qty = qty.abs()
    sku_median = abs_qty.groupby(df["Код"]).transform("median")
    robust_large = (abs_qty >= 5 * sku_median) & (abs_qty >= 10) & sku_median.gt(0)

    top = (
        df.assign(abs_qty=abs_qty)
        .nlargest(8, "abs_qty")[["Дата", "Номер", "Код", "Номенклатура", "Склад", "Количество"]]
        .to_dict("records")
    )

    return df, {
        "path": str(path),
        "source_rows_excluding_header": int(len(raw)),
        "valid_transaction_rows": int(len(df)),
        "date_min": df["Дата_parsed"].min(),
        "date_max": df["Дата_parsed"].max(),
        "excluded_summary_or_invalid_rows": int(len(raw) - len(df)),
        "unique_skus": int(df["Код"].nunique(dropna=True)),
        "unique_documents": int(df["Номер"].nunique(dropna=True)),
        "warehouses": df["Склад"].value_counts(dropna=False).head(20).to_dict(),
        "missing_codes_in_source": int(raw["Код"].isna().sum()),
        "missing_qty_in_source": int(raw["Количество_num"].isna().sum()),
        "positive_rows_sales": int(positive.sum()),
        "negative_rows_returns_or_reversals": int(negative.sum()),
        "zero_rows": int((qty == 0).sum()),
        "gross_sales_units_positive_lines": float(qty[positive].sum()),
        "return_or_reversal_units_absolute": float((-qty[negative]).sum()),
        "net_demand_units": float(qty.sum()),
        "exact_duplicate_rows": exact_dupes,
        "robust_large_line_candidates": int(robust_large.fillna(False).sum()),
        "top_absolute_lines": top,
    }


def load_monthly(path: Path, code_candidates=("Номенклатура.Код", "Код 1с")):
    df = pd.read_excel(path)
    code_col = next(c for c in code_candidates if c in df.columns)
    df = df[df[code_col].notna()].copy()
    df[code_col] = df[code_col].astype(str).str.strip()
    month_cols = [c for c in df.columns if month_from_header(c) is not None]
    values = df[month_cols].apply(pd.to_numeric, errors="coerce")
    return df, code_col, month_cols, values


def monthly_stats(path: Path, kind: str):
    df, code_col, month_cols, values = load_monthly(path)
    last_col = month_cols[-1]
    nonnull = int(values.notna().sum().sum())
    total_cells = int(values.shape[0] * values.shape[1])
    out = {
        "path": str(path),
        "kind": kind,
        "rows_with_code": int(len(df)),
        "unique_skus": int(df[code_col].nunique()),
        "duplicate_code_rows": int(df[code_col].duplicated().sum()),
        "months": int(len(month_cols)),
        "month_first": month_from_header(month_cols[0]),
        "month_last": month_from_header(last_col),
        "nonnull_cells": nonnull,
        "total_month_cells": total_cells,
        "nonnull_rate": nonnull / total_cells if total_cells else None,
        "zero_cells": int((values == 0).sum().sum()),
        "negative_cells": int((values < 0).sum().sum()),
        "last_month_nonnull_skus": int(values[last_col].notna().sum()),
        "last_month_zero_skus": int((values[last_col] == 0).sum()),
    }
    return df, code_col, month_cols, values, out


def moq_stats(path: Path, code_col: str):
    df = pd.read_excel(path, engine="openpyxl")
    df = df[df[code_col].notna()].copy()
    moq_col = "Кратность" if "Кратность" in df.columns else "Мин. разр. к отгр."
    moq = pd.to_numeric(df[moq_col], errors="coerce")
    positive = moq[moq > 0]
    return df, {
        "path": str(path),
        "rows_with_code": int(len(df)),
        "unique_skus": int(df[code_col].astype(str).str.strip().nunique()),
        "moq_column": moq_col,
        "positive_moq": int((moq > 0).sum()),
        "zero_moq": int((moq == 0).sum()),
        "missing_or_invalid_moq": int(moq.isna().sum()),
        "moq_median_positive": float(positive.median()) if not positive.empty else None,
        "moq_p95_positive": float(positive.quantile(0.95)) if not positive.empty else None,
        "moq_max": float(positive.max()) if not positive.empty else None,
    }


def iek_in_transit_stats(path: Path):
    df = pd.read_excel(path)
    df = df[df["Код 1с"].notna()].copy()
    order_cols = list(df.columns[3:])
    vals = df[order_cols].apply(pd.to_numeric, errors="coerce")
    by_order = []
    for col in order_cols:
        s = vals[col]
        by_order.append({
            "order": col,
            "nonzero_skus": int((s.fillna(0) != 0).sum()),
            "units": float(s.fillna(0).sum()),
        })
    return df, {
        "path": str(path),
        "rows_with_code": int(len(df)),
        "unique_skus": int(df["Код 1с"].astype(str).str.strip().nunique()),
        "shipment_columns": int(len(order_cols)),
        "skus_with_any_in_transit": int((vals.fillna(0).abs().sum(axis=1) > 0).sum()),
        "total_units_in_transit": float(vals.fillna(0).sum().sum()),
        "orders": by_order,
    }


def system_consolidated_stats(path: Path):
    df = pd.read_excel(path, sheet_name="TDSheet", header=1)
    df = df[df["Код 1с"].notna()].copy()
    month_cols = [c for c in df.columns if month_from_header(c) is not None]
    numeric_cols = [
        "СС реал", "Остаток ТЗ", "РЦ ЕКТ  Рыскулова", "Розничный склад",
        "Остаток", "Зарезервировано", "Свободный остаток", "Заказ",
        "СЭ в пути 24.09", "   Ср мес за последние 12 мес", "Кэф. Роста",
        "Кэф. Сез-ти",
    ]
    num = {c: pd.to_numeric(df[c], errors="coerce") for c in numeric_cols if c in df.columns}
    month_vals = df[month_cols].apply(pd.to_numeric, errors="coerce")
    category = df["Категория 2026"].astype("string").fillna("missing")
    return df, {
        "path": str(path),
        "rows": int(len(df)),
        "unique_skus": int(df["Код 1с"].astype(str).str.strip().nunique()),
        "duplicate_code_rows": int(df["Код 1с"].astype(str).str.strip().duplicated().sum()),
        "month_first": month_from_header(month_cols[0]),
        "month_last": month_from_header(month_cols[-1]),
        "monthly_negative_cells": int((month_vals < 0).sum().sum()),
        "monthly_zero_cells": int((month_vals == 0).sum().sum()),
        "category_counts": category.value_counts().to_dict(),
        "cost_present": int(num["СС реал"].notna().sum()),
        "cost_zero": int((num["СС реал"] == 0).sum()),
        "free_stock_present": int(num["Свободный остаток"].notna().sum()),
        "free_stock_negative": int((num["Свободный остаток"] < 0).sum()),
        "reserved_nonzero": int((num["Зарезервировано"].fillna(0) != 0).sum()),
        "in_transit_nonzero": int((num["СЭ в пути 24.09"].fillna(0) != 0).sum()),
        "in_transit_units": float(num["СЭ в пути 24.09"].fillna(0).sum()),
        "average_demand_positive": int((num["   Ср мес за последние 12 мес"] > 0).sum()),
        "manual_order_nonblank": int(num["Заказ"].notna().sum()),
        "growth_coeff_present": int(num["Кэф. Роста"].notna().sum()),
        "seasonality_coeff_present": int(num["Кэф. Сез-ти"].notna().sum()),
    }


def code_set(df, col):
    return set(df[col].dropna().astype(str).str.strip())


def join_coverage(source_map):
    sets = {name: code_set(df, col) for name, (df, col) in source_map.items()}
    union = set().union(*sets.values())
    intersection = set.intersection(*sets.values()) if sets else set()
    return {
        "union_skus": len(union),
        "all_sources_intersection_skus": len(intersection),
        "sources": {
            name: {
                "skus": len(values),
                "coverage_of_union": len(values) / len(union) if union else None,
                "missing_vs_union": len(union - values),
            }
            for name, values in sets.items()
        },
        "pairwise_intersections": {
            f"{a}__{b}": len(sets[a] & sets[b])
            for i, a in enumerate(sets)
            for b in list(sets)[i + 1 :]
        },
    }


def transaction_month_reconciliation(tx_df, monthly_df, monthly_code_col, month_cols):
    tx = tx_df.copy()
    tx = tx[tx["Дата_parsed"].notna() & tx["Код"].notna()].copy()
    tx["month"] = tx["Дата_parsed"].dt.to_period("M").dt.to_timestamp()
    tx["net_demand"] = pd.to_numeric(tx["Количество_num"], errors="coerce")
    txg = tx.groupby([tx["Код"].astype(str).str.strip(), "month"], dropna=True)["net_demand"].sum()

    long = monthly_df[[monthly_code_col] + month_cols].melt(
        id_vars=[monthly_code_col], var_name="month_label", value_name="monthly_value"
    )
    long["month"] = long["month_label"].map(month_from_header)
    long["code"] = long[monthly_code_col].astype(str).str.strip()
    long["monthly_value"] = pd.to_numeric(long["monthly_value"], errors="coerce")
    long = long[long["monthly_value"].notna()]
    long["tx_value"] = [txg.get((c, m), np.nan) for c, m in zip(long["code"], long["month"])]
    comparable = long[long["tx_value"].notna()].copy()
    if comparable.empty:
        return {"comparable_cells": 0}
    diff = comparable["monthly_value"] - comparable["tx_value"]
    return {
        "comparable_cells": int(len(comparable)),
        "exact_match_cells": int(np.isclose(diff, 0, atol=1e-9).sum()),
        "exact_match_rate": float(np.isclose(diff, 0, atol=1e-9).mean()),
        "absolute_difference_units": float(diff.abs().sum()),
        "median_absolute_difference": float(diff.abs().median()),
        "cells_difference_gt_1": int((diff.abs() > 1).sum()),
    }


def main():
    paths = {
        "iek_tx": find_one("Динамика продаж_2025-2026.xlsx"),
        "iek_monthly_sales": find_one("Ежемесячные продажи в количественном выражении за последние 2 года.xlsx"),
        "iek_inventory": find_one("Ежемесячные остатки продукции за последние 2 года  ИЭК.xlsx"),
        "iek_moq": find_one("MOQ  ИЭК.xlsx"),
        "iek_transit": find_one("Путь ИЭК 22.09.2026.xlsx"),
        "system_tx": find_one("Динамика продаж_Syseme Electric_2025-2026.xlsx"),
        "system_monthly_sales": find_one("Ежемесячные продажи в кол-м выражении SystemElectric 2024-2026.xlsx"),
        "system_inventory": find_one("Ежемесячные остатки SystemElectric 2024-2026.xlsx"),
        "system_moq": find_one("MOQ SystemElectric.xlsx"),
        "system_consolidated": find_one("Товар в пути_SystemElectric на 22.09.2026.xlsx"),
    }

    iek_tx_df, iek_tx = transaction_stats(paths["iek_tx"])
    sys_tx_df, sys_tx = transaction_stats(paths["system_tx"])

    iek_sales_df, iek_sales_code, iek_sales_months, _, iek_sales = monthly_stats(paths["iek_monthly_sales"], "sales")
    iek_inv_df, iek_inv_code, _, _, iek_inv = monthly_stats(paths["iek_inventory"], "inventory")
    sys_sales_df, sys_sales_code, sys_sales_months, _, sys_sales = monthly_stats(paths["system_monthly_sales"], "sales")
    sys_inv_df, sys_inv_code, _, _, sys_inv = monthly_stats(paths["system_inventory"], "inventory")

    iek_moq_df, iek_moq = moq_stats(paths["iek_moq"], "Код 1с")
    sys_moq_df, sys_moq = moq_stats(paths["system_moq"], "Номенклатура.Код")
    iek_transit_df, iek_transit = iek_in_transit_stats(paths["iek_transit"])
    sys_cons_df, sys_cons = system_consolidated_stats(paths["system_consolidated"])

    report = {
        "IEK": {
            "transactions": iek_tx,
            "monthly_sales": iek_sales,
            "inventory": iek_inv,
            "moq": iek_moq,
            "in_transit": iek_transit,
            "join_coverage": join_coverage({
                "transactions": (iek_tx_df, "Код"),
                "monthly_sales": (iek_sales_df, iek_sales_code),
                "inventory": (iek_inv_df, iek_inv_code),
                "moq": (iek_moq_df, "Код 1с"),
                "in_transit": (iek_transit_df, "Код 1с"),
            }),
            "transaction_month_reconciliation": transaction_month_reconciliation(
                iek_tx_df, iek_sales_df, iek_sales_code, iek_sales_months
            ),
        },
        "SystemElectric": {
            "transactions": sys_tx,
            "monthly_sales": sys_sales,
            "inventory": sys_inv,
            "moq": sys_moq,
            "consolidated": sys_cons,
            "join_coverage": join_coverage({
                "transactions": (sys_tx_df, "Код"),
                "monthly_sales": (sys_sales_df, sys_sales_code),
                "inventory": (sys_inv_df, sys_inv_code),
                "moq": (sys_moq_df, "Номенклатура.Код"),
                "consolidated": (sys_cons_df, "Код 1с"),
            }),
            "transaction_month_reconciliation": transaction_month_reconciliation(
                sys_tx_df, sys_sales_df, sys_sales_code, sys_sales_months
            ),
        },
    }
    output = Path("data/analysis/dataset_metrics.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(json_safe(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "sections": list(report)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
