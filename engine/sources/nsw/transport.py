"""NSW train/metro station access from TfNSW open data (Sydney Probe recon).

Locations: locationfacilitydata.csv (LOCATION_NAME, LATITUDE, LONGITUDE,
TRANSPORT_MODE). Patronage: entry_exit.csv (MonthYear, Station, Station_Type,
Entry_Exit, Trip — small counts appear as "Less than 50"). We keep Sydney
Trains + Metro stations inside the city bbox, attach ~annual trips (latest 12
months of entries+exits), and compute the same residential-weighted access
metrics as the Vic adapter. Everything maps to the app's "metro" kind; there
is no V/Line analogue in v1 (intercity services aren't split out).
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from collections import defaultdict

from ... import config
from ...fetch import fetch

LOCATIONS_URL = ("https://opendata.transport.nsw.gov.au/data/dataset/25f006fd-d0fb-4a8e-bfda-7ea4033c1aeb/"
                 "resource/e9d94351-f22d-46ea-b64d-10e7e238368a/download/locationfacilitydata.csv")
PATRONAGE_URL = ("https://opendata.transport.nsw.gov.au/data/dataset/3977df59-a1fa-422e-91ff-cfaeac355cc9/"
                 "resource/f8bb2918-0540-4bb3-9ccf-f7aef04d4249/download/entry_exit.csv")
NEAR_KM = 3.0


def _to_km(lon, lat):
    lon0, lat0 = config.CITY_ORIGIN
    return ((lon - lon0) * 111.320 * math.cos(math.radians(lat0)),
            (lat - lat0) * 110.574)


def _norm_name(n: str) -> str:
    n = re.sub(r"\s+", " ", str(n)).strip()
    return re.sub(r"\s+Station$", "", n, flags=re.I).upper()


def _patronage() -> dict[str, int]:
    """{STATION: trips over the latest 12 months} (entries + exits)."""
    path = fetch(PATRONAGE_URL, "nsw_station_entry_exit.csv", max_age_days=60)
    per_month: dict[str, dict[str, float]] = defaultdict(dict)
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            st = _norm_name(r.get("Station", ""))
            month = str(r.get("MonthYear", ""))[:10]
            trip = str(r.get("Trip", "")).strip()
            if not st or not month:
                continue
            n = 25.0 if trip.lower().startswith("less") else \
                (float(trip.replace(",", "")) if trip.replace(",", "").replace(".", "").isdigit() else 0.0)
            per_month[st][month] = per_month[st].get(month, 0.0) + n
    out = {}
    for st, months in per_month.items():
        latest = sorted(months)[-12:]
        out[st] = int(sum(months[m] for m in latest))
    print(f"  transport: patronage for {len(out)} stations (latest 12 months)")
    return out


def _load_stations() -> list[dict]:
    path = fetch(LOCATIONS_URL, "nsw_location_facilities.csv", max_age_days=90)
    pax = _patronage()
    x0, y0, x1, y1 = config.CITY_BBOX
    stations, seen = [], set()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            mode = str(r.get("TRANSPORT_MODE", "")).lower()
            if "train" not in mode and "metro" not in mode:
                continue
            try:
                lat, lon = float(r["LATITUDE"]), float(r["LONGITUDE"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
            name = re.sub(r"\s+", " ", str(r.get("LOCATION_NAME", ""))).strip()
            key = _norm_name(name)
            if not name or key in seen:
                continue
            seen.add(key)
            stations.append({"name": name, "lon": lon, "lat": lat,
                             "kind": "metro", "pax": pax.get(key)})
    return stations


def get_stations(points: dict[str, tuple[float, float]],
                 res_points: dict[str, list] | None = None) -> dict[str, dict]:
    stations_raw = _load_stations()

    config.CITY_DATA.mkdir(parents=True, exist_ok=True)
    overlay = [{"type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [round(s["lon"], 5), round(s["lat"], 5)]},
                "properties": {"name": s["name"], "kind": s["kind"], "pax": s["pax"]}}
               for s in stations_raw]
    (config.CITY_DATA / "stations.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": overlay}, separators=(",", ":")),
        encoding="utf-8")

    stations = [(*_to_km(s["lon"], s["lat"]), s) for s in stations_raw]
    near2 = NEAR_KM ** 2

    def _subsample(pts: list, cap: int) -> list:
        if len(pts) <= cap:
            return pts
        step = len(pts) / cap
        return [pts[int(i * step)] for i in range(cap)]

    def _nearest(sample, cx, cy, subset):
        if not subset:
            return None, None
        best2, best = float("inf"), None
        for sx, sy, s in subset:
            d2 = (cx - sx) ** 2 + (cy - sy) ** 2
            if d2 < best2:
                best2, best = d2, s
        dists = sorted(min((px - sx) ** 2 + (py - sy) ** 2 for sx, sy, _ in subset)
                       for px, py in sample)
        return round(dists[len(dists) // 2] ** 0.5, 2), best

    out = {}
    res_used = 0
    for code, (lon, lat) in points.items():
        rp = _subsample((res_points or {}).get(code) or [], 48)
        if rp:
            res_used += 1
        sample = [_to_km(px, py) for px, py in rp] or [_to_km(lon, lat)]
        cx = sum(x for x, _ in sample) / len(sample)
        cy = sum(y for _, y in sample) / len(sample)
        count = sum(1 for sx, sy, _ in stations if (cx - sx) ** 2 + (cy - sy) ** 2 <= near2)
        any_km, any_st = _nearest(sample, cx, cy, stations)
        out[code] = {
            "nearest_station_km": any_km,
            "nearest_station": any_st["name"] if any_st else None,
            "stations_3km": count,
            "station_pax": any_st["pax"] if any_st else None,
            "metro_km": any_km, "metro_station": any_st["name"] if any_st else None,
            "metro_pax": any_st["pax"] if any_st else None,
            "vline_km": None, "vline_station": None, "vline_pax": None,
        }
    print(f"  transport: {len(stations)} NSW train/metro stations -> access for "
          f"{len(out)} SA2s ({res_used} measured from residential land)")
    return out
