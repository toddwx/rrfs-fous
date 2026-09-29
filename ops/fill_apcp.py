#!/usr/bin/env python3
"""Add the RRFS cumulative APCP records labelled in days at f024/f048/f072."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from eccodes import codes_get, codes_get_array, codes_get_values, codes_grib_new_from_file, codes_release

from fetch_rrfs import BASE, CASE, STATIONS, get, nearest, selected_records
from runtime import CYCLE


def main() -> None:
    values_path = CASE / "nearest_grid_values.csv"
    rows = list(csv.DictReader(values_path.open(newline="", encoding="utf-8")))
    present = {(int(r["lead_hour"]), r["short_name"], r["step_range"]) for r in rows}
    manifest_path = CASE / "selected_message_manifest.tsv"
    manifest_lines = []
    new_rows = []

    for lead in (24, 48, 72):
        step = f"0-{lead}"
        if any(item[0] == lead and item[1] == "tp" and item[2] == step for item in present):
            continue
        index_step = f"0-{lead // 24} day"
        stem = f"rrfs.t{CYCLE}z.2dfld.3km.f{lead:03d}.conus.grib2"
        idx = get(f"{BASE}/{stem}.idx")
        (CASE / f"{stem}.idx").write_bytes(idx)
        records = selected_records(idx.decode("utf-8", errors="replace").splitlines(),
                                   (f":APCP:surface:{index_step} acc fcst:",))
        if len(records) != 1:
            raise RuntimeError(f"Expected one APCP accumulation record at f{lead:03d}, found {len(records)}")
        start, end, description = records[0]
        raw = get(f"{BASE}/{stem}", (start, end))
        if not raw.startswith(b"GRIB"):
            raise RuntimeError(f"Byte range for {stem} did not begin with a GRIB message")

        selected_path = CASE / f"{stem.replace('.grib2', '.selected.grib2')}"
        with selected_path.open("ab") as output:
            output.write(raw)
        manifest_lines.append(
            f"{stem}\tbytes={start}-{end}\tsha256={hashlib.sha256(raw).hexdigest()}\t{description}"
        )

        message_path = CASE / f"{stem.replace('.grib2', '.apcp.grib2')}"
        message_path.write_bytes(raw)
        with message_path.open("rb") as stream:
            gid = codes_grib_new_from_file(stream)
            if gid is None:
                raise RuntimeError(f"Could not decode the APCP message at f{lead:03d}")
            try:
                lats = codes_get_array(gid, "latitudes")
                lons = codes_get_array(gid, "longitudes")
                grid_values = codes_get_values(gid)
                for station, (station_lat, station_lon) in STATIONS.items():
                    point, grid_lat, grid_lon, distance = nearest(lats, lons, station_lat, station_lon)
                    new_rows.append({
                        "station": station, "station_lat": station_lat, "station_lon": station_lon,
                        "grid_lat": grid_lat, "grid_lon": grid_lon, "distance_degrees": distance,
                        "lead_hour": lead, "short_name": codes_get(gid, "shortName"),
                        "type_of_level": codes_get(gid, "typeOfLevel"), "level": codes_get(gid, "level"),
                        "step_range": codes_get(gid, "stepRange"), "units": codes_get(gid, "units"),
                        "value": float(grid_values[point]), "source_file": stem,
                    })
            finally:
                codes_release(gid)
        print(f"Added RRFS cumulative precipitation at forecast hour {lead}")

    if new_rows:
        with values_path.open("a", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writerows(new_rows)
        with manifest_path.open("a", encoding="utf-8") as output:
            output.write("\n".join(manifest_lines) + "\n")
    print(f"Added {len(new_rows)} station values")


if __name__ == "__main__":
    main()
