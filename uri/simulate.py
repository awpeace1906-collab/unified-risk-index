"""Synthetic FHIR streams for demos and tests. Not patient data.

Each scenario yields FHIR R4 resources in time order, exactly as an EMR
subscription or interface engine would push them, so a replay exercises the
full path: FHIR -> events -> engine -> snapshots.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

T0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)

_UNITS = {
    "8867-4": "/min", "9279-1": "/min", "8480-6": "mm[Hg]", "8462-4": "mm[Hg]", "59408-5": "%",
    "8310-5": "Cel", "9269-2": "{score}", "2524-7": "mmol/L", "718-7": "g/dL", "2160-0": "mg/dL",
    "6690-2": "10*3/uL", "3255-7": "mg/dL", "33959-8": "ng/mL", "3151-8": "L/min",
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
    yield _obs(pid, T0 + timedelta(hours=3, minutes=30), "2524-7", 4.4, 903)
    yield _obs(pid, T0 + timedelta(hours=3, minutes=30), "2160-0", 2.1, 904)


def pph(pid: str = "sim-pph") -> Iterator[dict]:
    """Postpartum patient with placenta previa and twins; slow-then-brisk bleed."""
    yield _patient(pid, "1992-07-14")
    yield _condition(pid, "O44.13", 1)   # placenta previa
    yield _condition(pid, "O30.003", 2)  # twin gestation
    yield _condition(pid, "Z39.0", 3)    # postpartum care
    course = [  # minutes, hr, rr, sbp, dbp, spo2, temp, gcs, qbl, hgb
        (0, 88, 16, 124, 78, 99, 37.0, 15, 300, 11.8),
        (30, 96, 18, 118, 72, 99, 37.0, 15, 650, None),
        (60, 108, 20, 108, 64, 98, 37.1, 15, 900, 10.1),
        (90, 118, 22, 98, 56, 97, 37.1, 15, 1250, None),
        (120, 126, 24, 92, 52, 97, 37.2, 15, 1600, 8.6),
    ]
    n = 0
    for m, hr, rr, sbp, dbp, spo2, temp, gcs, qbl, hgb in course:
        t = T0 + timedelta(minutes=m)
        n += 10
        yield from _vitals(pid, t, n, hr, rr, sbp, dbp, spo2, temp, gcs)
        yield {**_obs(pid, t, "QBL", qbl, n + 7),
               "code": {"coding": [{"system": "urn:uri:local", "code": "QBL"}]},
               "valueQuantity": {"value": qbl, "unit": "mL"}}
        if hgb is not None:
            yield _obs(pid, t, "718-7", hgb, n + 8)
    yield _obs(pid, T0 + timedelta(minutes=125), "3255-7", 180, 999)


def stable(pid: str = "sim-stable") -> Iterator[dict]:
    """45-year-old with normal, steady vitals."""
    yield _patient(pid, "1981-05-20")
    n = 0
    for h in range(4):
        n += 10
        yield from _vitals(pid, T0 + timedelta(hours=h), n, 76, 14, 128, 80, 98, 36.8, 15)


SCENARIOS = {"sepsis": sepsis, "pph": pph, "stable": stable}
LOCAL_CODES = {"urn:uri:local|QBL": "qbl_ml"}


def stream(names: list[str]) -> list[dict]:
    """Merge scenarios into one feed in arrival (effective-time) order."""
    resources = [r for name in names for r in SCENARIOS[name]()]
    return sorted(resources, key=lambda r: r.get("effectiveDateTime", ""))
