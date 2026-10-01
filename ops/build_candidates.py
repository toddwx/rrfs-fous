#!/usr/bin/env python3
"""Build a clearly labeled, partial RRFS FOUS-code candidate from sampled GRIB fields."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from runtime import CASE, COMPARISONS as OUT, DATA, DATE, CYCLE, LEADS, OFFICIAL, STATIONS


def code_for(rows: list[dict], station: str, lead: int, short: str, level: str | None = None) -> dict | None:
    for row in rows:
        if row["station"] == station and int(row["lead_hour"]) == lead and row["short_name"] == short:
            if level is None or row["level"] == level:
                return row
    return None


def nearest_10deg_code(u: float, v: float) -> int:
    direction = (270.0 - math.degrees(math.atan2(v, u))) % 360.0
    code = int(math.floor(direction / 10.0 + 0.5)) % 36
    return 36 if code == 0 else code


def interp_pressure(samples: list[tuple[float, float]], target: float) -> float | None:
    samples = sorted((p, v) for p, v in samples if math.isfinite(p) and math.isfinite(v))
    if not samples:
        return None
    for pressure, value in samples:
        if abs(pressure - target) < 1e-6:
            return value
    below = [item for item in samples if item[0] < target]
    above = [item for item in samples if item[0] > target]
    if not below or not above:
        return None
    p0, v0 = below[-1]
    p1, v1 = above[0]
    return v0 + (v1 - v0) * ((target - p0) / (p1 - p0))


def sigma_layer_mean(
    rows: list[dict], station: str, lead: int, surface_hpa: float,
    surface_temp_k: float, sigma_bottom: float, sigma_top: float,
) -> float | None:
    """Pressure-weighted temperature mean across a sigma layer, in kelvin."""
    if not all(math.isfinite(value) for value in (surface_hpa, surface_temp_k)):
        return None
    profile = [(surface_hpa, surface_temp_k)]
    for row in rows:
        if row["station"] != station or int(row["lead_hour"]) != lead:
            continue
        if row["short_name"] != "t" or row.get("type_of_level") != "isobaricInhPa":
            continue
        pressure, value = float(row["level"]), float(row["value"])
        if pressure < surface_hpa and math.isfinite(value):
            profile.append((pressure, value))
    bottom, top = surface_hpa * sigma_bottom, surface_hpa * sigma_top
    low_value, high_value = interp_pressure(profile, top), interp_pressure(profile, bottom)
    if low_value is None or high_value is None:
        return None
    points = [(top, low_value)]
    points.extend((pressure, value) for pressure, value in profile if top < pressure < bottom)
    points.append((bottom, high_value))
    points.sort()
    area = sum((p1 - p0) * (t0 + t1) / 2.0 for (p0, t0), (p1, t1) in zip(points, points[1:]))
    return area / (bottom - top)


def low_layer_mean(rows: list[dict], station: str, lead: int, component: str, surface_hpa: float) -> float | None:
    surface = code_for(rows, station, lead, "10u" if component == "u" else "10v", "10")
    if not surface or not math.isfinite(surface["value"]):
        return None
    top = surface_hpa - 35.0
    profile = [(surface_hpa, surface["value"])]
    for pressure in (1000, 975, 950, 925):
        item = code_for(rows, station, lead, component, str(pressure))
        if item and pressure < surface_hpa and math.isfinite(item["value"]):
            profile.append((float(pressure), item["value"]))
    top_value = interp_pressure(profile, top)
    if top_value is None:
        return None
    points = [(surface_hpa, surface["value"]), (top, top_value)]
    points.extend((pressure, value) for pressure, value in profile if top < pressure < surface_hpa)
    points.sort(key=lambda item: item[0], reverse=True)
    area = sum((p0 - p1) * (v0 + v1) / 2.0 for (p0, v0), (p1, v1) in zip(points, points[1:]))
    return area / 35.0


def main() -> None:
    with (CASE / "nearest_grid_values.csv").open(newline="", encoding="utf-8") as source:
        grib = list(csv.DictReader(source))
    for row in grib:
        row["value"] = float(row["value"])
    layer_samples = CASE / "temperature_layer_trial_extra_leads0_84" / "nearest_grid_temperature_values.csv"
    if layer_samples.exists():
        with layer_samples.open(newline="", encoding="utf-8") as source:
            for item in csv.DictReader(source):
                grib.append({
                    "station": item["station"], "lead_hour": item["lead_hour"],
                    "short_name": "t", "type_of_level": "isobaricInhPa",
                    "level": item["level_mb"], "value": float(item["temperature_K"]),
                })
    if OFFICIAL.exists():
        with OFFICIAL.open(newline="", encoding="utf-8") as source:
            official = {(r["station"], int(r["forecast_hour"])): r for r in csv.DictReader(source)}
    else:
        official = {}
    rhli_path = OUT / f"FOUS61_{DATE}_{CYCLE}Z_RRFS_RH_LI_candidate.csv"
    rhli = {}
    if rhli_path.exists():
        with rhli_path.open(newline="", encoding="utf-8") as source:
            rhli = {(r["station"], int(r["forecast_hour"])): r for r in csv.DictReader(source)}
    vvv_path = OUT / f"FOUS61_{DATE}_{CYCLE}Z_RRFS_VVV_candidate.csv"
    vvv = {}
    if vvv_path.exists():
        with vvv_path.open(newline="", encoding="utf-8") as source:
            vvv = {(r["station"], int(r["forecast_hour"])): r for r in csv.DictReader(source)}

    candidates: list[dict] = []
    comparisons: list[dict] = []
    provenance: list[dict] = []
    for station in STATIONS:
        for lead in LEADS:
            row: dict = {"station": station, "forecast_hour": lead}
            evidence: dict[str, str] = {}

            def add(field: str, value: int | None, source: str, reason: str, official_field: str | None = None) -> None:
                row[field] = "" if value is None else value
                evidence[field] = reason if value is not None else "not available in sampled files"
                if value is not None:
                    truth = official.get((station, lead), {}).get(official_field or "", "")
                    diff = ""
                    if truth:
                        try:
                            truth_code = int(truth)
                            if official_field == "wind_dir_tens_deg":
                                candidate_angle = 0 if value == 36 else value % 36
                                official_angle = 0 if truth_code == 36 else truth_code % 36
                                diff = (candidate_angle - official_angle + 18) % 36 - 18
                            elif official_field in {"t1_code", "t3_code", "t5_code"}:
                                truth_temperature = truth_code - 100 if truth_code >= 50 else truth_code
                                diff = value - truth_temperature
                            else:
                                diff = value - truth_code
                        except ValueError:
                            pass
                    if truth:
                        comparisons.append(
                            {
                                "station": station,
                                "forecast_hour": lead,
                                "field": official_field or field,
                                "candidate_code": value,
                                "official_comparison_only": truth,
                                "candidate_minus_official": diff,
                                "source_method": reason,
                            }
                        )

            t2 = code_for(grib, station, lead, "2t", "2")
            add("T1_proxy_2m_temp_C", round(t2["value"] - 273.15) if t2 else None, "2m temperature", "Rounded 2 m temperature in C; proxy only, not the 35 mb FOUS layer", "t1_code")
            surface_pressure = code_for(grib, station, lead, "sp")
            surface_hpa = surface_pressure["value"] / 100.0 if surface_pressure and math.isfinite(surface_pressure["value"]) else None
            t_samples: list[tuple[float, float]] = []
            if surface_hpa is not None and t2 and math.isfinite(t2["value"]):
                t_samples.append((surface_hpa, t2["value"]))
                for pressure in (1000, 975, 950, 925):
                    item = code_for(grib, station, lead, "t", str(pressure))
                    if item and pressure < surface_hpa and math.isfinite(item["value"]):
                        t_samples.append((float(pressure), item["value"]))
            s1_pressure = surface_hpa * 0.98230 if surface_hpa is not None else None
            t1_s1 = interp_pressure(t_samples, s1_pressure) if s1_pressure is not None else None
            add("T1_proxy_S1_sigma_C", round(t1_s1 - 273.15) if t1_s1 is not None else None, "near-surface temperature profile", "Interpolate to the historical S1 target (0.98230 × surface pressure); candidate only, not a confirmed RRFS recipe", "t1_code")
            t_layer = sigma_layer_mean(grib, station, lead, surface_hpa, t2["value"], 1.000, 0.965) if surface_hpa is not None and t2 else None
            add("T1_layer_mean_C", round(t_layer - 273.15) if t_layer is not None else None, "RRFS temperature profile", "Estimated pressure-weighted average through the lowest 35 mb; standard pressure levels with a 2 m surface anchor", "t1_code")
            t3 = code_for(grib, station, lead, "t", "900")
            add("T3_proxy_900mb_C", round(t3["value"] - 273.15) if t3 else None, "900 mb temperature", "Rounded 900 mb temperature in C; direct pressure-level proxy", "t3_code")
            t3_layer = sigma_layer_mean(grib, station, lead, surface_hpa, t2["value"], 0.922, 0.872) if surface_hpa is not None and t2 else None
            add("T3_layer_mean_C", round(t3_layer - 273.15) if t3_layer is not None else None, "RRFS temperature profile", "Estimated pressure-weighted average across the historical T3 sigma layer; standard pressure levels with a 2 m surface anchor", "t3_code")
            t5 = code_for(grib, station, lead, "t", "800")
            add("T5_proxy_800mb_C", round(t5["value"] - 273.15) if t5 else None, "800 mb temperature", "Rounded 800 mb temperature in C; direct pressure-level proxy", "t5_code")
            t5_layer = sigma_layer_mean(grib, station, lead, surface_hpa, t2["value"], 0.816, 0.755) if surface_hpa is not None and t2 else None
            add("T5_layer_mean_C", round(t5_layer - 273.15) if t5_layer is not None else None, "RRFS temperature profile", "Estimated pressure-weighted average across the historical T5 sigma layer; standard pressure levels with a 2 m surface anchor", "t5_code")

            mslet = code_for(grib, station, lead, "mslet")
            add("PS_code", math.floor(mslet["value"] / 100.0) % 100 if mslet else None, "MSLET", "Mean sea-level pressure in hPa; retain the last two digits", "ps_code")

            u = code_for(grib, station, lead, "10u", "10")
            v = code_for(grib, station, lead, "10v", "10")
            if u and v:
                direction = nearest_10deg_code(u["value"], v["value"])
                speed = int(math.floor(math.hypot(u["value"], v["value"]) * 1.943844 + 0.5))
                add("DD_proxy_10m_code", direction, "10 m U/V", "Direction from 10 m wind vector; not the FOUS lowest-layer mean", "wind_dir_tens_deg")
                add("FF_proxy_10m_kt", speed, "10 m U/V", "Speed from 10 m wind vector in knots; not the FOUS lowest-layer mean", "wind_speed_kt")
            else:
                add("DD_proxy_10m_code", None, "10 m U/V", "10 m wind components unavailable", "wind_dir_tens_deg")
                add("FF_proxy_10m_kt", None, "10 m U/V", "10 m wind components unavailable", "wind_speed_kt")
            if surface_hpa is not None:
                mean_u = low_layer_mean(grib, station, lead, "u", surface_hpa)
                mean_v = low_layer_mean(grib, station, lead, "v", surface_hpa)
            else:
                mean_u = mean_v = None
            if mean_u is not None and mean_v is not None:
                add("DD_proxy_lowest35mb_code", nearest_10deg_code(mean_u, mean_v), "near-surface U/V profile", "Pressure-weighted wind vector through the lowest 35 mb using 10 m and available pressure-level winds", "wind_dir_tens_deg")
                add("FF_proxy_lowest35mb_kt", int(math.floor(math.hypot(mean_u, mean_v) * 1.943844 + 0.5)), "near-surface U/V profile", "Speed from the same pressure-weighted lowest-35-mb wind vector", "wind_speed_kt")
            else:
                add("DD_proxy_lowest35mb_code", None, "near-surface U/V profile", "Not enough valid wind levels to sample the lowest 35 mb", "wind_dir_tens_deg")
                add("FF_proxy_lowest35mb_kt", None, "near-surface U/V profile", "Not enough valid wind levels to sample the lowest 35 mb", "wind_speed_kt")

            h500 = code_for(grib, station, lead, "gh", "500")
            h1000 = code_for(grib, station, lead, "gh", "1000")
            thickness_code = int(math.floor((h500["value"] - h1000["value"]) / 10.0 + 0.5)) % 100 if h500 and h1000 else None
            add("HH_code", thickness_code, "HGT 500/1000 mb", "500–1000 mb height difference in decameters; retain the last two digits", "thickness_code")

            # Convert six-hour differences of cumulative model precipitation to hundredths of an inch.
            acc = code_for(grib, station, lead, "tp")
            if lead == 0:
                ptt = None
            # ecCodes normalizes the source index's "0-1 day" notation to "0-24".
            elif acc and acc["step_range"] == f"0-{lead}":
                previous = code_for(grib, station, lead - 6, "tp") if lead > 6 else None
                if previous:
                    ptt = int(math.floor((acc["value"] - previous["value"]) / 25.4 * 100.0 + 0.5))
                else:
                    ptt = int(math.floor(acc["value"] / 25.4 * 100.0 + 0.5))
            else:
                ptt = None
            add("PTT_hundredths_in", ptt, "APCP accumulation", "Six-hour total derived by subtracting consecutive RRFS cumulative precipitation fields", "precip_hundredths_in")

            # Humidity values are intentionally simple single-level proxies.
            supplement = rhli.get((station, lead), {})
            for field, key, description in (
                ("R1", "R1_proxy_surface_RH_pct", "2 m relative humidity; proxy for lowest-layer mean"),
                ("R2", "R2_proxy_700mb_RH_pct", "700 mb relative humidity; single-level proxy"),
                ("R3", "R3_proxy_500mb_RH_pct", "500 mb relative humidity; single-level proxy at upper-layer base"),
                ("LI", "LI_proxy_4LFTX_code", "RRFS 4LFTX 180–0 mb AGL lifted-index estimate"),
            ):
                row[field] = supplement.get(key, "")
                evidence[field] = description if row[field] != "" else "not yet sampled from RRFS"

            vvv_row = vvv.get((station, lead), {})
            row["VVV"] = vvv_row.get("idw4_1_2_1_candidate_vvv_code", "")
            evidence["VVV"] = (
                "700 mb geometric vertical velocity converted to FOUS units; 1:2:1 time average, four-point IDW; very rough"
                if row["VVV"] != "" else "not yet sampled at this lead"
            )
            for field, reason in evidence.items():
                provenance.append({"station": station, "forecast_hour": lead, "field": field, "source_or_status": reason})
            row["candidate_status"] = "partial experimental estimate; proxies are labeled"
            candidates.append(row)

    candidate_path = OUT / f"FOUS61_{DATE}_{CYCLE}Z_RRFS_parallel_candidate.csv"
    comparison_path = OUT / f"FOUS61_{DATE}_{CYCLE}Z_RRFS_parallel_comparison.csv"
    provenance_path = OUT / f"FOUS61_{DATE}_{CYCLE}Z_RRFS_parallel_provenance.csv"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    with candidate_path.open("w", newline="", encoding="utf-8") as f:
        fields = list(candidates[0])
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(candidates)
    with comparison_path.open("w", newline="", encoding="utf-8") as f:
        fields = list(comparisons[0]) if comparisons else ["station", "forecast_hour", "field", "candidate_code", "official_comparison_only", "candidate_minus_official", "source_method"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(comparisons)
    with provenance_path.open("w", newline="", encoding="utf-8") as f:
        fields = ["station", "forecast_hour", "field", "source_or_status"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(provenance)
    print(f"Wrote {candidate_path.name}, {comparison_path.name}, and {provenance_path.name}")


if __name__ == "__main__":
    main()
