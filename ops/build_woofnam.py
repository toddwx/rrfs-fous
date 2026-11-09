#!/usr/bin/env python3
"""Build a separate four-city FOUS-style pilot from public WOOF-NAM GRIB2."""
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
from datetime import datetime, timezone
from pathlib import Path

from eccodes import (codes_get, codes_get_array, codes_get_values,
                     codes_grib_new_from_file, codes_release)

ROOT = Path(__file__).resolve().parents[1]
STATIONS_PATH = ROOT / "site" / "data" / "stations.json"
STATIONS_ALL = json.loads(STATIONS_PATH.read_text(encoding="utf-8"))["stations"]
CITY_CODES = ("ALB", "BTV", "BOS", "LGA")
STATIONS = {r["code"]: (float(r["latitude"]), float(r["longitude"]))
            for r in STATIONS_ALL if r["code"] in CITY_CODES}
LEADS = tuple(range(0, 61, 6))
PROFILE_LEVELS = (1000, 975, 950, 925, 900, 875, 850, 825, 800, 775, 750, 725, 700)
WIND_LEVELS = (1000, 975, 950, 925)
HOURS = sorted(set(LEADS) | {h for lead in LEADS if lead for h in (lead - 1, lead + 1)})
from runtime import DATE, CYCLE
BASE = "https://woof-nam.recastsystems.com/woof-nam/12km/{}/{:02d}z/".format(DATE, int(CYCLE))
OUT = ROOT / "site" / "data" / "woofnam"
WORK = ROOT / "work" / "data" / "woofnam" / (DATE + "_" + CYCLE + "Z")


def get(url, byte_range=None):
    headers = {"User-Agent": "FOUS-style-WOOF-NAM-pilot/1.0"}
    if byte_range:
        headers["Range"] = "bytes={}-{}".format(*byte_range)
    request = urllib.request.Request(url, headers=headers)
    error = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
                if byte_range and response.status != 206:
                    raise RuntimeError("WOOF-NAM host did not honor a range request")
                return data
        except (TimeoutError, urllib.error.URLError, http.client.HTTPException, ConnectionError) as exc:
            error = exc
            if attempt == 3:
                break
            time.sleep(2 ** attempt)
    raise RuntimeError("Could not retrieve WOOF-NAM source: {}".format(error))


def round_away(value):
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


def nearest_code(u, v):
    direction = (270.0 - math.degrees(math.atan2(v, u))) % 360.0
    code = int(math.floor(direction / 10.0 + 0.5)) % 36
    return 36 if code == 0 else code


def interp(samples, target):
    samples = sorted((p, v) for p, v in samples if math.isfinite(p) and math.isfinite(v))
    for pressure, value in samples:
        if abs(pressure - target) < 1e-7:
            return value
    lower = [x for x in samples if x[0] < target]
    upper = [x for x in samples if x[0] > target]
    if not lower or not upper:
        return None
    p0, v0 = lower[-1]
    p1, v1 = upper[0]
    return v0 + (v1 - v0) * (target - p0) / (p1 - p0)


def layer_mean(profile, surface_hpa, surface_temp_k, sigma_bottom, sigma_top):
    samples = [(surface_hpa, surface_temp_k)] + [(p, v) for p, v in profile if p < surface_hpa]
    bottom, top = surface_hpa * sigma_bottom, surface_hpa * sigma_top
    low, high = interp(samples, top), interp(samples, bottom)
    if low is None or high is None:
        return None
    points = [(top, low)]
    points.extend((p, v) for p, v in samples if top < p < bottom)
    points.append((bottom, high))
    points.sort()
    return sum((p1-p0)*(v0+v1)/2.0 for (p0,v0),(p1,v1) in zip(points, points[1:]))/(bottom-top)


def idw4(lats, lons, vals, lat, lon):
    dx = ((lons - lon + 180.0) % 360.0 - 180.0) * math.cos(math.radians(lat))
    dy = lats - lat
    distances = (dx * dx + dy * dy) ** 0.5
    indices = distances.argpartition(3)[:4]
    if float(distances[indices].min()) < 1e-9:
        return float(vals[int(indices[distances[indices].argmin()])])
    weights = 1.0 / (distances[indices] ** 2)
    return float((vals[indices] * weights).sum() / weights.sum())


