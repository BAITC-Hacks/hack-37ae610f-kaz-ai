from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCENARIO_KEYS = {
    "lead_time_days",
    "review_period_days",
    "safety_stock_category_1",
    "safety_stock_category_2",
    "safety_stock_category_3",
    "safety_stock_category_5",
    "safety_stock_category_7",
    "apply_growth_coefficient",
    "apply_seasonality_coefficient",
    "order_budget_kzt",
    "minimum_total_order_kzt",
}

CORE_CONFIRMATION_KEYS = {
    "lead_time_days",
    "review_period_days",
    "safety_stock_category_1",
    "safety_stock_category_2",
    "safety_stock_category_3",
    "safety_stock_category_5",
    "safety_stock_category_7",
    "order_multiple_semantics",
    "inbound_eta_definition",
    "stock_balance_scope",
}


class PartnerAnswerError(ValueError):
    pass


def load_questionnaire(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise PartnerAnswerError("Questionnaire must contain a non-empty questions list")
    ids = [str(item.get("id", "")).strip() for item in questions]
    keys = [str(item.get("key", "")).strip() for item in questions]
    if any(not value for value in ids + keys):
        raise PartnerAnswerError("Every question must have an id and key")
    if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
        raise PartnerAnswerError("Question ids and keys must be unique")
    return payload


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _number(value: object, *, integer: bool) -> int | float:
    if isinstance(value, bool):
        raise PartnerAnswerError("Boolean value is not a number")
    if isinstance(value, str):
        value = value.strip().replace(" ", "").replace(",", ".")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PartnerAnswerError(f"Expected a number, got {value!r}") from exc
    if not math.isfinite(result):
        raise PartnerAnswerError("Number must be finite")
    if integer and not result.is_integer():
        raise PartnerAnswerError(f"Expected a whole number, got {result}")
    return int(result) if integer else result


def normalize_answer(value: object, question: Mapping[str, Any]) -> Any:
    if _is_blank(value):
        return None
    answer_type = question.get("answer_type", "text")
    if answer_type == "text":
        return str(value).strip()
    if answer_type == "integer":
        normalized = _number(value, integer=True)
    elif answer_type == "number":
        normalized = _number(value, integer=False)
    elif answer_type == "boolean":
        text = str(value).strip().casefold()
        if text in {"да", "yes", "true", "1"}:
            normalized = True
        elif text in {"нет", "no", "false", "0"}:
            normalized = False
        else:
            raise PartnerAnswerError(f"Expected Да or Нет, got {value!r}")
    elif answer_type == "choice":
        allowed = [str(item).strip() for item in question.get("allowed_values", [])]
        by_casefold = {item.casefold(): item for item in allowed}
        normalized = by_casefold.get(str(value).strip().casefold())
        if normalized is None:
            raise PartnerAnswerError(
                f"Expected one of {', '.join(allowed)}, got {value!r}"
            )
    else:
        raise PartnerAnswerError(f"Unsupported answer type: {answer_type}")

    if isinstance(normalized, (int, float)) and not isinstance(normalized, bool):
        minimum = question.get("min")
        maximum = question.get("max")
        if minimum is not None and normalized < float(minimum):
            raise PartnerAnswerError(f"Value must be at least {minimum}")
        if maximum is not None and normalized > float(maximum):
            raise PartnerAnswerError(f"Value must be at most {maximum}")
    return normalized


def parse_answer_rows(
    rows: Iterable[Mapping[str, object]], questionnaire: Mapping[str, Any]
) -> dict[str, Any]:
    questions = questionnaire["questions"]
    by_id = {str(item["id"]): item for item in questions}
    answers: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for row_number, row in enumerate(rows, start=2):
        question_id = str(row.get("ID") or "").strip()
        if not question_id:
            continue
        question = by_id.get(question_id)
        if question is None:
            errors.append(f"Строка {row_number}: неизвестный ID {question_id}")
            continue
        raw_answer = row.get("Ответ партнера")
        if _is_blank(raw_answer):
            continue
        try:
            value = normalize_answer(raw_answer, question)
        except PartnerAnswerError as exc:
            errors.append(f"{question_id}: {exc}")
            continue
        answers[str(question["key"])] = {
            "id": question_id,
            "value": value,
            "raw_answer": raw_answer,
            "comment": str(row.get("Комментарий или источник партнера") or "").strip(),
            "confirmed": True,
        }

    if errors:
        raise PartnerAnswerError("\n".join(errors))

    required_keys = {
        str(item["key"]) for item in questions if bool(item.get("required"))
    }
    missing_required = sorted(required_keys - answers.keys())
    core_missing = sorted(CORE_CONFIRMATION_KEYS - answers.keys())
    return {
        "version": 1,
        "partner": questionnaire.get("partner", "ТОО Электрокомплект"),
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "answered_count": len(answers),
        "question_count": len(questions),
        "required_count": len(required_keys),
        "missing_required_keys": missing_required,
        "complete": not missing_required,
        "core_parameters_confirmed": not core_missing,
        "missing_core_keys": core_missing,
        "answers": answers,
    }


def load_partner_answers(path: str | Path | None) -> dict[str, Any] | None:
    if path is None or not Path(path).exists():
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload.get("answers"), dict):
        raise PartnerAnswerError("Partner answers file must contain an answers object")
    return payload


