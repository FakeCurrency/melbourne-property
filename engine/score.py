"""Turn raw per-SA2 inputs into Liveability + Development scores.

Every input is converted to a 0-100 percentile *within Greater Melbourne*
(higher = always better; "bad" inputs like crime, social housing and density are
inverted), then blended with the weights in config.py.

Key v1 refinements:
  * Personal safety (crimes against the person) is weighted far above property
    crime in Liveability.
  * A Family Suitability sub-score (child share + education/occupation + safety)
    is surfaced as a badge and lightly folded into Liveability.
  * Development potential is PRELIMINARY: redevelopment headroom (detached stock),
    renter turnover and low current density.
"""
from __future__ import annotations

import bisect

from . import config


def _percentiles(values: dict[str, float | None], invert: bool = False) -> dict[str, float]:
    """Map each value to its 0-100 percentile among the non-missing values."""
    present = [v for v in values.values() if v is not None]
    if not present:
        return {k: 50.0 for k in values}
    ordered = sorted(present)
    n = len(ordered)
    out: dict[str, float] = {}
    for k, v in values.items():
        if v is None:
            out[k] = 50.0
            continue
        lo = bisect.bisect_left(ordered, v)
        hi = bisect.bisect_right(ordered, v)
        pct = (lo + hi) / 2 / n * 100
        out[k] = round(100 - pct if invert else pct, 1)
    return out


def _weighted(norm: dict[str, float], weights: dict[str, float]) -> float:
    total = sum(weights.values())
    return round(sum(norm[k] * w for k, w in weights.items()) / total, 1)


def _safest_pct(score: float) -> int:
    """For an inverted (safety) score, the ascending rank: 100 -> bottom 0%."""
    return max(0, min(100, round(100 - score)))


def _top_pct(score: float) -> int:
    return max(0, min(100, round(100 - score)))


def _explain_live(p: dict, family: float) -> str:
    s = p["person_safety"]["score"]
    safety = (f"among the safest {_safest_pct(s)}% of Greater Melbourne for crimes against the person"
              if s >= 78 else
              "lower-than-average personal-crime rates" if s >= 55 else
              "mid-range personal-crime rates" if s >= 40 else
              "elevated personal-crime rates by Melbourne standards")
    dec = p["seifa"]["decile"] or 0
    seifa = ("a high socio-economic profile (SEIFA decile " + str(dec) + ")" if dec >= 8 else
             "a below-average socio-economic profile (SEIFA decile " + str(dec) + ")" if dec <= 3 else
             "a mid-range socio-economic profile (SEIFA decile " + str(dec) + ")")
    # Surface the person-vs-property contrast where property crime is the real driver.
    prop = (" Property crime runs higher here, typically retail/transport precincts rather than homes."
            if p["property_safety"]["score"] < 35 else "")
    fam = " It also reads as family-friendly." if family >= 70 else ""
    safety = safety[0].upper() + safety[1:]   # capitalise first letter only (keep "Melbourne")
    return f"{safety}, with {seifa}.{prop}{fam}"


def _explain_dev(p: dict) -> str:
    det = p["detached"]["raw"] or 0
    head = (f"~{round(det * 100)}% of dwellings are detached houses — strong redevelopment headroom"
            if det >= 0.7 else
            f"only ~{round(det * 100)}% detached houses, so it's already fairly built-up" if det < 0.35 else
            f"~{round(det * 100)}% detached houses — moderate headroom")
    turn = ("high renter turnover signalling investor activity" if (p["rental"]["raw"] or 0) >= 0.45
            else "a settled owner-occupier base")
    return (f"Preliminary: {head}, with {turn}. Still missing: planning zoning/overlays, "
            "land values and electricity capacity (Phase 2).")


def _growth_signal(cagr: float | None) -> str:
    if cagr is None:
        return "n/a"
    return "Strong" if cagr >= 4 else "Moderate" if cagr >= 1.5 else "Soft"


def _value_signal(price_pctile: float | None, has_price: bool) -> str | None:
    if not has_price:
        return None
    return "Affordable" if price_pctile <= 33 else "Premium" if price_pctile >= 75 else "Mid-market"


def _money(v: float) -> str:
    return f"${v / 1e6:.2f}M" if v >= 1e6 else f"${round(v / 1e3)}k"


