"""Cached HTTP download helper shared by all data-source modules.

Downloads are large government files that rarely change, so we cache them in
data_raw/ and only re-fetch when missing (or force=True).
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

from . import config

_HEADERS = {"User-Agent": "Mozilla/5.0 (melb-scorer data build)"}


def fetch(url: str, filename: str | None = None, force: bool = False) -> Path:
    """Download ``url`` into data_raw/ and return the local path (cached)."""
    config.DATA_RAW.mkdir(parents=True, exist_ok=True)
    name = filename or url.split("/")[-1].split("?")[0]
    dest = config.DATA_RAW / name
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"  cached  {name} ({dest.stat().st_size/1e6:.1f} MB)")
        return dest

    print(f"  downloading {name} ...", flush=True)
    with requests.get(url, headers=_HEADERS, stream=True, timeout=120) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
        tmp.replace(dest)
    print(f"  downloaded  {name} ({dest.stat().st_size/1e6:.1f} MB)")
    return dest


def fetch_wayback(url: str, filename: str, force: bool = False) -> Path:
    """Download a file via the Wayback Machine.

    Some official hosts (e.g. land.vic.gov.au) sit behind a bot-protection WAF
    that 403s automated requests. The Internet Archive keeps clean copies, so we
    resolve the closest snapshot and pull the raw bytes (``id_`` form).
    """
    config.DATA_RAW.mkdir(parents=True, exist_ok=True)
    dest = config.DATA_RAW / filename
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"  cached  {filename} ({dest.stat().st_size/1e6:.1f} MB)")
        return dest
    avail = requests.get("http://archive.org/wayback/available",
                         params={"url": url}, headers=_HEADERS, timeout=60).json()
    snap = avail.get("archived_snapshots", {}).get("closest", {})
    if not snap.get("available"):
        raise RuntimeError(f"No Wayback snapshot for {url}")
    raw = f"https://web.archive.org/web/{snap['timestamp']}id_/{url}"
    print(f"  downloading {filename} via Wayback ({snap['timestamp']}) ...")
    r = requests.get(raw, headers=_HEADERS, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)
    print(f"  downloaded  {filename} ({dest.stat().st_size/1e6:.1f} MB)")
    return dest


def arcgis_query_all(layer_url: str, out_fields: str, where: str = "1=1") -> list[dict]:
    """Page through an ArcGIS FeatureServer layer and return all attribute dicts."""
    rows: list[dict] = []
    offset = 0
    page = 2000
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "false",
            "resultOffset": offset,
            "resultRecordCount": page,
            "f": "json",
        }
        r = requests.get(layer_url + "/query", params=params, headers=_HEADERS, timeout=120)
        r.raise_for_status()
        data = r.json()
        feats = data.get("features", [])
        rows.extend(f["attributes"] for f in feats)
        if not data.get("exceededTransferLimit") or not feats:
            break
        offset += len(feats)
    return rows


if __name__ == "__main__":  # pragma: no cover - manual use
    for u in sys.argv[1:]:
        print(fetch(u))
