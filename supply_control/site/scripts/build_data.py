from __future__ import annotations

import json
from pathlib import Path


SITE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = SITE_ROOT.parent
SOURCE = WORKSPACE_ROOT / "outputs/demo/systeme_recommendations_demo.json"
DESTINATION = SITE_ROOT / "dist/data/recommendations.json"


def main() -> None:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    required = {"summary", "assumptions", "recommendations"}
    missing = required.difference(payload)
    if missing:
        raise ValueError(f"Missing required sections: {sorted(missing)}")
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "destination": str(DESTINATION),
                "recommendations": len(payload["recommendations"]),
                "bytes": DESTINATION.stat().st_size,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

