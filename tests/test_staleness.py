"""Per-result staleness rules and encounter-based visits."""

import os
import unittest
from datetime import timedelta

from uri import simulate, staleness
from uri.engine import Engine
from uri.events import CONTEXT, EPOCH, MEASUREMENT, OUTCOME, Event
from uri.fhir import FhirAdapter

T0 = simulate.T0
H = timedelta(hours=1)
D = timedelta(days=1)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VITALS = {"hr": 76, "rr": 14, "sbp": 128, "dbp": 80, "spo2": 98, "temp_c": 36.8, "gcs": 15}


def vitals(pid, t, **over):
    return [Event(pid, t, MEASUREMENT, k, v) for k, v in {**VITALS, **over}.items()]


def stale(snap):
    return {x["parameter"]: x for x in snap["data_quality"]["stale"]}


def encounter(pid, eid, cls, start, end=None):
    return Event(pid, EPOCH, CONTEXT, "encounter", {"id": eid, "class": cls, "start": start.isoformat(),
                                                   "end": end.isoformat() if end else None})


class RuleTableTest(unittest.TestCase):
    def test_no_window_is_marked_verified_yet(self):
        for r in staleness.RULES:
            self.assertIn(r.status, staleness.STATUSES, r.params)
            self.assertNotEqual(r.status, staleness.VERIFIED, r.params)  # windows not read from full text
            self.assertTrue(r.pubmed_query and r.source and r.basis, r.params)

    def test_confirmed_citations_carry_pmid_and_doi(self):
        for r in staleness.RULES:
            if r.status == staleness.CITATION_CONFIRMED:
                self.assertTrue(r.pmids and len(r.dois) == len(r.pmids), r.params)
            if r.status == staleness.UNVERIFIED:
                self.assertEqual(r.pmids, (), r.params)

    def test_every_measured_parameter_has_a_rule(self):
        from uri.events import PARAMETERS
        missing = [p for p in PARAMETERS if p not in ("o2_supplemental",) and staleness.rule_for(p) is None]
        self.assertEqual(missing, [])

    def test_committed_doc_matches_code(self):
        with open(os.path.join(ROOT, "docs", "STALENESS_RULES.md")) as fh:
            self.assertEqual(fh.read().rstrip("\n"), staleness.render().rstrip("\n"),
                             "regenerate with: python -m uri.staleness > docs/STALENESS_RULES.md")

    def test_context_dependent_windows(self):
        self.assertEqual(staleness.window("hr", {}, 0)[0], 12)
        self.assertEqual(staleness.window("hr", {}, 3)[0], 4)
        self.assertEqual(staleness.window("hr", {}, 2, single_three=True)[0], 1)
        self.assertEqual(staleness.window("hr", {}, 8)[0], 0.5)
        self.assertEqual(staleness.window("lactate", {"lactate": 3.4})[0], 4)
        self.assertEqual(staleness.window("lactate", {"lactate": 1.1})[0], 24)
        self.assertEqual(staleness.window("hgb", {}, hgb_dropping=True)[0], 6)
        self.assertEqual(staleness.window("creatinine", {})[0], 24)


class EngineStalenessTest(unittest.TestCase):
    def test_vitals_window_tightens_with_news2(self):
        stable = Engine()
        stable.ingest_many(vitals("p", T0))
        self.assertNotIn("hr", stale(stable.tick(T0 + 5 * H)["p"]))
        sick = Engine()
        sick.ingest_many(vitals("p", T0, rr=23, hr=105))  # NEWS2 3 -> 4 h window
        entry = stale(sick.tick(T0 + 5 * H)["p"])["hr"]
        self.assertEqual((entry["window_h"], entry["rule"]), (4.0, "NEWS2 1-4"))
        self.assertEqual(entry["rule_status"], staleness.NOT_IN_PUBMED)

    def test_elevated_lactate_goes_stale_in_4h_normal_in_24h(self):
        e = Engine()
        e.ingest_many(vitals("p", T0) + [Event("p", T0, MEASUREMENT, "lactate", 3.2)])
        for h in (2, 4, 6):
            snap = e.ingest_many(vitals("p", T0 + h * H))["p"]
        self.assertIn("lactate", stale(snap))
        e2 = Engine()
        e2.ingest_many(vitals("p", T0) + [Event("p", T0, MEASUREMENT, "lactate", 1.4)])
        for h in (2, 4, 6):
            snap = e2.ingest_many(vitals("p", T0 + h * H))["p"]
        self.assertNotIn("lactate", stale(snap))

    def test_hours_since_features(self):
        from uri.model import features
        e = Engine()
        e.ingest_many(vitals("p", T0) + [Event("p", T0, MEASUREMENT, "creatinine", 1.1)])
        snap = e.ingest_many(vitals("p", T0 + 5 * H))["p"]
        f = features(snap)
        self.assertEqual((f["hours_since_vitals"], f["hours_since_creatinine"], f["hours_since_lactate"]),
                         (0.0, 5.0, 72.0))


