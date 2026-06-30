"""Crime inputs per SA2, split by offence type so personal safety can be
prioritised over property crime in the Liveability score.

Source: CSA Victoria "Recorded Offences by LGA" workbook.
  * Table 01 -> each LGA's total offence count + rate  => implied population
  * Table 02 -> offence counts by Offence Division per LGA
We compute, per LGA and per 100k population:
  person   = Division "A Crimes against the person"
  property = Division "B Property and deception offences"
  total    = all divisions
then attach each Greater Melbourne SA2 to the LGA that geographically contains
its representative point (pure-Python point-in-polygon). Coarse but defensible
for v1 — suburb-level crime is a later refinement.
"""
from __future__ import annotations

import json
import zipfile
from collections import defaultdict

import openpyxl
import shapefile

from .. import config, geo
from ..fetch import fetch

# Latest CSA "Recorded offences by LGA" workbook (year ending March 2026).
CRIME_XLSX_URL = (
    "https://files.crimestatistics.vic.gov.au/2026-06/"
    "Data_Tables_LGA_Recorded_Offences_Year_Ending_March_2026.xlsx"
)
LGA_SHP_URL = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/"
    "access-and-downloads/digital-boundary-files/LGA_2022_AUST_GDA2020_SHP.zip"
)

# ABS boundary names that CSA publishes under a newer name (LGA renames).
_ALIASES = {"moreland": "merri-bek"}


def _norm_lga(name: str) -> str:
    s = str(name).lower()
    for junk in ("(vic.)", "(c)", "(rc)", "(s)", "(b)", "(m)"):
        s = s.replace(junk, "")
    s = s.strip()
    return _ALIASES.get(s, s)


def _offences_by_lga() -> dict[str, dict]:
    """{lga_key: {person, property, total}} rates per 100k for the latest year."""
    cache = config.DATA_RAW / "crime_offences_by_lga.json"
    if cache.exists() and cache.stat().st_size > 0:
        print("  cached  crime_offences_by_lga.json")
        return json.loads(cache.read_text(encoding="utf-8"))

    path = fetch(CRIME_XLSX_URL, "csa_lga_recorded_offences.xlsx")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    # Table 01: LGA totals -> implied population = count / rate * 100k
    pop, total_rate = {}, {}
    years = set()
    for year, _end, _region, lga, count, rate in wb["Table 01"].iter_rows(min_row=2, values_only=True):
        if year is None or lga is None or not rate or _norm_lga(lga) == "total":
            continue
        years.add(int(year))
    latest = max(years)
    for year, _end, _region, lga, count, rate in wb["Table 01"].iter_rows(min_row=2, values_only=True):
        if int(year or 0) != latest or lga is None or not rate or not count:
            continue
        key = _norm_lga(lga)
        if key == "total":
            continue
        pop[key] = count / float(rate) * 1e5
        total_rate[key] = float(rate)

    # Table 02: offence counts by Offence Division per LGA
    person, prop, total = defaultdict(float), defaultdict(float), defaultdict(float)
    for row in wb["Table 02"].iter_rows(min_row=2, values_only=True):
        year, _end, _psa, lga, div, _sub, _grp, count = row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]
        if int(year or 0) != latest or lga is None or not count:
            continue
        key = _norm_lga(lga)
        total[key] += count
        if str(div).startswith("A "):        # crimes against the person
            person[key] += count
        elif str(div).startswith("B "):      # property and deception offences
            prop[key] += count
    wb.close()

    out = {}
    for key, p in pop.items():
        if p <= 0:
            continue
        out[key] = {
            "person": round(person[key] / p * 1e5, 1),
            "property": round(prop[key] / p * 1e5, 1),
            "total": round(total[key] / p * 1e5, 1) or total_rate.get(key),
        }
    cache.write_text(json.dumps(out), encoding="utf-8")
    print(f"  crime: {len(out)} LGAs, year {latest} (person/property/total rates)")
    return out


def _load_vic_lgas() -> list[dict]:
    """Victorian LGA polygons with bounding boxes for fast point-in-polygon."""
    zip_path = fetch(LGA_SHP_URL, "LGA_2022_SHP.zip")
    ed = config.DATA_RAW / "lga_shp"
    if not ed.exists():
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(ed)
    shp = next(ed.glob("*.shp"))
    r = shapefile.Reader(str(shp))
    fields = [f[0] for f in r.fields[1:]]
    i_name = next(i for i, f in enumerate(fields) if f.upper().startswith("LGA_NAME"))
    i_ste = next(i for i, f in enumerate(fields) if f.upper().startswith("STE_NAME"))
    lgas = []
    for sr in r.iterShapeRecords():
        if str(sr.record[i_ste]).strip() != config.STATE_NAME or not sr.shape.points:
            continue
        geom = sr.shape.__geo_interface__
        polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        rings = [[tuple(c) for c in poly[0]] for poly in polys]
        xs = [x for ring in rings for x, _ in ring]
        ys = [y for ring in rings for _, y in ring]
        lgas.append({
            "name": str(sr.record[i_name]), "key": _norm_lga(sr.record[i_name]),
            "rings": rings, "bbox": (min(xs), min(ys), max(xs), max(ys)),
        })
    return lgas


def get_crime() -> dict[str, dict]:
    """{sa2_code: {lga, person, property, total}} for Greater Melbourne SA2s."""
    rates = _offences_by_lga()
    lgas = _load_vic_lgas()
    points = geo.melbourne_sa2_points()

    out = {}
    for code, (x, y) in points.items():
        chosen = None
        for lga in lgas:
            x0, y0, x1, y1 = lga["bbox"]
            if x0 <= x <= x1 and y0 <= y <= y1 and any(geo.point_in_ring(x, y, r) for r in lga["rings"]):
                chosen = lga
                break
        if chosen is None:
            chosen = min(lgas, key=lambda L: (
                (x - (L["bbox"][0] + L["bbox"][2]) / 2) ** 2
                + (y - (L["bbox"][1] + L["bbox"][3]) / 2) ** 2))
        r = rates.get(chosen["key"], {})
        out[code] = {
            "lga": chosen["name"],
            "person": r.get("person"), "property": r.get("property"), "total": r.get("total"),
        }
    matched = sum(1 for v in out.values() if v["person"] is not None)
    print(f"  crime: matched {matched}/{len(out)} SA2s to an LGA rate")
    return out


if __name__ == "__main__":  # pragma: no cover
    c = get_crime()
    for code in list(c)[:3]:
        print(code, c[code])
