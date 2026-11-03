"""Skip a catch-up only when the page already has this or a newer RRFS cycle."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from runtime import CYCLE, DATE, ROOT

status_path = ROOT / "site" / "data" / "status.json"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


expected_status_cycle = datetime.strptime(f"{DATE}{CYCLE}", "%Y%m%d%H").replace(tzinfo=timezone.utc)
status = read_json(status_path)
try:
    current_cycle = datetime.strptime(status.get("cycleUtc", ""), "%Y%m%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
except (TypeError, ValueError):
    current_cycle = None

already_current = current_cycle is not None and current_cycle >= expected_status_cycle
refresh = not already_current
print(f"RRFS cycle to check: {DATE}_{CYCLE}Z")
if current_cycle and current_cycle > expected_status_cycle:
    print("The page already has a newer RRFS cycle; skipping this delayed run.")
elif already_current:
    print("The page already has this RRFS cycle; skipping catch-up.")
else:
    print("The RRFS page is behind; refreshing independently of the NAM bulletin.")
with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
    output.write("refresh=" + ("true" if refresh else "false") + "\n")
