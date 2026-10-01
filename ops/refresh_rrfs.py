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
    layer_levels = (725, 750, 775, 825, 850, 875)
    with manifest.open(encoding="utf-8") as source_manifest:
        manifest_lines = source_manifest.read().splitlines()
    source = {
        "provider": "NOAA/NCEP RRFS parallel data",
        "baseUrl": f"https://nomads.ncep.noaa.gov/pub/data/nccf/com/rrfs/para/rrfs.{DATE}/{CYCLE}/",
        "cycleUtc": f"{DATE}T{CYCLE}:00:00Z",
        "forecastLeadsHours": list(range(0, 85, 6)),
        "selectedMessageCount": len(manifest_lines),
        "selectedManifestSha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "stationCount": len(STATION_ROWS),
        "stationValueCount": sum(1 for _ in (case / "nearest_grid_values.csv").open(encoding="utf-8")) - 1,
        "temperatureLayerLevelsMb": list(layer_levels),
        "temperatureLayerMessageCount": sum(
            1 for line in manifest_lines if any(f":TMP:{level} mb:" in line for level in layer_levels)
        ),
        "candidateCsv": f"work/data/comparisons/FOUS61_{DATE}_{CYCLE}Z_RRFS_parallel_candidate.csv",
        "comparisonCsv": f"work/data/comparisons/FOUS61_{DATE}_{CYCLE}Z_RRFS_parallel_comparison.csv",
        "provenanceCsv": f"work/data/comparisons/FOUS61_{DATE}_{CYCLE}Z_RRFS_parallel_provenance.csv",
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
        "note": "Experimental RRFS estimate, not an official NWS bulletin. T1, T3, and T5 use estimated averages across their FOUS layers. Other fields are approximate, especially humidity and vertical motion. A matching NAM FOUS bulletin is kept separate for comparison.",
    }
    (ROOT / "site" / "data" / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    (ROOT / "site" / "data" / "stations.json").write_text(json.dumps({"stations": STATION_ROWS}, indent=2) + "\n", encoding="utf-8")
    print(f"\nUpdated public bulletin for RRFS {cycle_label()} at {len(STATION_ROWS)} stations.", flush=True)


if __name__ == "__main__":
    main()
