"""Engine unit tests — stdlib unittest, no extra deps.

Run:  python -m unittest discover -s engine/tests -v
Covers the scoring maths and matching helpers that regress most easily.
"""
import unittest

from engine import config
from engine.score import _percentiles, _weighted, compute_scores
from engine.sources.crime import _norm_lga, _sa2_localities


def _synthetic_record(name, **over):
    """A plausible fully-populated SA2 record for compute_scores."""
    base = {
        "name": name, "sa3": "Test SA3", "sa4": "Melbourne - Test", "lga": "Testville",
        "person_crime": 500.0, "property_crime": 3000.0, "total_crime": 5000.0,
        "person_trend_pct": -10.0, "crime_source": "suburb", "precinct": False,
        "irsad_score": 1000, "irsad_decile": 5, "ieo_score": 1000, "ieo_decile": 5,
        "population": 10000, "pop_year": 2025, "pop_growth_pct": 1.5,
        "income_weekly": 2000.0, "density": 2000.0, "child_share": 0.18,
        "owner_occ": 0.6, "mortgage": 0.3, "rental": 0.3, "social": 0.02, "detached": 0.7,
        "median_house": 900000.0, "median_unit": 600000.0,
        "house_12m": 2.0, "house_3yr_cagr": 3.0, "unit_12m": 1.0, "unit_3yr_cagr": 2.0,
        "house_year": 2024, "unit_year": 2024, "house_series": [[2020, 800000], [2024, 900000]],
        "rent_weekly": 550.0, "rent_12m": 4.0, "rent_bonds": 400.0,
        "rent_quarter": "Sep 2025", "rent_source": "suburb",
        "yield_house": 3.2, "yield_unit": 4.5, "yield_basis": "house",
        "nearest_station_km": 1.2, "nearest_station": "Test", "stations_3km": 2,
        "station_pax": 500000, "metro_km": 1.2, "metro_station": "Test", "metro_pax": 500000,
        "vline_km": 20.0, "vline_station": "Far", "vline_pax": 100000,
        "nearest_primary_km": 0.8, "nearest_secondary_km": 1.5, "schools_3km": 10,
        "zoning_raw": 0.3, "growth_share": 0.1, "standard_share": 0.5, "restrict_share": 0.1,
        "ugz_share": 0.0, "parks_share": 0.1, "heritage_share": 0.05,
        "flood_share": 0.02, "bushfire_share": 0.0, "noise_share": 0.0,
        "zone_mix": [["GRZ", 0.5]],
        "nearest_transmission_km": 2.0, "nearest_substation_km": 3.0,
        "substation_count_10km": 5, "nearest_line_kv": 220,
    }
    base.update(over)
    return base


class TestPercentiles(unittest.TestCase):
    def test_missing_stays_none(self):
        out = _percentiles({"a": 1.0, "b": None, "c": 3.0})
        self.assertIsNone(out["b"])
        self.assertLess(out["a"], out["c"])

    def test_invert(self):
        out = _percentiles({"a": 1.0, "b": 10.0}, invert=True)
        self.assertGreater(out["a"], out["b"])

    def test_all_missing(self):
        out = _percentiles({"a": None})
        self.assertIsNone(out["a"])


class TestWeighted(unittest.TestCase):
    def test_renormalises_over_present(self):
        val, used = _weighted({"x": 100.0, "y": None}, {"x": 0.5, "y": 0.5})
        self.assertEqual(val, 100.0)   # y missing -> x carries full weight
        self.assertEqual(used, 1)

    def test_all_missing_returns_none(self):
        val, used = _weighted({"x": None}, {"x": 1.0})
        self.assertIsNone(val)
        self.assertEqual(used, 0)


class TestComputeScores(unittest.TestCase):
    def setUp(self):
        self.records = {
            "1": _synthetic_record("Alpha", person_crime=300.0, median_house=700000.0),
            "2": _synthetic_record("Beta", person_crime=900.0, median_house=1200000.0),
            "3": _synthetic_record("Gamma", median_house=None, yield_house=None,
                                   yield_unit=None, house_3yr_cagr=None, house_12m=None,
                                   house_series=[]),
        }

    def test_runs_and_stretches(self):
        out = compute_scores(self.records)
        self.assertEqual(len(out), 3)
        for a in out.values():
            for k in ("live", "live_family", "dev", "dev_green", "dev_infill", "overall"):
                self.assertIsInstance(a[k], float)
                self.assertTrue(0 <= a[k] <= 100, f"{k}={a[k]}")
            self.assertIn(a["grade"], ("A+", "A", "B", "C", "D"))

    def test_safer_scores_higher_on_safety_pillar(self):
        out = compute_scores(self.records)
        self.assertGreater(out["1"]["pillars"]["person_safety"]["score"],
                           out["2"]["pillars"]["person_safety"]["score"])

    def test_missing_price_renormalises(self):
        out = compute_scores(self.records)
        cov = out["3"]["coverage"]
        self.assertLess(cov["dev_inputs"][0], cov["dev_inputs"][1])
        self.assertIsNone(out["3"]["pillars"]["yield"]["score"])

    def test_yield_basis_reflects_value_used(self):
        recs = {"1": _synthetic_record("A", yield_house=None, yield_unit=4.4,
                                       yield_basis="house"),
                "2": _synthetic_record("B")}
        out = compute_scores(recs)
        self.assertEqual(out["1"]["market"]["yield_basis"], "unit")
        self.assertEqual(out["1"]["market"]["yield_headline"], 4.4)

    def test_precinct_uses_flag(self):
        recs = {"1": _synthetic_record("Airport", precinct=True),
                "2": _synthetic_record("B")}
        out = compute_scores(recs)
        self.assertIn("Employment precinct", out["1"]["tags"])
        self.assertIn("employment precinct", out["1"]["explanation_live"].lower())


class TestMatching(unittest.TestCase):
    def test_norm_lga_alias(self):
        self.assertEqual(_norm_lga("Moreland (C)"), "merri-bek")
        self.assertEqual(_norm_lga(" Banyule "), "banyule")

    def test_sa2_localities(self):
        locs = _sa2_localities("Carlton North - Princes Hill")
        self.assertIn("CARLTON NORTH", locs)
        self.assertIn("PRINCES HILL", locs)

    def test_cbd_alias(self):
        self.assertIn("MELBOURNE", _sa2_localities("Melbourne CBD - East"))

    def test_compass_words_dropped(self):
        self.assertNotIn("NORTH", _sa2_localities("Reservoir - North"))


class TestGrades(unittest.TestCase):
    def test_grade_tiers(self):
        self.assertEqual(config.grade_for(95), "A+")
        self.assertEqual(config.grade_for(80), "A")
        self.assertEqual(config.grade_for(60), "B")
        self.assertEqual(config.grade_for(35), "C")
        self.assertEqual(config.grade_for(10), "D")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
