"""Typed evidence for purchase terms used by replenishment scenarios.

The calculation may use assumptions for exploration, but every value keeps its
origin and verification state.  Only confirmed values or explicit user input
are considered suitable for a production order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class ParameterStatus(str, Enum):
    CONFIRMED = "confirmed"
    USER_INPUT = "user_input"
    ASSUMPTION = "assumption"
    MISSING = "missing"


class ParameterSource(str, Enum):
    PARTNER_EXPORT = "partner_export"
    USER_INPUT = "user_input"
    DEMO_ASSUMPTION = "demo_assumption"
    DERIVED = "derived"
    LEGACY_FIELD = "legacy_field"
    MISSING = "missing"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParameterValue:
    value: float | int | None
    status: ParameterStatus
    source: ParameterSource
    confidence: Confidence
    unit: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.status == ParameterStatus.MISSING and self.value is not None:
            raise ValueError("missing parameter cannot have a value")
        if self.status != ParameterStatus.MISSING and self.value is None:
            raise ValueError("available parameter must have a value")

    @property
    def trusted_for_order(self) -> bool:
        return self.status in {ParameterStatus.CONFIRMED, ParameterStatus.USER_INPUT}

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "status": self.status.value,
            "source": self.source.value,
            "confidence": self.confidence.value,
            "unit": self.unit,
            "note": self.note,
        }


@dataclass
class ParameterSet:
    values: dict[str, ParameterValue] = field(default_factory=dict)

    def set(self, name: str, value: ParameterValue) -> None:
        if not name:
            raise ValueError("parameter name is required")
        self.values[name] = value

    def get(self, name: str) -> ParameterValue | None:
        return self.values.get(name)

    def trace(self, names: Iterable[str]) -> dict[str, dict]:
        return {name: self.values[name].as_dict() for name in names if name in self.values}

    def readiness(self, required: Iterable[str]) -> dict:
        required_names = list(required)
        missing = [
            name for name in required_names
            if name not in self.values or self.values[name].status == ParameterStatus.MISSING
        ]
        unverified = [
            name for name in required_names
            if name in self.values
            and self.values[name].status != ParameterStatus.MISSING
            and not self.values[name].trusted_for_order
        ]
        status = "blocked" if missing else "scenario" if unverified else "confirmed"
        return {
            "status": status,
            "order_ready": status == "confirmed",
            "missing": missing,
            "unverified": unverified,
        }


def confirmed_parameter(value: float | int, source: ParameterSource, unit: str = "", note: str = "") -> ParameterValue:
    return ParameterValue(value, ParameterStatus.CONFIRMED, source, Confidence.HIGH, unit, note)


def user_parameter(value: float | int, unit: str = "", note: str = "") -> ParameterValue:
    return ParameterValue(value, ParameterStatus.USER_INPUT, ParameterSource.USER_INPUT, Confidence.HIGH, unit, note)


def scenario_parameter(value: float | int, unit: str = "", note: str = "") -> ParameterValue:
    return ParameterValue(value, ParameterStatus.ASSUMPTION, ParameterSource.DEMO_ASSUMPTION, Confidence.LOW, unit, note)


def missing_parameter(unit: str = "", note: str = "") -> ParameterValue:
    return ParameterValue(None, ParameterStatus.MISSING, ParameterSource.MISSING, Confidence.UNKNOWN, unit, note)