class EncounterVisitTest(unittest.TestCase):
    def test_encounter_defines_the_visit_over_a_long_results_gap(self):
        # ED visit then admission; nothing charted for 48 h mid-stay (would split under the gap rule).
        e = Engine()
        e.preload([encounter("p", "ed", "EMER", T0, T0 + 6 * H), encounter("p", "ip", "IMP", T0 + 6 * H)])
        e.ingest_many(vitals("p", T0) + [Event("p", T0, MEASUREMENT, "lactate", 3.0)])
        snap = e.ingest_many(vitals("p", T0 + 60 * H))["p"]
        self.assertEqual(snap["visit"]["determined_by"], "encounter")
        self.assertEqual(snap["episode_start"], (T0 - H).isoformat())  # arrival tolerance
        self.assertTrue(snap["visit"]["open"])
        self.assertEqual(snap["scores"]["crs2"]["components"]["metabolic"], 2)  # lactate still in the visit

    def test_clinic_results_before_arrival_are_baseline(self):
        e = Engine()
        e.preload([encounter("p", "ed", "EMER", T0)])
        e.preload([Event("p", T0 - 5 * H, MEASUREMENT, "creatinine", 2.1)])  # same-day clinic draw
        snap = e.ingest_many(vitals("p", T0) + [Event("p", T0, MEASUREMENT, "creatinine", 2.2)])["p"]
        self.assertEqual(snap["baseline"]["creatinine"]["value"], 2.1)
        self.assertEqual(snap["scores"]["sofa"]["acute"], 0)

    def test_discharge_closes_the_visit(self):
        e = Engine()
        e.preload([encounter("p", "ed", "EMER", T0, T0 + 5 * H)])
        e.ingest_many(vitals("p", T0) + [Event("p", T0, MEASUREMENT, "lactate", 3.0)])
        snap = e.tick(T0 + 60 * H)["p"]
        self.assertFalse(snap["visit"]["open"])
        self.assertEqual(snap["scores"]["crs2"]["components"]["metabolic"], 0)

    def test_no_encounter_data_falls_back_to_results_gap(self):
        e = Engine()
        snap = e.ingest_many(vitals("p", T0))["p"]
        self.assertEqual(snap["visit"]["determined_by"], "results gap (no encounter data)")

    def test_prior_admission_outcome_excluded_by_encounter(self):
        e = Engine()
        e.preload([encounter("p", "old", "IMP", T0 - 30 * D, T0 - 25 * D),
                   Event("p", T0 - 29 * D, OUTCOME, "icu_transfer"), encounter("p", "ed", "EMER", T0)])
        e.preload(vitals("p", T0 - 29 * D))
        e.ingest_many(vitals("p", T0))
        self.assertEqual(e.outcomes(), [])

    def test_fhir_encounter_classes(self):
        a = FhirAdapter()
        def enc(cls, status="in-progress"):
            return {"resourceType": "Encounter", "id": cls, "status": status, "class": {"code": cls},
                    "subject": {"reference": "Patient/p"}, "period": {"start": T0.isoformat()}}
        self.assertIn("encounter", {ev.code for ev in a.events(enc("EMER"))})
        self.assertNotIn("encounter", {ev.code for ev in a.events(enc("AMB"))})  # clinic: baseline only
        self.assertNotIn("encounter", {ev.code for ev in a.events(enc("IMP", "cancelled"))})


if __name__ == "__main__":
    unittest.main()
