from __future__ import annotations

import shutil
from pathlib import Path


SITE = Path(__file__).resolve().parents[1]
SOURCE = SITE / "dist"
TARGET = SITE.parent / "public_site" / "dist"


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "data").mkdir(parents=True, exist_ok=True)
    html = (SOURCE / "index.html").read_text(encoding="utf-8")
    html = html.replace("<body>", '<body data-public-demo="true">', 1)
    html = html.replace("Supply Control · Электрокомплект", "KAZ-AI Supply Control · Безопасное демо")
    html = html.replace("Демонстрационный сервис расчёта заказов поставщикам для ТОО Электрокомплект.", "Безопасное синтетическое демо рекомендаций по пополнению склада.")
    (TARGET / "index.html").write_text(html, encoding="utf-8")
    shutil.copyfile(SOURCE / "app.js", TARGET / "app.js")
    shutil.copyfile(SOURCE / "styles.css", TARGET / "styles.css")
    shutil.copyfile(SOURCE / "data/synthetic.json", TARGET / "data/synthetic.json")
    partner = TARGET / "data/recommendations.json"
    if partner.exists():
        partner.unlink()
    print(f"Public demo prepared at {TARGET}")


if __name__ == "__main__":
    main()
