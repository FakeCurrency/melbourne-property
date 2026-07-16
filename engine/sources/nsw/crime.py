"""NSW crime inputs from BOCSAR's open datasets (Sydney Probe recon).

Suburb level: "Quarterly recorded crime dataset by Suburb" — a zip of one CSV
with rows (Suburb, Offence category, Subcategory, <one column per month>).
We sum the latest 12 months per suburb (and the same window four years
earlier for the trend arrow), split BOCSAR's categories into person vs
property, and allocate locality counts to SA2s exactly like the Vic adapter.

LGA level: v1 ships without an LGA rate fallback (suburb coverage is high);
SA2s with no matching locality get crime=None and are renormalised out.
LGA *names* still come from the national ABS boundary spatial join.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import defaultdict

from ... import config
from ...fetch import fetch, fresh
from ..crime import _load_vic_lgas, _sa2_localities  # city-agnostic helpers
from ... import geo
import json

SUBURB_ZIP = "https://bocsarblob.blob.core.windows.net/bocsar-open-data/SuburbData.zip"

# BOCSAR offence categories -> the app's person/property split (Vic Division A/B)
PERSON_CATS = {"homicide", "assault", "sexual offences", "abduction and kidnapping",
               "robbery", "blackmail and extortion", "intimidation, stalking and harassment"}
PROPERTY_CATS = {"theft", "arson", "malicious damage to property", "break and enter"}


def _month_cols(header: list[str]) -> list[int]:
    """Indices of columns whose header parses as a month (e.g. 'Jan 1995')."""
    pat = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[ \-/]?(\d{2,4})$", re.I)
    return [i for i, h in enumerate(header) if h and pat.match(str(h).strip())]


def _counts_by_suburb() -> dict[str, dict]:
    """{SUBURB: {person, property, total, person_prev}} from the BOCSAR csv."""
    cache = config.DATA_RAW / "nsw_crime_by_suburb.json"
    if fresh(cache, 60):
        print("  cached  nsw_crime_by_suburb.json")
        return json.loads(cache.read_text(encoding="utf-8"))

    path = fetch(SUBURB_ZIP, "nsw_crime_suburb.zip", max_age_days=60)
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        rows = csv.reader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig"))
        header = next(rows)
        mcols = _month_cols(header)
        if len(mcols) < 60:
            raise RuntimeError(f"BOCSAR suburb csv: only {len(mcols)} month columns recognised")
        latest12 = mcols[-12:]
        prev12 = mcols[-60:-48]           # same 12-month window, four years earlier
        lower = [str(h).strip().lower() for h in header]
        i_sub = next(i for i, h in enumerate(lower) if "suburb" in h)
        i_cat = next(i for i, h in enumerate(lower) if "categ" in h)

        counts: dict[str, dict] = defaultdict(
            lambda: {"person": 0.0, "property": 0.0, "total": 0.0, "person_prev": 0.0})
        for r in rows:
            if len(r) <= max(latest12[-1], i_sub, i_cat):
                continue
            sub = str(r[i_sub]).strip().upper()
            cat = str(r[i_cat]).strip().lower()
            if not sub:
                continue

            def _s(cols):
                t = 0.0
                for i in cols:
                    v = str(r[i]).strip()
                    if v and v.replace(".", "").isdigit():
                        t += float(v)
                return t

            cur = _s(latest12)
            c = counts[sub]
            c["total"] += cur
            if cat in PERSON_CATS:
                c["person"] += cur
                c["person_prev"] += _s(prev12)
            elif cat in PROPERTY_CATS or "theft" in cat or "steal" in cat:
                c["property"] += cur
    out = dict(counts)
    cache.write_text(json.dumps(out), encoding="utf-8")
    print(f"  crime: BOCSAR suburb counts for {len(out)} NSW localities (latest 12 months)")
    return out


def get_crime() -> dict[str, dict]:
    """{sa2_code: {lga, person, property, total}} — LGA names via the national
    ABS boundary join; no NSW LGA rate fallback in v1 (values stay None)."""
    lgas = _load_vic_lgas()          # filters by config.STATE_NAME — city-agnostic
    points = geo.sa2_points()
    out = {}
    for code, (x, y) in points.items():
        chosen = None
        for lga in lgas:
            x0, y0, x1, y1 = lga["bbox"]
            if x0 <= x <= x1 and y0 <= y <= y1 and any(geo.point_in_ring(x, y, r) for r in lga["rings"]):
                chosen = lga
                break
        if chosen is None and lgas:
            chosen = min(lgas, key=lambda L: (
                (x - (L["bbox"][0] + L["bbox"][2]) / 2) ** 2
                + (y - (L["bbox"][1] + L["bbox"][3]) / 2) ** 2))
        out[code] = {"lga": chosen["name"] if chosen else None,
                     "person": None, "property": None, "total": None}
    print(f"  crime: LGA names assigned for {len(out)} SA2s (BOCSAR rates are suburb-level)")
    return out


def get_crime_suburb(name_by_code: dict[str, str],
                     pop_by_code: dict[str, float],
                     lga_by_code: dict[str, str]) -> dict[str, dict]:
    """Same allocation as the Vic adapter: locality counts split across the
    SA2s referencing the locality (population-weighted), then per-100k rates."""
    counts = _counts_by_suburb()

    loc2sa2: dict[str, list[str]] = defaultdict(list)
    for code, name in name_by_code.items():
        if not pop_by_code.get(code):
            continue
        for loc in _sa2_localities(name):
            loc2sa2[loc].append(code)

    alloc: dict[str, dict] = defaultdict(
        lambda: {"person": 0.0, "property": 0.0, "total": 0.0, "person_prev": 0.0})
    for loc, c in counts.items():
        sa2s = loc2sa2.get(loc)
        if not sa2s:
            continue
        pop_sum = sum(pop_by_code[s] for s in sa2s)
        if pop_sum <= 0:
            continue
        for s in sa2s:
            w = pop_by_code[s] / pop_sum
            a = alloc[s]
            for k in ("person", "property", "total", "person_prev"):
                a[k] += c.get(k, 0.0) * w

    out = {}
    for code in name_by_code:
        pop = pop_by_code.get(code)
        a = alloc.get(code)
        if not pop or not a or a["total"] <= 0:
            out[code] = {}
            continue
        rec = {k: round(v / pop * 1e5, 1) for k, v in a.items() if k != "person_prev"}
        if a["person_prev"] >= 20 and a["person"] >= 20:
            rec["person_trend_pct"] = round((a["person"] - a["person_prev"])
                                            / a["person_prev"] * 100, 1)
        out[code] = rec
    matched = sum(1 for v in out.values() if v)
    print(f"  crime: suburb-level rates for {matched}/{len(out)} SA2s")
    return out


def get_postcode_map(name_by_code: dict[str, str],
                     lga_by_code: dict[str, str]) -> dict[str, list[str]]:
    """{postcode: [sa2_codes]} for search — (suburb, postcode) pairs from the
    NSW schools master dataset (most residential suburbs have a school)."""
    from .schools import suburb_postcode_pairs
    loc2sa2: dict[str, list[str]] = defaultdict(list)
    for code, name in name_by_code.items():
        for loc in _sa2_localities(name):
            loc2sa2[loc].append(code)
    out: dict[str, list[str]] = {}
    for loc, pc in suburb_postcode_pairs():
        for c in loc2sa2.get(loc, []):
            out.setdefault(pc, [])
            if c not in out[pc]:
                out[pc].append(c)
    print(f"  postcodes: {len(out)} NSW postcodes resolve to SA2s (schools-derived)")
    return out
