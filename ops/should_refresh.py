"""Skip a catch-up only when the page already has this or a newer RRFS cycle."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from runtime import CYCLE, DATE, ROOT

status_path = ROOT / "site" / "data" / "status.json"
woof_status_path = ROOT / "site" / "data" / "woofnam" / "status.json"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def cycle_from_status(status: dict):
    try:
        return datetime.strptime(status.get("cycleUtc", ""), "%Y%m%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


expected_status_cycle = datetime.strptime(f"{DATE}{CYCLE}", "%Y%m%d%H").replace(tzinfo=timezone.utc)
status = read_json(status_path)
current_cycle = cycle_from_status(status)
woof_status = read_json(woof_status_path)
woof_cycle = cycle_from_status(woof_status)

already_current = current_cycle is not None and current_cycle >= expected_status_cycle
refresh = not already_current
woofnam_refresh = woof_cycle is None or woof_cycle < expected_status_cycle
print(f"RRFS cycle to check: {DATE}_{CYCLE}Z")
if current_cycle and current_cycle > expected_status_cycle:
    print("The page already has a newer RRFS cycle; skipping this delayed run.")
elif already_current:
    print("The page already has this RRFS cycle; skipping catch-up.")
else:
    print("The RRFS page is behind; refreshing independently of the NAM bulletin.")
print(f"WOOF-NAM cycle to check: {DATE}_{CYCLE}Z")
print("WOOF-NAM pilot is behind; a separate catch-up may refresh it." if woofnam_refresh
      else "WOOF-NAM pilot already has this or a newer cycle.")
with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
    output.write("refresh=" + ("true" if refresh else "false") + "\n")
    output.write("woofnam_refresh=" + ("true" if woofnam_refresh else "false") + "\n")