def _explain_invest(m: dict) -> str:
    if not m["median_house"]:
        return "No recent Valuer-General sale medians for this area (often non-residential SA2s)."
    g12, cagr = m["house_12m"], m["house_3yr_cagr"]
    g12s = f"{'+' if (g12 or 0) >= 0 else ''}{g12}% over 12 months" if g12 is not None else "flat 12 months"
    cagrs = f"{'+' if (cagr or 0) >= 0 else ''}{cagr}% p.a. over 3 years" if cagr is not None else ""
    growth = {"Strong": "strong recent capital growth", "Moderate": "steady growth",
              "Soft": "soft/flat recent growth", "n/a": "limited growth history"}[m["growth_signal"]]
    val = {"Affordable": " An affordable entry point.", "Mid-market": " Mid-market pricing.",
           "Premium": " A premium, blue-chip market."}.get(m["value_signal"], "")
    return (f"Median house {_money(m['median_house'])} ({m['house_year']}): {g12s}, {cagrs} — {growth}.{val}")


def _infra_signal(score: float) -> str:
    return "Strong" if score >= 66 else "Moderate" if score >= 40 else "Limited"


def _explain_infra(inf: dict) -> str:
    t, s, c = inf["nearest_transmission_km"], inf["nearest_substation_km"], inf["substation_count_10km"]
    kv = inf["nearest_line_kv"]
    if t is None:
        return "No electricity-network data for this area."
    line = f"~{t} km from a transmission line" + (f" ({kv} kV)" if kv else "")
    subs = f"{c} substation{'s' if c != 1 else ''} within 10 km" + (f", nearest ~{s} km" if s is not None else "")
    if inf["score"] >= 66:
        return (f"Strong grid support: {line}; {subs}. Well placed for larger-scale "
                "development, subdivision or future EV/charging clusters.")
    if inf["score"] >= 40:
        return (f"Moderate grid support: {line}; {subs}. Smaller infill is more "
                "straightforward than major projects.")
    return (f"Limited existing network: {line}; {subs}. Connection costs are likely "
            "higher for larger projects until the area builds out.")


def _tags(p: dict, family: float, seifa_dec: int, dev: float, market: dict, infra_score: float) -> list[str]:
    """Up to 4 salient, plain-English chips (ordered by salience)."""
    t = []
    ps = p["person_safety"]["score"]
    if ps >= 85: t.append("Very safe")
    elif ps >= 65: t.append("Safe")
    if seifa_dec >= 9 and ps >= 78: t.append("Blue-chip")
    elif seifa_dec >= 8: t.append("Affluent")
    if family >= 72: t.append("Family-friendly")
    if dev >= 70 and p["child"]["score"] >= 60: t.append("Growth corridor")
    if infra_score >= 72: t.append("Grid-ready")
    if p["detached"]["score"] >= 72: t.append("Redevelopment headroom")
    if p["rental"]["score"] >= 72: t.append("High rental demand")
    if p["owner_occ"]["score"] >= 72 and p["rental"]["score"] <= 35: t.append("Tightly held")
    if p["low_density"]["score"] >= 72: t.append("Low density")
    elif p["low_density"]["score"] <= 22: t.append("Built-up")
    if market.get("growth_signal") == "Strong": t.append("Strong growth")
    if market.get("value_signal") == "Affordable" and dev >= 55: t.append("Affordable upside")
    elif market.get("value_signal") == "Premium": t.append("Premium market")
    return t[:5]


