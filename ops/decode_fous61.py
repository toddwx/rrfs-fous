#!/usr/bin/env python3
"""Split Albany FOUS61 compact station records into named fields."""
import argparse
import csv
import re
from runtime import STATIONS

TARGETS = set(STATIONS)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bulletin")
    parser.add_argument("--csv", required=True)
    args = parser.parse_args()
    text = open(args.bulletin, encoding="ascii").read()
    station_records = re.findall(r"\b([A-Z]{3})//(\d{6})\s+(\S{5})\s+(\S{6})\s+(\S{8})", text)
    stations = [record[0] for record in station_records]
    rows = []

    for station, humidity, vvvli, psddff, hht in station_records:
        if station not in TARGETS:
            continue
        rows.append(decode_row(station, 0, None, humidity, vvvli, psddff, hht))

    active_pair = []
    for line in text.splitlines():
        line_stations = re.findall(r"\b([A-Z]{3})//\d{6}", line)
        if line_stations:
            active_pair = line_stations
        tokens = line.split()
        if not tokens or not re.fullmatch(r"\d{11}", tokens[0]):
            continue
        records = [tokens[i:i+4] for i in range(0, len(tokens), 4)]
        for i, record in enumerate(records):
            if len(record) != 4 or i >= len(active_pair):
                continue
            station = active_pair[i]
            if station not in TARGETS:
                continue
            lead_precip_rh, vvvli, psddff, hht = record
            lead = int(lead_precip_rh[:2])
            precip = int(lead_precip_rh[2:5])
            humidity = lead_precip_rh[5:]
            rows.append(decode_row(station, lead, precip, humidity, vvvli, psddff, hht))

    fields = ["station", "forecast_hour", "precip_hundredths_in", "r1_pct", "r2_pct", "r3_pct", "vvv_code", "li_code", "li_c", "ps_code", "wind_dir_tens_deg", "wind_speed_kt", "thickness_code", "t1_code", "t3_code", "t5_code"]
    rows.sort(key=lambda r: (r["station"], r["forecast_hour"]))
    with open(args.csv, "w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Decoded {len(rows)} rows for {', '.join(sorted(TARGETS))}")

def decode_row(station, hour, precip, humidity, vvvli, psddff, hht):
    li_code = int(vvvli[-2:])
    li = li_code - 100 if li_code >= 50 else li_code
    return {
        "station": station,
        "forecast_hour": hour,
        "precip_hundredths_in": "" if precip is None else precip,
        "r1_pct": int(humidity[0:2]),
        "r2_pct": int(humidity[2:4]),
        "r3_pct": int(humidity[4:6]),
        "vvv_code": vvvli[:-2],
        "li_code": li_code,
        "li_c": li,
        "ps_code": psddff[0:2],
        "wind_dir_tens_deg": psddff[2:4],
        "wind_speed_kt": psddff[4:6],
        "thickness_code": hht[0:2],
        "t1_code": hht[2:4],
        "t3_code": hht[4:6],
        "t5_code": hht[6:8],
    }

if __name__ == "__main__":
    main()
