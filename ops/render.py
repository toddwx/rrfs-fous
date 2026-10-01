#!/usr/bin/env python3
"""Render an experimental RRFS candidate in the familiar FOUS61 line layout."""

from __future__ import annotations

import csv
from pathlib import Path
from datetime import datetime
from runtime import COMPARISONS, DATA, DATE, CYCLE, STATION_ROWS, STATIONS


SOURCE = COMPARISONS / f"FOUS61_{DATE}_{CYCLE}Z_RRFS_parallel_candidate.csv"
OUT = Path(__file__).resolve().parents[1] / "site" / "data" / "latest.txt"
STATION_CODES = tuple(STATIONS)
STATION_PAIRS = tuple(tuple(STATION_CODES[i:i+2]) for i in range(0, len(STATION_CODES), 2))


def two(value: str, *, cap: int | None = None) -> str:
    number = int(value)
    if cap is not None:
        number = min(number, cap)
    return f"{number:02d}"


def temperature(value: str) -> str:
    """Encode signed Celsius as the familiar two-digit FOUS temperature."""
    number = int(value)
    return f"{number % 100:02d}"


def vvv(value: str) -> str:
    number = int(value)
    magnitude = min(abs(number), 99) if number < 0 else number
    return f"-{magnitude:02d}" if number < 0 else f"{magnitude:03d}"


def initial(row: dict[str, str]) -> str:
    humidity = "".join(two(row[k], cap=99) for k in ("R1", "R2", "R3"))
    return (
        f"{row['station']}//{humidity} {vvv(row['VVV'])}{two(row['LI'])} "
        f"{two(row['PS_code'])}{two(row['DD_proxy_lowest35mb_code'])}{two(row['FF_proxy_lowest35mb_kt'])} "
        f"{two(row['HH_code'])}{temperature(row['T1_layer_mean_C'])}{temperature(row['T3_layer_mean_C'])}{temperature(row['T5_layer_mean_C'])}"
    )


def forecast(row: dict[str, str]) -> str:
    lead = int(row["forecast_hour"])
    return (
        f"{lead:02d}{int(row['PTT_hundredths_in']):03d}"
        f"{''.join(two(row[k], cap=99) for k in ('R1', 'R2', 'R3'))} "
        f"{vvv(row['VVV'])}{two(row['LI'])} "
        f"{two(row['PS_code'])}{two(row['DD_proxy_lowest35mb_code'])}{two(row['FF_proxy_lowest35mb_kt'])} "
        f"{two(row['HH_code'])}{temperature(row['T1_layer_mean_C'])}{temperature(row['T3_layer_mean_C'])}{temperature(row['T5_layer_mean_C'])}"
    )


def main() -> None:
    with SOURCE.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    by_key = {(r["station"], int(r["forecast_hour"])): r for r in rows}
    hours = sorted({int(r["forecast_hour"]) for r in rows if 0 < int(r["forecast_hour"]) <= 60})
    lines = [
        "RRFS-BASED FOUS-STYLE ESTIMATE",
        "EXPERIMENTAL - NOT AN OFFICIAL NWS BULLETIN",
        f"RRFS {CYCLE}Z {datetime.strptime(DATE, '%Y%m%d').strftime('%b %d %y').upper()}; DISPLAY THROUGH HOUR 60",
        "T1/T3/T5=LAYER AVERAGE ESTIMATES",
        "R1=2M RH  R2=700MB RH  R3=500MB RH; APPROXIMATE",
        "TTPTTR1R2R3 VVVLI PSDDFF HHT1T3T5   TTPTTR1R2R3 VVVLI PSDDFF HHT1T3T5",
    ]
    for pair_index, pair in enumerate(STATION_PAIRS):
        if pair_index:
            lines.append("")
        lines.append("   ".join(initial(by_key[(station, 0)]) for station in pair))
        for hour in hours:
            lines.append("   ".join(forecast(by_key[(station, hour)]) for station in pair))
    lines.extend(("", "Display note: RH values are capped at 99; negative VVV values at -99 to fit FOUS field widths."))
    OUT.write_text("\n".join(lines) + "\n", encoding="ascii")
    archive = COMPARISONS / f"FOUS61_{DATE}_{CYCLE}Z_RRFS_candidate_preview.txt"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("\n".join(lines) + "\n", encoding="ascii")
    print(f"Rendered {len(lines)} lines to {OUT}")


if __name__ == "__main__":
    main()
