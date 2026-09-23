from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


SOURCE = Path(
    "data/extracted/systeme/Systeme electric/"
    "Товар в пути_SystemElectric на 22.09.2026.xlsx"
)
OUTPUT = Path("data/analysis/forecast_baselines_systeme.json")


MONTHS = [
    "Январь 2024 г.", "Февраль 2024 г.", "Март 2024 г.",
    "Апрель 2024 г.", "Май 2024 г.", "Июнь 2024 г.",
    "Июль 2024 г.", "Август 2024 г.", "Сентябрь 2024 г.",
    "Октябрь 2024 г.", "Ноябрь 2024 г.", "Декабрь 2024 г.",
    "Январь 2025 г.", "Февраль 2025 г.", "Март 2025 г.",
    "Апрель 2025 г.", "Май 2025 г.", "Июнь 2025 г.",
    "Июль 2025 г.", "Август 2025 г.", "Сентябрь 2025 г.",
    "Октябрь 2025 г.", "Ноябрь 2025 г.", "Декабрь 2025 г.",
    "Январь 2026 г.", "Февраль 2026 г.", "Март 2026 г.",
    "Апрель 2026 г.", "Май 2026 г.", "Июнь 2026 г.",
    "Июль 2026 г.", "Август 2026 г.",
]


def clean_number(value):
    if value is None:
        return None
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def forecast(method: str, history: np.ndarray) -> float:
    history = np.asarray(history, dtype=float)
    if history.size == 0:
        return 0.0
    if method == "last_month":
        return float(history[-1])
    if method == "mean_3":
        return float(history[-3:].mean())
    if method == "mean_6":
        return float(history[-6:].mean())
    if method == "weighted_3":
        tail = history[-3:]
        weights = np.array([0.2, 0.3, 0.5])[-len(tail):]
        weights = weights / weights.sum()
        return float((tail * weights).sum())
    if method == "seasonal_naive":
        return float(history[-12]) if len(history) >= 12 else float(history[-6:].mean())
    if method == "croston_sba":
        alpha = 0.1
        nz = np.flatnonzero(history > 0)
        if nz.size == 0:
            return 0.0
        first = int(nz[0])
        level = float(history[first])
        interval = float(first + 1)
        gap = 1.0
        for value in history[first + 1 :]:
            if value > 0:
                level = level + alpha * (float(value) - level)
                interval = interval + alpha * (gap - interval)
                gap = 1.0
            else:
                gap += 1.0
        return max(0.0, (1.0 - alpha / 2.0) * level / max(interval, 1e-9))
    raise KeyError(method)


METHODS = [
    "last_month", "mean_3", "mean_6", "weighted_3",
    "seasonal_naive", "croston_sba",
]


def demand_class(series: np.ndarray):
    nonzero = series[series > 0]
    if nonzero.size == 0:
        return "no_demand", None, None
    adi = len(series) / nonzero.size
    cv2 = float((np.std(nonzero, ddof=1) / np.mean(nonzero)) ** 2) if len(nonzero) > 1 else 0.0
    if adi < 1.32 and cv2 < 0.49:
        label = "smooth"
    elif adi < 1.32 and cv2 >= 0.49:
        label = "erratic"
    elif adi >= 1.32 and cv2 < 0.49:
        label = "intermittent"
    else:
        label = "lumpy"
    return label, adi, cv2


def aggregate_metrics(actual, predicted):
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    denom = actual.sum()
    return {
        "wape": clean_number(np.abs(predicted - actual).sum() / denom) if denom > 0 else None,
        "bias": clean_number((predicted - actual).sum() / denom) if denom > 0 else None,
        "mae": clean_number(np.abs(predicted - actual).mean()) if actual.size else None,
        "actual_units": clean_number(denom),
        "forecast_units": clean_number(predicted.sum()),
        "observations": int(actual.size),
    }


