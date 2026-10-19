"""Synthetic FHIR streams for demos and tests. Not patient data.

Each scenario yields FHIR R4 resources in time order, exactly as an EMR
subscription or interface engine would push them, so a replay exercises the
full path: FHIR -> events -> engine -> snapshots.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Iterator

from .events import CONDITION, CONTEXT, EPOCH, MEASUREMENT, Event

T0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)

_UNITS = {
    "8867-4": "/min", "9279-1": "/min", "8480-6": "mm[Hg]", "8462-4": "mm[Hg]", "59408-5": "%",
    "8310-5": "Cel", "9269-2": "{score}", "2524-7": "mmol/L", "718-7": "g/dL", "2160-0": "mg/dL",
    "6690-2": "10*3/uL", "3255-7": "mg/dL", "33959-8": "ng/mL", "3151-8": "L/min",
    "777-3": "10*3/uL", "1975-2": "mg/dL", "3094-0": "mg/dL", "6301-6": "{INR}",
}


def _obs(pid: str, t: datetime, loinc: str, value: float, n: int) -> dict:
    return {
        "resourceType": "Observation", "id": f"{pid}-{n}", "status": "final",
        "subject": {"reference": f"Patient/{pid}"},
        "effectiveDateTime": t.isoformat(),
        "code": {"coding": [{"system": "http://loinc.org", "code": loinc}]},
        "valueQuantity": {"value": value, "code": _UNITS.get(loinc, "")},
    }


def _bp(pid: str, t: datetime, sbp: float, dbp: float, n: int) -> dict:
    return {
        "resourceType": "Observation", "id": f"{pid}-{n}", "status": "final",
        "subject": {"reference": f"Patient/{pid}"}, "effectiveDateTime": t.isoformat(),
        "code": {"coding": [{"system": "http://loinc.org", "code": "85354-9"}]},
        "component": [
            {"code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]},
             "valueQuantity": {"value": sbp, "code": "mm[Hg]"}},
            {"code": {"coding": [{"system": "http://loinc.org", "code": "8462-4"}]},
             "valueQuantity": {"value": dbp, "code": "mm[Hg]"}},
        ],
    }


def _condition(pid: str, icd10: str, n: int) -> dict:
    return {
        "resourceType": "Condition", "id": f"{pid}-c{n}", "subject": {"reference": f"Patient/{pid}"},
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": icd10}]},
    }


def _patient(pid: str, birth: str) -> dict:
    return {"resourceType": "Patient", "id": pid, "birthDate": birth}


def _vitals(pid, t, n, hr, rr, sbp, dbp, spo2, temp, gcs):
    yield _obs(pid, t, "8867-4", hr, n)
    yield _obs(pid, t, "9279-1", rr, n + 1)
    yield _bp(pid, t, sbp, dbp, n + 2)
    yield _obs(pid, t, "59408-5", spo2, n + 3)
    yield _obs(pid, t, "8310-5", temp, n + 4)
    yield _obs(pid, t, "9269-2", gcs, n + 5)


def sepsis(pid: str = "sim-sepsis") -> Iterator[dict]:
    """72-year-old with CHF and CKD, evolving septic shock over 6 hours."""
    yield _patient(pid, "1954-03-02")
    yield _condition(pid, "I50.22", 1)
    yield _condition(pid, "N18.3", 2)
    course = [  # hours, hr, rr, sbp, dbp, spo2, temp, gcs
        (0, 98, 20, 118, 70, 95, 38.2, 15),
        (1, 106, 22, 108, 64, 94, 38.6, 15),
        (2, 114, 24, 98, 58, 93, 38.9, 15),
        (3, 122, 26, 92, 52, 92, 39.2, 14),
        (4, 128, 28, 86, 48, 91, 39.3, 14),
        (5, 132, 30, 82, 46, 90, 39.4, 13),
    ]
    n = 0
    for h, *vals in course:
        n += 10
        yield from _vitals(pid, T0 + timedelta(hours=h), n, *vals)
    yield _obs(pid, T0 + timedelta(minutes=40), "2524-7", 2.6, 900)
    yield _obs(pid, T0 + timedelta(minutes=45), "6690-2", 17.5, 901)
    yield _obs(pid, T0 + timedelta(minutes=45), "2160-0", 1.6, 902)
    yield _obs(pid, T0 + timedelta(minutes=45), "777-3", 182, 905)
    yield _obs(pid, T0 + timedelta(minutes=45), "1975-2", 0.9, 906)
    yield _obs(pid, T0 + timedelta(hours=3), "3151-8", 4, 907)  # 4 L nasal cannula
    yield _obs(pid, T0 + timedelta(hours=3, minutes=30), "2524-7", 4.4, 903)
    yield _obs(pid, T0 + timedelta(hours=3, minutes=30), "2160-0", 2.1, 904)
    yield _obs(pid, T0 + timedelta(hours=3, minutes=30), "777-3", 96, 908)
    yield _obs(pid, T0 + timedelta(hours=3, minutes=30), "1975-2", 2.3, 909)


def gi_bleed(pid: str = "sim-gi_bleed") -> Iterator[dict]:
    """78-year-old on apixaban with AF and CKD; occult upper GI bleed. Vitals
    look tolerable for hours; the falling hemoglobin and rising BUN are the
    early signal."""
    yield _patient(pid, "1948-09-11")
    yield _condition(pid, "I48.91", 1)
    yield _condition(pid, "N18.4", 2)
    yield {"resourceType": "MedicationStatement", "id": f"{pid}-m1", "status": "active",
           "subject": {"reference": f"Patient/{pid}"},
           "medicationCodeableConcept": {"text": "apixaban 5 MG Oral Tablet"}}
    course = [  # hours, hr, rr, sbp, dbp, spo2, temp, gcs
        (0, 92, 16, 132, 74, 97, 36.7, 15),
        (2, 98, 17, 124, 70, 97, 36.6, 15),
        (4, 104, 18, 114, 64, 96, 36.6, 15),
        (6, 112, 20, 104, 58, 96, 36.5, 15),
        (8, 121, 22, 94, 52, 95, 36.4, 15),
    ]
    n = 0
    for h, *vals in course:
        n += 10
        yield from _vitals(pid, T0 + timedelta(hours=h), n, *vals)
    for h, hgb, bun, n_ in ((0.5, 11.2, 34, 900), (4.5, 9.6, 48, 910), (7.5, 8.1, 61, 920)):
        t = T0 + timedelta(hours=h)
        yield _obs(pid, t, "718-7", hgb, n_)
        yield _obs(pid, t, "3094-0", bun, n_ + 1)
        yield _obs(pid, t, "2160-0", 2.2, n_ + 2)
        yield _obs(pid, t, "6301-6", 1.3, n_ + 3)


def _device(pid, t, text, n):
    return {"resourceType": "Observation", "id": f"{pid}-{n}", "status": "final",
            "subject": {"reference": f"Patient/{pid}"}, "effectiveDateTime": t.isoformat(),
            "code": {"coding": [{"system": "urn:uri:local", "code": "O2DEV"}]},
            "valueCodeableConcept": {"text": text}}


def resp_failure(pid: str = "sim-resp_failure") -> Iterator[dict]:
    """68-year-old with COPD and OSA: hypercapnic respiratory failure that
    escalates nasal cannula -> HFNC -> BiPAP -> intubation, then ICU."""
    yield _patient(pid, "1958-02-17")
    yield _condition(pid, "J44.1", 1)
    yield _condition(pid, "G47.33", 2)
    course = [  # hours, hr, rr, sbp, dbp, spo2, temp, gcs, device, fio2
        (0, 98, 24, 142, 84, 90, 37.4, 15, "nasal cannula 2 L/min", None),
        (1.5, 106, 28, 148, 86, 88, 37.5, 15, "high flow nasal cannula", 50),
        (3, 114, 32, 152, 88, 87, 37.6, 14, "BiPAP", 60),
        (4.5, 122, 34, 156, 90, 85, 37.6, 13, "BiPAP", 70),
        (6, 118, 18, 128, 74, 96, 37.5, 10, "mechanical ventilator", 60),
    ]
    n = 0
    for h, hr, rr, sbp, dbp, spo2, temp, gcs, device, fio2 in course:
        t = T0 + timedelta(hours=h)
        n += 10
        yield from _vitals(pid, t, n, hr, rr, sbp, dbp, spo2, temp, gcs)
        yield _device(pid, t, device, n + 7)
        if fio2:
            yield _obs(pid, t, "3150-0", fio2, n + 8)
    for h, ph, pco2, po2, n_ in ((1, 7.31, 58, 62, 900), (4.6, 7.22, 74, 58, 910)):
        t = T0 + timedelta(hours=h)
        yield _obs(pid, t, "2744-1", ph, n_)
        yield _obs(pid, t, "2019-8", pco2, n_ + 1)
        yield _obs(pid, t, "2703-7", po2, n_ + 2)
    yield {"resourceType": "Procedure", "id": f"{pid}-p1", "status": "completed",
           "subject": {"reference": f"Patient/{pid}"},
           "performedDateTime": (T0 + timedelta(hours=5, minutes=50)).isoformat(),
           "code": {"coding": [{"system": "http://snomed.info/sct", "code": "112798008"}]}}
    yield {"resourceType": "Encounter", "id": f"{pid}-e1", "status": "in-progress",
           "class": {"code": "IMP"}, "subject": {"reference": f"Patient/{pid}"},
           "period": {"start": T0.isoformat()},
           "location": [{"location": {"display": "MICU Bed 4"},
                         "period": {"start": (T0 + timedelta(hours=6, minutes=30)).isoformat()}}]}


def stable(pid: str = "sim-stable") -> Iterator[dict]:
    """45-year-old with normal, steady vitals."""
    yield _patient(pid, "1981-05-20")
    n = 0
    for h in range(4):
        n += 10
        yield from _vitals(pid, T0 + timedelta(hours=h), n, 76, 14, 128, 80, 98, 36.8, 15)


SCENARIOS = {"sepsis": sepsis, "gi_bleed": gi_bleed, "resp_failure": resp_failure, "stable": stable}
DEFAULT_SCENARIOS = ["sepsis", "gi_bleed", "resp_failure", "stable"]
LOCAL_CODES = {"urn:uri:local|O2DEV": "resp_support"}


def stream(names: list[str]) -> list[dict]:
    """Merge scenarios into one feed in arrival (effective-time) order."""
    resources = [r for name in names for r in SCENARIOS[name]()]
    return sorted(resources, key=_when)


def _when(r: dict) -> str:
    """When an interface engine would send the resource (Encounter updates go
    out at the location change)."""
    locs = [loc.get("period", {}).get("start", "") for loc in r.get("location", [])]
    return r.get("effectiveDateTime") or r.get("performedDateTime") or max(locs, default="") or ""


def cohort(n: int = 300, seed: int = 0) -> tuple[list[Event], list[tuple[str, datetime]]]:
    """Synthetic cohort for exercising calibration: hourly vitals and two lab
    draws over 10 h. A latent severity drives both the physiology and the
    chance of a composite outcome, so a model fitted on the snapshots should
    discriminate. Normalized events (not FHIR) for speed."""
    rng = random.Random(seed)
    events: list[Event] = []
    outcomes: list[tuple[str, datetime]] = []
    for i in range(n):
        pid = f"c{i:04d}"
        sev = rng.random()
        deteriorates = rng.random() < 0.05 + 0.5 * sev ** 2
        onset = rng.uniform(2, 8) if deteriorates else None
        events.append(Event(pid, EPOCH, CONTEXT, "age", rng.randint(20, 95)))
        if rng.random() < sev:
            events.append(Event(pid, EPOCH, CONDITION, rng.choice(["chf", "copd", "ckd", "cci_chf", "cci_renal"])))
        for h in range(11):
            t = T0 + timedelta(hours=h)
            drift = max(0.0, h - onset) if onset is not None else 0.0
            x = sev * 0.6 + drift * 0.25 + rng.gauss(0, 0.15)
            vals = {"hr": 80 + 45 * x, "rr": 15 + 10 * x, "sbp": 130 - 45 * x, "dbp": 78 - 25 * x,
                    "spo2": min(100, 98 - 8 * x), "temp_c": 37 + 1.5 * x * rng.random(),
                    "gcs": 15 if x < 0.9 else 14}
            events += [Event(pid, t, MEASUREMENT, k, round(v, 1)) for k, v in vals.items()]
            if h in (1, 6):
                events += [Event(pid, t, MEASUREMENT, "lactate", round(max(0.6, 1 + 3 * x + rng.gauss(0, .3)), 1)),
                           Event(pid, t, MEASUREMENT, "creatinine", round(0.9 + 1.5 * x * rng.random(), 2)),
                           Event(pid, t, MEASUREMENT, "plt", round(250 - 120 * x)),
                           Event(pid, t, MEASUREMENT, "hgb", round(13 - 3 * x, 1))]
        if deteriorates:
            outcomes.append((pid, T0 + timedelta(hours=onset + rng.uniform(1, 5))))
    return events, outcomes
