"""Build a same-cycle NAM versus NAM FOUS check for temperatures and PTT.

All candidate values come only from NAM BUFR/GRIB. Official FOUS is decoded
separately and is used only for the comparison table and score summary.
"""
from __future__ import annotations

import csv
import hashlib
import http.client
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from runtime import CYCLE, DATA, DATE, OFFICIAL, ROOT, FOUS61_STATIONS

BASE = f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/nam/prod/nam.{DATE}"
BUFR_STATION_IDS = {
    "ALB": "725180", "BTV": "726170", "BOS": "725090", "LGA": "725030",
    "PHL": "724080", "IPT": "725140",
}
SIGMA_LAYERS = {"T1": (1.000, 0.965), "T3": (0.922, 0.872), "T5": (0.816, 0.755)}
FOUS_TEMP_FIELDS = {"T1": "t1_code", "T3": "t3_code", "T5": "t5_code"}
OUT = DATA / "comparisons"
CASE = DATA / "model" / "cases" / f"{DATE}_{CYCLE}Z_nam"
SITE_RESULT = ROOT / "site" / "data" / "nam_comparison.json"


def get(url: str, byte_range: tuple[int, int] | None = None) -> bytes:
    headers = {"User-Agent": "FOUS61-NAM-research/1.0"}
    if byte_range:
        end = "" if byte_range[1] is None else str(byte_range[1])
        headers["Range"] = f"bytes={byte_range[0]}-{end}"
    request = urllib.request.Request(url, headers=headers)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
                if byte_range and response.status != 206:
                    raise RuntimeError(f"NAM server did not honor a byte-range request (HTTP {response.status})")
                return body
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError,
                http.client.HTTPException, ConnectionError) as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not retrieve NAM data: {last_error}")


def interpolate(profile: list[tuple[float, float]], pressure: float) -> float | None:
    points = sorted((p, t) for p, t in profile if math.isfinite(p) and math.isfinite(t))
    if len(points) < 2 or pressure < points[0][0] or pressure > points[-1][0]:
        return None
    for (p0, t0), (p1, t1) in zip(points, points[1:]):
        if p0 <= pressure <= p1:
            if p1 == p0:
                return t0
            return t0 + (t1 - t0) * (pressure - p0) / (p1 - p0)
    return None


def layer_mean(profile: list[tuple[float, float]], bottom: float, top: float) -> float | None:
    lo, hi = sorted((bottom, top))
    t_lo, t_hi = interpolate(profile, lo), interpolate(profile, hi)
    if t_lo is None or t_hi is None:
        return None
    inside = [(p, t) for p, t in profile if lo < p < hi]
    points = [(lo, t_lo), *sorted(inside), (hi, t_hi)]
    area = sum((p1 - p0) * (t0 + t1) / 2 for (p0, t0), (p1, t1) in zip(points, points[1:]))
    return area / (hi - lo)


def fous_c(raw: str) -> float | None:
    if not raw:
        return None
    code = int(raw)
    return float(code - 100 if code >= 50 else code)


def nearest_index(latitudes, longitudes, lat: float, lon: float) -> int:
    import numpy as np
    dlon = (longitudes - lon + 180.0) % 360.0 - 180.0
    distances = (latitudes - lat) ** 2 + (dlon * math.cos(math.radians(lat))) ** 2
    return int(np.nanargmin(distances))


