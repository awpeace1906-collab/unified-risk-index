"""Arrival-time history, pre-episode baselines and episode handling."""

import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from datetime import timedelta

from uri import baseline, simulate
from uri.__main__ import _run
from uri.engine import Engine
from uri.events import EPOCH, MEASUREMENT, OUTCOME, Event
from uri.fhir import FhirAdapter
from uri.history import FhirHistory
from uri.series import Series

T0 = simulate.T0
H = timedelta(hours=1)
D = timedelta(days=1)
VITALS = {"hr": 76, "rr": 14, "sbp": 128, "dbp": 80, "spo2": 98, "temp_c": 36.8, "gcs": 15}


def vitals(pid, t, **over):
    return [Event(pid, t, MEASUREMENT, k, v) for k, v in {**VITALS, **over}.items()]


def lab(pid, t, param, value):
    return Event(pid, t, MEASUREMENT, param, value)


def signal_ids(snap):
    return {s["id"] for s in snap["trajectory"]["signals"]}


class EpisodeAndBaselineTest(unittest.TestCase):
    def test_episode_start_breaks_on_gap(self):
        times = [T0 - 40 * D, T0 - 39 * D, T0, T0 + H, T0 + 20 * H, T0 + 50 * H]
        self.assertEqual(baseline.episode_start(times, T0 + 20 * H, 24 * H), T0)
        self.assertEqual(baseline.episode_start(times, T0 + 50 * H, 24 * H), T0 + 50 * H)  # 30 h gap
        self.assertEqual(baseline.episode_start(times, T0 - 39 * D, 24 * H), T0 - 40 * D)
        self.assertIsNone(baseline.episode_start(times, T0 - 41 * D, 24 * H))

    def test_creatinine_recent_minimum_else_older_median(self):
        cr = Series()
        for days, x in ((200, 1.0), (100, 1.4), (60, 1.2)):
            cr.add(T0 - days * D, x)
        b = baseline.compute({"creatinine": cr}, T0)
        self.assertEqual(b["creatinine"]["value"], 1.2)  # median of 8-365 d
        cr.add(T0 - 3 * D, 1.6)
        cr.add(T0 - 2 * D, 1.5)
        b = baseline.compute({"creatinine": cr}, T0)
        self.assertEqual((b["creatinine"]["value"], b["creatinine"]["method"]), (1.5, "lowest in 7 d before episode"))

    def test_baseline_sofa_and_lookback(self):
        plt, bili, hgb = Series(), Series(), Series()
        plt.add(T0 - 30 * D, 90)
        bili.add(T0 - 30 * D, 2.5)
        hgb.add(T0 - 400 * D, 14.0)  # outside the 365-day look-back
        b = baseline.compute({"plt": plt, "bilirubin": bili, "hgb": hgb}, T0)
        self.assertEqual(b["sofa"]["components"], {"coagulation": 2, "liver": 2})
        self.assertNotIn("hgb", b)


class BaselineScoringTest(unittest.TestCase):
    def ckd_engine(self, prior_cr):
        e = Engine()
        e.preload([lab("p", T0 - d * D, "creatinine", prior_cr) for d in (90, 60, 30)])
        return e

    def test_ckd_is_not_new_organ_failure(self):
        with_history = self.ckd_engine(2.1).ingest_many(vitals("p", T0) + [lab("p", T0, "creatinine", 2.2)])["p"]
        without = Engine().ingest_many(vitals("p", T0) + [lab("p", T0, "creatinine", 2.2)])["p"]
        self.assertEqual(with_history["scores"]["sofa"]["components"]["renal"], 2)
        self.assertEqual(with_history["scores"]["sofa"]["acute"], 0)
        self.assertEqual(without["scores"]["sofa"]["acute"], 2)  # baseline unknown: assumed 0 (Sepsis-3)
        self.assertLess(with_history["uri_static"], without["uri_static"])
        self.assertNotIn("aki", signal_ids(with_history))

    def test_aki_staged_on_arrival_from_baseline(self):
        snap = self.ckd_engine(1.0).ingest_many(vitals("p", T0) + [lab("p", T0, "creatinine", 2.3)])["p"]
        aki = next(s for s in snap["trajectory"]["signals"] if s["id"] == "aki")
        self.assertIn("stage 2", aki["detail"])
        self.assertEqual(snap["scores"]["sofa"]["acute"], 2)
        self.assertIn("sofa_rise", signal_ids(snap))
        self.assertIn("Sepsis-3", snap["floor"]["reason"])

    def test_hgb_below_baseline_at_arrival(self):
        e = Engine()
        e.preload([lab("p", T0 - d * D, "hgb", 13.0) for d in (120, 40)])
        snap = e.ingest_many(vitals("p", T0) + [lab("p", T0, "hgb", 9.8)])["p"]
        hgb = next(s for s in snap["trajectory"]["signals"] if s["id"] == "hgb_drop")
        self.assertEqual(hgb["points"], 3)
        self.assertIn("below baseline 13", hgb["detail"])

    def test_chronic_anemia_is_not_a_drop(self):
        e = Engine()
        e.preload([lab("p", T0 - d * D, "hgb", 8.6) for d in (120, 40)])
        snap = e.ingest_many(vitals("p", T0) + [lab("p", T0, "hgb", 8.4)])["p"]
        self.assertNotIn("hgb_drop", signal_ids(snap))

    def test_preload_emits_no_snapshot(self):
        seen = []
        e = Engine(sinks=[seen.append])
        e.preload(vitals("p", T0 - 30 * D) + [lab("p", T0 - 30 * D, "creatinine", 1.0)])
        self.assertEqual(seen, [])
        e.ingest_many(vitals("p", T0))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["episode_start"], T0.isoformat())

    def test_prior_admission_icu_stay_is_not_an_outcome(self):
        e = Engine()
        e.preload(vitals("p", T0 - 60 * D) + [Event("p", T0 - 59 * D, OUTCOME, "icu_transfer")])
        e.ingest_many(vitals("p", T0))
        self.assertEqual(e.outcomes(), [])
        e.ingest_many([Event("p", T0 + 5 * H, OUTCOME, "icu_transfer")])
        self.assertEqual([(k, t) for _, k, t in e.outcomes()], [("icu_transfer", T0 + 5 * H)])


