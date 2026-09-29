#!/usr/bin/env python3
"""Refresh and publish one RRFS long-range run for the public FOUS-style page."""
from __future__ import annotations

import json
import os
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from runtime import CYCLE, DATE, ROOT, STATION_ROWS, cycle_label


def run(script: str) -> None:
    print(f"\n--- {script} ---", flush=True)
    subprocess.run([sys.executable, str(ROOT / "ops" / script)], cwd=ROOT, check=True)


def main() -> None:
    # Scheduled checks at 03:15/09:15/15:15/21:15 UTC map to long RRFS
    # cycles 00Z/06Z/12Z/18Z on the same UTC date.
    os.environ["FOUS_DATE"] = DATE
    os.environ["FOUS_CYCLE"] = CYCLE
    run("capture_official.py")  # Optional matching NAM control; never fills RRFS data.
    run("fetch_rrfs.py")
    run("fill_apcp.py")
    run("build_rhli.py")
    run("build_vvv.py")
    run("build_candidates.py")
    run("render.py")

    official_meta = ROOT / "work" / "data" / "official" / f"FOUS61_{DATE}_{CYCLE}Z_source.json"
    official = json.loads(official_meta.read_text()) if official_meta.exists() else {"available": False}
    case = ROOT / "work" / "data" / "model" / "cases" / f"{DATE}_{CYCLE}Z_rrfs_parallel"
    manifest = case / "selected_message_manifest.tsv"
    vvv_manifest = case / "rrfs_vvv_message_manifest.tsv"
    source = {
        "provider": "NOAA/NCEP RRFS parallel data",
        "baseUrl": f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/rrfs/para/rrfs.{DATE}/{CYCLE}/",
        "cycleUtc": f"{DATE}T{CYCLE}:00:00Z",
        "forecastLeadsHours": list(range(0, 85, 6)),
        "selectedMessageCount": sum(1 for _ in manifest.open()),
        "selectedManifestSha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "vvvMessageCount": sum(1 for _ in vvv_manifest.open()),
        "vvvManifestSha256": hashlib.sha256(vvv_manifest.read_bytes()).hexdigest(),
        "matchingAlbanyControl": official,
    }
    (ROOT / "site" / "data" / "source.json").write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    status = {
        "model": "RRFS estimate",
        "cycle": cycle_label(),
        "cycleUtc": f"{DATE}T{CYCLE}:00:00Z",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "forecastThroughHour": 60,
        "rrfsAvailableThroughHour": 84,
        "comparisonBulletinAvailable": bool(official.get("available")),
        "note": "Experimental forecast made from RRFS data, not an official NWS bulletin. T1, wind direction, precipitation, humidity, and other fields are estimates. Humidity and VVV are especially approximate. A matching NAM FOUS comparison is included only when Albany has posted the same cycle.",
    }
    (ROOT / "site" / "data" / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    (ROOT / "site" / "data" / "stations.json").write_text(json.dumps({"stations": STATION_ROWS}, indent=2) + "\n", encoding="utf-8")
    print(f"\nUpdated public bulletin for RRFS {cycle_label()} at {len(STATION_ROWS)} stations.", flush=True)


if __name__ == "__main__":
    main()
