"""Skip a scheduled catch-up only when the RRFS page already has its cycle."""
from __future__ import annotations

import json
import os
from pathlib import Path

from runtime import CYCLE, DATE, ROOT

status_path = ROOT / "site" / "data" / "status.json"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


expected_status_cycle = f"{DATE}T{CYCLE}:00:00Z"
status = read_json(status_path)
already_current = status.get("cycleUtc") == expected_status_cycle

refresh = not already_current
print(f"RRFS cycle to check: {DATE}_{CYCLE}Z")
print("RRFS page already has this cycle; skipping catch-up." if already_current
      else "RRFS page is behind; refreshing independently of the NAM bulletin.")
with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
    output.write(f"refresh={'true' if refresh else 'false'}\n")