def fetch_bufr_profiles() -> tuple[dict[tuple[str, int], list[tuple[float, float]]], list[str], list[dict]]:
    definitions = ROOT / "ops" / "definitions"
    current_definitions = os.environ.get("ECCODES_DEFINITION_PATH", "/MEMFS/definitions")
    os.environ["ECCODES_DEFINITION_PATH"] = f"{definitions}:{current_definitions}"
    import eccodes

    profile_dir = CASE / "bufr"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profiles: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    missing: list[str] = []
    sources: list[dict] = []
    for station in FOUS61_STATIONS:
        station_id = BUFR_STATION_IDS.get(station)
        if not station_id:
            missing.append(f"{station}: no NAM BUFR station number configured")
            continue
        name = f"bufr.{station_id}.{DATE}{CYCLE}"
        url = f"{BASE}/bufr.t{CYCLE}z/{name}"
        try:
            raw = get(url)
            path = profile_dir / f"{station}.bufr"
            path.write_bytes(raw)
            sources.append({"station": station, "url": url, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            with path.open("rb") as stream:
                while True:
                    handle = eccodes.codes_bufr_new_from_file(stream)
                    if handle is None:
                        break
                    try:
                        if int(eccodes.codes_get(handle, "dataCategory")) != 241:
                            continue
                        eccodes.codes_set(handle, "unpack", 1)
                        subsets = int(eccodes.codes_get(handle, "numberOfSubsets"))
                        arrays = {k: eccodes.codes_get_array(handle, k) for k in ("FTIM", "PRES", "TMDB")}
                        if subsets < 1 or len(arrays["PRES"]) % subsets:
                            raise ValueError(f"Unexpected NAM profile layout for {station}")
                        levels = len(arrays["PRES"]) // subsets
                        for subset in range(subsets):
                            lead = int(round(float(arrays["FTIM"][subset]) / 3600))
                            for level in range(levels):
                                ix = subset * levels + level
                                p_hpa = float(arrays["PRES"][ix]) / 100.0
                                temp_k = float(arrays["TMDB"][ix])
                                if math.isfinite(p_hpa) and math.isfinite(temp_k) and p_hpa > 0 and 150 < temp_k < 350:
                                    profiles[(station, lead)].append((p_hpa, temp_k))
                    finally:
                        eccodes.codes_release(handle)
        except Exception as exc:
            missing.append(f"{station}: NAM profile unavailable ({type(exc).__name__}: {exc})")
    return profiles, missing, sources


def index_records(index_text: str, descriptor: str) -> list[tuple[int, int | None, str]]:
    rows = []
    for line in index_text.splitlines():
        # NOMADS inventory lines begin with ``message:offset:d=DATE:...``.
        # Drop that date token before matching a GRIB field description such
        # as ``APCP:surface:0-3 hour acc fcst``.
        match = re.match(r"\d+:(\d+):d=\d+:(.*)", line)
        if match:
            rows.append((int(match.group(1)), line, match.group(2)))
    selected = []
    for i, (start, line, desc) in enumerate(rows):
        if desc.startswith(descriptor):
            end = rows[i + 1][0] - 1 if i + 1 < len(rows) else None
            selected.append((start, end, line))
    return selected


def fetch_ptt(leads: list[int]) -> tuple[dict[tuple[str, int], int], list[str], list[dict]]:
    import numpy as np
    from eccodes import codes_get, codes_get_array, codes_get_values, codes_grib_new_from_file, codes_release

    index_cache: dict[int, list[str]] = {}
    records: dict[tuple[int, int, int], tuple[str, int, int | None, str, tuple[int, int]]] = {}
    warnings: list[str] = []
    source_rows: list[dict] = []
    for lead in leads:
        if lead <= 0:
            continue
        first, last = lead - 3, lead
        for endpoint, start, end in ((first, lead - 6, lead - 3), (last, lead - 3, lead)):
            if endpoint not in index_cache:
                filename = f"nam.t{CYCLE}z.awphys{endpoint:02d}.tm00.grib2"
                idx_url = f"{BASE}/{filename}.idx"
                index_cache[endpoint] = get(idx_url).decode("utf-8", errors="replace").splitlines()
            filename = f"nam.t{CYCLE}z.awphys{endpoint:02d}.tm00.grib2"
            descriptor = f"APCP:surface:{start}-{end} hour acc fcst"
            found = index_records("\n".join(index_cache[endpoint]), descriptor)
            if not found:
                warnings.append(f"PTT lead {lead}: missing NAM APCP interval {start}-{end} h")
                continue
            for offset, finish, index_line in found:
                records[(lead, endpoint, offset)] = (filename, offset, finish, index_line, (start, end))

    ptt_mm: dict[tuple[str, int], float] = defaultdict(float)
    ptt_intervals_seen: dict[tuple[str, int], set[tuple[int, int]]] = defaultdict(set)
    hashes = []
    for (lead, endpoint, offset), (filename, start, end, index_line, interval) in records.items():
        url = f"{BASE}/{filename}"
        raw = get(url, (start, end))
        if not raw.startswith(b"GRIB"):
            raise RuntimeError(f"NAM byte range did not contain a GRIB message: {filename}")
        hashes.append({"url": url, "byteRange": [start, end], "sha256": hashlib.sha256(raw).hexdigest(), "index": index_line})
        grib_path = CASE / "apcp" / f"{filename}.{start}.grib2"
        grib_path.parent.mkdir(parents=True, exist_ok=True)
        grib_path.write_bytes(raw)
        with grib_path.open("rb") as stream:
            handle = codes_grib_new_from_file(stream)
            if handle is None:
                raise RuntimeError(f"Could not decode NAM APCP message {filename} at {start}")
            try:
                lats = codes_get_array(handle, "latitudes")
                lons = codes_get_array(handle, "longitudes")
                values = codes_get_values(handle)
                unit = str(codes_get(handle, "units"))
                for station, (lat, lon) in FOUS61_STATIONS.items():
                    point = nearest_index(lats, lons, lat, lon)
                    value = float(values[point])
                    if not math.isfinite(value) or abs(value) > 1e20:
                        continue
                    if unit.lower() in {"kg m-2", "kg m**-2", "mm"}:
                        value_mm = value
                    else:
                        raise ValueError(f"Unexpected NAM APCP unit {unit!r}")
                    ptt_mm[(station, lead)] += value_mm
                    ptt_intervals_seen[(station, lead)].add(interval)
            finally:
                codes_release(handle)
    # Convert millimetres to FOUS hundredths of an inch, rounded to nearest code.
    # Never treat an unavailable three-hour interval as zero precipitation.
    ptt_codes = {
        key: int(math.floor(mm * 100.0 / 25.4 + 0.5))
        for key, mm in ptt_mm.items()
        if len(ptt_intervals_seen[key]) == 2
    }
    return ptt_codes, warnings, hashes


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CASE.mkdir(parents=True, exist_ok=True)
    cycle_name = f"{DATE}_{CYCLE}Z"
    candidates: list[dict] = []
    comparisons: list[dict] = []
    provenance: list[dict] = []
    result = {"available": False, "cycle": cycle_name, "message": "NAM comparison has not completed."}
    try:
        if OFFICIAL.exists():
            with OFFICIAL.open(newline="", encoding="utf-8") as f:
                official_rows = list(csv.DictReader(f))
        else:
            official_rows = []
        official = {(r["station"], int(r["forecast_hour"])): r for r in official_rows}
        leads = sorted({h for _, h in official if h > 0}) or list(range(6, 61, 6))

        profiles, missing, profile_sources = fetch_bufr_profiles()
        profile_source_by_station = {r["station"]: r["url"] for r in profile_sources}
        for (station, lead), profile in sorted(profiles.items()):
            if lead not in leads:
                continue
            ps_hpa = max(p for p, _ in profile)
            row = {"station": station, "forecast_hour": lead, "surface_pressure_estimate_hPa": round(ps_hpa, 2)}
            for field, (sig_bottom, sig_top) in SIGMA_LAYERS.items():
                mean_k = layer_mean(profile, ps_hpa * sig_bottom, ps_hpa * sig_top)
                row[f"{field}_layer_mean_C"] = "" if mean_k is None else int(math.floor(mean_k - 273.15 + 0.5))
                if mean_k is not None:
                    provenance.append({"station": station, "forecast_hour": lead, "field": field,
                                       "candidate": row[f"{field}_layer_mean_C"], "source": profile_source_by_station.get(station, ""),
                                       "method": f"NAM BUFR profile; pressure-weighted mean between sigma {sig_bottom:.3f} and {sig_top:.3f}; surface pressure estimated from lowest reported profile level"})
            candidates.append(row)
        for candidate in candidates:
            truth = official.get((candidate["station"], candidate["forecast_hour"]))
            if truth is None:
                continue
            for field, official_field in FOUS_TEMP_FIELDS.items():
                val = candidate.get(f"{field}_layer_mean_C", "")
                obs = fous_c(truth.get(official_field, ""))
                if val == "" or obs is None:
                    continue
                diff = int(val) - obs
                comparisons.append({"station": candidate["station"], "forecast_hour": candidate["forecast_hour"],
                                    "field": field, "candidate_C": int(val), "official_C_comparison_only": int(obs),
                                    "candidate_minus_official_C": int(diff)})

        # Keep the warning list defined even if APCP retrieval itself raises;
        # the summary builder below uses it to explain missing intervals.
        ptt_error = None
        ptt_codes, ptt_warnings, ptt_sources = {}, [], []
        try:
            ptt_codes, ptt_warnings, ptt_sources = fetch_ptt(leads)
            missing.extend(ptt_warnings)
        except Exception as exc:
            ptt_codes, ptt_sources = {}, []
            ptt_error = f"{type(exc).__name__}: {exc}"
            missing.append(f"PTT data unavailable: {ptt_error}")
        for (station, lead), code in sorted(ptt_codes.items()):
            candidates.append({"station": station, "forecast_hour": lead, "PTT_hundredths_in_candidate": code,
                               "PTT_source_method": "NAM non-overlapping 3-hour APCP intervals summed over six hours; nearest grid point"})
            first_interval = f"APCP:surface:{lead - 6}-{lead - 3} hour acc fcst"
            second_interval = f"APCP:surface:{lead - 3}-{lead} hour acc fcst"
            selected_sources = [s for s in ptt_sources if first_interval in s["index"] or second_interval in s["index"]]
            provenance.append({"station": station, "forecast_hour": lead, "field": "PTT", "candidate": code,
                               "source": "; ".join(f"{s['url']} bytes {s['byteRange']}" for s in selected_sources),
                               "method": "Sum NAM APCP 3-hour intervals covering this six-hour period; convert mm to hundredths of an inch; nearest grid point"})
            truth_code = official.get((station, lead), {}).get("precip_hundredths_in", "")
            if truth_code != "":
                diff = code - int(truth_code)
                comparisons.append({"station": station, "forecast_hour": lead, "field": "PTT",
                                    "candidate_hundredths_in": code, "official_hundredths_in_comparison_only": int(truth_code),
                                    "candidate_minus_official_hundredths_in": diff})

        fields = {field: [] for field in (*FOUS_TEMP_FIELDS, "PTT")}
        for row in comparisons:
            fields[row["field"]].append(row)
        summaries = {}
        for field, rows in fields.items():
            if field == "PTT":
                wet = [r for r in rows if int(r["candidate_hundredths_in"]) > 0 or int(r["official_hundredths_in_comparison_only"]) > 0]
                within = sum(abs(int(r["candidate_minus_official_hundredths_in"])) <= 10 for r in wet)
                max_diff = max((abs(int(r["candidate_minus_official_hundredths_in"])) for r in wet), default=None)
                largest_amount = max((int(r[k]) for r in wet for k in ("candidate_hundredths_in", "official_hundredths_in_comparison_only")), default=0)
                meaningful = largest_amount >= 10
                summaries[field] = {"periodsCompared": len(rows), "dryPeriods": len(rows) - len(wet),
                                     "wetPeriods": len(wet), "wetWithin010In": within,
                                     "largestWetDifferenceHundredthsIn": max_diff,
                                     "status": "useful wet sample" if meaningful else "inconclusive: no matched amount reached 0.10 in"}
                if not rows and ptt_warnings:
                    summaries[field]["status"] = "unavailable: NAM APCP intervals are missing"
                elif ptt_warnings:
                    summaries[field]["status"] = "partial: some NAM APCP intervals are missing"
                if wet:
                    largest = max(wet, key=lambda r: abs(int(r["candidate_minus_official_hundredths_in"])))
                    summaries[field]["largestWetPeriod"] = {
                        "station": largest["station"], "forecastHour": largest["forecast_hour"],
                        "candidateHundredthsIn": int(largest["candidate_hundredths_in"]),
                        "officialHundredthsIn": int(largest["official_hundredths_in_comparison_only"]),
                        "differenceHundredthsIn": int(largest["candidate_minus_official_hundredths_in"]),
                    }
                    # Include early wet periods so readers can see the actual
                    # amounts instead of relying on an overall dry/trace label.
                    summaries[field]["earlyWetPeriods"] = [
                        {"station": r["station"], "forecastHour": r["forecast_hour"],
                         "candidateHundredthsIn": int(r["candidate_hundredths_in"]),
                         "officialHundredthsIn": int(r["official_hundredths_in_comparison_only"]),
                         "differenceHundredthsIn": int(r["candidate_minus_official_hundredths_in"])}
                        for r in sorted(wet, key=lambda r: (int(r["forecast_hour"]), r["station"]))
                        if int(r["forecast_hour"]) <= 24
                    ][:6]
            else:
                diffs = [abs(int(r["candidate_minus_official_C"])) for r in rows]
                summaries[field] = {"samples": len(rows), "within1C": sum(d <= 1 for d in diffs),
                                    "exact": sum(d == 0 for d in diffs), "largestDifferenceC": max(diffs, default=None)}
        if ptt_error and official_rows:
            summaries["PTT"] = {"periodsCompared": 0, "dryPeriods": 0, "wetPeriods": 0,
                                 "wetWithin010In": 0, "largestWetDifferenceHundredthsIn": None,
                                 "status": "unavailable: NAM APCP retrieval or decoding failed"}

        result = {"available": bool(candidates), "comparisonAvailable": bool(comparisons),
                  "cycle": cycle_name, "updatedAt": datetime.now(timezone.utc).isoformat(),
                  "officialBulletinAvailable": bool(official_rows), "summaries": summaries if official_rows else {},
                  "candidateRows": len(candidates), "comparisonRows": len(comparisons),
                  "missingOrUnavailable": missing, "methodNote": "NAM-only candidates. T1/T3/T5 use pressure-weighted averages of the historical sigma layers from NAM BUFR profiles; pressure uses the lowest reported profile level. PTT sums the two non-overlapping NAM 3-hour APCP intervals and samples the nearest grid point. FOUS is read only for separate scoring.",
                  "rawProfileSources": profile_sources, "rawPttMessages": ptt_sources}
        if ptt_error:
            result["pttStatus"] = "unavailable"
        elif not ptt_codes:
            result["pttStatus"] = "no candidate values"
        candidate_file = OUT / f"FOUS61_{cycle_name}_NAM_candidate.csv"
        comparison_file = OUT / f"FOUS61_{cycle_name}_NAM_comparison.csv"
        provenance_file = OUT / f"FOUS61_{cycle_name}_NAM_provenance.csv"
        with candidate_file.open("w", newline="", encoding="utf-8") as f:
            cols = sorted({k for r in candidates for k in r})
            writer = csv.DictWriter(f, fieldnames=cols); writer.writeheader(); writer.writerows(candidates)
        with comparison_file.open("w", newline="", encoding="utf-8") as f:
            cols = sorted({k for r in comparisons for k in r})
            writer = csv.DictWriter(f, fieldnames=cols or ["station", "forecast_hour", "field"]); writer.writeheader(); writer.writerows(comparisons)
        with provenance_file.open("w", newline="", encoding="utf-8") as f:
            cols = ("station", "forecast_hour", "field", "candidate", "source", "method")
            writer = csv.DictWriter(f, fieldnames=cols); writer.writeheader(); writer.writerows(provenance)
        (CASE / "source_snapshot.json").write_text(json.dumps({"cycle": cycle_name, "profileSources": profile_sources,
            "pttMessages": ptt_sources, "warnings": missing}, indent=2) + "\n", encoding="utf-8")
        result["candidateFile"] = candidate_file.name
        result["comparisonFile"] = comparison_file.name
        result["provenanceFile"] = provenance_file.name
        if not candidates:
            result["message"] = "No NAM candidate values could be decoded for this cycle; see missingOrUnavailable."
        elif not official_rows:
            result["message"] = "NAM candidate collected; waiting for the matching official FOUS bulletin."
        elif not comparisons:
            result["message"] = "NAM candidate and official bulletin were collected, but no common valid field/time pairs were available."
    except Exception as exc:
        result = {"available": False, "cycle": cycle_name, "updatedAt": datetime.now(timezone.utc).isoformat(),
                  "message": f"NAM comparison unavailable: {type(exc).__name__}: {exc}"}
        print(result["message"])
    # A scheduled run can briefly lack matching BUFR/APCP files (or hit a
    # transient decode error). Do not replace a useful prior NAM comparison
    # with an empty/error message; keep the last verified comparison visible.
    if not result.get("comparisonAvailable") and SITE_RESULT.exists():
        try:
            previous = json.loads(SITE_RESULT.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        if previous.get("comparisonAvailable"):
            print(f"No new usable NAM comparison for {cycle_name}; keeping the previous verified comparison for {previous.get('cycle')}.")
            result = previous
    SITE_RESULT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("rawProfileSources", "rawPttMessages")}, indent=2))


if __name__ == "__main__":
    main()
