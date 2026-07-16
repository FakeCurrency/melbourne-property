"""NSW school-access inputs from the DoE master dataset (government schools).

master_dataset.csv carries Latitude/Longitude, Level_of_schooling and
Town_suburb/Postcode (which also feeds the search postcode map). Non-government
schools aren't included in v1 — a national ACARA adapter can replace this for
every state later (docs/AUSTRALIA.md).
"""
from __future__ import annotations

import csv
import math

from ... import config
from ...fetch import fetch

MASTER_URL = ("https://data.nsw.gov.au/data/dataset/78c10ea3-8d04-4c9c-b255-bbf8547e37e7/"
              "resource/3e6d5f6a-055c-440d-a690-fc0537c31095/download/master_dataset.csv")
NEAR_KM = 3.0


def _to_km(lon, lat):
    lon0, lat0 = config.CITY_ORIGIN
    return ((lon - lon0) * 111.320 * math.cos(math.radians(lat0)),
            (lat - lat0) * 110.574)


def _rows():
    path = fetch(MASTER_URL, "nsw_schools_master.csv", max_age_days=120)
    for enc in ("utf-8-sig", "cp1252"):
        try:
            with open(path, encoding=enc, newline="") as fh:
                return list(csv.DictReader(fh))
        except UnicodeDecodeError:
            continue
    return []


def suburb_postcode_pairs() -> list[tuple[str, str]]:
    """(SUBURB, postcode) pairs for the search postcode map."""
    pairs = set()
    for r in _rows():
        sub = str(r.get("Town_suburb", "")).strip().upper()
        pc = str(r.get("Postcode", "")).strip().split(".")[0]
        if sub and len(pc) == 4 and pc.isdigit():
            pairs.add((sub, pc))
    return sorted(pairs)


def _load_schools():
    x0, y0, x1, y1 = config.CITY_BBOX
    schools = []
    for r in _rows():
        try:
            lat, lon = float(r["Latitude"]), float(r["Longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        level = str(r.get("Level_of_schooling", "")).strip().lower()
        primary = any(k in level for k in ("primary", "infant", "central", "community"))
        secondary = any(k in level for k in ("secondary", "high", "central", "community"))
        if not (primary or secondary):
            continue
        x, y = _to_km(lon, lat)
        schools.append((x, y, primary, secondary))
    return schools


def _subsample(pts: list, cap: int) -> list:
    if len(pts) <= cap:
        return pts
    step = len(pts) / cap
    return [pts[int(i * step)] for i in range(cap)]


def get_schools(points: dict[str, tuple[float, float]],
                res_points: dict[str, list] | None = None) -> dict[str, dict]:
    """{sa2_code: {nearest_primary_km, nearest_secondary_km, schools_3km}} —
    same residential-weighted median distances as the Vic adapter."""
    schools = _load_schools()
    near2 = NEAR_KM ** 2
    out = {}
    res_used = 0
    for code, (lon, lat) in points.items():
        rp = _subsample((res_points or {}).get(code) or [], 36)
        if rp:
            res_used += 1
        sample = [_to_km(px, py) for px, py in rp] or [_to_km(lon, lat)]
        cx = sum(x for x, _ in sample) / len(sample)
        cy = sum(y for _, y in sample) / len(sample)
        cand = []
        for reach in (12.0, 30.0, 1e9):
            cand = [s for s in schools if abs(s[0] - cx) <= reach and abs(s[1] - cy) <= reach]
            if any(s[2] for s in cand) and any(s[3] for s in cand):
                break
        count = sum(1 for x, y, _p, _s in cand if (cx - x) ** 2 + (cy - y) ** 2 <= near2)
        p_meds, s_meds = [], []
        for px, py in sample:
            bp = bs = float("inf")
            for x, y, primary, secondary in cand:
                d2 = (px - x) ** 2 + (py - y) ** 2
                if primary and d2 < bp:
                    bp = d2
                if secondary and d2 < bs:
                    bs = d2
            p_meds.append(bp)
            s_meds.append(bs)
        p_meds.sort(); s_meds.sort()
        bp2, bs2 = p_meds[len(p_meds) // 2], s_meds[len(s_meds) // 2]
        out[code] = {
            "nearest_primary_km": round(bp2 ** 0.5, 2) if bp2 < float("inf") else None,
            "nearest_secondary_km": round(bs2 ** 0.5, 2) if bs2 < float("inf") else None,
            "schools_3km": count,
        }
    print(f"  schools: {len(schools)} NSW government schools -> access for {len(out)} SA2s "
          f"({res_used} measured from residential land)")
    return out
