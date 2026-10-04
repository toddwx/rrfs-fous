#!/usr/bin/env python3
"""Add independently sampled NAM GRIB fields to the same-cycle FOUS comparison.

Official FOUS is read only after candidates have been calculated, for scoring.
The selected NAM messages and their provenance are preserved under the cycle case.
"""
from __future__ import annotations

import csv, hashlib, json, math, re, urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from eccodes import (codes_get, codes_get_array, codes_get_values,
                    codes_grib_new_from_file, codes_grib_multi_support_on,
                    codes_release)
from runtime import CYCLE, DATA, DATE, OFFICIAL, ROOT, STATIONS

BASE = f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/nam/prod/nam.{DATE}"
CASE = DATA / "model" / "cases" / f"{DATE}_{CYCLE}Z_nam"
OUT = DATA / "comparisons"
OLD_CANDIDATE = OUT / f"FOUS61_{DATE}_{CYCLE}Z_NAM_candidate.csv"
FIELDS = {
    "R1": ("RH:2 m above ground", "r1_pct"),
    "R2": ("RH:700 mb", "r2_pct"),
    "R3": ("RH:500 mb", "r3_pct"),
    "LI": ("4LFTX:180-0 mb above ground", "li_code"),
    "VVV": ("VVEL:700 mb", "vvv_code"),
    "PS": ("PRMSL:mean sea level", "ps_code"),
    "SP": ("PRES:surface", None),
    "H500": ("HGT:500 mb", None),
    "H1000": ("HGT:1000 mb", None),
    "U10": ("UGRD:10 m above ground", None),
    "V10": ("VGRD:10 m above ground", None),
    **{f"U{p}": (f"UGRD:{p} mb", None) for p in (1000, 975, 950, 925)},
    **{f"V{p}": (f"VGRD:{p} mb", None) for p in (1000, 975, 950, 925)},
}

def get(url: str, byte_range: tuple[int, int] | None = None) -> bytes:
    headers = {"User-Agent": "FOUS61-NAM-research/1.0"}
    if byte_range:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as res:
        data = res.read()
        if byte_range and res.status != 206:
            raise RuntimeError(f"Byte-range request not honored (HTTP {res.status})")
        return data

def nearest(lats, lons, lat, lon):
    import numpy as np
    dx = (lons - lon + 180) % 360 - 180
    return int(np.nanargmin((lats-lat)**2 + (dx*math.cos(math.radians(lat)))**2))

def records(index: str):
    parsed=[]
    for line in index.splitlines():
        m=re.match(r"\d+(?:\.\d+)?:([0-9]+):d=\d+:(.*)",line)
        if m: parsed.append((int(m.group(1)),m.group(2),line))
    starts=sorted(set(x[0] for x in parsed))
    next_start={a:(starts[i+1]-1 if i+1<len(starts) else None) for i,a in enumerate(starts)}
    return [(off,next_start[off],desc,line) for off,desc,line in parsed]

def rint(x): return int(math.floor(x+0.5))
def signed_li(x):
    val=int(math.copysign(math.floor(abs(x)+0.5),x))
    return val+100 if val<0 else val
def wind_code(u,v):
    deg=(270-math.degrees(math.atan2(v,u)))%360
    code=rint(deg/10)%36
    return 36 if code==0 else code
def interp(points,p):
    points=sorted((x,y) for x,y in points if math.isfinite(x) and math.isfinite(y))
    for x,y in points:
        if abs(x-p)<1e-6:return y
    for (p0,v0),(p1,v1) in zip(points,points[1:]):
        if p0<=p<=p1:return v0+(v1-v0)*(p-p0)/(p1-p0)
    return None
def wind_layer(values, component, sp):
    surf=values.get("U10" if component=="u" else "V10")
    if surf is None or sp is None:return None
    samples=[(sp,surf)]
    for p in (1000,975,950,925):
        v=values.get(f"{component.upper()}{p}")
        if v is not None and p<sp:samples.append((float(p),v))
    top=sp-35
    vt=interp(samples,top)
    if vt is None:return None
    points=[(sp,surf),(top,vt)]+[(p,v) for p,v in samples if top<p<sp]
    points.sort(reverse=True)
    return sum((p0-p1)*(v0+v1)/2 for (p0,v0),(p1,v1) in zip(points,points[1:]))/35

