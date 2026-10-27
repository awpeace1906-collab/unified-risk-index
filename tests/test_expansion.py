"""v1 risk-factor coverage, critical-care inputs, outcome capture, new causes."""

import unittest
from datetime import timedelta

from uri import labs, scores, simulate
from uri.causes import Facts, evaluate
from uri.config import Config
from uri.engine import Engine
from uri.events import CONDITION, CONTEXT, EPOCH, MEASUREMENT, MEDICATION, OUTCOME, Event
from uri.fhir import FhirAdapter

T0 = simulate.T0
H = timedelta(hours=1)
VITALS = {"hr": 76, "rr": 14, "sbp": 128, "dbp": 80, "spo2": 98, "temp_c": 36.8, "gcs": 15}


def vitals(pid, t, **over):
    return [Event(pid, t, MEASUREMENT, k, v) for k, v in {**VITALS, **over}.items()]


def factor_ids(snap):
    return {f["id"] for f in snap["risk_factors"]}


class DerivedFactorTest(unittest.TestCase):
    def setUp(self):
        self.a = FhirAdapter()

    def test_recent_surgery_from_procedure(self):
        proc = {"resourceType": "Procedure", "id": "x", "status": "completed",
                "subject": {"reference": "Patient/p"}, "performedDateTime": (T0 - 10 * 24 * H).isoformat(),
                "category": {"coding": [{"system": "http://snomed.info/sct", "code": "387713003"}]},
                "code": {"text": "laparoscopic cholecystectomy"}}
        e = Engine()
        e.ingest_many(self.a.events(proc) + vitals("p", T0))
        self.assertIn("recent_surgery", factor_ids(e.latest("p")))
        self.assertNotIn("recent_surgery", factor_ids(e.tick(T0 + 25 * 24 * H)["p"]))

    def test_recent_hospitalization_from_encounter(self):
        enc = {"resourceType": "Encounter", "id": "e", "status": "finished", "class": {"code": "IMP"},
               "subject": {"reference": "Patient/p"},
               "period": {"start": (T0 - 40 * 24 * H).isoformat(), "end": (T0 - 35 * 24 * H).isoformat()}}
        e = Engine()
        e.ingest_many(self.a.events(enc) + vitals("p", T0))
        self.assertIn("recent_hospitalization", factor_ids(e.latest("p")))

    def test_polypharmacy_counts_unmapped_meds_once_per_ingredient(self):
        names = ["atorvastatin 40 mg", "atorvastatin 80 mg", "metformin", "amlodipine", "gabapentin",
                 "tamsulosin", "allopurinol"]
        events = vitals("p", T0)
        for i, n in enumerate(names):
            events += self.a.events({"resourceType": "MedicationStatement", "id": str(i), "status": "active",
                                     "subject": {"reference": "Patient/p"},
                                     "medicationCodeableConcept": {"text": n}})
        snap = Engine().ingest_many(events)["p"]
        self.assertEqual(snap["context"]["active_medications"], 6)
        self.assertIn("polypharmacy", factor_ids(snap))

    def test_high_dose_loop_diuretic(self):
        def req(drug, mg):
            return {"resourceType": "MedicationRequest", "id": drug, "status": "active",
                    "subject": {"reference": "Patient/p"}, "medicationCodeableConcept": {"text": drug},
                    "dosageInstruction": [{"doseAndRate": [{"doseQuantity": {"value": mg, "unit": "mg"}}]}]}
        self.assertIn("high_dose_diuretic", {e.code for e in self.a.events(req("bumetanide", 2))})
        self.assertNotIn("high_dose_diuretic", {e.code for e in self.a.events(req("furosemide", 40))})

    def test_language_barrier_and_advance_directive(self):
        pat = {"resourceType": "Patient", "id": "p", "birthDate": "1950-01-01",
               "communication": [{"preferred": True, "language": {"coding": [{"code": "es"}]}}]}
        consent = {"resourceType": "Consent", "id": "c", "status": "active", "patient": {"reference": "Patient/p"},
                   "category": [{"coding": [{"code": "acd"}]}]}
        codes = {e.code for e in self.a.events(pat) + self.a.events(consent)}
        self.assertTrue({"language_barrier", "advance_directive"} <= codes)

    def test_padua_and_pe_risk(self):
        r = scores.padua({"malignancy", "recent_surgery", "obesity"}, 72)
        self.assertEqual(r.total, 7)
        e = Engine()
        e.ingest_many(vitals("p", T0) + [Event("p", EPOCH, CONDITION, "malignancy"),
                                         Event("p", EPOCH, CONDITION, "dvt_pe")])
        self.assertIn("pe_risk", factor_ids(e.latest("p")))

    def test_ckd_and_dm1_are_model_candidates(self):
        from uri.model import features
        e = Engine()
        e.ingest_many(vitals("p", T0) + [Event("p", EPOCH, CONDITION, "dm1")])
        x = features(e.latest("p"))
        self.assertEqual((x["dm1"], x["ckd"]), (1.0, 0.0))

    def test_frailty_local_code_becomes_context(self):
        a = FhirAdapter({"urn:local|CFS": "cfs"})
        obs = {**simulate._obs("p", T0, "x", 6, 1), "code": {"coding": [{"system": "urn:local", "code": "CFS"}]},
               "valueInteger": 6}
        obs.pop("valueQuantity")
        (ev,) = a.events(obs)
        self.assertEqual((ev.kind, ev.code, ev.value), (CONTEXT, "cfs", 6.0))


