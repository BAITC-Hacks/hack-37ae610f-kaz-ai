from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from typing import Iterable

from .cleaning import Transaction


@dataclass(frozen=True)
class OutlierDecision:
    transaction: Transaction
    is_outlier: bool
    reason: str | None
    median_document_quantity: float
    ratio_to_median: float
    robust_z_score: float | None


def aggregate_documents(transactions: Iterable[Transaction]) -> tuple[Transaction, ...]:
    grouped: dict[tuple[str, str, object, str | None], Transaction] = {}
    for transaction in transactions:
        key = (
            transaction.sku,
            transaction.document_id,
            transaction.transaction_date,
            transaction.customer_id,
        )
        existing = grouped.get(key)
        grouped[key] = (
            transaction
            if existing is None
            else replace(existing, quantity=existing.quantity + transaction.quantity)
        )
    return tuple(grouped.values())


def detect_one_off_orders(
    transactions: Iterable[Transaction],
    *,
    min_history_documents: int = 5,
    ratio_threshold: float = 8.0,
    robust_z_threshold: float = 6.0,
) -> tuple[OutlierDecision, ...]:
    """Flag exceptional document-level sales without deleting source records."""

    documents = aggregate_documents(transactions)
    by_sku: dict[str, list[Transaction]] = {}
    for transaction in documents:
        by_sku.setdefault(transaction.sku, []).append(transaction)

    decisions: list[OutlierDecision] = []
    for sku, sku_documents in by_sku.items():
        quantities = [item.quantity for item in sku_documents if item.quantity > 0]
        median = statistics.median(quantities) if quantities else 0.0
        deviations = [abs(value - median) for value in quantities]
        mad = statistics.median(deviations) if deviations else 0.0

        for transaction in sku_documents:
            ratio = transaction.quantity / median if median > 0 else math.inf
            robust_z = (
                0.6745 * (transaction.quantity - median) / mad if mad > 0 else None
            )
            enough_history = len(quantities) >= min_history_documents
            ratio_hit = median > 0 and ratio >= ratio_threshold
            z_hit = robust_z is not None and robust_z >= robust_z_threshold
            is_outlier = enough_history and ratio_hit and (z_hit or mad == 0)
            reason = None
            if is_outlier:
                reason = "one_off_large_document"
                if transaction.customer_id:
                    reason += ":single_customer"
            decisions.append(
                OutlierDecision(
                    transaction=transaction,
                    is_outlier=is_outlier,
                    reason=reason,
                    median_document_quantity=median,
                    ratio_to_median=ratio,
                    robust_z_score=robust_z,
                )
            )
    return tuple(decisions)


def exclude_flagged_orders(
    decisions: Iterable[OutlierDecision],
) -> tuple[tuple[Transaction, ...], tuple[OutlierDecision, ...]]:
    decisions = tuple(decisions)
    kept = tuple(item.transaction for item in decisions if not item.is_outlier)
    flagged = tuple(item for item in decisions if item.is_outlier)
    return kept, flagged

