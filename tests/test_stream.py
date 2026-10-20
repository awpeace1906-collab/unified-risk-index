import json
import threading
import unittest
import urllib.request
from datetime import timedelta

from uri import registry, simulate
from uri.config import Config
from uri.engine import Engine
from uri.events import CONDITION, MEASUREMENT, Event
from uri.fhir import FhirAdapter
from uri.series import Series

T0 = simulate.T0
H = timedelta(hours=1)
VITALS = {"hr": 76, "rr": 14, "sbp": 128, "dbp": 80, "spo2": 98, "temp_c": 36.8, "gcs": 15}


def vitals(pid, t, **over):
    return [Event(pid, t, MEASUREMENT, k, v) for k, v in {**VITALS, **over}.items()]


class RegistryTest(unittest.TestCase):
    def test_icd10_prefix_matching_ignores_dots(self):
        ids = {f.id for f in registry.factors_for_icd10("I50.22")}
        self.assertEqual(ids, {"chf", "cci_chf"})
        self.assertEqual({f.id for f in registry.factors_for_icd10("J44.1")}, {"copd", "cci_pulmonary"})

    def test_medication_classes(self):
        self.assertEqual([f.id for f in registry.medication_classes("Apixaban 5 MG Oral Tablet")], ["anticoagulant"])


class SeriesTest(unittest.TestCase):
    def test_out_of_order_and_correction(self):
        s = Series()
        s.add(T0 + 2 * H, 3.0)
        s.add(T0, 1.0)
        s.add(T0 + H, 2.0)
        s.add(T0 + H, 2.5)  # corrected result, same timestamp
        self.assertEqual([v for _, v in s], [1.0, 2.5, 3.0])
        self.assertEqual(s.latest(T0 + 90 * timedelta(minutes=1))[1], 2.5)
        self.assertIsNone(s.latest(T0 + 10 * H, max_age=H))


class EngineTest(unittest.TestCase):
    def test_trend_raises_index_above_static(self):
        e = Engine()
        e.ingest_many(vitals("p", T0) + [Event("p", T0, MEASUREMENT, "hgb", 12.0)])
        snap = e.ingest_many(vitals("p", T0 + 2 * H, hr=104, sbp=102) +
                             [Event("p", T0 + 2 * H, MEASUREMENT, "hgb", 9.5)])["p"]
        ids = {s["id"] for s in snap["trajectory"]["signals"]}
        self.assertIn("hgb_drop", ids)
        self.assertGreater(snap["uri"], snap["uri_static"])

    def test_floor_keeps_news2_tier(self):
        e = Engine()
        snap = e.ingest_many(vitals("p", T0, rr=26, hr=125, sbp=95))["p"]  # NEWS2 3+2+2 = 7
        self.assertEqual(snap["scores"]["news2"]["total"], 7)
        self.assertLess(snap["uri_static"], 61)
        self.assertEqual(snap["band"], "HIGH")
        self.assertIn("NEWS2 >= 7", snap["floor"]["reason"])

    def test_index_never_below_static(self):
        e = Engine()
        for i in range(5):
            snap = e.ingest_many(vitals("p", T0 + i * H, hr=120 - 10 * i))["p"]
            self.assertGreaterEqual(snap["uri"], snap["uri_static"])

    def test_kdigo_aki(self):
        e = Engine()
        e.ingest_many(vitals("p", T0) + [Event("p", T0, MEASUREMENT, "creatinine", 1.0)])
        snap = e.ingest_many([Event("p", T0 + 20 * H, MEASUREMENT, "creatinine", 1.35)])["p"]
        self.assertIn("aki", {s["id"] for s in snap["trajectory"]["signals"]})

    def test_late_lab_is_placed_in_time_order(self):
        e = Engine()
        e.ingest_many(vitals("p", T0 + 3 * H) + [Event("p", T0 + 3 * H, MEASUREMENT, "lactate", 4.0)])
        snap = e.ingest_many([Event("p", T0 + H, MEASUREMENT, "lactate", 2.5)])["p"]  # resulted late
        self.assertEqual(snap["time"], (T0 + 3 * H).isoformat())  # clock does not go back
        self.assertIn("lactate_rising", {s["id"] for s in snap["trajectory"]["signals"]})

    def test_stale_and_expired_values(self):
        e = Engine()
        e.ingest_many(vitals("p", T0))
        snap = e.tick(T0 + 3 * H)["p"]  # NEWS2 0: vitals current for 12 h
        self.assertNotIn("hr", {x["parameter"] for x in snap["data_quality"]["stale"]})
        snap = e.tick(T0 + 13 * H)["p"]  # past the 12 h usable horizon
        self.assertIn("rr", snap["scores"]["news2"]["missing"])

    def test_alerts_held_until_full_vitals(self):
        e = Engine()
        e.ingest(Event("p", T0, MEASUREMENT, "hr", 80))
        snap = e.ingest(Event("p", T0, MEASUREMENT, "rr", 30))
        self.assertEqual(snap["alerts"], [])

    def test_undated_condition_alone_gives_no_snapshot(self):
        e = Engine()
        from uri.events import EPOCH
        self.assertIsNone(e.ingest(Event("p", EPOCH, CONDITION, "chf")))

    def test_sink_receives_every_snapshot(self):
        seen = []
        e = Engine(sinks=[seen.append])
        e.ingest_many(vitals("p", T0))
        e.ingest_many(vitals("p", T0 + H))
        self.assertEqual(len(seen), 2)