def main():
    df = pd.read_excel(SOURCE, sheet_name="TDSheet", header=1)
    df = df[df["Код 1с"].notna()].copy()
    raw = df[MONTHS].apply(pd.to_numeric, errors="coerce")
    negative_cells = int((raw < 0).sum().sum())
    demand = raw.fillna(0).clip(lower=0).to_numpy(dtype=float)

    profiles = []
    class_counts = Counter()
    active_last_12 = 0
    inactive_last_12 = 0
    new_last_6 = 0
    for idx, row in df.reset_index(drop=True).iterrows():
        series = demand[idx]
        label, adi, cv2 = demand_class(series)
        class_counts[label] += 1
        if series[-12:].sum() > 0:
            active_last_12 += 1
        else:
            inactive_last_12 += 1
        positive_idx = np.flatnonzero(series > 0)
        is_new = bool(positive_idx.size and positive_idx[0] >= len(series) - 6)
        new_last_6 += int(is_new)
        profiles.append({
            "code": str(row["Код 1с"]),
            "name": str(row["Наименование"]),
            "class": label,
            "adi": clean_number(adi),
            "cv2": clean_number(cv2),
            "active_last_12": bool(series[-12:].sum() > 0),
            "new_last_6": is_new,
            "last_12_units": clean_number(series[-12:].sum()),
        })

    evaluation_start = 24  # Jan 2026
    all_results = {}
    per_class = {}
    for method in METHODS:
        actual, predicted = [], []
        grouped = defaultdict(lambda: ([], []))
        for sku_idx, series in enumerate(demand):
            cls = profiles[sku_idx]["class"]
            for t in range(evaluation_start, len(series)):
                actual.append(series[t])
                predicted.append(forecast(method, series[:t]))
                grouped[cls][0].append(series[t])
                grouped[cls][1].append(forecast(method, series[:t]))
        all_results[method] = aggregate_metrics(actual, predicted)
        per_class[method] = {
            cls: aggregate_metrics(vals[0], vals[1]) for cls, vals in grouped.items()
        }

    # Select a method per SKU on Jan-Jun 2026, then test the selection on Jul-Aug 2026.
    validation_idx = range(24, 30)
    holdout_idx = range(30, 32)
    selected_counts = Counter()
    holdout_actual, holdout_predicted = [], []
    holdout_by_method = {method: ([], []) for method in METHODS}
    selected_rows = []
    for sku_idx, series in enumerate(demand):
        errors = {}
        for method in METHODS:
            errors[method] = float(sum(abs(forecast(method, series[:t]) - series[t]) for t in validation_idx))
        selected = min(METHODS, key=lambda m: (errors[m], METHODS.index(m)))
        selected_counts[selected] += 1
        sku_actual = []
        sku_predicted = []
        for t in holdout_idx:
            sku_actual.append(series[t])
            sku_predicted.append(forecast(selected, series[:t]))
            holdout_actual.append(series[t])
            holdout_predicted.append(forecast(selected, series[:t]))
            for method in METHODS:
                holdout_by_method[method][0].append(series[t])
                holdout_by_method[method][1].append(forecast(method, series[:t]))
        selected_rows.append({
            "code": profiles[sku_idx]["code"],
            "class": profiles[sku_idx]["class"],
            "selected_method": selected,
            "validation_absolute_error": clean_number(errors[selected]),
            "holdout_actual_units": clean_number(sum(sku_actual)),
            "holdout_forecast_units": clean_number(sum(sku_predicted)),
        })

    output = {
        "source": str(SOURCE),
        "scope": {
            "skus": int(len(df)),
            "months": len(MONTHS),
            "period": "2024-01 to 2026-08",
            "september_2026_excluded_as_partial": True,
            "negative_month_cells_floored_to_zero": negative_cells,
        },
        "demand_profiles": {
            "class_counts": dict(class_counts),
            "active_last_12": active_last_12,
            "inactive_last_12": inactive_last_12,
            "new_last_6": new_last_6,
        },
        "rolling_one_month_evaluation_jan_aug_2026": all_results,
        "rolling_evaluation_by_class": per_class,
        "per_sku_selection": {
            "validation_period": "2026-01 to 2026-06",
            "holdout_period": "2026-07 to 2026-08",
            "selected_method_counts": dict(selected_counts),
            "holdout_metrics": aggregate_metrics(holdout_actual, holdout_predicted),
            "holdout_metrics_by_single_method": {
                method: aggregate_metrics(values[0], values[1])
                for method, values in holdout_by_method.items()
            },
        },
        "profiles": profiles,
        "selected_rows": selected_rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(OUTPUT),
        "classes": dict(class_counts),
        "selected_methods": dict(selected_counts),
        "holdout": output["per_sku_selection"]["holdout_metrics"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
