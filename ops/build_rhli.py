#!/usr/bin/env python3
"""Build simple single-level RH proxies and a direct RRFS lifted-index candidate."""

from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path

from eccodes import codes_get, codes_get_array, codes_get_values, codes_grib_new_from_file, codes_release

from fetch_rrfs import BASE, CASE, STATIONS, get, nearest, selected_records
from runtime import COMPARISONS as OUT, DATA, DATE, CYCLE, LEADS, OFFICIAL



FIELDS = {
    "R1": ("2dfld", ":RH:2 m above ground:"),
    "R2": ("prslev", ":RH:700 mb:"),
    "R3": ("prslev", ":RH:500 mb:"),
    "LI": ("2dfld", ":4LFTX:180-0 mb above ground:"),
}


def main() -> None:
    message_dir = CASE / "humidity_li_selected"
    message_dir.mkdir(exist_ok=True)
    manifest_path = CASE / "humidity_li_message_manifest.tsv"
    point_rows: list[dict] = []
    manifest: list[str] = []
    out_rows: list[dict] = []

    for lead in LEADS:
        for field, (kind, needle) in FIELDS.items():
            source = f"rrfs.t{CYCLE}z.{kind}.3km.f{lead:03d}.conus.grib2"
            index_path = CASE / f"{source}.idx"
            if index_path.exists():
                index = index_path.read_bytes()
            else:
                index = get(f"{BASE}/{source}.idx")
                index_path.write_bytes(index)
            matches = selected_records(index.decode("utf-8", errors="replace").splitlines(), (needle,))
            if len(matches) != 1:
                raise RuntimeError(f"Expected one {field} field at f{lead:03d}; found {len(matches)}")
            start, end, description = matches[0]
            saved = message_dir / f"f{lead:03d}_{field}.grib2"
            # Reuse the already saved RH/L1/LI messages. R3 is refreshed because
            # the selected representative pressure was changed from 300 to 500 mb.
            if saved.exists() and field != "R3":
                raw = saved.read_bytes()
            else:
                raw = get(f"{BASE}/{source}", (start, end))
            if not raw.startswith(b"GRIB"):
                raise RuntimeError(f"Byte range for {source} did not begin with a GRIB message")
            saved.write_bytes(raw)
            manifest.append(
                f"{source}\tbytes={start}-{end}\tsha256={hashlib.sha256(raw).hexdigest()}\t{description}"
            )
            with saved.open("rb") as stream:
                gid = codes_grib_new_from_file(stream)
                if gid is None:
                    raise RuntimeError(f"Could not decode {field} at f{lead:03d}")
                try:
                    lats, lons = codes_get_array(gid, "latitudes"), codes_get_array(gid, "longitudes")
                    values = codes_get_values(gid)
                    for station, (station_lat, station_lon) in STATIONS.items():
                        point, grid_lat, grid_lon, distance = nearest(lats, lons, station_lat, station_lon)
                        point_rows.append({
                            "station": station, "forecast_hour": lead, "field": field,
                            "short_name": codes_get(gid, "shortName"), "name": codes_get(gid, "name"),
                            "level": codes_get(gid, "level"), "units": codes_get(gid, "units"),
                            "value": float(values[point]), "grid_lat": grid_lat, "grid_lon": grid_lon,
                            "distance_degrees": distance, "source_file": source,
                            "index_description": description,
                        })
                finally:
                    codes_release(gid)
            print(f"Saved RRFS {field} proxy at forecast hour {lead}")

    point_path = CASE / "rrfs_humidity_li_point_values.csv"
    with point_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(point_rows[0]))
        writer.writeheader()
        writer.writerows(point_rows)
    with manifest_path.open("w", encoding="utf-8") as output:
        output.write("\n".join(manifest) + "\n")

    indexed = {(int(r["forecast_hour"]), r["station"], r["field"]): r for r in point_rows}
    official = {(r["station"], int(r["forecast_hour"])): r for r in csv.DictReader(OFFICIAL.open(newline="", encoding="utf-8"))} if OFFICIAL.exists() else {}
    comparison_rows: list[dict] = []
    for lead in LEADS:
        for station in STATIONS:
            row = {"station": station, "forecast_hour": lead}
            for field in FIELDS:
                source = indexed[(lead, station, field)]
                value = float(source["value"])
                if field == "LI":
                    li_c = int(math.copysign(math.floor(abs(value) + 0.5), value))
                    row["LI_proxy_4LFTX_code"] = li_c + 100 if li_c < 0 else li_c
                    row["LI_4LFTX_unrounded_K"] = value
                else:
                    row[f"{field}_proxy_{'surface' if field == 'R1' else '700mb' if field == 'R2' else '500mb'}_RH_pct"] = int(math.floor(value + 0.5))
            row["candidate_status"] = "single-level RH proxies and direct 4LFTX estimate; approximate"
            out_rows.append(row)
            truth = official.get((station, lead), {})
            if truth:
                for field in FIELDS:
                    cand_key = next(k for k in row if k.startswith(f"{field}_proxy_")) if field != "LI" else "LI_proxy_4LFTX_code"
                    truth_key = {"R1": "r1_pct", "R2": "r2_pct", "R3": "r3_pct", "LI": "li_code"}[field]
                    comparison_rows.append({
                        "station": station, "forecast_hour": lead, "field": field,
                        "candidate": row[cand_key], "official_comparison_only": truth[truth_key],
                        "candidate_minus_official": int(row[cand_key]) - int(truth[truth_key]),
                        "candidate_interpretation": "single-level proxy; official FOUS field is a layer/legacy product value",
                    })

    OUT.mkdir(parents=True, exist_ok=True)
    for suffix, rows in (("candidate", out_rows), ("comparison", comparison_rows)):
        name = f"FOUS61_{DATE}_{CYCLE}Z_RRFS_RH_LI_{suffix}.csv"
        fields = list(rows[0]) if rows else ["station", "forecast_hour", "field", "candidate", "official_comparison_only", "candidate_minus_official", "candidate_interpretation"]
        with (OUT / name).open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    print(f"Wrote {len(out_rows)} RH/LI candidates and {len(comparison_rows)} separate comparisons")


if __name__ == "__main__":
    main()