class CriticalCareTest(unittest.TestCase):
    def test_support_escalation_signal(self):
        e = Engine()
        e.ingest_many(vitals("p", T0) + [Event("p", T0, MEASUREMENT, "resp_support", 1)])
        snap = e.ingest_many(vitals("p", T0 + 2 * H) + [Event("p", T0 + 2 * H, MEASUREMENT, "resp_support", 3)])["p"]
        self.assertIn("resp_support_escalation", {s["id"] for s in snap["trajectory"]["signals"]})

    def test_two_pressors_score_sofa_four_and_escalation(self):
        a = FhirAdapter()
        def admin(i, drug, t):
            return a.events({"resourceType": "MedicationAdministration", "id": str(i), "status": "in-progress",
                             "subject": {"reference": "Patient/p"}, "effectiveDateTime": t.isoformat(),
                             "medicationCodeableConcept": {"text": drug}})
        e = Engine()
        e.ingest_many(vitals("p", T0, sbp=82, dbp=45) + admin(1, "norepinephrine 8 mg/250 mL", T0))
        snap = e.ingest_many(vitals("p", T0 + H, sbp=80, dbp=44) + admin(2, "vasopressin 20 units/100 mL", T0 + H))["p"]
        self.assertEqual(snap["context"]["n_pressors"], 2)  # norepinephrine not double-counted as epinephrine
        self.assertEqual(snap["scores"]["sofa"]["components"]["cardiovascular"], 4)
        self.assertIn("pressor_escalation", {s["id"] for s in snap["trajectory"]["signals"]})
        self.assertEqual([o["type"] for o in snap["outcomes"]], ["vasopressor_start"])

    def test_local_epinephrine_is_not_a_pressor_start(self):
        a = FhirAdapter()

        def admin(drug):
            return a.events({"resourceType": "MedicationAdministration", "id": "1", "status": "completed",
                             "subject": {"reference": "Patient/p"}, "effectiveDateTime": T0.isoformat(),
                             "medicationCodeableConcept": {"text": drug}})
        for drug in ("lidocaine 1% with epinephrine 1:100,000", "phenylephrine 2.5% ophthalmic drops"):
            self.assertFalse(any(ev.kind == OUTCOME or ev.code == "vasopressor" for ev in admin(drug)), drug)
        for drug in ("epinephrine 1 mg/10 mL IV push", "epinephrine 4 mg/250 mL infusion"):
            self.assertTrue(any(ev.kind == OUTCOME for ev in admin(drug)), drug)

    def test_gcs_and_oxygen_carry_forward_within_visit_only(self):
        core = {k: x for k, x in VITALS.items() if k != "gcs"}

        def core_vitals(t):
            return [Event("p", t, MEASUREMENT, k, x) for k, x in core.items()]
        e = Engine()
        e.ingest_many(core_vitals(T0) + [Event("p", T0, MEASUREMENT, "gcs", 12),
                                         Event("p", T0, MEASUREMENT, "o2_flow", 2),
                                         Event("p", T0, MEASUREMENT, "o2_supplemental", True)])
        # 20 h later, same visit (results kept coming): neither re-charted
        for h in range(4, 21, 4):
            snap = e.ingest_many(core_vitals(T0 + h * H))["p"]
        n2 = snap["scores"]["news2"]
        self.assertEqual(n2["components"]["consciousness"], 3)  # GCS 12 carried forward
        self.assertEqual(n2["components"]["o2"], 2)
        self.assertNotIn("upper", n2)
        self.assertTrue(any("carried forward" in f and "gcs (20.0 h old)" in f for f in n2["flags"]))
        self.assertIn("gcs", {x["parameter"] for x in snap["data_quality"]["stale"]})
        # a new visit (results gap > 36 h): not carried into it
        snap = e.ingest_many(core_vitals(T0 + 80 * H))["p"]
        self.assertEqual(sorted(snap["scores"]["news2"]["missing"]), ["consciousness", "o2"])

    def test_troponin_assays_are_interchangeable(self):
        a = FhirAdapter()

        def trop(loinc, value, unit, t):
            obs = simulate._obs("p", t, loinc, value, 1)
            obs["valueQuantity"] = {"value": value, "unit": unit, "code": unit}
            return a.events(obs)
        hs = trop("67151-1", 28, "ng/L", T0)          # hs-TnT, URL 14 ng/L
        conv = trop("10839-9", 0.08, "ng/mL", T0)     # conventional TnI, URL 0.04 ng/mL
        self.assertEqual([(ev.code, ev.value) for ev in hs + conv], [("troponin", 2.0), ("troponin", 2.0)])
        # a rise is detected on the shared scale whichever assay reported it
        e = Engine()
        e.ingest_many(vitals("p", T0) + trop("10839-9", 0.05, "ng/mL", T0))
        snap = e.ingest_many(vitals("p", T0 + 3 * H) + trop("10839-9", 0.20, "ng/mL", T0 + 3 * H))["p"]
        self.assertIn("troponin_rising", {sig["id"] for sig in snap["trajectory"]["signals"]})
        # a site's own URL is used when configured
        b = FhirAdapter(troponin_urls={"hs_tni": 34.0})
        obs = simulate._obs("p", T0, "89579-7", 68, 1)
        obs["valueQuantity"] = {"value": 68, "unit": "ng/L"}
        self.assertEqual(b.events(obs)[0].value, 2.0)

    def test_implausible_values_dropped(self):
        a = FhirAdapter()
        obs = simulate._obs("p", T0, "8867-4", 900, 1)  # heart rate 900
        self.assertEqual(a.events(obs), [])
        self.assertEqual(a.skipped["implausible:hr"], 1)


