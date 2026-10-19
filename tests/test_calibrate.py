import json
import os
import tempfile
import unittest

from uri import simulate
from uri.engine import Engine
from uri.model import FEATURES, Model

try:
    import numpy  # noqa: F401
    from uri import calibrate
except ImportError:  # calibration is an optional, numpy-based tool
    calibrate = None


@unittest.skipIf(calibrate is None, "numpy not installed")
class CalibrateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        events, outcomes = simulate.cohort(n=400, seed=1)
        cls.snaps = []
        engine = Engine(sinks=[cls.snaps.append])
        by_batch = {}
        for e in events:
            by_batch.setdefault((e.patient_id, e.time), []).append(e)
        for batch in by_batch.values():
            engine.ingest_many(batch)
        cls.outcomes = dict(outcomes)

    def test_auroc_helper(self):
        import numpy as np
        y = np.array([0, 0, 1, 1])
        self.assertEqual(calibrate.auroc(np.array([0.1, 0.2, 0.8, 0.9]), y), 1.0)
        self.assertEqual(calibrate.auroc(np.array([0.5, 0.5, 0.5, 0.5]), y), 0.5)

    def test_fit_discriminates_and_reports_comparators(self):
        spec = calibrate.calibrate(self.snaps, self.outcomes, landmark_min=60, n_boot=20)
        self.assertEqual(spec["features"], list(FEATURES))
        for h in ("6h", "12h", "24h", "36h", "48h"):
            r = spec["horizons"][h]
            self.assertGreater(r["events"], 10)
            self.assertGreater(r["auroc_corrected"], 0.7)
            self.assertLess(r["auroc_optimism"], 0.05)
            self.assertIn("news2", r["comparators_auroc"])

    def test_engine_uses_fitted_model(self):
        spec = calibrate.calibrate(self.snaps, self.outcomes)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(spec, fh)
        try:
            model = Model.load(fh.name)
            engine = Engine(model=model)
            snap = engine.ingest_many([e for e in simulate.cohort(1, seed=3)[0]])
            windows = next(iter(snap.values()))["time_windows"]
            self.assertTrue(all(w["calibrated"] for w in windows))
            pcts = [w["estimate_pct"] for w in windows]
            self.assertEqual(pcts, sorted(pcts))  # cumulative risk grows with the horizon
        finally:
            os.unlink(fh.name)

    def test_landmarks_stop_at_first_event(self):
        rows = calibrate.landmarks(self.snaps, self.outcomes, __import__("datetime").timedelta(minutes=60))
        for r in rows:
            if r["patient_id"] in self.outcomes:
                self.assertLess(r["_time"], self.outcomes[r["patient_id"]])


if __name__ == "__main__":
    unittest.main()
