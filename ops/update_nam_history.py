#!/usr/bin/env python3
"""Keep a rolling seven-day summary of verified, same-cycle NAM comparisons."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "site" / "data" / "nam_comparison.json"
HISTORY = ROOT / "site" / "data" / "nam_history.json"


def cycle_date(cycle: str) -> date:
    match = re.match(r"^(\d{4})(\d{2})(\d{2})_", cycle)
    if not match:
        raise ValueError(f"Unrecognized NAM cycle label: {cycle}")
    return date(*(int(part) for part in match.groups()))


def summarize(cycles: list[dict]) -> dict:
    definitions = {
        "T1": ("within1C", "samples", "within 1°C"),
        "T3": ("within1C", "samples", "within 1°C"),
        "T5": ("within1C", "samples", "within 1°C"),
        "R1": ("within10PercentagePoints", "samples", "within 10 percentage points"),
        "R2": ("within10PercentagePoints", "samples", "within 10 percentage points"),
        "R3": ("within10PercentagePoints", "samples", "within 10 percentage points"),
        "DD": ("within20deg", "samples", "within 20°"),
        "FF": ("within4Knots", "samples", "within 4 knots"),
        "VVV": ("within10Codes", "samples", "within 10 codes"),
        "PS": ("exact", "samples", "exact"),
        "HH": ("exact", "samples", "exact"),
        "LI": ("exact", "samples", "exact"),
    }
    totals: dict[str, dict] = {}
    ptt_wet = ptt_within = ptt_dry = ptt_periods = 0
    for record in cycles:
        summaries = record.get("summaries", {})
        for field, (success_key, sample_key, label) in definitions.items():
            score = summaries.get(field, {})
            if success_key not in score or sample_key not in score:
                continue
            total = totals.setdefault(field, {"within": 0, "samples": 0, "label": label})
            total["within"] += int(score[success_key] or 0)
            total["samples"] += int(score[sample_key] or 0)
        ptt = summaries.get("PTT", {})
        ptt_periods += int(ptt.get("periodsCompared", ptt.get("compared", 0)) or 0)
        ptt_dry += int(ptt.get("dryPeriods", ptt.get("dry", 0)) or 0)
        ptt_wet += int(ptt.get("wetPeriods", ptt.get("wet", 0)) or 0)
        ptt_within += int(ptt.get("wetWithin010In", ptt.get("within010in", 0)) or 0)
    if ptt_periods:
        totals["PTT"] = {
            "within": ptt_within,
            "samples": ptt_wet,
            "label": "wet periods within 0.10 in",
            "dryPeriods": ptt_dry,
            "wetPeriods": ptt_wet,
            "periodsCompared": ptt_periods,
        }
    days = sorted({record["date"] for record in cycles})
    return {
        "firstDateUtc": days[0] if days else None,
        "lastDateUtc": days[-1] if days else None,
        "daysRepresented": len(days),
        "sevenDayWindowCaptured": len(days) >= 7,
        "cyclesIncluded": len(cycles),
        "fieldSummaries": totals,
    }


def main() -> None:
    if not REPORT.exists():
        return
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if not (report.get("available") and report.get("comparisonAvailable")
            and report.get("officialBulletinAvailable")):
        print("No matched official NAM FOUS comparison; keeping the saved historical record.")
        return
    cycle = report.get("cycle", "")
    current_date = cycle_date(cycle).isoformat()
    summaries = dict(report.get("summaries", {}))
    summaries.update(report.get("additionalFieldSummaries", {}))
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else {"cycles": []}
    cycles = [entry for entry in history.get("cycles", []) if entry.get("cycle") != cycle]
    cycles.append({
        "cycle": cycle,
        "date": current_date,
        "updatedAt": report.get("updatedAt"),
        "summaries": summaries,
    })
    window_end = max(cycle_date(entry["cycle"]) for entry in cycles)
    window_start = window_end - timedelta(days=6)
    cycles = [entry for entry in cycles if cycle_date(entry["cycle"]) >= window_start]
    cycles.sort(key=lambda entry: entry["cycle"])
    result = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "method": "Same-cycle NAM-only reconstruction compared separately with Albany official FOUS61; no official values are used to build candidates.",
        "humidityNote": "R1 is represented by NAM 2 m RH, R2 by 700 mb RH, and R3 by 500 mb RH. These are single-level estimates compared with FOUS humidity codes and are not layer-for-layer matches.",
        "acceptedTolerances": {
            "T1_T3_T5": "within 1°C",
            "PTT": "within 0.10 in for wet six-hour periods",
            "R1_R2_R3": "within 10 percentage points; single-level estimates",
            "DD": "within 20 degrees",
            "FF": "within 4 knots",
            "VVV": "within 10 codes",
        },
        "summary": summarize(cycles),
        "cycles": cycles,
    }
    HISTORY.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(cycles)} matching NAM cycles across {result['summary']['daysRepresented']} UTC dates.")


if __name__ == "__main__":
    main()