class OutcomeTest(unittest.TestCase):
    def run_resp_failure(self):
        a = FhirAdapter(simulate.LOCAL_CODES)
        e = Engine()
        for r in simulate.stream(["resp_failure"]):
            e.ingest_many(a.events(r))
        return e

    def test_scenario_captures_intubation_and_icu(self):
        e = self.run_resp_failure()
        kinds = {k for _, k, _ in e.outcomes()}
        self.assertEqual(kinds, {"intubation", "icu_transfer"})

    def test_outcomes_do_not_move_clock_or_score(self):
        e = Engine()
        before = e.ingest_many(vitals("p", T0))["p"]
        self.assertEqual(e.ingest_many([Event("p", T0 + 5 * H, OUTCOME, "icu_transfer")]), {})
        after = e.ingest_many(vitals("p", T0 + H))["p"]
        self.assertEqual(after["time"], (T0 + H).isoformat())
        self.assertEqual(after["uri"], before["uri"])
        self.assertEqual(after["outcomes"], [])  # not visible before it happens
        self.assertEqual(e.tick(T0 + 6 * H)["p"]["outcomes"][0]["type"], "icu_transfer")

    def test_prior_history_is_not_an_outcome(self):
        e = Engine()
        e.ingest_many([Event("p", T0 - 30 * 24 * H, OUTCOME, "icu_transfer")] + vitals("p", T0))
        self.assertEqual(e.outcomes(), [])

    def test_resp_support_to_invasive_is_intubation(self):
        e = Engine()
        e.ingest_many(vitals("p", T0) + [Event("p", T0, MEASUREMENT, "resp_support", 3)])
        e.ingest_many([Event("p", T0 + H, MEASUREMENT, "resp_support", 4)])
        self.assertEqual([k for _, k, _ in e.outcomes()], ["intubation"])

    def test_death_and_rrt_local_code(self):
        a = FhirAdapter({"urn:local|RRT": "outcome:rrt"})
        rrt = {"resourceType": "Procedure", "id": "r", "status": "completed", "subject": {"reference": "Patient/p"},
               "performedDateTime": T0.isoformat(), "code": {"coding": [{"system": "urn:local", "code": "RRT"}]}}
        dead = {"resourceType": "Patient", "id": "p", "deceasedDateTime": (T0 + 3 * H).isoformat()}
        self.assertEqual({(e.kind, e.code) for e in a.events(rrt) + a.events(dead)},
                         {(OUTCOME, "rrt"), (OUTCOME, "death")})

    def test_study_eligibility(self):
        e = Engine()
        e.ingest_many(vitals("p", T0) + [Event("p", EPOCH, CONTEXT, "age", 16), Event("p", EPOCH, CONDITION, "dnr")])
        elig = e.latest("p")["study_eligibility"]
        self.assertFalse(elig["eligible"])
        self.assertEqual(len(elig["reasons"]), 2)