def index_lines(hour):
    if hour not in INDEX_CACHE:
        stem = "woof-nam.t{}z.awphys{:02d}.tm00.grib2".format(CYCLE, hour)
        data = get(BASE + stem + ".idx")
        path = WORK / (stem + ".idx")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        INDEX_CACHE[hour] = (stem, data.decode("utf-8", "replace").splitlines())
    return INDEX_CACHE[hour]


def select_line(hour, needle):
    stem, lines = index_lines(hour)
    parsed = []
    for line in lines:
        match = re.match(r"\d+:(\d+):(.*)", line)
        if match:
            parsed.append((int(match.group(1)), line))
    found = [(i, start, line) for i, (start, line) in enumerate(parsed) if needle in line]
    if len(found) != 1:
        raise RuntimeError("Expected one {} at f{:02d}; found {}".format(needle, hour, len(found)))
    i, start, line = found[0]
    end = parsed[i+1][0] - 1 if i+1 < len(parsed) else None
    if end is None:
        # This project uses records well before the file end; last-record fallback is not expected.
        raise RuntimeError("Selected GRIB message has no following byte offset")
    return stem, start, end, line


INDEX_CACHE = {}
VALUES = {}
MANIFEST = []


def sample(hour, field, needle, level=None, want_idw=False):
    key = (hour, field, str(level or ""))
    if key in VALUES:
        return VALUES[key]
    stem, start, end, description = select_line(hour, needle)
    raw_path = WORK / ("f{:02d}_{}_{}.grib2".format(hour, field, level or "surface"))
    raw = raw_path.read_bytes() if raw_path.exists() else get(BASE + stem, (start, end))
    if not raw.startswith(b"GRIB"):
        raise RuntimeError("Selected bytes were not a GRIB message: " + description)
    digest = hashlib.sha256(raw).hexdigest()
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw)
    values = {}
    with raw_path.open("rb") as stream:
        gid = codes_grib_new_from_file(stream)
        if gid is None:
            raise RuntimeError("ecCodes could not decode " + description)
        try:
            lats, lons, grid = codes_get_array(gid, "latitudes"), codes_get_array(gid, "longitudes"), codes_get_values(gid)
            metadata = {"shortName": codes_get(gid, "shortName"), "level": codes_get(gid, "level"),
                        "units": codes_get(gid, "units"), "stepRange": codes_get(gid, "stepRange")}
            for city, (lat, lon) in STATIONS.items():
                dx = (lons - lon + 180.0) % 360.0 - 180.0
                distances = (lats-lat)**2 + (dx*math.cos(math.radians(lat)))**2
                idx = int(distances.argmin())
                row = {"value": float(grid[idx]), "grid_lat": float(lats[idx]),
                       "grid_lon": float((lons[idx]+180.0)%360.0-180.0), **metadata}
                if want_idw:
                    row["idw4"] = idw4(lats, lons, grid, lat, lon)
                values[city] = row
        finally:
            codes_release(gid)
    MANIFEST.append({"forecastHour": hour, "file": stem, "byteRange": [start, end],
                     "sha256": digest, "indexRecord": description})
    VALUES[key] = values
    return values


def fetch(hour, field, level=None, want_idw=False):
    if field in ("t", "u", "v", "rh", "gh", "w", "4lftx", "tp"):
        aliases = {"t":"TMP", "u":"UGRD", "v":"VGRD", "rh":"RH", "gh":"HGT",
                   "w":"DZDT", "4lftx":"4LFTX", "tp":"APCP"}
        if field == "4lftx": needle = ":4LFTX:180-0 mb above ground:"
        elif field == "tp": raise ValueError("Precipitation requires interval-specific selector")
        else: needle = ":{}:{} mb:".format(aliases[field], level)
    else:
        aliases = {"sp":"PRES", "mslet":"MSLET", "t2":"TMP", "rh2":"RH",
                   "u10":"UGRD", "v10":"VGRD"}
        if field in ("t2", "rh2"): needle = ":{}:2 m above ground:".format(aliases[field])
        elif field in ("u10", "v10"): needle = ":{}:10 m above ground:".format(aliases[field])
        elif field == "mslet": needle = ":MSLET:mean sea level:"
        else: needle = ":PRES:surface:"
    return sample(hour, field, needle, level, want_idw)


