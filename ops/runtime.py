"""Shared settings for the cloud refresh job."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "work" / "data"
_NOW = datetime.now(timezone.utc)
# Use the newest RRFS cycle whose scheduled refresh window has opened.
# GitHub can start a scheduled job late, so the event's original cron time
# must not select an older cycle than the one now available.
_DATE_FOR_CYCLE = _NOW.date()
if _NOW.hour >= 21:
    _CYCLE_FROM_CLOCK = "18"
elif _NOW.hour >= 15:
    _CYCLE_FROM_CLOCK = "12"
elif _NOW.hour >= 9:
    _CYCLE_FROM_CLOCK = "06"
elif _NOW.hour >= 3:
    _CYCLE_FROM_CLOCK = "00"
else:
    _CYCLE_FROM_CLOCK = "18"
    _DATE_FOR_CYCLE -= timedelta(days=1)
DATE = os.environ.get("FOUS_DATE") or _DATE_FOR_CYCLE.strftime("%Y%m%d")
CYCLE = (os.environ.get("FOUS_CYCLE") or _CYCLE_FROM_CLOCK).zfill(2)
CASE = DATA / "model" / "cases" / f"{DATE}_{CYCLE}Z_rrfs_operational"
COMPARISONS = DATA / "comparisons"
OFFICIAL = DATA / "official" / f"FOUS61_{DATE}_{CYCLE}Z_decoded.csv"
RRFS_BASE = f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/rrfs/prod/rrfs.{DATE}/{CYCLE}"

with (ROOT / "site" / "data" / "stations.json").open(encoding="utf-8") as source:
    STATION_ROWS = json.load(source)["stations"]
STATIONS = {row["code"]: (float(row["latitude"]), float(row["longitude"])) for row in STATION_ROWS}
FOUS61_CODES = ("ALB", "BTV", "BOS", "LGA", "PHL", "IPT")
FOUS61_STATIONS = {code: STATIONS[code] for code in FOUS61_CODES if code in STATIONS}
LEADS = tuple(range(0, 85, 6))
VVV_LEADS = tuple(range(0, 61, 6))


def cycle_label() -> str:
    dt = datetime.strptime(f"{DATE}{CYCLE}", "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return dt.strftime("%HZ %b %d, %Y")
