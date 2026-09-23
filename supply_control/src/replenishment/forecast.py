from __future__ import annotations

import calendar
import math
import statistics
from datetime import date
from typing import Mapping, Sequence

from .models import DemandClass, ForecastResult, MonthlyDemand


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _prepare_history(
    history: Sequence[MonthlyDemand], as_of: date
) -> tuple[list[MonthlyDemand], list[str]]:
    current_month = _month_start(as_of)
    complete = sorted(
        (item for item in history if _month_start(item.period) < current_month),
        key=lambda item: item.period,
    )
    months = [_month_start(item.period) for item in complete]
    if len(months) != len(set(months)):
        raise ValueError("Duplicate monthly demand periods are not allowed")
    reasons: list[str] = []
    if any(item.quantity is None for item in complete):
        reasons.append("sales_history_gaps")
    if any(item.quantity is not None and item.quantity < 0 for item in complete):
        reasons.append("negative_monthly_values_ignored")
    return complete, reasons


def _adjust_for_stockouts(
    history: Sequence[MonthlyDemand],
) -> tuple[list[float], float, list[str]]:
    adjusted: list[float] = []
    raw_total = 0.0
    reasons: list[str] = []
    missing_stockout_data = False
    invalid_stockout_data = False

    for item in history:
        quantity = max(0.0, float(item.quantity or 0.0))
        raw_total += quantity
        days_in_month = calendar.monthrange(item.period.year, item.period.month)[1]
        if item.stockout_days is None:
            missing_stockout_data = True
            adjusted.append(quantity)
        elif not 0 <= item.stockout_days < days_in_month:
            invalid_stockout_data = True
            adjusted.append(quantity)
        elif item.stockout_days == 0:
            adjusted.append(quantity)
        else:
            available_days = days_in_month - item.stockout_days
            adjusted.append(quantity * days_in_month / available_days)

    if missing_stockout_data:
        reasons.append("stockout_data_missing")
    if invalid_stockout_data:
        reasons.append("invalid_stockout_days")
    return adjusted, sum(adjusted) - raw_total, reasons


def classify_demand(values: Sequence[float]) -> DemandClass:
    positive_positions = [idx for idx, value in enumerate(values) if value > 0]
    positives = [values[idx] for idx in positive_positions]
    if not positives:
        return DemandClass.NO_DEMAND
    adi = len(values) / len(positives)
    if len(positives) == 1 or statistics.mean(positives) == 0:
        cv2 = 0.0
    else:
        cv2 = (statistics.stdev(positives) / statistics.mean(positives)) ** 2
    if adi < 1.32 and cv2 < 0.49:
        return DemandClass.SMOOTH
    if adi < 1.32 and cv2 >= 0.49:
        return DemandClass.ERRATIC
    if adi >= 1.32 and cv2 < 0.49:
        return DemandClass.INTERMITTENT
    return DemandClass.LUMPY


def _weighted_three(values: Sequence[float]) -> float:
    tail = list(values[-3:])
    weights = [0.2, 0.3, 0.5][-len(tail) :]
    total_weight = sum(weights)
    return sum(value * weight for value, weight in zip(tail, weights)) / total_weight


def _forecast_at(model: str, values: Sequence[float]) -> float:
    if not values:
        return 0.0
    if model == "last_complete_month":
        return float(values[-1])
    if model == "weighted_3_months":
        return _weighted_three(values)
    if model == "seasonal_naive":
        return float(values[-12]) if len(values) >= 12 else float(values[-1])
    raise KeyError(model)


def _model_error(model: str, values: Sequence[float], validation_months: int) -> float:
    start = max(1, len(values) - validation_months)
    indices = [idx for idx in range(start, len(values)) if model != "seasonal_naive" or idx >= 12]
    if not indices:
        return math.inf
    actual = [values[idx] for idx in indices]
    predicted = [_forecast_at(model, values[:idx]) for idx in indices]
    absolute_error = sum(abs(forecast - fact) for forecast, fact in zip(predicted, actual))
    actual_total = sum(actual)
    return absolute_error / actual_total if actual_total > 0 else absolute_error / len(actual)


def _select_model(
    values: Sequence[float],
    *,
    allow_seasonal_naive: bool,
    minimum_improvement: float,
) -> str:
    champion = "last_complete_month"
    champion_error = _model_error(champion, values, validation_months=6)
    candidates = ["weighted_3_months"]
    if allow_seasonal_naive and len(values) >= 18:
        candidates.append("seasonal_naive")
    selected = champion
    selected_error = champion_error
    for candidate in candidates:
        candidate_error = _model_error(candidate, values, validation_months=6)
        if candidate_error <= champion_error * (1.0 - minimum_improvement):
            if candidate_error < selected_error:
                selected = candidate
                selected_error = candidate_error
    return selected


def forecast_monthly(
    history: Sequence[MonthlyDemand],
    *,
    as_of: date,
    growth_multiplier: float = 1.0,
    category_multiplier: float = 1.0,
    seasonality_indices: Mapping[int, float] | None = None,
    minimum_challenger_improvement: float = 0.05,
    allow_challengers: bool = False,
) -> ForecastResult:
    """Forecast one monthly demand rate using only complete calendar months."""

    if growth_multiplier < 0 or category_multiplier < 0:
        raise ValueError("Growth and category multipliers must be non-negative")
    complete, reasons = _prepare_history(history, as_of)
    values, stockout_uplift, stockout_reasons = _adjust_for_stockouts(complete)
    reasons.extend(stockout_reasons)
    demand_class = classify_demand(values)

    if len(values) < 6:
        reasons.append("insufficient_history")
    if demand_class in (DemandClass.INTERMITTENT, DemandClass.LUMPY):
        reasons.append(f"{demand_class.value}_demand")

    has_explicit_seasonality = bool(seasonality_indices and as_of.month in seasonality_indices)
    model = "last_complete_month"
    if allow_challengers:
        model = _select_model(
            values,
            allow_seasonal_naive=not has_explicit_seasonality,
            minimum_improvement=minimum_challenger_improvement,
        )
    base = _forecast_at(model, values)
    seasonality_factor = (
        float(seasonality_indices[as_of.month]) if has_explicit_seasonality else 1.0
    )
    monthly_units = max(
        0.0,
        base * seasonality_factor * growth_multiplier * category_multiplier,
    )
    return ForecastResult(
        base_monthly_units=base,
        monthly_units=monthly_units,
        model=model,
        demand_class=demand_class,
        history_months=len(values),
        seasonality_factor=seasonality_factor,
        growth_multiplier=growth_multiplier,
        category_multiplier=category_multiplier,
        stockout_uplift_units=stockout_uplift,
        review_reasons=tuple(dict.fromkeys(reasons)),
    )