def bundle(resources, next_url=None):
    b = {"resourceType": "Bundle", "entry": [{"resource": r} for r in resources]}
    if next_url:
        b["link"] = [{"relation": "next", "url": next_url}]
    return b


class FakeFhir:
    """Answers FhirHistory queries from canned data; records calls."""

    def __init__(self, pid):
        self.calls, self.headers = [], []
        self.pid = pid

    def __call__(self, url, headers):
        self.calls.append(url)
        self.headers.append(headers)
        path = url.split("/fhir/", 1)[1]
        if path.startswith("Patient/"):
            return {"resourceType": "Patient", "id": self.pid, "birthDate": "1950-01-01"}
        if path.startswith("Observation") and "code=" in path:
            return bundle([simulate._obs(self.pid, T0 - 60 * D, "2160-0", 2.1, 1)], next_url="http://ehr/fhir/page2")
        if path == "page2":
            return bundle([simulate._obs(self.pid, T0 - 30 * D, "2160-0", 2.0, 2),
                           simulate._obs(self.pid, T0 - 60 * D, "2160-0", 2.1, 1)])  # duplicate id
        if path.startswith("Condition"):
            return bundle([simulate._condition(self.pid, "N18.4", 1)])
        if path.startswith("Encounter"):
            raise urllib.error.URLError("timed out")
        return bundle([])


class HistoryClientTest(unittest.TestCase):
    def test_queries_pagination_dedupe_auth_and_errors(self):
        fake = FakeFhir("p1")
        h = FhirHistory("http://ehr/fhir/", token="secret", fetch=fake)
        resources = h.resources("p1", T0)
        kinds = [r["resourceType"] for r in resources]
        self.assertEqual(kinds.count("Observation"), 2)  # page 2 followed, duplicate dropped
        self.assertIn("Condition", kinds)
        self.assertEqual(len(h.errors), 1)
        self.assertTrue(h.errors[0].startswith("Encounter"))
        self.assertTrue(all(hd["Authorization"] == "Bearer secret" for hd in fake.headers))
        self.assertTrue(any("date=ge2025-01-01" in c for c in fake.calls))  # 365-day look-back


class ServerHistoryTest(unittest.TestCase):
    def test_first_sight_pulls_history_once(self):
        from uri.server import serve
        fake = FakeFhir("s1")
        e = Engine()
        httpd = serve(e, FhirAdapter(), port=0, history=FhirHistory("http://ehr/fhir", fetch=fake))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def post(resources):
            req = urllib.request.Request(base + "/fhir", json.dumps(bundle(resources)).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                return json.load(resp)
        try:
            out = post([simulate._obs("s1", T0, "8867-4", 88, 10), simulate._obs("s1", T0, "2160-0", 2.2, 11)])
            snap = out["snapshots"][0]
            self.assertEqual(snap["baseline"]["creatinine"]["value"], 2.05)  # median of 2.1 and 2.0
            self.assertEqual(snap["scores"]["sofa"]["acute"], 0)
            n_calls = len(fake.calls)
            post([simulate._obs("s1", T0 + H, "8867-4", 90, 12)])
            self.assertEqual(len(fake.calls), n_calls)  # not pulled again
        finally:
            httpd.shutdown()
            httpd.server_close()


class ReplayOrderTest(unittest.TestCase):
    def test_bulk_export_order_is_sorted_by_time(self):
        # Bulk exports group by resource type: a later Hgb can precede earlier vitals.
        records = [simulate._obs("r", T0 + 6 * H, "718-7", 9.0, 1), simulate._obs("r", T0, "718-7", 12.5, 2)]
        records += list(simulate._vitals("r", T0, 10, 80, 16, 120, 70, 98, 37.0, 15))
        records += list(simulate._vitals("r", T0 + 6 * H, 20, 112, 22, 98, 60, 96, 37.0, 15))
        e = Engine()
        with redirect_stdout(io.StringIO()):
            _run(e, FhirAdapter(), records)
        snap = e.latest("r")
        self.assertEqual(snap["time"], (T0 + 6 * H).isoformat())
        self.assertIn("hgb_drop", signal_ids(snap))
        self.assertEqual(len(e.history("r")), 2)  # one snapshot per clinical time


class CalibrateEpisodeTest(unittest.TestCase):
    def test_landmarks_use_latest_episode_only(self):
        try:
            from uri.calibrate import landmarks
        except ImportError:
            self.skipTest("numpy not installed")
        snaps = []
        e = Engine(sinks=[snaps.append])
        e.ingest_many(vitals("p", T0 - 60 * D))  # prior admission
        e.ingest_many(vitals("p", T0))
        e.ingest_many(vitals("p", T0 + 2 * H))
        rows = landmarks(snaps, {}, timedelta(minutes=60))
        self.assertEqual({r["episode_start"] for r in rows}, {T0.isoformat()})
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
