"""Orchestrate the full build: geography + sources -> scored JSON for the site."""
from __future__ import annotations

import datetime as dt
import json

from . import config, geo, score
from .sources import census, crime, electricity, prices

SOURCES_NOTE = {
    "boundaries": "ABS ASGS Edition 3 SA2 (2021)",
    "crime": "Crime Statistics Agency Victoria — recorded offences by LGA, split person vs property, YE Mar 2026",
    "seifa": "ABS SEIFA 2021 (IRSAD + IEO) by SA2",
    "housing": "ABS Census 2021 G37 (tenure x dwelling structure) by SA2",
    "demographics": "ABS Census 2021 G01 (age 0-14 child share) by SA2",
    "prices": "Victorian Valuer-General — Median House/Unit by Suburb time series (2014-2024 / 2013-2023)",
    "electricity": "Geoscience Australia — National Electricity Infrastructure (transmission lines + substations, v4 2024)",
}


def _load_sa2_index() -> dict[str, dict]:
    """Authoritative SA2 code -> name/sa3/sa4 from the boundary GeoJSON."""
    path = config.PUBLIC_DATA / "melbourne.geojson"
    if not path.exists():
        geo.build_geojson()
    fc = json.loads(path.read_text(encoding="utf-8"))
    idx = {}
    for f in fc["features"]:
        p = f["properties"]
        idx[p["sa2_code"]] = {"name": p["sa2_name"], "sa3": p["sa3_name"], "sa4": p["sa4_name"]}
    return idx


def build() -> None:
    print("1/5 boundaries"); geo.build_geojson()
    index = _load_sa2_index()
    print("2/5 census (SEIFA + housing + demographics)")
    seifa = census.get_seifa()
    housing = census.get_housing()
    demo = census.get_demographics()
    print("3/6 crime"); crime_data = crime.get_crime()
    print("4/6 prices (Valuer-General)")
    price_data = prices.get_prices({code: m["name"] for code, m in index.items()})
    print("5/6 electricity (Geoscience Australia)")
    infra_data = electricity.get_infra(geo.melbourne_sa2_points())

    print("6/6 scoring")
    records = {}
    for code, meta in index.items():
        s = seifa.get(code, {})
        h = housing.get(code, {})
        d = demo.get(code, {})
        cr = crime_data.get(code, {})
        pr = price_data.get(code, {})
        inf = infra_data.get(code, {})
        records[code] = {
            "name": meta["name"], "sa3": meta["sa3"], "sa4": meta["sa4"],
            "lga": cr.get("lga"),
            "person_crime": cr.get("person"), "property_crime": cr.get("property"),
            "total_crime": cr.get("total"),
            "irsad_score": s.get("irsad_score"), "irsad_decile": s.get("irsad_decile"),
            "ieo_score": s.get("ieo_score"), "ieo_decile": s.get("ieo_decile"),
            "population": s.get("population"), "density": s.get("density"),
            "child_share": d.get("child_share"),
            "owner_occ": h.get("owner_occ"), "mortgage": h.get("mortgage"),
            "rental": h.get("rental"), "social": h.get("social"),
            "detached": h.get("detached"),
            "median_house": pr.get("median_house"), "median_unit": pr.get("median_unit"),
            "house_12m": pr.get("house_12m"), "house_3yr_cagr": pr.get("house_3yr_cagr"),
            "house_year": pr.get("house_year"), "unit_year": pr.get("unit_year"),
            "nearest_transmission_km": inf.get("nearest_transmission_km"),
            "nearest_substation_km": inf.get("nearest_substation_km"),
            "substation_count_10km": inf.get("substation_count_10km"),
            "nearest_line_kv": inf.get("nearest_line_kv"),
        }
    scored = score.compute_scores(records)

    payload = {
        "city": config.GCC_NAME,
        "generated": dt.date.today().isoformat(),
        "count": len(scored),
        "default_blend": config.DEFAULT_BLEND,
        "mode_presets": config.MODE_PRESETS,
        "presets": config.PRESETS,
        "weights": {"liveability": config.LIVE_WEIGHTS,
                    "liveability_family": config.LIVE_WEIGHTS_FAMILY,
                    "development": config.DEV_WEIGHTS, "family": config.FAMILY_WEIGHTS},
        "sources": SOURCES_NOTE,
        "areas": scored,
    }
    config.PUBLIC_DATA.mkdir(parents=True, exist_ok=True)
    out = config.PUBLIC_DATA / "scores.json"
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"  wrote {out.name}: {len(scored)} areas, {out.stat().st_size/1e6:.2f} MB")


if __name__ == "__main__":  # pragma: no cover
    build()