def compute_scores(records: dict[str, dict]) -> dict[str, dict]:
    codes = list(records)
    g = lambda key: {c: records[c].get(key) for c in codes}  # noqa: E731

    n = {
        "person_safety": _percentiles(g("person_crime"), invert=True),
        "property_safety": _percentiles(g("property_crime"), invert=True),
        "seifa": _percentiles(g("irsad_score")),
        "ieo": _percentiles(g("ieo_score")),
        "child": _percentiles(g("child_share")),
        "owner_occ": _percentiles(g("owner_occ")),
        "low_social": _percentiles(g("social"), invert=True),
        "detached": _percentiles(g("detached")),
        "rental": _percentiles(g("rental")),
        "mortgage": _percentiles(g("mortgage")),
        "low_density": _percentiles(g("density"), invert=True),
        "growth": _percentiles(g("house_3yr_cagr")),   # higher recent CAGR = higher
        "price": _percentiles(g("median_house")),       # higher median = pricier
        "trans_prox": _percentiles(g("nearest_transmission_km"), invert=True),  # closer = higher
        "sub_prox": _percentiles(g("nearest_substation_km"), invert=True),
        "sub_dens": _percentiles(g("substation_count_10km")),                   # more = higher
    }

    out: dict[str, dict] = {}
    for c in codes:
        r = records[c]
        family = _weighted(
            {"child": n["child"][c], "ieo": n["ieo"][c], "person_safety": n["person_safety"][c]},
            config.FAMILY_WEIGHTS)
        # Shared pillar inputs; two Liveability values from two weight sets.
        live_norm = {
            "person_safety": n["person_safety"][c], "seifa": n["seifa"][c],
            "owner_occ": n["owner_occ"][c], "property_safety": n["property_safety"][c],
            "family_child": n["child"][c],
        }
        live = _weighted(live_norm, config.LIVE_WEIGHTS)               # base (Balanced/Invest)
        live_family = _weighted(live_norm, config.LIVE_WEIGHTS_FAMILY)  # Live / Family-First
        infra_score = _weighted({
            "transmission": n["trans_prox"][c], "substation": n["sub_prox"][c],
            "density": n["sub_dens"][c],
        }, config.INFRA_WEIGHTS)
        dev = _weighted({
            "detached_share": n["detached"][c], "growth": n["growth"][c],
            "infra": infra_score, "rental_share": n["rental"][c],
            "low_density": n["low_density"][c],
        }, config.DEV_WEIGHTS)
        overall = round(config.DEFAULT_BLEND["live"] * live + config.DEFAULT_BLEND["dev"] * dev, 1)

        pillars = {
            "person_safety": {"score": n["person_safety"][c], "raw": r.get("person_crime")},
            "property_safety": {"score": n["property_safety"][c], "raw": r.get("property_crime")},
            "seifa": {"score": n["seifa"][c], "raw": r.get("irsad_score"), "decile": r.get("irsad_decile")},
            "ieo": {"score": n["ieo"][c], "decile": r.get("ieo_decile")},
            "owner_occ": {"score": n["owner_occ"][c], "raw": r.get("owner_occ")},
            "low_social": {"score": n["low_social"][c], "raw": r.get("social")},
            "child": {"score": n["child"][c], "raw": r.get("child_share")},
            "detached": {"score": n["detached"][c], "raw": r.get("detached")},
            "rental": {"score": n["rental"][c], "raw": r.get("rental")},
            "mortgage": {"score": n["mortgage"][c], "raw": r.get("mortgage")},
            "low_density": {"score": n["low_density"][c], "raw": r.get("density")},
        }
        seifa_dec = r.get("irsad_decile") or 0
        fam_label = ("Family-friendly" if family >= 72 else
                     "OK for families" if family >= 50 else "Less family-oriented")
        market = {
            "median_house": r.get("median_house"), "median_unit": r.get("median_unit"),
            "house_12m": r.get("house_12m"), "house_3yr_cagr": r.get("house_3yr_cagr"),
            "house_year": r.get("house_year"), "unit_year": r.get("unit_year"),
            "growth_score": n["growth"][c],
            "growth_signal": _growth_signal(r.get("house_3yr_cagr")),
            "value_signal": _value_signal(n["price"][c], bool(r.get("median_house"))),
        }
        infra = {
            "score": infra_score,
            "advantage": _infra_signal(infra_score),
            "nearest_transmission_km": r.get("nearest_transmission_km"),
            "nearest_substation_km": r.get("nearest_substation_km"),
            "substation_count_10km": r.get("substation_count_10km"),
            "nearest_line_kv": r.get("nearest_line_kv"),
        }
        out[c] = {
            "name": r.get("name"), "sa3": r.get("sa3"), "sa4": r.get("sa4"),
            "lga": r.get("lga"), "population": r.get("population"),
            "live": live, "live_family": live_family, "dev": dev,
            "overall": overall, "grade": config.grade_for(overall),
            "family": {"score": family, "label": fam_label},
            "market": market,
            "infra": infra,
            "pillars": pillars,
            "explanation_live": _explain_live(pillars, family),
            "explanation_dev": _explain_dev(pillars),
            "explanation_invest": _explain_invest(market),
            "explanation_infra": _explain_infra(infra),
            "tags": _tags(pillars, family, seifa_dec, dev, market, infra_score),
        }
    return out
