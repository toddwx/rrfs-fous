"""Shared settings for the cloud refresh job."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "work" / "data"
_NOW = datetime.now(timezone.utc)
_SCHEDULE_CYCLES = {"15 3 * * *": (3, "00"), "15 9 * * *": (9, "06"),
                    "15 15 * * *": (15, "12"), "15 21 * * *": (21, "18"),
                    "45 3 * * *": (3, "00"), "45 9 * * *": (9, "06"),
                    "45 15 * * *": (15, "12"), "45 21 * * *": (21, "18")}
_SCHEDULE_EVENT = {}
if os.environ.get("GITHUB_EVENT_PATH"):
    try:
        with Path(os.environ["GITHUB_EVENT_PATH"]).open(encoding="utf-8") as event_file:
            _SCHEDULE_EVENT = json.load(event_file)
    except (OSError, json.JSONDecodeError):
        _SCHEDULE_EVENT = {}
_SCHEDULE = _SCHEDULE_EVENT.get("schedule")
_SCHEDULE_HOUR, _SCHEDULE_CYCLE = _SCHEDULE_CYCLES.get(_SCHEDULE, (None, None))
_DATE_FOR_CYCLE = _NOW.date()
if _SCHEDULE_HOUR is not None and _NOW.hour < _SCHEDULE_HOUR:
    _DATE_FOR_CYCLE -= timedelta(days=1)
elif _SCHEDULE == "17,47 * * * *":
    # These frequent runs also act as forecast catch-ups if GitHub drops a
    # main scheduled event. Map each half-hour check to the most recent
    # long-range cycle whose normal refresh window has begun.
    if _NOW.hour >= 21:
        _SCHEDULE_CYCLE = "18"
    elif _NOW.hour >= 15:
        _SCHEDULE_CYCLE = "12"
    elif _NOW.hour >= 9:
        _SCHEDULE_CYCLE = "06"
    elif _NOW.hour >= 3:
        _SCHEDULE_CYCLE = "00"
    else:
        _SCHEDULE_CYCLE = "18"
        _DATE_FOR_CYCLE -= timedelta(days=1)
DATE = os.environ.get("FOUS_DATE") or _DATE_FOR_CYCLE.strftime("%Y%m%d")
CYCLE = (os.environ.get("FOUS_CYCLE") or _SCHEDULE_CYCLE or f"{(_NOW.hour // 6) * 6:02d}").zfill(2)
CASE = DATA / "model" / "cases" / f"{DATE}_{CYCLE}Z_rrfs_parallel"
COMPARISONS = DATA / "comparisons"
OFFICIAL = DATA / "official" / f"FOUS61_{DATE}_{CYCLE}Z_decoded.csv"
RRFS_BASE = f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/rrfs/para/rrfs.{DATE}/{CYCLE}"

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
