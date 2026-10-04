#!/usr/bin/env python3
"""Archive the current Albany FOUS61 bulletin under its own model cycle."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from runtime import ROOT

URL = "https://www.atmos.albany.edu/gopher-local/albany/FOUS61"
ARCHIVE = ROOT / "site" / "data" / "nam_archive"


def main() -> None:
    request = urllib.request.Request(
        URL + f"?refresh={datetime.now(timezone.utc).timestamp():.0f}",
        headers={"User-Agent": "FOUS61-RRFS-public-page/1.0", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        last_modified = response.headers.get("Last-Modified")

    text = body.decode("ascii", errors="replace")
    header = re.search(r"FOUS61\s+KWNO\s+(\d{2})(\d{2})", text)
    model = re.search(r"OUTPUT\s+FROM\s+NAM\s+(\d{2})Z\s+([A-Z]{3})\s+(\d{1,2})\s+(\d{2})", text, re.I)
    if not header or not model or header.group(1) != model.group(3).zfill(2) or header.group(2) != model.group(1):
        raise ValueError("Albany response did not contain a consistent NAM FOUS61 cycle header")

    year = 2000 + int(model.group(4))
    month = datetime.strptime(model.group(2).upper(), "%b").month
    cycle = f"{year:04d}{month:02d}{int(model.group(3)):02d}_{model.group(1)}Z"
    digest = hashlib.sha256(body).hexdigest()
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    raw_path = ARCHIVE / f"FOUS61_{cycle}.txt"
    meta_path = ARCHIVE / f"FOUS61_{cycle}_source.json"
    previous = {}
    try:
        previous = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass

    raw_path.write_bytes(body)
    metadata = {
        "available": True,
        "cycle": cycle,
        "sourceUrl": URL,
        "cycleHeader": header.group(0),
        "lastModified": last_modified,
        "firstCapturedAt": previous.get("firstCapturedAt", datetime.now(timezone.utc).isoformat()),
        "sha256": digest,
    }
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"archived": True, "cycle": cycle, "sha256": digest}))


if __name__ == "__main__":
    main()
