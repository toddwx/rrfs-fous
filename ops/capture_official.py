#!/usr/bin/env python3
"""Save Albany FOUS61 only when its bulletin matches this RRFS initialization."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from runtime import CYCLE, DATA, DATE, OFFICIAL, ROOT

URL = "https://www.atmos.albany.edu/gopher-local/albany/FOUS61"


def main() -> dict:
    target_raw = DATA / "official" / f"FOUS61_{DATE}_{CYCLE}Z.txt"
    target_meta = DATA / "official" / f"FOUS61_{DATE}_{CYCLE}Z_source.json"
    target_raw.parent.mkdir(parents=True, exist_ok=True)
    result: dict = {"available": False, "reason": "Albany FOUS has not matched this run yet."}
    archive = ROOT / "site" / "data" / "nam_archive"
    archive_raw = archive / f"FOUS61_{DATE}_{CYCLE}Z.txt"
    archive_meta = archive / f"FOUS61_{DATE}_{CYCLE}Z_source.json"
    if archive_raw.exists():
        try:
            body = archive_raw.read_bytes()
            archived = json.loads(archive_meta.read_text(encoding="utf-8")) if archive_meta.exists() else {}
            target_raw.write_bytes(body)
            decoded = DATA / "official" / f"FOUS61_{DATE}_{CYCLE}Z_decoded.csv"
            subprocess.run([sys.executable, str(Path(__file__).with_name("decode_fous61.py")), str(target_raw), "--csv", str(decoded)], check=True)
            result = {**archived, "available": True, "captureMethod": "Previously archived Albany bulletin", "sha256": hashlib.sha256(body).hexdigest()}
            target_meta.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result))
            return result
        except Exception as exc:
            result = {"available": False, "reason": f"Archived Albany FOUS could not be decoded: {type(exc).__name__}: {exc}"}
    request = urllib.request.Request(URL + f"?refresh={datetime.now(timezone.utc).timestamp():.0f}", headers={
        "User-Agent": "FOUS61-RRFS-public-page/1.0", "Cache-Control": "no-cache",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            last_modified = response.headers.get("Last-Modified")
        text = body.decode("ascii", errors="replace")
        head = re.search(r"FOUS61\s+KWNO\s+(\d{2})(\d{2})", text)
        expected = datetime.strptime(DATE + CYCLE, "%Y%m%d%H")
        model_line = f"OUTPUT FROM NAM {CYCLE}Z {expected.strftime('%b').upper()} {expected.day:02d} {expected.year % 100:02d}"
        if not head or head.group(1) != expected.strftime("%d") or head.group(2) != CYCLE or model_line not in text.upper():
            result = {"available": False, "reason": "Albany feed responded, but its newest bulletin does not match the RRFS cycle."}
        else:
            target_raw.write_bytes(body)
            decoded = DATA / "official" / f"FOUS61_{DATE}_{CYCLE}Z_decoded.csv"
            subprocess.run([sys.executable, str(Path(__file__).with_name("decode_fous61.py")), str(target_raw), "--csv", str(decoded)], check=True)
            result = {
                "available": True, "sourceUrl": URL, "cycleHeader": head.group(0),
                "lastModified": last_modified, "retrievedAt": datetime.now(timezone.utc).isoformat(),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
    except Exception as exc:  # Optional control; RRFS-only creation must continue if Albany is down.
        result = {"available": False, "reason": f"Albany FOUS check failed: {type(exc).__name__}: {exc}"}
    target_meta.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    main()
