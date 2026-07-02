"""Estimated Resident Population (ERP) per SA2 — ABS Regional Population.

Census 2021 counts are badly stale in growth corridors (Tarneit-North has
roughly doubled), which inflated per-100k crime rates exactly where the Invest
lens points. This module pulls the ABS "Regional population" data cube
(32180DS0001, ERP at 30 June by SA2) and returns the latest year's estimate.

Population *structure* ratios (child share, tenure) stay on Census 2021 — the
mix moves far more slowly than the level.
"""
from __future__ import annotations

import json

import openpyxl

from .. import config
from ..fetch import fetch

ERP_URL = ("https://www.abs.gov.au/statistics/people/population/regional-population/"
           "2024-25/32180DS0001_2024-25.xlsx")


def get_erp() -> dict[str, dict]:
    """{sa2_code: {"erp": latest ERP, "erp_year": June year}} for Victorian SA2s."""
    cache = config.DATA_RAW / "abs_erp_sa2.json"
    if cache.exists() and cache.stat().st_size > 0:
        print("  cached  abs_erp_sa2.json")
        return json.loads(cache.read_text(encoding="utf-8"))

    path = fetch(ERP_URL, "abs_erp_sa2.xlsx")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: dict[str, dict] = {}
    year = None
    for sn in wb.sheetnames:
        if not sn.lower().startswith("table"):
            continue
        i_code = i_erp = None
        tbl_year = None
        prev: tuple = ()
        for r in wb[sn].iter_rows(min_row=1, max_row=None, values_only=True):
            if i_code is None:
                # The "ERP at 30 June" year columns sit in the row just above the
                # header row (other "no." columns are migration components — skip).
                if any(str(v).strip() == "SA2 code" for v in r if v):
                    i_code = next(i for i, v in enumerate(r) if str(v).strip() == "SA2 code")
                    ycols = {int(v): i for i, v in enumerate(prev)
                             if isinstance(v, (int, float)) and 2000 < v < 2100}
                    if not ycols:
                        break
                    tbl_year = max(ycols)
                    i_erp = ycols[tbl_year]
                prev = r
                continue
            code = str(r[i_code] or "").strip()
            v = r[i_erp] if i_erp < len(r) else None
            if len(code) == 9 and code.isdigit() and isinstance(v, (int, float)):
                out[code] = {"erp": int(v), "erp_year": tbl_year}
                year = tbl_year
    wb.close()
    cache.write_text(json.dumps(out), encoding="utf-8")
    print(f"  erp: {len(out)} SA2s (ERP at 30 June {year})")
    return out


if __name__ == "__main__":  # pragma: no cover
    e = get_erp()
    for code, nm in (("213051584", "Tarneit - North"), ("206061138", "Toorak"),
                     ("208031192", "Moorabbin Airport")):
        print(f"  {nm:20} {e.get(code)}")