class RiseTest(unittest.TestCase):
    def test_first_lab_is_not_a_sofa_rise(self):
        e = Engine()
        e.ingest_many(vitals("p", T0))
        snap = e.ingest_many([Event("p", T0 + H, MEASUREMENT, "creatinine", 3.8),
                              Event("p", T0 + H, MEASUREMENT, "plt", 40)])["p"]
        self.assertGreaterEqual(snap["scores"]["sofa"]["total"], 6)
        self.assertNotIn("sofa_rise", {s["id"] for s in snap["trajectory"]["signals"]})

    def test_repeat_lab_worsening_is_a_sofa_rise(self):
        e = Engine()
        e.ingest_many(vitals("p", T0) + [Event("p", T0, MEASUREMENT, "plt", 180),
                                         Event("p", T0, MEASUREMENT, "creatinine", 1.0)])
        snap = e.ingest_many([Event("p", T0 + 6 * H, MEASUREMENT, "plt", 90),
                              Event("p", T0 + 6 * H, MEASUREMENT, "creatinine", 2.2)])["p"]
        self.assertIn("sofa_rise", {s["id"] for s in snap["trajectory"]["signals"]})

    def test_rox_low_on_high_flow(self):
        e = Engine()
        snap = e.ingest_many(vitals("p", T0, spo2=88, rr=34) + [Event("p", T0, MEASUREMENT, "fio2", 70)])["p"]
        self.assertIn("rox_low", {s["id"] for s in snap["trajectory"]["signals"]})