def main():
    if not OFFICIAL.exists():
        print(f"No matching official FOUS bulletin is available for {DATE} {CYCLE}Z; preserving any prior comparison.")
        return
    # NAM's U/V components can share a single multi-field GRIB message.
    codes_grib_multi_support_on()
    OUT.mkdir(parents=True,exist_ok=True); CASE.mkdir(parents=True,exist_ok=True)
    with OFFICIAL.open(newline="",encoding="utf-8") as f: official={(r["station"],int(r["forecast_hour"])):r for r in csv.DictReader(f)}
    # Carry forward candidate values already built by the trusted NAM BUFR/APCP routine.
    previous={}
    if OLD_CANDIDATE.exists():
        for row in csv.DictReader(OLD_CANDIDATE.open(newline="",encoding="utf-8")):
            key=(row["station"],int(row["forecast_hour"]))
            merged=previous.setdefault(key,{})
            merged.update({k:v for k,v in row.items() if v not in (None,"")})
    leads=sorted(h for _,h in official if h>0)
    messages_dir=CASE/"full_fields"; messages_dir.mkdir(exist_ok=True)
    vals=defaultdict(dict); provenance=[]; missing=[]; manifest=[]
    for lead in leads:
        name=f"nam.t{CYCLE}z.awphys{lead:02d}.tm00.grib2"; url=f"{BASE}/{name}"
        try:
            idx_url=url+".idx"; idx_path=CASE/(name+".idx")
            if idx_path.exists(): index=idx_path.read_text(encoding="utf-8")
            else:
                index=get(idx_url).decode("utf-8",errors="replace"); idx_path.write_text(index,encoding="utf-8")
            parsed=records(index)
            wanted=[]
            for key,(needle,_) in FIELDS.items():
                matches=[r for r in parsed if r[2].startswith(needle+":")]
                if not matches:
                    missing.append(f"F{lead:02d} {key}: NAM GRIB index has no {needle}")
                wanted.extend((key,r) for r in matches)
            by_offset=defaultdict(list)
            for key,r in wanted: by_offset[r[0]].append((key,r))
            for offset, selected in by_offset.items():
                end=selected[0][1][1]
                saved=messages_dir/f"f{lead:03d}_{offset}.grib2"
                block=saved.read_bytes() if saved.exists() else get(url,(offset,end))
                if not saved.exists(): saved.write_bytes(block)
                digest=hashlib.sha256(block).hexdigest()
                manifest.append({"lead":lead,"url":url,"byteRange":[offset,end],"sha256":digest,
                                 "indexLines":[x[1][3] for x in selected]})
                with saved.open("rb") as stream:
                    while True:
                        gid=codes_grib_new_from_file(stream)
                        if gid is None:break
                        try:
                            short=str(codes_get(gid,"shortName")).lower()
                            level=str(codes_get(gid,"level"))
                            level_type=str(codes_get(gid,"typeOfLevel"))
                            lats=codes_get_array(gid,"latitudes"); lons=codes_get_array(gid,"longitudes"); data=codes_get_values(gid)
                            units=str(codes_get(gid,"units"))
                            for key,(needle,_) in FIELDS.items():
                                match=next((r for k,r in selected if k==key),None)
                                if not match:continue
                                desc=match[2]
                                expected_short={"RH":"2r" if key=="R1" else "r","4LFTX":"4lftx","VVEL":"w","PRMSL":"prmsl","HGT":"gh","UGRD":"10u" if key=="U10" else "u","VGRD":"10v" if key=="V10" else "v","PRES":"sp"}.get(desc.split(":",1)[0],"")
                                if short != expected_short:continue
                                # Confirm pressure/height keys so a shared byte range cannot mislabel its sibling.
                                expected_level=re.search(r":(\d+) mb",desc)
                                if expected_level and level!=expected_level.group(1):continue
                                if "2 m above ground" in desc and level_type!="heightAboveGround":continue
                                if "4LFTX:" in desc and level_type!="pressureFromGroundLayer":continue
                                if key.startswith("U") and short!=("10u" if key=="U10" else "u"):continue
                                if key in {"V10","V1000","V975","V950","V925"} and short!=("10v" if key=="V10" else "v"):continue
                                for station,(lat,lon) in STATIONS.items():
                                    point=nearest(lats,lons,lat,lon); val=float(data[point])
                                    if math.isfinite(val) and abs(val)<1e20:
                                        vals[(station,lead)][key]=val
                                        provenance.append({"station":station,"forecast_hour":lead,"field":key,
                                            "raw_value":val,"units":units,"source":url,
                                            "byte_range":f"{offset}-{end}","sha256":digest,
                                            "method":"nearest NAM grid point; selected GRIB message from NOMADS index"})
                        finally: codes_release(gid)
        except Exception as exc:
            missing.append(f"F{lead:02d}: NAM GRIB retrieval/decoding failed ({type(exc).__name__}: {exc})")

    candidates=[]; comparisons=[]
    for (station,lead),v in sorted(vals.items()):
        base=dict(previous.get((station,lead),{})); row={"station":station,"forecast_hour":lead,**base}
        # Interpretable, deliberately provisional field recipes.
        for key,out in (("R1","R1_candidate_pct"),("R2","R2_candidate_pct"),("R3","R3_candidate_pct")):
            row[out]=rint(v[key]) if key in v else ""
        row["LI_candidate_code"]=signed_li(v["LI"]) if "LI" in v else ""
        # NAM VVEL is pressure vertical velocity in Pa/s. FOUS VVV uses tenths of microbar/s,
        # where descent is negative: -omega * 100 converts to the encoded signed magnitude.
        row["VVV_candidate_code"]=rint(-v["VVV"]*100) if "VVV" in v else ""
        row["PS_candidate_code"]=int(math.floor((v["PS"]/100)%100+0.5)) if "PS" in v else ""
        if "H500" in v and "H1000" in v: row["HH_candidate_code"]=rint((v["H500"]-v["H1000"])/10)%100
        else: row["HH_candidate_code"]=""
        sp=v.get("SP")
        if sp is not None: sp=sp/100
        u=wind_layer(v,"u",sp); w=wind_layer(v,"v",sp)
        row["DD_candidate_lowest35mb_code"]=wind_code(u,w) if u is not None and w is not None else ""
        row["FF_candidate_lowest35mb_kt"]=rint(math.hypot(u,w)*1.943844) if u is not None and w is not None else ""
        row["candidate_notes"]="RH single-level proxies; LI=4LFTX; VVV direct 700-mb NAM omega; PS=MSLP last two digits; DD/FF pressure-weighted wind vector through lowest 35 mb; HH 500–1000-mb thickness. Experimental recipes."
        candidates.append(row)
        truth=official.get((station,lead),{})
        field_map={"R1":("R1_candidate_pct","r1_pct"),"R2":("R2_candidate_pct","r2_pct"),"R3":("R3_candidate_pct","r3_pct"),
          "LI":("LI_candidate_code","li_code"),"VVV":("VVV_candidate_code","vvv_code"),"PS":("PS_candidate_code","ps_code"),
          "HH":("HH_candidate_code","thickness_code"),"DD":("DD_candidate_lowest35mb_code","wind_dir_tens_deg"),"FF":("FF_candidate_lowest35mb_kt","wind_speed_kt")}
        for field,(ck,ok) in field_map.items():
            cv=row.get(ck,""); ov=truth.get(ok,"")
            if cv=="" or ov=="":continue
            if field=="LI":
                cand_signed=int(cv)-100 if int(cv)>=50 else int(cv)
                off_code=int(ov); off_signed=off_code-100 if off_code>=50 else off_code
                cv,ov,diff=cand_signed,off_signed,cand_signed-off_signed
            else: diff=int(cv)-int(ov)
            if field=="DD":
                diff=((int(cv)%36)-(int(ov)%36)+18)%36-18
            comparisons.append({"station":station,"forecast_hour":lead,"field":field,"candidate":cv,
              "official_comparison_only":ov,"candidate_minus_official":diff,
              "difference_unit":"degrees circular" if field=="DD" else "C" if field=="LI" else "FOUS code" if field in ("VVV","PS","HH") else "% RH" if field.startswith("R") else "knots",
              "candidate_method":row["candidate_notes"]})
        # Existing NAM BUFR layer and APCP candidate values are included as originally calculated.
        for field,ck,ok,unit in (("T1","T1_layer_mean_C","t1_code","C"),("T3","T3_layer_mean_C","t3_code","C"),("T5","T5_layer_mean_C","t5_code","C")):
            cv=row.get(ck,""); ov=truth.get(ok,"")
            if cv=="" or ov=="":continue
            code=int(ov); temp=code-100 if code>=50 else code; diff=int(cv)-temp
            comparisons.append({"station":station,"forecast_hour":lead,"field":field,"candidate":cv,"official_comparison_only":temp,
              "candidate_minus_official":diff,"difference_unit":unit,"candidate_method":"NAM BUFR pressure-weighted historical sigma-layer mean; existing independently built candidate"})
        cv=row.get("PTT_hundredths_in_candidate",""); ov=truth.get("precip_hundredths_in","")
        if cv!="" and ov!="":
            comparisons.append({"station":station,"forecast_hour":lead,"field":"PTT","candidate":cv,"official_comparison_only":ov,
              "candidate_minus_official":int(cv)-int(ov),"difference_unit":"hundredths inch","candidate_method":"Existing NAM-only, non-overlapping APCP intervals"})

    # Save clear full-field records; the official comparisons remain a separate table.
    def write(name,rows):
        path=OUT/name
        keys=list(dict.fromkeys(k for r in rows for k in r)) or ["station","forecast_hour"]
        with path.open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(rows)
        return path
    cycle=f"{DATE}_{CYCLE}Z"
    cand_path=write(f"FOUS61_{cycle}_NAM_full_candidate.csv",candidates)
    comp_path=write(f"FOUS61_{cycle}_NAM_full_comparison.csv",comparisons)
    prov_path=write(f"FOUS61_{cycle}_NAM_full_provenance.csv",provenance)
    (CASE/"full_fields_manifest.json").write_text(json.dumps({"cycle":cycle,"retrievedAt":datetime.now(timezone.utc).isoformat(),"messages":manifest,"missingOrUnavailable":missing},indent=2)+"\n",encoding="utf-8")
    # Field summaries express the user-relevant tolerances and sample size.
    grouped=defaultdict(list)
    for r in comparisons:grouped[r["field"]].append(r)
    summaries={}
    for field,rows in grouped.items():
        diffs=[int(r["candidate_minus_official"]) for r in rows]
        if field=="PTT":
            wet=[r for r in rows if int(r["candidate"])>0 or int(r["official_comparison_only"])>0]
            summaries[field]={"compared":len(rows),"dry":len(rows)-len(wet),"wet":len(wet),"within010in":sum(abs(int(r["candidate_minus_official"]))<=10 for r in wet),"largestWetDifferenceHundredthsIn":max((abs(int(r["candidate_minus_official"])) for r in wet),default=None)}
            if wet:
                largest=max(wet,key=lambda r:abs(int(r["candidate_minus_official"])))
                summaries[field]["largestWetPeriod"]={"station":largest["station"],"forecastHour":int(largest["forecast_hour"]),"NAM":int(largest["candidate"]),"officialFOUS":int(largest["official_comparison_only"]),"difference":int(largest["candidate_minus_official"])}
                summaries[field]["wetPeriodsThrough24h"]=[{"station":r["station"],"forecastHour":int(r["forecast_hour"]),"NAM":int(r["candidate"]),"officialFOUS":int(r["official_comparison_only"]),"difference":int(r["candidate_minus_official"])} for r in sorted(wet,key=lambda x:(int(x["forecast_hour"]),x["station"])) if int(r["forecast_hour"])<=24]
        elif field in ("T1","T3","T5"): summaries[field]={"samples":len(rows),"within1C":sum(abs(d)<=1 for d in diffs),"exact":sum(d==0 for d in diffs),"largestDifferenceC":max(map(abs,diffs),default=None)}
        elif field=="DD": summaries[field]={"samples":len(rows),"within20deg":sum(abs(d)<=2 for d in diffs),"exact":sum(d==0 for d in diffs),"largestCircularDifferenceDeg":max((abs(d)*10 for d in diffs),default=None)}
        elif field=="VVV": summaries[field]={"samples":len(rows),"within10Codes":sum(abs(d)<=10 for d in diffs),"exact":sum(d==0 for d in diffs),"largestAbsoluteDifference":max(map(abs,diffs),default=None),"acceptedToleranceCodes":10}
        else: summaries[field]={"samples":len(rows),"exact":sum(d==0 for d in diffs),"largestAbsoluteDifference":max(map(abs,diffs),default=None)}
    report={"cycle":cycle,"createdAt":datetime.now(timezone.utc).isoformat(),"summaries":summaries,"missingOrUnavailable":missing,
      "candidateFile":cand_path.name,"comparisonFile":comp_path.name,"provenanceFile":prov_path.name,
      "candidateUsesOfficialValues":False,"caveat":"Values are NAM-only candidates. Several non-temperature methods are experimental proxies, not confirmed FOUS recipes."}
    (OUT/f"FOUS61_{cycle}_NAM_full_summary.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    # Add the expanded study to the public status record, preserving the existing
    # temperature/PTT score calculation from refresh_nam_comparison.py.
    site_result=ROOT/"site"/"data"/"nam_comparison.json"
    if site_result.exists():
        try:
            visible=json.loads(site_result.read_text(encoding="utf-8"))
            if visible.get("cycle")==cycle:
                visible["additionalFieldSummaries"]=summaries
                visible["additionalFieldNote"]="Experimental NAM-only proxies. RH uses single pressure levels, LI uses 4LFTX, VVV uses 700-mb pressure velocity, wind uses a pressure-weighted lowest-35-mb vector, PS uses sea-level pressure, and HH uses 500–1000-mb thickness. These methods are not confirmed FOUS recipes."
                visible["additionalFieldsUpdatedAt"]=report["createdAt"]
                visible["additionalFieldsAvailable"]=True
                site_result.write_text(json.dumps(visible,indent=2)+"\n",encoding="utf-8")
        except (OSError,json.JSONDecodeError):
            pass
    print(json.dumps(report,indent=2))

if __name__=="__main__":main()