def pcp(hour):
    if hour == 0:
        return {city: None for city in STATIONS}
    if hour % 12 == 6:
        start, end = hour - 6, hour
        accum = sample(hour, "tp", ":APCP:surface:{}-{} hour acc fcst:".format(start, end))
        return {city: max(0.0, row["value"]) for city, row in accum.items()}
    if hour == 12:
        start, end, prior_hour = 0, 12, 6
    else:
        start = hour - (12 if hour % 24 == 0 else 12)
        end, prior_hour = hour, hour - 6
        if hour % 24 == 0:
            start = hour - 12
        else:
            start = hour - 12
    total = sample(hour, "tp", ":APCP:surface:{}-{} hour acc fcst:".format(start, end))
    previous = pcp(prior_hour)
    return {city: max(0.0, total[city]["value"] - (previous[city] or 0.0)) for city in STATIONS}


def round_code(value):
    return int(math.copysign(math.floor(abs(value)+0.5), value))


def specific_humidity_from_rh(temp_k, rh_percent, pressure_pa=70000.0):
    """Estimate q from WOOF-NAM's available 700-mb temperature and RH."""
    tc = temp_k - 273.15
    es = 611.2 * math.exp(17.67 * tc / (tc + 243.5))
    vapor = max(0.0, min(100.0, rh_percent)) / 100.0 * es
    return 0.622 * vapor / (pressure_pa - 0.378 * vapor)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    # Retrieve GRIB message subsets by index; the 5.8 GB complete cycle is not downloaded.
    for lead in LEADS:
        for field, level in (("sp", None), ("mslet", None), ("t2", None), ("rh2", None),
                             ("u10", None), ("v10", None), ("4lftx", None),
                             ("rh", 700), ("rh", 500), ("gh", 500), ("gh", 1000),
                             ("w", 700), ("t", 700)):
            fetch(lead, field, level, want_idw=(field in ("w", "t") or (field == "rh" and level == 700)))
        for level in PROFILE_LEVELS:
            fetch(lead, "t", level)
        for level in WIND_LEVELS:
            fetch(lead, "u", level)
            fetch(lead, "v", level)
        pcp(lead)
    # VVV uses the same 700-mb conversion, four-point spatial estimate and 1:2:1 time average as RRFS.
    for lead in LEADS:
        if lead:
            for hour in (lead-1, lead+1):
                for field in ("w", "t"):
                    fetch(hour, field, 700, want_idw=True)
    rows, provenance = [], []
    for city in CITY_CODES:
        for lead in LEADS:
            getv = lambda f, lev=None, h=lead, spatial="value": fetch(h, f, lev, want_idw=(spatial=="idw4"))[city][spatial]
            ps = getv("sp") / 100.0
            t2 = getv("t2")
            prof = [(p, getv("t", p)) for p in PROFILE_LEVELS]
            t1 = layer_mean(prof, ps, t2, 1.000, 0.965)
            t3 = layer_mean(prof, ps, t2, 0.922, 0.872)
            t5 = layer_mean(prof, ps, t2, 0.816, 0.755)
            u_surface, v_surface = getv("u10"), getv("v10")
            u_profile = [(ps, u_surface)] + [(p, getv("u", p)) for p in WIND_LEVELS if p < ps]
            v_profile = [(ps, v_surface)] + [(p, getv("v", p)) for p in WIND_LEVELS if p < ps]
            top = ps - 35.0
            u_top, v_top = interp(u_profile, top), interp(v_profile, top)
            if u_top is None or v_top is None:
                u_mean = v_mean = None
            else:
                u_points = sorted([(ps,u_surface),(top,u_top)] + [(p,v) for p,v in u_profile if top<p<ps], reverse=True)
                v_points = sorted([(ps,v_surface),(top,v_top)] + [(p,v) for p,v in v_profile if top<p<ps], reverse=True)
                u_mean = sum((p0-p1)*(u0+u1)/2 for (p0,u0),(p1,u1) in zip(u_points,u_points[1:]))/35.0
                v_mean = sum((p0-p1)*(v0+v1)/2 for (p0,v0),(p1,v1) in zip(v_points,v_points[1:]))/35.0
            rh1, rh2, rh3 = getv("rh2"), getv("rh",700), getv("rh",500)
            mslet = getv("mslet") / 100.0
            thickness = (getv("gh",500)-getv("gh",1000))/10.0
            li = round_code(getv("4lftx"))
            li_code = li+100 if li < 0 else li
            wz = getv("w",700,lead,"idw4")
            if lead:
                w0 = fetch(lead-1,"w",700,True)[city]["idw4"]
                w2 = fetch(lead+1,"w",700,True)[city]["idw4"]
                wz = (w0+2*wz+w2)/4.0
            temp_v = getv("t",700,lead,"idw4")
            rh_v = getv("rh",700,lead,"idw4")
            q_v = specific_humidity_from_rh(temp_v, rh_v)
            density = 70000.0/(287.05*temp_v*(1.0+0.61*q_v))
            vvv = round_code(density*9.80665*wz*100.0)
            rain = pcp(lead)[city]
            ptt = None if rain is None else int(math.floor(rain/25.4*100.0+0.5))
            row = {"station":city,"forecast_hour":lead,"PTT_hundredths_in":ptt,
                   "R1":round_code(rh1),"R2":round_code(rh2),"R3":round_code(rh3),"VVV":vvv,"LI":li_code,
                   "PS_code":math.floor(mslet)%100,
                   "DD_code":nearest_code(u_mean,v_mean) if u_mean is not None else None,
                   "FF_kt":int(math.floor(math.hypot(u_mean,v_mean)*1.943844+0.5)) if u_mean is not None else None,
                   "HH_code":round_code(thickness)%100,
                   "T1_C":round_code(t1-273.15) if t1 is not None else None,
                   "T3_C":round_code(t3-273.15) if t3 is not None else None,
                   "T5_C":round_code(t5-273.15) if t5 is not None else None,
                   "surfacePressure_hPa":round(ps,1),"MSLET_hPa":round(mslet,1),"layerMethod":"same pressure-weighted sigma-layer average as RRFS pilot"}
            rows.append(row)
            provenance.append({"station":city,"forecast_hour":lead,
               "PTT":"WOOF-NAM APCP windows; derive trailing 6 h using direct 6 h buckets or subtraction from matching 12 h total",
               "R1":"RH at 2 m (single-level estimate)","R2":"RH at 700 mb (single-level estimate)","R3":"RH at 500 mb (single-level estimate)",
               "VVV":"700 mb DZDT and TMP; estimate specific humidity from 700 mb RH/T, four-point IDW and 1:2:1 time average",
               "LI":"4LFTX 180-0 mb above ground","PS":"MSLET, last two hPa digits",
               "DDFF":"pressure-weighted vector through lowest 35 mb; 10 m anchor plus 1000/975/950/925 mb winds",
               "HH":"500-1000 mb height thickness in decameters","T1":"pressure-weighted 1.000-0.965 sigma layer mean",
               "T3":"pressure-weighted 0.922-0.872 sigma layer mean","T5":"pressure-weighted 0.816-0.755 sigma layer mean"})
    OUT.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with (OUT/"candidate.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    with (OUT/"provenance.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(provenance[0])); w.writeheader(); w.writerows(provenance)
    (WORK/"selected_message_manifest.json").write_text(json.dumps(MANIFEST,indent=2)+"\n",encoding="utf-8")
    (OUT/"manifest.json").write_text(json.dumps(MANIFEST,indent=2)+"\n",encoding="utf-8")
    by={(r["station"],r["forecast_hour"]):r for r in rows}
    def two(x, cap=None):
        n=int(x); n=min(n,cap) if cap is not None else n
        return "{:02d}".format(n)
    def temp(x): return "{:02d}".format(int(x)%100)
    def vvvf(x):
        n=int(x)
        return "-{:02d}".format(min(abs(n),99)) if n<0 else "{:03d}".format(min(n,999))
    def initial(r):
        return "{}//{} {}{} {}{}{} {}{}{}".format(r["station"],"".join(two(r[k],99) for k in ("R1","R2","R3")),vvvf(r["VVV"]),two(r["LI"]),two(r["PS_code"]),two(r["DD_code"]),two(r["FF_kt"]),two(r["HH_code"]),temp(r["T1_C"]),temp(r["T3_C"])+temp(r["T5_C"]))
    def fcst(r):
        return "{:02d}{:03d}{} {}{} {}{}{} {}{}{}".format(r["forecast_hour"],r["PTT_hundredths_in"],"".join(two(r[k],99) for k in ("R1","R2","R3")),vvvf(r["VVV"]),two(r["LI"]),two(r["PS_code"]),two(r["DD_code"]),two(r["FF_kt"]),two(r["HH_code"]),temp(r["T1_C"]),temp(r["T3_C"])+temp(r["T5_C"]))
    text=["WOOF-NAM 12 KM FOUS-STYLE ESTIMATE","EXPERIMENTAL COMMUNITY MODEL; NOT THE NWS NAM OR AN OFFICIAL BULLETIN",
          "WOOF-NAM {}Z {} {}; DISPLAY THROUGH HOUR 60".format(CYCLE,datetime.strptime(DATE,"%Y%m%d").strftime("%b %d %y").upper(),"SAME CYCLE AS RRFS"),
          "T1/T3/T5=LAYER AVERAGE ESTIMATES; RH=2M/700MB/500MB SINGLE LEVEL",
          "PTT=PREVIOUS SIX-HOUR TOTAL; VVV IS A ROUGH ESTIMATE",
          "TTPTTR1R2R3 VVVLI PSDDFF HHT1T3T5   TTPTTR1R2R3 VVVLI PSDDFF HHT1T3T5"]
    for pair in (("ALB","BTV"),("BOS","LGA")):
        if len(text)>6: text.append("")
        text.append("   ".join(initial(by[(city,0)]) for city in pair))
        for lead in LEADS[1:]: text.append("   ".join(fcst(by[(city,lead)]) for city in pair))
    text.extend(["","Source: Recast WOOF-NAM public GRIB2 files. It uses NAM grid/physics but starts from GFS analysis, so it is a different model run from the retired operational NAM."])
    (OUT/"latest.txt").write_text("\n".join(text)+"\n",encoding="ascii")
    cycle_utc="{}T{}:00:00Z".format(DATE,CYCLE)
    status={"model":"WOOF-NAM estimate","cycle":datetime.strptime(DATE+CYCLE,"%Y%m%d%H").strftime("%HZ %b %d, %Y"),
            "cycleUtc":cycle_utc,"updatedAt":datetime.now(timezone.utc).isoformat(),"forecastThroughHour":60,
            "provider":"Recast WOOF-NAM 12 km GRIB2","baseUrl":BASE,"stationCodes":list(CITY_CODES),
            "sourceNote":"Independent NAM-style model run. Uses NAM grid and physics but starts from GFS analysis; it is not the retired operational NAM forecast and is not an official NWS product.",
            "sameMethodNote":"Temperature-layer averages, 35 mb wind estimates, and single-level humidity choices use the same reconstruction choices as the RRFS pilot. VVV is less exact here because 700-mb specific humidity is estimated from the available RH and temperature rather than read directly.",
            "selectedMessageCount":len(MANIFEST),"selectedMessagesSha256":hashlib.sha256((WORK/"selected_message_manifest.json").read_bytes()).hexdigest()}
    (OUT/"status.json").write_text(json.dumps(status,indent=2)+"\n",encoding="utf-8")
    (OUT/"source.json").write_text(json.dumps({"provider":"Recast WOOF-NAM 12 km","cycleUtc":cycle_utc,"forecastHours":list(LEADS),"selectedMessageCount":len(MANIFEST),"manifest":"site/data/woofnam/manifest.json","officialNamValuesUsed":False},indent=2)+"\n",encoding="utf-8")
    print("Built WOOF-NAM {}Z {} pilot for {} from {} selected GRIB messages".format(CYCLE,DATE,",".join(CITY_CODES),len(MANIFEST)))


if __name__ == "__main__":
    main()