class FhirTest(unittest.TestCase):
    def setUp(self):
        self.a = FhirAdapter()

    def test_bp_panel_and_units(self):
        bp = simulate._bp("p", T0, 90, 50, 1)
        self.assertEqual({(e.code, e.value) for e in self.a.events(bp)}, {("sbp", 90.0), ("dbp", 50.0)})
        temp = {**simulate._obs("p", T0, "8310-5", 101.3, 2), "valueQuantity": {"value": 101.3, "code": "[degF]"}}
        self.assertEqual(self.a.events(temp)[0].value, 38.5)
        glu = {**simulate._obs("p", T0, "2345-7", 5.0, 3), "valueQuantity": {"value": 5.0, "code": "mmol/L"}}
        self.assertAlmostEqual(self.a.events(glu)[0].value, 90.08)

    def test_o2_flow_means_supplemental(self):
        obs = simulate._obs("p", T0, "3151-8", 2, 4)
        self.assertEqual({(e.code, e.value) for e in self.a.events(obs)},
                         {("o2_flow", 2.0), ("o2_supplemental", True)})

    def test_entered_in_error_skipped(self):
        obs = {**simulate._obs("p", T0, "8867-4", 80, 5), "status": "entered-in-error"}
        self.assertEqual(self.a.events(obs), [])

    def test_resolved_condition_inactive(self):
        c = simulate._condition("p", "I50.9", 1)
        c["clinicalStatus"] = {"coding": [{"code": "resolved"}]}
        events = self.a.events(c)
        self.assertEqual({(e.code, e.active) for e in events}, {("chf", False), ("cci_chf", False)})

    def test_medication_request(self):
        m = {"resourceType": "MedicationRequest", "id": "m1", "status": "active",
             "subject": {"reference": "Patient/p"}, "authoredOn": T0.isoformat(),
             "medicationCodeableConcept": {"text": "insulin glargine 100 unit/mL"}}
        events = self.a.events(m)
        self.assertEqual({(e.kind, e.code, e.active) for e in events},
                         {("medication", "insulin", True), ("medication", "rx:insulin", True)})

    def test_bundle_and_unknown_resources(self):
        bundle = {"resourceType": "Bundle", "entry": [
            {"resource": simulate._obs("p", T0, "8867-4", 80, 1)},
            {"resource": {"resourceType": "Appointment", "id": "e"}}]}
        self.assertEqual(len(self.a.events(bundle)), 1)
        self.assertEqual(self.a.skipped["resource:Appointment"], 1)


class ScenarioTest(unittest.TestCase):
    def run_scenario(self, name, cfg=None):
        a = FhirAdapter()
        e = Engine(cfg)
        for r in simulate.stream([name]):
            e.ingest_many(a.events(r))
        return e.latest(f"sim-{name}")

    def test_sepsis_reaches_critical(self):
        snap = self.run_scenario("sepsis")
        self.assertEqual(snap["band"], "CRITICAL")
        self.assertIn("Sepsis / undifferentiated infection", {c["cause"] for c in snap["correctable_causes"]})

    def test_sepsis_shows_new_organ_dysfunction(self):
        snap = self.run_scenario("sepsis")
        self.assertIn("sofa_rise", {s["id"] for s in snap["trajectory"]["signals"]})
        self.assertGreaterEqual(snap["scores"]["sofa"]["rise_48h"], 2)

    def test_gi_bleed_caught_by_trend_without_sepsis_cause(self):
        snap = self.run_scenario("gi_bleed")
        causes = {c["cause"] for c in snap["correctable_causes"]}
        self.assertIn("GI bleed / hemorrhagic shock", causes)
        self.assertNotIn("Sepsis / undifferentiated infection", causes)
        self.assertGreater(snap["uri"], snap["uri_static"])

    def test_stable_stays_low(self):
        snap = self.run_scenario("stable")
        self.assertEqual(snap["band"], "LOW")
        self.assertEqual(snap["correctable_causes"], [])


class ServerTest(unittest.TestCase):
    def test_post_and_get(self):
        from uri.server import serve
        e = Engine()
        httpd = serve(e, FhirAdapter(), port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            bundle = {"resourceType": "Bundle", "entry": [{"resource": r} for r in simulate.stable("s1")]}
            req = urllib.request.Request(base + "/fhir", json.dumps(bundle).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 202)
            with urllib.request.urlopen(base + "/patients") as resp:
                rows = json.load(resp)
            self.assertEqual(rows[0]["patient_id"], "s1")
            with urllib.request.urlopen(base + "/patients/s1/history") as resp:
                self.assertTrue(json.load(resp))
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
