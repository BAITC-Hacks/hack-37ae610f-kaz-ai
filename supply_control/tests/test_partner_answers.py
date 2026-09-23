from __future__ import annotations

import json
import unittest
from pathlib import Path

from replenishment.partner_answers import (
    PartnerAnswerError,
    apply_answers_to_scenario,
    load_questionnaire,
    parse_answer_rows,
)


ROOT = Path(__file__).resolve().parents[1]
QUESTIONNAIRE = ROOT / "config/partner_questionnaire.json"
BASE_CONFIG = ROOT / "config/demo_systeme_assumptions.json"


def valid_answer(question: dict) -> object:
    answer_type = question["answer_type"]
    if answer_type in {"choice", "boolean"}:
        return question["allowed_values"][0]
    if answer_type == "integer":
        return int(question.get("min", 0))
    if answer_type == "number":
        return float(question.get("min", 0))
    return "Подтверждено партнером"


class PartnerAnswersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.questionnaire = load_questionnaire(QUESTIONNAIRE)

    def test_complete_valid_answers_confirm_core_parameters(self) -> None:
        rows = [
            {
                "ID": question["id"],
                "Ответ партнера": valid_answer(question),
                "Комментарий или источник партнера": "Регламент закупок",
            }
            for question in self.questionnaire["questions"]
        ]
        payload = parse_answer_rows(rows, self.questionnaire)
        self.assertTrue(payload["complete"])
        self.assertTrue(payload["core_parameters_confirmed"])
        self.assertEqual(payload["answered_count"], len(rows))

    def test_invalid_numeric_answer_is_rejected(self) -> None:
        with self.assertRaises(PartnerAnswerError):
            parse_answer_rows(
                [
                    {
                        "ID": "Q04",
                        "Ответ партнера": 999,
                        "Комментарий или источник партнера": "",
                    }
                ],
                self.questionnaire,
            )

    def test_confirmed_values_override_only_matching_scenario_fields(self) -> None:
        base = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
        answers = {
            "answered_count": 3,
            "question_count": 21,
            "complete": False,
            "core_parameters_confirmed": False,
            "answers": {
                "lead_time_days": {"value": 45},
                "safety_stock_category_1": {"value": 18},
                "apply_growth_coefficient": {"value": False},
            },
        }
        effective = apply_answers_to_scenario(base, answers)
        self.assertEqual(effective["lead_time_days"], 45)
        self.assertEqual(effective["review_period_days"], 30)
        self.assertEqual(effective["category_safety_stock_days"]["1"], 18)
        self.assertFalse(effective["apply_growth_coefficient"])


if __name__ == "__main__":
    unittest.main()

