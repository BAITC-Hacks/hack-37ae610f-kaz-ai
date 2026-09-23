from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any


SITE = Path(__file__).resolve().parents[1]
SOURCE = SITE / "dist"
SITES_TARGET = SITE.parent / "public_site" / "dist"
TARGET = SITES_TARGET if (SITES_TARGET.parent / ".openai/hosting.json").exists() else SITE / "public-dist"


SENSITIVE_KEYS = {
    "customer_id",
    "customer_ids",
    "client_id",
    "client_ids",
    "excluded_customers",
    "raw_history",
    "sales_history",
    "transactions",
    "operations",
    "documents",
}


def _remove_sensitive_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _remove_sensitive_fields(item)
            for key, item in value.items()
            if key.lower() not in SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_remove_sensitive_fields(item) for item in value]
    return value


def public_partner_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return partner-derived recommendations without customers, raw history, or prices."""
    public = _remove_sensitive_fields(deepcopy(payload))
    summary = public["summary"]
    summary.update(
        {
            "mode": "partner",
            "exposure": "public_derived",
            "source_label": "Systeme Electric · публичные производные рекомендации",
            "warning": (
                "Производные рекомендации для хакатона. Клиентские ID, сырая история "
                "продаж и цены не публикуются. Демо-допущения требуют проверки партнёра."
            ),
            "valued_order_lines": 0,
            "total_order_value_demo": None,
        }
    )
    for row in public["recommendations"]:
        row["unit_cost"] = None
        row["order_value_demo"] = None
    public["assumptions"]["notes"] = [
        "Опубликованы только производные рекомендации, а не исходные таблицы партнёра.",
        "Клиентские идентификаторы, сырая история продаж и цены исключены.",
        "Каждая рекомендация требует подтверждения сотрудника.",
    ]
    return public


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "data").mkdir(parents=True, exist_ok=True)
    html = (SOURCE / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        "<body>",
        '<body data-public-demo="expanded" data-default-mode="partner">',
        1,
    )
    html = html.replace(
        "Supply Control · Электрокомплект",
        "KAZ-AI Supply Control · Расширенное демо",
    )
    html = html.replace(
        "Демонстрационный сервис расчёта заказов поставщикам для ТОО Электрокомплект.",
        "Публичное расширенное демо рекомендаций по пополнению склада без клиентских данных, сырой истории и цен.",
    )
    html = html.replace(
        '<option value="partner">Данные партнёра</option>',
        '<option value="partner">Расширенный режим · 497 SKU</option>',
    )
    (TARGET / "index.html").write_text(html, encoding="utf-8")
    shutil.copyfile(SOURCE / "app.js", TARGET / "app.js")
    shutil.copyfile(SOURCE / "styles.css", TARGET / "styles.css")
    if (SOURCE / "downloads").exists():
        shutil.copytree(
            SOURCE / "downloads", TARGET / "downloads", dirs_exist_ok=True
        )
    shutil.copyfile(SOURCE / "data/synthetic.json", TARGET / "data/synthetic.json")
    partner_source = json.loads((SOURCE / "data/recommendations.json").read_text(encoding="utf-8"))
    partner_public = public_partner_payload(partner_source)
    partner_target = TARGET / "data/recommendations.json"
    partner_target.write_text(
        json.dumps(partner_public, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Public expanded demo prepared at {TARGET}: "
        f"{partner_public['summary']['source_skus']} partner-derived SKU"
    )


if __name__ == "__main__":
    main()
