"""Skip a scheduled catch-up when both public products already cover its cycle."""
from __future__ import annotations

import json
import os
from pathlib import Path

from runtime import CYCLE, DATE, ROOT

status_path = ROOT / "site" / "data" / "status.json"
nam_path = ROOT / "site" / "data" / "nam_comparison.json"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


expected_status_cycle = f"{DATE}T{CYCLE}:00:00Z"
expected_nam_cycle = f"{DATE}_{CYCLE}Z"
status = read_json(status_path)
nam = read_json(nam_path)
already_current = (
    status.get("cycleUtc") == expected_status_cycle
    and nam.get("cycle") == expected_nam_cycle
    and nam.get("comparisonAvailable") is True
    and nam.get("officialBulletinAvailable") is True
)

refresh = not already_current
print(f"Cycle to check: {expected_nam_cycle}")
print("Both products already match this cycle; skipping catch-up." if already_current
      else "At least one product is behind or missing; refreshing.")
with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
    output.write(f"refresh={'true' if refresh else 'false'}\n")