def answer_value(answers: Mapping[str, Any] | None, key: str) -> Any:
    if not answers:
        return None
    item = answers.get("answers", {}).get(key)
    return item.get("value") if isinstance(item, Mapping) else None


def apply_answers_to_scenario(
    base: Mapping[str, Any], answers: Mapping[str, Any] | None
) -> dict[str, Any]:
    result = deepcopy(dict(base))
    if not answers:
        result["partner_confirmation"] = {
            "answered_count": 0,
            "question_count": 0,
            "complete": False,
            "core_parameters_confirmed": False,
        }
        return result

    for key in ("lead_time_days", "review_period_days", "order_budget_kzt", "minimum_total_order_kzt"):
        value = answer_value(answers, key)
        if value is not None:
            result[key] = value

    category = dict(result.get("category_safety_stock_days", {}))
    for category_id in ("1", "2", "3", "5", "7"):
        value = answer_value(answers, f"safety_stock_category_{category_id}")
        if value is not None:
            category[category_id] = value
    result["category_safety_stock_days"] = category

    for key in ("apply_growth_coefficient", "apply_seasonality_coefficient"):
        value = answer_value(answers, key)
        if value is not None:
            result[key] = bool(value)

    result["partner_confirmation"] = {
        "answered_count": int(answers.get("answered_count", 0)),
        "question_count": int(answers.get("question_count", 0)),
        "complete": bool(answers.get("complete")),
        "core_parameters_confirmed": bool(answers.get("core_parameters_confirmed")),
        "missing_required_keys": list(answers.get("missing_required_keys", [])),
        "missing_core_keys": list(answers.get("missing_core_keys", [])),
    }
    return result


def questionnaire_view(
    questionnaire: Mapping[str, Any], answers: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    answer_map = answers.get("answers", {}) if answers else {}
    rows: list[dict[str, Any]] = []
    for question in questionnaire["questions"]:
        partner = answer_map.get(question["key"])
        rows.append(
            {
                "id": question["id"],
                "key": question["key"],
                "section": question["section"],
                "priority": question["priority"],
                "question": question["question"],
                "current_answer": question.get("current_answer"),
                "current_status": question.get("current_status"),
                "evidence": question.get("evidence"),
                "project_impact": question.get("project_impact"),
                "partner_answer": partner.get("value") if partner else None,
                "partner_comment": partner.get("comment") if partner else None,
                "status": "partner_confirmed" if partner else "awaiting_partner",
            }
        )
    return rows

