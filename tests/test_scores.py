import unittest

from uri import scores
from uri.config import Config

NORMAL = {"hr": 75, "rr": 16, "sbp": 125, "dbp": 80, "spo2": 98, "temp_c": 37.0, "gcs": 15}


class News2Test(unittest.TestCase):
    def test_normal_is_zero(self):
        r = scores.news2(NORMAL)
        self.assertEqual(r.total, 0)
        self.assertEqual(r.missing, [])

    def test_rr_bands(self):
        for rr, pts in [(8, 3), (9, 1), (11, 1), (12, 0), (20, 0), (21, 2), (24, 2), (25, 3)]:
            self.assertEqual(scores.news2({**NORMAL, "rr": rr}).components["rr"], pts, rr)

    def test_temp_bands(self):
        for t, pts in [(35.0, 3), (35.1, 1), (36.0, 1), (36.1, 0), (38.0, 0), (38.1, 1), (39.0, 1), (39.1, 2)]:
            self.assertEqual(scores.news2({**NORMAL, "temp_c": t}).components["temp_c"], pts, t)

    def test_hr_and_sbp_bands(self):
        for hr, pts in [(40, 3), (41, 1), (50, 1), (51, 0), (90, 0), (91, 1), (110, 1), (111, 2), (130, 2), (131, 3)]:
            self.assertEqual(scores.news2({**NORMAL, "hr": hr}).components["hr"], pts, hr)
        for sbp, pts in [(90, 3), (91, 2), (100, 2), (101, 1), (110, 1), (111, 0), (219, 0), (220, 3)]:
            self.assertEqual(scores.news2({**NORMAL, "sbp": sbp}).components["sbp"], pts, sbp)

    def test_supplemental_oxygen_adds_two(self):
        self.assertEqual(scores.news2({**NORMAL, "o2_supplemental": True}).total, 2)
        self.assertEqual(scores.news2({**NORMAL, "fio2": 40}).total, 2)

    def test_any_non_alert_scores_three(self):
        self.assertEqual(scores.news2({**NORMAL, "gcs": 14}).components["consciousness"], 3)
        self.assertEqual(scores.news2({**NORMAL, "avpu": "C"}).components["consciousness"], 3)

    def test_scale2_for_hypercapnic_target(self):
        on_air = {**NORMAL, "spo2": 89}
        self.assertEqual(scores.news2(on_air).components["spo2"], 3)
        self.assertEqual(scores.news2(on_air, hypercapnic_target=True).components["spo2"], 0)
        on_o2 = {**NORMAL, "spo2": 97, "o2_supplemental": True}
        self.assertEqual(scores.news2(on_o2, hypercapnic_target=True).components["spo2"], 3)

    def test_max_is_twenty(self):
        worst = {"hr": 150, "rr": 30, "sbp": 80, "spo2": 85, "temp_c": 34, "gcs": 6, "o2_supplemental": True}
        self.assertEqual(scores.news2(worst).total, 20)

    def test_missing_reported(self):
        r = scores.news2({"hr": 80})
        self.assertIn("rr", r.missing)
        self.assertIn("consciousness", r.missing)


class QsofaMewsTest(unittest.TestCase):
    def test_qsofa(self):
        self.assertEqual(scores.qsofa(NORMAL).total, 0)
        r = scores.qsofa({**NORMAL, "rr": 22, "sbp": 100, "gcs": 14})
        self.assertEqual(r.total, 3)
        self.assertTrue(r.flags)

    def test_mews_max_is_fourteen(self):
        worst = {"hr": 140, "rr": 35, "sbp": 60, "temp_c": 34, "gcs": 5}
        self.assertEqual(scores.mews(worst).total, 14)
        self.assertEqual(scores.mews(NORMAL).total, 1)  # RR 15-20 scores 1 in MEWS


class CompositeTest(unittest.TestCase):
    def test_all_max_is_100_every_profile(self):
        totals = {"news2": 20, "qsofa": 3, "mews": 14, "crs": 30, "sofa": 24, "crs2": 36}
        for cfg in (Config(), Config(include_mews=False)):
            self.assertEqual(scores.composite(totals, cfg.weights_v1(), cfg)[0], 100.0)
        self.assertEqual(scores.composite(totals, Config().weights_v2, Config())[0], 100.0)

    def test_v1_weights_match_manuscript(self):
        cfg = Config()
        zero = {"news2": 10, "qsofa": 0, "mews": 0, "crs": 0}
        self.assertEqual(scores.composite(zero, cfg.weights_v1(), cfg)[0], 20.0)  # 10/20 * 0.40
        cfg = Config(include_mews=False)
        self.assertEqual(scores.composite(zero, cfg.weights_v1(), cfg)[0], 25.0)  # 10/20 * 0.50

    def test_bands(self):
        for s, b in [(0, "LOW"), (20, "LOW"), (20.4, "LOW"), (21, "LOW-MODERATE"), (41, "MODERATE"),
                     (61, "HIGH"), (80.9, "CRITICAL"), (81, "CRITICAL")]:
            self.assertEqual(scores.band(s), b, s)

    def test_time_windows_cap(self):
        w = scores.time_windows(90, Config())
        self.assertEqual(w[0], {"window": "0-6h", "estimate_pct": 99, "level": "VERY HIGH"})
        self.assertEqual(w[-1]["estimate_pct"], 68)


