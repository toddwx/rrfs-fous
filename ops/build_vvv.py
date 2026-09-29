#!/usr/bin/env python3
"""Sample RRFS 700-mb geometric vertical motion and estimate FOUS VVV codes."""

from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path

from eccodes import codes_get, codes_get_array, codes_get_values, codes_grib_new_from_file, codes_release

from fetch_rrfs import BASE, CASE, STATIONS, get, nearest, selected_records
from runtime import COMPARISONS, DATE, CYCLE, OFFICIAL, VVV_LEADS


LEADS = VVV_LEADS
PARAMETERS = {"DZDT": ":DZDT:700 mb:", "TMP": ":TMP:700 mb:", "SPFH": ":SPFH:700 mb:"}
FETCH_HOURS = sorted(set(LEADS) | {h for lead in LEADS if lead for h in (lead - 1, lead + 1)})


def main() -> None:
    CASE.mkdir(parents=True, exist_ok=True)
    message_dir = CASE / "vvv_selected"
    message_dir.mkdir(exist_ok=True)
    values_path = CASE / "rrfs_vvv_point_values.csv"
    manifest_path = CASE / "rrfs_vvv_message_manifest.tsv"
    values = list(csv.DictReader(values_path.open(newline="", encoding="utf-8"))) if values_path.exists() else []
    known = {(int(r["forecast_hour"]), r["requested_field"], r["station"]) for r in values}
    manifest: list[str] = []
    new_values: list[dict] = []

    for lead in FETCH_HOURS:
        source = f"rrfs.t{CYCLE}z.prslev.3km.f{lead:03d}.conus.grib2"
        index_path = CASE / f"{source}.idx"
        if index_path.exists():
            index_data = index_path.read_bytes()
        else:
            index_data = get(f"{BASE}/{source}.idx")
            index_path.write_bytes(index_data)
        index_lines = index_data.decode("utf-8", errors="replace").splitlines()

        needed_parameters = PARAMETERS if lead in LEADS else {"DZDT": PARAMETERS["DZDT"]}
        for field, needle in needed_parameters.items():
            if all((lead, field, station) in known for station in STATIONS):
                continue
            matches = selected_records(index_lines, (needle,))
            if len(matches) != 1:
                raise RuntimeError(f"Expected one {field} message at f{lead:03d}; found {len(matches)}")
            start, end, index_line = matches[0]
            raw = get(f"{BASE}/{source}", (start, end))
            if not raw.startswith(b"GRIB"):
                raise RuntimeError(f"Byte range for {source} did not begin with a GRIB message")
            saved = message_dir / f"f{lead:03d}_{field}.grib2"
            saved.write_bytes(raw)
            manifest.append(
                f"{source}\tbytes={start}-{end}\tsha256={hashlib.sha256(raw).hexdigest()}\t{index_line}"
            )

            with saved.open("rb") as stream:
                gid = codes_grib_new_from_file(stream)
                if gid is None:
                    raise RuntimeError(f"Could not decode {field} at f{lead:03d}")
                try:
                    lats = codes_get_array(gid, "latitudes")
                    lons = codes_get_array(gid, "longitudes")
                    grid_values = codes_get_values(gid)
                    for station, (station_lat, station_lon) in STATIONS.items():
                        point, grid_lat, grid_lon, distance = nearest(lats, lons, station_lat, station_lon)
                        new_values.append({
                            "station": station, "forecast_hour": lead, "requested_field": field,
                            "short_name": codes_get(gid, "shortName"), "name": codes_get(gid, "name"),
                            "level": codes_get(gid, "level"), "units": codes_get(gid, "units"),
                            "value": float(grid_values[point]), "grid_lat": grid_lat, "grid_lon": grid_lon,
                            "distance_degrees": distance, "source_file": source,
                            "index_description": index_line,
                        })
                finally:
                    codes_release(gid)
            print(f"Saved {field} at 700 mb for RRFS forecast hour {lead}")

    if new_values:
        values.extend(new_values)

    # Calculate a four-nearest-point inverse-distance sample from the saved GRIBs.
    # Keep this separate from the nearest-point value so the station method is visible.
    spatial_values: dict[tuple[int, str, str], float] = {}
    for message in message_dir.glob("f*_*.grib2"):
        lead_text, field = message.stem.split("_")
        lead = int(lead_text[1:])
        with message.open("rb") as stream:
            gid = codes_grib_new_from_file(stream)
            if gid is None:
                raise RuntimeError(f"Could not decode saved RRFS message {message.name}")
            try:
                lats = codes_get_array(gid, "latitudes")
                lons = codes_get_array(gid, "longitudes")
                grid_values = codes_get_values(gid)
                for station, (station_lat, station_lon) in STATIONS.items():
                    dx = ((lons - station_lon + 180.0) % 360.0 - 180.0) * math.cos(math.radians(station_lat))
                    dy = lats - station_lat
                    distances = (dx * dx + dy * dy) ** 0.5
                    nearest_indices = distances.argpartition(3)[:4]
                    if distances[nearest_indices].min() < 1e-9:
                        estimate = float(grid_values[int(nearest_indices[distances[nearest_indices].argmin()])])
                    else:
                        weights = 1.0 / (distances[nearest_indices] ** 2)
                        estimate = float((grid_values[nearest_indices] * weights).sum() / weights.sum())
                    spatial_values[(lead, station, field)] = estimate
            finally:
                codes_release(gid)

    for row in values:
        row["idw4_value"] = spatial_values[(int(row["forecast_hour"]), row["station"], row["requested_field"])]
    with values_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(values[0]))
        writer.writeheader()
        writer.writerows(values)
    if new_values:
        with manifest_path.open("a", encoding="utf-8") as output:
            output.write("\n".join(manifest) + "\n")

    by_point = {(int(r["forecast_hour"]), r["station"], r["requested_field"]): r for r in values}
    by_idw = {(int(r["forecast_hour"]), r["station"], r["requested_field"]): float(r["idw4_value"]) for r in values}
    official = {(r["station"], int(r["forecast_hour"])): r for r in csv.DictReader(OFFICIAL.open(newline="", encoding="utf-8"))} if OFFICIAL.exists() else {}
    candidate_rows: list[dict] = []
    comparison_rows: list[dict] = []
    rd, gravity, pressure = 287.05, 9.80665, 70000.0

    def round_code(value: float) -> int:
        return int(math.copysign(math.floor(abs(value) + 0.5), value))

    for lead in LEADS:
        for station in STATIONS:
            w = by_point[(lead, station, "DZDT")]
            t = by_point[(lead, station, "TMP")]
            q = by_point[(lead, station, "SPFH")]
            temperature_k, specific_humidity, wz = float(t["value"]), float(q["value"]), float(w["value"])
            if lead:
                w_before = float(by_point[(lead - 1, station, "DZDT")]["value"])
                w_after = float(by_point[(lead + 1, station, "DZDT")]["value"])
                wz_weighted = (w_before + 2.0 * wz + w_after) / 4.0
                w_before_idw = by_idw[(lead - 1, station, "DZDT")]
                w_after_idw = by_idw[(lead + 1, station, "DZDT")]
                wz_idw = by_idw[(lead, station, "DZDT")]
                wz_idw_weighted = (w_before_idw + 2.0 * wz_idw + w_after_idw) / 4.0
            else:
                w_before = w_after = wz
                wz_weighted = wz
                w_before_idw = w_after_idw = wz_idw = wz_idw_weighted = by_idw[(lead, station, "DZDT")]
            virtual_temperature = temperature_k * (1.0 + 0.61 * specific_humidity)
            density = pressure / (rd * virtual_temperature)
            omega_pa_s = -density * gravity * wz
            # FOUS VVV is tenths of a microbar per second; negative denotes descent.
            # Since 1 Pa = 10 microbar, multiply pressure velocity by 100 after reversing sign.
            raw_code = -omega_pa_s * 100.0
            candidate_center = round_code(raw_code)
            weighted_code = round_code(density * gravity * wz_weighted * 100.0)
            temperature_idw = by_idw[(lead, station, "TMP")]
            humidity_idw = by_idw[(lead, station, "SPFH")]
            density_idw = pressure / (rd * temperature_idw * (1.0 + 0.61 * humidity_idw))
            idw_center_code = round_code(density_idw * gravity * wz_idw * 100.0)
            idw_weighted_code = round_code(density_idw * gravity * wz_idw_weighted * 100.0)
            truth = official.get((station, lead), {}).get("vvv_code", "")
            candidate_rows.append({
                "station": station, "forecast_hour": lead, "RRFS_700mb_wz_m_s": wz,
                "RRFS_700mb_wz_1_2_1_m_s": wz_weighted,
                "wz_before_m_s": w_before, "wz_after_m_s": w_after,
                "RRFS_700mb_wz_idw4_m_s": wz_idw,
                "RRFS_700mb_wz_idw4_1_2_1_m_s": wz_idw_weighted,
                "RRFS_700mb_temperature_K": temperature_k, "RRFS_700mb_specific_humidity_kg_kg": specific_humidity,
                "derived_air_density_kg_m3": density, "derived_omega_Pa_s": omega_pa_s,
                "center_hour_candidate_vvv_code": candidate_center,
                "candidate_vvv_code": weighted_code,
                "idw4_center_candidate_vvv_code": idw_center_code,
                "idw4_1_2_1_candidate_vvv_code": idw_weighted_code,
                "candidate_method": "nearest RRFS grid point; 1:2:1 time average of geometric w; convert to pressure velocity with 700-mb ideal-gas density; FOUS scale/sign",
            })
            if truth != "":
                official_code = int(truth)
                comparison_rows.append({
                    "station": station, "forecast_hour": lead,
                    "weighted_candidate_vvv_code": weighted_code,
                    "center_hour_candidate_vvv_code": candidate_center,
                    "idw4_weighted_candidate_vvv_code": idw_weighted_code,
                    "idw4_center_candidate_vvv_code": idw_center_code,
                    "official_comparison_only": official_code,
                    "weighted_candidate_minus_official": weighted_code - official_code,
                    "center_candidate_minus_official": candidate_center - official_code,
                    "idw4_weighted_candidate_minus_official": idw_weighted_code - official_code,
                    "idw4_center_candidate_minus_official": idw_center_code - official_code,
                    "weighted_same_up_down_sign": (weighted_code >= 0) == (official_code >= 0),
                    "center_same_up_down_sign": (candidate_center >= 0) == (official_code >= 0),
                    "idw4_weighted_same_up_down_sign": (idw_weighted_code >= 0) == (official_code >= 0),
                })

    COMPARISONS.mkdir(parents=True, exist_ok=True)
    for suffix, rows in (("candidate", candidate_rows), ("comparison", comparison_rows)):
        name = f"FOUS61_{DATE}_{CYCLE}Z_RRFS_VVV_{suffix}.csv"
        fields = list(rows[0]) if rows else ["station", "forecast_hour", "weighted_candidate_vvv_code", "center_hour_candidate_vvv_code", "idw4_weighted_candidate_vvv_code", "idw4_center_candidate_vvv_code", "official_comparison_only", "weighted_candidate_minus_official", "center_candidate_minus_official", "idw4_weighted_candidate_minus_official", "idw4_center_candidate_minus_official", "weighted_same_up_down_sign", "center_same_up_down_sign", "idw4_weighted_same_up_down_sign"]
        with (COMPARISONS / name).open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    print(f"Wrote {len(candidate_rows)} VVV candidate rows and {len(comparison_rows)} separate official comparisons")


if __name__ == "__main__":
    main()
