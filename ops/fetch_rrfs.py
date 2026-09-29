#!/usr/bin/env python3
"""Fetch a small set of RRFS GRIB messages and sample them near FOUS stations."""

from __future__ import annotations

import csv
import hashlib
import math
import re
import time
import urllib.request
import urllib.error
from pathlib import Path
from runtime import CASE, CYCLE, DATE, LEADS, RRFS_BASE as BASE, STATIONS

from eccodes import (
    codes_get,
    codes_get_array,
    codes_get_values,
    codes_grib_new_from_file,
    codes_release,
)





def apcp_accumulation_label(lead: int) -> str:
    if lead % 24 == 0:
        return f"0-{lead // 24} day acc fcst:"
    return f"0-{lead} hour acc fcst:"
NEEDLES = {
    "2dfld": (
        ":MSLET:mean sea level:",
        ":PRES:surface:",
        ":TMP:2 m above ground:",
        ":UGRD:10 m above ground:",
        ":VGRD:10 m above ground:",
        ":APCP:surface:0-6 hour acc fcst:",
        ":APCP:surface:0-12 hour acc fcst:",
    ),
    "prslev": (
        ":TMP:800 mb:",
        ":TMP:900 mb:",
        ":HGT:500 mb:",
        ":HGT:1000 mb:",
        ":TMP:950 mb:",
        ":TMP:975 mb:",
        ":TMP:1000 mb:",
        ":TMP:925 mb:",
        ":UGRD:950 mb:",
        ":UGRD:975 mb:",
        ":UGRD:1000 mb:",
        ":UGRD:925 mb:",
        ":VGRD:950 mb:",
        ":VGRD:975 mb:",
        ":VGRD:1000 mb:",
        ":VGRD:925 mb:",
    ),
}


def get(url: str, byte_range: tuple[int, int] | None = None) -> bytes:
    headers = {"User-Agent": "FOUS61-RRFS-research/1.0"}
    if byte_range:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
    request = urllib.request.Request(url, headers=headers)
    last_error = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                data = response.read()
                if byte_range and response.status != 206:
                    raise RuntimeError(f"Server did not honor byte range for {url} (HTTP {response.status})")
                return data
        except (TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not download {url}: {last_error}")


def selected_records(index: list[str], needles: tuple[str, ...]) -> list[tuple[int, int, str]]:
    parsed: list[tuple[int, str]] = []
    for line in index:
        match = re.match(r"\d+:(\d+):(.*)", line)
        if match:
            parsed.append((int(match.group(1)), line))
    found = []
    for i, (start, line) in enumerate(parsed):
        if any(needle in line for needle in needles):
            end = parsed[i + 1][0] - 1 if i + 1 < len(parsed) else start + 20_000_000
            found.append((start, end, line))
    return found


def nearest(latitudes, longitudes, station_lat: float, station_lon: float) -> tuple[int, float, float, float]:
    delta_lon = (longitudes - station_lon + 180.0) % 360.0 - 180.0
    distances = (latitudes - station_lat) ** 2 + (delta_lon * math.cos(math.radians(station_lat))) ** 2
    i = int(distances.argmin())
    grid_lon = float((longitudes[i] + 180.0) % 360.0 - 180.0)
    return i, float(latitudes[i]), grid_lon, float(distances[i]) ** 0.5


def main() -> None:
    CASE.mkdir(parents=True, exist_ok=True)
    extracted: list[dict] = []
    manifest: list[str] = []
    for lead in LEADS:
        for kind, needles in NEEDLES.items():
            if kind == "2dfld" and lead > 0:
                needles = needles + (f":APCP:surface:{apcp_accumulation_label(lead)}",)
            stem = f"rrfs.t{CYCLE}z.{kind}.3km.f{lead:03d}.conus.grib2"
            idx_data = get(f"{BASE}/{stem}.idx")
            idx_lines = idx_data.decode("utf-8", errors="replace").splitlines()
            records = selected_records(idx_lines, needles)
            if not records:
                continue
            grib_path = CASE / f"{stem.replace('.grib2', '.selected.grib2')}"
            with grib_path.open("wb") as output:
                for start, end, idx_line in records:
                    raw = get(f"{BASE}/{stem}", (start, end))
                    if not raw.startswith(b"GRIB"):
                        raise RuntimeError(f"Range at {start} in {stem} did not begin with a GRIB message")
                    output.write(raw)
                    manifest.append(
                        f"{stem}\tbytes={start}-{end}\tsha256={hashlib.sha256(raw).hexdigest()}\t{idx_line}"
                    )
            with grib_path.open("rb") as stream:
                while True:
                    gid = codes_grib_new_from_file(stream)
                    if gid is None:
                        break
                    try:
                        lats = codes_get_array(gid, "latitudes")
                        lons = codes_get_array(gid, "longitudes")
                        values = codes_get_values(gid)
                        metadata = {
                            "lead_hour": lead,
                            "source_file": stem,
                            "short_name": codes_get(gid, "shortName"),
                            "type_of_level": codes_get(gid, "typeOfLevel"),
                            "level": codes_get(gid, "level"),
                            "step_range": codes_get(gid, "stepRange"),
                            "units": codes_get(gid, "units"),
                        }
                        for station, (station_lat, station_lon) in STATIONS.items():
                            point, grid_lat, grid_lon, distance = nearest(lats, lons, station_lat, station_lon)
                            value = float(values[point])
                            if abs(value) > 1e20:
                                value = float("nan")
                            extracted.append(
                                {
                                    "station": station,
                                    "station_lat": station_lat,
                                    "station_lon": station_lon,
                                    "grid_lat": grid_lat,
                                    "grid_lon": grid_lon,
                                    "distance_degrees": distance,
                                    "value": value,
                                    **metadata,
                                }
                            )
                    finally:
                        codes_release(gid)

    columns = [
        "station", "station_lat", "station_lon", "grid_lat", "grid_lon", "distance_degrees",
        "lead_hour", "short_name", "type_of_level", "level", "step_range", "units", "value", "source_file",
    ]
    with (CASE / "nearest_grid_values.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(extracted)
    (CASE / "selected_message_manifest.tsv").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    print(f"Extracted {len(extracted)} station values from {len(manifest)} RRFS messages")


if __name__ == "__main__":
    main()