class CrsTest(unittest.TestCase):
    def test_domains(self):
        v = {"ph": 7.2, "lactate": 4.5, "creatinine": 2.0, "hgb": 6.5, "troponin_hs": 50, "bnp": 900}
        r = scores.crs(v, {"age": 85, "cfs": 6}, {"chf", "copd"}, {"insulin"}, 0, 0, Config())
        d = r.components
        self.assertEqual((d["metabolic"], d["renal"], d["hematologic"], d["cardiac"]), (8, 2, 4, 5))
        self.assertEqual((d["comorbidity"], d["medication"], d["frailty_age"]), (4.0, 1.5, 4))
        self.assertEqual(r.total, 28.5)

    def test_capped_at_30(self):
        v = {"ph": 7.0, "lactate": 9, "creatinine": 5, "hgb": 5, "troponin_hs": 500, "bnp": 2000}
        r = scores.crs(v, {"age": 90, "cfs": 8}, {"chf", "copd", "cad", "ckd", "malignancy"},
                       {"anticoagulant", "insulin", "opioid"}, 20, 3, Config())
        self.assertEqual(r.total, 30)

    def test_unweighted_factors_add_nothing(self):
        r = scores.crs({}, {}, {"htn", "ob_accreta", "cci_chf"}, {"diuretic"}, 0, 0, Config())
        self.assertEqual(r.total, 0)


class SofaTest(unittest.TestCase):
    def test_normal_is_zero(self):
        v = {**NORMAL, "plt": 250, "bilirubin": 0.6, "creatinine": 0.9}
        r = scores.sofa(v, set())
        self.assertEqual(r.total, 0)
        self.assertEqual(r.missing, [])

    def test_organ_bands(self):
        v = {**NORMAL, "spo2": 98, "plt": 45, "bilirubin": 6.5, "creatinine": 3.6, "gcs": 9, "sbp": 85, "dbp": 50}
        d = scores.sofa(v, set()).components
        self.assertEqual((d["coagulation"], d["liver"], d["renal"], d["cns"], d["cardiovascular"]), (3, 3, 3, 3, 1))

    def test_vasoactive_scores_three(self):
        r = scores.sofa({**NORMAL}, {"vasoactive"})
        self.assertEqual(r.components["cardiovascular"], 3)

    def test_respiration_from_pao2_and_support(self):
        # P/F 150 on HFNC FiO2 60% -> 3; same ratio without support caps at 2
        self.assertEqual(scores.sofa({"po2": 90, "fio2": 60}, set()).components["respiration"], 3)
        self.assertEqual(scores.sofa({"po2": 30}, set()).components["respiration"], 2)

    def test_respiration_from_sf_surrogate(self):
        r = scores.sofa({"spo2": 90}, set())  # S/F 429 on room air -> P/F-equivalent ~434
        self.assertEqual(r.components["respiration"], 0)
        r = scores.sofa({"spo2": 92, "o2_supplemental": True, "o2_flow": 4}, set())  # FiO2 ~37%
        self.assertEqual(r.components["respiration"], 2)
        self.assertIn("respiration from S/F surrogate", r.flags)

    def test_unknown_fio2_is_missing(self):
        self.assertIn("respiration", scores.sofa({"spo2": 95, "o2_supplemental": True}, set()).missing)


class CharlsonTest(unittest.TestCase):
    def test_weights_and_hierarchy(self):
        r = scores.charlson({"cci_chf", "cci_dm", "cci_dm_complicated", "cci_malignancy", "cci_metastatic"})
        self.assertEqual(r.components, {"cci_chf": 2, "cci_dm_complicated": 1, "cci_metastatic": 6})
        self.assertEqual(r.total, 9)


class Crs2Test(unittest.TestCase):
    def test_no_vital_or_sofa_double_counting(self):
        v = {**NORMAL, "rr": 30, "sbp": 80, "creatinine": 4.0, "plt": 30, "bilirubin": 8}
        self.assertEqual(scores.crs2(v, {"age": 40}, set(), set(), 0, Config()).total, 0)

    def test_domains(self):
        v = {"lactate": 4.5, "ph": 7.15, "k": 6.4, "glucose": 45, "hgb": 8, "inr": 3.5,
             "troponin_hs": 80, "procalcitonin": 3, "wbc": 22}
        r = scores.crs2(v, {"age": 70}, set(), {"opioid"}, 9, Config())
        d = r.components
        self.assertEqual((d["metabolic"], d["electrolyte_glucose"], d["hematologic"], d["cardiac"],
                          d["infection"], d["comorbidity_charlson"], d["medication"], d["frailty_age"]),
                         (6, 4, 4, 3, 4, 6, 1.0, 1))

    def test_obstetric_domain_only_when_module_active(self):
        cfg = Config(obstetric_module=True)
        r = scores.crs2({}, {}, {"ob_previa_abruption"}, set(), 0, cfg, ob_active=True)
        self.assertAlmostEqual(r.components["obstetric_hemorrhage"], 2.02, places=2)  # 2 * ln(2.74)
        r = scores.crs2({}, {}, {"ob_previa_abruption"}, set(), 0, cfg, ob_active=False)
        self.assertNotIn("obstetric_hemorrhage", r.components)


if __name__ == "__main__":
    unittest.main()
