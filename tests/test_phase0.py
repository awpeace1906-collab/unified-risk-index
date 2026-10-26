import csv
import os
import tempfile
import unittest
from datetime import timedelta

from uri import audit, datadict, samplesize, simulate

T0 = simulate.T0
H = timedelta(hours=1)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SampleSizeTest(unittest.TestCase):
    def test_reproduces_riley_worked_example(self):
        # pmsampsize(type="b", rsquared=0.288, parameters=24, prevalence=0.174) -> 662, 116 events
        d = samplesize.development(0.174, 24, 0.288)
        self.assertEqual((d["n_criterion_i"], d["n_criterion_ii"], d["n_criterion_iii"]), (623, 662, 221))
        self.assertEqual((d["n"], d["events"]), (662, 116))

    def test_more_parameters_need_more_patients(self):
        self.assertLess(samplesize.development(0.06, 4, 0.1)["n"], samplesize.development(0.06, 14, 0.1)["n"])

    def test_rejects_impossible_r2(self):
        with self.assertRaises(ValueError):
            samplesize.development(0.03, 4, 0.3)  # max R2cs at 3% is ~0.236

    def test_validation_floor_of_100_events(self):
        v = samplesize.validation(0.06)
        self.assertGreaterEqual(v["events"], 100)
        self.assertGreaterEqual(v["n"], v["n_min_events"])

    def test_lp_distribution_matches_rate_and_c(self):
        mu, sigma = samplesize.lp_distribution(0.06, 0.75)
        phi, c = samplesize._lp_moments(mu, sigma)
        self.assertAlmostEqual(phi, 0.06, places=4)
        self.assertAlmostEqual(c, 0.75, places=3)

    def test_calibration_slope_criterion(self):
        # checked by simulation: 300 validation sets of this n gave slope SD 0.052 (target 0.051)
        v = samplesize.validation(0.06, 0.75)
        self.assertEqual(v["binding"], "slope")
        self.assertAlmostEqual(v["n_calibration_slope"], 8763, delta=50)
        # a tighter slope CI needs about four times the patients
        mu, sigma = samplesize.lp_distribution(0.06, 0.75)
        self.assertAlmostEqual(samplesize.n_calibration_slope(mu, sigma, 0.1) / v["n_calibration_slope"], 4, delta=0.01)


class AuditTest(unittest.TestCase):
    def write(self, rows):
        fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
        cols = ["record_id", "triage_time", "admitted", "excluded_reason", "icu_transfer_time", "rrt_time",
                "intubation_time", "vasopressor_start_time", "death_time", "ed_revisit_72h"]
        w = csv.DictWriter(fh, cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
        fh.close()
        self.addCleanup(os.unlink, fh.name)
        return fh.name

    def test_rates_windows_and_exclusions(self):
        rows = [{"record_id": str(i), "triage_time": T0.isoformat(), "admitted": "Y"} for i in range(20)]
        rows[0]["icu_transfer_time"] = (T0 + 3 * H).isoformat()
        rows[1]["rrt_time"] = (T0 + 10 * H).isoformat()
        rows[1]["death_time"] = (T0 + 30 * H).isoformat()  # second component, same patient
        rows[2]["death_time"] = (T0 + 60 * H).isoformat()  # beyond 48 h
        rows[3]["icu_transfer_time"] = (T0 - 30 * 24 * H).isoformat()  # prior stay, not an outcome
        rows[4]["excluded_reason"] = "DNR/DNI without treatment intent"
        rows[5]["admitted"] = "N"
        included, excluded = audit.load_audit(self.write(rows))
        self.assertEqual((len(included), len(excluded)), (18, 2))
        s = audit.summarize(included)
        cum = {c["window"]: c["events"] for c in s["cumulative"]}
        self.assertEqual((cum["0-6h"], cum["0-12h"], cum["0-48h"]), (1, 2, 2))
        self.assertEqual([i["events"] for i in s["interval"]], [1, 1, 0, 0, 0])
        self.assertEqual(s["components"]["death"]["events"], 1)
        lo, hi = s["cumulative"][-1]["ci95"]
        self.assertTrue(lo < 2 / 18 < hi)

    def test_wilson_known_value(self):
        lo, hi = audit.wilson(12, 200)  # 6%: 3.5% - 10.2%
        self.assertAlmostEqual(lo, 0.0347, places=3)
        self.assertAlmostEqual(hi, 0.1018, places=3)

    def test_decision_rule(self):
        self.assertIn("reframe Phase 1", audit.decision(0.02))
        self.assertIn("as planned", audit.decision(0.06))

    def test_engine_outcomes_input(self):
        out = self.write([])
        with open(out, "w") as fh:
            fh.write("patient_id,time,type\np1,%s,intubation\np9,%s,death\n" % ((T0 + 5 * H).isoformat(), T0.isoformat()))
        idx = out + ".idx.csv"
        with open(idx, "w") as fh:
            fh.write("patient_id,triage_time\np1,%s\np2,%s\n" % (T0.isoformat(), T0.isoformat()))
        self.addCleanup(os.unlink, idx)
        s = audit.summarize(audit.from_engine(out, idx))
        self.assertEqual((s["n"], s["cumulative"][0]["events"]), (2, 1))


class DataDictionaryTest(unittest.TestCase):
    def test_committed_copy_matches_code(self):
        with open(os.path.join(ROOT, "docs", "DATA_DICTIONARY.md")) as fh:
            committed = fh.read().rstrip("\n")
        self.assertEqual(committed, datadict.render().rstrip("\n"),
                         "regenerate with: python -m uri.datadict > docs/DATA_DICTIONARY.md")


if __name__ == "__main__":
    unittest.main()
