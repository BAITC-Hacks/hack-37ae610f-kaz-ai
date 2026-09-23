from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class DemandClass(StrEnum):
    SMOOTH = "smooth"
    ERRATIC = "erratic"
    INTERMITTENT = "intermittent"
    LUMPY = "lumpy"
    NO_DEMAND = "no_demand"


class RecommendationStatus(StrEnum):
    BLOCKED = "blocked"
    NO_ORDER = "no_order"
    REVIEW_REQUIRED = "review_required"
    READY_FOR_APPROVAL = "ready_for_approval"


@dataclass(frozen=True)
class MonthlyDemand:
    """Demand for a calendar month.

    ``quantity=None`` preserves a missing source cell. Forecasting can use a
    zero placeholder, but must keep the missing-data review flag.
    """

    period: date
    quantity: float | None
    stockout_days: int | None = None


@dataclass(frozen=True)
class InboundShipment:
    quantity: float
    expected_date: date
    reference: str | None = None


@dataclass(frozen=True)
class InventoryState:
    on_hand: float
    reserved: float = 0.0
    inbound: tuple[InboundShipment, ...] = ()

    @property
    def free_stock(self) -> float:
        return self.on_hand - self.reserved


@dataclass(frozen=True)
class SkuPolicy:
    sku: str
    supplier: str | None
    moq: float | None
    lead_time_days: int | None
    review_period_days: int | None
    safety_stock_days: float | None
    category: str | None = None
    category_multiplier: float = 1.0
    growth_multiplier: float = 1.0


@dataclass(frozen=True)
class ForecastResult:
    base_monthly_units: float
    monthly_units: float
    model: str
    demand_class: DemandClass
    history_months: int
    seasonality_factor: float
    growth_multiplier: float
    category_multiplier: float
    stockout_uplift_units: float
    review_reasons: tuple[str, ...] = ()

    @property
    def review_required(self) -> bool:
        return bool(self.review_reasons)


@dataclass(frozen=True)
class PurchaseRecommendation:
    sku: str
    supplier: str | None
    status: RecommendationStatus
    recommended_quantity: float | None
    urgency: str
    forecast_monthly_units: float
    cover_days: float | None
    target_stock: float | None
    free_stock: float
    eligible_inbound: float
    excluded_inbound: float
    raw_need: float | None
    moq: float | None
    projected_stockout_date: date | None
    explanation: str
    reasons: tuple[str, ...] = field(default_factory=tuple)

