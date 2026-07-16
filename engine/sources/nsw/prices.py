"""NSW sale medians from the Valuer General's bulk Property Sales Information.

The direct __psi zips are WAF-403 (like land.vic), so yearly archives come via
the Wayback Machine (fetch_wayback). Each yearly zip nests weekly zips of .DAT
files whose "B" records are individual sales:

  B;district;propertyId;saleCounter;downloadDT;propertyName;unitNo;houseNo;
  street;locality;postcode;area;areaType;contractDate;settlementDate;
  purchasePrice;zoning;natureOfProperty;primaryPurpose;strataLotNo;...

We keep residential sales (natureOfProperty == "R"), call a sale a unit when
it has a strata lot number, aggregate raw prices per (SA2, year) through the
same locality-name matching the crime adapter uses, and emit the exact fields
the Vic prices adapter does. Yearly medians only — the 12-month change is
latest-year vs previous-year.
"""
from __future__ import annotations

import io
import statistics
import zipfile
from collections import defaultdict

from ... import config
from ...fetch import fetch_wayback, fresh
from ..crime import _sa2_localities
import json

YEARS = list(range(2018, 2025))
MIN_SALES = 5


def _sales_by_locality() -> dict[str, dict]:
    """{LOCALITY: {"house": {year: [prices]}, "unit": {year: [prices]}}}"""
    cache = config.DATA_RAW / "nsw_psi_medians.json"
    if fresh(cache, 90):
        print("  cached  nsw_psi_medians.json")
        return json.loads(cache.read_text(encoding="utf-8"))

    acc: dict[str, dict] = defaultdict(lambda: {"house": defaultdict(list),
                                                "unit": defaultdict(list)})
    for year in YEARS:
        try:
            path = fetch_wayback(
                f"https://www.valuergeneral.nsw.gov.au/__psi/yearly/{year}.zip",
                f"nsw_psi_{year}.zip")
        except Exception as e:  # noqa: BLE001 - a missing year shrinks the series
            print(f"  prices: {year}.zip unavailable ({e}) — skipping")
            continue
        n = 0
        with zipfile.ZipFile(path) as outer:
            inners = [m for m in outer.namelist() if m.lower().endswith(".zip")]
            for m in inners or [None]:
                try:
                    zf = zipfile.ZipFile(io.BytesIO(outer.read(m))) if m else outer
                except Exception:  # noqa: BLE001
                    continue
                for dat in [d for d in zf.namelist() if d.lower().endswith(".dat")]:
                    try:
                        text = zf.read(dat).decode("utf-8", "replace")
                    except Exception:  # noqa: BLE001
                        continue
                    for line in text.splitlines():
                        if not line.startswith("B;"):
                            continue
                        f = line.split(";")
                        if len(f) < 20 or f[17].strip() != "R":
                            continue
                        try:
                            price = float(f[15])
                        except ValueError:
                            continue
                        if price < 20000:
                            continue
                        loc = f[9].strip().upper()
                        cd = f[13].strip()
                        if not loc or len(cd) < 4 or not cd[:4].isdigit():
                            continue
                        y = int(cd[:4])
                        kind = "unit" if f[19].strip() else "house"
                        acc[loc][kind][str(y)].append(price)
                        n += 1
        print(f"  prices: {year}.zip -> {n} residential sales parsed")

    # store medians+counts, not raw prices (keeps the cache small)
    out: dict[str, dict] = {}
    for loc, kinds in acc.items():
        rec = {}
        for kind, by_year in kinds.items():
            rec[kind] = {y: [round(statistics.median(v)), len(v)]
                         for y, v in by_year.items() if len(v) >= MIN_SALES}
        if rec.get("house") or rec.get("unit"):
            out[loc] = rec
    cache.write_text(json.dumps(out), encoding="utf-8")
    print(f"  prices: medians for {len(out)} NSW localities")
    return out


def _series_stats(by_year: dict[str, list]) -> dict:
    years = sorted(int(y) for y in by_year)
    if not years:
        return {}
    latest = years[-1]
    med = {y: by_year[str(y)][0] for y in years}
    o = {"median": med[latest], "year": latest,
         "series": [[y, med[y]] for y in years]}
    if latest - 1 in med and med[latest - 1]:
        o["chg_12m"] = round((med[latest] - med[latest - 1]) / med[latest - 1] * 100, 1)
    if latest - 3 in med and med[latest - 3]:
        o["cagr_3yr"] = round(((med[latest] / med[latest - 3]) ** (1 / 3) - 1) * 100, 1)
    return o


def get_prices(name_by_code: dict[str, str]) -> dict[str, dict]:
    """Vic-compatible per-SA2 price fields from PSI locality medians."""
    locs = _sales_by_locality()
    out = {}
    for code, name in name_by_code.items():
        # merge this SA2's localities, weighting medians by sale counts
        merged = {"house": defaultdict(list), "unit": defaultdict(list)}
        for loc in _sa2_localities(name):
            rec = locs.get(loc)
            if not rec:
                continue
            for kind in ("house", "unit"):
                for y, (med, cnt) in (rec.get(kind) or {}).items():
                    merged[kind][y].append((med, cnt))
        rec = {}
        for kind in ("house", "unit"):
            by_year = {}
            for y, pairs in merged[kind].items():
                total = sum(c for _, c in pairs)
                if total >= MIN_SALES:
                    wavg = sum(m * c for m, c in pairs) / total
                    by_year[y] = [round(wavg), total]
            s = _series_stats(by_year)
            if not s:
                continue
            if kind == "house":
                rec.update({"median_house": s["median"], "house_year": s["year"],
                            "house_12m": s.get("chg_12m"), "house_3yr_cagr": s.get("cagr_3yr"),
                            "house_series": s["series"]})
            else:
                rec.update({"median_unit": s["median"], "unit_year": s["year"],
                            "unit_12m": s.get("chg_12m"), "unit_3yr_cagr": s.get("cagr_3yr")})
        out[code] = rec
    matched = sum(1 for v in out.values() if v.get("median_house"))
    print(f"  prices: house medians for {matched}/{len(out)} SA2s (VG PSI via Wayback)")
    return out