class CriticalValuesAndCausesTest(unittest.TestCase):
    def test_v1_thresholds_most_severe_only(self):
        out = labs.critical_values({"lactate": 4.5, "k": 6.4, "mg": 1.5, "ddimer": 0.7}, {"age": 80}, Config())
        by = {c["parameter"]: c for c in out}
        self.assertEqual(by["lactate"]["severity"], "critical")
        self.assertEqual(len([c for c in out if c["parameter"] == "lactate"]), 1)
        self.assertIn("mg", by)
        self.assertNotIn("ddimer", by)  # 0.7 < age-adjusted 0.8

    def causes_for(self, v, factors=(), meds=(), signals=()):
        return {c["cause"] for c in evaluate(Facts(v, set(factors), set(meds), set(signals), 0))}

    def test_hypercapnia_vs_metabolic_acidosis(self):
        resp = self.causes_for({"ph": 7.22, "pco2": 74})
        self.assertIn("Hypercapnic respiratory failure", resp)
        self.assertNotIn("Metabolic acidosis (HAGMA / NAGMA)", resp)
        self.assertIn("Metabolic acidosis (HAGMA / NAGMA)", self.causes_for({"ph": 7.22, "pco2": 30, "hco3": 12}))

    def test_new_lab_driven_causes(self):
        self.assertIn("Hepatic encephalopathy / acute liver failure", self.causes_for({"ammonia": 140}))
        self.assertIn("Thyroid storm / myxedema coma", self.causes_for({"tsh": 0.02, "hr": 145}))
        self.assertIn("Severe hypomagnesemia / hypophosphatemia", self.causes_for({"phos": 1.1}))
        self.assertIn("Acute pancreatitis", self.causes_for({"lipase": 900}))
        self.assertIn("Coagulopathy with bleeding risk", self.causes_for({"inr": 4.2}, meds=["anticoagulant"]))
        self.assertIn("Adrenal crisis / cortisol deficiency", self.causes_for({"cortisol": 4, "sbp": 82}))

    def test_hypercapnic_copd_uses_news2_scale_2(self):
        e = Engine()
        e.ingest_many(vitals("p", T0, spo2=89) + [Event("p", EPOCH, CONDITION, "copd")])
        self.assertEqual(e.latest("p")["context"]["news2_spo2_scale"], 1)  # not confirmed yet
        snap = e.ingest_many([Event("p", T0 + H, MEASUREMENT, "pco2", 55)])["p"]
        self.assertEqual(snap["context"]["news2_spo2_scale"], 2)
        self.assertEqual(snap["scores"]["news2"]["components"]["spo2"], 0)


if __name__ == "__main__":
    unittest.main()
